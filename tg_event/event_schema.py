from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import Any


EVENT_FIELDS = {
    "is_event",
    "title",
    "date",
    "end_date",
    "time",
    "end_time",
    "place",
    "address",
    "category",
    "price",
    "confidence",
    "reason",
}

ALLOWED_CATEGORIES = {
    "music",
    "cinema",
    "lecture",
    "exhibition",
    "market",
    "workshop",
    "food",
    "sport",
    "party",
    "kids",
    "other",
}

CHANGE_FIELDS = {"title", "date", "end_date", "time", "end_time", "place", "address", "price", "category"}
EVENT_ACTIONS = {"announcement", "update", "reschedule", "cancel"}


class EventStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    REJECTED = "rejected"
    REPEAT = "repeat"
    NOT_EVENT = "not_event"


@dataclass(frozen=True)
class ParsedEvent:
    is_event: bool
    title: str | None
    date: str | None
    end_date: str | None
    time: str | None
    end_time: str | None
    place: str | None
    address: str | None
    category: str | None
    price: str | None
    confidence: float
    reason: str
    status: EventStatus
    organizer: str | None = None
    action: str = "announcement"
    previous_date: str | None = None
    previous_time: str | None = None
    changed_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class ParsedEventResponse:
    events: list[ParsedEvent]
    post_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [event.to_dict() for event in self.events],
            "post_reason": self.post_reason,
        }


def validate_event(payload: dict[str, Any]) -> ParsedEvent:
    extra_fields = set(payload) - (EVENT_FIELDS | {
        "organizer", "action", "previous_date", "previous_time", "changed_fields",
    })
    if extra_fields:
        names = ", ".join(sorted(extra_fields))
        raise ValueError(f"Unexpected extra fields: {names}")

    missing_fields = EVENT_FIELDS - set(payload)
    if missing_fields:
        names = ", ".join(sorted(missing_fields))
        raise ValueError(f"Missing required fields: {names}")

    confidence = float(payload["confidence"])
    if confidence < 0 or confidence > 1:
        raise ValueError("confidence must be between 0 and 1")

    category = _nullable_text(payload["category"])
    if category is not None and category not in ALLOWED_CATEGORIES:
        allowed = ", ".join(sorted(ALLOWED_CATEGORIES))
        raise ValueError(f"category must be one of: {allowed}")

    title = _nullable_text(payload["title"])
    event_date = _nullable_text(payload["date"])
    end_date = _nullable_text(payload["end_date"])
    is_event = bool(payload["is_event"])
    action = payload.get("action", "announcement")
    if not isinstance(action, str) or action not in EVENT_ACTIONS:
        raise ValueError("Unknown event action")
    changed_fields = payload.get("changed_fields", [])
    if (not isinstance(changed_fields, list)
            or any(not isinstance(k, str) or k not in CHANGE_FIELDS for k in changed_fields)):
        raise ValueError("Invalid changed_fields")
    if not is_event:
        status = EventStatus.NOT_EVENT
    elif title and _valid_event_dates(event_date, end_date):
        status = EventStatus.PUBLISHED
    else:
        status = EventStatus.DRAFT

    return ParsedEvent(
        is_event=is_event,
        title=title,
        date=event_date,
        end_date=end_date,
        time=_nullable_text(payload["time"]),
        end_time=_nullable_text(payload["end_time"]),
        place=_nullable_text(payload["place"]),
        address=_nullable_text(payload["address"]),
        category=category,
        price=_nullable_text(payload["price"]),
        confidence=confidence,
        reason=str(payload["reason"]).strip(),
        status=status,
        organizer=_nullable_text(payload.get("organizer")),
        action=action,
        previous_date=_nullable_text(payload.get("previous_date")),
        previous_time=_nullable_text(payload.get("previous_time")),
        changed_fields=changed_fields,
    )


def validate_event_response(
    payload: dict[str, Any],
) -> ParsedEventResponse:
    extra_fields = set(payload) - {"events", "post_reason"}
    if extra_fields:
        names = ", ".join(sorted(extra_fields))
        raise ValueError(f"Unexpected extra fields: {names}")

    if "events" not in payload:
        raise ValueError("Missing required field: events")
    if "post_reason" not in payload:
        raise ValueError("Missing required field: post_reason")
    if not isinstance(payload["events"], list):
        raise ValueError("events must be a list")

    events = [
        validate_event(event_payload)
        for event_payload in payload["events"]
    ]
    return ParsedEventResponse(
        events=events,
        post_reason=str(payload["post_reason"]).strip(),
    )


def _valid_event_dates(start: str | None, end: str | None) -> bool:
    if start is None:
        return False
    try:
        start_date = date.fromisoformat(start)
        if start_date.isoformat() != start:
            return False
        if end is not None:
            end_date = date.fromisoformat(end)
            if end_date.isoformat() != end or end_date < start_date:
                return False
    except ValueError:
        return False
    return True


def _nullable_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
