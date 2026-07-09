import unittest

from tg_event.openrouter_parser import build_prompt, extract_json_object


class OpenRouterParserTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
