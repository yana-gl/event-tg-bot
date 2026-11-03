from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timezone
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
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def filter_new_posts(
    rows: list[dict[str, Any]],
    state: dict[str, int],
    since_date: str,
    max_posts_per_channel: int | None = None,
    max_posts_per_cycle: int | None = None,
    known_posts: dict | None = None,
) -> list[dict[str, Any]]:
    selected = []
    for row in rows:
        known = (known_posts or {}).get((row["source"], int(row["message_id"])))
        if known is not None and _version_time(row) < _version_time(known):
            continue
        if known is not None and row["text"] == known["text"] and row.get("edited_at") == known.get("edited_at"):
            continue
        changed = known is not None and (
            row["text"] != known["text"] or row.get("edited_at") != known.get("edited_at")
        )
        if changed:
            changed = _version_time(row) >= _version_time(known)
        if not changed and row["source"] not in state and _post_date(row["published_at"]) < since_date:
            continue
        last_message_id = state.get(row["source"], 0)
        if not changed and int(row["message_id"]) <= last_message_id:
            continue
        selected.append(row)

    selected.sort(key=lambda row: (_version_time(row), row["source"], int(row["message_id"])))
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


def _version_time(row: dict[str, Any]) -> datetime:
    value = row.get("edited_at") or row["published_at"]
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).astimezone(timezone.utc)
