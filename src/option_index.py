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

import pandas as pd

from .agency_components import (
    build_agency_component_options,
    get_agency_component_config,
    infer_subtier_filter_type,
    transaction_component_names,
    transaction_matches_component,
)
from .analysis import normalize_transactions
from .constants import ALL_COMPONENTS, ALL_LOCATIONS, ALL_NAICS, ALL_SET_ASIDES, COUNTRY_NAMES, SET_ASIDE_TYPE_OPTIONS, STATE_OPTIONS
from .state import FilterSnapshot, default_end_date, default_start_date
from .usaspending import (
    OPTION_DISCOVERY_DOWNLOAD_LIMIT,
    OPTION_INDEX_DOWNLOAD_COLUMNS,
    _fetch_category_result_rows,
    fetch_scoped_set_aside_options,
    fetch_scoped_location_options,
    fetch_subagencies,
    fetch_toptier_agencies,
    fetch_transaction_download_rows,
    option_discovery_snapshot,
    option_code,
    post_usaspending,
)
from .utils import clean_text, encode_option, format_option


SCHEMA_VERSION = "3"
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
BUILD_PROGRESS_PATH = PROJECT_ROOT / "data" / "option_index_build.progress.json"
OPTIONAL_ENRICHMENT_MAX_SCOPES = 8000


class OptionIndexError(RuntimeError):
    pass


def _update_build_progress(phase: str, message: str = "", **fields) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "message": message,
        **fields,
    }
    BUILD_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = BUILD_PROGRESS_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, BUILD_PROGRESS_PATH)
    line = message or phase
    if "current" in fields and "total" in fields:
        line = f"{line} ({fields['current']}/{fields['total']})"
    print(f"[option-index] {line}", flush=True)


def _start_build_progress() -> None:
    _update_build_progress("starting", "Option index build started")


def _finish_build_progress(status: str, message: str = "", **fields) -> None:
    _update_build_progress(status, message, **fields)


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

FUNDING_OFFICE_NAME_ALIASES = {
    ("Department of State", "BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS"): {
        "BUREAU OF INTERNATIONAL NARCOTICS",
        "BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS",
    },
}


def _canonical_funding_office_name(agency_name: str, component_name: str) -> str:
    name = clean_text(component_name)
    for (agency, canonical), aliases in FUNDING_OFFICE_NAME_ALIASES.items():
        if agency_name == agency and name in {clean_text(alias) for alias in aliases}:
            return canonical
    return name


def _funding_office_match_names(agency_name: str, component_name: str) -> set[str]:
    canonical = _canonical_funding_office_name(agency_name, component_name)
    for (agency, alias_canonical), aliases in FUNDING_OFFICE_NAME_ALIASES.items():
        if agency_name == agency and canonical == alias_canonical:
            return {clean_text(alias) for alias in aliases}
    return {canonical}


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


def _agency_filters(
    agency_name: str,
    component_name: str | None = None,
    *,
    subtier_type: str = "awarding",
) -> list[dict]:
    config = get_agency_component_config(agency_name)
    component = clean_text(component_name)
    if component and config["dimension_type"] == "awarding_subagency":
        return [{"type": subtier_type, "tier": "subtier", "name": component, "toptier_name": clean_text(agency_name)}]
    return [{"type": "awarding", "tier": "toptier", "name": clean_text(agency_name)}]


def _category_payload(
    agency_name: str,
    component_name: str | None,
    category: str,
    page: int,
    limit: int = 100,
    *,
    subtier_type: str = "awarding",
) -> dict:
    return {
        "category": category,
        "spending_level": "transactions",
        "limit": limit,
        "page": page,
        "filters": {
            "agencies": _agency_filters(agency_name, component_name, subtier_type=subtier_type),
            "award_type_codes": ["A", "B", "C", "D"],
            "award_or_idv_flag": "AWARD",
            "time_period": [{"start_date": SOURCE_PERIOD_START, "end_date": current_source_period_end()}],
        },
    }


def _category_options(
    agency_name: str,
    component_name: str | None,
    category: str,
    max_pages: int = 100,
    *,
    subtier_type: str = "awarding",
) -> tuple[list[dict], dict]:
    results = []
    payloads = []
    for page in range(1, max_pages + 1):
        payload = _category_payload(agency_name, component_name, category, page, subtier_type=subtier_type)
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
                "subtier_filter_type": "awarding",
            }
        )
    return rows


