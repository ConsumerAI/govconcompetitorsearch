from __future__ import annotations

import functools
import csv
import hashlib
import io
import json
import time
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from concurrent.futures import ThreadPoolExecutor, as_completed

from .agency_components import get_agency_component_config
from .analysis import normalize_transactions
from .constants import (
    ALL_COMPONENTS,
    ALL_LOCATIONS,
    ALL_NAICS,
    ALL_SET_ASIDES,
    AWARD_OR_IDV_FLAG,
    AWARD_TYPE_CODES,
    BASE_URL,
    COUNTRY_NAMES,
    SET_ASIDE_TYPE_OPTIONS,
    STATE_OPTIONS,
)
from .state import FilterSnapshot, default_end_date, default_start_date, recent_wins_period
from .utils import clean_text, encode_option, format_option


CORE_TRANSACTION_FIELDS = [
    "Award ID",
    "Mod",
    "Transaction Description",
    "Transaction Amount",
    "Action Date",
    "Recipient Name",
    "Action Type",
    "Awarding Office",
    "Awarding Office Code",
    "Awarding Office Name",
    "Funding Office",
    "Funding Office Code",
    "Funding Office Name",
]
TRANSACTION_FIELDS = [
    *CORE_TRANSACTION_FIELDS,
    "NAICS",
    "PSC",
    "Primary Place of Performance",
]
BASE_TRANSACTION_FIELDS = [field for field in CORE_TRANSACTION_FIELDS if "Office" not in field]
DOWNLOAD_TRANSACTION_COLUMNS = [
    "contract_award_unique_key",
    "award_id_piid",
    "modification_number",
    "transaction_number",
    "transaction_description",
    "federal_action_obligation",
    "total_dollars_obligated",
    "current_total_value_of_award",
    "potential_total_value_of_award",
    "action_date",
    "action_type",
    "recipient_name",
    "recipient_uei",
    "awarding_agency_name",
    "awarding_sub_agency_name",
    "funding_agency_name",
    "funding_sub_agency_name",
    "naics_code",
    "naics_description",
    "product_or_service_code",
    "product_or_service_code_description",
    "awarding_office_code",
    "awarding_office_name",
    "funding_office_code",
    "funding_office_name",
    "type_of_set_aside",
    "primary_place_of_performance_country_code",
    "primary_place_of_performance_state_code",
]

OPTION_INDEX_DOWNLOAD_COLUMNS = DOWNLOAD_TRANSACTION_COLUMNS


@dataclass(frozen=True)
class ApiFailure:
    endpoint: str
    method: str
    payload: dict
    headers: dict
    status_code: int | None
    response_body: str
    message: str

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "method": self.method,
            "payload": self.payload,
            "headers": self.headers,
            "status_code": self.status_code,
            "response_body": self.response_body,
            "message": self.message,
        }


def request_headers() -> dict:
    return {
        "Accept": "application/json",
        "User-Agent": "govcon-competitor-finder/1.0",
    }


DOWNLOAD_GET_ATTEMPTS = 5
SEGMENT_ATTEMPTS = 3
FILE_DOWNLOAD_TIMEOUT = 60


@functools.lru_cache(maxsize=1)
def _http_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=DOWNLOAD_GET_ATTEMPTS,
        connect=DOWNLOAD_GET_ATTEMPTS,
        read=DOWNLOAD_GET_ATTEMPTS,
        backoff_factor=1.5,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=4)
    session.mount("https://", adapter)
    return session


def _get_with_retries(url: str, *, timeout: int, attempts: int = DOWNLOAD_GET_ATTEMPTS) -> tuple[requests.Response | None, str | None]:
    session = _http_session()
    headers = request_headers()
    last_error = ""
    for attempt in range(attempts):
        try:
            response = session.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response, None
        except requests.RequestException as exc:
            last_error = str(exc)
            if attempt + 1 < attempts:
                time.sleep(min(1.5 * (2**attempt), 12.0))
                continue
    return None, last_error


def _is_transient_download_error(diagnostics: dict) -> bool:
    body = str(diagnostics.get("response_body") or "")
    transient_markers = (
        "RemoteDisconnected",
        "Connection aborted",
        "Connection reset",
        "Connection refused",
        "Read timed out",
        "ReadTimeout",
        "ConnectTimeout",
        "timed out",
        "502",
        "503",
        "504",
    )
    return any(marker in body for marker in transient_markers)


