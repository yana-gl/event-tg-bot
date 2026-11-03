from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from tg_event.collector import collect_posts
from tg_event.config import Settings, load_dotenv
from tg_event.cleanup import run_cleanup
from tg_event.bot import today_in_city
from tg_event.channels import list_channels, initialize_recent_channel, normalize_channel, record_channel_check
from tg_event.database import (
    connect_database,
    fetch_candidate_events,
    fetch_known_posts,
    fetch_tracked_posts,
    init_database,
    init_watch_progress,
    save_parsed_rows,
)
from tg_event.openrouter_parser import find_repeats, parse_event_with_openrouter
from tg_event.event_updates import change_timestamp
from tg_event.storage import dated_path, read_jsonl, write_jsonl
from tg_event.source_policy import load_source_rules
from tg_event.watcher import (
    filter_new_posts,
    load_state,
    save_state,
    today_string,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram event parser POC")
    parser.add_argument(
        "command",
        choices=["collect", "parse", "run", "watch", "serve", "cleanup"],
        help="collect, parse, watch, serve admin UI, or preview/apply data cleanup",
    )
    parser.add_argument("--input", type=Path, help="Raw JSONL file for parse command")
    parser.add_argument("--apply", action="store_true", help="Apply cleanup after backup; default is preview only")
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of raw posts to parse in this run",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=1800,
        help="Seconds between watch cycles",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one watch cycle and exit",
    )
    parser.add_argument(
        "--since",
        default=today_string(),
        help="Initial watch cutoff, YYYY-MM-DD. Saved channel progress resumes across restarts.",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("data/state/channels.json"),
        help="Path to watch state JSON",
    )
    parser.add_argument(
        "--max-posts-per-cycle",
        type=int,
        default=None,
        help="Optional total maximum new posts to parse in one watch cycle",
    )
    parser.add_argument(
        "--max-posts-per-channel",
        type=int,
        default=10,
        help="Maximum new posts to parse per channel in one watch cycle",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Admin server host (default: 127.0.0.1 or ADMIN_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Admin server port (default: 8080 or ADMIN_PORT)",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Serve admin API only (use Vite dev server for the UI)",
    )
    args = parser.parse_args()

    if args.command == "cleanup":
        load_dotenv()
        result = run_cleanup(
            Path(os.getenv("DATABASE_PATH", "data/tg_event.sqlite3")),
            Path(os.getenv("RAW_DIR", "data/raw")), Path(os.getenv("PARSED_DIR", "data/parsed")),
            os.getenv("EVENT_TIMEZONE", "Asia/Vladivostok").strip(), apply=args.apply,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    settings = Settings.from_env()
    if args.command == "collect":
        raw_path = asyncio.run(_collect(settings))
        print(raw_path)
    elif args.command == "parse":
        if not args.input:
            raise SystemExit("--input is required for parse")
        parsed_path = _parse(settings, args.input, limit=args.limit)
        print(parsed_path)
    elif args.command == "run":
        raw_path = asyncio.run(_collect(settings))
        parsed_path = _parse(settings, raw_path, limit=args.limit)
        print(parsed_path)
    elif args.command == "watch":
        asyncio.run(
            _watch(
                settings,
                interval=args.interval,
                once=args.once,
                since_date=args.since,
                state_path=args.state,
                max_posts_per_cycle=args.max_posts_per_cycle,
                max_posts_per_channel=args.max_posts_per_channel,
            )
        )
    elif args.command == "serve":
        _serve(
            settings,
            host=args.host or settings.admin_host,
            port=args.port or settings.admin_port,
            dev=args.dev,
        )


async def _collect(
    settings: Settings, *, channel_progress: dict[str, tuple[int, str]] | None = None,
    errors: list[str] | None = None,
    managed_channels: dict[str, dict] | None = None, state_key: str | None = None,
) -> Path:
    tracked = {}
    with connect_database(settings.database_path) as connection:
        init_database(connection)
        if managed_channels is None:
            managed_channels = {c["username"]: c for c in list_channels(connection) if c["enabled"]}
        tracked = fetch_tracked_posts(connection, (date.today() - timedelta(days=30)).isoformat())
    try:
        posts = await collect_posts(settings, tracked_posts=tracked, channel_progress=channel_progress, errors=errors,
                                    managed_channels=managed_channels, state_key=state_key)
    except Exception:
        with connect_database(settings.database_path) as connection:
            for channel in managed_channels:
                record_channel_check(connection, channel, error="Не удалось завершить проверку Telegram; будет повторная попытка")
        raise
    path = dated_path(settings.raw_dir, "posts")
    write_jsonl(path, [post.to_dict() for post in posts])
    return path


def _parse(settings: Settings, raw_path: Path, limit: int | None = None) -> Path:
    rows = read_jsonl(raw_path)
    parsed_rows = parse_rows(rows, settings, limit=limit, progress=print)

    path = dated_path(settings.parsed_dir, "events")
    write_jsonl(path, parsed_rows)
    _save_to_database(settings, parsed_rows)
    return path


async def _watch(
    settings: Settings,
    interval: int,
    once: bool,
    since_date: str,
    state_path: Path,
    max_posts_per_cycle: int | None,
    max_posts_per_channel: int,
) -> None:
    if once:
        await _watch_loop(
            settings,
            interval=interval,
            once=True,
            since_date=since_date,
            state_path=state_path,
            max_posts_per_cycle=max_posts_per_cycle,
            max_posts_per_channel=max_posts_per_channel,
        )
        return

    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is required for watch")

    with connect_database(settings.database_path) as connection:
        init_database(connection)

    from telethon import TelegramClient

    from tg_event.bot import register_handlers

    bot_client = TelegramClient(
        settings.bot_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await bot_client.start(bot_token=settings.bot_token)
    register_handlers(bot_client, settings)
    print("Bot started", flush=True)

    watcher_task = asyncio.create_task(
        _watch_loop(
            settings,
            interval=interval,
            once=False,
            since_date=since_date,
            state_path=state_path,
            max_posts_per_cycle=max_posts_per_cycle,
            max_posts_per_channel=max_posts_per_channel,
        )
    )
    try:
        await bot_client.run_until_disconnected()
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass


async def _watch_loop(
    settings: Settings,
    interval: int,
    once: bool,
    since_date: str,
    state_path: Path,
    max_posts_per_cycle: int | None,
    max_posts_per_channel: int,
) -> None:
    while True:
        try:
            failed = await _watch_cycle(settings, since_date, state_path,
                                        max_posts_per_cycle, max_posts_per_channel)
            if failed and once:
                raise RuntimeError("Some posts or channels failed; successful results were saved")
        except Exception as error:
            print(f"Watch cycle failed ({type(error).__name__}); progress remains in SQLite", flush=True)
            if once:
                raise
        if once:
            return
        await asyncio.sleep(interval)


async def _watch_cycle(
    settings: Settings, since_date: str, state_path: Path,
    max_posts_per_cycle: int | None, max_posts_per_channel: int,
) -> bool:
    state_key = str(state_path.resolve())
    with connect_database(settings.database_path) as connection:
        init_database(connection)
        managed = {c["username"]: c for c in list_channels(connection) if c["enabled"]}
        exists = connection.execute("SELECT 1 FROM watch_progress WHERE state_key = ? LIMIT 1", (state_key,)).fetchone()
        legacy = {} if exists else {normalize_channel(k): v for k, v in load_state(state_path).items()}
        progress = init_watch_progress(connection, state_key, list(managed), since_date, legacy)
        for channel, config in managed.items():
            if config["mode"] == "recent" and config["initial_start_id"] is not None:
                initialize_recent_channel(connection, channel, state_key, config["initial_posts"], [])
        progress = init_watch_progress(connection, state_key, [], since_date, {})
    errors: list[str] = []
    raw_path = await _collect(settings, channel_progress=progress, errors=errors, managed_channels=managed, state_key=state_key)
    rows = await asyncio.to_thread(read_jsonl, raw_path)
    state = {channel: position[0] for channel, position in progress.items()}
    with connect_database(settings.database_path) as connection:
        known_posts = fetch_known_posts(connection, rows)
    new_rows = filter_new_posts(rows, state, since_date=since_date,
                                max_posts_per_channel=max_posts_per_channel,
                                max_posts_per_cycle=max_posts_per_cycle, known_posts=known_posts)
    parsed_rows = []
    if new_rows:
        write_jsonl(dated_path(settings.raw_dir, "new-posts"), new_rows)
        parsed_rows = await asyncio.to_thread(parse_rows, new_rows, settings, progress=print, continue_on_error=True)
    try:
        saved_state = await asyncio.to_thread(_save_to_database, settings, parsed_rows,
                                             watch_state_key=state_key, fetched_rows=rows)
    except Exception:
        with connect_database(settings.database_path) as connection:
            for channel in {r["source"] for r in new_rows}:
                record_channel_check(connection, channel, error="Не удалось сохранить результаты обработки; будет повторная попытка")
        raise
    failed_posts = {(r["source"], r["message_id"]) for r in new_rows} - {(r["source"], r["message_id"]) for r in parsed_rows}
    if failed_posts:
        with connect_database(settings.database_path) as connection:
            for channel in {source for source, _ in failed_posts}:
                record_channel_check(connection, channel, error="Не удалось разобрать часть постов; будет повторная попытка")
    if parsed_rows:
        parsed_path = dated_path(settings.parsed_dir, "events")
        write_jsonl(parsed_path, parsed_rows)
        print(f"Parsed {len(parsed_rows)}/{len(new_rows)} new or edited posts: {parsed_path}", flush=True)
    else:
        print("No successfully parsed new posts", flush=True)
    save_state(state_path, saved_state)
    failed = bool(errors or len(parsed_rows) < len(new_rows))
    if not failed:
        cleanup = await asyncio.to_thread(
            run_cleanup, settings.database_path, settings.raw_dir, settings.parsed_dir,
            getattr(settings, "event_timezone", "Asia/Vladivostok"), apply=True, weekly=True,
        )
        if not cleanup.get("skipped"):
            print("Cleanup completed: " + ", ".join(f"{k}={len(cleanup[k])}" for k in ("events", "posts", "files")), flush=True)
    return failed


def parse_rows(
    rows,
    settings,
    limit: int | None = None,
    parse_one=parse_event_with_openrouter,
    progress=print,
    continue_on_error: bool = False,
):
    selected_rows = rows[:limit] if limit is not None else rows
    total = len(selected_rows)
    parsed_rows = []
    for index, row in enumerate(selected_rows, start=1):
        progress(f"Parsing {index}/{total}: {row['source']}#{row['message_id']}")
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
            continue
        parsed_rows.append(
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
    return parsed_rows


def _save_to_database(
    settings: Settings, parsed_rows, *, watch_state_key: str | None = None,
    fetched_rows: list[dict[str, Any]] | None = None,
) -> dict[str, int] | None:
    rules = load_source_rules(settings.source_rules_path)
    ordered_rows = sorted(parsed_rows, key=lambda row: (change_timestamp(row), row["source"], int(row["message_id"])))
    with connect_database(settings.database_path) as connection:
        init_database(connection)
        _mark_repeats(connection, settings, ordered_rows)
        return save_parsed_rows(connection, ordered_rows, source_rules=rules,
                                watch_state_key=watch_state_key, fetched_rows=fetched_rows,
                                not_before=today_in_city(getattr(settings, "event_timezone", "Asia/Vladivostok")).isoformat())


def _mark_repeats(connection, settings: Settings, parsed_rows) -> None:
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


def _serve(settings: Settings, host: str, port: int, dev: bool) -> None:
    if not settings.admin_user or not settings.admin_password:
        raise SystemExit("ADMIN_USER and ADMIN_PASSWORD are required for serve")

    from tg_event.admin import serve as serve_admin
    from tg_event.database import init_database

    with connect_database(settings.database_path, check_same_thread=False) as connection:
        init_database(connection)
        if not dev and not (settings.admin_static_dir / "index.html").exists():
            print(
                "warning: admin-ui/dist not found; run `cd admin-ui && npm install && npm run build`",
                flush=True,
            )
        serve_admin(settings, connection, host=host, port=port, dev=dev)


if __name__ == "__main__":
    main()
