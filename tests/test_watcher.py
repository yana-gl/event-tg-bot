import unittest

from tg_event.watcher import filter_new_posts, update_state


def post(source, message_id, published_at):
    return {
        "source": source,
        "message_id": message_id,
        "published_at": published_at,
        "text": "event",
        "url": f"https://t.me/{source}/{message_id}",
        "city": "Воронеж",
    }


class WatcherTest(unittest.TestCase):
    def test_filter_new_posts_keeps_only_posts_since_date_and_after_state(self):
        rows = [
            post("events_vrn", 100, "2026-07-05T23:59:00+00:00"),
            post("events_vrn", 101, "2026-07-06T08:00:00+00:00"),
            post("events_vrn", 102, "2026-07-06T09:00:00+00:00"),
            post("avanturacoffee", 10, "2026-07-06T10:00:00+00:00"),
        ]
        state = {"events_vrn": 101}

        filtered = filter_new_posts(rows, state, since_date="2026-07-06")

        self.assertEqual(
            [(row["source"], row["message_id"]) for row in filtered],
            [("events_vrn", 102), ("avanturacoffee", 10)],
        )

    def test_filter_new_posts_applies_max_posts_per_cycle_after_sorting(self):
        rows = [
            post("events_vrn", 102, "2026-07-06T09:00:00+00:00"),
            post("events_vrn", 101, "2026-07-06T08:00:00+00:00"),
            post("events_vrn", 103, "2026-07-06T10:00:00+00:00"),
        ]

        filtered = filter_new_posts(
            rows,
            state={},
            since_date="2026-07-06",
            max_posts_per_cycle=2,
        )

        self.assertEqual([row["message_id"] for row in filtered], [101, 102])

    def test_filter_new_posts_applies_max_posts_per_channel(self):
        rows = [
            post("events_vrn", 101, "2026-07-06T08:00:00+00:00"),
            post("events_vrn", 102, "2026-07-06T09:00:00+00:00"),
            post("avanturacoffee", 10, "2026-07-06T08:30:00+00:00"),
            post("avanturacoffee", 11, "2026-07-06T09:30:00+00:00"),
        ]

        filtered = filter_new_posts(
            rows,
            state={},
            since_date="2026-07-06",
            max_posts_per_channel=1,
        )

        self.assertEqual(
            [(row["source"], row["message_id"]) for row in filtered],
            [("events_vrn", 101), ("avanturacoffee", 10)],
        )

    def test_update_state_keeps_highest_message_id_per_channel(self):
        rows = [
            post("events_vrn", 101, "2026-07-06T08:00:00+00:00"),
            post("events_vrn", 103, "2026-07-06T10:00:00+00:00"),
            post("avanturacoffee", 10, "2026-07-06T10:00:00+00:00"),
        ]

        state = update_state({"events_vrn": 100}, rows)

        self.assertEqual(state, {"events_vrn": 103, "avanturacoffee": 10})

    def test_edit_is_selected_despite_old_date_and_processed_id(self):
        row = post("events_vrn", 1, "2026-07-01T10:00:00+00:00")
        row["edited_at"] = "2026-07-10T10:00:00+00:00"
        known = {("events_vrn", 1): dict(row, edited_at=None)}
        result = filter_new_posts([row], {"events_vrn": 100}, "2026-07-10", known_posts=known)
        self.assertEqual(result, [row])
        self.assertEqual(update_state({"events_vrn": 100}, result), {"events_vrn": 100})

    def test_identical_or_stale_version_is_not_reparsed(self):
        row = post("events_vrn", 1, "2026-07-01T10:00:00+00:00")
        row["edited_at"] = "2026-07-10T10:00:00+00:00"
        known = {("events_vrn", 1): dict(row)}
        self.assertEqual(filter_new_posts([row], {"events_vrn": 100}, "2026-07-10", known_posts=known), [])
        row["edited_at"] = None
        row["text"] = "old version"
        self.assertEqual(filter_new_posts([row], {"events_vrn": 100}, "2026-07-10", known_posts=known), [])


if __name__ == "__main__":
    unittest.main()
