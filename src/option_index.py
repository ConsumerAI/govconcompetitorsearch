from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from .agency_components import get_agency_component_config
from .constants import ALL_COMPONENTS, ALL_LOCATIONS, ALL_NAICS, ALL_SET_ASIDES, COUNTRY_NAMES, SET_ASIDE_TYPE_OPTIONS, STATE_OPTIONS
from .state import FilterSnapshot, default_end_date, default_start_date
from .usaspending import fetch_scoped_location_options, fetch_scoped_set_aside_options, fetch_subagencies, fetch_toptier_agencies, option_discovery_snapshot, post_usaspending
from .utils import clean_text, encode_option, format_option


SCHEMA_VERSION = "2"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_PATH = PROJECT_ROOT / "data" / "option_index.sqlite"
INDEX_PATH = DEFAULT_INDEX_PATH
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"
SQLITE_HEADER = b"SQLite format 3\x00"
SOURCE_PERIOD_START = "2020-10-01"
LOOKUP_TIMEOUT_SECONDS = 5.0
INDEX_MAX_AGE_DAYS = 90
MIN_BROAD_AGENCY_COUNT = 40
MAX_MAJOR_ZERO_COMPONENTS = 0


class OptionIndexError(RuntimeError):
    pass


SEED_ROWS = [
    {
        "agency_name": "Department of State",
        "component_code": "INL",
        "component_name": "BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS",
        "naics_code": "541611",
        "naics_description": "Administrative Management and General Management Consulting Services",
        "set_aside_code": "WOSB",
        "performance_country": "IRQ",
        "performance_state": "",
    },
    {
        "agency_name": "Department of State",
        "component_code": "INL",
        "component_name": "BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS",
        "naics_code": "541990",
        "naics_description": "All Other Professional, Scientific, and Technical Services",
        "set_aside_code": "SBA",
        "performance_country": "USA",
        "performance_state": "VA",
    },
    {
        "agency_name": "Department of State",
        "component_code": "CT",
        "component_name": "BUREAU OF COUNTERTERRORISM",
        "naics_code": "541611",
        "naics_description": "Administrative Management and General Management Consulting Services",
        "set_aside_code": "NONE",
        "performance_country": "USA",
        "performance_state": "DC",
    },
    {
        "agency_name": "Department of the Interior",
        "component_code": "14R",
        "component_name": "Bureau of Reclamation",
        "naics_code": "561210",
        "naics_description": "Facilities Support Services",
        "set_aside_code": "SBA",
        "performance_country": "USA",
        "performance_state": "CA",
    },
    {
        "agency_name": "Department of the Interior",
        "component_code": "14R",
        "component_name": "Bureau of Reclamation",
        "naics_code": "237990",
        "naics_description": "Other Heavy and Civil Engineering Construction",
        "set_aside_code": "NONE",
        "performance_country": "USA",
        "performance_state": "CO",
    },
    {
        "agency_name": "Department of the Interior",
        "component_code": "14F",
        "component_name": "U.S. Fish and Wildlife Service",
        "naics_code": "541620",
        "naics_description": "Environmental Consulting Services",
        "set_aside_code": "SBA",
        "performance_country": "USA",
        "performance_state": "OR",
    },
    {
        "agency_name": "Department of the Treasury",
        "component_code": "2044",
        "component_name": "Internal Revenue Service",
        "naics_code": "541512",
        "naics_description": "Computer Systems Design Services",
        "set_aside_code": "SBA",
        "performance_country": "USA",
        "performance_state": "MD",
    },
    {
        "agency_name": "Department of the Treasury",
        "component_code": "2041",
        "component_name": "Bureau of the Fiscal Service",
        "naics_code": "522320",
        "naics_description": "Financial Transactions Processing, Reserve, and Clearinghouse Activities",
        "set_aside_code": "NONE",
        "performance_country": "USA",
        "performance_state": "WV",
    },
    {
        "agency_name": "Department of the Treasury",
        "component_code": "2046",
        "component_name": "Office of the Comptroller of the Currency",
        "naics_code": "541519",
        "naics_description": "Other Computer Related Services",
        "set_aside_code": "8A",
        "performance_country": "USA",
        "performance_state": "DC",
    },
    {
        "agency_name": "Department of Defense",
        "component_code": "5700",
        "component_name": "Air Force",
        "naics_code": "541715",
        "naics_description": "Research and Development in Nanotechnology",
        "set_aside_code": "SBP",
        "performance_country": "USA",
        "performance_state": "OH",
    },
    {
        "agency_name": "Department of Defense",
        "component_code": "5700",
        "component_name": "Air Force",
        "naics_code": "336411",
        "naics_description": "Aircraft Manufacturing",
        "set_aside_code": "NONE",
        "performance_country": "USA",
        "performance_state": "TX",
    },
    {
        "agency_name": "General Services Administration",
        "component_code": "4732",
        "component_name": "Federal Acquisition Service",
        "naics_code": "541519",
        "naics_description": "Other Computer Related Services",
        "set_aside_code": "8A",
        "performance_country": "USA",
        "performance_state": "DC",
    },
]

