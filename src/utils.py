from __future__ import annotations

from datetime import date, datetime
from urllib.parse import quote

import pandas as pd

from .constants import OPTION_SEPARATOR


def clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def first_present(mapping: dict, keys: list[str]) -> object | None:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def encode_option(code: str, description: str = "") -> str:
    return f"{clean_text(code)}{OPTION_SEPARATOR}{clean_text(description)}"


def decode_option(option: str | None) -> tuple[str, str]:
    if not option:
        return "", ""
    text = str(option)
    if OPTION_SEPARATOR not in text:
        return clean_text(text), ""
    code, description = text.split(OPTION_SEPARATOR, 1)
    return clean_text(code), clean_text(description)


def format_option(option: str) -> str:
    code, description = decode_option(option)
    return f"{code} - {description}" if description else code


def parse_amount(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = clean_text(value).replace("$", "").replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    text = clean_text(value)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def format_money(value: object) -> str:
    amount = parse_amount(value)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1_000_000_000:
        return f"{sign}${amount / 1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"{sign}${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{sign}${amount / 1_000:.1f}K"
    return f"{sign}${amount:,.2f}"


def format_full_money(value: object) -> str:
    amount = parse_amount(value)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def format_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%"


def usaspending_award_url(contract_award_unique_key: str) -> str:
    award_key = clean_text(contract_award_unique_key)
    if not award_key:
        return ""
    return f"https://www.usaspending.gov/award/{quote(award_key, safe='_')}"


def usaspending_recipient_search_url(query: str) -> str:
    text = clean_text(query)
    if not text:
        return ""
    return f"https://www.usaspending.gov/search/?hash=recipient&recipient_search_text={quote(text)}"


def usaspending_recipient_profile_url(uei: str = "", name: str = "") -> str:
    # USAspending direct /recipient/{uei}/latest links require internal recipient hashes.
    # Recipient search is reliable with either UEI or company name.
    query = clean_text(uei) or clean_text(name)
    return usaspending_recipient_search_url(query)

