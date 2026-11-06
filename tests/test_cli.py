from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tg_event.cli import _serve, main
from tg_event.database import connect_database


class CliTest(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(admin_host="127.0.0.1", admin_port=8080)
        settings = patch("tg_event.cli.Settings.from_env", return_value=self.settings)
        self.read_settings = settings.start()
        self.addCleanup(settings.stop)

    def run_command(self, *arguments):
        with patch("sys.argv", ["tg-event", *arguments]), patch("sys.stdout", new_callable=io.StringIO) as output:
            main()
            return output.getvalue()

    def test_collect_prints_the_export_path(self):
        with patch("tg_event.cli.collect_to_file", AsyncMock(return_value=Path("raw.jsonl"))) as collect:
            self.assertEqual(self.run_command("collect"), "raw.jsonl\n")
        collect.assert_awaited_once_with(self.settings)

    def test_parse_and_run_keep_input_and_limit(self):
        for arguments in (("parse", "--input", "raw.jsonl", "--limit", "2"), ("run", "--limit", "2")):
            with self.subTest(command=arguments[0]), patch(
                "tg_event.cli.collect_to_file", AsyncMock(return_value=Path("raw.jsonl")),
            ) as collect, patch("tg_event.cli.parse_file", return_value=Path("parsed.jsonl")) as parse:
                self.assertEqual(self.run_command(*arguments), "parsed.jsonl\n")
                parse.assert_called_once_with(self.settings, Path("raw.jsonl"), limit=2)
                self.assertEqual(collect.await_count, int(arguments[0] == "run"))

    def test_parse_requires_input(self):
        with self.assertRaisesRegex(SystemExit, "--input is required"):
            self.run_command("parse")

    def test_watch_passes_the_existing_options(self):
        with patch("tg_event.cli.run_watch", AsyncMock()) as watch:
            self.run_command("watch", "--once", "--interval", "60", "--since", "2026-11-01",
                             "--state", "custom.json", "--max-posts-per-cycle", "7",
                             "--max-posts-per-channel", "3")
        watch.assert_awaited_once_with(self.settings, interval=60, once=True, since_date="2026-11-01",
                                       state_path=Path("custom.json"), max_posts_per_cycle=7,
                                       max_posts_per_channel=3)

    def test_serve_keeps_defaults_and_overrides(self):
        for arguments, expected in (
            (("serve",), dict(host="127.0.0.1", port=8080, dev=False)),
            (("serve", "--host", "localhost", "--port", "9090", "--dev"),
             dict(host="localhost", port=9090, dev=True)),
        ):
            with self.subTest(arguments=arguments), patch("tg_event.cli._serve") as serve:
                self.run_command(*arguments)
                serve.assert_called_once_with(self.settings, **expected)

    def test_help_does_not_load_credentials(self):
        with self.assertRaises(SystemExit) as stopped:
            self.run_command("--help")
        self.assertEqual(stopped.exception.code, 0)
        self.read_settings.assert_not_called()

    def test_admin_startup_closes_setup_connection_before_serving(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = SimpleNamespace(database_path=Path(folder) / "db.sqlite3", admin_user="admin",
                                       admin_password="fake", admin_secret="fake", admin_static_dir=Path(folder))
            setup_connection = connect_database(settings.database_path)
            self.addCleanup(setup_connection.close)

            def serve(config, **options):
                self.assertIs(config, settings)
                with self.assertRaises(sqlite3.ProgrammingError):
                    setup_connection.execute("SELECT 1")

            with patch("tg_event.cli.connect_database", return_value=setup_connection), \
                    patch("tg_event.admin.serve", side_effect=serve) as server:
                _serve(settings, "127.0.0.1", 8080, True)
            server.assert_called_once()