def post_usaspending(endpoint: str, payload: dict, timeout: int = 24) -> tuple[dict | None, ApiFailure | None]:
    headers = request_headers()
    try:
        response = requests.post(f"{BASE_URL}{endpoint}", json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.json(), None
    except requests.exceptions.HTTPError as exc:
        response = exc.response
        body = response.text if response is not None else str(exc)
        status_code = response.status_code if response is not None else None
        return None, ApiFailure(endpoint, "POST", payload, headers, status_code, body, f"HTTP {status_code} from USAspending")
    except requests.RequestException as exc:
        return None, ApiFailure(endpoint, "POST", payload, headers, None, str(exc), f"{type(exc).__name__}: live USAspending request unavailable")
    except ValueError as exc:
        return None, ApiFailure(endpoint, "POST", payload, headers, None, str(exc), "Invalid JSON response from USAspending")


def _get(endpoint: str, params: dict | None = None, timeout: int = 18) -> dict:
    response = requests.get(f"{BASE_URL}{endpoint}", params=params, headers=request_headers(), timeout=timeout)
    response.raise_for_status()
    return response.json()


@functools.lru_cache(maxsize=1)
def fetch_toptier_agencies() -> list[dict]:
    try:
        payload = _get("/api/v2/references/toptier_agencies/")
    except (requests.RequestException, ValueError):
        return []
    records = []
    seen = set()
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        name = clean_text(item.get("agency_name"))
        code = clean_text(item.get("toptier_code"))
        active_fy = item.get("active_fy")
        if not name or not code or not active_fy or name.lower() in seen:
            continue
        seen.add(name.lower())
        records.append({"agency_name": name, "toptier_code": code, "abbreviation": clean_text(item.get("abbreviation"))})
    return sorted(records, key=lambda record: record["agency_name"])


def agency_record_by_name(agency_records: list[dict], agency_name: str) -> dict:
    for record in agency_records:
        if record.get("agency_name", "").lower() == clean_text(agency_name).lower():
            return record
    return {}


def _fetch_subagencies_uncached(toptier_code: str, fiscal_year: int | None = None) -> list[str]:
    if not toptier_code:
        return []
    all_results = []
    page = 1
    while True:
        payload = _get(
            f"/api/v2/agency/{toptier_code}/sub_agency/",
            {"fiscal_year": int(fiscal_year or current_fiscal_year()), "page": page},
        )
        all_results.extend(payload.get("results") or [])
        if not (payload.get("page_metadata") or {}).get("hasNext"):
            break
        page += 1
    names = []
    for item in all_results:
        if isinstance(item, str):
            names.append(clean_text(item))
            continue
        if not isinstance(item, dict):
            continue
        nested = item.get("subtier_agency") or item.get("agency")
        nested_name = nested.get("name") if isinstance(nested, dict) else ""
        names.append(
            clean_text(
                item.get("name")
                or item.get("agency_name")
                or item.get("subagency_name")
                or item.get("sub_agency_name")
                or item.get("subtier_name")
                or item.get("bureau_name")
                or nested_name
            )
        )
    return sorted({name for name in names if name})


_SUBAGENCY_CACHE: dict[tuple[str, int], list[str]] = {}


def fetch_subagencies(toptier_code: str, fiscal_year: int | None = None) -> list[str]:
    if not toptier_code:
        return []
    fy = int(fiscal_year or current_fiscal_year())
    cache_key = (toptier_code, fy)
    if cache_key in _SUBAGENCY_CACHE:
        return _SUBAGENCY_CACHE[cache_key]
    for attempt in range(3):
        try:
            names = _fetch_subagencies_uncached(toptier_code, fy)
            if names:
                _SUBAGENCY_CACHE[cache_key] = names
                return names
        except (requests.RequestException, ValueError, TypeError):
            pass
        time.sleep(1.5 * (attempt + 1))
    return []


def current_fiscal_year() -> int:
    today = date.today()
    return today.year + 1 if today.month >= 10 else today.year


def fiscal_year_date_range(fiscal_year: int | None = None) -> tuple[str, str]:
    fy = int(fiscal_year or current_fiscal_year())
    start = date(fy - 1, 10, 1)
    end = date(fy, 9, 30)
    today = date.today()
    if fy == current_fiscal_year() and today < end:
        end = today
    return start.isoformat(), end.isoformat()


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(str(value))


def period_metadata(snapshot: FilterSnapshot | None = None) -> dict:
    if snapshot and snapshot.start_date and snapshot.end_date:
        start_date, end_date = snapshot.start_date, snapshot.end_date
    else:
        start_date, end_date = fiscal_year_date_range()
    return {
        "fiscal_year": current_fiscal_year(),
        "start_date": start_date,
        "end_date": end_date,
        "label": f"{start_date} to {end_date}",
        "ytd_cutoff_logic": "explicit selected date range",
    }


def federal_fiscal_year_segments(start_date: str, end_date: str) -> list[dict]:
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    segments = []
    cursor = start
    while cursor <= end:
        fy_end = date(cursor.year if cursor.month <= 9 else cursor.year + 1, 9, 30)
        segment_end = min(fy_end, end)
        segments.append({"start_date": cursor.isoformat(), "end_date": segment_end.isoformat()})
        cursor = segment_end + timedelta(days=1)
    return segments


def agency_filter(
    agency_name: str,
    component: str = ALL_COMPONENTS,
    *,
    subtier_filter_type: str | None = None,
) -> list[dict]:
    config = get_agency_component_config(agency_name)
    agency = clean_text(agency_name)
    component_value = clean_text(component)
    if component_value and component_value != ALL_COMPONENTS:
        if config["dimension_type"] == "awarding_subagency":
            filter_type = clean_text(subtier_filter_type) or None
            if not filter_type:
                from .option_index import lookup_subtier_filter_type

                filter_type = lookup_subtier_filter_type(agency, component_value)
            if filter_type == "dual":
                return [{"type": "awarding", "tier": "toptier", "name": agency}]
            tier_type = "funding" if filter_type == "funding" else "awarding"
            return [{"type": tier_type, "tier": "subtier", "name": component_value, "toptier_name": agency}]
    return [{"type": "awarding", "tier": "toptier", "name": agency}]


def option_code(option: str) -> str:
    return clean_text(str(option or "").split(" - ", 1)[0].split("||", 1)[0])


def location_filter(location: str) -> dict | None:
    if not location or location == ALL_LOCATIONS:
        return None
    code = option_code(location).upper()
    if len(code) == 2:
        return {"country": "USA", "state": code}
    return {"country": code}


NEW_AWARDS_DATE_TYPE = "new_awards_only"


def base_filters(
    snapshot: FilterSnapshot,
    *,
    subtier_filter_type: str | None = None,
    date_type: str | None = None,
) -> dict:
    time_period = {"start_date": snapshot.start_date, "end_date": snapshot.end_date}
    if date_type:
        time_period["date_type"] = date_type
    filters = {
        "agencies": agency_filter(snapshot.agency, snapshot.component, subtier_filter_type=subtier_filter_type),
        "award_type_codes": AWARD_TYPE_CODES,
        "award_or_idv_flag": AWARD_OR_IDV_FLAG,
        "time_period": [time_period],
    }
    if snapshot.naics != ALL_NAICS:
        filters["naics_codes"] = {"require": [option_code(snapshot.naics)]}
    if snapshot.set_aside != ALL_SET_ASIDES:
        filters["set_aside_type_codes"] = [option_code(snapshot.set_aside)]
    pop_filter = location_filter(snapshot.location)
    if pop_filter:
        filters["place_of_performance_locations"] = [pop_filter]
    return filters


def transaction_payload(snapshot: FilterSnapshot, page: int = 1, limit: int = 100, include_office_fields: bool = True) -> dict:
    return {
        "filters": base_filters(snapshot),
        "fields": TRANSACTION_FIELDS if include_office_fields else BASE_TRANSACTION_FIELDS,
        "limit": limit,
        "page": page,
        "sort": "Action Date",
        "order": "desc",
    }


OPTION_DISCOVERY_DOWNLOAD_LIMIT = 10000


def transaction_download_payload(
    snapshot: FilterSnapshot,
    limit: int | None = None,
    *,
    columns: list[str] | None = None,
    date_type: str | None = None,
) -> dict:
    payload = {
        "filters": base_filters(snapshot, date_type=date_type),
        "columns": columns or DOWNLOAD_TRANSACTION_COLUMNS,
        "file_format": "csv",
    }
    if limit is not None:
        payload["limit"] = int(limit)
    return payload


def option_index_transaction_download_payload(snapshot: FilterSnapshot, limit: int | None = None) -> dict:
    return transaction_download_payload(snapshot, limit, columns=OPTION_INDEX_DOWNLOAD_COLUMNS)


def snapshot_for_segment(snapshot: FilterSnapshot, segment: dict) -> FilterSnapshot:
    return FilterSnapshot(
        agency=snapshot.agency,
        component=snapshot.component,
        naics=snapshot.naics,
        set_aside=snapshot.set_aside,
        location=snapshot.location,
        start_date=segment["start_date"],
        end_date=segment["end_date"],
    )


def category_options_payload(
    snapshot: FilterSnapshot,
    category: str,
    limit: int = 50,
    *,
    subtier_filter_type: str | None = None,
) -> dict:
    return {
        "category": category,
        "spending_level": "transactions",
        "limit": limit,
        "page": 1,
        "filters": base_filters(snapshot, subtier_filter_type=subtier_filter_type),
    }


def normalize_category_options(data: dict, default_option: str) -> list[str]:
    options = {}
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        code = clean_text(item.get("code") or item.get("id"))
        description = clean_text(item.get("name") or item.get("description"))
        if code:
            options[code] = encode_option(code, description)
    return [default_option] + sorted(options.values(), key=lambda option: format_option(option).lower())


def normalize_country_options(data: dict) -> list[str]:
    options = {}
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        code = clean_text(item.get("code") or item.get("id")).upper()
        description = clean_text(item.get("name") or item.get("description"))
        if code and code not in {"USA", "US", "FOREIGN"}:
            options[code] = encode_option(code, description)
    return sorted(options.values(), key=lambda option: format_option(option).lower())


@functools.lru_cache(maxsize=256)
def fetch_category_options_cached(
    agency: str,
    component: str,
    naics: str,
    set_aside: str,
    location: str,
    start_date: str,
    end_date: str,
    category: str,
    query_fingerprint: str,
) -> tuple[list[str], dict]:
    snapshot = FilterSnapshot(
        agency=agency,
        component=component,
        naics=naics,
        set_aside=set_aside,
        location=location,
        start_date=start_date,
        end_date=end_date,
    )
    default_option = ALL_NAICS if category == "naics" else ALL_LOCATIONS
    payload = category_options_payload(snapshot, category, limit=100 if category == "country" else 50)
    data, failure = post_usaspending(f"/api/v2/search/spending_by_category/{category}/", payload)
    if failure or not data:
        return [], {"payload": payload, "error": failure.to_dict() if failure else {"message": "No option data returned"}}
    if category == "country":
        return normalize_country_options(data), {"payload": payload, "error": None}
    return normalize_category_options(data, default_option), {"payload": payload, "error": None}


def fetch_naics_options(snapshot: FilterSnapshot) -> tuple[list[str], dict]:
    option_snapshot = FilterSnapshot(
        agency=snapshot.agency,
        component=snapshot.component,
        naics=ALL_NAICS,
        set_aside=snapshot.set_aside,
        location=snapshot.location,
        start_date=default_start_date(),
        end_date=default_end_date(),
    )
    return fetch_category_options_cached(
        option_snapshot.agency,
        option_snapshot.component,
        ALL_NAICS,
        option_snapshot.set_aside,
        option_snapshot.location,
        option_snapshot.start_date,
        option_snapshot.end_date,
        "naics",
        query_fingerprint(option_snapshot, option_category="naics"),
    )


def fetch_country_options(snapshot: FilterSnapshot) -> tuple[list[str], dict]:
    option_snapshot = FilterSnapshot(
        agency=snapshot.agency,
        component=snapshot.component,
        naics=snapshot.naics,
        set_aside=snapshot.set_aside,
        location=ALL_LOCATIONS,
        start_date=default_start_date(),
        end_date=default_end_date(),
    )
    return fetch_category_options_cached(
        option_snapshot.agency,
        option_snapshot.component,
        option_snapshot.naics,
        option_snapshot.set_aside,
        ALL_LOCATIONS,
        option_snapshot.start_date,
        option_snapshot.end_date,
        "country",
        query_fingerprint(option_snapshot, option_category="country"),
    )


def fetch_state_options(snapshot: FilterSnapshot) -> tuple[list[str], dict]:
    option_snapshot = FilterSnapshot(
        agency=snapshot.agency,
        component=snapshot.component,
        naics=snapshot.naics,
        set_aside=snapshot.set_aside,
        location=ALL_LOCATIONS,
        start_date=snapshot.start_date or default_start_date(),
        end_date=snapshot.end_date or default_end_date(),
    )
    return fetch_category_options_cached(
        option_snapshot.agency,
        option_snapshot.component,
        option_snapshot.naics,
        option_snapshot.set_aside,
        ALL_LOCATIONS,
        option_snapshot.start_date,
        option_snapshot.end_date,
        "state_territory",
        query_fingerprint(option_snapshot, option_category="state_territory"),
    )


def option_discovery_snapshot(
    agency: str,
    component: str | None,
    naics_code: str | None,
    set_aside_code: str | None = None,
) -> FilterSnapshot:
    component_value = clean_text(component) or ALL_COMPONENTS
    naics = clean_text(naics_code)
    naics_option = encode_option(naics, "") if naics and naics != ALL_NAICS else ALL_NAICS
    set_aside = clean_text(set_aside_code)
    if set_aside and set_aside != ALL_SET_ASIDES:
        set_aside_option = f"{set_aside} - {SET_ASIDE_TYPE_OPTIONS.get(set_aside, set_aside)}"
    else:
        set_aside_option = ALL_SET_ASIDES
    return FilterSnapshot(
        agency=agency,
        component=component_value,
        naics=naics_option,
        set_aside=set_aside_option,
        location=ALL_LOCATIONS,
        start_date=default_start_date(),
        end_date=default_end_date(),
    )


def _fetch_category_result_rows(
    snapshot: FilterSnapshot,
    category: str,
    *,
    max_pages: int = 20,
    limit: int = 100,
    subtier_filter_type: str | None = None,
) -> tuple[list[dict], dict]:
    results: list[dict] = []
    payloads: list[dict] = []
    for page in range(1, max_pages + 1):
        payload = category_options_payload(snapshot, category, limit=limit, subtier_filter_type=subtier_filter_type)
        payload["page"] = page
        payloads.append(payload)
        data, failure = post_usaspending(f"/api/v2/search/spending_by_category/{category}/", payload)
        if failure:
            return [], {"error": failure.to_dict(), "payloads": payloads}
        page_results = data.get("results") if isinstance(data, dict) else []
        if not page_results:
            break
        results.extend(item for item in page_results if isinstance(item, dict))
        page_meta = data.get("page_metadata") or {}
        if not page_meta.get("hasNext"):
            break
    return results, {"payloads": payloads, "error": None}


def _set_aside_code_has_spending(
    snapshot: FilterSnapshot,
    code: str,
    *,
    subtier_filter_type: str | None = None,
) -> bool:
    filters = base_filters(snapshot, subtier_filter_type=subtier_filter_type)
    filters["set_aside_type_codes"] = [code]
    payload = {
        "category": "country",
        "spending_level": "transactions",
        "limit": 1,
        "page": 1,
        "filters": filters,
    }
    data, failure = post_usaspending("/api/v2/search/spending_by_category/country/", payload)
    return bool(not failure and data and data.get("results"))


def _scoped_location_values(country_rows: list[dict], state_rows: list[dict]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in state_rows:
        if float(item.get("amount") or 0) <= 0:
            continue
        code = clean_text(item.get("code")).upper()
        if not code:
            continue
        name = clean_text(item.get("name")) or STATE_OPTIONS.get(code, code)
        values[code] = f"{code} - {name}"
    for item in country_rows:
        if float(item.get("amount") or 0) <= 0:
            continue
        code = clean_text(item.get("code")).upper()
        if not code or code in {"USA", "US"}:
            continue
        name = clean_text(item.get("name")) or COUNTRY_NAMES.get(code, code)
        values[code] = f"{code} - {name}"
    return values


def _fetch_scoped_set_aside_options_uncached(
    snapshot: FilterSnapshot,
    *,
    subtier_filter_type: str | None = None,
) -> tuple[list[str], dict]:
    codes: list[str] = []
    for code in SET_ASIDE_TYPE_OPTIONS:
        if _set_aside_code_has_spending(snapshot, code, subtier_filter_type=subtier_filter_type):
            codes.append(code)
    values = [
        f"{code} - {SET_ASIDE_TYPE_OPTIONS[code]}"
        for code in sorted(codes, key=lambda value: SET_ASIDE_TYPE_OPTIONS[value].lower())
    ]
    return [ALL_SET_ASIDES] + values, {
        "lookup_type": "Set-Aside",
        "cache_level_used": "live_api",
        "rows_returned": len(values),
    }


@functools.lru_cache(maxsize=128)
def fetch_scoped_set_aside_options_cached(
    agency: str,
    component: str,
    naics: str,
    start_date: str,
    end_date: str,
    query_fingerprint: str,
) -> tuple[tuple[str, ...], dict]:
    snapshot = FilterSnapshot(
        agency=agency,
        component=component,
        naics=naics,
        set_aside=ALL_SET_ASIDES,
        location=ALL_LOCATIONS,
        start_date=start_date,
        end_date=end_date,
    )
    codes: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_set_aside_code_has_spending, snapshot, code): code for code in SET_ASIDE_TYPE_OPTIONS}
        for future in as_completed(futures):
            code = futures[future]
            if future.result():
                codes.append(code)
    values = [
        f"{code} - {SET_ASIDE_TYPE_OPTIONS[code]}"
        for code in sorted(codes, key=lambda value: SET_ASIDE_TYPE_OPTIONS[value].lower())
    ]
    options = tuple([ALL_SET_ASIDES] + values)
    return options, {
        "lookup_type": "Set-Aside",
        "cache_level_used": "live_api",
        "rows_returned": max(0, len(options) - 1),
    }


