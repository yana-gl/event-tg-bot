from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path
from typing import Any

from tg_event.collector import collect_posts
from tg_event.config import Settings
from tg_event.database import (
    connect_database,
    fetch_candidate_events,
    init_database,
    save_parsed_rows,
)
from tg_event.openrouter_parser import find_repeats, parse_event_with_openrouter
from tg_event.storage import dated_path, read_jsonl, write_jsonl
from tg_event.watcher import (
    filter_new_posts,
    load_state,
    save_state,
    today_string,
    update_state,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram event parser POC")
    parser.add_argument(
        "command",
        choices=["collect", "parse", "run", "watch", "serve"],
        help="collect raw posts, parse a raw file, do both, watch for new posts, or serve admin UI",
    )
    parser.add_argument("--input", type=Path, help="Raw JSONL file for parse command")
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
        help="Earliest post date to process, YYYY-MM-DD. Defaults to today.",
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


async def _collect(settings: Settings) -> Path:
    posts = await collect_posts(settings)
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
        raw_path = await _collect(settings)
        rows = await asyncio.to_thread(read_jsonl, raw_path)
        state = load_state(state_path)
        new_rows = filter_new_posts(
            rows,
            state,
            since_date=since_date,
            max_posts_per_channel=max_posts_per_channel,
            max_posts_per_cycle=max_posts_per_cycle,
        )

        if new_rows:
            raw_new_path = dated_path(settings.raw_dir, "new-posts")
            write_jsonl(raw_new_path, new_rows)
            parsed_rows = await asyncio.to_thread(parse_rows, new_rows, settings, progress=print)
            parsed_path = dated_path(settings.parsed_dir, "events")
            write_jsonl(parsed_path, parsed_rows)
            await asyncio.to_thread(_save_to_database, settings, parsed_rows)
            save_state(state_path, update_state(state, new_rows))
            print(f"Parsed {len(new_rows)} new posts: {parsed_path}")
        else:
            print(f"No new posts since {since_date}")

        if once:
            return
        await asyncio.sleep(interval)


def parse_rows(
    rows,
    settings,
    limit: int | None = None,
    parse_one=parse_event_with_openrouter,
    progress=print,
):
    selected_rows = rows[:limit] if limit is not None else rows
    total = len(selected_rows)
    parsed_rows = []
    for index, row in enumerate(selected_rows, start=1):
        progress(f"Parsing {index}/{total}: {row['source']}#{row['message_id']}")
        events = parse_one(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
            fallback_model=settings.openrouter_fallback_model,
            text=row["text"],
            published_at=row["published_at"],
            source=row["source"],
            city=row["city"],
        )
        parsed_rows.append(
            {
                "source": row["source"],
                "message_id": row["message_id"],
                "published_at": row["published_at"],
                "post_url": row["url"],
                "url": row["url"],
                "raw_text": row["text"],
                "links": row.get("links") or extract_links(row["text"]),
                "events": [event.to_dict() for event in events],
            }
        )
    return parsed_rows


def _save_to_database(settings: Settings, parsed_rows) -> None:
    with connect_database(settings.database_path) as connection:
        init_database(connection)
        _mark_repeats(connection, settings, parsed_rows)
        save_parsed_rows(connection, parsed_rows)


def _mark_repeats(connection, settings: Settings, parsed_rows) -> None:
    flat: list[dict[str, Any]] = []
    for row in parsed_rows:
        for event in row["events"]:
            if event.get("is_event") and event.get("status") != "not_event":
                flat.append(event)
    if not flat:
        return

    dates = sorted({event["date"] for event in flat if event.get("date")})
    candidates = fetch_candidate_events(connection, dates)
    if not candidates:
        return

    duplicate_ids = find_repeats(
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_fallback_model,
        new_events=flat,
        existing_events=candidates,
    )
    for event, original_id in zip(flat, duplicate_ids):
        if original_id is not None:
            event["status"] = "repeat"


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
