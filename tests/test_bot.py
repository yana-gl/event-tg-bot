from __future__ import annotations

import sqlite3
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from tg_event.bot import MAX_MESSAGE_LEN, PERIODS, _split_messages, format_events, today_in_city
from tg_event.database import fetch_published_events, init_database, save_parsed_row
from tg_event.pipeline import match_events
from tg_event.event_schema import validate_event
from test_database import parsed_row


class BotSourceTest(unittest.TestCase):
    def test_city_date_changes_at_vladivostok_midnight(self):
        for stamp, expected in (
            ("2026-12-31T13:59:59+00:00", date(2026, 12, 31)),
            ("2026-12-31T14:00:00+00:00", date(2027, 1, 1)),
        ):
            with self.subTest(stamp=stamp), patch("tg_event.bot.datetime") as clock:
                instant = datetime.fromisoformat(stamp)
                clock.now.side_effect = lambda tz: instant.astimezone(tz)
                self.assertEqual(today_in_city(), expected)
                self.assertEqual(PERIODS["today"](today_in_city()), (expected, expected))

    def test_city_timezone_can_be_selected_without_server_timezone(self):
        instant = datetime.fromisoformat("2026-12-31T14:00:00+00:00")
        with patch("tg_event.bot.datetime") as clock:
            clock.now.side_effect = lambda tz: instant.astimezone(tz)
            self.assertEqual(today_in_city("Europe/Moscow"), date(2026, 12, 31))
            self.assertEqual(today_in_city("Asia/Vladivostok"), date(2027, 1, 1))

    def test_period_lengths_include_today_across_month_and_year_boundaries(self):
        for today in (date(2026, 12, 31), date(2028, 2, 28)):
            for period, days in (("today", 1), ("week", 7), ("month", 30)):
                with self.subTest(today=today, period=period):
                    start, end = PERIODS[period](today)
                    self.assertEqual(start, today)
                    self.assertEqual((end - start).days + 1, days)
        self.assertEqual(PERIODS["tomorrow"](date(2026, 12, 31)),
                         (date(2027, 1, 1), date(2027, 1, 1)))

    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        init_database(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_published_event_links_to_its_post(self):
        row = parsed_row()
        row["events"] = row["events"][:1]
        row["events"][0]["status"] = "published"
        save_parsed_row(self.connection, row)

        events = fetch_published_events(self.connection, "2026-07-12", "2026-07-12")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["post_url"], row["post_url"])
        self.assertIn(f'<a href="{row["post_url"]}">Ссылка</a>', format_events(events))

    def test_digest_cards_share_source_and_filters_still_apply(self):
        row = parsed_row()
        first = row["events"][0]
        first["status"] = "published"
        row["events"] = [
            first,
            dict(first, title="Второе событие", time="18:00"),
            dict(first, title="Черновик", status="draft"),
            dict(first, title="Повтор", status="repeat"),
            dict(first, title="Отклонено", status="rejected"),
            dict(first, title="Другая дата", date="2026-07-13"),
            dict(first, title="Без даты", date=None),
        ]
        save_parsed_row(self.connection, row)

        events = fetch_published_events(self.connection, "2026-07-12", "2026-07-12")
        cards = format_events(events).split("\n\n")[1:]

        self.assertEqual([e["title"] for e in events], [first["title"], "Второе событие"])
        self.assertEqual(len(cards), 2)
        for card in cards:
            self.assertIn(f'<a href="{row["post_url"]}">Ссылка</a>', card)

    def test_long_listing_keeps_cards_and_sources_together(self):
        row = parsed_row()
        event = dict(row["events"][0], status="published")
        row["events"] = [dict(event, title=f"Событие {i} " + "а" * 150) for i in range(30)]
        save_parsed_row(self.connection, row)
        events = fetch_published_events(self.connection, "2026-07-12", "2026-07-12")
        text = format_events(events)

        chunks = _split_messages(text)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= MAX_MESSAGE_LEN for chunk in chunks))
        for chunk in chunks:
            self.assertTrue(chunk.startswith("<b>12 июля, воскресенье</b>\n\n"))
        for card in text.split("\n\n")[1:]:
            self.assertEqual(sum(card in chunk for chunk in chunks), 1)
            self.assertTrue(card.endswith(f'<a href="{row["post_url"]}">Ссылка</a>'))

    def test_compact_format_escapes_text_and_hides_category(self):
        row = parsed_row()
        event = dict(row["events"][0], title='Джаз <live> & друзья',
                     place='Кафе "Зёрна"', address="ул. Ленина, 10",
                     price="500 ₽", post_url=row["post_url"])

        self.assertEqual(format_events([event]),
                         '<b>12 июля, воскресенье</b>\n\n'
                         'Джаз &lt;live&gt; &amp; друзья\n'
                         '15:00\n'
                         'Кафе &quot;Зёрна&quot;, ул. Ленина, 10\n'
                         'Цена: 500 ₽\n'
                         '<a href="https://t.me/events_vrn/3178">Ссылка</a>')

    def test_date_groups_and_multiday_range(self):
        row = parsed_row()
        event = dict(row["events"][0], post_url=row["post_url"])
        text = format_events([
            event, dict(event, title="Вечерняя встреча"),
            dict(event, date="2026-07-13", end_date="2026-07-15", time=None),
        ])

        self.assertEqual(text.count("<b>12 июля, воскресенье</b>"), 1)
        self.assertEqual(text.count("<b>13 июля, понедельник</b>"), 1)
        self.assertIn("13 июля — 15 июля", text)
        self.assertNotIn("2026-07-12", text)

    def test_split_keeps_new_day_heading_with_its_card(self):
        text = '<b>12 июля, воскресенье</b>\n\n' + 'а' * 120
        text += '\n\n<b>13 июля, понедельник</b>\n\nСледующее событие'

        with patch("tg_event.bot.MAX_MESSAGE_LEN", 160):
            chunks = _split_messages(text)

        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0].endswith('а' * 120))
        self.assertEqual(chunks[1], '<b>13 июля, понедельник</b>\n\nСледующее событие')

    def test_period_includes_overlapping_events_and_both_boundaries(self):
        row = parsed_row()
        event = dict(row["events"][0], status="published")
        row["events"] = [
            dict(event, title="Continues", date="2026-07-10", end_date="2026-07-14"),
            dict(event, title="Ends at boundary", date="2026-07-11", end_date="2026-07-12"),
            dict(event, title="Starts at boundary", date="2026-07-13", end_date=None),
            dict(event, title="Already ended", date="2026-07-10", end_date="2026-07-11"),
            dict(event, title="Later", date="2026-07-14", end_date=None),
            dict(event, title="Cancelled", date="2026-07-10", end_date="2026-07-14", event_state="cancelled"),
            dict(event, title="Postponed", date=None, end_date=None, event_state="postponed"),
            dict(event, title="Draft", date="2026-07-10", end_date="2026-07-14", status="draft"),
        ]
        save_parsed_row(self.connection, row)

        self.connection.execute("UPDATE events SET event_state = 'cancelled' WHERE title = 'Cancelled'")

        events = fetch_published_events(self.connection, "2026-07-12", "2026-07-13")

        self.assertEqual({e["title"] for e in events},
                         {"Continues", "Ends at boundary", "Starts at boundary"})
        text = format_events(events, date_from="2026-07-12")
        self.assertTrue(text.startswith("<b>12 июля, воскресенье</b>"))
        self.assertNotIn("<b>10 июля", text)
        self.assertIn("10 июля — 14 июля", text)
        self.assertEqual(text.count("Continues"), 1)

    def test_new_events_publish_without_changing_existing_statuses(self):
        existing = parsed_row()
        first = existing["events"][0]
        existing["events"] = [
            dict(first, title=f"Карточка {status}", status=status)
            for status in ("draft", "rejected", "repeat")
        ]
        save_parsed_row(self.connection, existing)
        row = parsed_row()
        row["message_id"] = 3179
        row["post_url"] = "https://t.me/events_vrn/3179"
        payload = dict(first)
        payload.pop("status")
        row["events"] = [validate_event(payload).to_dict()]
        save_parsed_row(self.connection, row)

        events = fetch_published_events(self.connection, "2026-07-12", "2026-07-12")

        self.assertEqual(len(events), 1)
        self.assertIn(row["post_url"], format_events(events))
        statuses = self.connection.execute("SELECT status FROM events ORDER BY id").fetchall()
        self.assertEqual([r["status"] for r in statuses],
                         ["draft", "rejected", "repeat", "published"])

    def test_overlap_across_year_boundary_sorts_by_visible_day_and_time(self):
        row = parsed_row()
        event = dict(row["events"][0], status="published")
        row["events"] = [
            dict(event, title="Ongoing evening", date="2026-12-30", end_date="2027-01-02", time="18:00"),
            dict(event, title="Morning", date="2027-01-01", end_date=None, time="10:00"),
            dict(event, title="Next day", date="2027-01-02", end_date=None, time="09:00"),
        ]
        save_parsed_row(self.connection, row)

        events = fetch_published_events(self.connection, "2027-01-01", "2027-01-02")

        self.assertEqual([e["title"] for e in events], ["Morning", "Ongoing evening", "Next day"])
        text = format_events(events, date_from="2027-01-01")
        self.assertEqual(text.count("<b>1 января, пятница</b>"), 1)
        self.assertEqual(text.count("<b>2 января, суббота</b>"), 1)
        self.assertIn("30 декабря — 2 января", text)

    def test_deduplication_hides_auto_published_event(self):
        existing = parsed_row()
        existing["events"] = existing["events"][:1]
        save_parsed_row(self.connection, existing)
        row = parsed_row()
        row["message_id"] = 3179
        row["post_url"] = "https://t.me/events_vrn/3179"
        payload = row["events"][0]
        payload.pop("status")
        row["events"] = [validate_event(payload).to_dict()]
        settings = SimpleNamespace(openrouter_api_key="test", openrouter_fallback_model="test")

        with patch("tg_event.pipeline.find_repeats", return_value=[1]):
            match_events(self.connection, settings, [row])
        save_parsed_row(self.connection, row)

        self.assertEqual(row["events"][0]["_match_id"], 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertEqual(fetch_published_events(self.connection, "2026-07-12", "2026-07-12"), [])


if __name__ == "__main__":
    unittest.main()
