from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from .constants import ALL_COMPONENTS, ALL_LOCATIONS, ALL_NAICS, ALL_SET_ASIDES


def add_calendar_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


def default_start_date() -> str:
    return add_calendar_years(date.today(), -6).isoformat()


def default_end_date() -> str:
    return date.today().isoformat()


def recent_wins_start_date() -> str:
    return add_calendar_years(date.today(), -1).isoformat()


def recent_wins_end_date() -> str:
    return date.today().isoformat()


def recent_wins_period() -> tuple[str, str]:
    return recent_wins_start_date(), recent_wins_end_date()


@dataclass(frozen=True)
class FilterSnapshot:
    agency: str = ""
    component: str = ALL_COMPONENTS
    naics: str = ALL_NAICS
    set_aside: str = ALL_SET_ASIDES
    location: str = ALL_LOCATIONS
    start_date: str = field(default_factory=default_start_date)
    end_date: str = field(default_factory=default_end_date)

    def to_dict(self) -> dict:
        return asdict(self)


def fresh_session_state() -> dict:
    return {
        "pending": FilterSnapshot(),
        "analyzed": None,
        "results": None,
        "active_chips": [],
        "prior_analyzed_filter_snapshot": None,
    }


def snapshots_differ(pending: FilterSnapshot, analyzed: FilterSnapshot | None) -> bool:
    if analyzed is None:
        return False
    return (
        pending.agency,
        pending.component,
        pending.naics,
        pending.set_aside,
        pending.location,
        pending.start_date,
        pending.end_date,
    ) != (
        analyzed.agency,
        analyzed.component,
        analyzed.naics,
        analyzed.set_aside,
        analyzed.location,
        analyzed.start_date,
        analyzed.end_date,
    )


def active_filter_chip_entries(snapshot: FilterSnapshot | None, component_label: str = "Agency Component") -> list[dict[str, str]]:
    if snapshot is None:
        return []
    chips: list[dict[str, str]] = []
    if snapshot.agency:
        chips.append({"id": "agency", "label": f"Agency: {snapshot.agency}"})
    if snapshot.component != ALL_COMPONENTS:
        chips.append({"id": "component", "label": f"{component_label}: {snapshot.component}"})
    if snapshot.naics != ALL_NAICS:
        chips.append({"id": "naics", "label": f"NAICS: {snapshot.naics}"})
    if snapshot.set_aside != ALL_SET_ASIDES:
        chips.append({"id": "set_aside", "label": f"Set-Aside: {snapshot.set_aside}"})
    if snapshot.location != ALL_LOCATIONS:
        chips.append({"id": "location", "label": f"Performance Location: {snapshot.location}"})
    if snapshot.start_date and snapshot.end_date:
        chips.append({"id": "period", "label": f"Period: {snapshot.start_date} to {snapshot.end_date}"})
    return chips


def active_filter_chips(snapshot: FilterSnapshot | None, component_label: str = "Agency Component") -> list[str]:
    return [chip["label"] for chip in active_filter_chip_entries(snapshot, component_label)]


def run_new_analysis(session: dict, pending: FilterSnapshot, results: dict) -> dict:
    replacement = dict(session)
    replacement["pending"] = pending
    replacement["analyzed"] = pending
    replacement["results"] = results
    replacement["active_chips"] = active_filter_chips(pending)
    replacement["prior_analyzed_filter_snapshot"] = pending.to_dict()
    return replacement


def update_pending_only(session: dict, pending: FilterSnapshot) -> dict:
    replacement = dict(session)
    replacement["pending"] = pending
    return replacement
