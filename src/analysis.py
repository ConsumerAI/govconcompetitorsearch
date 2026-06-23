from __future__ import annotations

from collections import Counter

import pandas as pd

from .agency_components import get_agency_component_config
from .constants import ALL_COMPONENTS, ALL_LOCATIONS, ALL_NAICS, ALL_SET_ASIDES, COUNTRY_NAMES, OPTION_SEPARATOR, STATE_OPTIONS
from .state import FilterSnapshot
from .utils import (
    clean_text,
    first_present,
    parse_amount,
    parse_date,
    usaspending_award_url,
    usaspending_recipient_profile_url,
)


def canonical_contractor_name(name: object) -> str:
    text = clean_text(name).upper()
    suffixes = [", INC.", " INC.", ", LLC", " LLC", ", LTD.", " LTD.", " CORPORATION", " CORP."]
    for suffix in suffixes:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return " ".join(text.split()) or "UNKNOWN CONTRACTOR"


def _field(row: dict, aliases: list[str]) -> object | None:
    return first_present(row, aliases)


def _parts(value: object, code_aliases: list[str] | None = None, name_aliases: list[str] | None = None) -> tuple[str, str]:
    code_aliases = code_aliases or ["code", "id"]
    name_aliases = name_aliases or ["name", "description", "label"]
    if isinstance(value, dict):
        code = clean_text(first_present(value, code_aliases))
        name = clean_text(first_present(value, name_aliases))
        return code, name
    if isinstance(value, list) and value:
        return _parts(value[0], code_aliases, name_aliases)
    text = clean_text(value)
    if " - " in text:
        code, name = text.split(" - ", 1)
        return clean_text(code), clean_text(name)
    return text, ""


def _office_parts(row: dict, object_alias: str, code_alias: str, name_alias: str) -> tuple[str, str]:
    code, name = _parts(
        row.get(object_alias),
        ["code", "id", code_alias],
        ["name", "office_name", name_alias],
    )
    return (
        code or clean_text(row.get(code_alias)),
        name or clean_text(row.get(name_alias)),
    )


def _place_parts(row: dict) -> tuple[str, str]:
    place = first_present(row, ["Primary Place of Performance", "primary_place_of_performance", "Place of Performance"])
    country, state = _parts(place, ["country_code", "country", "country_code_alpha3"], ["state_code", "state"])
    country = country or clean_text(
        _field(
            row,
            [
                "primary_place_of_performance_country_code",
                "place_of_performance_country_code",
                "Place of Performance Country Code",
                "pop_country_code",
            ],
        )
    ).upper()
    state = state or clean_text(
        _field(
            row,
            [
                "primary_place_of_performance_state_code",
                "place_of_performance_state_code",
                "Place of Performance State Code",
                "pop_state",
            ],
        )
    ).upper()
    return country.upper(), state.upper()


