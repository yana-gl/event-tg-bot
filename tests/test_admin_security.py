from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_admin import default_settings
from tg_event.admin import AdminHandler
from tg_event.cli import _serve


class StaticSecurityTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.static = self.root / "dist"
        self.static.mkdir()
        (self.static / "index.html").write_text("admin", encoding="utf-8")
        (self.root / "private.txt").write_text("outside static directory", encoding="utf-8")
        self.handler = SimpleNamespace(
            static_dir=self.static, _send_file=Mock(), _send_text=Mock(),
        )

    def test_parent_paths_are_forbidden(self):
        for path in ("/../private.txt", "/%2e%2e/private.txt", "/..%2fprivate.txt"):
            with self.subTest(path=path):
                self.handler._send_file.reset_mock()
                self.handler._send_text.reset_mock()
                AdminHandler._serve_static(self.handler, path)
                self.handler._send_file.assert_not_called()
                self.handler._send_text.assert_called_once_with("Forbidden", status=403)

    def test_symlink_outside_static_directory_is_forbidden(self):
        (self.static / "linked.txt").symlink_to(self.root / "private.txt")
        AdminHandler._serve_static(self.handler, "/linked.txt")
        self.handler._send_file.assert_not_called()
        self.handler._send_text.assert_called_once_with("Forbidden", status=403)

    def test_directory_index_cannot_follow_symlink_outside(self):
        directory = self.static / "nested"
        directory.mkdir()
        (directory / "index.html").symlink_to(self.root / "private.txt")
        AdminHandler._serve_static(self.handler, "/nested/")
        self.handler._send_file.assert_not_called()
        self.handler._send_text.assert_called_once_with("Forbidden", status=403)

    def test_spa_fallback_cannot_follow_symlink_outside(self):
        (self.static / "index.html").unlink()
        (self.static / "index.html").symlink_to(self.root / "private.txt")
        AdminHandler._serve_static(self.handler, "/events/123")
        self.handler._send_file.assert_not_called()

    def test_assets_and_spa_routes_still_work(self):
        asset = self.static / "app.js"
        asset.write_text("app", encoding="utf-8")
        for path, expected in (("/app.js", asset), ("/events/123", self.static / "index.html")):
            with self.subTest(path=path):
                self.handler._send_file.reset_mock()
                AdminHandler._serve_static(self.handler, path)
                self.handler._send_file.assert_called_once_with(expected.resolve())


class AdminConfigurationSecurityTest(unittest.TestCase):
    def test_server_requires_secret_before_opening_database(self):
        for secret in ("", "   "):
            with self.subTest(secret=secret), patch("tg_event.cli.connect_database") as connect:
                settings = default_settings(admin_user="admin", admin_password="test", admin_secret=secret)
                with self.assertRaisesRegex(SystemExit, "ADMIN_SECRET"):
                    _serve(settings, "127.0.0.1", 8080, False)
                connect.assert_not_called()
