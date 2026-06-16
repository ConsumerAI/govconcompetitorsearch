from __future__ import annotations

import html
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date
import time

import pandas as pd
import streamlit as st

from .agency_components import get_agency_component_config
from .analysis import analyze, contractor_detail, filter_transactions
from .constants import ALL_COMPONENTS, ALL_LOCATIONS, ALL_NAICS, ALL_SET_ASIDES, STATE_OPTIONS
from .option_index import (
    LOOKUP_TIMEOUT_SECONDS,
    OptionIndexError,
    component_option_values,
    get_agency_options,
    index_deployment_diagnostics,
    index_freshness,
    location_option_values,
    naics_option_values,
    set_aside_option_values,
    validate_index,
)
from .state import FilterSnapshot, active_filter_chips, add_calendar_years, default_end_date, default_start_date, snapshots_differ
from .usaspending import fetch_transactions_for_snapshot
from .utils import decode_option, format_full_money, format_money, format_option, format_percent

UNAVAILABLE = "Unable to load options"
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
        [data-testid="stDateInput"] label { color: var(--text) !important; font-weight: 650; }
        [data-testid="stDateInput"] input {
            background: rgba(15, 23, 42, 0.96) !important;
            border: 1px solid rgba(148, 163, 184, 0.30) !important;
            border-radius: 8px !important;
            color: var(--text) !important;
            min-height: 42px;
        }
        .stButton>button, .stDownloadButton>button {
            background: linear-gradient(135deg, #0ea5e9, #14b8a6) !important;
            border: 1px solid rgba(255,255,255,.18) !important;
            border-radius: 8px !important;
            color: #001018 !important;
            font-weight: 800 !important;
            min-height: 42px;
            box-shadow: 0 14px 30px rgba(20, 184, 166, .18);
        }
        .stButton>button:disabled {
            background: rgba(148, 163, 184, .16) !important;
            color: rgba(226, 232, 240, .46) !important;
            box-shadow: none !important;
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
        .applied-filter-heading { color: #cbd5e1; font-weight: 800; margin-top: .8rem; margin-bottom: .25rem; }
        .applied-filter-chip {
            display: inline-block; color: #dbeafe; border: 1px solid rgba(56,189,248,.34);
            background: rgba(14, 165, 233, .12); border-radius: 999px; padding: .25rem .62rem; margin: 0 .35rem .35rem 0;
            font-size: .82rem; font-weight: 650;
        }
        .award-drilldown-table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; background: rgba(9,14,27,.74); }
        .award-drilldown-table { width: 100%; border-collapse: collapse; font-size: .82rem; }
        .award-drilldown-table th {
            text-align: left; color: #dbeafe; background: rgba(15,23,42,.96); padding: .65rem .7rem; border-bottom: 1px solid var(--border);
        }
        .award-drilldown-table td { color: #e5edf8; padding: .58rem .7rem; border-bottom: 1px solid rgba(148,163,184,.12); vertical-align: top; }
        .award-drilldown-table tr:nth-child(even) td { background: rgba(15,23,42,.42); }
        .award-drilldown-table a { color: #67e8f9; text-decoration: none; font-weight: 750; }
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


def _display_option(value: str) -> str:
    if value in {"", UNAVAILABLE, ALL_COMPONENTS, ALL_NAICS, ALL_SET_ASIDES, ALL_LOCATIONS}:
        return value or "Select agency"
    if "||" in value:
        return format_option(value).replace(" - ", " — ")
    return value


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
    result, timed_out = _run_with_deadline(loader)
    placeholder.empty()
    elapsed = (time.perf_counter() - started) * 1000
    if timed_out:
        options = list(last_valid.get(key, [])) or [UNAVAILABLE]
        retry_key = "retry_" + "_".join(str(part).replace(" ", "_").replace("/", "_") for part in key)
        cols = st.columns([3, 1])
        with cols[0]:
            st.warning(timeout_text)
        with cols[1]:
            if st.button("Retry", key=retry_key):
                session_cache.pop(key, None)
                st.rerun()
        return options, {
            "lookup_type": kind,
            "cache_level_used": "timeout",
            "rows_returned": max(0, len(options) - 1),
            "elapsed_ms": elapsed,
            "error": timeout_text,
        }
    if result is None:
        return [UNAVAILABLE], {"lookup_type": kind, "cache_level_used": "error", "rows_returned": 0, "elapsed_ms": elapsed}
    options, diag = result
    if len(options) <= 1 and not allow_default_only:
        options = [UNAVAILABLE]
    session_cache[key] = (list(options), {**diag, "elapsed_ms": elapsed})
    last_valid[key] = list(options)
    return options, {**diag, "elapsed_ms": elapsed}


def _option_sets(pending: FilterSnapshot) -> tuple[list[str], list[str], list[str], list[str], dict]:
    diagnostics: dict = {}
    if not pending.agency:
        return [ALL_COMPONENTS], [ALL_NAICS], [ALL_SET_ASIDES], [ALL_LOCATIONS], diagnostics

    component_options, component_diag = _session_cached_lookup(
        "Agency Component",
        (pending.agency,),
        lambda: component_option_values(pending.agency),
        loading_text=_loading_message("component", pending.agency),
        timeout_text=_timeout_message("component", pending.agency),
    )
    diagnostics["component"] = component_diag
    component = pending.component if pending.component in component_options else ALL_COMPONENTS

    naics_options, naics_diag = _session_cached_lookup(
        "NAICS",
        (pending.agency, component),
        lambda: naics_option_values(pending.agency, component),
        loading_text=_loading_message("naics", pending.agency, component),
        timeout_text=_timeout_message("naics", pending.agency, component),
    )
    diagnostics["naics"] = naics_diag
    naics = pending.naics if pending.naics in naics_options else ALL_NAICS
    naics_code, _naics_description = decode_option(naics)

    set_asides, set_aside_diag = _session_cached_lookup(
        "Set-Aside",
        (pending.agency, component, naics_code),
        lambda: set_aside_option_values(pending.agency, component, naics_code),
        loading_text=_loading_message("set_aside", pending.agency, component),
        timeout_text=_timeout_message("set_aside", pending.agency, component),
        allow_default_only=True,
    )
    diagnostics["set_aside"] = set_aside_diag
    set_aside = pending.set_aside if pending.set_aside in set_asides else ALL_SET_ASIDES
    set_aside_code, _set_aside_description = decode_option(set_aside)
    if not set_aside_code and " - " in set_aside:
        set_aside_code = set_aside.split(" - ", 1)[0]

    location_options, location_diag = _session_cached_lookup(
        "Performance Location",
        (pending.agency, component, naics_code, set_aside_code),
        lambda: location_option_values(pending.agency, component, naics_code, set_aside_code),
        loading_text=_loading_message("location", pending.agency, component),
        timeout_text=_timeout_message("location", pending.agency, component),
        allow_default_only=True,
    )
    diagnostics["location"] = location_diag
    return component_options, naics_options, set_asides, location_options, diagnostics


def _option_diagnostic_errors(diagnostics: dict) -> dict:
    return {key: value for key, value in diagnostics.items() if isinstance(value, dict) and value.get("error")}


def render_filters() -> tuple[FilterSnapshot, bool, dict]:
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
        return current, False, {"index": diagnostics}
    freshness = index_freshness()
    st.session_state.option_index_refresh_needed = bool(freshness.get("is_stale"))
    agencies = [record["agency_name"] for record in get_agency_options()]
    current: FilterSnapshot = st.session_state.pending_snapshot
    start_date, end_date, date_error = _pending_date_values(current)
    agency_options = [""] + agencies
    agency_ok = bool(agencies)
    if not agency_ok:
        agency_options = [UNAVAILABLE]
    agency = st.selectbox(
        "Agency",
        agency_options,
        index=_safe_index(agency_options, current.agency),
        format_func=_display_option,
        disabled=not agency_ok,
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
    component_options, naics_options, set_asides, location_options, diagnostics = _option_sets(temporary_snapshot)
    component_config = get_agency_component_config(agency)
    component = current.component if current.component in component_options else ALL_COMPONENTS
    component = st.selectbox(
        component_config["label"],
        component_options,
        index=_safe_index(component_options, component),
        format_func=_display_option,
        disabled=component_options == [UNAVAILABLE],
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
    component_options, naics_options, set_asides, location_options, diagnostics = _option_sets(refreshed_snapshot)
    naics = current.naics if current.naics in naics_options else ALL_NAICS
    naics = st.selectbox("NAICS", naics_options, index=_safe_index(naics_options, naics), format_func=_display_option, disabled=naics_options == [UNAVAILABLE])
    if naics == UNAVAILABLE:
        naics = ALL_NAICS

    with st.expander("Optional refinements", expanded=False):
        set_aside = current.set_aside if current.set_aside in set_asides else ALL_SET_ASIDES
        set_aside = st.selectbox("Set-Aside", set_asides, index=_safe_index(set_asides, set_aside), format_func=_display_option)
        location = current.location if current.location in location_options else ALL_LOCATIONS
        location = st.selectbox("Performance Location", location_options, index=_safe_index(location_options, location), format_func=_display_option)

    start_date, end_date, date_error = render_date_range(current)
    if date_error:
        st.warning(date_error)

    snapshot = FilterSnapshot(agency=agency, component=component, naics=naics, set_aside=set_aside, location=location, start_date=start_date, end_date=end_date)
    st.session_state.pending_snapshot = snapshot
    ready = bool(not date_error and agency and component_options != [UNAVAILABLE] and naics_options != [UNAVAILABLE])
    if not agency_ok:
        diagnostics["agency"] = {"error": {"message": "Unable to load options"}}
    return snapshot, ready, diagnostics


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


def render_leaderboard(leaderboard: pd.DataFrame) -> str:
    st.markdown('<div class="section-title">Top Competitors</div>', unsafe_allow_html=True)
    if leaderboard.empty:
        st.info("No contractors found for this scope.")
        return ""
    display = leaderboard.copy()
    display["Obligations in Scope"] = display["Obligations in Scope"].apply(format_full_money)
    display["Market Share"] = display["Market Share"].apply(format_percent)
    display["Most Recent Action Date"] = display["Most Recent Action Date"].apply(lambda value: value.isoformat() if pd.notna(value) and value else "")
    st.dataframe(display, use_container_width=True, hide_index=True)
    options = leaderboard["Contractor Name"].tolist()
    return st.selectbox("Contractor detail", [""] + options, format_func=lambda value: value or "Select a contractor")


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
        st.markdown(
            f"""
            <div class="market-concentration-legend-row">
                <div class="market-concentration-legend-name">{html.escape(row["contractor"])}</div>
                <div class="market-concentration-legend-metrics">{html.escape(format_full_money(row["amount"]))} · {html.escape(format_percent(row["share"]))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_awards(awards: pd.DataFrame) -> None:
    st.markdown('<div class="section-title">Top Relevant Awards</div>', unsafe_allow_html=True)
    if awards.empty:
        st.info("No award rows found for this scope.")
        return
    visible = awards.head(25).copy()
    rows = []
    for row in visible.to_dict("records"):
        link = row.get("USAspending Award Link") or ""
        award = html.escape(str(row.get("Award ID") or "Unavailable"))
        award_markup = f'<a href="{html.escape(link)}" target="_blank" rel="noopener noreferrer">{award}</a>' if link else award
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('Contractor') or ''))}</td>"
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
    with st.expander(f"Contractor Detail - {detail['contractor_name']}", expanded=True):
        st.write(
            f"{format_full_money(detail['obligations'])} in scope, "
            f"{format_percent(detail['market_share'])} market share, "
            f"{detail['unique_awards']} unique awards."
        )
        if detail["recipient_entities"]:
            st.markdown("Recipient entities")
            st.dataframe(pd.DataFrame(detail["recipient_entities"]), use_container_width=True, hide_index=True, column_config={"recipient_link": st.column_config.LinkColumn("USAspending recipient search")})
        render_awards(detail["top_awards"])
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
    pending, options_ready, option_diagnostics = render_filters()
    option_errors = _option_diagnostic_errors(option_diagnostics)
    if option_errors:
        st.warning("Unable to load options")
        render_diagnostics(option_errors)
    if st.session_state.analysis_results is not None and snapshots_differ(pending, st.session_state.analyzed_snapshot):
        st.caption("Filters changed")
    disabled = analysis_disabled(pending, options_ready, _validate_date_range(pending.start_date, pending.end_date))
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
    chips = active_filter_chips(analyzed, config["label"])
    if chips:
        st.markdown('<div class="applied-filter-heading">Applied filters</div>', unsafe_allow_html=True)
        st.markdown("".join(f'<span class="applied-filter-chip">{html.escape(chip)}</span>' for chip in chips), unsafe_allow_html=True)
    render_scope_line(results)
    render_kpis(results)
    selected_contractor = render_leaderboard(results["leaderboard"])
    st.markdown('<div class="section-title">Market Concentration</div>', unsafe_allow_html=True)
    render_concentration(results["concentration"])
    render_awards(results["awards"])
    render_detail(results, selected_contractor)
