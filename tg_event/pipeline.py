from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from tg_event.bot import today_in_city
from tg_event.channels import list_channels, record_channel_check
from tg_event.collector import collect_posts
from tg_event.config import Settings
from tg_event.database import (
    connect_database, fetch_candidate_events, fetch_tracked_posts, init_database, save_parsed_rows,
)
from tg_event.event_updates import change_timestamp
from tg_event.event_schema import ParsedEvent
from tg_event.openrouter_parser import find_repeats, parse_event_with_openrouter
from tg_event.source_policy import load_source_rules
from tg_event.storage import dated_path, read_jsonl, write_jsonl


async def collect_to_file(
    settings: Settings, *, channel_progress: dict[str, tuple[int, str]] | None = None,
    errors: list[str] | None = None,
    managed_channels: dict[str, dict] | None = None, state_key: str | None = None,
) -> Path:
    with closing(connect_database(settings.database_path)) as connection, connection:
        init_database(connection)
        if managed_channels is None:
            managed_channels = {c["username"]: c for c in list_channels(connection) if c["enabled"]}
        tracked = fetch_tracked_posts(connection, (date.today() - timedelta(days=30)).isoformat())
    try:
        posts = await collect_posts(settings, tracked_posts=tracked, channel_progress=channel_progress, errors=errors,
                                    managed_channels=managed_channels, state_key=state_key)
    except Exception:
        with closing(connect_database(settings.database_path)) as connection, connection:
            for channel in managed_channels:
                record_channel_check(connection, channel, error="Не удалось завершить проверку Telegram; будет повторная попытка")
        raise
    path = dated_path(settings.raw_dir, "posts")
    write_jsonl(path, [post.to_dict() for post in posts])
    return path


def parse_file(settings: Settings, raw_path: Path, limit: int | None = None) -> Path:
    rows = read_jsonl(raw_path)
    parsed_rows = parse_rows(rows, settings, limit=limit, progress=print)

    path = dated_path(settings.parsed_dir, "events")
    write_jsonl(path, parsed_rows)
    save_to_database(settings, parsed_rows)
    return path


def parse_rows(
    rows: list[dict[str, Any]],
    settings: Settings,
    limit: int | None = None,
    parse_one: Callable[..., list[ParsedEvent]] = parse_event_with_openrouter,
    progress: Callable[[str], None] = print,
    continue_on_error: bool = False,
    max_workers: int = 3,
) -> list[dict[str, Any]]:
    selected_rows = rows[:limit] if limit is not None else rows
    total = len(selected_rows)

    def parse_row(row: dict[str, Any]) -> dict[str, Any] | None:
        try:
            events = parse_one(
                api_key=settings.openrouter_api_key,
                model=settings.openrouter_model,
                fallback_model=settings.openrouter_fallback_model,
                text=row["text"],
                published_at=row["published_at"],
                edited_at=row.get("edited_at"),
                source=row["source"],
                city=row["city"],
            ) if row["text"].strip() else []
        except Exception as error:
            if not continue_on_error:
                raise
            progress(f"Parsing failed: {row['source']}#{row['message_id']} ({type(error).__name__})")
            return None
        return (
            {
                "source": row["source"],
                "message_id": row["message_id"],
                "published_at": row["published_at"],
                "edited_at": row.get("edited_at"),
                "city": row["city"],
                "post_url": row["url"],
                "url": row["url"],
                "raw_text": row["text"],
                "links": row.get("links") or extract_links(row["text"]),
                "events": [event.to_dict() for event in events],
            }
        )
    if not selected_rows:
        return []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        remaining = iter(enumerate(selected_rows))
        pending = {}
        results = [None] * total

        def submit_next() -> None:
            item = next(remaining, None)
            if item is None:
                return
            index, row = item
            progress(f"Parsing {index + 1}/{total}: {row['source']}#{row['message_id']}")
            pending[executor.submit(parse_row, row)] = index

        for _ in range(min(max_workers, total)):
            submit_next()
        try:
            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    results[pending.pop(future)] = future.result()
                for _ in completed:
                    submit_next()
        except Exception:
            for future in pending:
                future.cancel()
            raise
    return [row for row in results if row is not None]


