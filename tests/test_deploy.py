from __future__ import annotations

import gzip
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy.backup import backup_database
from deploy.check_config import main as check_config


class DeploymentBackupTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "events.sqlite3"
        self.directory = self.root / "backups"
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE watch_progress (source TEXT, position INTEGER)")
            connection.execute("INSERT INTO watch_progress VALUES ('channel', 42)")

    def test_compressed_backup_can_be_restored_with_progress(self):
        path = backup_database(self.database, self.directory)
        restored = self.root / "restored.sqlite3"
        with gzip.open(path, "rb") as source:
            restored.write_bytes(source.read())
        with sqlite3.connect(restored) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT * FROM watch_progress").fetchone(), ("channel", 42))

    def test_retention_keeps_seven_backups_and_unrelated_files(self):
        self.directory.mkdir()
        unrelated = self.directory / "notes.txt"
        unrelated.write_text("keep", encoding="utf-8")
        first = backup_database(self.database, self.directory)
        for _ in range(7):
            backup_database(self.database, self.directory)
        self.assertEqual(len(list(self.directory.glob("*.sqlite3.gz"))), 7)
        self.assertFalse(first.exists())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_missing_database_does_not_create_an_empty_database(self):
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            backup_database(missing, self.directory)
        self.assertFalse(missing.exists())
        self.assertEqual(list(self.directory.iterdir()), [])


class WatcherConfigurationTest(unittest.TestCase):
    def test_placeholder_credentials_prevent_start(self):
        values = {
            "TELEGRAM_API_ID": "1",
            "TELEGRAM_API_HASH": "NOT_CONFIGURED",
            "TELEGRAM_PHONE": "NOT_CONFIGURED",
            "OPENROUTER_API_KEY": "NOT_CONFIGURED",
            "BOT_TOKEN": "",
        }
        with patch.dict("os.environ", values, clear=True), patch("deploy.check_config.load_dotenv"):
            with self.assertRaises(SystemExit) as error:
                check_config()
        self.assertIn("BOT_TOKEN", str(error.exception))
        self.assertIn("TELEGRAM_API_HASH", str(error.exception))
        self.assertNotIn("NOT_CONFIGURED", str(error.exception))
