from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_admin import default_settings
from tg_event.admin import AdminHandler
from tg_event.auth import LoginLimiter


class LoginLimiterTest(unittest.TestCase):
    def test_attempt_limit_is_per_client_and_expires(self):
        limiter = LoginLimiter()
        with patch("tg_event.auth.time.monotonic", return_value=100):
            self.assertTrue(all(limiter.allow("one") for _ in range(5)))
            self.assertFalse(limiter.allow("one"))
            self.assertTrue(limiter.allow("two"))
        with patch("tg_event.auth.time.monotonic", return_value=160):
            self.assertTrue(limiter.allow("one"))

    def test_parallel_attempts_cannot_bypass_limit(self):
        limiter = LoginLimiter()
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(limiter.allow, ["one"] * 30))
        self.assertEqual(sum(results), 5)

    def test_client_cache_is_bounded_and_recovers_after_expiry(self):
        limiter = LoginLimiter()
        with patch("tg_event.auth.time.monotonic", return_value=100):
            self.assertTrue(all(limiter.allow(str(i)) for i in range(1024)))
            self.assertFalse(limiter.allow("new"))
        with patch("tg_event.auth.time.monotonic", return_value=160):
            self.assertTrue(limiter.allow("new"))


class LoginSecurityTest(unittest.TestCase):
    def setUp(self):
        self.handler = SimpleNamespace(
            settings=default_settings(admin_user="admin", admin_password="test", admin_secret="test-secret"),
            client_address=("127.0.0.1", 1234), headers={}, login_limiter=LoginLimiter(),
            _read_body=Mock(), _send_json=Mock(),
        )

    def login(self, payload):
        self.handler._read_body.return_value = payload
        self.handler._send_json.reset_mock()
        AdminHandler._handle_login(self.handler)
        return self.handler._send_json.call_args

    def test_repeated_bad_passwords_are_blocked_before_reading_body(self):
        for _ in range(5):
            self.assertEqual(self.login({"user": "admin", "password": "bad"}).kwargs["status"], 401)
        self.assertEqual(self.login({"user": "admin", "password": "test"}).kwargs["status"], 429)
        self.assertEqual(self.handler._read_body.call_count, 5)

    def test_successful_login_resets_failed_attempts(self):
        for _ in range(4):
            self.login({"user": "admin", "password": "bad"})
        self.assertIn("token", self.login({"user": "admin", "password": "test"}).args[0])
        self.assertEqual(self.login({"user": "admin", "password": "bad"}).kwargs["status"], 401)

    def test_malformed_login_payloads_are_rejected(self):
        for payload in ([], None, {"user": ["admin"], "password": "test"}, {"user": "admin", "password": 1}):
            with self.subTest(payload=payload):
                self.assertEqual(self.login(payload).kwargs["status"], 400)

    def test_proxy_header_is_only_used_for_local_connections(self):
        self.handler.login_limiter = Mock()
        self.handler.login_limiter.allow.return_value = False
        self.handler.headers = {"X-Real-IP": "192.0.2.1"}
        self.login({})
        self.handler.login_limiter.allow.assert_called_with("192.0.2.1")
        self.handler.client_address = ("198.51.100.1", 1234)
        self.login({})
        self.handler.login_limiter.allow.assert_called_with("198.51.100.1")