def save_to_database(
    settings: Settings, parsed_rows: list[dict[str, Any]], *, watch_state_key: str | None = None,
    fetched_rows: list[dict[str, Any]] | None = None,
) -> dict[str, int] | None:
    rules = load_source_rules(settings.source_rules_path)
    ordered_rows = sorted(parsed_rows, key=lambda row: (change_timestamp(row), row["source"], int(row["message_id"])))
    with closing(connect_database(settings.database_path)) as connection, connection:
        init_database(connection)
        match_events(connection, settings, ordered_rows)
        return save_parsed_rows(connection, ordered_rows, source_rules=rules,
                                watch_state_key=watch_state_key, fetched_rows=fetched_rows,
                                not_before=today_in_city(getattr(settings, "event_timezone", "Asia/Vladivostok")).isoformat())


def match_events(connection, settings: Settings, parsed_rows) -> None:
    flat: list[dict[str, Any]] = []
    for row in parsed_rows:
        previous = connection.execute(
            """
            SELECT p.text, p.edited_at FROM raw_posts p JOIN sources s ON s.id = p.source_id
            WHERE s.username = ? AND p.message_id = ?
            """, (row["source"], row["message_id"]),
        ).fetchone()
        is_edit = bool(previous is not None and row.get("edited_at") and (
            row["edited_at"] != previous["edited_at"]
            or (row.get("raw_text") or row.get("text")) != previous["text"]
        ))
        for event in row["events"]:
            if event.get("is_event") and event.get("status") != "not_event":
                event["_is_edit"] = is_edit
                event["_source"] = row["source"]
                event["_message_id"] = row["message_id"]
                event["_timestamp"] = row.get("edited_at") or row["published_at"]
                flat.append(event)
    if not flat:
        return

    dates = sorted({event["date"] for event in flat if event.get("date")})
    has_changes = any(e.get("action", "announcement") != "announcement" or e["_is_edit"] for e in flat)
    since = None
    if has_changes:
        since = (date.fromisoformat(min(e["_timestamp"][:10] for e in flat)) - timedelta(days=30)).isoformat()
    candidates = fetch_candidate_events(connection, dates, updates_since=since)
    for candidate in candidates:
        extracts = connection.execute(
            "SELECT raw_post_id, extracted_json FROM event_sources WHERE event_id = ? AND extracted_json IS NOT NULL",
            (candidate["id"],),
        ).fetchall()
        candidate["announcements"] = []
        for extracted in extracts:
            payload = json.loads(extracted["extracted_json"])
            if payload.get("action", "announcement") == "announcement" or extracted["raw_post_id"] == candidate["last_change_post_id"]:
                candidate["announcements"].append(payload)
    if not has_changes:
        candidates = [c for c in candidates if c["date"] in dates or c["previous_date"] in dates
                      or any(e.get("date") in dates for e in c["announcements"])]
    for index, event in enumerate(flat):
        event.pop("_match_id", None)
        event["_batch_id"] = -index - 1
    if not candidates and len(flat) < 2:
        return

    candidates += [dict(event, id=event["_batch_id"]) for event in flat]

    duplicate_ids = find_repeats(
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_fallback_model,
        new_events=flat,
        existing_events=candidates,
    )
    by_id = {candidate["id"]: candidate for candidate in candidates}
    for event, original_id in zip(flat, duplicate_ids):
        if type(original_id) is not int:
            continue
        original = by_id.get(original_id)
        if original is None or (original_id < 0 and original_id <= event["_batch_id"]):
            continue
        is_change = event.get("action", "announcement") != "announcement" or event["_is_edit"]
        known_dates = {e.get("date") for e in original.get("announcements", [])}
        known_dates.update([original.get("date"), original.get("previous_date")])
        if is_change:
            if event.get("previous_date") and event["previous_date"] not in known_dates:
                continue
            known_times = {e.get("time") for e in original.get("announcements", [])}
            known_times.add(original.get("time"))
            if event.get("previous_time") and event["previous_time"] not in known_times:
                continue
            event["_match_id"] = original_id
            continue
        if not event.get("date") or event["date"] not in known_dates:
            continue
        historical = original.get("last_change_at") and event.get("date") != original.get("date")
        if not historical and event.get("time") and original.get("time") and event["time"] != original["time"]:
            continue
        event["_match_id"] = original_id


def extract_links(text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in re.findall(r"https?://[^\s)>\]}]+", text):
        cleaned = match.rstrip(".,;:!?")
        if cleaned not in seen:
            links.append(cleaned)
            seen.add(cleaned)

    for username in re.findall(r"(?<!\w)@([A-Za-z0-9_]{5,32})", text):
        link = f"https://t.me/{username}"
        if link not in seen:
            links.append(link)
            seen.add(link)
    return links