def _funding_office_component_rows(agency_name: str, transactions: pd.DataFrame) -> list[dict]:
    config = get_agency_component_config(agency_name)
    options = build_agency_component_options(transactions, agency_name)
    rows = []
    seen = set()
    for option in options:
        name = _canonical_funding_office_name(agency_name, clean_text(option.get("name") or option.get("value")))
        if not name or name == ALL_COMPONENTS:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "agency_name": agency_name,
                "component_dimension_type": config["dimension_type"],
                "component_code": clean_text(option.get("code")),
                "component_name": name,
                "subtier_filter_type": "awarding",
            }
        )
    return rows


def _default_component_row(agency_name: str, config: dict, component_name: str, component_code: str = "") -> dict:
    return {
        "agency_name": agency_name,
        "component_dimension_type": config["dimension_type"],
        "component_code": clean_text(component_code),
        "component_name": clean_text(component_name),
        "subtier_filter_type": "awarding",
    }


def _apply_subtier_filter_types(component_rows: list[dict], agency_transactions: dict[str, pd.DataFrame]) -> None:
    by_agency: dict[str, list[dict]] = {}
    for row in component_rows:
        by_agency.setdefault(row["agency_name"], []).append(row)
    for agency, rows in by_agency.items():
        frame = agency_transactions.get(agency, pd.DataFrame())
        for row in rows:
            row["subtier_filter_type"] = infer_subtier_filter_type(frame, row["component_name"])


def _component_lookup(component_rows: list[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for row in component_rows:
        lookup[row["component_name"].lower()] = row
    return lookup


def _discover_components_from_transactions(agency_name: str, transactions: pd.DataFrame) -> list[dict]:
    if transactions is None or transactions.empty:
        return []
    config = get_agency_component_config(agency_name)
    rows = []
    seen: set[str] = set()
    for record in transactions.to_dict("records"):
        if float(record.get("federal_action_obligation") or 0) == 0:
            continue
        for name in transaction_component_names(record, config, agency_name):
            canonical = (
                _canonical_funding_office_name(agency_name, name)
                if config["dimension_type"] == "funding_office"
                else name
            )
            if not canonical or canonical.lower() in seen:
                continue
            seen.add(canonical.lower())
            rows.append(
                {
                    "agency_name": agency_name,
                    "component_dimension_type": config["dimension_type"],
                    "component_code": "",
                    "component_name": canonical,
                    "subtier_filter_type": infer_subtier_filter_type(transactions, canonical),
                }
            )
    return rows


def _source_rows_from_agency_transactions(
    agency_name: str,
    component_lookup: dict[str, dict],
    transactions: pd.DataFrame,
) -> list[dict]:
    if transactions is None or transactions.empty or not component_lookup:
        return []

    config = get_agency_component_config(agency_name)
    if config["dimension_type"] == "funding_office":
        if config["field_name"] not in transactions.columns:
            return []
    elif "awarding_sub_agency_name" not in transactions.columns and "funding_sub_agency_name" not in transactions.columns:
        return []

    rows: list[dict] = []
    seen: set[tuple] = set()
    descriptions: dict[tuple[str, str], str] = {}

    for record in transactions.to_dict("records"):
        if float(record.get("federal_action_obligation") or 0) == 0:
            continue
        naics_code = clean_text(record.get("naics_code"))
        if not naics_code:
            continue
        naics_description = clean_text(record.get("naics_description"))
        matched_components: list[tuple[str, dict]] = []
        for component_meta in component_lookup.values():
            component_name = component_meta["component_name"]
            if transaction_matches_component(record, component_name, config, agency_name):
                matched_components.append((component_name, component_meta))
        if not matched_components:
            continue

        set_aside_code, set_aside_description = _normalize_set_aside(record.get("set_aside_type"))
        country = clean_text(record.get("place_of_performance_country_code")).upper()
        state = clean_text(record.get("place_of_performance_state_code")).upper()

        for component_name, component_meta in matched_components:
            descriptions[(component_name, naics_code)] = naics_description or descriptions.get((component_name, naics_code), "")
            combos = [(component_name, naics_code, "", "", "")]
            if set_aside_code:
                combos.append((component_name, naics_code, set_aside_code, "", ""))
            if country or state:
                combos.append((component_name, naics_code, set_aside_code, country, state))

            for comp, naics, sa_code, ctry, st in combos:
                key = (comp, naics, sa_code, ctry, st)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "agency_name": agency_name,
                        "component_dimension_type": component_meta["component_dimension_type"],
                        "component_code": component_meta["component_code"],
                        "component_name": comp,
                        "naics_code": naics,
                        "naics_description": descriptions[(comp, naics)],
                        "set_aside_code": sa_code,
                        "set_aside_description": set_aside_description if sa_code else "",
                        "performance_country": ctry,
                        "performance_state": st,
                        "support_awarding_agency_name": agency_name,
                        "support_funding_agency_name": agency_name if config["dimension_type"] == "funding_office" else "",
                    }
                )
    return rows