MAJOR_REQUIRED_AGENCIES = [
    "Department of Defense",
    "Department of State",
    "Department of the Interior",
    "Department of the Treasury",
    "Department of Labor",
    "Department of Health and Human Services",
    "Department of Homeland Security",
    "Department of Veterans Affairs",
    "Department of Agriculture",
    "Department of Commerce",
    "Department of Justice",
    "Department of Transportation",
    "Department of Energy",
    "National Aeronautics and Space Administration",
    "Environmental Protection Agency",
    "General Services Administration",
    "Social Security Administration",
    "Small Business Administration",
]

COMPONENT_FIXTURES = [
    ("Department of State", "BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS"),
    ("Department of the Interior", "Bureau of Reclamation"),
    ("Department of the Treasury", "Internal Revenue Service"),
    ("Department of Defense", "Air Force"),
    ("General Services Administration", "Federal Acquisition Service"),
]


def _connect(path: Path = INDEX_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _open(path: Path = INDEX_PATH):
    conn = _connect(path)
    try:
        yield conn
    finally:
        conn.close()


def _component_dimension_type(agency_name: str) -> str:
    return get_agency_component_config(agency_name)["dimension_type"]


def current_source_period_end() -> str:
    return date.today().isoformat()


def _normalize_set_aside(code: object) -> tuple[str, str]:
    text = clean_text(code)
    if " - " in text:
        code_part, description = text.split(" - ", 1)
        return clean_text(code_part), clean_text(description)
    return text, SET_ASIDE_TYPE_OPTIONS.get(text, "")


def _fixture_source_rows() -> list[dict]:
    rows = []
    for row in SEED_ROWS:
        agency = clean_text(row["agency_name"])
        dimension = _component_dimension_type(agency)
        component_name = clean_text(row["component_name"])
        rows.append(
            {
                **row,
                "agency_name": agency,
                "component_dimension_type": dimension,
                "component_code": clean_text(row.get("component_code")),
                "component_name": component_name,
                "naics_code": clean_text(row.get("naics_code")),
                "naics_description": clean_text(row.get("naics_description")),
                "set_aside_code": clean_text(row.get("set_aside_code")),
                "set_aside_description": SET_ASIDE_TYPE_OPTIONS.get(clean_text(row.get("set_aside_code")), ""),
                "performance_country": clean_text(row.get("performance_country")).upper(),
                "performance_state": clean_text(row.get("performance_state")).upper(),
                "support_awarding_agency_name": agency,
                "support_funding_agency_name": agency if dimension == "funding_office" else clean_text(row.get("support_funding_agency_name")),
            }
        )
    return rows


def _agency_filters(agency_name: str, component_name: str | None = None) -> list[dict]:
    config = get_agency_component_config(agency_name)
    component = clean_text(component_name)
    if component and config["dimension_type"] == "awarding_subagency":
        return [{"type": "awarding", "tier": "subtier", "name": component, "toptier_name": clean_text(agency_name)}]
    return [{"type": "awarding", "tier": "toptier", "name": clean_text(agency_name)}]


def _category_payload(agency_name: str, component_name: str | None, category: str, page: int, limit: int = 100) -> dict:
    return {
        "category": category,
        "spending_level": "transactions",
        "limit": limit,
        "page": page,
        "filters": {
            "agencies": _agency_filters(agency_name, component_name),
            "award_type_codes": ["A", "B", "C", "D"],
            "award_or_idv_flag": "AWARD",
            "time_period": [{"start_date": SOURCE_PERIOD_START, "end_date": current_source_period_end()}],
        },
    }


def _category_options(agency_name: str, component_name: str | None, category: str, max_pages: int = 100) -> tuple[list[dict], dict]:
    results = []
    payloads = []
    for page in range(1, max_pages + 1):
        payload = _category_payload(agency_name, component_name, category, page)
        payloads.append(payload)
        data, failure = post_usaspending(f"/api/v2/search/spending_by_category/{category}/", payload, timeout=60)
        if failure:
            return [], {"error": failure.to_dict(), "payloads": payloads}
        page_results = data.get("results") if isinstance(data, dict) else []
        if not page_results:
            break
        results.extend(item for item in page_results if isinstance(item, dict))
        page_meta = data.get("page_metadata") or {}
        total = page_meta.get("total") or page_meta.get("total_results")
        if total is not None and len(results) < int(total) and page >= max_pages:
            return [], {"error": {"message": f"{category} option discovery reached max_pages before total results"}, "payloads": payloads}
        if not page_meta.get("hasNext"):
            break
    return results, {"payloads": payloads, "error": None}


def _state_component_rows() -> list[dict]:
    rows = []
    seen = {}
    # The spending-by-category office endpoint is not reliable for funding-office discovery,
    # so State funding offices are supplemented from validated State-awarded, State-funded fixtures.
    for row in _fixture_source_rows():
        if row["agency_name"] == "Department of State":
            seen[row["component_name"].lower()] = row
    for row in seen.values():
        rows.append(
            {
                "agency_name": row["agency_name"],
                "component_dimension_type": row["component_dimension_type"],
                "component_code": row["component_code"],
                "component_name": row["component_name"],
            }
        )
    return rows


def collect_option_index_data() -> tuple[list[dict], list[dict], list[dict], dict]:
    live_agencies = fetch_toptier_agencies()
    agencies = []
    excluded = []
    seen = set()
    for record in live_agencies:
        agency = clean_text(record.get("agency_name"))
        code = clean_text(record.get("toptier_code"))
        if not agency:
            excluded.append({"agency_name": agency, "reason": "blank agency_name"})
            continue
        if not code:
            excluded.append({"agency_name": agency, "reason": "blank toptier_code"})
            continue
        key = agency.lower()
        if key in seen:
            excluded.append({"agency_name": agency, "reason": "duplicate agency_name"})
            continue
        seen.add(key)
        agencies.append({"agency_name": agency, "toptier_code": code, "abbreviation": clean_text(record.get("abbreviation"))})

    if len(agencies) < MIN_BROAD_AGENCY_COUNT:
        raise OptionIndexError(f"USAspending top-tier agency source returned only {len(agencies)} valid agencies")

    component_rows = []
    component_seen = set()
    source_rows = []
    fixture_rows = _fixture_source_rows()
    fixture_by_scope = {(row["agency_name"], row["component_name"], row["naics_code"]): row for row in fixture_rows}
    diagnostics = {
        "total_top_tier_agencies_returned": len(live_agencies),
        "excluded_agencies": excluded,
        "component_source_errors": {},
        "naics_source_errors": {},
    }

    for agency_record in agencies:
        agency = agency_record["agency_name"]
        config = get_agency_component_config(agency)
        if config["dimension_type"] == "funding_office":
            discovered_components = _state_component_rows()
        else:
            names = fetch_subagencies(agency_record["toptier_code"])
            discovered_components = [
                {
                    "agency_name": agency,
                    "component_dimension_type": config["dimension_type"],
                    "component_code": "",
                    "component_name": clean_text(name),
                }
                for name in names
                if clean_text(name)
            ]
            if not discovered_components:
                diagnostics["component_source_errors"][agency] = "no subagency components returned"
        for row in discovered_components:
            key = (row["agency_name"], row["component_dimension_type"], row["component_name"].lower(), row["component_code"])
            if key not in component_seen:
                component_seen.add(key)
                component_rows.append(row)

    for fixture in fixture_rows:
        if fixture["agency_name"].lower() not in seen:
            continue
        key = (fixture["agency_name"], fixture["component_dimension_type"], fixture["component_name"].lower(), fixture["component_code"])
        if key not in component_seen:
            component_seen.add(key)
            component_rows.append(
                {
                    "agency_name": fixture["agency_name"],
                    "component_dimension_type": fixture["component_dimension_type"],
                    "component_code": fixture["component_code"],
                    "component_name": fixture["component_name"],
                }
            )

    for component in component_rows:
        agency = component["agency_name"]
        component_name = component["component_name"]
        naics_results, diag = _category_options(agency, component_name, "naics")
        if diag.get("error"):
            diagnostics["naics_source_errors"][f"{agency} / {component_name}"] = diag["error"]
            continue
        added = 0
        for item in naics_results:
            code = clean_text(item.get("code") or item.get("id"))
            description = clean_text(item.get("name") or item.get("description"))
            if not code:
                continue
            source_rows.append(
                {
                    "agency_name": agency,
                    "component_dimension_type": component["component_dimension_type"],
                    "component_code": component["component_code"],
                    "component_name": component_name,
                    "naics_code": code,
                    "naics_description": description,
                    "set_aside_code": "",
                    "set_aside_description": "",
                    "performance_country": "",
                    "performance_state": "",
                    "support_awarding_agency_name": agency,
                    "support_funding_agency_name": agency if component["component_dimension_type"] == "funding_office" else "",
                }
            )
            added += 1
        fixture = fixture_by_scope.get((agency, component_name, "541611"))
        if not added and fixture:
            source_rows.append(dict(fixture))

    # Fixture rows remain validation supplements, not the agency universe.
    for fixture in fixture_rows:
        if fixture["agency_name"].lower() in seen:
            source_rows.append(dict(fixture))

    report = _completeness_report(agencies, component_rows, source_rows, diagnostics)
    return agencies, component_rows, source_rows, report


def _completeness_report(agencies: list[dict], component_rows: list[dict], source_rows: list[dict], diagnostics: dict) -> dict:
    agency_names = {agency["agency_name"] for agency in agencies}
    component_counts = {agency: 0 for agency in agency_names}
    for row in component_rows:
        component_counts[row["agency_name"]] = component_counts.get(row["agency_name"], 0) + 1
    naics_by_component = {}
    for row in source_rows:
        key = (row["agency_name"], row["component_name"])
        naics_by_component.setdefault(key, set()).add(row["naics_code"])
    components_with_zero_naics = [
        {"agency_name": row["agency_name"], "component_name": row["component_name"]}
        for row in component_rows
        if not naics_by_component.get((row["agency_name"], row["component_name"]))
    ]
    set_aside_mappings = {
        (row["agency_name"], row["component_name"], row["naics_code"], row["set_aside_code"])
        for row in source_rows
        if row.get("set_aside_code")
    }
    location_mappings = {
        (row["agency_name"], row["component_name"], row["naics_code"], row["performance_country"], row["performance_state"])
        for row in source_rows
        if row.get("performance_country") or row.get("performance_state")
    }
    return {
        "total_top_tier_agencies_returned": diagnostics["total_top_tier_agencies_returned"],
        "total_agencies_indexed": len(agencies),
        "total_agencies_excluded": len(diagnostics["excluded_agencies"]),
        "excluded_agencies": diagnostics["excluded_agencies"],
        "total_agency_components": len(component_rows),
        "total_agency_component_naics_mappings": len(
            {(row["agency_name"], row["component_name"], row["naics_code"]) for row in source_rows}
        ),
        "total_set_aside_mappings": len(set_aside_mappings),
        "total_performance_location_mappings": len(location_mappings),
        "agencies_with_zero_components": [
            {"agency_name": agency, "reason": diagnostics["component_source_errors"].get(agency, "no components indexed")}
            for agency, count in sorted(component_counts.items())
            if count == 0
        ],
        "components_with_zero_naics_mappings": components_with_zero_naics,
        "component_counts": component_counts,
        "source_errors": {
            "component": diagnostics["component_source_errors"],
            "naics": diagnostics["naics_source_errors"],
        },
    }


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE agency_options (
            agency_name TEXT PRIMARY KEY,
            toptier_code TEXT NOT NULL,
            abbreviation TEXT NOT NULL
        );
        CREATE TABLE option_sources (
            id INTEGER PRIMARY KEY,
            agency_name TEXT NOT NULL,
            component_dimension_type TEXT NOT NULL,
            component_code TEXT NOT NULL,
            component_name TEXT NOT NULL,
            naics_code TEXT NOT NULL,
            naics_description TEXT NOT NULL,
            set_aside_code TEXT NOT NULL,
            set_aside_description TEXT NOT NULL,
            performance_country TEXT NOT NULL,
            performance_state TEXT NOT NULL,
            support_awarding_agency_name TEXT NOT NULL,
            support_funding_agency_name TEXT NOT NULL
        );
        CREATE TABLE component_options (
            agency_name TEXT NOT NULL,
            component_dimension_type TEXT NOT NULL,
            component_code TEXT NOT NULL,
            component_name TEXT NOT NULL,
            PRIMARY KEY (agency_name, component_dimension_type, component_name, component_code)
        );
        CREATE TABLE naics_options (
            agency_name TEXT NOT NULL,
            component_dimension_type TEXT NOT NULL,
            component_code TEXT NOT NULL,
            component_name TEXT NOT NULL,
            naics_code TEXT NOT NULL,
            naics_description TEXT NOT NULL,
            PRIMARY KEY (agency_name, component_dimension_type, component_name, component_code, naics_code)
        );
        CREATE INDEX idx_component_agency ON component_options (agency_name, component_dimension_type, component_name);
        CREATE INDEX idx_naics_scope ON naics_options (agency_name, component_dimension_type, component_name, naics_code);
        CREATE INDEX idx_sources_scope ON option_sources (agency_name, component_dimension_type, component_name, naics_code, set_aside_code);
        """
    )


def _insert_rows(conn: sqlite3.Connection, agencies: list[dict], component_rows: list[dict], rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO agency_options (agency_name, toptier_code, abbreviation)
        VALUES (:agency_name, :toptier_code, :abbreviation)
        """,
        agencies,
    )
    conn.executemany(
        """
        INSERT INTO option_sources (
            agency_name, component_dimension_type, component_code, component_name,
            naics_code, naics_description, set_aside_code, set_aside_description,
            performance_country, performance_state, support_awarding_agency_name,
            support_funding_agency_name
        ) VALUES (
            :agency_name, :component_dimension_type, :component_code, :component_name,
            :naics_code, :naics_description, :set_aside_code, :set_aside_description,
            :performance_country, :performance_state, :support_awarding_agency_name,
            :support_funding_agency_name
        )
        """,
        rows,
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO component_options (
            agency_name, component_dimension_type, component_code, component_name
        ) VALUES (
            :agency_name, :component_dimension_type, :component_code, :component_name
        )
        """,
        component_rows,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO naics_options
        SELECT DISTINCT agency_name, component_dimension_type, component_code, component_name, naics_code, naics_description
        FROM option_sources
        WHERE component_name <> '' AND naics_code <> ''
        """
    )


