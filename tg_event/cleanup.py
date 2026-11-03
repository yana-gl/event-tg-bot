from __future__ import annotations

import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def _local_date(value: str, zone: ZoneInfo) -> date:
    if len(value) == 10:
        return date.fromisoformat(value)
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).astimezone(zone).date()


def cleanup_plan(connection: sqlite3.Connection, raw_dir: Path, parsed_dir: Path,
                 now: datetime, timezone_name: str) -> dict:
    zone = ZoneInfo(timezone_name)
    today = now.astimezone(zone).date()
    cutoff = today - timedelta(days=30)
    events = []
    for row in connection.execute("SELECT * FROM events"):
        event = dict(row)
        try:
            state = event.get("event_state", "scheduled")
            end_value = event["end_date"] or event["date"]
            end = _local_date(end_value, zone) if end_value else None
            if state != "postponed" and end is not None and end >= today:
                continue
            changed = _local_date(event.get("updated_at") or event["created_at"], zone)
            confirmed = _local_date(event["last_change_at"], zone) if event.get("last_change_at") else changed
            if state == "postponed":
                anchor = max(changed, confirmed)
            elif state == "cancelled":
                anchor = max(d for d in (end, confirmed, changed) if d is not None)
            elif end is None or event["status"] != "published":
                anchor = max(d for d in (end, changed) if d is not None)
            else:
                anchor = end
        except (ValueError, TypeError):
            continue
        if anchor <= cutoff:
            events.append({"id": event["id"], "title": event["title"], "status": event["status"],
                           "event_state": state, "date": event["date"], "anchor": anchor.isoformat()})

    deleted = {e["id"] for e in events}
    protected = set()
    for row in connection.execute("SELECT * FROM events"):
        event = dict(row)
        if event["id"] not in deleted:
            protected.update(event.get(k) for k in ("raw_post_id", "last_change_post_id", "conflict_post_id"))
    for table in ("event_sources", "event_conflict_resolutions"):
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            protected.update(r[1] for r in connection.execute(f"SELECT event_id, raw_post_id FROM {table}")
                             if r[0] not in deleted)
    progress = {}
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='watch_progress'").fetchone():
        progress = {r[0]: r[1] for r in connection.execute(
            "SELECT source, MIN(last_message_id) FROM watch_progress GROUP BY source")}
    posts = []
    for row in connection.execute(
        "SELECT p.id, p.message_id, p.published_at, s.username AS source FROM raw_posts p JOIN sources s ON s.id=p.source_id"
    ):
        if row["id"] in protected or row["message_id"] > progress.get(row["source"], row["message_id"]):
            continue
        try:
            old = _local_date(row["published_at"], zone) <= cutoff
        except (ValueError, TypeError):
            old = False
        if old:
            posts.append(dict(row))
    files = []
    seen = set()
    for label, directory in (("raw", raw_dir), ("parsed", parsed_dir)):
        for path in sorted(directory.glob("*.jsonl")):
            if path.is_symlink() or not path.is_file() or path.resolve() in seen:
                continue
            seen.add(path.resolve())
            modified = datetime.fromtimestamp(path.stat().st_mtime, zone).date()
            if modified <= cutoff:
                files.append({"path": str(path.resolve()), "archive_name": f"{label}/{path.name}"})
    return {"cutoff": cutoff.isoformat(), "timezone": timezone_name, "events": events, "posts": posts, "files": files}


def _backup(database_path: Path, backup_path: Path, plan: dict) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=backup_path.parent) as folder:
        snapshot = Path(folder) / "database.sqlite3"
        with closing(sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination)
        archive = Path(folder) / "cleanup.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.write(snapshot, "database.sqlite3")
            output.writestr("manifest.json", json.dumps(plan, ensure_ascii=False, indent=2))
            for item in plan["files"]:
                output.write(item["path"], item["archive_name"])
        archive.replace(backup_path)


def run_cleanup(database_path: Path, raw_dir: Path, parsed_dir: Path,
                timezone_name: str = "Asia/Vladivostok", *, apply: bool = False,
                weekly: bool = False, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Cleanup time must include a timezone")
    if not database_path.exists():
        raise FileNotFoundError("Database does not exist; cleanup did not create it")
    uri = database_path.resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if apply:
            connection.execute("BEGIN IMMEDIATE")
        if weekly and connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='maintenance_state'"
        ).fetchone():
            previous = connection.execute("SELECT completed_at FROM maintenance_state WHERE name='cleanup'").fetchone()
            if previous and now - datetime.fromisoformat(previous[0]) < timedelta(days=7):
                return {"skipped": True}
        plan = cleanup_plan(connection, raw_dir, parsed_dir, now, timezone_name)
        if not apply:
            return plan
        backup_path = database_path.parent / "backups" / "cleanup-latest.zip"
        if any(plan[k] for k in ("events", "posts", "files")):
            _backup(database_path, backup_path, plan)
            connection.executemany("DELETE FROM events WHERE id = ?", [(e["id"],) for e in plan["events"]])
            connection.executemany("DELETE FROM raw_posts WHERE id = ?", [(p["id"],) for p in plan["posts"]])
            for item in plan["files"]:
                Path(item["path"]).unlink()
            plan["backup"] = str(backup_path.resolve())
        elif backup_path.exists() and now - datetime.fromtimestamp(backup_path.stat().st_mtime, timezone.utc) >= timedelta(days=7):
            backup_path.unlink()
        connection.execute("CREATE TABLE IF NOT EXISTS maintenance_state (name TEXT PRIMARY KEY, completed_at TEXT NOT NULL)")
        connection.execute(
            """INSERT INTO maintenance_state (name, completed_at) VALUES ('cleanup', ?)
            ON CONFLICT(name) DO UPDATE SET completed_at=excluded.completed_at""", (now.isoformat(),),
        )
    plan["applied"] = True
    return plan
