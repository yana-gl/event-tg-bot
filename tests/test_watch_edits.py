from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_deduplication import post
from tg_event.cli import _watch_loop, parse_rows
from tg_event.database import connect_database, fetch_event_detail, init_database, save_parsed_row
from tg_event.event_schema import validate_event
from tg_event.storage import write_jsonl


class WatchEditsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cleanup = patch("tg_event.cli.run_cleanup", return_value={"skipped": True})
        cleanup.start()
        self.addCleanup(cleanup.stop)

    async def test_old_edited_post_is_processed_once_without_resetting_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = SimpleNamespace(
                database_path=root / "test.sqlite3", raw_dir=root / "raw", parsed_dir=root / "parsed",
                source_rules_path=root / "rules.json", openrouter_api_key="test", openrouter_model="test",
                openrouter_fallback_model="test",
            )
            original = post(1)
            original["published_at"] = "2026-07-01T10:00:00+00:00"
            with connect_database(settings.database_path) as db:
                init_database(db)
                db.execute("INSERT INTO channel_settings (username,mode) VALUES ('events_vrn','legacy')")
                save_parsed_row(db, original)
                event_id = db.execute("SELECT id FROM events").fetchone()[0]
            raw = dict(source=original["source"], message_id=1, published_at=original["published_at"],
                       edited_at="2026-07-03T10:00:00+00:00", url=original["post_url"],
                       text="Теперь начало в 18:00", city="Воронеж")
            raw_path = root / "raw.jsonl"
            write_jsonl(raw_path, [raw])
            state_path = root / "state.json"
            state_path.write_text('{"events_vrn":100}')
            payload = dict(original["events"][0], time="18:00")
            payload.pop("status")

            def fake_parse(rows, settings, **kwargs):
                return parse_rows(rows, settings, parse_one=lambda **kw: [validate_event(payload)],
                                  progress=lambda message: None)

            with patch("tg_event.cli._collect", AsyncMock(return_value=raw_path)), patch(
                "tg_event.cli.parse_rows", side_effect=fake_parse,
            ) as parse, patch("tg_event.cli.find_repeats", return_value=[event_id]), patch("builtins.print"):
                for _ in range(2):
                    await _watch_loop(settings, interval=1, once=True, since_date="2026-07-03",
                                      state_path=state_path, max_posts_per_cycle=None, max_posts_per_channel=10)

            self.assertEqual(parse.call_count, 1)
            self.assertEqual(json.loads(state_path.read_text()), {"events_vrn": 100})
            with connect_database(settings.database_path) as db:
                detail = fetch_event_detail(db, event_id)
                self.assertEqual(detail["time"], "18:00")
                self.assertEqual(detail["last_change_url"], raw["url"])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
