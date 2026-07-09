from __future__ import annotations

from dataclasses import asdict, dataclass
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


class EventStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
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


def validate_event(payload: dict[str, Any], confidence_threshold: float = 0.7) -> ParsedEvent:
    extra_fields = set(payload) - EVENT_FIELDS
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

    is_event = bool(payload["is_event"])
    if not is_event:
        status = EventStatus.NOT_EVENT
    elif confidence < confidence_threshold:
        status = EventStatus.NEEDS_REVIEW
    else:
        status = EventStatus.PENDING

    return ParsedEvent(
        is_event=is_event,
        title=_nullable_text(payload["title"]),
        date=_nullable_text(payload["date"]),
        end_date=_nullable_text(payload["end_date"]),
        time=_nullable_text(payload["time"]),
        end_time=_nullable_text(payload["end_time"]),
        place=_nullable_text(payload["place"]),
        address=_nullable_text(payload["address"]),
        category=category,
        price=_nullable_text(payload["price"]),
        confidence=confidence,
        reason=str(payload["reason"]).strip(),
        status=status,
    )


def validate_event_response(
    payload: dict[str, Any],
    confidence_threshold: float = 0.7,
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
        validate_event(event_payload, confidence_threshold=confidence_threshold)
        for event_payload in payload["events"]
    ]
    return ParsedEventResponse(
        events=events,
        post_reason=str(payload["post_reason"]).strip(),
    )


def _nullable_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
