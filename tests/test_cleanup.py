from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_deduplication import post
from tg_event.cleanup import run_cleanup
from tg_event.watcher import watch_cycle
from tg_event.cli import main
from tg_event.database import connect_database, init_database, save_parsed_row, save_parsed_rows, update_event


class CleanupTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "db.sqlite3"
        self.raw = self.root / "raw"
        self.parsed = self.root / "parsed"
        self.raw.mkdir()
        self.parsed.mkdir()
        self.now = datetime(2026, 8, 11, 2, tzinfo=timezone.utc)
        self.db = connect_database(self.path)
        self.addCleanup(self.db.close)
        init_database(self.db)

    def event(self, message_id=1, *, state="scheduled", changed="2026-07-01T00:00:00+00:00",
              confirmed=None, **fields):
        row = post(message_id)
        row["published_at"] = "2026-07-01T00:00:00+00:00"
        row["events"][0].update(fields)
        save_parsed_row(self.db, row)
        event_id = self.db.execute("SELECT MAX(id) FROM events").fetchone()[0]
        with self.db:
            self.db.execute("UPDATE events SET event_state=?, last_change_at=? WHERE id=?", (state, confirmed, event_id))
            self.db.execute("UPDATE events SET created_at=?, updated_at=? WHERE id=?", (changed, changed, event_id))
        return event_id, row

    def run_cleanup(self, **kwargs):
        return run_cleanup(self.path, self.raw, self.parsed, now=kwargs.pop("now", self.now), **kwargs)

    def old_file(self, directory=None, name="posts-old.jsonl"):
        path = (directory or self.raw) / name
        path.write_text('{"text":"archive me"}\n')
        stamp = (self.now - timedelta(days=31)).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def test_preview_is_read_only_and_lists_records_and_files(self):
        event_id, _ = self.event()
        old = self.old_file()
        before = self.path.read_bytes()
        plan = self.run_cleanup()
        self.assertEqual([e["id"] for e in plan["events"]], [event_id])
        self.assertEqual(len(plan["posts"]), 1)
        self.assertEqual(plan["files"][0]["path"], str(old))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(old.exists())
        self.assertFalse((self.root / "backups").exists())

    def test_apply_backs_up_database_and_files_before_deleting(self):
        self.event()
        old = self.old_file()
        plan = self.run_cleanup(apply=True)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0], 0)
        self.assertFalse(old.exists())
        with zipfile.ZipFile(plan["backup"]) as archive:
            self.assertIn("archive me", archive.read("raw/posts-old.jsonl").decode())
            restored = self.root / "restored.sqlite3"
            restored.write_bytes(archive.read("database.sqlite3"))
            with sqlite3.connect(restored) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(len(json.loads(archive.read("manifest.json"))["events"]), 1)

    def test_multiday_future_event_and_shared_digest_post_are_preserved(self):
        _, row = self.event()
        row["events"].append(dict(row["events"][0], title="Future", date="2026-12-01"))
        row["events"].append(dict(row["events"][0], title="Ongoing", date="2026-07-01", end_date="2026-08-20"))
        save_parsed_rows(self.db, [row])
        plan = self.run_cleanup(apply=True)
        self.assertEqual(len(plan["events"]), 1)
        self.assertEqual(plan["posts"], [])
        self.assertEqual({r[0] for r in self.db.execute("SELECT title FROM events")}, {"Future", "Ongoing"})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0], 1)

    def test_thirtieth_day_boundary_uses_vladivostok_midnight(self):
        event_id, _ = self.event()
        before = datetime(2026, 8, 10, 13, 59, 59, tzinfo=timezone.utc)
        after = before + timedelta(seconds=1)
        self.assertEqual(self.run_cleanup(now=before)["events"], [])
        self.assertEqual([e["id"] for e in self.run_cleanup(now=after)["events"]], [event_id])

    def test_cancel_uses_later_confirmation_and_preserves_future_dates(self):
        old_id, _ = self.event(state="cancelled", date="2026-07-01", confirmed="2026-07-02T00:00:00+00:00")
        self.event(2, state="cancelled", date="2026-07-01", confirmed="2026-08-01T00:00:00+00:00")
        self.event(3, state="cancelled", date="2026-12-01")
        no_date_id, _ = self.event(4, state="cancelled", date=None, confirmed="2026-07-01T00:00:00+00:00")
        self.assertEqual({e["id"] for e in self.run_cleanup()["events"]}, {old_id, no_date_id})

    def test_postponement_and_undated_draft_use_meaningful_update(self):
        old_id, row = self.event(state="postponed", date=None, confirmed="2026-07-02T00:00:00+00:00")
        self.event(2, state="postponed", date=None, changed="2026-08-01T00:00:00+00:00")
        draft_id, _ = self.event(3, date=None, status="draft")
        self.event(4, date="2026-12-01", status="rejected")
        save_parsed_rows(self.db, [row])
        self.assertEqual({e["id"] for e in self.run_cleanup()["events"]}, {old_id, draft_id})
        update_event(self.db, old_id, {"price": "Updated"})
        self.assertEqual({e["id"] for e in self.run_cleanup()["events"]}, {draft_id})

    def test_postponed_old_date_is_not_future_but_future_draft_end_is_protected(self):
        postponed_id, _ = self.event(state="postponed", date="2026-12-01")
        self.event(2, date=None, end_date="2026-12-01", status="draft")
        self.assertEqual([e["id"] for e in self.run_cleanup()["events"]], [postponed_id])

    def test_noop_reparse_and_initialization_do_not_extend_retention(self):
        event_id, row = self.event(date=None, status="draft")
        save_parsed_rows(self.db, [row])
        init_database(self.db)
        updated = self.db.execute("SELECT updated_at FROM events WHERE id=?", (event_id,)).fetchone()[0]
        self.assertEqual(updated, "2026-07-01T00:00:00+00:00")
        self.assertEqual(len(self.run_cleanup()["events"]), 1)

    def test_updated_at_is_initialized_for_existing_schema_without_resetting_age(self):
        with sqlite3.connect(":memory:") as legacy:
            legacy.row_factory = sqlite3.Row
            legacy.executescript("""
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY, raw_post_id INTEGER NOT NULL, is_event INTEGER NOT NULL,
                    title TEXT, date TEXT, end_date TEXT, time TEXT, end_time TEXT, place TEXT,
                    address TEXT, category TEXT, price TEXT, confidence REAL, reason TEXT, status TEXT,
                    created_at TEXT NOT NULL
                );
                INSERT INTO events (id, raw_post_id, is_event, created_at)
                VALUES (1, 1, 1, '2026-01-01 00:00:00');
            """)
            init_database(legacy)
            init_database(legacy)
            self.assertEqual(legacy.execute("SELECT updated_at FROM events").fetchone()[0], "2026-01-01 00:00:00")

    def test_orphan_posts_ahead_of_any_watch_cursor_are_not_reprocessed(self):
        _, row = self.event()
        row["events"] = []
        row["message_id"] = 2
        save_parsed_row(self.db, row)
        with self.db:
            self.db.executemany("INSERT INTO watch_progress VALUES (?, 'events_vrn', ?, '2026-07-01')",
                                [("profile1", 2), ("profile2", 0)])
        plan = self.run_cleanup(apply=True)
        self.assertEqual(plan["posts"], [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM watch_progress").fetchone()[0], 2)
        with self.db:
            self.db.execute("UPDATE watch_progress SET last_message_id=2")
        self.assertEqual(len(self.run_cleanup()["posts"]), 2)

    def test_related_records_are_cascaded_but_progress_remains(self):
        event_id, _ = self.event()
        raw_id = self.db.execute("SELECT id FROM raw_posts").fetchone()[0]
        with self.db:
            self.db.execute("INSERT INTO event_field_locks (event_id,field) VALUES (?, 'title')", (event_id,))
            self.db.execute("INSERT INTO event_conflict_resolutions VALUES (?, ?, '2026-07-01')", (event_id, raw_id))
            self.db.execute("INSERT INTO watch_progress VALUES ('profile', 'events_vrn', 10, '2026-07-01')")
        self.run_cleanup(apply=True)
        for table in ("events", "event_sources", "event_field_locks", "event_conflict_resolutions", "post_links"):
            self.assertEqual(self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT last_message_id FROM watch_progress").fetchone()[0], 10)

    def test_backup_failure_prevents_any_deletion(self):
        self.event()
        old = self.old_file()
        with patch("tg_event.cleanup._backup", side_effect=OSError("no space")):
            with self.assertRaises(OSError):
                self.run_cleanup(apply=True)
        self.assertTrue(old.exists())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertIsNone(self.db.execute("SELECT completed_at FROM maintenance_state").fetchone())

    def test_database_failure_rolls_back_deletions_and_keeps_backup(self):
        self.event()
        self.db.execute("CREATE TRIGGER block_cleanup BEFORE DELETE ON raw_posts BEGIN SELECT RAISE(ABORT, 'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_cleanup(apply=True)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertTrue((self.root / "backups/cleanup-latest.zip").exists())

    def test_partial_file_failure_leaves_recoverable_archive_and_rolls_back_sql(self):
        self.event()
        first = self.old_file(name="posts-a.jsonl")
        second = self.old_file(name="posts-b.jsonl")
        unlink = Path.unlink

        def fail_second(path, *args, **kwargs):
            if path == second:
                raise OSError("cannot delete second file")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", autospec=True, side_effect=fail_second):
            with self.assertRaises(OSError):
                self.run_cleanup(apply=True)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertIsNone(self.db.execute("SELECT completed_at FROM maintenance_state").fetchone())
        with zipfile.ZipFile(self.root / "backups/cleanup-latest.zip") as backup:
            self.assertIn("archive me", backup.read("raw/posts-a.jsonl").decode())

    def test_weekly_schedule_and_expiration_of_last_backup(self):
        self.event()
        first = self.run_cleanup(apply=True, weekly=True)
        archive = Path(first["backup"])
        os.utime(archive, (self.now.timestamp(), self.now.timestamp()))
        self.assertTrue(self.run_cleanup(apply=True, weekly=True, now=self.now + timedelta(days=6))["skipped"])
        self.run_cleanup(apply=True, weekly=True, now=self.now + timedelta(days=7))
        self.assertFalse(archive.exists())
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_only_old_regular_jsonl_exports_are_removed(self):
        old = self.old_file()
        parsed = self.old_file(self.parsed, "events-old.jsonl")
        recent = self.raw / "posts-new.jsonl"
        recent.write_text("[]")
        stamp = self.now.timestamp()
        os.utime(recent, (stamp, stamp))
        other = self.raw / "state.json"
        other.write_text("{}")
        link = self.raw / "linked.jsonl"
        link.symlink_to(old)
        plan = self.run_cleanup(apply=True)
        self.assertEqual({Path(f["path"]) for f in plan["files"]}, {old, parsed})
        self.assertTrue(recent.exists())
        self.assertTrue(other.exists())
        self.assertTrue(link.is_symlink())

    def test_cli_preview_needs_no_telegram_or_llm_credentials(self):
        self.event()
        with patch.dict(os.environ, {"DATABASE_PATH": str(self.path), "RAW_DIR": str(self.raw),
                                     "PARSED_DIR": str(self.parsed)}, clear=True), \
                patch("tg_event.cli.load_dotenv"), patch("sys.argv", ["tg_event", "cleanup"]), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            main()
        self.assertIn("events", json.loads(output.getvalue()))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_missing_database_is_not_created(self):
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(FileNotFoundError):
            run_cleanup(missing, self.raw, self.parsed)
        self.assertFalse(missing.exists())


class CleanupWatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_watch_runs_weekly_cleanup_without_telegram_or_llm(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = SimpleNamespace(database_path=root / "db.sqlite3", raw_dir=root / "raw",
                                       parsed_dir=root / "parsed", source_rules_path=root / "rules.json",
                                       event_timezone="Asia/Vladivostok")
            with connect_database(settings.database_path) as db:
                init_database(db)
                row = post(1, date="2020-01-01")
                row["published_at"] = "2020-01-01T00:00:00+00:00"
                save_parsed_row(db, row)
            settings.raw_dir.mkdir()
            raw_path = settings.raw_dir / "posts-empty.jsonl"
            raw_path.write_text("")
            with patch("tg_event.watcher.collect_to_file", new=AsyncMock(return_value=raw_path)), \
                    patch("tg_event.watcher.parse_rows") as parser, patch("builtins.print"), \
                    patch("tg_event.watcher.run_cleanup", wraps=run_cleanup) as cleanup:
                for _ in range(2):
                    failed = await watch_cycle(settings, "2026-01-01", root / "state.json", None, 10)
                    self.assertFalse(failed)
                parser.assert_not_called()
                self.assertEqual(cleanup.call_count, 2)
            with connect_database(settings.database_path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM maintenance_state WHERE name='cleanup'").fetchone()[0], 1)
            self.assertTrue((root / "backups/cleanup-latest.zip").exists())
