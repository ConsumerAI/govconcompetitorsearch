from __future__ import annotations

import functools
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Iterable
from urllib.parse import quote, urlencode

import pandas as pd
import requests

from .constants import BASE_URL, OPTION_SEPARATOR


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


@functools.lru_cache(maxsize=4096)
def _recipient_id_for_keyword(keyword: str) -> str:
    text = clean_text(keyword)
    if not text:
        return ""
    try:
        response = requests.post(
            f"{BASE_URL}/api/v2/recipient/",
            json={"keyword": text, "limit": 10, "page": 1},
            timeout=20,
        )
        response.raise_for_status()
        results = response.json().get("results") or []
    except requests.RequestException:
        return ""
    if not results:
        return ""
    normalized = text.upper()
    if len(normalized) == 12 and normalized.isalnum():
        for row in results:
            if clean_text(row.get("uei")).upper() == normalized:
                return clean_text(row.get("id"))
    for row in results:
        if clean_text(row.get("name")).upper() == normalized:
            return clean_text(row.get("id"))
    return clean_text(results[0].get("id"))


def warm_recipient_profile_cache(keywords: Iterable[str], *, max_workers: int = 8) -> None:
    unique: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        text = clean_text(keyword)
        if not text:
            continue
        token = text.upper()
        if token in seen:
            continue
        seen.add(token)
        unique.append(text)
    if not unique:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_recipient_id_for_keyword, unique))


def usaspending_recipient_search_url(query: str) -> str:
    text = clean_text(query)
    if not text:
        return ""
    params = urlencode({"hash": "recipient", "recipient_search_text": text})
    return f"https://www.usaspending.gov/search/?{params}"


def usaspending_recipient_profile_url(uei: str = "", name: str = "") -> str:
    recipient_id = ""
    recipient_uei = clean_text(uei)
    if recipient_uei:
        recipient_id = _recipient_id_for_keyword(recipient_uei)
    if not recipient_id and name:
        recipient_id = _recipient_id_for_keyword(name)
    if recipient_id:
        return f"https://www.usaspending.gov/recipient/{quote(recipient_id, safe='')}/latest"
    return ""