def fetch_scoped_set_aside_options(
    snapshot: FilterSnapshot,
    *,
    subtier_filter_type: str | None = None,
) -> tuple[list[str], dict]:
    if subtier_filter_type:
        return _fetch_scoped_set_aside_options_uncached(snapshot, subtier_filter_type=subtier_filter_type)
    options, diag = fetch_scoped_set_aside_options_cached(
        snapshot.agency,
        snapshot.component,
        snapshot.naics,
        snapshot.start_date,
        snapshot.end_date,
        query_fingerprint(snapshot, option_category="set_aside"),
    )
    return list(options), diag


def _fetch_scoped_location_options_uncached(
    snapshot: FilterSnapshot,
    *,
    subtier_filter_type: str | None = None,
) -> tuple[list[str], dict]:
    with ThreadPoolExecutor(max_workers=2) as executor:
        country_future = executor.submit(
            _fetch_category_result_rows,
            snapshot,
            "country",
            subtier_filter_type=subtier_filter_type,
        )
        state_future = executor.submit(
            _fetch_category_result_rows,
            snapshot,
            "state_territory",
            subtier_filter_type=subtier_filter_type,
        )
        country_rows, country_diag = country_future.result()
        state_rows, state_diag = state_future.result()
    values = _scoped_location_values(country_rows, state_rows)
    options = [ALL_LOCATIONS] + [values[key] for key in sorted(values)]
    error = country_diag.get("error") or state_diag.get("error")
    return options, {
        "lookup_type": "Performance Location",
        "cache_level_used": "live_api",
        "rows_returned": max(0, len(options) - 1),
        "error": error,
    }


