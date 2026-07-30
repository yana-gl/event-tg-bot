import json
import sqlite3
import unittest
from pathlib import Path

from tg_event.admin import (
    _path_id,
    _validate_fields,
    make_handler,
)
from tg_event.auth import issue_token, verify_token
from tg_event.config import Settings
from tg_event.database import (
    delete_event,
    fetch_event_detail,
    fetch_events_by_status,
    init_database,
    save_parsed_row,
    update_event,
)
from tg_event.event_schema import EventStatus

from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def default_settings(**overrides) -> Settings:
    base = {
        "telegram_api_id": 1,
        "telegram_api_hash": "h",
        "telegram_phone": "+7",
        "openrouter_api_key": "k",
        "openrouter_model": "m",
        "openrouter_fallback_model": "f",
        "bot_token": "",
        "city": "Воронеж",
        "channels": ["events_vrn"],
    }
    base.update(overrides)
    return Settings(**base)


def parsed_row(message_id=3178):
    return {
        "source": "events_vrn",
        "message_id": message_id,
        "published_at": "2026-07-03T09:13:48+00:00",
        "post_url": "https://t.me/events_vrn/%d" % message_id,
        "url": "https://t.me/events_vrn/%d" % message_id,
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
                "status": "draft",
            }
        ],
    }


def db_with_event() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_database(conn)
    save_parsed_row(conn, parsed_row())
    return conn


class DatabaseAdminTest(unittest.TestCase):
    def setUp(self):
        self.connection = db_with_event()

    def tearDown(self):
        self.connection.close()

    def _first_event_id(self) -> int:
        return self.connection.execute("SELECT id FROM events").fetchone()[0]

    def test_fetch_events_by_status_returns_drafts(self):
        rows = fetch_events_by_status(self.connection, "draft")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Винил-маркет")

    def test_fetch_event_detail_includes_post_and_links(self):
        event_id = self._first_event_id()
        detail = fetch_event_detail(self.connection, event_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["source"], "events_vrn")
        self.assertEqual(detail["url"], "https://t.me/events_vrn/3178")
        self.assertEqual(detail["text"], "raw post text")
        self.assertEqual(detail["links"], ["https://example.com"])
        self.assertEqual(detail["reason"], "Есть дата, время и место.")

    def test_fetch_event_detail_missing_returns_none(self):
        self.assertIsNone(fetch_event_detail(self.connection, 999999))

    def test_update_event_changes_fields(self):
        event_id = self._first_event_id()
        ok = update_event(self.connection, event_id, {"title": "Новое название", "status": "published"})
        self.assertTrue(ok)
        detail = fetch_event_detail(self.connection, event_id)
        self.assertEqual(detail["title"], "Новое название")
        self.assertEqual(detail["status"], "published")

    def test_update_event_unknown_field_raises(self):
        event_id = self._first_event_id()
        with self.assertRaises(ValueError):
            update_event(self.connection, event_id, {"confidence": 0.1})

    def test_update_event_missing_returns_false(self):
        self.assertFalse(update_event(self.connection, 999999, {"title": "x"}))

    def test_update_event_empty_payload_returns_true_when_exists(self):
        event_id = self._first_event_id()
        self.assertTrue(update_event(self.connection, event_id, {}))

    def test_delete_event_removes_row(self):
        event_id = self._first_event_id()
        self.assertTrue(delete_event(self.connection, event_id))
        self.assertIsNone(fetch_event_detail(self.connection, event_id))
        raw_count = self.connection.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0]
        self.assertEqual(raw_count, 1)

    def test_delete_event_missing_returns_false(self):
        self.assertFalse(delete_event(self.connection, 999999))


class AdminHelpersTest(unittest.TestCase):
    def test_path_id_parses_int(self):
        self.assertEqual(_path_id("/api/events/42"), 42)

    def test_path_id_with_suffix(self):
        self.assertEqual(_path_id("/api/events/42/delete", suffix="/delete"), 42)

    def test_path_id_invalid(self):
        self.assertIsNone(_path_id("/api/events/abc"))

    def test_path_id_missing_suffix(self):
        self.assertIsNone(_path_id("/api/events/42", suffix="/delete"))

    def test_validate_fields_accepts_valid(self):
        self.assertEqual(_validate_fields({"status": "published", "category": "music"}), [])

    def test_validate_fields_rejects_bad_status(self):
        errors = _validate_fields({"status": "repeat"})
        self.assertTrue(any("status" in e for e in errors))

    def test_validate_fields_rejects_bad_category(self):
        errors = _validate_fields({"category": "noise"})
        self.assertTrue(errors)


class AdminApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = sqlite3.connect(":memory:", check_same_thread=False)
        cls.connection.row_factory = sqlite3.Row
        init_database(cls.connection)
        cls.settings = default_settings(
            admin_user="admin",
            admin_password="pass",
            admin_secret="topsecret",
            admin_static_dir=Path(__file__).resolve().parent / "_nonexistent_static",
        )
        handler = make_handler(cls.settings, cls.connection, cls.settings.admin_static_dir)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        import threading
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.connection.close()

    def setUp(self) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM events")
            self.connection.execute("DELETE FROM post_links")
            self.connection.execute("DELETE FROM raw_posts")
            self.connection.execute("DELETE FROM sources")
        save_parsed_row(self.connection, parsed_row())

    def _first_event_id(self) -> int:
        return self.connection.execute("SELECT id FROM events").fetchone()[0]

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _token(self) -> str:
        return issue_token("admin", self.settings.admin_secret)

    def _request(self, path: str, method="GET", body=None, auth=True) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self._token()}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(self._url(path), data=data, headers=headers, method=method)
        try:
            with urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "null")
        except HTTPError as err:
            return err.code, json.loads(err.read().decode("utf-8") or "null")

    def test_login_valid(self):
        status, body = self._request("/api/login", method="POST", body={"user": "admin", "password": "pass"}, auth=False)
        self.assertEqual(status, 200)
        self.assertIn("token", body)
        self.assertEqual(verify_token(body["token"], "topsecret"), "admin")

    def test_login_invalid_credentials(self):
        status, _ = self._request("/api/login", method="POST", body={"user": "admin", "password": "bad"}, auth=False)
        self.assertEqual(status, 401)

    def test_login_missing_fields(self):
        status, _ = self._request("/api/login", method="POST", body={"user": "admin"}, auth=False)
        self.assertEqual(status, 400)

    def test_list_requires_auth(self):
        status, body = self._request("/api/events", auth=False)
        self.assertEqual(status, 401)

    def test_list_drafts(self):
        status, body = self._request("/api/events?status=draft")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "draft")
        self.assertEqual(len(body["items"]), 1)

    def test_list_invalid_status(self):
        status, _ = self._request("/api/events?status=bogus")
        self.assertEqual(status, 400)

    def test_detail_found(self):
        event_id = self.connection.execute("SELECT id FROM events").fetchone()[0]
        status, body = self._request(f"/api/events/{event_id}")
        self.assertEqual(status, 200)
        self.assertEqual(body["title"], "Винил-маркет")
        self.assertEqual(body["source"], "events_vrn")

    def test_detail_missing(self):
        status, _ = self._request("/api/events/999999")
        self.assertEqual(status, 404)

    def test_update_event(self):
        event_id = self.connection.execute("SELECT id FROM events").fetchone()[0]
        status, body = self._request(f"/api/events/{event_id}", method="POST", body={"title": "Новое", "status": "published"})
        self.assertEqual(status, 200)
        title = self.connection.execute("SELECT title FROM events WHERE id=?", (event_id,)).fetchone()[0]
        self.assertEqual(title, "Новое")

    def test_update_bad_category(self):
        event_id = self.connection.execute("SELECT id FROM events").fetchone()[0]
        status, body = self._request(f"/api/events/{event_id}", method="POST", body={"category": "noise"})
        self.assertEqual(status, 400)

    def test_update_bad_status(self):
        event_id = self.connection.execute("SELECT id FROM events").fetchone()[0]
        status, _ = self._request(f"/api/events/{event_id}", method="POST", body={"status": "repeat"})
        self.assertEqual(status, 400)

    def test_update_missing(self):
        status, _ = self._request("/api/events/999999", method="POST", body={"title": "x"})
        self.assertEqual(status, 404)

    def test_delete_event(self):
        event_id = self.connection.execute("SELECT id FROM events").fetchone()[0]
        status, _ = self._request(f"/api/events/{event_id}/delete", method="POST", body={})
        self.assertEqual(status, 200)
        count = self.connection.execute("SELECT COUNT(*) FROM events WHERE id=?", (event_id,)).fetchone()[0]
        self.assertEqual(count, 0)

    def test_delete_missing(self):
        status, _ = self._request("/api/events/999999/delete", method="POST", body={})
        self.assertEqual(status, 404)

    def test_update_ignores_protected_fields(self):
        event_id = self.connection.execute("SELECT id FROM events").fetchone()[0]
        original_conf = self.connection.execute("SELECT confidence FROM events WHERE id=?", (event_id,)).fetchone()[0]
        status, body = self._request(
            f"/api/events/{event_id}", method="POST",
            body={"confidence": 0.01, "reason": "взлом", "is_event": False, "status": "published"}
        )
        self.assertEqual(status, 200)
        conf = self.connection.execute("SELECT confidence FROM events WHERE id=?", (event_id,)).fetchone()[0]
        self.assertEqual(conf, original_conf)


class ServeRequiresCredentialsTest(unittest.TestCase):
    def test_settings_store_admin_fields(self):
        s = default_settings(admin_user="u", admin_password="p", admin_secret="s")
        self.assertEqual(s.admin_user, "u")
        self.assertEqual(s.admin_password, "p")
        self.assertEqual(s.admin_secret, "s")
        self.assertEqual(s.admin_port, 8080)
        self.assertEqual(str(s.admin_static_dir), "admin-ui/dist")


if __name__ == "__main__":
    unittest.main()