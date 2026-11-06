from __future__ import annotations

import sqlite3
from datetime import date
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_deduplication import post
from tg_event.bot import format_events
from tg_event.pipeline import match_events, save_to_database
from tg_event.database import (
    fetch_event_detail, fetch_published_events, init_database,
    save_parsed_row, save_parsed_rows, update_event,
)
from tg_event.event_schema import validate_event


class EventUpdatesTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        init_database(self.db)
        self.original = post(1)
        self.original["published_at"] = "2026-07-01T10:00:00+00:00"
        save_parsed_row(self.db, self.original)
        self.event_id = self.db.execute("SELECT id FROM events").fetchone()[0]

    def tearDown(self):
        self.db.close()

    def change(self, action, *, message_id=2, published_at="2026-07-02T10:00:00+00:00", **fields):
        row = post(message_id, action=action, **fields)
        row["published_at"] = published_at
        row["events"][0]["_match_id"] = self.event_id
        save_parsed_rows(self.db, [row])
        return fetch_event_detail(self.db, self.event_id)

    def visible(self):
        return fetch_published_events(self.db, "2026-07-01", "2026-08-31")

    def test_cancel_hides_without_deleting_or_changing_publication_status(self):
        event = self.change("cancel")
        self.assertEqual(event["event_state"], "cancelled")
        self.assertEqual(event["status"], "published")
        self.assertEqual(self.visible(), [])
        self.assertEqual(event["last_change_url"], "https://t.me/events_vrn/2")
        self.assertEqual(len(event["sources"]), 2)

    def test_reschedule_then_unknown_date_then_clarification(self):
        event = self.change("reschedule", date="2026-07-15")
        self.assertEqual(event["date"], "2026-07-15")
        self.assertEqual(event["previous_date"], "2026-07-12")
        self.assertEqual(len(self.visible()), 1)
        event = self.change("reschedule", message_id=3, date=None,
                            published_at="2026-07-03T10:00:00+00:00")
        self.assertEqual(event["event_state"], "postponed")
        self.assertEqual(event["previous_date"], "2026-07-15")
        self.assertEqual(self.visible(), [])
        event = self.change("update", message_id=4, date="2026-07-20", time="18:00",
                            changed_fields=["date", "time"],
                            published_at="2026-07-04T10:00:00+00:00")
        self.assertEqual(event["event_state"], "scheduled")
        self.assertEqual(event["date"], "2026-07-20")
        self.assertEqual(len(self.visible()), 1)

    def test_ordinary_repost_cannot_restore_cancelled_or_postponed_event(self):
        self.change("reschedule", date=None)
        self.change("announcement", message_id=3, published_at="2026-07-03T10:00:00+00:00")
        self.assertEqual(self.visible(), [])
        self.assertEqual(fetch_event_detail(self.db, self.event_id)["event_state"], "postponed")

    def test_older_explicit_update_does_not_revert_newer_change(self):
        self.change("reschedule", date="2026-07-15")
        event = self.change("reschedule", message_id=3, date="2026-07-12",
                            published_at="2026-07-01T12:00:00+00:00")
        self.assertEqual(event["date"], "2026-07-15")
        self.assertEqual(event["last_change_url"], "https://t.me/events_vrn/2")

    def test_manual_fields_are_not_overwritten_and_conflict_is_visible(self):
        update_event(self.db, self.event_id, {"time": "20:00"})
        event = self.change("update", time="18:00", changed_fields=["time"])
        self.assertEqual(event["time"], "20:00")
        self.assertEqual(event["conflict_url"], "https://t.me/events_vrn/2")
        text = format_events(self.visible())
        self.assertIn("Есть изменение, уточните у организатора", text)

    def test_untrusted_update_does_not_fill_missing_fields(self):
        row = post(2, action="update", price="500", changed_fields=["price"], _match_id=self.event_id)
        row.update(source="unknown", post_url="https://t.me/unknown/2",
                   published_at="2026-07-02T10:00:00+00:00")
        save_parsed_rows(self.db, [row])

        detail = fetch_event_detail(self.db, self.event_id)
        self.assertIsNone(detail["price"])
        self.assertIsNone(detail["last_change_at"])
        self.assertEqual(detail["conflict_url"], row["post_url"])
        self.assertEqual(len(detail["sources"]), 2)

    def test_old_updates_and_announcements_do_not_restore_cleared_time(self):
        self.change("update", time=None, changed_fields=["time"],
                    published_at="2026-07-03T10:00:00+00:00")
        cleared = fetch_event_detail(self.db, self.event_id)
        self.assertIsNone(cleared["time"])
        for action in ("update", "announcement"):
            with self.subTest(action=action):
                row = post(3, action=action, changed_fields=["time"], _match_id=self.event_id)
                row["published_at"] = "2026-07-02T10:00:00+00:00"
                save_parsed_rows(self.db, [row])
                detail = fetch_event_detail(self.db, self.event_id)
                self.assertIsNone(detail["time"])
                self.assertEqual(detail["last_change_at"], cleared["last_change_at"])
        save_parsed_rows(self.db, [self.original])
        self.assertIsNone(fetch_event_detail(self.db, self.event_id)["time"])
        self.change("update", message_id=4, time="19:00", changed_fields=["time"],
                    published_at="2026-07-04T10:00:00+00:00")
        self.assertEqual(fetch_event_detail(self.db, self.event_id)["time"], "19:00")

    def test_ordinary_announcement_still_fills_missing_fields(self):
        row = post(2, price="500", _match_id=self.event_id)
        row.update(source="unknown", post_url="https://t.me/unknown/2",
                   published_at="2026-07-02T10:00:00+00:00")
        save_parsed_rows(self.db, [row])
        detail = fetch_event_detail(self.db, self.event_id)
        self.assertEqual(detail["price"], "500")
        self.assertIsNone(detail["conflict_at"])
        self.assertIsNone(detail["last_change_at"])

    def test_rejected_event_is_not_published_after_clarification(self):
        update_event(self.db, self.event_id, {"status": "rejected"})
        self.change("reschedule", date=None)
        event = self.change("update", message_id=3, date="2026-07-15", changed_fields=["date"],
                            published_at="2026-07-03T10:00:00+00:00")
        self.assertEqual(event["status"], "rejected")
        self.assertEqual(self.visible(), [])

    def test_unknown_channel_update_is_conflict_not_automatic_cancellation(self):
        row = post(2, action="cancel")
        row.update(source="other_channel", published_at="2026-07-02T10:00:00+00:00",
                   post_url="https://t.me/other_channel/2")
        row["events"][0]["_match_id"] = self.event_id
        save_parsed_rows(self.db, [row])
        self.assertEqual(len(self.visible()), 1)
        self.assertEqual(fetch_event_detail(self.db, self.event_id)["conflict_url"], row["post_url"])

    def test_uncertain_cancel_does_not_change_existing_card_or_publish_new_one(self):
        row = post(2, action="cancel")
        save_parsed_rows(self.db, [row])
        self.assertEqual(len(self.visible()), 1)
        self.assertEqual(fetch_event_detail(self.db, self.event_id)["event_state"], "scheduled")

    def test_only_target_event_in_digest_is_cancelled(self):
        row = dict(self.original, events=[dict(self.original["events"][0]),
                                         dict(self.original["events"][0], title="Лекция", time="18:00")])
        save_parsed_rows(self.db, [row])
        sibling_id = self.db.execute("SELECT id FROM events WHERE title='Лекция'").fetchone()[0]
        before = dict(self.db.execute("SELECT * FROM events WHERE id=?", (sibling_id,)).fetchone())
        row["edited_at"] = "2026-07-02T10:00:00+00:00"
        row["raw_text"] = "Первое событие отменено, лекция состоится"
        row["events"][0].update(action="cancel", _match_id=self.event_id)
        save_parsed_rows(self.db, [row])

        self.assertEqual([e["title"] for e in self.visible()], ["Лекция"])
        self.assertEqual(dict(self.db.execute("SELECT * FROM events WHERE id=?", (sibling_id,)).fetchone()), before)
        self.assertEqual(fetch_event_detail(self.db, self.event_id)["event_state"], "cancelled")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event_sources").fetchone()[0], 2)

    def test_edit_of_original_updates_only_changed_fields_and_keeps_id(self):
        row = post(1, time="18:00")
        row["published_at"] = self.original["published_at"]
        row["edited_at"] = "2026-07-02T10:00:00+00:00"
        row["events"][0]["_match_id"] = self.event_id
        save_parsed_rows(self.db, [row])
        self.assertEqual(fetch_event_detail(self.db, self.event_id)["time"], "18:00")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_replaying_same_change_does_not_refresh_timestamp(self):
        event = self.change("cancel")
        again = self.change("cancel")
        self.assertEqual(again["last_change_at"], event["last_change_at"])

    def test_update_matching_uses_previous_date_across_date_boundaries(self):
        row = post(2, action="reschedule", previous_date="2026-07-12", date="2026-07-15")
        row["published_at"] = "2026-07-02T10:00:00+00:00"
        settings = SimpleNamespace(openrouter_api_key="test", openrouter_fallback_model="test")
        with patch("tg_event.pipeline.find_repeats", return_value=[self.event_id]) as match:
            match_events(self.db, settings, [row])
        self.assertTrue(match.called)
        self.assertEqual(row["events"][0]["_match_id"], self.event_id)

    def test_schema_accepts_actions_but_rejects_unknown_action_or_fields(self):
        payload = dict(self.original["events"][0])
        payload.pop("status")
        self.assertEqual(validate_event(dict(payload, action="cancel")).action, "cancel")
        for changes in ({"action": "delete"}, {"action": "update", "changed_fields": ["status"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_event(dict(payload, **changes))

    def test_full_pipeline_repost_of_old_date_does_not_create_visible_duplicate(self):
        self.change("reschedule", date="2026-07-15")
        self.change("reschedule", message_id=3, date="2026-07-20", time="18:00",
                    published_at="2026-07-03T10:00:00+00:00")
        row = post(4)
        row["published_at"] = "2026-07-04T10:00:00+00:00"
        settings = SimpleNamespace(openrouter_api_key="test", openrouter_fallback_model="test")
        with patch("tg_event.pipeline.find_repeats", return_value=[self.event_id]):
            match_events(self.db, settings, [row])
        save_parsed_rows(self.db, [row])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertEqual(self.visible()[0]["date"], "2026-07-20")

    def test_known_organizer_can_update_without_repeating_its_name(self):
        row = post(1, organizer="Клуб")
        row["published_at"] = self.original["published_at"]
        save_parsed_row(self.db, row)
        update = post(2, action="cancel")
        update["published_at"] = "2026-07-02T10:00:00+00:00"
        update["events"][0]["_match_id"] = self.event_id
        save_parsed_rows(self.db, [update], source_rules={"events_vrn": {"organizers": ["Клуб"]}})
        self.assertEqual(self.visible(), [])

    def test_stale_copy_of_edited_post_does_not_replace_saved_version(self):
        changed = post(1, time="18:00")
        changed.update(published_at=self.original["published_at"], edited_at="2026-07-02T10:00:00+00:00")
        changed["events"][0]["_match_id"] = self.event_id
        save_parsed_rows(self.db, [changed])
        save_parsed_row(self.db, self.original)
        self.assertEqual(fetch_event_detail(self.db, self.event_id)["time"], "18:00")
        self.assertEqual(self.db.execute("SELECT edited_at FROM raw_posts WHERE message_id=1").fetchone()[0],
                         changed["edited_at"])

    def test_event_service_data_is_deleted_with_event(self):
        self.change("cancel")
        self.db.execute("DELETE FROM events WHERE id = ?", (self.event_id,))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event_sources").fetchone()[0], 0)
        self.assertIsNone(fetch_event_detail(self.db, self.event_id))
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_repeated_untrusted_updates_do_not_gain_authority(self):
        for message_id in (2, 3):
            row = post(message_id, action="cancel")
            row.update(source="other", post_url=f"https://t.me/other/{message_id}",
                       published_at=f"2026-07-0{message_id}T10:00:00+00:00")
            row["events"][0]["_match_id"] = self.event_id
            save_parsed_rows(self.db, [row])
        self.assertEqual(fetch_event_detail(self.db, self.event_id)["event_state"], "scheduled")
        self.assertEqual(self.visible()[0]["post_url"], self.original["post_url"])

    def test_edit_conflicting_with_manual_field_keeps_a_working_primary_link(self):
        update_event(self.db, self.event_id, {"time": "20:00"})
        row = post(1, time="18:00")
        row.update(published_at=self.original["published_at"], edited_at="2026-07-02T10:00:00+00:00")
        row["events"][0]["_match_id"] = self.event_id
        save_parsed_rows(self.db, [row])
        self.assertEqual(self.visible()[0]["time"], "20:00")
        self.assertEqual(self.visible()[0]["conflict_url"], self.original["post_url"])

    def test_organizer_authority_survives_a_sparse_edit(self):
        row = post(1, organizer="Клуб")
        row["published_at"] = self.original["published_at"]
        save_parsed_row(self.db, row)
        changed = post(1, action="reschedule", date="2026-07-15")
        changed.update(published_at=self.original["published_at"], edited_at="2026-07-02T10:00:00+00:00")
        changed["events"][0]["_match_id"] = self.event_id
        rules = {"events_vrn": {"organizers": ["Клуб"]}, "venue": {"venues": ["Митбоулинг"]}}
        save_parsed_rows(self.db, [changed], source_rules=rules)
        venue = post(3, action="cancel", date="2026-07-15")
        venue.update(source="venue", post_url="https://t.me/venue/3", published_at="2026-07-03T10:00:00+00:00")
        venue["events"][0]["_match_id"] = self.event_id
        save_parsed_rows(self.db, [venue], source_rules=rules)
        self.assertEqual(fetch_event_detail(self.db, self.event_id)["event_state"], "scheduled")
        self.assertEqual(self.visible()[0]["conflict_url"], venue["post_url"])

    def test_organizer_authority_survives_content_only_edit(self):
        original = dict(self.original, events=[dict(self.original["events"][0], organizer="Клуб")])
        rules = {"events_vrn": {"organizers": ["Клуб"]}, "venue": {"venues": ["Митбоулинг"]}}
        save_parsed_rows(self.db, [original], source_rules=rules)
        before = dict(self.db.execute("SELECT * FROM events WHERE id=?", (self.event_id,)).fetchone())
        edited = dict(self.original, edited_at="2026-07-02T10:00:00+00:00", raw_text="Правка только текста")
        save_parsed_rows(self.db, [edited], source_rules=rules)
        after = dict(self.db.execute("SELECT * FROM events WHERE id=?", (self.event_id,)).fetchone())
        self.assertEqual(after["last_change_at"], before["last_change_at"])
        self.assertEqual(after["updated_at"], before["updated_at"])

        venue = post(3, action="cancel", _match_id=self.event_id)
        venue.update(source="venue", post_url="https://t.me/venue/3", published_at="2026-07-03T10:00:00+00:00")
        save_parsed_rows(self.db, [venue], source_rules=rules)
        detail = fetch_event_detail(self.db, self.event_id)
        self.assertEqual(detail["event_state"], "scheduled")
        self.assertEqual(detail["conflict_url"], venue["post_url"])

        own_update = post(4, action="reschedule", date="2026-07-15", _match_id=self.event_id)
        own_update["published_at"] = "2026-07-04T10:00:00+00:00"
        save_parsed_rows(self.db, [own_update], source_rules=rules)
        detail = fetch_event_detail(self.db, self.event_id)
        self.assertEqual(detail["date"], "2026-07-15")
        self.assertEqual(detail["event_state"], "scheduled")
        self.assertIsNone(detail["conflict_at"])
        self.assertEqual(detail["last_change_url"], own_update["post_url"])

    def test_sparse_organizer_keeps_priority_over_linked_venue(self):
        original = dict(self.original, events=[dict(self.original["events"][0], organizer="Клуб")])
        rules = {"events_vrn": {"organizers": ["Клуб"]}, "venue": {"venues": ["Митбоулинг"]}}
        save_parsed_rows(self.db, [original], source_rules=rules)
        venue = post(2, _match_id=self.event_id)
        venue.update(source="venue", post_url="https://t.me/venue/2", published_at="2026-07-01T12:00:00+00:00")
        save_parsed_rows(self.db, [venue], source_rules=rules)
        edited = dict(self.original, edited_at="2026-07-02T10:00:00+00:00", raw_text="Правка только текста")
        save_parsed_rows(self.db, [edited], source_rules=rules)

        detail = fetch_event_detail(self.db, self.event_id)
        self.assertEqual(detail["url"], original["post_url"])
        self.assertIsNone(detail["last_change_at"])
        self.assertIsNone(detail["last_change_post_id"])
        self.assertNotIn("organizer", detail["sources"][0]["extracted_event"])
        venue["message_id"] = 3
        venue["post_url"] = "https://t.me/venue/3"
        venue["published_at"] = "2026-07-03T10:00:00+00:00"
        venue["events"][0]["action"] = "cancel"
        save_parsed_rows(self.db, [venue], source_rules=rules)
        detail = fetch_event_detail(self.db, self.event_id)
        self.assertEqual(detail["event_state"], "scheduled")
        self.assertEqual(detail["conflict_url"], venue["post_url"])

    def test_upgrade_from_legacy_schema_keeps_id_fields_and_status(self):
        legacy = sqlite3.connect(":memory:")
        legacy.row_factory = sqlite3.Row
        self.addCleanup(legacy.close)
        legacy.execute("PRAGMA foreign_keys = ON")
        legacy.executescript("""
            CREATE TABLE sources (id INTEGER PRIMARY KEY, username TEXT UNIQUE, city TEXT);
            CREATE TABLE raw_posts (
                id INTEGER PRIMARY KEY, source_id INTEGER REFERENCES sources(id), message_id INTEGER,
                published_at TEXT, city TEXT, url TEXT, text TEXT, UNIQUE(source_id, message_id)
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY, raw_post_id INTEGER REFERENCES raw_posts(id), is_event INTEGER,
                title TEXT, date TEXT, end_date TEXT, time TEXT, end_time TEXT, place TEXT, address TEXT,
                category TEXT, price TEXT, confidence REAL, reason TEXT, status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO sources VALUES (1, 'channel', 'Воронеж');
            INSERT INTO raw_posts VALUES (1, 1, 10, '2026-07-01T10:00:00+00:00', 'Воронеж', 'https://t.me/channel/10', 'text');
            INSERT INTO events (id,raw_post_id,is_event,title,date,confidence,reason,status)
                VALUES (77,1,1,'Ручное название','2026-07-12',0.9,'reason','published');
        """)
        init_database(legacy)
        init_database(legacy)
        event = fetch_event_detail(legacy, 77)
        self.assertEqual(event["title"], "Ручное название")
        self.assertEqual(event["status"], "published")
        self.assertEqual(event["event_state"], "scheduled")
        self.assertIsNone(event["last_change_at"])
        self.assertEqual(legacy.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_postponement_hides_event_even_if_manual_date_is_preserved(self):
        update_event(self.db, self.event_id, {"date": "2026-07-13"})
        event = self.change("reschedule", date=None)
        self.assertEqual(event["event_state"], "postponed")
        self.assertEqual(event["date"], "2026-07-13")
        self.assertEqual(event["previous_date"], "2026-07-13")
        self.assertEqual(self.visible(), [])

    def test_newest_first_file_is_applied_chronologically(self):
        original = post(10)
        original["published_at"] = "2026-07-01T10:00:00+00:00"
        transfer = post(11, action="reschedule", date="2026-07-15", previous_date="2026-07-12", time="18:00")
        transfer["published_at"] = "2026-07-02T10:00:00+00:00"
        with tempfile.TemporaryDirectory() as folder:
            settings = SimpleNamespace(database_path=Path(folder) / "db.sqlite3",
                                       source_rules_path=Path(folder) / "rules.json",
                                       openrouter_api_key="test", openrouter_fallback_model="test")
            with patch("tg_event.pipeline.find_repeats", return_value=[None, -1]), \
                    patch("tg_event.pipeline.today_in_city", return_value=date(2026, 7, 1)):
                save_to_database(settings, [transfer, original])
            connection = sqlite3.connect(settings.database_path)
            connection.row_factory = sqlite3.Row
            try:
                events = connection.execute("SELECT * FROM events").fetchall()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["date"], "2026-07-15")
                self.assertEqual(events[0]["status"], "published")
            finally:
                connection.close()
