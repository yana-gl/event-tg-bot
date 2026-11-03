from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tg_event.collector import collect_posts
from tg_event.database import connect_database, init_database


class CollectorEditTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_tracked_posts_outside_recent_window_and_keeps_edit_timestamp(self):
        stamp = datetime(2026, 7, 1, tzinfo=timezone.utc)
        recent = SimpleNamespace(id=100, message="анонс", date=stamp, edit_date=None, entities=[])
        old = SimpleNamespace(id=1, message="перенос", date=stamp, edit_date=stamp, entities=[])

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def start(self, **kwargs):
                pass

            async def iter_messages(self, channel, limit):
                yield recent

        client = Client()
        client.get_messages = AsyncMock(return_value=[old, None])
        settings = SimpleNamespace(session_name="test", telegram_api_id=1, telegram_api_hash="test",
                                   telegram_phone="test", posts_per_channel=20, city="Воронеж")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings.database_path = Path(folder.name) / 'db.sqlite3'
        with connect_database(settings.database_path) as db:
            init_database(db)
        with patch.dict("sys.modules", {"telethon": SimpleNamespace(TelegramClient=lambda *a: client)}):
            rows = await collect_posts(settings, tracked_posts={"events": [100, 1, 2]}, managed_channels={"events": {"mode": "legacy"}})

        client.get_messages.assert_awaited_once_with("events", ids=[1, 2])
        self.assertEqual([p.message_id for p in rows], [100, 1])
        self.assertEqual(rows[1].edited_at, stamp.isoformat())
