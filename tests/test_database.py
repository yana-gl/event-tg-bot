import sqlite3
import json
import unittest

from tg_event.database import (
    fetch_event_detail,
    fetch_published_events,
    init_database,
    save_event_source,
    save_parsed_row,
)


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        init_database(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_save_parsed_row_persists_source_post_links_and_events(self):
        save_parsed_row(self.connection, parsed_row())

        source = self.connection.execute("SELECT username, city FROM sources").fetchone()
        raw_post = self.connection.execute(
            "SELECT message_id, url, text FROM raw_posts"
        ).fetchone()
        links = self.connection.execute("SELECT url FROM post_links").fetchall()
        events = self.connection.execute(
            "SELECT title, date, end_time, category, price, status FROM events"
        ).fetchall()

        self.assertEqual(dict(source), {"username": "events_vrn", "city": "Воронеж"})
        self.assertEqual(raw_post["message_id"], 3178)
        self.assertEqual(raw_post["url"], "https://t.me/events_vrn/3178")
        self.assertIn("raw post text", raw_post["text"])
        self.assertEqual([row["url"] for row in links], ["https://example.com"])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["title"], "Винил-маркет")
        self.assertEqual(events[0]["end_time"], "20:00")
        self.assertEqual(events[0]["category"], "market")
        self.assertEqual(events[1]["status"], "draft")

    def test_changed_extraction_does_not_delete_previous_cards_without_safe_match(self):
        row = parsed_row()
        save_parsed_row(self.connection, row)
        row["events"] = [
            {
                "is_event": True,
                "title": "Обновленное событие",
                "date": "2026-07-13",
                "end_date": None,
                "time": "18:00",
                "end_time": None,
                "place": "Площадка",
                "address": None,
                "category": "other",
                "price": None,
                "confidence": 0.8,
                "reason": "Повторный парсинг.",
                "status": "draft",
            }
        ]

        save_parsed_row(self.connection, row)

        raw_posts_count = self.connection.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0]
        events = self.connection.execute("SELECT title FROM events").fetchall()

        self.assertEqual(raw_posts_count, 1)
        self.assertEqual([row["title"] for row in events],
                         ["Винил-маркет", "Сомнительное событие", "Обновленное событие"])

    def test_new_digest_preserves_each_extraction_separately(self):
        row = parsed_row()
        post_id = save_parsed_row(self.connection, row)

        sources = self.connection.execute(
            "SELECT event_id, raw_post_id, extracted_json FROM event_sources ORDER BY event_id"
        ).fetchall()

        self.assertEqual(len(sources), 2)
        self.assertEqual([r["raw_post_id"] for r in sources], [post_id, post_id])
        self.assertEqual([json.loads(r["extracted_json"]) for r in sources], row["events"])

    def test_one_event_can_have_two_sources_without_duplicate_bot_cards(self):
        row = parsed_row()
        row["events"] = row["events"][:1]
        row["events"][0]["status"] = "published"
        first_post_id = save_parsed_row(self.connection, row)
        event_id = self.connection.execute("SELECT id FROM events").fetchone()[0]
        second = dict(row, source="venue", message_id=42,
                      post_url="https://t.me/venue/42", events=[])
        second_post_id = save_parsed_row(self.connection, second)
        extraction = dict(row["events"][0], price="500р")

        with self.connection:
            save_event_source(self.connection, event_id, second_post_id, extraction)
            save_event_source(self.connection, event_id, second_post_id, extraction)

        detail = fetch_event_detail(self.connection, event_id)
        self.assertEqual([s["raw_post_id"] for s in detail["sources"]],
                         [first_post_id, second_post_id])
        self.assertEqual(detail["sources"][1]["source"], "venue")
        self.assertEqual(detail["sources"][1]["extracted_event"], extraction)
        self.assertIsNone(detail["price"])
        self.assertEqual(detail["url"], row["post_url"])
        events = fetch_published_events(self.connection, "2026-07-12", "2026-07-12")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["post_url"], row["post_url"])

    def test_migration_preserves_legacy_events_and_does_not_guess_extractions(self):
        row = parsed_row()
        first = row["events"][0]
        row["events"] = [dict(first, title=f"Карточка {status}", status=status) for status in
                         ("draft", "published", "rejected", "repeat", "not_event")]
        save_parsed_row(self.connection, row)
        self.connection.execute("UPDATE events SET title = 'Ручная правка', price = NULL")
        self.connection.commit()
        before = [dict(r) for r in self.connection.execute("SELECT * FROM events ORDER BY id")]
        self.connection.execute("DROP TABLE event_sources")

        init_database(self.connection)
        init_database(self.connection)

        after = [dict(r) for r in self.connection.execute("SELECT * FROM events ORDER BY id")]
        self.assertEqual(after, before)
        sources = self.connection.execute("SELECT * FROM event_sources").fetchall()
        self.assertEqual(len(sources), len(before))
        self.assertTrue(all(r["extracted_json"] is None for r in sources))
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_initialization_does_not_overwrite_saved_extractions(self):
        row = parsed_row()
        save_parsed_row(self.connection, row)
        self.connection.execute("UPDATE events SET title = 'Ручное название'")
        self.connection.commit()

        init_database(self.connection)

        source = self.connection.execute(
            "SELECT extracted_json FROM event_sources ORDER BY event_id LIMIT 1"
        ).fetchone()
        self.assertEqual(json.loads(source["extracted_json"]), row["events"][0])

    def test_source_links_follow_event_deletion(self):
        row = parsed_row()
        save_parsed_row(self.connection, row)
        event_id = self.connection.execute("SELECT id FROM events ORDER BY id LIMIT 1").fetchone()[0]

        with self.connection:
            self.connection.execute("DELETE FROM events WHERE id = ?", (event_id,))

        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM event_sources").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0], 1)

    def test_source_link_rejects_missing_event_or_post(self):
        row = parsed_row()
        post_id = save_parsed_row(self.connection, row)
        event_id = self.connection.execute("SELECT id FROM events LIMIT 1").fetchone()[0]

        for ids in ((999999, post_id), (event_id, 999999)):
            with self.subTest(ids=ids):
                with self.assertRaises(sqlite3.IntegrityError), self.connection:
                    save_event_source(self.connection, *ids, row["events"][0])


def parsed_row():
    return {
        "source": "events_vrn",
        "message_id": 3178,
        "published_at": "2026-07-03T09:13:48+00:00",
        "post_url": "https://t.me/events_vrn/3178",
        "url": "https://t.me/events_vrn/3178",
        "raw_text": "raw post text",
        "city": "Воронеж",
        "links": ["https://example.com"],
        "events": [
            {
                "is_event": True,
                "title": "Винил-маркет",
                "date": "2026-07-12",
                "end_date": None,
                "time": "15:00",
                "end_time": "20:00",
                "place": "Митбоулинг",
                "address": "ул. Пушкинская, 11б",
                "category": "market",
                "price": None,
                "confidence": 0.95,
                "reason": "Есть дата, время и место.",
                "status": "draft",
            },
            {
                "is_event": True,
                "title": "Сомнительное событие",
                "date": None,
                "end_date": None,
                "time": None,
                "end_time": None,
                "place": None,
                "address": None,
                "category": "other",
                "price": None,
                "confidence": 0.5,
                "reason": "Мало данных.",
                "status": "draft",
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
