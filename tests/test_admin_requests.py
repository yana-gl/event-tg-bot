from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from test_admin import default_settings, parsed_row
from tg_event.admin import make_handler, serve
from tg_event.auth import issue_token
from tg_event.database import connect_database, init_database, save_parsed_row


def request(handler_type, path, *, method="GET", raw=b"", headers=None, auth=True):
    handler = handler_type.__new__(handler_type)
    handler.path, handler.request_version, handler.requestline = path, "HTTP/1.1", path
    handler.command = method
    handler.client_address = ("127.0.0.1", 1234)
    handler.headers = {"Content-Length": str(len(raw))}
    if auth:
        handler.headers["Authorization"] = "Bearer " + issue_token("admin", handler.settings.admin_secret)
    handler.headers.update(headers or {})
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    (handler.do_POST if method == "POST" else handler.do_GET)()
    head, body = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
    return int(head.split()[1]), json.loads(body)


class AdminRequestValidationTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        init_database(self.db)
        save_parsed_row(self.db, parsed_row())
        self.settings = default_settings(admin_secret="offline")
        self.handler_type = make_handler(self.settings, self.db, Path("/unused-static"))

    def test_non_object_bodies_are_bad_requests(self):
        for path in ("/api/events/1", "/api/events/1/merge", "/api/channels", "/api/login"):
            for raw in (b"[]", b"null", b'"text"', b"1"):
                with self.subTest(path=path, raw=raw):
                    self.handler_type.login_limiter.reset("127.0.0.1")
                    status, _ = request(self.handler_type, path, method="POST", raw=raw)
                    self.assertEqual(status, 400)

    def test_invalid_utf8_and_content_length_are_bad_requests(self):
        cases = [(b"\xff", {}), (b"{", {}), (b"{}", {"Content-Length": "3"})]
        cases.extend((b"{}", {"Content-Length": value}) for value in ("bad", "-1", "1.5", str(10 ** 40)))
        for path in ("/api/events/1", "/api/events/1/merge", "/api/channels", "/api/login"):
            for raw, headers in cases:
                with self.subTest(path=path, headers=headers, raw=raw):
                    self.handler_type.login_limiter.reset("127.0.0.1")
                    status, _ = request(self.handler_type, path, method="POST", raw=raw, headers=headers)
                    self.assertEqual(status, 400)

    def test_enum_containers_produce_validation_details(self):
        for field in ("status", "category", "event_state"):
            for value in ([], {}):
                with self.subTest(field=field, value=value):
                    status, body = request(self.handler_type, "/api/events/1", method="POST",
                                           raw=json.dumps({field: value}).encode())
                    self.assertEqual(status, 400)
                    self.assertEqual(body["error"], "validation")
                    self.assertTrue(body["details"])

    def test_oversized_path_ids_are_not_found(self):
        for event_id in (2 ** 63, -(2 ** 63) - 1):
            for method, suffix in (("GET", ""), ("POST", ""), ("POST", "/delete"), ("POST", "/merge")):
                with self.subTest(event_id=event_id, method=method, suffix=suffix):
                    status, _ = request(self.handler_type, f"/api/events/{event_id}{suffix}", method=method,
                                        raw=b'{"target_id":1}')
                    self.assertEqual(status, 404)

    def test_oversized_merge_target_is_bad_request(self):
        status, _ = request(self.handler_type, "/api/events/1/merge", method="POST",
                            raw=json.dumps({"target_id": 2 ** 63}).encode())
        self.assertEqual(status, 400)

    def test_non_ascii_bearer_is_unauthorized(self):
        for path in ("/api/events", "/api/events/1", "/api/channels"):
            with self.subTest(path=path):
                status, body = request(self.handler_type, path, headers={"Authorization": "Bearer é.x"})
                self.assertEqual((status, body), (401, {"error": "unauthorized"}))

    def test_update_preserves_normalization_and_protected_fields(self):
        status, body = request(self.handler_type, "/api/events/1", method="POST",
                               raw=b'{"place":"", "status":"published", "confidence":0, "raw_post_id":999}')
        self.assertEqual((status, body), (200, {"ok": True}))
        row = self.db.execute("SELECT place, status, confidence, raw_post_id FROM events WHERE id=1").fetchone()
        self.assertEqual(tuple(row), (None, "published", 0.95, 1))
        locks = self.db.execute("SELECT field FROM event_field_locks WHERE event_id=1").fetchall()
        self.assertEqual({row[0] for row in locks}, {"place", "status"})


class AdminRequestConnectionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = default_settings(database_path=self.root / "db.sqlite3", admin_secret="offline")
        db = connect_database(self.settings.database_path)
        try:
            init_database(db)
            save_parsed_row(db, parsed_row())
            save_parsed_row(db, parsed_row(message_id=3179))
        finally:
            db.close()
        self.connections = []
        self.handler_type = make_handler(self.settings, None, self.root)

    def connect(self, path):
        connection = connect_database(path)
        self.connections.append(connection)
        self.addCleanup(connection.close)
        return connection

    def assert_closed(self):
        for connection in self.connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_each_database_request_opens_and_closes_its_connection(self):
        calls = [
            ("GET", "/api/events", b""), ("GET", "/api/events/1", b""),
            ("GET", "/api/channels", b""), ("POST", "/api/channels", b'{"username":"audit"}'),
            ("POST", "/api/channels/audit", b'{"enabled":false}'),
            ("POST", "/api/events/1", b'{"title":"Changed"}'),
            ("POST", "/api/events/2/merge", b'{"target_id":1}'),
            ("POST", "/api/events/1/delete", b"{}"),
        ]
        with patch("tg_event.admin.connect_database", side_effect=self.connect):
            for method, path, raw in calls:
                with self.subTest(path=path, method=method):
                    self.assertEqual(request(self.handler_type, path, method=method, raw=raw)[0], 200)
                    self.assert_closed()
        self.assertEqual(len(self.connections), len(calls))

    def test_database_failure_still_closes_connection(self):
        with patch("tg_event.admin.connect_database", side_effect=self.connect), patch(
            "tg_event.admin.fetch_events_by_status", side_effect=sqlite3.OperationalError("injected")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                request(self.handler_type, "/api/events")
        self.assertEqual(len(self.connections), 1)
        self.assert_closed()

    def test_unauthorized_requests_do_not_open_database(self):
        with patch("tg_event.admin.connect_database", side_effect=self.connect):
            self.assertEqual(request(self.handler_type, "/api/events", auth=False)[0], 401)
        self.assertEqual(self.connections, [])

    def test_client_errors_close_connection(self):
        with patch("tg_event.admin.connect_database", side_effect=self.connect):
            status, body = request(self.handler_type, "/api/events/999")
            self.assertEqual((status, body), (404, {"error": "not found"}))
            status, body = request(self.handler_type, "/api/events/1", method="POST", raw=b'{"status":[]}')
            self.assertEqual((status, body["error"]), (400, "validation"))
        self.assertEqual(len(self.connections), 2)
        self.assert_closed()

    def test_production_server_uses_request_connections_and_closes_server(self):
        with patch("tg_event.admin.ThreadingHTTPServer") as server_type, patch(
            "tg_event.admin.connect_database", side_effect=self.connect
        ), patch("builtins.print"):
            server = server_type.return_value
            server.serve_forever.side_effect = KeyboardInterrupt
            serve(self.settings, host="127.0.0.1", port=0, dev=False)
        handler_type = server_type.call_args.args[1]
        self.assertIsNone(handler_type.db)
        server.server_close.assert_called_once_with()
        self.assertEqual(self.connections, [])

    def test_shared_fixture_does_not_commit_another_requests_failed_update(self):
        writing = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        second_finished = threading.Event()

        class PausedConnection(sqlite3.Connection):
            def executemany(self, sql, values):
                if "event_field_locks" in sql:
                    writing.set()
                    if not release.wait(5):
                        raise RuntimeError("test did not release update")
                    raise sqlite3.OperationalError("injected before field locks")
                return super().executemany(sql, values)

        db = sqlite3.connect(":memory:", check_same_thread=False, factory=PausedConnection)
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        init_database(db)
        save_parsed_row(db, parsed_row())
        handler_type = make_handler(self.settings, db, self.root)

        def add():
            second_started.set()
            try:
                return request(handler_type, "/api/channels", method="POST", raw=b'{"username":"audit"}')
            finally:
                second_finished.set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(request, handler_type, "/api/events/1", method="POST", raw=b'{"title":"Failed"}')
            try:
                self.assertTrue(writing.wait(3))
                second = pool.submit(add)
                self.assertTrue(second_started.wait(3))
                self.assertFalse(second_finished.wait(0.1))
            finally:
                release.set()
            with self.assertRaisesRegex(sqlite3.OperationalError, "injected"):
                first.result(timeout=3)
            self.assertEqual(second.result(timeout=3)[0], 200)
        self.assertEqual(db.execute("SELECT title FROM events WHERE id=1").fetchone()[0], "Винил-маркет")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM event_field_locks").fetchone()[0], 0)
