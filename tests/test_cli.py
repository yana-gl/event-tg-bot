import unittest
from types import SimpleNamespace

from tg_event.cli import extract_links, parse_rows


class FakeEvent:
    def __init__(self, title):
        self.title = title

    def to_dict(self):
        return {"title": self.title, "status": "draft"}


class CliTest(unittest.TestCase):
    def test_parse_rows_respects_limit(self):
        rows = [
            {
                "source": "events_vrn",
                "message_id": index,
                "published_at": "2026-07-04T10:00:00+03:00",
                "url": f"https://t.me/events_vrn/{index}",
                "text": f"post {index}",
                "links": [],
                "city": "Воронеж",
            }
            for index in range(1, 4)
        ]
        settings = SimpleNamespace(
            openrouter_api_key="key",
            openrouter_model="model",
            openrouter_fallback_model="fallback",
        )

        parsed = parse_rows(
            rows,
            settings,
            limit=2,
            parse_one=lambda **kwargs: [FakeEvent(kwargs["text"])],
            progress=lambda message: None,
        )

        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["post_url"], "https://t.me/events_vrn/1")
        self.assertEqual(parsed[0]["events"][0]["title"], "post 1")
        self.assertEqual(parsed[1]["events"][0]["title"], "post 2")

    def test_parse_rows_writes_multiple_events_for_one_post(self):
        rows = [
            {
                "source": "events_vrn",
                "message_id": 10,
                "published_at": "2026-07-04T10:00:00+03:00",
                "url": "https://t.me/events_vrn/10",
                "text": "two events https://example.com/info",
                "city": "Воронеж",
            }
        ]
        settings = SimpleNamespace(
            openrouter_api_key="key",
            openrouter_model="model",
            openrouter_fallback_model="fallback",
        )

        parsed = parse_rows(
            rows,
            settings,
            parse_one=lambda **kwargs: [FakeEvent("Лекция"), FakeEvent("Концерт")],
            progress=lambda message: None,
        )

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["links"], ["https://example.com/info"])
        self.assertEqual([event["title"] for event in parsed[0]["events"]], ["Лекция", "Концерт"])

    def test_extract_links_finds_urls_and_mentions(self):
        text = "Подробности: https://example.com/a и @events_vrn"

        self.assertEqual(extract_links(text), ["https://example.com/a", "https://t.me/events_vrn"])


if __name__ == "__main__":
    unittest.main()
