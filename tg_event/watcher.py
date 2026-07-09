from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


def today_string() -> str:
    return date.today().isoformat()


def load_state(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): int(value) for key, value in data.items()}


def save_state(path: Path, state: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def filter_new_posts(
    rows: list[dict[str, Any]],
    state: dict[str, int],
    since_date: str,
    max_posts_per_channel: int | None = None,
    max_posts_per_cycle: int | None = None,
) -> list[dict[str, Any]]:
    selected = []
    for row in rows:
        if _post_date(row["published_at"]) < since_date:
            continue
        last_message_id = state.get(row["source"], 0)
        if int(row["message_id"]) <= last_message_id:
            continue
        selected.append(row)

    selected.sort(key=lambda row: (row["published_at"], row["source"], int(row["message_id"])))
    if max_posts_per_channel is not None:
        selected = _limit_per_channel(selected, max_posts_per_channel)
    if max_posts_per_cycle is not None:
        return selected[:max_posts_per_cycle]
    return selected


def _limit_per_channel(
    rows: list[dict[str, Any]],
    max_posts_per_channel: int,
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    limited = []
    for row in rows:
        source = row["source"]
        count = counts.get(source, 0)
        if count >= max_posts_per_channel:
            continue
        limited.append(row)
        counts[source] = count + 1
    return limited


def update_state(state: dict[str, int], rows: list[dict[str, Any]]) -> dict[str, int]:
    updated = dict(state)
    for row in rows:
        source = row["source"]
        message_id = int(row["message_id"])
        updated[source] = max(updated.get(source, 0), message_id)
    return updated


def _post_date(value: str) -> str:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).date().isoformat()
