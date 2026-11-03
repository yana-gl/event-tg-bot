from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from test_deduplication import post
from tg_event.database import fetch_event_detail, init_database, save_parsed_row, save_parsed_rows
from tg_event.source_policy import load_source_rules, select_primary_source


class SourcePolicyTest(unittest.TestCase):
    def event(self):
        fields = dict(title="Концерт", date="2026-07-12", time="19:00",
                      place="Кофейня Зёрна", price=None, address=None)
        return dict(fields, raw_post_id=1, sources=[
            dict(raw_post_id=1, source="guide", published_at="2026-07-01",
                 extracted_event=dict(fields, price="500р", address="Ленина, 1")),
            dict(raw_post_id=2, source="venue", published_at="2026-07-02", extracted_event=fields),
            dict(raw_post_id=3, source="organizer", published_at="2026-07-03",
                 extracted_event=dict(fields, organizer="Джаз Клуб")),
        ])

    def test_organizer_then_venue_then_guide(self):
        event = self.event()
        rules = {"venue": {"venues": ["Кофейня «Зерна»"]},
                 "organizer": {"organizers": ["Джаз Клуб"]}}
        self.assertEqual(select_primary_source(event, rules), 3)
        event["sources"].pop()
        self.assertEqual(select_primary_source(event, rules), 2)
        self.assertEqual(select_primary_source(event, {}), 1)

    def test_venue_promoting_another_place_has_no_priority(self):
        event = self.event()
        event["sources"] = event["sources"][:2]
        self.assertEqual(select_primary_source(event, {"venue": {"venues": ["Другая площадка"]}}), 1)

    def test_responsible_source_with_conflicting_time_does_not_replace_link(self):
        event = self.event()
        event["sources"][2]["extracted_event"]["time"] = "20:00"
        rules = {"organizer": {"organizers": ["Джаз Клуб"]}}
        self.assertEqual(select_primary_source(event, rules), 1)

    def test_rule_file_validation_and_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rules.json"
            self.assertEqual(load_source_rules(path), {})
            path.write_text(json.dumps({"@Venue": {"venues": ["Кофейня"]}}))
            self.assertIn("venue", load_source_rules(path))
            path.write_text('{"venue": {"venues": "Кофейня"}}')
            with self.assertRaises(ValueError):
                load_source_rules(path)


class SourceSelectionIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        init_database(self.db)

    def tearDown(self):
        self.db.close()

    def test_primary_link_changes_but_reparse_keeps_card_id(self):
        save_parsed_row(self.db, post(1))
        event_id = self.db.execute("SELECT id FROM events").fetchone()[0]
        new = post(2, organizer="Джаз Клуб", price="500р")
        new["source"] = "organizer"
        new["post_url"] = "https://t.me/organizer/2"
        new["events"][0]["_match_id"] = event_id
        new["events"][0]["_batch_id"] = -1
        save_parsed_rows(self.db, [new], source_rules={"organizer": {"organizers": ["Джаз Клуб"]}})

        self.assertEqual(fetch_event_detail(self.db, event_id)["url"], new["post_url"])
        save_parsed_row(self.db, post(1))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertEqual(len(fetch_event_detail(self.db, event_id)["sources"]), 2)
