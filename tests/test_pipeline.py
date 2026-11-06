import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from types import SimpleNamespace

from tg_event.pipeline import extract_links, parse_rows


class FakeEvent:
    def __init__(self, title):
        self.title = title

    def to_dict(self):
        return {"title": self.title, "status": "draft"}


class PipelineTest(unittest.TestCase):
    def test_fatal_parse_error_does_not_start_the_rest_of_the_batch(self):
        rows = [dict(source="test", message_id=i, published_at="2026-11-05", text=str(i),
                     url=f"https://t.me/test/{i}", city="Владивосток") for i in range(1, 21)]
        settings = SimpleNamespace(openrouter_api_key="test", openrouter_model="test", openrouter_fallback_model="test")
        started = Barrier(3, timeout=3)
        failed = Event()
        release = Event()
        extra_request = Event()

        def parse_one(**kwargs):
            message_id = int(kwargs["text"])
            if message_id > 3:
                extra_request.set()
                return [FakeEvent(kwargs["text"])]
            started.wait()
            if message_id == 2:
                failed.set()
                raise ValueError("invalid response")
            if not release.wait(5):
                raise TimeoutError("test did not release parsing")
            return [FakeEvent(kwargs["text"])]

        with ThreadPoolExecutor(max_workers=1) as caller:
            result = caller.submit(parse_rows, rows, settings, parse_one=parse_one,
                                   progress=lambda message: None, max_workers=3)
            try:
                self.assertTrue(failed.wait(3))
                self.assertFalse(extra_request.wait(1), "a failed batch started another LLM request")
            finally:
                release.set()
            with self.assertRaisesRegex(ValueError, "invalid response"):
                result.result(timeout=3)

    def test_parallel_parsing_is_bounded_ordered_and_keeps_successes(self):
        rows = [dict(source="test", message_id=i, published_at="2026-11-05", text=str(i),
                     url=f"https://t.me/test/{i}", city="Владивосток") for i in range(1, 7)]
        settings = SimpleNamespace(openrouter_api_key="test", openrouter_model="test", openrouter_fallback_model="test")
        barrier = Barrier(3, timeout=3)
        lock = Lock()
        active = peak = 0

        def parse_one(**kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(active, peak)
            try:
                barrier.wait()
                if kwargs["text"] == "2":
                    raise ValueError("invalid response")
                return [FakeEvent(kwargs["text"])]
            finally:
                with lock:
                    active -= 1

        parsed = parse_rows(rows, settings, parse_one=parse_one, continue_on_error=True,
                            progress=lambda message: None, max_workers=3)
        self.assertEqual(peak, 3)
        self.assertEqual([row["message_id"] for row in parsed], [1, 3, 4, 5, 6])

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
