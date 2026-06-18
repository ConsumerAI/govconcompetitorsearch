from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.option_index import BUILD_PROGRESS_PATH, INDEX_PATH, metadata


def _format_age(updated_at: str) -> str:
    try:
        updated = datetime.fromisoformat(updated_at)
        age_seconds = (datetime.now(timezone.utc) - updated).total_seconds()
    except ValueError:
        return "unknown"
    if age_seconds < 60:
        return f"{int(age_seconds)}s ago"
    if age_seconds < 3600:
        return f"{int(age_seconds // 60)}m ago"
    return f"{age_seconds / 3600:.1f}h ago"


def main() -> None:
    if not BUILD_PROGRESS_PATH.exists():
        print(f"No build progress file at {BUILD_PROGRESS_PATH}")
        print("Start a rebuild with: python -m scripts.refresh_option_index")
        raise SystemExit(1)

    progress = json.loads(BUILD_PROGRESS_PATH.read_text(encoding="utf-8"))
    print(f"progress_file={BUILD_PROGRESS_PATH}")
    print(f"updated_at={progress.get('updated_at')} ({_format_age(progress.get('updated_at', ''))})")
    print(f"phase={progress.get('phase')}")
    print(f"message={progress.get('message')}")
    for key in sorted(k for k in progress if k not in {"updated_at", "phase", "message"}):
        print(f"{key}={progress[key]}")

    try:
        meta = metadata(INDEX_PATH)
        print(f"index_schema_version={meta.get('schema_version')}")
        print(f"index_generated_at={meta.get('generated_at')}")
    except Exception as exc:
        print(f"index_status=unavailable ({exc})")


if __name__ == "__main__":
    main()
