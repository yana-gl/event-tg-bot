from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def load_source_rules(path: Path) -> dict[str, dict[str, list[str]]]:
    if not path.exists():
        return {}
    rules = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rules, dict):
        raise ValueError("Source rules must be an object keyed by channel username")
    for channel, rule in rules.items():
        if not isinstance(rule, dict) or set(rule) - {"organizers", "venues"}:
            raise ValueError(f"Invalid source rule for {channel}")
        for aliases in rule.values():
            if not isinstance(aliases, list) or any(not isinstance(a, str) or not a.strip() for a in aliases):
                raise ValueError(f"Source aliases must be non-empty strings: {channel}")
    return {channel.lstrip("@").casefold(): rule for channel, rule in rules.items()}


def _normalized(value: str | None) -> str:
    return " ".join(re.findall(r"\w+", (value or "").casefold().replace("ё", "е")))


def source_score(
    source: dict[str, Any], event: dict[str, Any], rules: dict,
) -> tuple[int, int, str, int]:
    extraction = source["extracted_event"]
    if extraction is None:
        extraction = event
    rule = rules.get(source["source"].casefold(), {})
    role = 0
    organizer = _normalized(extraction.get("organizer"))
    place = _normalized(extraction.get("place"))
    if organizer and organizer in {_normalized(a) for a in rule.get("organizers", [])}:
        role = 2
    elif place and place in {_normalized(a) for a in rule.get("venues", [])}:
        role = 1
    if source["raw_post_id"] == (event.get("last_change_post_id") or event.get("raw_post_id")):
        role = max(role, event.get("last_change_role", 0))
    completeness = sum(bool(extraction.get(k)) for k in
                       ("title", "date", "end_date", "time", "end_time", "place", "address", "price"))
    return role, completeness, source["published_at"], -source["raw_post_id"]


def select_primary_source(event: dict[str, Any], rules: dict) -> int:
    sources = event["sources"]
    candidates = []
    for source in sources:
        extraction = source["extracted_event"]
        if (source["raw_post_id"] != event["raw_post_id"]
                and (source["raw_post_id"] == event.get("conflict_post_id")
                     or (extraction is not None and extraction.get("action", "announcement") != "announcement"
                         and source["raw_post_id"] != event.get("last_change_post_id")))):
            continue
        if source["raw_post_id"] != event["raw_post_id"] and extraction is not None:
            if any(event.get(k) and extraction.get(k)
                   and _normalized(event[k]) != _normalized(extraction[k])
                   for k in ("date", "end_date", "time", "end_time", "place", "address", "price")):
                continue
        candidates.append(source)
    return max(candidates, key=lambda s: source_score(s, event, rules))["raw_post_id"]
