from __future__ import annotations

import html
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date
import time

import pandas as pd
import streamlit as st

from .agency_components import get_agency_component_config
from .analysis import analyze, award_table, canonical_contractor_name, contractor_detail, filter_transactions
from .constants import ALL_COMPONENTS, ALL_LOCATIONS, ALL_NAICS, ALL_SET_ASIDES, STATE_OPTIONS
from .global_filter_options import (
    global_location_option_values,
    global_naics_option_values,
    global_set_aside_option_values,
)
from .option_index import (
    LOOKUP_TIMEOUT_SECONDS,
    OptionIndexError,
    component_option_values,
    get_agency_options,
    index_deployment_diagnostics,
    index_freshness,
    validate_index,
)
from .state import (
    FilterSnapshot,
    active_filter_chip_entries,
    add_calendar_years,
    default_end_date,
    default_start_date,
    snapshots_differ,
)
from .usaspending import fetch_transactions_for_snapshot
from .utils import decode_option, format_full_money, format_money, format_option, format_percent, usaspending_recipient_profile_url

UNAVAILABLE = "Unable to load options"
_RETRY_BUTTON_KEYS_THIS_RUN: set[str] = set()
AGENCY_WIDGET_KEY = "filter_agency"
COMPONENT_WIDGET_KEY = "filter_component"
NAICS_WIDGET_KEY = "filter_naics"
SET_ASIDE_WIDGET_KEY = "filter_set_aside"
LOCATION_WIDGET_KEY = "filter_location"
CONTRACTOR_WIDGET_KEY = "selected_contractor"
INDEX_DEPLOYMENT_ERROR = (
    "Competitor filters are temporarily unavailable because the option index was not included in this deployment."
)


def init_streamlit_state() -> None:
    st.session_state.setdefault("pending_snapshot", FilterSnapshot())
    st.session_state.setdefault("analyzed_snapshot", None)
    st.session_state.setdefault("analysis_results", None)
    st.session_state.setdefault("base_transactions", pd.DataFrame())
    st.session_state.setdefault("last_data_error", "")
    st.session_state.setdefault("last_data_diagnostics", {})
    st.session_state.setdefault("option_diagnostics", {})
    st.session_state.setdefault("option_lookup_cache", {})
    st.session_state.setdefault("last_valid_option_lists", {})
    st.session_state.setdefault("component_request_generation", 0)
    st.session_state.setdefault("naics_request_generation", 0)
    st.session_state.setdefault("option_index_refresh_needed", False)
    st.session_state.setdefault("selected_contractor", "")


def styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg: #0b0c10;
            --panel: #111827;
            --panel-2: #0f172a;
            --border: rgba(148, 163, 184, 0.22);
            --text: #f4f7fb;
            --muted: #9ca3af;
            --accent: #38bdf8;
            --accent-2: #2dd4bf;
            --danger: #fb7185;
        }
        html, body, [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at top left, rgba(56, 189, 248, 0.10), transparent 34rem),
                linear-gradient(135deg, #080a0f 0%, #0b1120 45%, #111827 100%) !important;
            color: var(--text) !important;
        }
        [data-testid="stHeader"], [data-testid="stToolbar"] { background: transparent !important; }
        .main .block-container { max-width: 1220px; padding-top: 1.4rem; padding-bottom: 3rem; }
        h1, h2, h3, label, p, span, div { color: var(--text); }
        h1 { font-weight: 800; letter-spacing: 0; margin-bottom: .35rem; }
        [data-testid="stSidebar"] { background: rgba(5, 8, 15, 0.94) !important; border-right: 1px solid var(--border); }
        [data-testid="stSelectbox"] label, [data-testid="stExpander"] summary {
            color: var(--text) !important; font-weight: 650;
        }
        [data-baseweb="select"] > div {
            background: rgba(15, 23, 42, 0.96) !important;
            border: 1px solid rgba(148, 163, 184, 0.30) !important;
            border-radius: 8px !important;
            color: var(--text) !important;
            min-height: 42px;
        }
        [data-baseweb="select"] span, [data-baseweb="select"] input { color: var(--text) !important; }
        [data-baseweb="select"] input::placeholder {
            color: #9ca3af !important;
            opacity: 1 !important;
            font-style: italic;
        }
        [data-testid="stDateInput"] label { color: var(--text) !important; font-weight: 650; }
        [data-testid="stDateInput"] input {
            background: rgba(15, 23, 42, 0.96) !important;
            border: 1px solid rgba(148, 163, 184, 0.30) !important;
            border-radius: 8px !important;
            color: var(--text) !important;
            min-height: 42px;
        }
        .stButton>button, .stDownloadButton>button {
            background: linear-gradient(135deg, #2563eb, #3b82f6) !important;
            border: 1px solid rgba(147, 197, 253, .55) !important;
            border-radius: 8px !important;
            color: #ffffff !important;
            font-weight: 800 !important;
            min-height: 42px;
            box-shadow: 0 14px 30px rgba(59, 130, 246, .40);
        }
        .stButton>button:hover:not(:disabled), .stDownloadButton>button:hover:not(:disabled) {
            background: linear-gradient(135deg, #1d4ed8, #2563eb) !important;
            box-shadow: 0 16px 34px rgba(59, 130, 246, .50);
        }
        .stButton>button:disabled {
            background: rgba(148, 163, 184, .16) !important;
            color: rgba(226, 232, 240, .46) !important;
            box-shadow: none !important;
        }
        .filter-guide-caption {
            color: #93c5fd;
            font-size: .9rem;
            font-weight: 650;
            margin: 0 0 .45rem;
            padding: .55rem .75rem;
            border-left: 3px solid #3b82f6;
            background: rgba(59, 130, 246, .10);
            border-radius: 0 8px 8px 0;
        }
        div[data-testid="stVerticalBlockBorderWrapper"].filter-guide-active {
            border-color: rgba(59, 130, 246, .72) !important;
            background: rgba(59, 130, 246, .06) !important;
            box-shadow: 0 0 0 1px rgba(59, 130, 246, .18), 0 12px 28px rgba(59, 130, 246, .12);
        }
        .filter-guide-submit-wrap.filter-guide-active {
            border: 2px solid rgba(59, 130, 246, .72);
            border-radius: 10px;
            padding: .75rem .85rem .35rem;
            margin-top: .35rem;
            background: rgba(59, 130, 246, .06);
            box-shadow: 0 0 0 1px rgba(59, 130, 246, .18), 0 12px 28px rgba(59, 130, 246, .12);
        }
        [data-testid="stExpander"] {
            background: rgba(9, 14, 27, .72) !important;
            border: 1px solid var(--border) !important;
            border-radius: 8px !important;
            overflow: hidden;
        }
        [data-testid="stExpanderDetails"] { background: rgba(9, 14, 27, .48) !important; border-top: 1px solid rgba(148,163,184,.14); }
        .metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .8rem; margin: 1rem 0 1.1rem; }
        .metric-card, .market-intel-card {
            position: relative;
            background: linear-gradient(180deg, rgba(15, 23, 42, .98), rgba(8, 13, 24, .98));
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: .95rem 1rem;
            box-shadow: 0 18px 44px rgba(0,0,0,.28);
            overflow: hidden;
        }
        .metric-card:before, .market-intel-card:before {
            content: ""; position: absolute; inset: 0 auto 0 0; width: 4px; background: var(--accent);
        }
        .metric-label, .market-intel-label { color: #a7b4c7; font-size: .76rem; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; }
        .metric-value, .market-intel-value { color: var(--text); font-size: 1.55rem; font-weight: 850; margin-top: .25rem; }
        .metric-sub, .market-intel-subtitle, .market-intel-helper { color: var(--muted); font-size: .84rem; margin-top: .22rem; }
        .section-title { color: var(--text); font-size: 1.05rem; font-weight: 850; margin: 1.25rem 0 .45rem; }
        .applied-filter-heading { color: #cbd5e1; font-weight: 800; margin-top: .8rem; margin-bottom: .45rem; }
        div[data-testid="stHorizontalBlock"]:has(.applied-filter-chip-slot) {
            flex-wrap: wrap !important;
            gap: .5rem !important;
            align-items: center !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.applied-filter-chip-slot) > div[data-testid="column"] {
            width: auto !important;
            flex: 0 0 auto !important;
            min-width: 0 !important;
        }
        div[data-testid="column"]:has(.applied-filter-chip-slot) [data-testid="stButton"] > button {
            border-radius: 999px !important;
            border: 1px solid rgba(148, 163, 184, 0.28) !important;
            background: rgba(30, 41, 59, 0.96) !important;
            color: #e5edf8 !important;
            font-size: .82rem !important;
            font-weight: 650 !important;
            padding: .4rem .85rem !important;
            box-shadow: none !important;
            min-height: unset !important;
            white-space: nowrap !important;
            width: auto !important;
        }
        div[data-testid="column"]:has(.applied-filter-chip-slot) [data-testid="stButton"] > button:hover {
            border-color: rgba(251, 113, 133, 0.45) !important;
            background: rgba(30, 41, 59, 1) !important;
        }
        .applied-filter-chip-slot { display: none; }
        .award-drilldown-table-wrap, .competitor-table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; background: rgba(9,14,27,.74); }
        .award-drilldown-table, .competitor-table { width: 100%; border-collapse: collapse; font-size: .82rem; }
        .award-drilldown-table th, .competitor-table th {
            text-align: left; color: #dbeafe; background: rgba(15,23,42,.96); padding: .65rem .7rem; border-bottom: 1px solid var(--border);
        }
        .award-drilldown-table td, .competitor-table td { color: #e5edf8; padding: .58rem .7rem; border-bottom: 1px solid rgba(148,163,184,.12); vertical-align: top; }
        .award-drilldown-table tr:nth-child(even) td, .competitor-table tr:nth-child(even) td { background: rgba(15,23,42,.42); }
        .market-concentration-legend-name a, .award-drilldown-table a, .competitor-table a { color: #67e8f9; text-decoration: none; font-weight: 750; }
        .market-concentration-legend-row {
            display: flex; justify-content: space-between; gap: .8rem; padding: .55rem .65rem;
            border-left: 4px solid #38bdf8; border-radius: 8px; margin-top: .45rem; background: rgba(56,189,248,.10);
        }
        .market-concentration-legend-name { color: #e5edf8; font-weight: 750; }
        .market-concentration-legend-metrics { color: #bae6fd; white-space: nowrap; }
        .stDataFrame { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _safe_index(options: list[str], value: str) -> int:
    return options.index(value) if value in options else 0


def _option_label(value: str) -> str:
    if "||" in value:
        return format_option(value).replace(" - ", " — ")
    return value


_SEARCH_HELP = "Type in the field to search and filter the list."


def _agency_option_label(value: str) -> str:
    if value in {"", UNAVAILABLE}:
        return "Start typing agency name..."
    return _option_label(value)


def _broad_filter_option_label(value: str, broad_value: str, placeholder: str) -> str:
    if value == UNAVAILABLE:
        return placeholder
    if value == broad_value:
        return f"{broad_value} — type to search"
    return _option_label(value)


def _parse_snapshot_date(value: str) -> date:
    return date.fromisoformat(value)


def _date_label(value: str, long: bool = False) -> str:
    fmt = "%B %d, %Y" if long else "%b %d, %Y"
    return _parse_snapshot_date(value).strftime(fmt).replace(" 0", " ")


def _validate_date_range(start_date: str, end_date: str) -> str:
    try:
        start = _parse_snapshot_date(start_date)
        end = _parse_snapshot_date(end_date)
    except ValueError:
        return "From and Through dates are required."
    today = date.today()
    if start > end:
        return "Start date must be on or before the end date."
    if end > today:
        return "The end date cannot be in the future."
    if add_calendar_years(start, 10) < end:
        return "Select a period of 10 years or less."
    return ""


def analysis_disabled(pending: FilterSnapshot, options_ready: bool, date_error: str = "") -> bool:
    return bool(date_error) or not pending.agency or not options_ready


def _has_subagencies(component_options: list[str]) -> bool:
    return any(option not in (UNAVAILABLE, ALL_COMPONENTS) for option in component_options)


def _filter_guide_step(
    agency: str,
    component_options: list[str],
    component: str,
    naics: str,
    options_ready: bool,
    *,
    component_label: str = "bureau",
) -> tuple[str, str]:
    if not agency:
        return "agency", "Start by choosing the agency."
    if _has_subagencies(component_options) and component == ALL_COMPONENTS:
        return "component", f"Choose a {component_label.lower()} or keep All Components for agency-wide results."
    if not options_ready:
        return "naics", "Loading filter options..."
    if naics == ALL_NAICS:
        return "naics", "Optionally pick a NAICS code, or click Find Competitors below to run with All NAICS."
    return "submit", "Click Find Competitors to run the analysis."


def _submit_guide_active(guide_step: str) -> bool:
    return guide_step == "submit"


def _init_filter_widget(key: str, value: str) -> str:
    if key not in st.session_state:
        st.session_state[key] = value
    return st.session_state[key]


def _sync_selectbox_state(key: str, options: list[str], preferred: str) -> None:
    if not options:
        return
    current = st.session_state.get(key)
    if current not in options:
        st.session_state[key] = preferred if preferred in options else options[0]


def _widget_value(key: str, fallback: str = "") -> str:
    value = st.session_state.get(key, fallback)
    if value in {UNAVAILABLE, None}:
        return ""
    return value or ""


def _guide_suppressed(snapshot: FilterSnapshot) -> bool:
    analyzed = st.session_state.analyzed_snapshot
    results = st.session_state.analysis_results
    return bool(results is not None and analyzed is not None and not snapshots_differ(snapshot, analyzed))


def _render_guide_caption(step: str, active_step: str, hint: str, *, suppressed: bool = False) -> None:
    if suppressed or step != active_step:
        return
    st.markdown(f'<p class="filter-guide-caption">{html.escape(hint)}</p>', unsafe_allow_html=True)


def _guide_container(step: str, active_step: str, *, suppressed: bool = False):
    return st.container(border=step == active_step and not suppressed)


def _ensure_date_state(current: FilterSnapshot) -> None:
    st.session_state.setdefault("date_from", _parse_snapshot_date(current.start_date))
    st.session_state.setdefault("date_through", _parse_snapshot_date(current.end_date))


def _pending_date_values(current: FilterSnapshot) -> tuple[str, str, str]:
    _ensure_date_state(current)
    start_date = st.session_state.date_from.isoformat()
    end_date = st.session_state.date_through.isoformat()
    return start_date, end_date, _validate_date_range(start_date, end_date)


def render_date_range(current: FilterSnapshot) -> tuple[str, str, str]:
    st.markdown('<div class="section-title compact-date-title">Date Range</div>', unsafe_allow_html=True)
    today = date.today()
    _ensure_date_state(current)
    cols = st.columns([1.15, 1.15, 3.8])
    with cols[0]:
        selected_start = st.date_input("From", max_value=today, key="date_from")
    with cols[1]:
        selected_end = st.date_input("Through", max_value=today, key="date_through")
    start_date = selected_start.isoformat()
    end_date = selected_end.isoformat()
    return start_date, end_date, _validate_date_range(start_date, end_date)


def _loading_message(kind: str, agency: str, component: str = "") -> str:
    if kind == "component":
        if agency == "Department of State":
            return f"Loading bureaus / funding offices for {agency}..."
        return f"Loading bureaus for {agency}..."
    if kind == "naics":
        return f"Loading NAICS for {component or agency}..."
    if kind == "set_aside":
        return f"Loading set-asides for {component or agency}..."
    return f"Loading performance locations for {component or agency}..."


def _timeout_message(kind: str, agency: str, component: str = "") -> str:
    if kind == "component":
        label = "bureaus / funding offices" if agency == "Department of State" else "bureaus"
        return f"Unable to load {label} for {agency}."
    if kind == "naics":
        return f"Unable to load NAICS for {component or agency}."
    if kind == "set_aside":
        return f"Unable to load set-asides for {component or agency}."
    return f"Unable to load performance locations for {component or agency}."


def _retry_button_key(cache_key: tuple) -> str:
    return "retry_" + "_".join(str(part).replace(" ", "_").replace("/", "_") for part in cache_key)


def _run_with_deadline(loader, timeout_seconds: float = LOOKUP_TIMEOUT_SECONDS):
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(loader)
    try:
        return future.result(timeout=timeout_seconds), False
    except TimeoutError:
        future.cancel()
        return None, True
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _session_cached_lookup(
    kind: str,
    scope: tuple,
    loader,
    *,
    loading_text: str,
    timeout_text: str,
    allow_default_only: bool = False,
    timeout_seconds: float = LOOKUP_TIMEOUT_SECONDS,
) -> tuple[list[str], dict]:
    session_cache = st.session_state.option_lookup_cache
    last_valid = st.session_state.last_valid_option_lists
    key = (kind, *scope)
    started = time.perf_counter()
    if key in session_cache:
        options, diag = session_cache[key]
        return list(options), {**diag, "cache_level_used": "session", "elapsed_ms": (time.perf_counter() - started) * 1000}

    placeholder = st.empty()
    placeholder.info(loading_text)
    result, timed_out = _run_with_deadline(loader, timeout_seconds)
    placeholder.empty()
    elapsed = (time.perf_counter() - started) * 1000
    if timed_out:
        options = list(last_valid.get(key, [])) or [UNAVAILABLE]
        diag = {
            "lookup_type": kind,
            "cache_level_used": "timeout",
            "rows_returned": max(0, len(options) - 1),
            "elapsed_ms": elapsed,
            "error": timeout_text,
        }
        session_cache[key] = (list(options), diag)
        retry_key = _retry_button_key(key)
        if retry_key not in _RETRY_BUTTON_KEYS_THIS_RUN:
            _RETRY_BUTTON_KEYS_THIS_RUN.add(retry_key)
            cols = st.columns([3, 1])
            with cols[0]:
                st.warning(timeout_text)
            with cols[1]:
                if st.button("Retry", key=retry_key):
                    session_cache.pop(key, None)
                    st.rerun()
        return options, diag
    if result is None:
        return [UNAVAILABLE], {"lookup_type": kind, "cache_level_used": "error", "rows_returned": 0, "elapsed_ms": elapsed}
    options, diag = result
    if len(options) <= 1 and not allow_default_only:
        options = [UNAVAILABLE]
    session_cache[key] = (list(options), {**diag, "elapsed_ms": elapsed})
    last_valid[key] = list(options)
    return options, {**diag, "elapsed_ms": elapsed}


def _option_sets(
    pending: FilterSnapshot,
    *,
    stop_after: str | None = None,
) -> tuple[list[str], list[str], list[str], list[str], dict]:
    diagnostics: dict = {}
    naics_options, naics_diag = global_naics_option_values()
    set_asides, set_aside_diag = global_set_aside_option_values()
    location_options, location_diag = global_location_option_values()
    diagnostics["naics"] = naics_diag
    diagnostics["set_aside"] = set_aside_diag
    diagnostics["location"] = location_diag
    if not pending.agency:
        return [ALL_COMPONENTS], naics_options, set_asides, location_options, diagnostics

    component_options, component_diag = _session_cached_lookup(
        "Agency Component",
        (pending.agency,),
        lambda: component_option_values(pending.agency),
        loading_text=_loading_message("component", pending.agency),
        timeout_text=_timeout_message("component", pending.agency),
        allow_default_only=True,
    )
    diagnostics["component"] = component_diag
    if stop_after == "component":
        return component_options, naics_options, set_asides, location_options, diagnostics
    return component_options, naics_options, set_asides, location_options, diagnostics


def _option_diagnostic_errors(diagnostics: dict) -> dict:
    required = {"agency", "component"}
    return {
        key: value
        for key, value in diagnostics.items()
        if key in required
        and isinstance(value, dict)
        and value.get("error")
        and value.get("cache_level_used") != "timeout"
    }


def render_filters() -> tuple[FilterSnapshot, bool, dict, str, str]:
    try:
        validate_index()
    except OptionIndexError as exc:
        diagnostics = index_deployment_diagnostics()
        diagnostics["error"] = str(exc)
        logging.getLogger(__name__).error("Option index validation failed: %s", diagnostics)
        st.error(INDEX_DEPLOYMENT_ERROR)
        with st.expander("Developer diagnostics", expanded=False):
            st.json(diagnostics)
        current: FilterSnapshot = st.session_state.pending_snapshot
        return current, False, {"index": diagnostics}, "agency", "Start by choosing the awarding agency."
    freshness = index_freshness()
    st.session_state.option_index_refresh_needed = bool(freshness.get("is_stale"))
    agencies = [record["agency_name"] for record in get_agency_options()]
    current: FilterSnapshot = st.session_state.pending_snapshot
    start_date, end_date, date_error = _pending_date_values(current)
    agency_options = [""] + agencies
    agency_ok = bool(agencies)
    if not agency_ok:
        agency_options = [UNAVAILABLE]
    suppress_guide = _guide_suppressed(current)
    agency_live = _init_filter_widget(AGENCY_WIDGET_KEY, current.agency if current.agency in agency_options else "")
    if agency_live == UNAVAILABLE:
        agency_live = ""
        st.session_state[AGENCY_WIDGET_KEY] = ""
    focus_step, focus_hint = (
        ("agency", "Start by choosing the agency.")
        if not agency_live
        else ("", "")
    )
    with _guide_container("agency", focus_step, suppressed=suppress_guide):
        _render_guide_caption("agency", focus_step, focus_hint, suppressed=suppress_guide)
        agency = st.selectbox(
            "Agency",
            agency_options,
            format_func=_agency_option_label,
            placeholder="Start typing agency name...",
            help=_SEARCH_HELP,
            disabled=not agency_ok,
            key=AGENCY_WIDGET_KEY,
        )
    if agency == UNAVAILABLE:
        agency = ""

    temporary_snapshot = FilterSnapshot(
        agency=agency,
        component=current.component,
        naics=current.naics,
        set_aside=current.set_aside,
        location=current.location,
        start_date=default_start_date(),
        end_date=default_end_date(),
    )
    if agency != current.agency:
        st.session_state.component_request_generation += 1
        st.session_state.naics_request_generation += 1
        st.session_state[COMPONENT_WIDGET_KEY] = ALL_COMPONENTS
        st.session_state[NAICS_WIDGET_KEY] = ALL_NAICS
        st.session_state.option_lookup_cache = {}
    component_options, naics_options, set_asides, location_options, diagnostics = _option_sets(temporary_snapshot, stop_after="component")
    component_config = get_agency_component_config(agency)
    component_default = current.component if current.component in component_options else ALL_COMPONENTS
    component_live = _init_filter_widget(COMPONENT_WIDGET_KEY, component_default)
    _sync_selectbox_state(COMPONENT_WIDGET_KEY, component_options, component_default)
    component_live = st.session_state[COMPONENT_WIDGET_KEY]
    focus_step, focus_hint = _filter_guide_step(
        agency,
        component_options,
        component_live,
        _widget_value(NAICS_WIDGET_KEY, current.naics),
        agency_ok and component_options != [UNAVAILABLE],
        component_label=component_config["label"],
    )
    with _guide_container("component", focus_step, suppressed=suppress_guide):
        _render_guide_caption("component", focus_step, focus_hint, suppressed=suppress_guide)
        component = st.selectbox(
            component_config["label"],
            component_options,
            format_func=lambda value: _broad_filter_option_label(
                value,
                ALL_COMPONENTS,
                f"Start typing {component_config['label'].lower()}...",
            ),
            placeholder=f"Start typing {component_config['label'].lower()}...",
            help=_SEARCH_HELP,
            disabled=component_options == [UNAVAILABLE],
            key=COMPONENT_WIDGET_KEY,
        )
    if component == UNAVAILABLE:
        component = ALL_COMPONENTS

    refreshed_snapshot = FilterSnapshot(
        agency=agency,
        component=component,
        naics=current.naics,
        set_aside=current.set_aside,
        location=current.location,
        start_date=default_start_date(),
        end_date=default_end_date(),
    )
    if component != current.component:
        st.session_state.naics_request_generation += 1
        st.session_state[NAICS_WIDGET_KEY] = ALL_NAICS
    component_options, naics_options, set_asides, location_options, naics_diagnostics = _option_sets(refreshed_snapshot)
    diagnostics.update(naics_diagnostics)
    naics_default = current.naics if current.naics in naics_options else ALL_NAICS
    naics_live = _init_filter_widget(NAICS_WIDGET_KEY, naics_default)
    _sync_selectbox_state(NAICS_WIDGET_KEY, naics_options, naics_default)
    naics_live = st.session_state[NAICS_WIDGET_KEY]
    options_ready = bool(not date_error and agency and component_options != [UNAVAILABLE])
    focus_step, focus_hint = _filter_guide_step(
        agency,
        component_options,
        component,
        naics_live,
        options_ready,
        component_label=component_config["label"],
    )
    with _guide_container("naics", focus_step, suppressed=suppress_guide):
        _render_guide_caption("naics", focus_step, focus_hint, suppressed=suppress_guide)
        naics = st.selectbox(
            "NAICS",
            naics_options,
            format_func=lambda value: _broad_filter_option_label(
                value,
                ALL_NAICS,
                "Start typing NAICS code or name...",
            ),
            placeholder="Start typing NAICS code or name...",
            help=_SEARCH_HELP,
            key=NAICS_WIDGET_KEY,
        )
    if naics == UNAVAILABLE:
        naics = ALL_NAICS
    if naics != current.naics:
        st.session_state[SET_ASIDE_WIDGET_KEY] = ALL_SET_ASIDES
        st.session_state[LOCATION_WIDGET_KEY] = ALL_LOCATIONS

    set_aside = ALL_SET_ASIDES
    location = ALL_LOCATIONS
    if agency:
        optional_snapshot = FilterSnapshot(
            agency=agency,
            component=component,
            naics=naics,
            set_aside=current.set_aside,
            location=current.location,
            start_date=start_date,
            end_date=end_date,
        )
        component_options, naics_options, set_asides, location_options, optional_diagnostics = _option_sets(optional_snapshot)
        diagnostics.update(optional_diagnostics)

        with st.expander("Optional refinements", expanded=False):
            set_aside_default = current.set_aside if current.set_aside in set_asides else ALL_SET_ASIDES
            set_aside_live = _init_filter_widget(SET_ASIDE_WIDGET_KEY, set_aside_default)
            if set_aside_live not in set_asides:
                set_aside_live = ALL_SET_ASIDES
                st.session_state[SET_ASIDE_WIDGET_KEY] = ALL_SET_ASIDES
            set_aside = st.selectbox(
                "Set-Aside",
                set_asides,
                format_func=lambda value: _broad_filter_option_label(
                    value,
                    ALL_SET_ASIDES,
                    "Start typing set-aside type...",
                ),
                placeholder="Start typing set-aside type...",
                help=_SEARCH_HELP,
                key=SET_ASIDE_WIDGET_KEY,
            )
            location_default = current.location if current.location in location_options else ALL_LOCATIONS
            location_live = _init_filter_widget(LOCATION_WIDGET_KEY, location_default)
            if location_live not in location_options:
                location_live = ALL_LOCATIONS
                st.session_state[LOCATION_WIDGET_KEY] = ALL_LOCATIONS
            location = st.selectbox(
                "Performance Location",
                location_options,
                format_func=lambda value: _broad_filter_option_label(
                    value,
                    ALL_LOCATIONS,
                    "Start typing performance location...",
                ),
                placeholder="Start typing performance location...",
                help=_SEARCH_HELP,
                key=LOCATION_WIDGET_KEY,
            )
    else:
        with st.expander("Optional refinements", expanded=False):
            st.caption("Select an agency to enable optional set-aside and performance location filters.")

    start_date, end_date, date_error = render_date_range(current)
    if date_error:
        st.warning(date_error)

    snapshot = FilterSnapshot(agency=agency, component=component, naics=naics, set_aside=set_aside, location=location, start_date=start_date, end_date=end_date)
    st.session_state.pending_snapshot = snapshot
    ready = bool(not date_error and agency and component_options != [UNAVAILABLE])
    guide_step, guide_hint = _filter_guide_step(
        agency,
        component_options,
        component,
        naics,
        ready,
        component_label=component_config["label"],
    )
    if not agency_ok:
        diagnostics["agency"] = {"error": {"message": "Unable to load options"}}
    return snapshot, ready, diagnostics, guide_step, guide_hint


def metric_card(label: str, value: str, subtext: str, accent: str = "#38bdf8") -> None:
    st.markdown(
        f"""
        <section class="metric-card" style="--accent: {html.escape(accent)};">
            <div class="metric-label">{html.escape(label)}</div>
            <div class="metric-value">{html.escape(value)}</div>
            <div class="metric-sub">{html.escape(subtext)}</div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_kpis(results: dict) -> None:
    kpis = results["kpis"]
    concentration = results["concentration"]
    st.markdown('<div class="metric-grid">', unsafe_allow_html=True)
    cols = st.columns(4)
    with cols[0]:
        metric_card("Net Obligations", format_money(kpis["net_obligations"]), "Transaction obligations in scope", "#38bdf8")
    with cols[1]:
        metric_card("Contractors", f"{kpis['contractors']:,}", "Canonical contractor groups", "#2dd4bf")
    with cols[2]:
        metric_card("Unique Awards", f"{kpis['unique_awards']:,}", "Awards supporting the ranking", "#a78bfa")
    with cols[3]:
        metric_card("Market Concentration", format_percent(concentration["top_share"]), "Top 5 share of positive obligations", "#f59e0b")
    st.markdown("</div>", unsafe_allow_html=True)


def render_scope_line(results: dict) -> None:
    period = results.get("period") or {}
    start_date = period.get("start_date")
    end_date = period.get("end_date")
    if start_date and end_date:
        st.caption(f"Competitor activity from {_date_label(start_date, long=True)} through {_date_label(end_date, long=True)}")


def _requires_download(previous: FilterSnapshot, pending: FilterSnapshot) -> bool:
    return (previous.agency, previous.start_date, previous.end_date) != (
        pending.agency,
        pending.start_date,
        pending.end_date,
    )


def _snapshot_after_chip_removal(chip_id: str, analyzed: FilterSnapshot) -> FilterSnapshot | None:
    if chip_id == "agency":
        st.session_state[AGENCY_WIDGET_KEY] = ""
        st.session_state[COMPONENT_WIDGET_KEY] = ALL_COMPONENTS
        st.session_state[NAICS_WIDGET_KEY] = ALL_NAICS
        st.session_state[SET_ASIDE_WIDGET_KEY] = ALL_SET_ASIDES
        st.session_state[LOCATION_WIDGET_KEY] = ALL_LOCATIONS
        return None
    pending = FilterSnapshot(
        agency=analyzed.agency,
        component=ALL_COMPONENTS if chip_id == "component" else analyzed.component,
        naics=ALL_NAICS if chip_id == "naics" else analyzed.naics,
        set_aside=ALL_SET_ASIDES if chip_id == "set_aside" else analyzed.set_aside,
        location=ALL_LOCATIONS if chip_id == "location" else analyzed.location,
        start_date=default_start_date() if chip_id == "period" else analyzed.start_date,
        end_date=default_end_date() if chip_id == "period" else analyzed.end_date,
    )
    if chip_id == "component":
        st.session_state[COMPONENT_WIDGET_KEY] = ALL_COMPONENTS
    elif chip_id == "naics":
        st.session_state[NAICS_WIDGET_KEY] = ALL_NAICS
    elif chip_id == "set_aside":
        st.session_state[SET_ASIDE_WIDGET_KEY] = ALL_SET_ASIDES
    elif chip_id == "location":
        st.session_state[LOCATION_WIDGET_KEY] = ALL_LOCATIONS
    elif chip_id == "period":
        st.session_state.date_from = _parse_snapshot_date(pending.start_date)
        st.session_state.date_through = _parse_snapshot_date(pending.end_date)
    st.session_state.pending_snapshot = pending
    return pending


def _apply_analysis_snapshot(pending: FilterSnapshot, *, download: bool) -> None:
    diagnostic: dict = {}
    if download or st.session_state.base_transactions.empty:
        transactions, diagnostic = fetch_transactions_for_snapshot(pending)
        st.session_state.last_data_diagnostics = diagnostic
        if diagnostic.get("error"):
            st.session_state.last_data_error = diagnostic["error"]
            return
        st.session_state.last_data_error = ""
        st.session_state.base_transactions = transactions
    else:
        transactions = st.session_state.base_transactions
        diagnostic = st.session_state.last_data_diagnostics or {}

    scoped = filter_transactions(transactions, pending)
    period = diagnostic.get("period") or {}
    if not download and st.session_state.analysis_results:
        period = period or st.session_state.analysis_results.get("period", {})
    analyzed_snapshot = FilterSnapshot(
        agency=pending.agency,
        component=pending.component,
        naics=pending.naics,
        set_aside=pending.set_aside,
        location=pending.location,
        start_date=period.get("start_date", pending.start_date),
        end_date=period.get("end_date", pending.end_date),
    )
    st.session_state.analysis_results = analyze(scoped, FilterSnapshot(), period=period)
    st.session_state.analyzed_snapshot = analyzed_snapshot


def _handle_chip_removal(chip_id: str, analyzed: FilterSnapshot) -> None:
    pending = _snapshot_after_chip_removal(chip_id, analyzed)
    st.session_state.selected_contractor = ""
    if pending is None:
        st.session_state.analysis_results = None
        st.session_state.analyzed_snapshot = None
        st.session_state.base_transactions = pd.DataFrame()
        return
    _apply_analysis_snapshot(pending, download=_requires_download(analyzed, pending))


def render_applied_filters(analyzed: FilterSnapshot, component_label: str) -> None:
    chips = active_filter_chip_entries(analyzed, component_label)
    if not chips:
        return
    st.markdown('<div class="applied-filter-heading">Applied filters</div>', unsafe_allow_html=True)
    chip_cols = st.columns(len(chips), gap="small")
    for idx, chip in enumerate(chips):
        with chip_cols[idx]:
            st.markdown('<span class="applied-filter-chip-slot"></span>', unsafe_allow_html=True)
            if st.button(
                f"{chip['label']}  ×",
                key=f"remove_filter_{chip['id']}",
                help=f"Remove {chip['label']}",
                type="secondary",
            ):
                _handle_chip_removal(chip["id"], analyzed)
                st.rerun()


def _contractor_link_markup(name: str, profile_link: str = "") -> str:
    contractor = html.escape(name)
    link = profile_link or usaspending_recipient_profile_url("", name)
    if not link:
        return contractor
    return f'<a href="{html.escape(link)}" target="_blank" rel="noopener noreferrer">{contractor}</a>'


def render_leaderboard(leaderboard: pd.DataFrame) -> None:
    st.markdown('<div class="section-title">Top Competitors</div>', unsafe_allow_html=True)
    if leaderboard.empty:
        st.info("No contractors found for this scope.")
        return
    rows = []
    for row in leaderboard.to_dict("records"):
        name = str(row.get("Contractor Name") or "")
        profile_link = str(row.get("Recipient Profile Link") or "")
        obligations = format_full_money(row.get("Obligations in Scope"))
        share = format_percent(row.get("Market Share"))
        unique_awards = int(row.get("Unique Awards") or 0)
        recent = row.get("Most Recent Action Date")
        recent_text = recent.isoformat() if pd.notna(recent) and recent else ""
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('Rank') or ''))}</td>"
            f"<td>{_contractor_link_markup(name, profile_link)}</td>"
            f"<td>{html.escape(obligations)}</td>"
            f"<td>{html.escape(share)}</td>"
            f"<td>{unique_awards:,}</td>"
            f"<td>{html.escape(recent_text)}</td>"
            "</tr>"
        )
    st.markdown(
        """
        <div class="competitor-table-wrap">
          <table class="competitor-table">
            <thead><tr><th>Rank</th><th>Contractor Name</th><th>Obligations in Scope</th><th>Market Share</th><th>Unique Awards</th><th>Most Recent Action Date</th></tr></thead>
            <tbody>
        """
        + "".join(rows)
        + "</tbody></table></div>",
        unsafe_allow_html=True,
    )


def render_contractor_selector(options: list[str]) -> str:
    st.markdown('<div class="section-title">Contractor Detail</div>', unsafe_allow_html=True)
    choices = [""] + options
    current = st.session_state.get(CONTRACTOR_WIDGET_KEY, "")
    if current and current not in choices:
        st.session_state[CONTRACTOR_WIDGET_KEY] = ""
    return st.selectbox(
        "Choose a contractor to drill down",
        choices,
        format_func=lambda value: value or "Start typing contractor name...",
        placeholder="Start typing contractor name...",
        help=_SEARCH_HELP,
        key=CONTRACTOR_WIDGET_KEY,
        label_visibility="collapsed",
    )


def render_contractor_kpis(transactions: pd.DataFrame, contractor_name: str) -> None:
    detail = contractor_detail(transactions, contractor_name)
    if not detail:
        return
    st.markdown('<div class="metric-grid" style="grid-template-columns: repeat(2, minmax(0, 1fr));">', unsafe_allow_html=True)
    cols = st.columns(2)
    with cols[0]:
        metric_card("Net Obligations", format_money(detail["obligations"]), "Transaction obligations for this contractor", "#38bdf8")
    with cols[1]:
        metric_card("Unique Awards", f"{detail['unique_awards']:,}", "Awards for this contractor in scope", "#a78bfa")
    st.markdown("</div>", unsafe_allow_html=True)


def render_concentration(concentration: dict) -> None:
    st.markdown(
        f"""
        <section class="market-intel-card" style="--accent: #a78bfa;">
            <div class="market-intel-label">Market Concentration</div>
            <div class="market-intel-value">{html.escape(format_percent(concentration["top_share"]))}</div>
            <div class="market-intel-subtitle">Top 5 share of positive obligations</div>
            <div class="market-intel-helper">Uses positive obligation transactions. Negative obligations remain included in net obligation totals.</div>
        </section>
        """,
        unsafe_allow_html=True,
    )
    if not concentration["breakdown"]:
        st.info("No positive obligation transactions in this scope.")
        return
    for row in concentration["breakdown"]:
        contractor_markup = _contractor_link_markup(
            str(row["contractor"]),
            usaspending_recipient_profile_url("", str(row["contractor"])),
        )
        st.markdown(
            f"""
            <div class="market-concentration-legend-row">
                <div class="market-concentration-legend-name">{contractor_markup}</div>
                <div class="market-concentration-legend-metrics">{html.escape(format_full_money(row["amount"]))} · {html.escape(format_percent(row["share"]))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_awards(transactions: pd.DataFrame, contractor_name: str = "") -> None:
    title = "Top Relevant Awards"
    if contractor_name:
        target = canonical_contractor_name(contractor_name)
        scoped = transactions[transactions["canonical_contractor"] == target]
        awards = award_table(scoped)
        title = f"Top Relevant Awards — {contractor_name}"
    else:
        awards = award_table(transactions)
    st.markdown(f'<div class="section-title">{html.escape(title)}</div>', unsafe_allow_html=True)
    if awards.empty:
        st.info("No award rows found for this scope.")
        return
    visible = awards.head(25).copy()
    rows = []
    for row in visible.to_dict("records"):
        award_link = row.get("USAspending Award Link") or ""
        award = html.escape(str(row.get("Award ID") or "Unavailable"))
        award_markup = f'<a href="{html.escape(award_link)}" target="_blank" rel="noopener noreferrer">{award}</a>' if award_link else award
        contractor_markup = _contractor_link_markup(
            str(row.get("Contractor") or ""),
            usaspending_recipient_profile_url("", str(row.get("Contractor") or "")),
        )
        rows.append(
            "<tr>"
            f"<td>{contractor_markup}</td>"
            f"<td>{award_markup}</td>"
            f"<td>{html.escape(str(row.get('Description') or ''))}</td>"
            f"<td>{html.escape(format_full_money(row.get('Obligations in Scope')))}</td>"
            f"<td>{html.escape(str(row.get('Performance Location') or ''))}</td>"
            f"<td>{html.escape(str(row.get('Awarding Office') or ''))}</td>"
            f"<td>{html.escape(str(row.get('Funding Office') or ''))}</td>"
            "</tr>"
        )
    st.markdown(
        """
        <div class="award-drilldown-table-wrap">
          <table class="award-drilldown-table">
            <thead><tr><th>Contractor</th><th>Award ID</th><th>Description</th><th>Obligations in Scope</th><th>Performance Location</th><th>Awarding Office</th><th>Funding Office</th></tr></thead>
            <tbody>
        """
        + "".join(rows)
        + "</tbody></table></div>",
        unsafe_allow_html=True,
    )


def render_detail(results: dict, contractor_name: str) -> None:
    if not contractor_name:
        return
    detail = contractor_detail(results["transactions"], contractor_name)
    if not detail:
        return
    with st.expander(f"More detail — {detail['contractor_name']}", expanded=False):
        st.write(
            f"{format_full_money(detail['obligations'])} in scope, "
            f"{format_percent(detail['market_share'])} market share, "
            f"{detail['unique_awards']} unique awards."
        )
        if detail["recipient_entities"]:
            st.markdown("Recipient entities")
            st.dataframe(
                pd.DataFrame(detail["recipient_entities"]),
                use_container_width=True,
                hide_index=True,
                column_config={"recipient_link": st.column_config.LinkColumn("USAspending recipient profile")},
            )
        st.markdown("Location mix")
        st.dataframe(detail["location_mix"], use_container_width=True, hide_index=True)
        st.markdown("NAICS mix")
        st.dataframe(detail["naics_mix"], use_container_width=True, hide_index=True)


def render_diagnostics(diagnostics: dict) -> None:
    if not diagnostics:
        return
    with st.expander("Developer diagnostics", expanded=False):
        st.json(diagnostics)


def main() -> None:
    st.set_page_config(page_title="GovCon Competitor Finder", layout="wide", initial_sidebar_state="expanded")
    init_streamlit_state()
    styles()
    st.title("Find Competitors")
    pending, options_ready, option_diagnostics, guide_step, guide_hint = render_filters()
    option_errors = _option_diagnostic_errors(option_diagnostics)
    if option_errors:
        st.warning("Unable to load options")
        render_diagnostics(option_errors)
    if st.session_state.analysis_results is not None and snapshots_differ(pending, st.session_state.analyzed_snapshot):
        st.caption("Filters changed")
    disabled = analysis_disabled(pending, options_ready, _validate_date_range(pending.start_date, pending.end_date))
    submit_highlight = _submit_guide_active(guide_step) and not disabled and not _guide_suppressed(pending)
    with st.container(border=submit_highlight):
        _render_guide_caption("submit", guide_step, guide_hint, suppressed=not submit_highlight)
        if st.button("Find Competitors", type="primary", disabled=disabled):
            progress = st.empty()
            with st.spinner("Fetching USAspending transactions and ranking competitors..."):
                transactions, diagnostic = fetch_transactions_for_snapshot(pending, progress_callback=progress.info)
                st.session_state.last_data_diagnostics = diagnostic
                if diagnostic.get("error"):
                    st.session_state.last_data_error = diagnostic["error"]
                    st.error("Unable to load the complete selected date range. No new analysis was applied.")
                else:
                    st.session_state.last_data_error = ""
                    scoped = filter_transactions(transactions, pending)
                    period = diagnostic.get("period", {})
                    analyzed_snapshot = FilterSnapshot(
                        agency=pending.agency,
                        component=pending.component,
                        naics=pending.naics,
                        set_aside=pending.set_aside,
                        location=pending.location,
                        start_date=period.get("start_date", pending.start_date),
                        end_date=period.get("end_date", pending.end_date),
                    )
                    st.session_state.analysis_results = analyze(scoped, FilterSnapshot(), period=period)
                    st.session_state.analyzed_snapshot = analyzed_snapshot
                    st.session_state.selected_contractor = ""
                    if not transactions.empty:
                        st.session_state.base_transactions = transactions
            progress.empty()
    if st.session_state.last_data_error:
        render_diagnostics(st.session_state.last_data_diagnostics)
    results = st.session_state.analysis_results
    analyzed = st.session_state.analyzed_snapshot
    if results is None or analyzed is None:
        return
    config = get_agency_component_config(analyzed.agency)
    render_applied_filters(analyzed, config["label"])
    render_scope_line(results)
    render_kpis(results)
    render_leaderboard(results["leaderboard"])
    st.markdown('<div class="section-title">Market Concentration</div>', unsafe_allow_html=True)
    render_concentration(results["concentration"])
    contractor_options = results["leaderboard"]["Contractor Name"].tolist() if not results["leaderboard"].empty else []
    selected_contractor = render_contractor_selector(contractor_options)
    if selected_contractor:
        render_contractor_kpis(results["transactions"], selected_contractor)
    render_awards(results["transactions"], selected_contractor)
    render_detail(results, selected_contractor)
