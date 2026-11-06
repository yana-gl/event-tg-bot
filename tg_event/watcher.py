from __future__ import annotations

import asyncio
import json
import os
import tempfile
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from tg_event.channels import initialize_recent_channel, list_channels, normalize_channel, record_channel_check
from tg_event.cleanup import run_cleanup
from tg_event.config import Settings
from tg_event.database import connect_database, fetch_known_posts, init_database, init_watch_progress
from tg_event.pipeline import collect_to_file, parse_rows, save_to_database
from tg_event.storage import dated_path, read_jsonl, write_jsonl


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


async def run_watch(
    settings: Settings,
    interval: int,
    once: bool,
    since_date: str,
    state_path: Path,
    max_posts_per_cycle: int | None,
    max_posts_per_channel: int,
) -> None:
    if once:
        await watch_loop(
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

    with closing(connect_database(settings.database_path)) as connection, connection:
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
        watch_loop(
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


async def watch_loop(
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
            initial_positions = {} if once else _initial_load_positions(settings, state_path)
            failed = await watch_cycle(settings, since_date, state_path,
                                        max_posts_per_cycle, max_posts_per_channel)
            if failed and once:
                raise RuntimeError("Some posts or channels failed; successful results were saved")
            if not once and not failed and any(
                position > initial_positions.get(channel, -1)
                for channel, position in _initial_load_positions(settings, state_path).items()
            ):
                continue
        except Exception as error:
            print(f"Watch cycle failed ({type(error).__name__}); progress remains in SQLite", flush=True)
            if once:
                raise
        if once:
            return
        await asyncio.sleep(interval)


def _initial_load_positions(settings: Settings, state_path: Path) -> dict[str, int]:
    with closing(connect_database(settings.database_path)) as connection, connection:
        return dict(connection.execute(
            """SELECT c.username, p.last_message_id
               FROM channel_settings c JOIN watch_progress p ON p.source = c.username
               WHERE p.state_key = ? AND c.enabled = 1 AND c.mode = 'recent'
                 AND p.last_message_id < c.initial_end_id""",
            (str(state_path.resolve()),),
        ))


async def watch_cycle(
    settings: Settings, since_date: str, state_path: Path,
    max_posts_per_cycle: int | None, max_posts_per_channel: int,
) -> bool:
    state_key = str(state_path.resolve())
    with closing(connect_database(settings.database_path)) as connection, connection:
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
    raw_path = await collect_to_file(settings, channel_progress=progress, errors=errors, managed_channels=managed, state_key=state_key)
    rows = await asyncio.to_thread(read_jsonl, raw_path)
    state = {channel: position[0] for channel, position in progress.items()}
    with closing(connect_database(settings.database_path)) as connection, connection:
        known_posts = fetch_known_posts(connection, rows)
    new_rows = filter_new_posts(rows, state, since_date=since_date,
                                max_posts_per_channel=max_posts_per_channel,
                                max_posts_per_cycle=max_posts_per_cycle, known_posts=known_posts)
    parsed_rows = []
    if new_rows:
        write_jsonl(dated_path(settings.raw_dir, "new-posts"), new_rows)
        parsed_rows = await asyncio.to_thread(parse_rows, new_rows, settings, progress=print, continue_on_error=True)
    try:
        saved_state = await asyncio.to_thread(save_to_database, settings, parsed_rows,
                                             watch_state_key=state_key, fetched_rows=rows)
    except Exception:
        with closing(connect_database(settings.database_path)) as connection, connection:
            for channel in {r["source"] for r in new_rows}:
                record_channel_check(connection, channel, error="Не удалось сохранить результаты обработки; будет повторная попытка")
        raise
    failed_posts = {(r["source"], r["message_id"]) for r in new_rows} - {(r["source"], r["message_id"]) for r in parsed_rows}
    if failed_posts:
        with closing(connect_database(settings.database_path)) as connection, connection:
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
