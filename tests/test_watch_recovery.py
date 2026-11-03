from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_deduplication import post
from tg_event.cli import _watch_loop, parse_rows
from tg_event.database import connect_database, init_database, save_event_source, save_parsed_row, update_event
from tg_event.event_schema import validate_event


class FeedClient:
    def __init__(self):
        self.feed = {}
        self.failed_channels = set()
        self.options = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def start(self, **kwargs):
        pass

    async def iter_messages(self, channel, **options):
        self.options.append((channel, options))
        if channel in self.failed_channels:
            raise OSError("unavailable")
        rows = sorted(self.feed.get(channel, []), key=lambda m: m.id, reverse=not options.get("reverse"))
        rows = [m for m in rows if m.id > options.get("min_id", 0)]
        if "offset_date" in options:
            rows = [m for m in rows if m.date > options["offset_date"]]
        for row in rows[:options["limit"]]:
            yield row

    async def get_messages(self, channel, ids):
        by_id = {m.id: m for m in self.feed.get(channel, [])}
        return [by_id.get(i) for i in ids]


class WatchRecoveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = SimpleNamespace(
            database_path=self.root / "db.sqlite3", raw_dir=self.root / "raw", parsed_dir=self.root / "parsed",
            source_rules_path=self.root / "rules.json", openrouter_api_key="test", openrouter_model="test",
            openrouter_fallback_model="test", posts_per_channel=20,
            session_name="test", telegram_api_id=1, telegram_api_hash="test", telegram_phone="test", city="Воронеж",
        )
        self.state_path = self.root / "state.json"
        self.client = FeedClient()
        self.fail_posts = set()
        self.attempts = []
        self.add_patch(patch.dict("sys.modules", {"telethon": SimpleNamespace(TelegramClient=lambda *a: self.client)}))
        self.add_patch(patch("tg_event.cli.parse_rows", side_effect=self.parse))
        self.add_patch(patch("tg_event.cli.find_repeats", side_effect=lambda **kw: [None] * len(kw["new_events"])))
        self.add_patch(patch("builtins.print"))
        self.add_patch(patch("tg_event.cli.run_cleanup", return_value={"skipped": True}))
        self.add_patch(patch("tg_event.cli.today_in_city", return_value=date(2026, 7, 1)))
        with connect_database(self.settings.database_path) as db:
            init_database(db)
            db.execute("INSERT INTO channel_settings (username,mode) VALUES ('events_vrn','legacy')")

    def add_patch(self, target):
        target.start()
        self.addCleanup(target.stop)

    def parse(self, rows, settings, **kwargs):
        return parse_rows(rows, settings, parse_one=self.parse_one, **kwargs)

    def parse_one(self, **kwargs):
        message_id = int(kwargs["text"])
        self.attempts.append((kwargs["source"], message_id))
        if message_id in self.fail_posts:
            raise ValueError("invalid response")
        payload = dict(post(message_id)["events"][0], title=f"Событие {kwargs['source']} {message_id}")
        payload.pop("status")
        return [validate_event(payload)]

    def feed(self, count, channel="events_vrn", *, empty=False):
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        self.client.feed[channel] = [SimpleNamespace(
            id=i, date=start + timedelta(minutes=i), edit_date=None, entities=[], message="" if empty else str(i),
        ) for i in range(1, count + 1)]

    async def run_cycle(self, *, quota=10, since="2026-07-01"):
        await _watch_loop(self.settings, interval=1, once=True, since_date=since, state_path=self.state_path,
                          max_posts_per_cycle=None, max_posts_per_channel=quota)

    def cursor(self, channel="events_vrn"):
        with connect_database(self.settings.database_path) as db:
            row = db.execute("SELECT last_message_id FROM watch_progress WHERE state_key = ? AND source = ?",
                             (str(self.state_path.resolve()), channel)).fetchone()
            return row[0]

    def counts(self):
        with connect_database(self.settings.database_path) as db:
            return (db.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0],
                    db.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    async def test_backlog_larger_than_collection_window_drains_without_loss(self):
        self.feed(45)
        for _ in range(5):
            await self.run_cycle()
        self.assertEqual(self.cursor(), 45)
        self.assertEqual(self.counts(), (45, 45))
        self.assertEqual(len(self.attempts), 45)
        self.assertEqual(self.client.options[0][1]["reverse"], True)
        self.assertEqual(self.client.options[1][1]["min_id"], 10)

    async def test_initial_cutoff_survives_restart_before_first_success(self):
        self.feed(2)
        self.client.failed_channels.add("events_vrn")
        with self.assertRaises(RuntimeError):
            await self.run_cycle()
        self.assertEqual(self.cursor(), 0)
        self.client.failed_channels.clear()
        await self.run_cycle(since="2026-07-03")
        self.assertEqual(self.cursor(), 2)
        self.assertEqual(self.counts(), (2, 2))

    async def test_partial_parse_failure_keeps_gap_and_retries_only_unprocessed_post(self):
        self.feed(3)
        self.fail_posts.add(2)
        with self.assertRaises(RuntimeError):
            await self.run_cycle()
        self.assertEqual(self.cursor(), 1)
        self.assertEqual(self.counts(), (2, 2))
        with connect_database(self.settings.database_path) as db:
            event_id = db.execute("SELECT e.id FROM events e JOIN raw_posts p ON p.id=e.raw_post_id WHERE p.message_id=3").fetchone()[0]
            update_event(db, event_id, {"price": "Ручная правка"})
        self.fail_posts.clear()
        await self.run_cycle()
        self.assertEqual(self.cursor(), 3)
        self.assertEqual(self.counts(), (3, 3))
        self.assertEqual([i for _, i in self.attempts], [1, 2, 3, 2])
        with connect_database(self.settings.database_path) as db:
            self.assertEqual(db.execute("SELECT price FROM events WHERE id=?", (event_id,)).fetchone()[0], "Ручная правка")

    async def test_sql_failure_rolls_back_data_and_cursor_then_retry_is_safe(self):
        self.feed(2)

        def fail_second(db, event_id, post_id, event):
            if event["title"].endswith(" 2"):
                raise sqlite3.OperationalError("write failure")
            return save_event_source(db, event_id, post_id, event)

        with patch("tg_event.database.save_event_source", side_effect=fail_second), self.assertRaises(sqlite3.OperationalError):
            await self.run_cycle()
        self.assertEqual(self.cursor(), 0)
        self.assertEqual(self.counts(), (0, 0))
        await self.run_cycle()
        self.assertEqual(self.cursor(), 2)
        self.assertEqual(self.counts(), (2, 2))

    async def test_json_failure_and_restart_use_sqlite_without_reparsing(self):
        self.feed(2)
        with patch("tg_event.cli.save_state", side_effect=OSError("export failure")), self.assertRaises(OSError):
            await self.run_cycle()
        self.assertEqual(self.cursor(), 2)
        self.state_path.write_text("{")
        await self.run_cycle(since="2026-07-03")
        self.assertEqual(len(self.attempts), 2)
        self.assertEqual(json.loads(self.state_path.read_text()), {"events_vrn": 2})
        self.feed(3)
        await self.run_cycle(since="2026-07-03")
        self.assertEqual(self.cursor(), 3)
        self.assertEqual(self.counts(), (3, 3))

    async def test_saved_later_posts_do_not_jump_over_quota_limited_gap(self):
        self.feed(5)
        with connect_database(self.settings.database_path) as db:
            init_database(db)
            for i in (4, 5):
                row = post(i)
                row.update(published_at=self.client.feed["events_vrn"][i - 1].date.isoformat(), raw_text=str(i))
                save_parsed_row(db, row)
        await self.run_cycle(quota=1)
        self.assertEqual(self.cursor(), 1)
        await self.run_cycle(quota=1)
        self.assertEqual(self.cursor(), 2)
        await self.run_cycle(quota=1)
        self.assertEqual(self.cursor(), 5)
        self.assertEqual([i for _, i in self.attempts], [1, 2, 3])

    async def test_tracked_future_post_outside_page_does_not_advance_cursor(self):
        self.feed(100)
        self.settings.posts_per_channel = 5
        with connect_database(self.settings.database_path) as db:
            init_database(db)
            row = post(100, date="2027-07-12")
            row.update(published_at=self.client.feed["events_vrn"][99].date.isoformat(), raw_text="100")
            save_parsed_row(db, row)
        await self.run_cycle()
        self.assertEqual(self.cursor(), 5)
        self.assertEqual([i for _, i in self.attempts], [1, 2, 3, 4, 5])

    async def test_empty_messages_do_not_stall_cursor_or_call_llm(self):
        self.feed(25, empty=True)
        for _ in range(3):
            await self.run_cycle()
        self.assertEqual(self.cursor(), 25)
        self.assertEqual(self.counts(), (25, 0))
        self.assertEqual(self.attempts, [])

    async def test_failed_channel_does_not_block_other_channels(self):
        with connect_database(self.settings.database_path) as db:
            db.execute("INSERT INTO channel_settings (username,mode) VALUES ('other','legacy')")
        self.feed(2)
        self.feed(2, "other")
        self.client.failed_channels.add("events_vrn")
        with self.assertRaises(RuntimeError):
            await self.run_cycle()
        self.assertEqual(self.cursor(), 0)
        self.assertEqual(self.cursor("other"), 2)
        self.client.failed_channels.clear()
        await self.run_cycle()
        self.assertEqual(self.cursor(), 2)
        self.assertEqual(self.counts(), (4, 4))

    async def test_continuous_watch_survives_failed_cycle(self):
        with patch("tg_event.cli._watch_cycle", AsyncMock(side_effect=[OSError("offline"), False])) as cycle, patch(
            "tg_event.cli.asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()]),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await _watch_loop(self.settings, interval=1, once=False, since_date="2026-07-01",
                                  state_path=self.state_path, max_posts_per_cycle=None, max_posts_per_channel=10)
        self.assertEqual(cycle.await_count, 2)
