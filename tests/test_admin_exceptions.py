from __future__ import annotations

import io
import json
import sqlite3
import unittest

from test_admin import default_settings
from test_deduplication import post
from tg_event.admin import make_handler
from tg_event.auth import issue_token
from tg_event.database import (
    fetch_event_detail, fetch_events_by_status, fetch_published_events,
    init_database, merge_events, save_parsed_row, save_parsed_rows, update_event,
)


class AdminExceptionsTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        init_database(self.db)
        self.original = post(1)
        self.original["published_at"] = "2026-07-01T10:00:00+00:00"
        save_parsed_row(self.db, self.original)
        self.event_id = self.db.execute("SELECT id FROM events").fetchone()[0]

    def tearDown(self):
        self.db.close()

    def conflict(self):
        update_event(self.db, self.event_id, {"time": "20:00"})
        row = post(2, action="update", time="18:00", changed_fields=["time"])
        row["published_at"] = "2026-07-02T10:00:00+00:00"
        row["events"][0]["_match_id"] = self.event_id
        save_parsed_rows(self.db, [row])
        return row

    def resolution(self):
        detail = fetch_event_detail(self.db, self.event_id)
        return {"post_id": detail["conflict_post_id"], "at": detail["conflict_at"]}

    def test_exception_list_includes_published_conflicts_and_drafts_only(self):
        self.conflict()
        draft = post(3, title="Draft")
        draft["events"][0]["status"] = "draft"
        save_parsed_row(self.db, draft)
        save_parsed_row(self.db, post(4, title="Ordinary"))
        rows = fetch_events_by_status(self.db, "exceptions")
        self.assertEqual({r["title"] for r in rows}, {self.original["events"][0]["title"], "Draft"})
        self.assertEqual([r["id"] for r in fetch_events_by_status(self.db, "conflicts")], [self.event_id])

    def test_dismissed_conflict_does_not_reopen_until_new_version(self):
        row = self.conflict()
        update_event(self.db, self.event_id, {}, resolve_conflict=self.resolution())
        save_parsed_rows(self.db, [row])
        detail = fetch_event_detail(self.db, self.event_id)
        self.assertIsNone(detail["conflict_post_id"])
        self.assertEqual(detail["time"], "20:00")
        row["edited_at"] = "2026-07-03T10:00:00+00:00"
        row["raw_text"] = "Updated again"
        row["events"][0]["time"] = "19:00"
        save_parsed_rows(self.db, [row])
        self.assertIsNotNone(fetch_event_detail(self.db, self.event_id)["conflict_post_id"])

    def test_resolution_can_save_manual_fields_and_reject_stale_marker(self):
        self.conflict()
        marker = self.resolution()
        with self.assertRaises(ValueError):
            update_event(self.db, self.event_id, {"time": "12:00"},
                         resolve_conflict=dict(marker, at="2026-07-01T00:00:00+00:00"))
        self.assertEqual(fetch_event_detail(self.db, self.event_id)["time"], "20:00")
        update_event(self.db, self.event_id, {"time": "18:00"}, resolve_conflict=marker)
        self.assertIsNone(fetch_event_detail(self.db, self.event_id)["conflict_post_id"])
        self.assertEqual(fetch_event_detail(self.db, self.event_id)["time"], "18:00")

    def test_manual_state_and_hide_survive_reparse(self):
        update_event(self.db, self.event_id, {"event_state": "cancelled", "status": "rejected"})
        save_parsed_rows(self.db, [self.original])
        detail = fetch_event_detail(self.db, self.event_id)
        self.assertEqual((detail["event_state"], detail["status"]), ("cancelled", "rejected"))
        self.assertEqual(fetch_published_events(self.db, "2026-07-12", "2026-07-12"), [])

        change = post(3, action="reschedule", date="2026-07-15")
        change["published_at"] = "2026-07-03T10:00:00+00:00"
        change["events"][0]["_match_id"] = self.event_id
        save_parsed_rows(self.db, [change])
        detail = fetch_event_detail(self.db, self.event_id)
        self.assertEqual((detail["event_state"], detail["status"]), ("cancelled", "rejected"))
        self.assertIsNotNone(detail["conflict_post_id"])

    def test_merge_keeps_target_fields_and_sources_across_reparse(self):
        duplicate = post(2, title="Another extracted title", price="500")
        save_parsed_row(self.db, duplicate)
        source_id = self.db.execute("SELECT MAX(id) FROM events").fetchone()[0]
        merge_events(self.db, source_id, self.event_id)
        detail = fetch_event_detail(self.db, self.event_id)
        self.assertEqual(detail["title"], self.original["events"][0]["title"])
        self.assertIsNone(detail["price"])
        self.assertEqual(len(detail["sources"]), 2)
        save_parsed_rows(self.db, [duplicate, self.original])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertIsNone(fetch_event_detail(self.db, self.event_id)["price"])

    def test_merge_does_not_affect_other_event_in_digest(self):
        digest = post(2, title="Duplicate")
        digest["events"].append(dict(digest["events"][0], title="Different event", time="22:00"))
        save_parsed_row(self.db, digest)
        source_id = self.db.execute("SELECT id FROM events WHERE title = 'Duplicate'").fetchone()[0]
        merge_events(self.db, source_id, self.event_id)
        save_parsed_rows(self.db, [digest])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT time FROM events WHERE title = 'Different event'").fetchone()[0], "22:00")

    def test_merge_does_not_block_future_automatic_cancellation(self):
        save_parsed_row(self.db, post(2))
        source_id = self.db.execute("SELECT MAX(id) FROM events").fetchone()[0]
        merge_events(self.db, source_id, self.event_id)
        cancellation = post(3, action="cancel")
        cancellation["published_at"] = "2026-07-04T10:00:00+00:00"
        cancellation["events"][0]["_match_id"] = self.event_id
        save_parsed_rows(self.db, [cancellation])
        self.assertEqual(fetch_event_detail(self.db, self.event_id)["event_state"], "cancelled")

    def test_merge_rejects_same_post_and_rolls_back_on_write_failure(self):
        digest = post(2, title="One")
        digest["events"].append(dict(digest["events"][0], title="Two"))
        save_parsed_row(self.db, digest)
        ids = [r[0] for r in self.db.execute("SELECT id FROM events WHERE title IN ('One', 'Two')")]
        with self.assertRaises(ValueError):
            merge_events(self.db, *ids)
        self.db.execute("CREATE TRIGGER fail_merge BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            merge_events(self.db, ids[0], self.event_id)
        self.assertEqual(len(fetch_event_detail(self.db, self.event_id)["sources"]), 1)
        self.assertIsNotNone(fetch_event_detail(self.db, ids[0]))

    def test_resolution_rows_are_deleted_with_event(self):
        self.conflict()
        update_event(self.db, self.event_id, {}, resolve_conflict=self.resolution())
        self.db.execute("DELETE FROM events WHERE id = ?", (self.event_id,))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event_conflict_resolutions").fetchone()[0], 0)

    def test_merge_preserves_source_conflict_and_acknowledgement(self):
        row = self.conflict()
        update_event(self.db, self.event_id, {}, resolve_conflict=self.resolution())
        duplicate = post(3, title="Keep this title")
        save_parsed_row(self.db, duplicate)
        target_id = self.db.execute("SELECT MAX(id) FROM events").fetchone()[0]
        merge_events(self.db, self.event_id, target_id)
        row["events"][0]["_match_id"] = target_id
        save_parsed_rows(self.db, [row])
        self.assertIsNone(fetch_event_detail(self.db, target_id)["conflict_post_id"])
        self.assertEqual(self.db.execute("SELECT event_id FROM event_conflict_resolutions").fetchone()[0], target_id)

    def test_merge_carries_unresolved_conflict_to_target(self):
        self.conflict()
        marker = self.resolution()
        save_parsed_row(self.db, post(3, title="Target"))
        target_id = self.db.execute("SELECT MAX(id) FROM events").fetchone()[0]
        merge_events(self.db, self.event_id, target_id)
        detail = fetch_event_detail(self.db, target_id)
        self.assertEqual((detail["conflict_post_id"], detail["conflict_at"]), (marker["post_id"], marker["at"]))

    def request(self, path, body=None, *, auth=True):
        settings = default_settings(admin_secret="offline-test")
        handler_type = make_handler(settings, self.db, settings.admin_static_dir)
        handler = handler_type.__new__(handler_type)
        payload = json.dumps(body).encode() if body is not None else b""
        handler.path = path
        handler.request_version = "HTTP/1.1"
        handler.requestline = path
        handler.command = "POST" if body is not None else "GET"
        handler.headers = {"Content-Length": str(len(payload))}
        if auth:
            handler.headers["Authorization"] = "Bearer " + issue_token("admin", settings.admin_secret)
        handler.rfile = io.BytesIO(payload)
        handler.wfile = io.BytesIO()
        (handler.do_POST if body is not None else handler.do_GET)()
        headers, content = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        return int(headers.split()[1]), json.loads(content)

    def test_offline_api_auth_list_resolution_and_state_validation(self):
        self.conflict()
        self.assertEqual(self.request("/api/events", auth=False)[0], 401)
        status, body = self.request("/api/events")
        self.assertEqual((status, body["status"], len(body["items"])), (200, "exceptions", 1))
        status, _ = self.request(f"/api/events/{self.event_id}", {"event_state": "bad"})
        self.assertEqual(status, 400)
        status, _ = self.request(f"/api/events/{self.event_id}", {"resolve_conflict": self.resolution()})
        self.assertEqual(status, 200)
        self.assertIsNone(fetch_event_detail(self.db, self.event_id)["conflict_post_id"])

    def test_offline_merge_api(self):
        save_parsed_row(self.db, post(2, title="Duplicate"))
        source_id = self.db.execute("SELECT MAX(id) FROM events").fetchone()[0]
        path = f"/api/events/{source_id}/merge"
        self.assertEqual(self.request(path, {"target_id": self.event_id}, auth=False)[0], 401)
        self.assertEqual(self.request(path, {"target_id": True})[0], 400)
        self.assertEqual(self.request(path, {"target_id": 9999})[0], 404)
        self.assertEqual(self.request(path, {"target_id": self.event_id})[0], 200)
        self.assertEqual(len(fetch_event_detail(self.db, self.event_id)["sources"]), 2)
