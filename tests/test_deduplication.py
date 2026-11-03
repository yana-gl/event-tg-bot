from __future__ import annotations

import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tg_event.cli import _mark_repeats
from tg_event.database import (
    fetch_event_detail, fetch_published_events, init_database,
    save_parsed_row, save_parsed_rows, update_event,
)
from test_database import parsed_row


def post(message_id, **changes):
    row = parsed_row()
    row["message_id"] = message_id
    row["post_url"] = f"https://t.me/events_vrn/{message_id}"
    row["events"] = [dict(row["events"][0], status="published", **changes)]
    return row


class DeduplicationTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        init_database(self.db)
        self.settings = SimpleNamespace(openrouter_api_key="test", openrouter_fallback_model="test")

    def tearDown(self):
        self.db.close()

    def process(self, rows, matches):
        with patch("tg_event.cli.find_repeats", return_value=matches):
            _mark_repeats(self.db, self.settings, rows)
        save_parsed_rows(self.db, rows)

    def test_merge_fills_only_empty_fields_and_keeps_id(self):
        save_parsed_row(self.db, post(1))
        event_id = self.db.execute("SELECT id FROM events").fetchone()[0]
        self.process([post(2, price="500р", address="Другой адрес")], [event_id])

        detail = fetch_event_detail(self.db, event_id)
        self.assertEqual(detail["price"], "500р")
        self.assertEqual(detail["address"], "ул. Пушкинская, 11б")
        self.assertEqual(len(detail["sources"]), 2)
        self.assertEqual(len(fetch_published_events(self.db, "2026-07-12", "2026-07-12")), 1)

    def test_batch_duplicates_merge_without_existing_database_candidates(self):
        self.process([post(1), post(2, price="500р")], [None, -1])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event_sources").fetchone()[0], 2)

    def test_different_sessions_and_dates_are_not_merged_even_if_model_matches(self):
        save_parsed_row(self.db, post(1))
        event_id = self.db.execute("SELECT id FROM events").fetchone()[0]
        self.process([post(2, time="18:00"), post(3, date="2026-07-13")], [event_id, event_id])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)

    def test_reparse_preserves_manual_title_empty_price_status_and_id(self):
        row = post(1, price="400р")
        save_parsed_row(self.db, row)
        event_id = self.db.execute("SELECT id FROM events").fetchone()[0]
        update_event(self.db, event_id, {"title": "Ручное название", "price": None,
                                         "status": "rejected"})
        row["events"][0]["price"] = "500р"
        save_parsed_row(self.db, row)

        detail = fetch_event_detail(self.db, event_id)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertEqual(detail["title"], "Ручное название")
        self.assertIsNone(detail["price"])
        self.assertEqual(detail["status"], "rejected")
        self.assertEqual(detail["sources"][0]["extracted_event"]["price"], "500р")

    def test_new_source_does_not_republish_rejected_event(self):
        save_parsed_row(self.db, post(1))
        event_id = self.db.execute("SELECT id FROM events").fetchone()[0]
        update_event(self.db, event_id, {"status": "rejected"})
        self.process([post(2, price="500р")], [event_id])
        self.assertEqual(fetch_event_detail(self.db, event_id)["status"], "rejected")
        self.assertEqual(fetch_published_events(self.db, "2026-07-12", "2026-07-12"), [])

    def test_empty_reparse_does_not_delete_cards(self):
        row = post(1)
        save_parsed_row(self.db, row)
        row["events"] = []
        save_parsed_row(self.db, row)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_unchanged_undated_draft_is_not_duplicated_on_reparse(self):
        row = post(1, date=None)
        row["events"][0]["status"] = "draft"
        save_parsed_row(self.db, row)
        save_parsed_row(self.db, row)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_reordered_digest_uses_extractions_not_array_positions(self):
        row = post(1)
        row["events"].append(dict(row["events"][0], title="Лекция", time="18:00"))
        save_parsed_row(self.db, row)
        ids = [r[0] for r in self.db.execute("SELECT id FROM events ORDER BY id")]
        update_event(self.db, ids[0], {"title": "Ручное название"})
        row["events"].reverse()
        save_parsed_row(self.db, row)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
        self.assertEqual(fetch_event_detail(self.db, ids[0])["title"], "Ручное название")
        self.assertEqual(fetch_event_detail(self.db, ids[1])["title"], "Лекция")

    def test_unknown_and_forward_batch_ids_are_ignored(self):
        self.process([post(1), post(2)], [-2, 999999])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)

    def test_missing_time_does_not_bridge_two_different_sessions(self):
        self.process([post(1, time=None), post(2, time="15:00"), post(3, time="18:00")],
                     [None, -1, -1])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)

    def test_legacy_fields_including_empty_values_are_protected(self):
        save_parsed_row(self.db, post(1))
        event_id = self.db.execute("SELECT id FROM events").fetchone()[0]
        with self.db:
            self.db.execute("UPDATE event_sources SET extracted_json = NULL")
        init_database(self.db)
        self.process([post(2, price="500р")], [event_id])
        self.assertIsNone(fetch_event_detail(self.db, event_id)["price"])

    def test_unchanged_form_fields_do_not_block_later_enrichment(self):
        save_parsed_row(self.db, post(1))
        event_id = self.db.execute("SELECT id FROM events").fetchone()[0]
        update_event(self.db, event_id, {"title": "Ручное название", "price": None})
        self.process([post(2, price="500р")], [event_id])
        self.assertEqual(fetch_event_detail(self.db, event_id)["price"], "500р")