def normalize_transactions(rows: list[dict], default_agency: str = "") -> pd.DataFrame:
    normalized = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        agency = clean_text(_field(row, ["awarding_agency_name", "Awarding Agency Name", "awarding_toptier_agency_name"])) or default_agency
        recipient = clean_text(_field(row, ["recipient_name", "Recipient Name", "Awardee Name"])) or "Unknown Contractor"
        award_key = clean_text(_field(row, ["contract_award_unique_key", "generated_unique_award_id", "generated_internal_id", "Award ID"]))
        award_id = clean_text(_field(row, ["award_id_piid", "Award ID", "PIID"])) or award_key
        country_code, state_code = _place_parts(row)
        awarding_code, awarding_name = _office_parts(row, "Awarding Office", "awarding_office_code", "awarding_office_name")
        funding_code, funding_name = _office_parts(row, "Funding Office", "funding_office_code", "funding_office_name")
        subagency_code, subagency_name = _parts(
            first_present(row, ["Awarding Sub Agency", "Awarding Subagency", "awarding_sub_agency"]),
            ["code", "id", "awarding_sub_agency_code"],
            ["name", "agency_name", "awarding_sub_agency_name"],
        )
        naics_code, naics_description = _parts(first_present(row, ["NAICS", "naics"]))
        normalized.append(
            {
                "row_order": index,
                "contract_award_unique_key": award_key,
                "award_id_piid": award_id,
                "modification_number": clean_text(_field(row, ["modification_number", "Mod", "Modification Number"])),
                "transaction_number": clean_text(_field(row, ["transaction_number", "Transaction Number"])),
                "recipient_name": recipient,
                "canonical_contractor": canonical_contractor_name(recipient),
                "recipient_uei": clean_text(_field(row, ["recipient_uei", "Recipient UEI", "awardee_or_recipient_uei"])),
                "federal_action_obligation": parse_amount(
                    _field(row, ["Transaction Amount", "federal_action_obligation", "Federal Action Obligation", "transaction_obligated_amount", "amount"])
                ),
                "current_total_value_of_award": parse_amount(
                    _field(row, ["current_total_value_of_award", "Current Total Value of Award", "Current Award Value"])
                ),
                "potential_total_value_of_award": parse_amount(
                    _field(row, ["potential_total_value_of_award", "Potential Total Value of Award", "Award Ceiling"])
                ),
                "action_date": parse_date(_field(row, ["action_date", "Action Date", "date"])),
                "transaction_description": clean_text(
                    _field(row, ["transaction_description", "Transaction Description", "award_description", "Description"])
                ),
                "awarding_agency_name": agency,
                "funding_agency_name": clean_text(_field(row, ["funding_agency_name", "Funding Agency Name", "Funding Agency"])),
                "funding_sub_agency_name": clean_text(_field(row, ["funding_sub_agency_name", "Funding Sub Agency Name", "Funding Sub Agency"])),
                "awarding_sub_agency_code": subagency_code or clean_text(_field(row, ["awarding_sub_agency_code", "Awarding Sub Agency Code"])),
                "awarding_sub_agency_name": subagency_name or clean_text(_field(row, ["awarding_sub_agency_name", "Awarding Sub Agency Name", "Awarding Subagency"])),
                "funding_office_code": funding_code,
                "funding_office_name": funding_name,
                "awarding_office_code": awarding_code,
                "awarding_office_name": awarding_name,
                "naics_code": naics_code or clean_text(_field(row, ["naics_code", "NAICS Code", "naics"])),
                "naics_description": naics_description or clean_text(_field(row, ["naics_description", "NAICS Description", "naics_desc"])),
                "set_aside_type": clean_text(
                    _field(row, ["set_aside_type", "set_aside_type_code", "type_of_set_aside", "Set-Aside Type"])
                ),
                "place_of_performance_country_code": country_code,
                "place_of_performance_state_code": state_code,
                "performance_location": format_location(country_code, state_code),
            }
        )
    return pd.DataFrame(normalized)


def format_location(country_code: str, state_code: str) -> str:
    if state_code and country_code in ("", "USA", "US"):
        return f"{STATE_OPTIONS.get(state_code, state_code)}, United States"
    if country_code:
        return COUNTRY_NAMES.get(country_code, country_code)
    return "Unspecified"


def location_option_value(country_code: str, state_code: str) -> str:
    if state_code and country_code in ("", "USA", "US"):
        return state_code
    return country_code


def build_naics_options(transactions: pd.DataFrame) -> list[str]:
    if transactions is None or transactions.empty or "naics_code" not in transactions.columns:
        return [ALL_NAICS]
    values = {}
    for row in transactions.to_dict("records"):
        code = clean_text(row.get("naics_code"))
        if not code:
            continue
        label = code
        description = clean_text(row.get("naics_description"))
        if description:
            label = f"{code} - {description}"
        values[code] = label
    return [ALL_NAICS] + [values[key] for key in sorted(values)]


def build_set_aside_options(transactions: pd.DataFrame) -> list[str]:
    if transactions is None or transactions.empty or "set_aside_type" not in transactions.columns:
        return [ALL_SET_ASIDES]
    values = sorted({clean_text(value) for value in transactions["set_aside_type"].tolist() if clean_text(value)})
    return [ALL_SET_ASIDES] + values


def build_location_options(transactions: pd.DataFrame) -> list[str]:
    if transactions is None or transactions.empty:
        return [ALL_LOCATIONS]
    values = {}
    for row in transactions.to_dict("records"):
        country = clean_text(row.get("place_of_performance_country_code")).upper()
        state = clean_text(row.get("place_of_performance_state_code")).upper()
        value = location_option_value(country, state)
        label = format_location(country, state)
        if value and label != "Unspecified":
            values[value] = f"{value} - {label}"
    return [ALL_LOCATIONS] + [values[key] for key in sorted(values)]


