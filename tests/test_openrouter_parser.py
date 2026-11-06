import json
import unittest
from unittest.mock import patch

from tg_event.openrouter_parser import (
    build_prompt,
    extract_json_object,
    find_repeats,
    parse_event_with_openrouter,
)


class _FakeResponse:
    def __init__(self, content):
        self.content = content


def _make_find_repeats_response(content):
    return {"choices": [{"message": {"content": content}}]}


class OpenRouterParserTest(unittest.TestCase):
    def test_invalid_event_response_uses_fallback(self):
        valid_event = dict(is_event=True, title="Лекция", date="2026-11-07", end_date=None,
                           time=None, end_time=None, place=None, address=None, category="lecture",
                           price=None, confidence=0.9, reason="Анонс")
        incomplete = dict(valid_event)
        incomplete.pop("end_date")
        invalid_responses = [
            _make_find_repeats_response(json.dumps({"events": [incomplete], "post_reason": "Анонс"})),
            _make_find_repeats_response("invalid JSON"),
            {"choices": []},
            _make_find_repeats_response(None),
            {"choices": [{"finish_reason": "length", "message": {"content": '{"events": [], "post_reason": ""}'}}]},
            TimeoutError("timed out"),
        ]
        valid = _make_find_repeats_response(json.dumps({"events": [valid_event], "post_reason": "Анонс"}))
        for invalid in invalid_responses:
            with self.subTest(invalid=invalid), patch(
                "tg_event.openrouter_parser._request_openrouter", side_effect=[invalid, valid],
            ) as request:
                events = parse_event_with_openrouter(
                    api_key="test", model="primary", fallback_model="fallback", text="Лекция",
                    published_at="2026-11-05", source="test", city="Владивосток",
                )
                self.assertEqual(events[0].date, "2026-11-07")
                self.assertEqual([call.kwargs["model"] for call in request.call_args_list], ["primary", "fallback"])

    def test_both_invalid_responses_fail_after_one_retry(self):
        with patch("tg_event.openrouter_parser._request_openrouter", return_value=
                   _make_find_repeats_response('{"events": [{}], "post_reason": ""}')) as request:
            with self.assertRaises(ValueError):
                parse_event_with_openrouter(
                    api_key="test", model="same", fallback_model="same", text="Лекция",
                    published_at="2026-11-05", source="test", city="Владивосток",
                )
        self.assertEqual(request.call_count, 2)

    def test_extract_json_object_accepts_plain_json(self):
        text = '{"is_event": false, "title": null}'

        self.assertEqual(extract_json_object(text), {"is_event": False, "title": None})

    def test_extract_json_object_accepts_markdown_fenced_json(self):
        text = """```json
{"is_event": true, "title": "Лекция"}
```"""

        self.assertEqual(extract_json_object(text), {"is_event": True, "title": "Лекция"})

    def test_extract_json_object_rejects_missing_json(self):
        with self.assertRaisesRegex(ValueError, "JSON"):
            extract_json_object("Я не могу определить событие.")

    def test_build_prompt_contains_required_context(self):
        prompt = build_prompt(
            text="Сегодня концерт в 20:00",
            published_at="2026-07-04T10:00:00+03:00",
            source="events_vrn",
            city="Воронеж",
        )

        self.assertIn("events_vrn", prompt)
        self.assertIn("Воронеж", prompt)
        self.assertIn("2026-07-04T10:00:00+03:00", prompt)
        self.assertIn('"events"', prompt)
        self.assertIn('"post_reason"', prompt)
        self.assertIn('"is_event"', prompt)
        self.assertIn('"end_date"', prompt)
        self.assertIn('"end_time"', prompt)
        self.assertIn('"price"', prompt)
        self.assertIn("music, cinema, lecture, exhibition, market, workshop, food, sport, party, kids, other", prompt)
        self.assertIn('Если дата окончания совпадает с "date", ставь "end_date": null', prompt)
        self.assertIn("place пиши как название площадки в именительном падеже", prompt)
        self.assertIn("Не считай постоянные услуги, товары, меню, скидки, обычный режим работы", prompt)

    def test_find_repeats_returns_matched_original_ids(self):
        from tg_event import openrouter_parser

        new = [
            {"title": "Концерт Х", "date": "2026-07-12", "place": "АУТ"},
            {"title": "Лекция по ботанике", "date": "2026-07-13", "place": "Библиотека"},
        ]
        existing = [
            {"id": 7, "title": "Х (большой концерт)", "date": "2026-07-12", "place": "АУТ, Кольцовская"},
            {"id": 9, "title": "Винил-маркет", "date": "2026-07-12", "place": "Митбоулинг"},
        ]

        def fake_request(api_key, model, prompt):
            return {"choices": [{"message": {"content": '{"0": 7, "1": null}'}}]}

        original = openrouter_parser._request_openrouter
        openrouter_parser._request_openrouter = fake_request
        try:
            result = find_repeats(
                api_key="key",
                model="m",
                new_events=new,
                existing_events=existing,
            )
        finally:
            openrouter_parser._request_openrouter = original

        self.assertEqual(result, [7, None])

    def test_find_repeats_returns_none_when_no_existing(self):
        result = find_repeats(
            api_key="key",
            model="m",
            new_events=[{"title": "X", "date": "2026-07-12", "place": "Y"}],
            existing_events=[],
        )
        self.assertEqual(result, [None])

    def test_find_repeats_handles_request_error(self):
        from tg_event import openrouter_parser

        def fake_request(api_key, model, prompt):
            raise ValueError("network error")

        original = openrouter_parser._request_openrouter
        openrouter_parser._request_openrouter = fake_request
        try:
            result = find_repeats(
                api_key="key",
                model="m",
                new_events=[{"title": "X", "date": "2026-07-12", "place": "Y"}],
                existing_events=[{"id": 1, "title": "X", "date": "2026-07-12", "place": "Y"}],
            )
        finally:
            openrouter_parser._request_openrouter = original

        self.assertEqual(result, [None])

    def test_invalid_or_unknown_matches_are_ignored(self):
        for content in ('not json', '{"0": 12345}', '{"0": true}', '{"0": 7.5}'):
            with self.subTest(content=content), patch(
                "tg_event.openrouter_parser._request_openrouter",
                return_value=_make_find_repeats_response(content),
            ):
                self.assertEqual(find_repeats(
                    api_key="test", model="test", new_events=[{"title": "X"}],
                    existing_events=[{"id": 7, "title": "X"}],
                ), [None])


if __name__ == "__main__":
    unittest.main()
