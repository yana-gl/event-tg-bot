import sqlite3
import unittest

from tg_event.database import init_database, save_parsed_row


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
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
        self.assertEqual(events[1]["status"], "needs_review")

    def test_save_parsed_row_replaces_existing_events_for_same_post(self):
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
                "status": "pending",
            }
        ]

        save_parsed_row(self.connection, row)

        raw_posts_count = self.connection.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0]
        events = self.connection.execute("SELECT title FROM events").fetchall()

        self.assertEqual(raw_posts_count, 1)
        self.assertEqual([row["title"] for row in events], ["Обновленное событие"])


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
                "status": "pending",
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
                "status": "needs_review",
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