def option_code(option: str) -> str:
    return clean_text(str(option).split(" - ", 1)[0].split(OPTION_SEPARATOR, 1)[0])


def filter_transactions(transactions: pd.DataFrame, snapshot: FilterSnapshot) -> pd.DataFrame:
    if transactions is None or transactions.empty:
        return pd.DataFrame()
    scoped = transactions.copy()
    if snapshot.agency:
        scoped = scoped[scoped["awarding_agency_name"] == snapshot.agency]
    config = get_agency_component_config(snapshot.agency)
    if snapshot.component != ALL_COMPONENTS:
        if config["dimension_type"] == "awarding_subagency":
            component = snapshot.component
            scoped = scoped[
                (scoped["awarding_sub_agency_name"] == component) | (scoped["funding_sub_agency_name"] == component)
            ]
        else:
            scoped = scoped[scoped[config["field_name"]] == snapshot.component]
    if snapshot.naics != ALL_NAICS:
        code = option_code(snapshot.naics)
        scoped = scoped[scoped["naics_code"].astype(str).str.startswith(code, na=False)]
    if snapshot.set_aside != ALL_SET_ASIDES:
        scoped = scoped[scoped["set_aside_type"] == option_code(snapshot.set_aside)]
    if snapshot.location != ALL_LOCATIONS:
        code = option_code(snapshot.location).upper()
        if code in STATE_OPTIONS:
            scoped = scoped[scoped["place_of_performance_state_code"] == code]
        else:
            scoped = scoped[scoped["place_of_performance_country_code"] == code]
    return scoped.reset_index(drop=True)


def competitor_leaderboard(transactions: pd.DataFrame) -> pd.DataFrame:
    columns = ["Rank", "Contractor Name", "Obligations in Scope", "Market Share", "Unique Awards", "Most Recent Action Date"]
    if transactions is None or transactions.empty:
        return pd.DataFrame(columns=columns)
    total_net = float(pd.to_numeric(transactions["federal_action_obligation"], errors="coerce").fillna(0).sum())
    grouped = (
        transactions.groupby("canonical_contractor", as_index=False)
        .agg(
            contractor_name=("recipient_name", "first"),
            primary_uei=("recipient_uei", "first"),
            obligations=("federal_action_obligation", "sum"),
            unique_awards=("contract_award_unique_key", pd.Series.nunique),
            most_recent=("action_date", "max"),
        )
        .sort_values(["obligations", "contractor_name"], ascending=[False, True])
        .reset_index(drop=True)
    )
    grouped["Rank"] = grouped.index + 1
    grouped["Contractor Name"] = grouped["contractor_name"]
    grouped["Recipient Profile Link"] = grouped.apply(
        lambda row: usaspending_recipient_profile_url(str(row["primary_uei"] or ""), str(row["contractor_name"])),
        axis=1,
    )
    grouped["Obligations in Scope"] = grouped["obligations"]
    grouped["Market Share"] = grouped["obligations"].apply(lambda amount: amount / total_net if abs(total_net) >= 0.005 else None)
    grouped["Unique Awards"] = grouped["unique_awards"].astype(int)
    grouped["Most Recent Action Date"] = grouped["most_recent"]
    positive = grouped[grouped["obligations"] > 0]
    non_positive = grouped[grouped["obligations"] <= 0]
    ordered = pd.concat([positive, non_positive], ignore_index=True)
    ordered["Rank"] = ordered.index + 1
    return ordered[columns]


def market_concentration(transactions: pd.DataFrame, top_n: int = 5) -> dict:
    if transactions is None or transactions.empty:
        return {"top_share": None, "positive_total": 0.0, "breakdown": [], "contractor_count": 0}
    positive = transactions[transactions["federal_action_obligation"] > 0].copy()
    positive_total = float(positive["federal_action_obligation"].sum()) if not positive.empty else 0.0
    if positive_total <= 0:
        return {"top_share": None, "positive_total": positive_total, "breakdown": [], "contractor_count": 0}
    grouped = (
        positive.groupby("canonical_contractor", as_index=False)
        .agg(
            contractor=("recipient_name", "first"),
            primary_uei=("recipient_uei", "first"),
            amount=("federal_action_obligation", "sum"),
        )
        .sort_values("amount", ascending=False)
        .reset_index(drop=True)
    )
    top = grouped.head(top_n)
    top_sum = float(top["amount"].sum())
    return {
        "top_share": top_sum / positive_total,
        "positive_total": positive_total,
        "breakdown": [
            {
                "contractor": row["contractor"],
                "amount": float(row["amount"]),
                "share": float(row["amount"]) / positive_total,
                "recipient_profile_link": usaspending_recipient_profile_url(
                    str(row["primary_uei"] or ""),
                    str(row["contractor"]),
                ),
            }
            for row in top.to_dict("records")
        ],
        "contractor_count": int(len(grouped)),
    }


