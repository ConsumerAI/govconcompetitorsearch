from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .constants import ALL_LOCATIONS, ALL_NAICS, ALL_SET_ASIDES, SET_ASIDE_TYPE_OPTIONS
from .utils import encode_option, format_option

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NAICS_REFERENCE_PATH = PROJECT_ROOT / "data" / "naics_reference.json"
LOCATION_REFERENCE_PATH = PROJECT_ROOT / "data" / "location_reference.json"


@lru_cache(maxsize=1)
def _naics_reference() -> list[dict]:
    if not NAICS_REFERENCE_PATH.exists():
        return []
    payload = json.loads(NAICS_REFERENCE_PATH.read_text(encoding="utf-8"))
    return [row for row in payload if isinstance(row, dict) and row.get("code")]


@lru_cache(maxsize=1)
def _location_reference() -> list[dict]:
    if not LOCATION_REFERENCE_PATH.exists():
        return []
    payload = json.loads(LOCATION_REFERENCE_PATH.read_text(encoding="utf-8"))
    return [row for row in payload if isinstance(row, dict) and row.get("code")]


def global_naics_option_values() -> tuple[list[str], dict]:
    rows = _naics_reference()
    values = [encode_option(row["code"], row.get("description", "")) for row in rows]
    return [ALL_NAICS] + sorted(values, key=lambda option: format_option(option).lower()), {
        "lookup_type": "NAICS",
        "source": "static_reference",
        "rows_returned": len(rows),
        "cache_level_used": "static",
    }


def global_set_aside_option_values() -> tuple[list[str], dict]:
    values = [f"{code} - {label}" if label else code for code, label in sorted(SET_ASIDE_TYPE_OPTIONS.items(), key=lambda item: item[1])]
    return [ALL_SET_ASIDES] + values, {
        "lookup_type": "Set-Aside",
        "source": "static_reference",
        "rows_returned": len(values),
        "cache_level_used": "static",
    }


def global_location_option_values() -> tuple[list[str], dict]:
    rows = _location_reference()
    labels = {row["code"]: row.get("label") or row["code"] for row in rows}
    return [ALL_LOCATIONS] + [labels[code] for code in sorted(labels)], {
        "lookup_type": "Performance Location",
        "source": "static_reference",
        "rows_returned": len(labels),
        "cache_level_used": "static",
    }
