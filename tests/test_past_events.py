from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_deduplication import post
from tg_event.pipeline import save_to_database
from tg_event.database import connect_database, fetch_known_posts, init_database, init_watch_progress, save_parsed_row
from tg_event.watcher import filter_new_posts


class PastEventsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.settings = SimpleNamespace(database_path=root / "db.sqlite3", source_rules_path=root / "rules.json",
            event_timezone="Asia/Vladivostok", openrouter_api_key="test", openrouter_fallback_model="test")
        clock = patch("tg_event.pipeline.today_in_city", return_value=date(2026, 10, 26))
        self.clock = clock.start()
        self.addCleanup(clock.stop)

    def save(self, rows, matches=None, **kwargs):
        count = sum(len(r["events"]) for r in rows)
        with patch("tg_event.pipeline.find_repeats", return_value=matches or [None] * count):
            return save_to_database(self.settings, rows, **kwargs)

    def events(self):
        with connect_database(self.settings.database_path) as db:
            return [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id")]

    def test_digest_keeps_today_future_and_ongoing_but_not_finished_events(self):
        row = post(1, title="Yesterday", date="2026-10-25")
        base = row["events"][0]
        row["events"] += [dict(base, title="Today", date="2026-10-26"),
                          dict(base, title="Future", date="2026-10-27"),
                          dict(base, title="Ongoing", end_date="2026-10-26"),
                          dict(base, title="Finished range", date="2026-10-20", end_date="2026-10-25")]
        self.save([row])
        self.assertEqual({e["title"] for e in self.events()}, {"Today", "Future", "Ongoing"})
        self.clock.assert_called_once_with("Asia/Vladivostok")

    def test_undated_draft_is_not_mistaken_for_finished_event(self):
        row = post(1, date=None)
        row["events"][0]["status"] = "draft"
        self.save([row])
        self.assertEqual(self.events()[0]["status"], "draft")

    def test_old_announcement_and_future_reschedule_in_same_batch_keep_one_future_card(self):
        original = post(1, date="2026-10-25")
        transfer = post(2, action="reschedule", date="2026-10-29", previous_date="2026-10-25")
        transfer["published_at"] = "2026-10-26T01:00:00+00:00"
        self.save([transfer, original], [None, -1])
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["date"], events[0]["status"]), ("2026-10-29", "published"))

    def test_cancellation_updates_existing_past_card_without_deleting_history(self):
        with connect_database(self.settings.database_path) as db:
            init_database(db)
            save_parsed_row(db, post(1, date="2026-10-25"))
        cancellation = post(2, action="cancel", date="2026-10-25")
        cancellation["published_at"] = "2026-10-26T01:00:00+00:00"
        self.save([cancellation], [1])
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.events()[0]["event_state"], "cancelled")

    def test_finished_post_is_recorded_and_cursor_advances_without_reparse(self):
        row = post(1, date="2026-10-25")
        with connect_database(self.settings.database_path) as db:
            init_database(db)
            init_watch_progress(db, "profile", [row["source"]], "2026-10-01", {})
        fetched = dict(row, text=row["raw_text"])
        state = self.save([row], watch_state_key="profile", fetched_rows=[fetched])
        self.assertEqual(state[row["source"]], 1)
        self.assertEqual(self.events(), [])
        with connect_database(self.settings.database_path) as db:
            known = fetch_known_posts(db, [fetched])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_sources").fetchone()[0], 0)
        self.assertEqual(filter_new_posts([fetched], state, "2026-10-01", known_posts=known), [])

    def test_local_midnight_changes_creation_cutoff(self):
        instant = datetime.fromisoformat("2026-10-25T14:00:00+00:00")
        from tg_event.bot import today_in_city
        with patch("tg_event.pipeline.today_in_city", wraps=today_in_city), patch("tg_event.bot.datetime") as clock:
            clock.now.side_effect = lambda tz: instant.astimezone(tz)
            self.save([post(1, date="2026-10-25")])
            self.assertEqual(self.events(), [])
            self.settings.event_timezone = "Europe/Moscow"
            self.save([post(2, date="2026-10-25")])
            self.assertEqual(len(self.events()), 1)
