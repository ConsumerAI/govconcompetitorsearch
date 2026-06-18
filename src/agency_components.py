from __future__ import annotations

import pandas as pd

from .constants import ALL_COMPONENTS
from .utils import clean_text


AGENCY_COMPONENT_CONFIG = {
    "Department of State": {
        "label": "Bureau / Funding Office",
        "field_code": "funding_office_code",
        "field_name": "funding_office_name",
        "dimension_type": "funding_office",
    },
    "__default__": {
        "label": "Subagency / Bureau",
        "field_code": "awarding_sub_agency_code",
        "field_name": "awarding_sub_agency_name",
        "dimension_type": "awarding_subagency",
    },
}


def get_agency_component_config(agency_name: str) -> dict:
    return dict(AGENCY_COMPONENT_CONFIG.get(clean_text(agency_name), AGENCY_COMPONENT_CONFIG["__default__"]))


def transaction_component_names(record: dict, config: dict, agency_name: str = "") -> list[str]:
    if config["dimension_type"] == "funding_office":
        if clean_text(record.get("funding_agency_name")) != clean_text(agency_name):
            return []
        name = clean_text(record.get(config["field_name"]))
        return [name] if name else []
    names = []
    for field in ("awarding_sub_agency_name", "funding_sub_agency_name"):
        name = clean_text(record.get(field))
        if name and name not in names:
            names.append(name)
    return names


def transaction_matches_component(record: dict, component_name: str, config: dict, agency_name: str = "") -> bool:
    component = clean_text(component_name)
    if not component:
        return False
    if config["dimension_type"] == "funding_office":
        agency = clean_text(agency_name or record.get("funding_agency_name"))
        if clean_text(record.get("funding_agency_name")) != agency:
            return False
        return clean_text(record.get(config["field_name"])) == component
    return component in {
        clean_text(record.get("awarding_sub_agency_name")),
        clean_text(record.get("funding_sub_agency_name")),
    }


def infer_subtier_filter_type(transactions: pd.DataFrame, component_name: str) -> str:
    component = clean_text(component_name).lower()
    if not component or transactions is None or transactions.empty:
        return "awarding"
    awarding_hits = 0
    funding_hits = 0
    for record in transactions.to_dict("records"):
        if float(record.get("federal_action_obligation") or 0) == 0:
            continue
        if clean_text(record.get("awarding_sub_agency_name")).lower() == component:
            awarding_hits += 1
        if clean_text(record.get("funding_sub_agency_name")).lower() == component:
            funding_hits += 1
    if funding_hits and not awarding_hits:
        return "funding"
    if awarding_hits and funding_hits:
        return "dual"
    return "awarding"


def build_agency_component_options(transactions: pd.DataFrame, agency_name: str) -> list[dict]:
    config = get_agency_component_config(agency_name)
    code_field = config["field_code"]
    name_field = config["field_name"]
    options_by_name: dict[str, dict] = {}
    if transactions is None or transactions.empty or name_field not in transactions.columns:
        return [{"label": ALL_COMPONENTS, "value": ALL_COMPONENTS, "code": "", "name": ""}]
    scoped = transactions
    if config["dimension_type"] == "funding_office" and "funding_agency_name" in scoped.columns:
        funding_agencies = scoped["funding_agency_name"].map(clean_text)
        if funding_agencies.any():
            scoped = scoped[funding_agencies == clean_text(agency_name)]
    for row in scoped.to_dict("records"):
        if config["dimension_type"] == "awarding_subagency":
            names = transaction_component_names(row, config, agency_name)
        else:
            names = [clean_text(row.get(name_field))]
        for name in names:
            if not name or name.lower() in {"none", "nan", "unspecified"}:
                continue
            code = clean_text(row.get(code_field)) if code_field in transactions.columns else ""
            options_by_name[name.lower()] = {"label": name, "value": name, "code": code, "name": name}
    return [{"label": ALL_COMPONENTS, "value": ALL_COMPONENTS, "code": "", "name": ""}] + sorted(
        options_by_name.values(),
        key=lambda option: option["label"].lower(),
    )
