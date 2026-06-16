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
        name = clean_text(row.get(name_field))
        if not name or name.lower() in {"none", "nan", "unspecified"}:
            continue
        code = clean_text(row.get(code_field)) if code_field in transactions.columns else ""
        options_by_name[name.lower()] = {"label": name, "value": name, "code": code, "name": name}
    return [{"label": ALL_COMPONENTS, "value": ALL_COMPONENTS, "code": "", "name": ""}] + sorted(
        options_by_name.values(),
        key=lambda option: option["label"].lower(),
    )
