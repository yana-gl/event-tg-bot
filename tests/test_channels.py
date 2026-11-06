from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_admin import default_settings
from test_watch_recovery import FeedClient
from tg_event.admin import make_handler
from tg_event.auth import issue_token
from tg_event.channels import add_channel, list_channels, normalize_channel, update_channel
from tg_event.watcher import watch_loop
from tg_event.pipeline import parse_rows
from tg_event.database import connect_database, init_database


class ChannelRegistryTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        init_database(self.db)
        self.addCleanup(self.db.close)

    def test_names_are_canonical_and_duplicate_forms_are_rejected(self):
        for raw in ("Name", "@NAME", "https://t.me/Name/", "t.me/name"):
            self.assertEqual(normalize_channel(raw), "name")
        add_channel(self.db, "@Name", 5)
        with self.assertRaises(ValueError):
            add_channel(self.db, "https://t.me/NAME", 5)
        self.assertEqual(len(list_channels(self.db)), 1)
        for raw in ("", "https://evil.test/name", "https://t.me/+private", "t.me/name/123", None):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_channel(raw)

    def test_count_limits_and_pending_settings(self):
        for count in (0, -1, 1001, True, 2.5, "20"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                add_channel(self.db, "name", count)
        add_channel(self.db, "name", 1)
        update_channel(self.db, "name", {"initial_posts": 1000, "enabled": False})
        row = list_channels(self.db)[0]
        self.assertEqual((row["initial_posts"], row["enabled"], row["initial_status"]), (1000, False, "pending"))
        self.assertFalse(update_channel(self.db, "missing", {"enabled": False}))
        with self.assertRaises(ValueError):
            update_channel(self.db, "name", {"enabled": 0})

    def test_existing_registry_and_progress_are_preserved(self):
        self.db.execute("INSERT INTO channel_settings (username,mode,enabled) VALUES ('name','legacy',0)")
        self.db.execute("INSERT INTO watch_progress VALUES ('profile','name',123,'2026-07-01')")
        self.db.execute("INSERT INTO maintenance_state VALUES ('channel_import','2026-01-01')")
        self.db.commit()
        init_database(self.db)
        row = list_channels(self.db)[0]
        self.assertEqual((row["username"], row["enabled"], row["last_message_id"]), ("name", False, 123))
        with self.assertRaises(ValueError):
            update_channel(self.db, "name", {"initial_posts": 99})

    def request(self, path, body=None, *, auth=True):
        settings = default_settings(admin_secret="offline")
        handler_type = make_handler(settings, self.db, settings.admin_static_dir)
        handler = handler_type.__new__(handler_type)
        payload = json.dumps(body).encode() if body is not None else b""
        handler.path, handler.request_version, handler.requestline = path, "HTTP/1.1", path
        handler.command = "POST" if body is not None else "GET"
        handler.headers = {"Content-Length": str(len(payload))}
        if auth:
            handler.headers["Authorization"] = "Bearer " + issue_token("admin", "offline")
        handler.rfile, handler.wfile = io.BytesIO(payload), io.BytesIO()
        (handler.do_POST if body is not None else handler.do_GET)()
        headers, content = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        return int(headers.split()[1]), json.loads(content)

    def test_offline_api_auth_add_edit_validation_and_list(self):
        self.assertEqual(self.request("/api/channels", auth=False)[0], 401)
        self.assertEqual(self.request("/api/channels", {"username": "name"}, auth=False)[0], 401)
        self.assertEqual(self.request("/api/channels", {"username": "@Name", "initial_posts": 5})[0], 200)
        self.assertEqual(self.request("/api/channels", {"username": "t.me/name"})[0], 400)
        self.assertEqual(self.request("/api/channels/name", {"enabled": False})[0], 200)
        self.assertEqual(self.request("/api/channels/name", {"initial_posts": 0})[0], 400)
        self.assertEqual(self.request("/api/channels/name", {"initial_posts": True})[0], 400)
        self.assertEqual(self.request("/api/channels/missing", {"enabled": False})[0], 404)
        self.assertEqual(self.request("/api/channels", ["invalid"])[0], 400)
        status, data = self.request("/api/channels")
        self.assertEqual(status, 200)
        self.assertEqual([(r["username"], r["enabled"]) for r in data["items"]], [("name", False)])

    def test_api_ignores_legacy_environment_channel_list(self):
        with patch.dict(os.environ, {"CHANNELS": "must_not_import"}):
            self.assertEqual(self.request("/api/channels")[1]["items"], [])
            self.assertEqual(self.request("/api/channels", {"username": "manual"})[0], 200)
            self.assertEqual([r["username"] for r in self.request("/api/channels")[1]["items"]], ["manual"])
        self.assertIsNone(self.db.execute("SELECT 1 FROM maintenance_state WHERE name='channel_import'").fetchone())


class BootstrapClient(FeedClient):
    def __init__(self):
        super().__init__()
        self.snapshots = 0
        self.fail_pages = False
        self.after_snapshot = None

    async def iter_messages(self, channel, **options):
        if options.get("reverse") and self.fail_pages:
            raise OSError("must not expose token=test-secret")
        async for message in super().iter_messages(channel, **options):
            yield message
        if not options.get("reverse"):
            self.snapshots += 1
            if self.after_snapshot:
                self.after_snapshot()


class ManagedWatchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = SimpleNamespace(database_path=self.root / "db.sqlite3", raw_dir=self.root / "raw",
            parsed_dir=self.root / "parsed", source_rules_path=self.root / "rules.json",
            posts_per_channel=2, session_name="test", telegram_api_id=1, telegram_api_hash="test",
            telegram_phone="test", city="Владивосток", openrouter_api_key="test", openrouter_model="test",
            openrouter_fallback_model="test")
        self.client = BootstrapClient()
        self.attempts = []
        self.fail_posts = set()
        for target in (
            patch.dict("sys.modules", {"telethon": SimpleNamespace(TelegramClient=lambda *a: self.client)}),
            patch("tg_event.watcher.parse_rows", side_effect=self.parse),
            patch("tg_event.watcher.run_cleanup", return_value={"skipped": True}),
            patch("builtins.print"),
        ):
            target.start()
            self.addCleanup(target.stop)
        with connect_database(self.settings.database_path) as db:
            init_database(db)

    def parse(self, rows, settings, **kwargs):
        def parse_one(**fields):
            message_id = int(fields["text"])
            self.attempts.append((fields["source"], message_id))
            if message_id in self.fail_posts:
                raise ValueError("bad response")
            return []
        return parse_rows(rows, settings, parse_one=parse_one, max_workers=1, **kwargs)

    def feed(self, count, channel="name"):
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        self.client.feed[channel] = [SimpleNamespace(id=i, message=str(i), date=start + timedelta(minutes=i),
                                                    edit_date=None, entities=[]) for i in range(1, count + 1)]

    def add(self, channel="name", count=3):
        with connect_database(self.settings.database_path) as db:
            add_channel(db, channel, count)

    def update(self, **fields):
        with connect_database(self.settings.database_path) as db:
            update_channel(db, "name", fields)

    def channels(self):
        with connect_database(self.settings.database_path) as db:
            return list_channels(db)

    async def cycle(self, quota=1):
        await watch_loop(self.settings, interval=1, once=True, since_date="2026-08-01",
                          state_path=self.root / "state.json", max_posts_per_cycle=None, max_posts_per_channel=quota)

    async def test_initial_load_drains_without_waiting_between_batches(self):
        self.feed(5)
        self.add(count=5)
        with patch("tg_event.watcher.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError())) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await watch_loop(self.settings, interval=1800, once=False, since_date="2026-08-01",
                                  state_path=self.root / "state.json", max_posts_per_cycle=None,
                                  max_posts_per_channel=2)
        self.assertEqual(self.attempts, [("name", i) for i in range(1, 6)])
        self.assertEqual(self.channels()[0]["initial_status"], "complete")
        sleep.assert_awaited_once_with(1800)

    async def test_initial_load_waits_after_failure_or_no_progress(self):
        self.feed(5)
        self.add(count=5)
        self.fail_posts.add(1)
        with patch("tg_event.watcher.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError())) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await watch_loop(self.settings, interval=1800, once=False, since_date="2026-08-01",
                                  state_path=self.root / "state.json", max_posts_per_cycle=None,
                                  max_posts_per_channel=2)
        self.assertEqual(self.attempts, [("name", 1), ("name", 2)])
        self.assertEqual(self.channels()[0]["initial_status"], "loading")
        sleep.assert_awaited_once_with(1800)
        self.fail_posts.clear()
        with patch("tg_event.watcher.watch_cycle", AsyncMock(return_value=False)) as cycle, patch(
            "tg_event.watcher.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError()),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await watch_loop(self.settings, interval=1800, once=False, since_date="2026-08-01",
                                  state_path=self.root / "state.json", max_posts_per_cycle=None,
                                  max_posts_per_channel=2)
        cycle.assert_awaited_once()

    async def test_initial_window_is_frozen_and_new_posts_during_bootstrap_are_not_lost(self):
        self.feed(5)
        self.add()
        self.client.after_snapshot = lambda: self.feed(6)
        for _ in range(4):
            await self.cycle()
        self.assertEqual(self.attempts, [("name", i) for i in (3, 4, 5, 6)])
        self.assertEqual(self.client.snapshots, 1)
        row = self.channels()[0]
        self.assertEqual((row["initial_start_id"], row["initial_end_id"], row["initial_status"]), (2, 5, "complete"))
        self.assertIsNotNone(row["last_success_at"])
        self.assertIsNone(row["last_error"])

    async def test_collection_failure_after_snapshot_resumes_same_window_without_exposing_error(self):
        self.feed(5)
        self.add()
        self.client.fail_pages = True
        with self.assertRaises(RuntimeError):
            await self.cycle()
        row = self.channels()[0]
        self.assertEqual(row["initial_start_id"], 2)
        self.assertNotIn("test-secret", row["last_error"])
        self.client.fail_pages = False
        self.feed(6)
        for _ in range(4):
            await self.cycle()
        self.assertEqual(self.attempts, [("name", i) for i in (3, 4, 5, 6)])
        self.assertEqual(self.client.snapshots, 1)

    async def test_parse_failure_retains_cursor_and_does_not_reparse_successful_rows(self):
        self.feed(5)
        self.add()
        self.fail_posts.add(3)
        with self.assertRaises(RuntimeError):
            await self.cycle(quota=2)
        self.assertEqual(self.channels()[0]["last_message_id"], 2)
        self.assertIn("разобрать", self.channels()[0]["last_error"])
        self.fail_posts.clear()
        await self.cycle(quota=2)
        await self.cycle(quota=2)
        self.assertEqual(self.attempts, [("name", 3), ("name", 4), ("name", 3), ("name", 5)])
        self.assertEqual(self.channels()[0]["initial_status"], "complete")

    async def test_pause_resume_and_new_channel_are_seen_on_next_cycle(self):
        self.feed(5)
        self.add()
        await self.cycle()
        self.update(enabled=False)
        options_before = len(self.client.options)
        self.feed(8)
        await self.cycle()
        self.assertEqual(len(self.client.options), options_before)
        self.update(enabled=True)
        self.feed(1, "second")
        self.add("second", 5)
        for _ in range(5):
            await self.cycle(quota=2)
        self.assertEqual([i for source, i in self.attempts if source == "name"], list(range(3, 9)))
        self.assertIn(("second", 1), self.attempts)
        self.assertEqual(self.client.snapshots, 2)

    async def test_count_is_editable_before_bootstrap_and_locked_after(self):
        self.feed(5)
        self.add()
        self.update(initial_posts=2)
        await self.cycle()
        with self.assertRaises(ValueError):
            self.update(initial_posts=3)
        await self.cycle()
        self.assertEqual(self.attempts, [("name", 4), ("name", 5)])

    async def test_count_change_during_snapshot_retries_new_setting_without_wrong_anchor(self):
        self.feed(5)
        self.add()
        self.client.after_snapshot = lambda: self.update(initial_posts=4)
        with self.assertRaises(RuntimeError):
            await self.cycle()
        self.assertIsNone(self.channels()[0]["initial_start_id"])
        self.client.after_snapshot = None
        for _ in range(4):
            await self.cycle()
        self.assertEqual(self.attempts, [("name", i) for i in (2, 3, 4, 5)])

    async def test_failed_database_save_keeps_initial_window_for_retry(self):
        self.feed(5)
        self.add()
        with patch("tg_event.watcher.save_to_database", side_effect=sqlite3.OperationalError("failed")):
            with self.assertRaises(sqlite3.OperationalError):
                await self.cycle()
        self.assertEqual(self.channels()[0]["initial_start_id"], 2)
        self.assertIn("сохранить", self.channels()[0]["last_error"])
        self.feed(6)
        for _ in range(4):
            await self.cycle()
        self.assertEqual(self.attempts, [("name", i) for i in (3, 3, 4, 5, 6)])
        self.assertEqual(self.client.snapshots, 1)

    async def test_no_active_channels_does_not_open_telegram(self):
        with patch.dict(os.environ, {"CHANNELS": "must_not_import"}), \
                patch.dict("sys.modules", {"telethon": SimpleNamespace(TelegramClient=lambda *a: self.fail("Telegram opened"))}):
            await self.cycle()
        self.assertEqual(self.channels(), [])

    async def test_connection_failure_records_safe_error_and_keeps_pending_state(self):
        self.add()
        self.client.start = AsyncMock(side_effect=OSError("do not expose api_key=secret"))
        with self.assertRaises(OSError):
            await self.cycle()
        row = self.channels()[0]
        self.assertIsNotNone(row["last_attempt_at"])
        self.assertNotIn("api_key", row["last_error"])
        self.assertIsNone(row["initial_start_id"])

    async def test_empty_and_short_channel_complete_without_repeated_snapshot(self):
        self.add(count=10)
        await self.cycle()
        self.assertEqual(self.channels()[0]["initial_status"], "complete")
        self.feed(2)
        await self.cycle(quota=10)
        self.assertEqual(self.attempts, [("name", 1), ("name", 2)])
        self.assertEqual(self.client.snapshots, 1)

    async def test_unknown_channel_error_does_not_prevent_other_channel(self):
        self.add("broken", 3)
        self.add("name", 3)
        self.feed(2)
        self.client.failed_channels.add("broken")
        with self.assertRaises(RuntimeError):
            await self.cycle(quota=10)
        statuses = {row["username"]: row for row in self.channels()}
        self.assertIsNotNone(statuses["broken"]["last_error"])
        self.assertIsNone(statuses["name"]["last_error"])
        self.assertEqual(self.attempts, [("name", 1), ("name", 2)])