@functools.lru_cache(maxsize=128)
def fetch_scoped_location_options_cached(
    agency: str,
    component: str,
    naics: str,
    set_aside: str,
    start_date: str,
    end_date: str,
    query_fingerprint: str,
) -> tuple[tuple[str, ...], dict]:
    snapshot = FilterSnapshot(
        agency=agency,
        component=component,
        naics=naics,
        set_aside=set_aside,
        location=ALL_LOCATIONS,
        start_date=start_date,
        end_date=end_date,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        country_future = executor.submit(_fetch_category_result_rows, snapshot, "country")
        state_future = executor.submit(_fetch_category_result_rows, snapshot, "state_territory")
        country_rows, country_diag = country_future.result()
        state_rows, state_diag = state_future.result()
    values = _scoped_location_values(country_rows, state_rows)
    options = tuple([ALL_LOCATIONS] + [values[key] for key in sorted(values)])
    error = country_diag.get("error") or state_diag.get("error")
    return options, {
        "lookup_type": "Performance Location",
        "cache_level_used": "live_api",
        "rows_returned": max(0, len(options) - 1),
        "error": error,
    }


def fetch_scoped_location_options(
    snapshot: FilterSnapshot,
    *,
    subtier_filter_type: str | None = None,
) -> tuple[list[str], dict]:
    if subtier_filter_type:
        return _fetch_scoped_location_options_uncached(snapshot, subtier_filter_type=subtier_filter_type)
    options, diag = fetch_scoped_location_options_cached(
        snapshot.agency,
        snapshot.component,
        snapshot.naics,
        snapshot.set_aside,
        snapshot.start_date,
        snapshot.end_date,
        query_fingerprint(snapshot, option_category="location"),
    )
    return list(options), diag


def set_aside_options() -> list[str]:
    return [ALL_SET_ASIDES] + [f"{code} - {label}" for code, label in sorted(SET_ASIDE_TYPE_OPTIONS.items(), key=lambda item: item[1])]


def recent_wins_snapshot(snapshot: FilterSnapshot) -> FilterSnapshot:
    start_date, end_date = recent_wins_period()
    return FilterSnapshot(
        agency=snapshot.agency,
        component=snapshot.component,
        naics=snapshot.naics,
        set_aside=snapshot.set_aside,
        location=snapshot.location,
        start_date=start_date,
        end_date=end_date,
    )


def recent_wins_period_metadata(snapshot: FilterSnapshot | None = None) -> dict:
    metadata = period_metadata(snapshot)
    metadata["date_type"] = NEW_AWARDS_DATE_TYPE
    metadata["ytd_cutoff_logic"] = "rolling twelve-month new awards only"
    return metadata


def query_fingerprint(snapshot: FilterSnapshot, *, option_category: str = "", date_type: str = "") -> str:
    config = get_agency_component_config(snapshot.agency)
    period = {"option_data_version": "six-year-option-discovery"} if option_category else period_metadata(snapshot)
    if date_type:
        period = {**period, "date_type": date_type}
    payload = {
        "agency": snapshot.agency,
        "component": snapshot.component,
        "naics": snapshot.naics,
        "set_aside": snapshot.set_aside,
        "location": snapshot.location,
        "component_dimension_type": config["dimension_type"],
        "component_field_name": config["field_name"],
        "component_field_code": config["field_code"],
        "period": period,
        "award_type_codes": AWARD_TYPE_CODES,
        "award_or_idv_flag": AWARD_OR_IDV_FLAG,
        "download_columns": DOWNLOAD_TRANSACTION_COLUMNS,
        "option_category": option_category,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _download_finished(status_payload: dict) -> bool:
    return str(status_payload.get("status") or "").lower() == "finished"


def fetch_transaction_download_rows(
    snapshot: FilterSnapshot,
    *,
    timeout: int = 24,
    max_elapsed: float = 75.0,
    allow_truncated: bool = False,
    download_limit: int | None = OPTION_DISCOVERY_DOWNLOAD_LIMIT,
    columns: list[str] | None = None,
    date_type: str | None = None,
) -> tuple[list[dict], dict]:
    endpoint = "/api/v2/download/transactions/"
    payload = transaction_download_payload(snapshot, limit=download_limit, columns=columns, date_type=date_type)
    diagnostics = {
        "endpoint": endpoint,
        "method": "POST",
        "payload": payload,
        "headers": request_headers(),
        "status_code": None,
        "response_body": "",
        "status_poll_responses": [],
        "files_returned": [],
        "prime_files_loaded": [],
        "rows_per_file": {},
    }
    started_at = time.monotonic()
    data, failure = post_usaspending(endpoint, payload, timeout=timeout)
    if failure:
        diagnostics.update(failure.to_dict())
        return [], {"error": diagnostics}
    status_url = data.get("status_url") if isinstance(data, dict) else ""
    file_url = data.get("file_url") if isinstance(data, dict) else ""
    while status_url and time.monotonic() - started_at < max_elapsed:
        response, poll_error = _get_with_retries(status_url, timeout=timeout)
        if poll_error:
            diagnostics["response_body"] = poll_error
            if time.monotonic() - started_at < max_elapsed - 5:
                time.sleep(1.5)
                continue
            return [], {"error": diagnostics}
        diagnostics["status_code"] = response.status_code
        try:
            status_payload = response.json()
        except ValueError as exc:
            diagnostics["response_body"] = str(exc)
            if time.monotonic() - started_at < max_elapsed - 5:
                time.sleep(1.5)
                continue
            return [], {"error": diagnostics}
        diagnostics["status_poll_responses"].append(status_payload)
        if str(status_payload.get("status") or "").lower() == "failed":
            diagnostics["response_body"] = str(status_payload)
            return [], {"error": diagnostics}
        if _download_finished(status_payload):
            file_url = status_payload.get("file_url") or file_url
            break
        time.sleep(0.75)
    if not file_url:
        diagnostics["response_body"] = "download job timed out or did not return file_url"
        return [], {"error": diagnostics}
    try:
        file_response, download_error = _get_with_retries(file_url, timeout=max(timeout, FILE_DOWNLOAD_TIMEOUT))
        if download_error:
            diagnostics["response_body"] = download_error
            return [], {"error": diagnostics}
        diagnostics["status_code"] = file_response.status_code
        rows: list[dict] = []
        with zipfile.ZipFile(io.BytesIO(file_response.content)) as archive:
            diagnostics["files_returned"] = archive.namelist()
            prime_files = [name for name in archive.namelist() if "PrimeTransactions" in name]
            for filename in prime_files:
                with archive.open(filename) as csv_file:
                    reader = csv.DictReader(io.TextIOWrapper(csv_file, encoding="utf-8-sig"))
                    file_rows = [dict(row) for row in reader]
                    diagnostics["prime_files_loaded"].append(filename)
                    diagnostics["rows_per_file"][filename] = len(file_rows)
                    rows.extend(file_rows)
        diagnostics["row_count"] = len(rows)
        total_rows = None
        for status_payload in reversed(diagnostics["status_poll_responses"]):
            total_rows = status_payload.get("total_rows") or status_payload.get("total_records") or total_rows
            if total_rows is not None:
                break
        diagnostics["api_reported_total_rows"] = total_rows
        diagnostics["limit_reached"] = payload.get("limit") is not None and len(rows) >= int(payload.get("limit") or 0)
        diagnostics["truncation_detected"] = bool(total_rows is not None and int(total_rows) != len(rows))
        if (diagnostics["limit_reached"] or diagnostics["truncation_detected"]) and not allow_truncated:
            diagnostics["response_body"] = "download appears capped or truncated"
            return [], {"error": diagnostics}
        if diagnostics["limit_reached"] or diagnostics["truncation_detected"]:
            diagnostics["partial_download"] = True
        return rows, {"payload": payload, "diagnostics": diagnostics}
    except (requests.RequestException, zipfile.BadZipFile, OSError, UnicodeDecodeError, csv.Error) as exc:
        diagnostics["response_body"] = str(exc)
        return [], {"error": diagnostics}


def _transaction_identity(row: dict) -> tuple:
    return (
        clean_text(row.get("contract_award_unique_key")),
        clean_text(row.get("award_id_piid")),
        clean_text(row.get("modification_number")),
        clean_text(row.get("transaction_number")),
        clean_text(row.get("action_date")),
        clean_text(row.get("federal_action_obligation")),
    )


def _dedupe_exact_transactions(rows: list[dict]) -> tuple[list[dict], dict]:
    seen = set()
    deduped = []
    for row in rows:
        key = _transaction_identity(row)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped, {
        "rows_before_exact_dedupe": len(rows),
        "exact_duplicate_rows_removed": len(rows) - len(deduped),
        "rows_after_exact_dedupe": len(deduped),
    }


def fetch_transactions_uncached(
    agency: str,
    component: str,
    naics: str,
    set_aside: str,
    location: str,
    start_date: str,
    end_date: str,
    query_fingerprint_value: str,
    max_pages: int = 25,
    progress_callback=None,
    *,
    date_type: str = "",
) -> tuple[pd.DataFrame, dict]:
    snapshot = FilterSnapshot(agency=agency, component=component, naics=naics, set_aside=set_aside, location=location, start_date=start_date, end_date=end_date)
    segments = federal_fiscal_year_segments(start_date, end_date)
    all_download_rows: list[dict] = []
    payloads = []
    segment_diagnostics = []
    period = recent_wins_period_metadata(snapshot) if date_type == NEW_AWARDS_DATE_TYPE else period_metadata(snapshot)
    loading_label = "recent contract wins" if date_type == NEW_AWARDS_DATE_TYPE else "competitor data"
    for index, segment in enumerate(segments, start=1):
        if progress_callback:
            progress_callback(f"Loading {loading_label}: {index} of {len(segments)} periods")
        segment_snapshot = snapshot_for_segment(snapshot, segment)
        download_rows: list[dict] = []
        download_diag: dict = {}
        for attempt in range(1, SEGMENT_ATTEMPTS + 1):
            download_rows, download_diag = fetch_transaction_download_rows(
                segment_snapshot,
                date_type=date_type or None,
            )
            if not download_diag.get("error"):
                break
            error_diag = download_diag["error"]
            if attempt < SEGMENT_ATTEMPTS and _is_transient_download_error(error_diag):
                if progress_callback:
                    progress_callback(f"Retrying period {index} of {len(segments)} (attempt {attempt + 1})")
                time.sleep(2 * attempt)
                continue
            error = dict(error_diag)
            error["segment"] = segment
            error["attempts"] = attempt
            return normalize_transactions([], default_agency=agency), {
                "payloads": payloads + [transaction_download_payload(segment_snapshot, date_type=date_type or None)],
                "segments": segment_diagnostics,
                "period": period,
                "query_fingerprint": query_fingerprint_value,
                "error": "Unable to load the complete selected date range. No new analysis was applied.",
                "failures": [error],
            }
        payloads.append(download_diag.get("payload", transaction_download_payload(segment_snapshot)))
        segment_diag = download_diag.get("diagnostics", {})
        segment_diag["segment"] = segment
        segment_diagnostics.append(segment_diag)
        all_download_rows.extend(download_rows)
        if index < len(segments):
            time.sleep(0.25)
    if progress_callback:
        progress_callback("Combining transaction data")
    if all_download_rows:
        deduped_rows, dedupe_diag = _dedupe_exact_transactions(all_download_rows)
        if progress_callback:
            progress_callback("Calculating recent winners" if date_type == NEW_AWARDS_DATE_TYPE else "Calculating competitors")
        return normalize_transactions(deduped_rows, default_agency=agency), {
            "payloads": payloads,
            "segments": segment_diagnostics,
            "download": {"segments": segment_diagnostics, **dedupe_diag},
            "dedupe": dedupe_diag,
            "period": period,
            "query_fingerprint": query_fingerprint_value,
            "error": "",
            "failures": [],
        }
    rows = []
    payloads = [transaction_download_payload(snapshot, date_type=date_type or None)]
    failures = []
    for page in range(1, max_pages + 1):
        payload = transaction_payload(snapshot, page=page)
        payloads.append(payload)
        data, failure = post_usaspending("/api/v2/search/spending_by_transaction/", payload)
        if failure:
            fallback_payload = transaction_payload(snapshot, page=page, include_office_fields=False)
            payloads.append(fallback_payload)
            fallback_data, fallback_failure = post_usaspending("/api/v2/search/spending_by_transaction/", fallback_payload)
            if fallback_data:
                fallback_payload["office_fields_unavailable"] = True
                data = fallback_data
            else:
                failures.append(failure.to_dict())
                if fallback_failure:
                    failures.append(fallback_failure.to_dict())
                return normalize_transactions(rows, default_agency=agency), {
                    "payloads": payloads,
                    "error": "Unable to load USAspending data. No analysis was performed.",
                    "failures": failures,
                }
        page_rows = data.get("results") or []
        rows.extend(page_rows)
        if not page_rows or not (data.get("page_metadata") or {}).get("hasNext"):
            break
    return normalize_transactions(rows, default_agency=agency), {
        "payloads": payloads,
        "period": period,
        "query_fingerprint": query_fingerprint_value,
        "error": "",
        "failures": failures,
    }


@functools.lru_cache(maxsize=128)
def fetch_transactions_cached(
    agency: str,
    component: str,
    naics: str,
    set_aside: str,
    location: str,
    start_date: str,
    end_date: str,
    query_fingerprint_value: str,
    max_pages: int = 25,
    date_type: str = "",
) -> tuple[pd.DataFrame, dict]:
    return fetch_transactions_uncached(
        agency,
        component,
        naics,
        set_aside,
        location,
        start_date,
        end_date,
        query_fingerprint_value,
        max_pages=max_pages,
        date_type=date_type,
    )


def api_snapshot_for_fetch(snapshot: FilterSnapshot) -> FilterSnapshot:
    api_snapshot = snapshot
    config = get_agency_component_config(snapshot.agency)
    if config["dimension_type"] == "funding_office":
        api_snapshot = FilterSnapshot(
            agency=snapshot.agency,
            component=ALL_COMPONENTS,
            naics=snapshot.naics,
            set_aside=snapshot.set_aside,
            location=snapshot.location,
            start_date=snapshot.start_date,
            end_date=snapshot.end_date,
        )
    elif snapshot.component != ALL_COMPONENTS and config["dimension_type"] == "awarding_subagency":
        from .option_index import lookup_subtier_filter_type

        if lookup_subtier_filter_type(snapshot.agency, snapshot.component) == "dual":
            api_snapshot = FilterSnapshot(
                agency=snapshot.agency,
                component=ALL_COMPONENTS,
                naics=snapshot.naics,
                set_aside=snapshot.set_aside,
                location=snapshot.location,
                start_date=snapshot.start_date,
                end_date=snapshot.end_date,
            )
    return api_snapshot


def _fetch_transactions_for_api_snapshot(
    api_snapshot: FilterSnapshot,
    *,
    progress_callback=None,
    date_type: str = "",
) -> tuple[pd.DataFrame, dict]:
    fingerprint = query_fingerprint(api_snapshot, date_type=date_type)
    if progress_callback:
        return fetch_transactions_uncached(
            api_snapshot.agency,
            api_snapshot.component,
            api_snapshot.naics,
            api_snapshot.set_aside,
            api_snapshot.location,
            api_snapshot.start_date,
            api_snapshot.end_date,
            fingerprint,
            progress_callback=progress_callback,
            date_type=date_type,
        )
    return fetch_transactions_cached(
        api_snapshot.agency,
        api_snapshot.component,
        api_snapshot.naics,
        api_snapshot.set_aside,
        api_snapshot.location,
        api_snapshot.start_date,
        api_snapshot.end_date,
        fingerprint,
        date_type=date_type,
    )


def fetch_transactions_for_snapshot(snapshot: FilterSnapshot, progress_callback=None) -> tuple[pd.DataFrame, dict]:
    return _fetch_transactions_for_api_snapshot(api_snapshot_for_fetch(snapshot), progress_callback=progress_callback)


def fetch_recent_wins_for_snapshot(snapshot: FilterSnapshot, progress_callback=None) -> tuple[pd.DataFrame, dict]:
    return _fetch_transactions_for_api_snapshot(
        api_snapshot_for_fetch(recent_wins_snapshot(snapshot)),
        progress_callback=progress_callback,
        date_type=NEW_AWARDS_DATE_TYPE,
    )
