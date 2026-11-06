from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from tg_event.event_schema import CHANGE_FIELDS, _valid_event_dates
from tg_event.source_policy import source_score


def change_timestamp(post: dict[str, Any]) -> str:
    value = post.get("edited_at") or post["published_at"]
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()


def apply_event_update(
    connection: sqlite3.Connection, current: dict[str, Any], raw_post_id: int,
    event: dict[str, Any], rules: dict, *, is_edit: bool = False,
) -> bool:
    action = event.get("action", "announcement")
    post = dict(connection.execute(
        "SELECT p.*, s.username AS source FROM raw_posts p JOIN sources s ON s.id = p.source_id WHERE p.id = ?",
        (raw_post_id,),
    ).fetchone())
    stamp = change_timestamp(post)
    if current["last_change_at"] and stamp < current["last_change_at"]:
        return False
    previous = connection.execute(
        "SELECT extracted_json FROM event_sources WHERE event_id = ? AND raw_post_id = ?",
        (current["id"], raw_post_id),
    ).fetchone()
    baseline_id = current["last_change_post_id"] or current["raw_post_id"]
    baseline_role = current["last_change_role"]
    if is_edit and raw_post_id == baseline_id and previous is not None and previous[0] is not None:
        previous_role = source_score(dict(raw_post_id=raw_post_id, source=post["source"],
                                          published_at=stamp, extracted_event=json.loads(previous[0])), current, rules)[0]
        baseline_role = max(baseline_role, previous_role)
        if baseline_role != current["last_change_role"]:
            connection.execute("UPDATE events SET last_change_role = ? WHERE id = ?",
                               (baseline_role, current["id"]))
    fields = event.get("changed_fields", [k for k in CHANGE_FIELDS if event.get(k) is not None])
    if action == "announcement":
        if not is_edit or previous is None or previous[0] is None:
            return True
        old = json.loads(previous[0])
        fields = [k for k in CHANGE_FIELDS if event.get(k) is not None and event.get(k) != old.get(k)]
        if not fields:
            return True
        action = "update"

    updates = {k: event.get(k) for k in fields if k in CHANGE_FIELDS}
    if action == "cancel":
        updates = {"event_state": "cancelled"}
    elif action == "reschedule" and not _valid_event_dates(event.get("date"), event.get("end_date")):
        updates = {"event_state": "postponed", "date": None, "end_date": None,
                   "time": None, "end_time": None}
    elif action == "reschedule":
        updates["date"] = event["date"]
        updates["event_state"] = "scheduled"
        if event["date"] != current["date"] and not event.get("end_date"):
            updates["end_date"] = None
    elif current["event_state"] == "postponed" and "date" in updates:
        if _valid_event_dates(event.get("date"), event.get("end_date")):
            updates["event_state"] = "scheduled"

    changes = {k: v for k, v in updates.items() if current[k] != v}
    if not changes:
        return False
    baseline = connection.execute(
        """
        SELECT p.id AS raw_post_id, p.published_at, s.username AS source, es.extracted_json, es.version_at
        FROM raw_posts p JOIN sources s ON s.id = p.source_id
        LEFT JOIN event_sources es ON es.raw_post_id = p.id AND es.event_id = ? WHERE p.id = ?
        """, (current["id"], baseline_id),
    ).fetchone()
    baseline_time = current["last_change_at"] or change_timestamp(
        {"published_at": baseline["version_at"] or baseline["published_at"]}
    )
    if stamp < baseline_time:
        return False
    linked_sources = connection.execute(
        """
        SELECT p.id, es.extracted_json FROM event_sources es JOIN raw_posts p ON p.id = es.raw_post_id
        WHERE es.event_id = ? AND p.source_id = ?
        """, (current["id"], post["source_id"]),
    ).fetchall()
    known_sources = [r for r in linked_sources if r["id"] in {current["raw_post_id"], current["last_change_post_id"]}
                     or (r["extracted_json"] and json.loads(r["extracted_json"]).get("action", "announcement") == "announcement")]
    known_channel = bool(known_sources)
    original = dict(baseline)
    original["extracted_event"] = json.loads(original["extracted_json"]) if original["extracted_json"] else None
    original_role = max(baseline_role, source_score(original, current, rules)[0])
    incoming = dict(raw_post_id=raw_post_id, source=post["source"], published_at=stamp, extracted_event=event)
    role = source_score(incoming, current, rules)[0]
    if known_channel:
        for linked in known_sources:
            if linked["extracted_json"]:
                role = max(role, source_score(dict(incoming, extracted_event=json.loads(linked["extracted_json"])), current, rules)[0])
        if any(r["id"] == baseline_id for r in known_sources):
            role = max(role, baseline_role)
    locked = {r[0] for r in connection.execute(
        "SELECT field FROM event_field_locks WHERE event_id = ?", (current["id"],)
    )}
    if updates.get("event_state") == "postponed":
        changes = {k: v for k, v in changes.items() if k not in locked}
    if not changes:
        return False
    valid_date = (not ({"date", "end_date"} & set(changes))
                  or updates.get("event_state") == "postponed"
                  or _valid_event_dates(updates.get("date", current["date"]),
                                        updates.get("end_date", current["end_date"])))
    conflict = (not ((known_channel and role >= original_role) or role > original_role)
                or bool(set(changes) & locked) or not valid_date
                or (current["event_state"] == "cancelled" and action != "cancel")
                or (stamp == baseline_time and baseline_id != raw_post_id))
    if conflict:
        if current["conflict_at"] is None or stamp >= current["conflict_at"]:
            connection.execute("UPDATE events SET conflict_post_id = ?, conflict_at = ? WHERE id = ?",
                               (raw_post_id, stamp, current["id"]))
        return False
    if ("date" in changes or changes.get("event_state") == "postponed") and current["date"]:
        changes["previous_date"] = current["date"]
    changes.update(last_change_at=stamp, last_change_post_id=raw_post_id, last_change_role=role)
    if current["conflict_at"] is None or stamp >= current["conflict_at"]:
        changes.update(conflict_post_id=None, conflict_at=None)
    assignments = ", ".join(f"{k} = ?" for k in changes)
    connection.execute(f"UPDATE events SET {assignments} WHERE id = ?",
                       [*changes.values(), current["id"]])
    return True