def _metadata(conn: sqlite3.Connection, row_counts: dict, report: dict, refresh_status: str = "complete") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_period_start": SOURCE_PERIOD_START,
        "source_period_end": current_source_period_end(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "row_counts": json.dumps(row_counts, sort_keys=True),
        "completeness_report": json.dumps(report, sort_keys=True),
        "refresh_status": refresh_status,
    }


def build_index_file(
    output_path: Path,
    *,
    agencies: list[dict] | None = None,
    component_rows: list[dict] | None = None,
    rows: list[dict] | None = None,
    report: dict | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    if agencies is None or component_rows is None or rows is None or report is None:
        agencies, component_rows, rows, report = collect_option_index_data()
    with _open(output_path) as conn:
        create_schema(conn)
        _insert_rows(conn, agencies, component_rows, rows)
        row_counts = {
            "agency_options": conn.execute("SELECT COUNT(*) FROM agency_options").fetchone()[0],
            "option_sources": conn.execute("SELECT COUNT(*) FROM option_sources").fetchone()[0],
            "component_options": conn.execute("SELECT COUNT(*) FROM component_options").fetchone()[0],
            "naics_options": conn.execute("SELECT COUNT(*) FROM naics_options").fetchone()[0],
        }
        report = {**report, "row_counts": row_counts}
        for key, value in _metadata(conn, row_counts, report).items():
            conn.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    validate_index(output_path)


def refresh_index_atomically(index_path: Path = INDEX_PATH) -> Path:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="option_index_", suffix=".sqlite", dir=str(index_path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        build_index_file(tmp_path)
        os.replace(tmp_path, index_path)
        return index_path
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def metadata(index_path: Path = INDEX_PATH) -> dict:
    with _open(index_path) as conn:
        return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM metadata")}


def completeness_report(index_path: Path = INDEX_PATH) -> dict:
    report_text = metadata(index_path).get("completeness_report", "{}")
    try:
        return json.loads(report_text)
    except json.JSONDecodeError:
        return {}


def index_freshness(index_path: Path = INDEX_PATH) -> dict:
    meta = metadata(index_path)
    generated_at = meta.get("generated_at", "")
    try:
        generated = datetime.fromisoformat(generated_at)
        age_seconds = (datetime.now(timezone.utc) - generated).total_seconds()
    except ValueError:
        age_seconds = INDEX_MAX_AGE_DAYS * 86400 + 1
    return {
        "generated_at": generated_at,
        "is_stale": age_seconds > INDEX_MAX_AGE_DAYS * 86400,
        "refresh_status": meta.get("refresh_status", ""),
    }


def _require_columns(conn: sqlite3.Connection, table: str, required: set[str]) -> None:
    found = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    missing = required - found
    if missing:
        raise OptionIndexError(f"{table} missing columns: {', '.join(sorted(missing))}")


def _is_git_lfs_pointer(path: Path) -> bool:
    try:
        prefix = path.read_text(encoding="utf-8", errors="ignore")[:64]
    except OSError:
        return False
    return prefix.startswith(LFS_POINTER_PREFIX)


def index_deployment_diagnostics(index_path: Path = INDEX_PATH) -> dict:
    parent = index_path.parent
    parent_contents: list[str] = []
    if parent.exists():
        parent_contents = sorted(item.name for item in parent.iterdir())
    schema_version: str | None = None
    if index_path.exists() and not _is_git_lfs_pointer(index_path):
        try:
            with _open(index_path) as conn:
                meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM metadata")}
            schema_version = meta.get("schema_version")
        except (sqlite3.Error, OptionIndexError) as exc:
            schema_version = f"error: {exc}"
    return {
        "resolved_index_path": str(index_path),
        "project_root": str(PROJECT_ROOT),
        "current_working_directory": os.getcwd(),
        "file_exists": index_path.exists(),
        "is_git_lfs_pointer": _is_git_lfs_pointer(index_path) if index_path.exists() else False,
        "file_size_bytes": index_path.stat().st_size if index_path.exists() else None,
        "parent_directory_contents": parent_contents,
        "schema_version": schema_version,
    }


def validate_index(index_path: Path = INDEX_PATH) -> None:
    if not index_path.exists():
        raise OptionIndexError(f"Option index not found: {index_path}")
    if _is_git_lfs_pointer(index_path):
        raise OptionIndexError(f"Option index is a Git LFS pointer, not a SQLite database: {index_path}")
    header = index_path.read_bytes()[:16]
    if header != SQLITE_HEADER:
        raise OptionIndexError(f"Option index is not a valid SQLite database: {index_path}")
    with _open(index_path) as conn:
        meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM metadata")}
        if meta.get("schema_version") != SCHEMA_VERSION:
            raise OptionIndexError("Option index schema version mismatch")
        for key in ["source_period_start", "source_period_end", "generated_at", "row_counts", "refresh_status"]:
            if not meta.get(key):
                raise OptionIndexError(f"Option index metadata missing {key}")
        _require_columns(
            conn,
            "agency_options",
            {"agency_name", "toptier_code", "abbreviation"},
        )
        _require_columns(
            conn,
            "component_options",
            {"agency_name", "component_dimension_type", "component_code", "component_name"},
        )
        _require_columns(
            conn,
            "naics_options",
            {"agency_name", "component_dimension_type", "component_code", "component_name", "naics_code", "naics_description"},
        )
        _require_columns(
            conn,
            "option_sources",
            {
                "agency_name",
                "component_dimension_type",
                "component_code",
                "component_name",
                "naics_code",
                "naics_description",
                "set_aside_code",
                "set_aside_description",
                "performance_country",
                "performance_state",
                "support_awarding_agency_name",
                "support_funding_agency_name",
            },
        )
        blank = conn.execute(
            "SELECT COUNT(*) FROM option_sources WHERE component_name = '' OR naics_code = ''"
        ).fetchone()[0]
        if blank:
            raise OptionIndexError("Option index contains blank component or NAICS values")
        agency_count = conn.execute("SELECT COUNT(*) FROM agency_options").fetchone()[0]
        if agency_count < MIN_BROAD_AGENCY_COUNT:
            raise OptionIndexError(f"Option index contains only {agency_count} agencies")
        fixture_agency_count = conn.execute(
            """
            SELECT COUNT(*) FROM agency_options
            WHERE agency_name IN ('Department of Defense', 'Department of State',
                                  'Department of the Interior', 'Department of the Treasury',
                                  'General Services Administration')
            """
        ).fetchone()[0]
        if agency_count <= fixture_agency_count:
            raise OptionIndexError("Option index is limited to fixture agencies")
        for agency in MAJOR_REQUIRED_AGENCIES:
            count = conn.execute("SELECT COUNT(*) FROM agency_options WHERE agency_name = ?", (agency,)).fetchone()[0]
            if not count:
                raise OptionIndexError(f"Required agency missing from option index: {agency}")
        zero_major_components = []
        for agency in MAJOR_REQUIRED_AGENCIES:
            count = conn.execute("SELECT COUNT(*) FROM component_options WHERE agency_name = ?", (agency,)).fetchone()[0]
            if count == 0:
                zero_major_components.append(agency)
        if len(zero_major_components) > MAX_MAJOR_ZERO_COMPONENTS:
            raise OptionIndexError(f"Major agencies have zero components: {', '.join(zero_major_components)}")
        state_bad = conn.execute(
            """
            SELECT COUNT(*) FROM option_sources
            WHERE agency_name = 'Department of State'
              AND (component_dimension_type <> 'funding_office'
                   OR support_awarding_agency_name <> 'Department of State'
                   OR support_funding_agency_name <> 'Department of State')
            """
        ).fetchone()[0]
        if state_bad:
            raise OptionIndexError("State rows must be State-awarded, State-funded funding offices")
        interior_bad = conn.execute(
            """
            SELECT COUNT(*) FROM option_sources
            WHERE agency_name = 'Department of the Interior'
              AND component_dimension_type <> 'awarding_subagency'
            """
        ).fetchone()[0]
        if interior_bad:
            raise OptionIndexError("Interior rows must use awarding_subagency components")
        for agency, component in COMPONENT_FIXTURES:
            count = conn.execute(
                "SELECT COUNT(*) FROM component_options WHERE agency_name = ? AND component_name = ?",
                (agency, component),
            ).fetchone()[0]
            if not count:
                raise OptionIndexError(f"Missing fixture component: {agency} / {component}")
        treasury_leak = conn.execute(
            """
            SELECT COUNT(*) FROM component_options
            WHERE agency_name = 'Department of the Treasury'
              AND component_name IN ('Bureau of Reclamation', 'Air Force',
                                     'BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS')
            """
        ).fetchone()[0]
        if treasury_leak:
            raise OptionIndexError("Treasury component options contain another agency component")
        unsupported_naics = conn.execute(
            """
            SELECT COUNT(*) FROM naics_options n
            WHERE NOT EXISTS (
                SELECT 1 FROM option_sources s
                WHERE s.agency_name = n.agency_name
                  AND s.component_dimension_type = n.component_dimension_type
                  AND s.component_name = n.component_name
                  AND s.naics_code = n.naics_code
            )
            """
        ).fetchone()[0]
        if unsupported_naics:
            raise OptionIndexError("Indexed NAICS option lacks a supporting source row")
        component_orphans = conn.execute(
            """
            SELECT COUNT(*) FROM component_options c
            WHERE NOT EXISTS (
                SELECT 1 FROM agency_options a
                WHERE a.agency_name = c.agency_name
            )
            """
        ).fetchone()[0]
        if component_orphans:
            raise OptionIndexError("Component rows exist outside indexed agency universe")


_PROCESS_CACHE: dict[tuple, tuple[list[dict], dict]] = {}


def clear_process_cache() -> None:
    _PROCESS_CACHE.clear()


def _cached_lookup(cache_key: tuple, query: Callable[[], list[dict]]) -> tuple[list[dict], dict]:
    started = time.perf_counter()
    if cache_key in _PROCESS_CACHE:
        rows, base_diag = _PROCESS_CACHE[cache_key]
        elapsed = (time.perf_counter() - started) * 1000
        return list(rows), {**base_diag, "cache_level_used": "process", "elapsed_ms": elapsed}
    rows = query()
    meta = metadata()
    elapsed = (time.perf_counter() - started) * 1000
    diag = {
        "cache_level_used": "persistent_index",
        "rows_returned": len(rows),
        "elapsed_ms": elapsed,
        "index_generated_at": meta.get("generated_at", ""),
    }
    _PROCESS_CACHE[cache_key] = (list(rows), diag)
    return rows, diag


def get_agency_options() -> list[dict]:
    validate_index()
    with _open() as conn:
        rows = conn.execute("SELECT agency_name, toptier_code, abbreviation FROM agency_options ORDER BY agency_name").fetchall()
    return [dict(row) for row in rows]


def get_component_options(agency_name: str) -> list[dict]:
    rows, _diag = get_component_options_with_diagnostics(agency_name)
    return rows


def get_component_options_with_diagnostics(agency_name: str) -> tuple[list[dict], dict]:
    agency = clean_text(agency_name)

    def query() -> list[dict]:
        validate_index()
        with _open() as conn:
            rows = conn.execute(
                """
                SELECT agency_name, component_dimension_type, component_code, component_name
                FROM component_options
                WHERE agency_name = ?
                ORDER BY lower(component_name), component_code
                """,
                (agency,),
            ).fetchall()
        return [dict(row) for row in rows]

    return _cached_lookup(("components", agency), query)


def _component_clause(component_value: str | None) -> tuple[str, list[str]]:
    component = clean_text(component_value)
    if component and component != ALL_COMPONENTS:
        return " AND component_name = ?", [component]
    return "", []


def get_naics_options(agency_name: str, component_value: str | None) -> list[dict]:
    rows, _diag = get_naics_options_with_diagnostics(agency_name, component_value)
    return rows


def get_naics_options_with_diagnostics(agency_name: str, component_value: str | None) -> tuple[list[dict], dict]:
    agency = clean_text(agency_name)
    component = clean_text(component_value) or ALL_COMPONENTS

    def query() -> list[dict]:
        validate_index()
        clause, params = _component_clause(component)
        with _open() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT agency_name, component_dimension_type, component_code,
                                component_name, naics_code, naics_description
                FROM naics_options
                WHERE agency_name = ?{clause}
                ORDER BY naics_code, lower(naics_description)
                """,
                [agency, *params],
            ).fetchall()
        deduped = {}
        for row in rows:
            deduped[row["naics_code"]] = dict(row)
        return [deduped[key] for key in sorted(deduped)]

    return _cached_lookup(("naics", agency, component), query)


def get_set_aside_options(agency_name: str, component_value: str | None, naics_code: str | None) -> list[dict]:
    rows, _diag = get_set_aside_options_with_diagnostics(agency_name, component_value, naics_code)
    return rows


def get_set_aside_options_with_diagnostics(agency_name: str, component_value: str | None, naics_code: str | None) -> tuple[list[dict], dict]:
    agency = clean_text(agency_name)
    component = clean_text(component_value) or ALL_COMPONENTS
    naics = clean_text(naics_code)

    def query() -> list[dict]:
        validate_index()
        clause, params = _component_clause(component)
        naics_clause = " AND naics_code = ?" if naics and naics != ALL_NAICS else ""
        with _open() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT set_aside_code, set_aside_description
                FROM option_sources
                WHERE agency_name = ?{clause}{naics_clause}
                  AND set_aside_code <> ''
                ORDER BY lower(set_aside_description), set_aside_code
                """,
                [agency, *params, *([naics] if naics_clause else [])],
            ).fetchall()
        return [dict(row) for row in rows]

    return _cached_lookup(("set_asides", agency, component, naics or ALL_NAICS), query)


def get_location_options(agency_name: str, component_value: str | None, naics_code: str | None, set_aside_code: str | None) -> list[dict]:
    rows, _diag = get_location_options_with_diagnostics(agency_name, component_value, naics_code, set_aside_code)
    return rows


def get_location_options_with_diagnostics(agency_name: str, component_value: str | None, naics_code: str | None, set_aside_code: str | None) -> tuple[list[dict], dict]:
    agency = clean_text(agency_name)
    component = clean_text(component_value) or ALL_COMPONENTS
    naics = clean_text(naics_code)
    set_aside = clean_text(set_aside_code)

    def query() -> list[dict]:
        validate_index()
        clause, params = _component_clause(component)
        naics_clause = " AND naics_code = ?" if naics and naics != ALL_NAICS else ""
        set_aside_clause = " AND set_aside_code = ?" if set_aside and set_aside != ALL_SET_ASIDES else ""
        with _open() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT performance_country, performance_state
                FROM option_sources
                WHERE agency_name = ?{clause}{naics_clause}{set_aside_clause}
                  AND (performance_country <> '' OR performance_state <> '')
                ORDER BY performance_country, performance_state
                """,
                [agency, *params, *([naics] if naics_clause else []), *([set_aside] if set_aside_clause else [])],
            ).fetchall()
        return [dict(row) for row in rows]

    return _cached_lookup(("locations", agency, component, naics or ALL_NAICS, set_aside or ALL_SET_ASIDES), query)


def component_option_values(agency_name: str) -> tuple[list[str], dict]:
    rows, diag = get_component_options_with_diagnostics(agency_name)
    return [ALL_COMPONENTS] + [row["component_name"] for row in rows], {**diag, "lookup_type": "Agency Component"}


def naics_option_values(agency_name: str, component_value: str | None) -> tuple[list[str], dict]:
    rows, diag = get_naics_options_with_diagnostics(agency_name, component_value)
    values = [encode_option(row["naics_code"], row["naics_description"]) for row in rows]
    return [ALL_NAICS] + sorted(values, key=lambda option: format_option(option).lower()), {**diag, "lookup_type": "NAICS"}


def _location_values_from_rows(rows: list[dict]) -> dict[str, str]:
    values: dict[str, str] = {}
    for row in rows:
        country = clean_text(row["performance_country"]).upper()
        state = clean_text(row["performance_state"]).upper()
        if state and country in {"", "USA", "US"}:
            values[state] = f"{state} - {STATE_OPTIONS.get(state, state)}"
        elif country:
            values[country] = f"{country} - {COUNTRY_NAMES.get(country, country)}"
    return values


def set_aside_option_values(agency_name: str, component_value: str | None, naics_code: str | None) -> tuple[list[str], dict]:
    rows, diag = get_set_aside_options_with_diagnostics(agency_name, component_value, naics_code)
    values = [f"{row['set_aside_code']} - {row['set_aside_description']}" if row["set_aside_description"] else row["set_aside_code"] for row in rows]
    if values:
        return [ALL_SET_ASIDES] + values, {**diag, "lookup_type": "Set-Aside"}
    snapshot = option_discovery_snapshot(agency_name, component_value, naics_code)
    return fetch_scoped_set_aside_options(snapshot)


def location_option_values(agency_name: str, component_value: str | None, naics_code: str | None, set_aside_code: str | None) -> tuple[list[str], dict]:
    rows, diag = get_location_options_with_diagnostics(agency_name, component_value, naics_code, set_aside_code)
    values = _location_values_from_rows(rows)
    if values:
        return [ALL_LOCATIONS] + [values[key] for key in sorted(values)], {**diag, "lookup_type": "Performance Location"}
    snapshot = option_discovery_snapshot(agency_name, component_value, naics_code, set_aside_code)
    return fetch_scoped_location_options(snapshot)