def _subtier_types_for_component(component: dict) -> list[str]:
    filter_type = clean_text(component.get("subtier_filter_type")) or "awarding"
    if filter_type == "funding":
        return ["funding", "awarding"]
    if filter_type == "dual":
        return ["awarding", "funding"]
    return ["awarding", "funding"]


def _prioritize_optional_scopes(scopes: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    major = {agency.lower() for agency in MAJOR_REQUIRED_AGENCIES}

    def sort_key(scope: tuple[str, str, str]) -> tuple[int, str, str, str]:
        agency, component, naics = scope
        return (0 if agency.lower() in major else 1, agency, component, naics)

    return sorted(scopes, key=sort_key)


def _append_source_rows(rows: list[dict], existing_keys: set[tuple], source_rows: list[dict]) -> None:
    for row in rows:
        key = (
            row["component_name"],
            row["naics_code"],
            row["set_aside_code"],
            row["performance_country"],
            row["performance_state"],
        )
        if key in existing_keys:
            continue
        existing_keys.add(key)
        source_rows.append(row)


def _category_naics_rows(agency_name: str, component: dict) -> tuple[list[dict], dict | None]:
    component_name = component["component_name"]
    subtier_types = _subtier_types_for_component(component)
    seen_codes: set[str] = set()
    rows: list[dict] = []
    last_error = None
    for subtier_type in subtier_types:
        if subtier_type in {"dual"}:
            continue
        naics_results, diag = _category_options(agency_name, component_name, "naics", subtier_type=subtier_type)
        if diag.get("error"):
            last_error = diag["error"]
            continue
        for item in naics_results:
            if float(item.get("amount") or 0) <= 0:
                continue
            code = clean_text(item.get("code") or item.get("id"))
            description = clean_text(item.get("name") or item.get("description"))
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            rows.append(
                {
                    "agency_name": agency_name,
                    "component_dimension_type": component["component_dimension_type"],
                    "component_code": component["component_code"],
                    "component_name": component_name,
                    "naics_code": code,
                    "naics_description": description,
                    "set_aside_code": "",
                    "set_aside_description": "",
                    "performance_country": "",
                    "performance_state": "",
                    "support_awarding_agency_name": agency_name,
                    "support_funding_agency_name": agency_name if component["component_dimension_type"] == "funding_office" else "",
                }
            )
        if rows:
            return rows, None
    if last_error:
        return [], last_error
    return rows, None


def _enrich_optional_rows_from_api(
    agency_name: str,
    component: dict,
    naics_code: str,
    existing_keys: set[tuple],
) -> list[dict]:
    component_name = component["component_name"]
    subtier_filter_type = clean_text(component.get("subtier_filter_type")) or "awarding"
    snapshot = option_discovery_snapshot(agency_name, component_name, naics_code)
    rows: list[dict] = []
    set_aside_options, _diag = fetch_scoped_set_aside_options(snapshot, subtier_filter_type=subtier_filter_type)
    for option in set_aside_options[1:]:
        set_aside_code = option_code(option)
        if not set_aside_code:
            continue
        _, set_aside_description = _normalize_set_aside(option)
        base_key = (component_name, naics_code, set_aside_code, "", "")
        if base_key not in existing_keys:
            existing_keys.add(base_key)
            rows.append(
                {
                    "agency_name": agency_name,
                    "component_dimension_type": component["component_dimension_type"],
                    "component_code": component["component_code"],
                    "component_name": component_name,
                    "naics_code": naics_code,
                    "naics_description": "",
                    "set_aside_code": set_aside_code,
                    "set_aside_description": set_aside_description,
                    "performance_country": "",
                    "performance_state": "",
                    "support_awarding_agency_name": agency_name,
                    "support_funding_agency_name": agency_name if component["component_dimension_type"] == "funding_office" else "",
                }
            )
        snapshot_with_set_aside = option_discovery_snapshot(agency_name, component_name, naics_code, set_aside_code)
        location_options, _location_diag = fetch_scoped_location_options(
            snapshot_with_set_aside,
            subtier_filter_type=subtier_filter_type,
        )
        for location in location_options[1:]:
            loc_code = option_code(location).upper()
            if len(loc_code) == 2:
                country, state = "USA", loc_code
            else:
                country, state = loc_code, ""
            loc_key = (component_name, naics_code, set_aside_code, country, state)
            if loc_key in existing_keys:
                continue
            existing_keys.add(loc_key)
            rows.append(
                {
                    "agency_name": agency_name,
                    "component_dimension_type": component["component_dimension_type"],
                    "component_code": component["component_code"],
                    "component_name": component_name,
                    "naics_code": naics_code,
                    "naics_description": "",
                    "set_aside_code": set_aside_code,
                    "set_aside_description": set_aside_description,
                    "performance_country": country,
                    "performance_state": state,
                    "support_awarding_agency_name": agency_name,
                    "support_funding_agency_name": agency_name if component["component_dimension_type"] == "funding_office" else "",
                }
            )
    if not set_aside_options[1:]:
        state_rows, _state_diag = _fetch_category_result_rows(
            snapshot,
            "state_territory",
            max_pages=5,
            subtier_filter_type=subtier_filter_type,
        )
        country_rows, _country_diag = _fetch_category_result_rows(
            snapshot,
            "country",
            max_pages=5,
            subtier_filter_type=subtier_filter_type,
        )
        for item in state_rows:
            if float(item.get("amount") or 0) <= 0:
                continue
            state = clean_text(item.get("code") or item.get("id")).upper()
            if not state:
                continue
            loc_key = (component_name, naics_code, "", "USA", state)
            if loc_key in existing_keys:
                continue
            existing_keys.add(loc_key)
            rows.append(
                {
                    "agency_name": agency_name,
                    "component_dimension_type": component["component_dimension_type"],
                    "component_code": component["component_code"],
                    "component_name": component_name,
                    "naics_code": naics_code,
                    "naics_description": "",
                    "set_aside_code": "",
                    "set_aside_description": "",
                    "performance_country": "USA",
                    "performance_state": state,
                    "support_awarding_agency_name": agency_name,
                    "support_funding_agency_name": agency_name if component["component_dimension_type"] == "funding_office" else "",
                }
            )
        for item in country_rows:
            if float(item.get("amount") or 0) <= 0:
                continue
            country = clean_text(item.get("code") or item.get("id")).upper()
            if not country or country in {"USA", "US"}:
                continue
            loc_key = (component_name, naics_code, "", country, "")
            if loc_key in existing_keys:
                continue
            existing_keys.add(loc_key)
            rows.append(
                {
                    "agency_name": agency_name,
                    "component_dimension_type": component["component_dimension_type"],
                    "component_code": component["component_code"],
                    "component_name": component_name,
                    "naics_code": naics_code,
                    "naics_description": "",
                    "set_aside_code": "",
                    "set_aside_description": "",
                    "performance_country": country,
                    "performance_state": "",
                    "support_awarding_agency_name": agency_name,
                    "support_funding_agency_name": agency_name if component["component_dimension_type"] == "funding_office" else "",
                }
            )
    return rows


def _scopes_missing_optional_data(source_rows: list[dict]) -> list[tuple[str, str, str]]:
    naics_scopes = {
        (row["agency_name"], row["component_name"], row["naics_code"])
        for row in source_rows
        if row.get("naics_code")
    }
    scopes_with_set_aside = {
        (row["agency_name"], row["component_name"], row["naics_code"])
        for row in source_rows
        if row.get("set_aside_code")
    }
    scopes_with_location = {
        (row["agency_name"], row["component_name"], row["naics_code"])
        for row in source_rows
        if row.get("performance_country") or row.get("performance_state")
    }
    return sorted(
        scope
        for scope in naics_scopes
        if scope not in scopes_with_set_aside or scope not in scopes_with_location
    )


def collect_option_index_data() -> tuple[list[dict], list[dict], list[dict], dict]:
    _start_build_progress()
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
    agency_transactions: dict[str, pd.DataFrame] = {}
    diagnostics = {
        "total_top_tier_agencies_returned": len(live_agencies),
        "excluded_agencies": excluded,
        "component_source_errors": {},
        "naics_source_errors": {},
        "optional_source_errors": {},
        "partial_download_agencies": {},
    }

    for index, agency_record in enumerate(agencies, start=1):
        agency = agency_record["agency_name"]
        _update_build_progress(
            "download_agency_transactions",
            f"Downloading transactions for {agency}",
            current=index,
            total=len(agencies),
            agency=agency,
        )
        frame, tx_diag = _fetch_agency_transactions_for_index_build(agency)
        agency_transactions[agency] = frame
        if tx_diag.get("partial_download"):
            diagnostics["partial_download_agencies"][agency] = {
                "rows_returned": tx_diag.get("rows_returned"),
                "partial_download": True,
            }
        if frame.empty:
            diagnostics["component_source_errors"][agency] = tx_diag.get("error", "no transactions returned for option index build")

        config = get_agency_component_config(agency)
        if config["dimension_type"] == "funding_office":
            discovered_components = (
                _funding_office_component_rows(agency, frame)
                if not frame.empty
                else (_state_component_rows() if agency == "Department of State" else [])
            )
        else:
            names = fetch_subagencies(agency_record["toptier_code"])
            discovered_components = [
                _default_component_row(agency, config, clean_text(name))
                for name in names
                if clean_text(name)
            ]
            discovered_components.extend(_discover_components_from_transactions(agency, frame))
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
                    "subtier_filter_type": "awarding",
                }
            )

    _apply_subtier_filter_types(component_rows, agency_transactions)
    _update_build_progress(
        "derive_transaction_rows",
        "Deriving NAICS, set-aside, and location rows from agency transactions",
        agencies_total=len(agencies),
        components_total=len(component_rows),
    )

    components_by_agency: dict[str, list[dict]] = {}
    for row in component_rows:
        components_by_agency.setdefault(row["agency_name"], []).append(row)

    existing_keys: set[tuple] = set()
    for agency, agency_components in components_by_agency.items():
        frame = agency_transactions.get(agency, pd.DataFrame())
        lookup = _component_lookup(agency_components)
        tx_rows = _source_rows_from_agency_transactions(agency, lookup, frame)
        for row in tx_rows:
            existing_keys.add(
                (
                    row["component_name"],
                    row["naics_code"],
                    row["set_aside_code"],
                    row["performance_country"],
                    row["performance_state"],
                )
            )
        source_rows.extend(tx_rows)

    _update_build_progress(
        "supplement_category_naics",
        "Supplementing NAICS from category API for all components",
        components_total=len(component_rows),
        source_rows=len(source_rows),
    )
    for index, component in enumerate(component_rows, start=1):
        agency = component["agency_name"]
        component_name = component["component_name"]
        existing_naics = {
            row["naics_code"]
            for row in source_rows
            if row["agency_name"] == agency and row["component_name"] == component_name and row.get("naics_code")
        }
        if index == 1 or index % 25 == 0 or index == len(component_rows):
            _update_build_progress(
                "supplement_category_naics",
                f"Category NAICS supplement for {agency} / {component_name}",
                current=index,
                total=len(component_rows),
                source_rows=len(source_rows),
            )
        naics_rows, err = _category_naics_rows(agency, component)
        if err:
            diagnostics["naics_source_errors"][f"{agency} / {component_name}"] = err
            continue
        new_rows = [row for row in naics_rows if row["naics_code"] not in existing_naics]
        if not new_rows and not existing_naics:
            diagnostics["naics_source_errors"][f"{agency} / {component_name}"] = "no NAICS returned for scope"
            continue
        _append_source_rows(new_rows, existing_keys, source_rows)

    optional_scopes = _prioritize_optional_scopes(_scopes_missing_optional_data(source_rows))
    optional_total = len(optional_scopes)
    optional_limit = min(optional_total, OPTIONAL_ENRICHMENT_MAX_SCOPES)
    _update_build_progress(
        "optional_api_enrichment",
        "Enriching set-aside and performance location options from scoped API",
        current=0,
        total=optional_limit,
        optional_scopes_total=optional_total,
        optional_scopes_capped=optional_total > OPTIONAL_ENRICHMENT_MAX_SCOPES,
        source_rows=len(source_rows),
    )
    for index, (agency, component_name, naics_code) in enumerate(optional_scopes[:optional_limit], start=1):
        if index == 1 or index % 10 == 0 or index == optional_limit:
            _update_build_progress(
                "optional_api_enrichment",
                f"Optional API enrichment for {agency} / {component_name} / {naics_code}",
                current=index,
                total=optional_limit,
                optional_scopes_total=optional_total,
                optional_scopes_capped=optional_total > OPTIONAL_ENRICHMENT_MAX_SCOPES,
                source_rows=len(source_rows),
            )
        component = next(
            (row for row in component_rows if row["agency_name"] == agency and row["component_name"] == component_name),
            None,
        )
        if not component:
            continue
        try:
            source_rows.extend(_enrich_optional_rows_from_api(agency, component, naics_code, existing_keys))
        except Exception as exc:
            diagnostics["optional_source_errors"][f"{agency} / {component_name} / {naics_code}"] = str(exc)

    # Fixture rows remain validation supplements, not the agency universe.
    for fixture in fixture_rows:
        if fixture["agency_name"].lower() in seen:
            source_rows.append(dict(fixture))

    report = _completeness_report(agencies, component_rows, source_rows, diagnostics)
    _finish_build_progress(
        "collect_complete",
        "Collected option index source data",
        agencies_total=len(agencies),
        components_total=len(component_rows),
        source_rows=len(source_rows),
        optional_scopes_total=optional_total,
        optional_scopes_enriched=optional_limit,
    )
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
        "agencies_with_partial_transaction_downloads": sorted(diagnostics.get("partial_download_agencies", {}).keys()),
        "partial_download_agencies": diagnostics.get("partial_download_agencies", {}),
        "component_counts": component_counts,
        "source_errors": {
            "component": diagnostics["component_source_errors"],
            "naics": diagnostics["naics_source_errors"],
            "optional": diagnostics.get("optional_source_errors", {}),
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
            subtier_filter_type TEXT NOT NULL DEFAULT 'awarding',
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
    for row in component_rows:
        row.setdefault("subtier_filter_type", "awarding")
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
            agency_name, component_dimension_type, component_code, component_name, subtier_filter_type
        ) VALUES (
            :agency_name, :component_dimension_type, :component_code, :component_name, :subtier_filter_type
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
        meta = metadata(index_path)
        _finish_build_progress(
            "complete",
            f"Option index build complete: {index_path}",
            schema_version=meta.get("schema_version"),
            generated_at=meta.get("generated_at"),
            row_counts=meta.get("row_counts"),
        )
        return index_path
    except Exception as exc:
        _finish_build_progress("failed", f"Option index build failed: {exc}")
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


def _fetch_agency_transactions_for_index_build(agency_name: str) -> tuple[pd.DataFrame, dict]:
    agency = clean_text(agency_name)
    snapshot = FilterSnapshot(
        agency=agency,
        component=ALL_COMPONENTS,
        naics=ALL_NAICS,
        set_aside=ALL_SET_ASIDES,
        location=ALL_LOCATIONS,
        start_date=default_start_date(),
        end_date=default_end_date(),
    )
    rows, diag = fetch_transaction_download_rows(
        snapshot,
        max_elapsed=120.0,
        allow_truncated=True,
        download_limit=OPTION_DISCOVERY_DOWNLOAD_LIMIT,
        columns=OPTION_INDEX_DOWNLOAD_COLUMNS,
    )
    if diag.get("error") and not rows:
        return pd.DataFrame(), diag
    frame = normalize_transactions(rows, default_agency=agency)
    diagnostics = diag.get("diagnostics") or {}
    return frame, {
        "rows_returned": len(frame),
        "partial_download": diagnostics.get("partial_download") or diagnostics.get("limit_reached"),
    }


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


def lookup_subtier_filter_type(agency_name: str, component_name: str) -> str:
    agency = clean_text(agency_name)
    component = clean_text(component_name)
    if not agency or not component:
        return "awarding"
    try:
        validate_index()
        with _open() as conn:
            row = conn.execute(
                """
                SELECT subtier_filter_type
                FROM component_options
                WHERE agency_name = ? AND component_name = ?
                LIMIT 1
                """,
                (agency, component),
            ).fetchone()
        if row and clean_text(row["subtier_filter_type"]):
            return clean_text(row["subtier_filter_type"])
    except OptionIndexError:
        pass
    return "awarding"


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
    seen = set()
    values = [ALL_COMPONENTS]
    for row in rows:
        name = row["component_name"]
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        values.append(name)
    return values, {**diag, "lookup_type": "Agency Component"}


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
    return [ALL_SET_ASIDES] + values, {**diag, "lookup_type": "Set-Aside"}


def location_option_values(agency_name: str, component_value: str | None, naics_code: str | None, set_aside_code: str | None) -> tuple[list[str], dict]:
    rows, diag = get_location_options_with_diagnostics(agency_name, component_value, naics_code, set_aside_code)
    values = _location_values_from_rows(rows)
    return [ALL_LOCATIONS] + [values[key] for key in sorted(values)], {**diag, "lookup_type": "Performance Location"}
