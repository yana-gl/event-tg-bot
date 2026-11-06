from __future__ import annotations

from sqlite3 import Connection
from typing import Any

from tg_event import database
from tg_event.event_schema import ALLOWED_CATEGORIES, EventStatus


EDITABLE_STATUSES = {
    EventStatus.DRAFT.value,
    EventStatus.PUBLISHED.value,
    EventStatus.REJECTED.value,
    EventStatus.NOT_EVENT.value,
}

_IGNORE_FIELDS = {"is_event", "confidence", "reason", "raw_post_id"}


class ValidationError(ValueError):
    def __init__(self, details: list[str]) -> None:
        super().__init__("validation")
        self.details = details


def validate_fields(fields: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "event_state" in fields and (
        not isinstance(fields["event_state"], str)
        or fields["event_state"] not in {"scheduled", "postponed", "cancelled"}
    ):
        errors.append("event_state must be scheduled, postponed or cancelled")
    if "status" in fields and (
        not isinstance(fields["status"], str) or fields["status"] not in EDITABLE_STATUSES
    ):
        errors.append("status must be one of: draft, published, rejected, not_event")
    if "category" in fields:
        category = fields["category"]
        if category is not None and (not isinstance(category, str) or category not in ALLOWED_CATEGORIES):
            errors.append("category must be one of: " + ", ".join(sorted(ALLOWED_CATEGORIES)))
    for key in ("title", "place", "address", "price", "date", "end_date", "time", "end_time"):
        if key in fields and fields[key] is not None and not isinstance(fields[key], str):
            errors.append(f"{key} must be a string or null")
    return errors


def prepare_event_update(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    clean = {key: value for key, value in payload.items() if key not in _IGNORE_FIELDS}
    resolution = clean.pop("resolve_conflict", None)
    if resolution is not None and not isinstance(resolution, dict):
        raise ValueError("invalid conflict resolution")
    for key in ("title", "place", "address", "price", "date", "end_date", "time", "end_time"):
        if clean.get(key) == "":
            clean[key] = None
    errors = validate_fields(clean)
    if errors:
        raise ValidationError(errors)
    return clean, resolution


def update_event(connection: Connection, event_id: int, payload: dict[str, Any]) -> bool:
    fields, resolution = prepare_event_update(payload)
    return database.update_event(connection, event_id, fields, resolve_conflict=resolution)


def merge_events(connection: Connection, event_id: int, payload: dict[str, Any]) -> int | None:
    target_id = payload.get("target_id")
    if type(target_id) is not int or not 0 < target_id <= 2 ** 63 - 1:
        raise ValueError("target_id must be a positive integer")
    return target_id if database.merge_events(connection, event_id, target_id) else None