def award_table(transactions: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Contractor",
        "Award ID",
        "Description",
        "Obligations in Scope",
        "Current Award Value",
        "Award Ceiling",
        "Awarding Office",
        "Funding Office",
        "Performance Location",
        "USAspending Award Link",
    ]
    if transactions is None or transactions.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for award_key, group in transactions.groupby("contract_award_unique_key", dropna=False):
        latest = group.sort_values(["action_date", "row_order"], na_position="first").iloc[-1]
        contractor_name = latest["recipient_name"]
        contractor_uei = clean_text(latest["recipient_uei"])
        rows.append(
            {
                "Contractor": contractor_name,
                "Recipient Profile Link": usaspending_recipient_profile_url(contractor_uei, contractor_name),
                "Award ID": latest["award_id_piid"],
                "Description": latest["transaction_description"],
                "Obligations in Scope": float(group["federal_action_obligation"].sum()),
                "Current Award Value": float(latest["current_total_value_of_award"]),
                "Award Ceiling": float(latest["potential_total_value_of_award"]),
                "Awarding Office": latest["awarding_office_name"],
                "Funding Office": latest["funding_office_name"],
                "Performance Location": latest["performance_location"],
                "USAspending Award Link": usaspending_award_url(str(award_key)),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values("Obligations in Scope", ascending=False).reset_index(drop=True)


def contractor_detail(transactions: pd.DataFrame, contractor_name: str) -> dict:
    if transactions is None or transactions.empty:
        return {}
    target = canonical_contractor_name(contractor_name)
    scoped = transactions[transactions["canonical_contractor"] == target].copy()
    if scoped.empty:
        return {}
    total_scope = float(transactions["federal_action_obligation"].sum())
    contractor_total = float(scoped["federal_action_obligation"].sum())
    uei_counts = Counter(clean_text(value) for value in scoped["recipient_uei"].tolist() if clean_text(value))
    return {
        "contractor_name": clean_text(scoped["recipient_name"].iloc[0]),
        "obligations": contractor_total,
        "market_share": contractor_total / total_scope if abs(total_scope) >= 0.005 else None,
        "unique_awards": int(scoped["contract_award_unique_key"].nunique()),
        "recipient_entities": [
            {"uei": uei, "recipient_link": usaspending_recipient_profile_url(uei)} for uei, _count in uei_counts.most_common()
        ],
        "top_awards": award_table(scoped).head(10),
        "recent_actions": scoped.sort_values(["action_date", "row_order"], ascending=[False, False]).head(10),
        "location_mix": scoped.groupby("performance_location", as_index=False)["federal_action_obligation"].sum().sort_values("federal_action_obligation", ascending=False),
        "naics_mix": scoped.groupby(["naics_code", "naics_description"], as_index=False)["federal_action_obligation"].sum().sort_values("federal_action_obligation", ascending=False),
    }


def analyze(transactions: pd.DataFrame, snapshot: FilterSnapshot, period: dict | None = None) -> dict:
    scoped = filter_transactions(transactions, snapshot)
    leaderboard = competitor_leaderboard(scoped)
    awards = award_table(scoped)
    concentration = market_concentration(scoped)
    return {
        "snapshot": snapshot.to_dict(),
        "period": period or {},
        "transactions": scoped,
        "leaderboard": leaderboard,
        "awards": awards,
        "concentration": concentration,
        "kpis": {
            "net_obligations": float(scoped["federal_action_obligation"].sum()) if not scoped.empty else 0.0,
            "contractors": int(scoped["canonical_contractor"].nunique()) if not scoped.empty else 0,
            "unique_awards": int(scoped["contract_award_unique_key"].nunique()) if not scoped.empty else 0,
        },
    }
