import unittest

from tg_event.event_schema import EventStatus, validate_event, validate_event_response


class EventSchemaTest(unittest.TestCase):
    def test_validate_event_accepts_strict_model_json(self):
        payload = {
            "is_event": True,
            "title": "Концерт во дворе",
            "date": "2026-07-12",
            "end_date": None,
            "time": "19:30",
            "end_time": None,
            "place": "Культурный центр",
            "address": "ул. Ленина, 1",
            "category": "music",
            "price": "600р",
            "confidence": 0.86,
            "reason": "В тексте есть дата, время, место и описание концерта.",
        }

        event = validate_event(payload)

        self.assertEqual(event.title, "Концерт во дворе")
        self.assertIsNone(event.end_date)
        self.assertIsNone(event.end_time)
        self.assertEqual(event.price, "600р")
        self.assertEqual(event.status, EventStatus.PENDING)

    def test_validate_event_marks_low_confidence_as_needs_review(self):
        payload = {
            "is_event": True,
            "title": "Встреча",
            "date": None,
            "end_date": None,
            "time": None,
            "end_time": None,
            "place": None,
            "address": None,
            "category": "other",
            "price": None,
            "confidence": 0.51,
            "reason": "Похоже на событие, но дата не указана явно.",
        }

        event = validate_event(payload)

        self.assertEqual(event.status, EventStatus.NEEDS_REVIEW)

    def test_validate_event_marks_non_event_as_not_event(self):
        payload = {
            "is_event": False,
            "title": None,
            "date": None,
            "end_date": None,
            "time": None,
            "end_time": None,
            "place": None,
            "address": None,
            "category": None,
            "price": None,
            "confidence": 0.91,
            "reason": "Это рекламный пост без события.",
        }

        event = validate_event(payload)

        self.assertEqual(event.status, EventStatus.NOT_EVENT)

    def test_validate_event_rejects_unknown_fields(self):
        payload = {
            "is_event": False,
            "title": None,
            "date": None,
            "end_date": None,
            "time": None,
            "end_time": None,
            "place": None,
            "address": None,
            "category": None,
            "price": None,
            "confidence": 0.5,
            "reason": "Нет события.",
            "extra": "not allowed",
        }

        with self.assertRaisesRegex(ValueError, "extra"):
            validate_event(payload)

    def test_validate_event_response_accepts_multiple_events(self):
        payload = {
            "events": [
                {
                    "is_event": True,
                    "title": "Лекция",
                    "date": "2026-07-10",
                    "end_date": None,
                    "time": "18:00",
                    "end_time": None,
                    "place": "Библиотека",
                    "address": None,
                    "category": "lecture",
                    "price": None,
                    "confidence": 0.82,
                    "reason": "Есть дата, время и формат события.",
                },
                {
                    "is_event": True,
                    "title": "Концерт",
                    "date": "2026-07-11",
                    "end_date": "2026-07-12",
                    "time": "20:00",
                    "end_time": "02:00",
                    "place": "Бар",
                    "address": "ул. Кольцовская, 1",
                    "category": "music",
                    "price": "1000р",
                    "confidence": 0.63,
                    "reason": "Событие похоже на концерт, но адрес может быть неполным.",
                },
            ],
            "post_reason": "Пост содержит подборку из двух событий.",
        }

        response = validate_event_response(payload)

        self.assertEqual(response.post_reason, "Пост содержит подборку из двух событий.")
        self.assertEqual(len(response.events), 2)
        self.assertEqual(response.events[0].status, EventStatus.PENDING)
        self.assertEqual(response.events[1].end_date, "2026-07-12")
        self.assertEqual(response.events[1].end_time, "02:00")
        self.assertEqual(response.events[1].price, "1000р")
        self.assertEqual(response.events[1].status, EventStatus.NEEDS_REVIEW)

    def test_validate_event_response_accepts_empty_events(self):
        payload = {
            "events": [],
            "post_reason": "Это рекламный пост без конкретных событий.",
        }

        response = validate_event_response(payload)

        self.assertEqual(response.events, [])
        self.assertEqual(response.post_reason, "Это рекламный пост без конкретных событий.")

    def test_validate_event_rejects_unknown_category(self):
        payload = {
            "is_event": True,
            "title": "Концерт",
            "date": "2026-07-11",
            "end_date": None,
            "time": "20:00",
            "end_time": None,
            "place": "Бар",
            "address": None,
            "category": "concert",
            "price": None,
            "confidence": 0.9,
            "reason": "Есть дата и место.",
        }

        with self.assertRaisesRegex(ValueError, "category"):
            validate_event(payload)


if __name__ == "__main__":
    unittest.main()
