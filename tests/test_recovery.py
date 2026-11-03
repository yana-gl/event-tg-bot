from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_deduplication import post
from tg_event.cli import parse_rows
from tg_event.database import init_database, save_parsed_row, save_parsed_rows
from tg_event.event_schema import validate_event
from tg_event.watcher import save_state


class RecoveryTest(unittest.TestCase):
    def test_failed_batch_rolls_back_all_new_posts_and_events(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        init_database(db)
        save_parsed_row(db, post(1))
        broken = post(3)
        broken["events"][0]["confidence"] = "broken"

        with self.assertRaises(ValueError):
            save_parsed_rows(db, [post(2), broken])

        self.assertEqual(db.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_parse_failure_does_not_discard_successful_posts(self):
        rows = [dict(source="events", message_id=i, published_at="2026-07-01T10:00:00+00:00",
                     text=str(i), city="Воронеж", url=f"https://t.me/events/{i}") for i in (1, 2, 3)]
        settings = SimpleNamespace(openrouter_api_key="test", openrouter_model="test", openrouter_fallback_model="test")
        payload = dict(post(1)["events"][0])
        payload.pop("status")

        def parse_one(**kwargs):
            if kwargs["text"] == "2":
                raise ValueError("invalid response")
            return [validate_event(payload)]

        parsed = parse_rows(rows, settings, parse_one=parse_one, progress=lambda m: None, continue_on_error=True)
        self.assertEqual([r["message_id"] for r in parsed], [1, 3])

    def test_empty_post_is_processed_without_llm(self):
        row = dict(source="events", message_id=1, published_at="2026-07-01T10:00:00+00:00",
                   text="", city="Воронеж", url="https://t.me/events/1")
        settings = SimpleNamespace(openrouter_api_key="test", openrouter_model="test", openrouter_fallback_model="test")
        parsed = parse_rows([row], settings, parse_one=lambda **kw: self.fail("empty post sent to LLM"),
                            progress=lambda m: None)
        self.assertEqual(parsed[0]["events"], [])

    def test_failed_state_write_keeps_previous_valid_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_text('{"events": 1}')
            original_write = Path.write_text

            def failing_write(target, *args, **kwargs):
                original_write(target, "{", encoding="utf-8")
                raise OSError("disk error")

            with patch.object(Path, "write_text", autospec=True, side_effect=failing_write):
                with self.assertRaises(OSError):
                    save_state(path, {"events": 2})
            self.assertEqual(json.loads(path.read_text()), {"events": 1})
