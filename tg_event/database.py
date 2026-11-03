from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from tg_event.event_updates import apply_event_update, change_timestamp
from tg_event.source_policy import select_primary_source
from tg_event.event_schema import _valid_event_dates


def connect_database(path: Path, check_same_thread: bool = True) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=check_same_thread)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            city TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS raw_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            message_id INTEGER NOT NULL,
            published_at TEXT NOT NULL,
            city TEXT NOT NULL,
            url TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, message_id)
        );

        CREATE TABLE IF NOT EXISTS post_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_post_id INTEGER NOT NULL REFERENCES raw_posts(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            UNIQUE(raw_post_id, url)
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_post_id INTEGER NOT NULL REFERENCES raw_posts(id) ON DELETE CASCADE,
            is_event INTEGER NOT NULL,
            title TEXT,
            date TEXT,
            end_date TEXT,
            time TEXT,
            end_time TEXT,
            place TEXT,
            address TEXT,
            category TEXT,
            price TEXT,
            confidence REAL NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS event_sources (
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            raw_post_id INTEGER NOT NULL REFERENCES raw_posts(id) ON DELETE CASCADE,
            extracted_json TEXT,
            PRIMARY KEY (event_id, raw_post_id)
        );

        CREATE TABLE IF NOT EXISTS event_field_locks (
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            field TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT 'manual',
            PRIMARY KEY (event_id, field)
        );

        CREATE TABLE IF NOT EXISTS watch_progress (
            state_key TEXT NOT NULL,
            source TEXT NOT NULL,
            last_message_id INTEGER NOT NULL,
            since_date TEXT NOT NULL,
            PRIMARY KEY (state_key, source)
        );

        CREATE TABLE IF NOT EXISTS event_conflict_resolutions (
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            raw_post_id INTEGER NOT NULL REFERENCES raw_posts(id) ON DELETE CASCADE,
            version_at TEXT NOT NULL,
            PRIMARY KEY (event_id, raw_post_id)
        );

        CREATE TABLE IF NOT EXISTS channel_settings (
            username TEXT PRIMARY KEY,
            initial_posts INTEGER NOT NULL DEFAULT 20,
            enabled INTEGER NOT NULL DEFAULT 1,
            mode TEXT NOT NULL DEFAULT 'recent',
            initial_start_id INTEGER,
            initial_end_id INTEGER,
            last_attempt_at TEXT,
            last_success_at TEXT,
            last_error TEXT
        );

        INSERT OR IGNORE INTO event_sources (event_id, raw_post_id)
        SELECT id, raw_post_id FROM events;

        DROP VIEW IF EXISTS events_for_review;
        CREATE VIEW events_for_review AS
        SELECT * FROM events WHERE status = 'draft' ORDER BY id DESC;

        DROP VIEW IF EXISTS events_current;
        CREATE VIEW events_current AS
        SELECT * FROM events
        WHERE status = 'published' AND date IS NOT NULL AND date >= date('now')
        ORDER BY date, time;

        DROP VIEW IF EXISTS events_outdated;
        CREATE VIEW events_outdated AS
        SELECT * FROM events
        WHERE date IS NOT NULL AND date < date('now')
        ORDER BY date DESC;
        """
    )
    for table, additions in {
        "raw_posts": {"edited_at": "TEXT"},
        "event_sources": {"version_at": "TEXT"},
        "events": {
            "updated_at": "TEXT",
            "event_state": "TEXT NOT NULL DEFAULT 'scheduled'",
            "previous_date": "TEXT", "last_change_at": "TEXT",
            "last_change_role": "INTEGER NOT NULL DEFAULT 0",
            "last_change_post_id": "INTEGER REFERENCES raw_posts(id) ON DELETE SET NULL",
            "conflict_at": "TEXT",
            "conflict_post_id": "INTEGER REFERENCES raw_posts(id) ON DELETE SET NULL",
        },
    }.items():
        columns = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    connection.execute("DROP VIEW IF EXISTS events_current")
    connection.execute("UPDATE events SET updated_at = created_at WHERE updated_at IS NULL")
    changed = " OR ".join(f"NEW.{field} IS NOT OLD.{field}" for field in ADMIN_EDITABLE_FIELDS)
    connection.executescript(f"""
        CREATE TRIGGER IF NOT EXISTS events_inserted_at AFTER INSERT ON events
        WHEN NEW.updated_at IS NULL BEGIN
            UPDATE events SET updated_at = NEW.created_at WHERE id = NEW.id;
        END;
        CREATE TRIGGER IF NOT EXISTS events_changed_at AFTER UPDATE ON events
        WHEN {changed} BEGIN
            UPDATE events SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
        END;
        CREATE TABLE IF NOT EXISTS maintenance_state (
            name TEXT PRIMARY KEY,
            completed_at TEXT NOT NULL
        );
    """)
    connection.execute("""
        UPDATE event_sources SET version_at = (
            SELECT COALESCE(edited_at, published_at) FROM raw_posts WHERE id = raw_post_id
        ) WHERE version_at IS NULL
    """)
    connection.execute("""
        CREATE VIEW events_current AS SELECT * FROM events
        WHERE status = 'published' AND event_state = 'scheduled'
          AND date IS NOT NULL AND date >= date('now') ORDER BY date, time
    """)
    for field in ADMIN_EDITABLE_FIELDS:
        connection.execute(
            """
            INSERT OR IGNORE INTO event_field_locks (event_id, field, reason)
            SELECT event_id, ?, 'legacy' FROM event_sources WHERE extracted_json IS NULL
            """, (field,),
        )
    connection.commit()


def save_parsed_row(
    connection: sqlite3.Connection, row: dict[str, Any],
    *, _batch_ids: dict[int, int] | None = None,
    source_rules: dict | None = None,
    _created_ids: set[int] | None = None,
) -> int:
    with (connection if _batch_ids is None else nullcontext()):
        source_id = _upsert_source(connection, row["source"], row.get("city") or "Воронеж")
        previous_post = connection.execute(
            "SELECT id, text, published_at, edited_at FROM raw_posts WHERE source_id = ? AND message_id = ?",
            (source_id, int(row["message_id"])),
        ).fetchone()
        if previous_post is not None and change_timestamp(row) < change_timestamp(dict(previous_post)):
            if _batch_ids is not None:
                for event in row.get("events", []):
                    matched = _match_saved_extraction(connection, previous_post["id"], event)
                    if matched is None and isinstance(event.get("_match_id"), int):
                        ref = event["_match_id"]
                        matched = _batch_ids.get(ref) if ref < 0 else ref
                    if matched is not None and "_batch_id" in event:
                        _batch_ids[event["_batch_id"]] = matched
            return previous_post["id"]
        is_edit = bool(previous_post is not None and row.get("edited_at") and (
            row["edited_at"] != previous_post["edited_at"]
            or (row.get("raw_text") or row.get("text")) != previous_post["text"]
        ))
        raw_post_id = _upsert_raw_post(connection, source_id, row)
        _replace_post_links(connection, raw_post_id, row.get("links", []))
        _save_events(connection, raw_post_id, row.get("events", []),
                     _batch_ids if _batch_ids is not None else {},
                     source_rules or {}, is_edit=is_edit, created_ids=_created_ids)
    return raw_post_id


def save_parsed_rows(
    connection: sqlite3.Connection, rows: list[dict[str, Any]],
    *, source_rules: dict | None = None,
    watch_state_key: str | None = None, fetched_rows: list[dict[str, Any]] | None = None,
    not_before: str | None = None,
) -> dict[str, int] | None:
    batch_ids: dict[int, int] = {}
    touched_ids: set[int] = set()
    created_ids: set[int] = set()
    with connection:
        for row in rows:
            post_id = save_parsed_row(connection, row, _batch_ids=batch_ids, source_rules=source_rules,
                                      _created_ids=created_ids)
            touched_ids.update(r[0] for r in connection.execute(
                "SELECT event_id FROM event_sources WHERE raw_post_id = ?", (post_id,)
            ))
        if not_before is not None:
            for event_id in created_ids:
                event = connection.execute("SELECT date, end_date, is_event FROM events WHERE id=?", (event_id,)).fetchone()
                if event["is_event"] and _valid_event_dates(event["date"], event["end_date"]) and (
                    event["end_date"] or event["date"]
                ) < not_before:
                    connection.execute("DELETE FROM events WHERE id=?", (event_id,))
                    touched_ids.discard(event_id)
        for event_id in touched_ids:
            detail = fetch_event_detail(connection, event_id)
            primary = select_primary_source(detail, source_rules or {})
            connection.execute("UPDATE events SET raw_post_id = ? WHERE id = ?", (primary, event_id))
        if watch_state_key is not None:
            return advance_watch_progress(connection, watch_state_key, fetched_rows or [])
    return None


def save_event_source(
    connection: sqlite3.Connection,
    event_id: int,
    raw_post_id: int,
    extracted_event: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO event_sources (event_id, raw_post_id, extracted_json, version_at)
        VALUES (?, ?, ?, (SELECT COALESCE(edited_at, published_at) FROM raw_posts WHERE id = ?))
        ON CONFLICT(event_id, raw_post_id) DO UPDATE SET
            extracted_json = excluded.extracted_json,
            version_at = excluded.version_at
        """,
        (event_id, raw_post_id, json.dumps(
            {k: v for k, v in extracted_event.items() if not k.startswith("_")},
            ensure_ascii=False), raw_post_id),
    )


def fetch_published_events(
    connection: sqlite3.Connection,
    date_from: str,
    date_to: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT e.title, e.date, e.end_date, e.time, e.end_time,
               e.place, e.address, e.category, e.price, p.url AS post_url,
               e.previous_date, e.last_change_at, cp.url AS last_change_url, xp.url AS conflict_url
        FROM events e
        JOIN raw_posts p ON p.id = e.raw_post_id
        LEFT JOIN raw_posts cp ON cp.id = e.last_change_post_id
        LEFT JOIN raw_posts xp ON xp.id = e.conflict_post_id
        WHERE e.status = 'published' AND e.date IS NOT NULL
          AND e.event_state = 'scheduled'
          AND COALESCE(e.end_date, e.date) >= ? AND e.date <= ?
        ORDER BY MAX(e.date, ?), e.time, e.date, e.id
        """,
        (date_from, date_to, date_from),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_candidate_events(
    connection: sqlite3.Connection,
    dates: list[str],
    *, updates_since: str | None = None,
) -> list[dict[str, Any]]:
    if not dates and updates_since is None:
        return []
    placeholders = ",".join("?" * len(dates))
    condition = f"e.date IN ({placeholders}) OR e.previous_date IN ({placeholders})" if dates else "0"
    values = list(dates) * 2
    if updates_since is None:
        condition += " OR e.last_change_at IS NOT NULL"
    if updates_since is not None:
        condition += " OR COALESCE(e.end_date, e.date) >= ? OR e.last_change_at >= ? OR (e.date IS NULL AND e.created_at >= ?)"
        values += [updates_since] * 3
    rows = connection.execute(
        f"""
        SELECT e.id, e.title, e.date, e.time, e.place, e.address, e.category,
               e.previous_date, e.event_state, e.last_change_at, e.last_change_post_id,
               s.username AS source, p.message_id
        FROM events e JOIN raw_posts p ON p.id = e.raw_post_id
        JOIN sources s ON s.id = p.source_id
        WHERE ({condition}) AND e.is_event = 1 AND e.status NOT IN ('repeat', 'not_event')
        """,
        values,
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_known_posts(connection: sqlite3.Connection, rows: list[dict[str, Any]]) -> dict:
    known = {}
    for row in rows:
        saved = connection.execute(
            """
            SELECT p.text, p.edited_at, p.published_at FROM raw_posts p JOIN sources s ON s.id = p.source_id
            WHERE s.username = ? AND p.message_id = ?
            """, (row["source"], row["message_id"]),
        ).fetchone()
        if saved is not None:
            known[(row["source"], int(row["message_id"]))] = dict(saved)
    return known


def init_watch_progress(
    connection: sqlite3.Connection, state_key: str, channels: list[str], since_date: str,
    legacy_state: dict[str, int],
) -> dict[str, tuple[int, str]]:
    with connection:
        connection.executemany(
            "INSERT OR IGNORE INTO watch_progress (state_key, source, last_message_id, since_date) VALUES (?, ?, ?, ?)",
            [(state_key, channel, legacy_state.get(channel, 0), since_date) for channel in channels],
        )
    return {r["source"]: (r["last_message_id"], r["since_date"]) for r in connection.execute(
        "SELECT source, last_message_id, since_date FROM watch_progress WHERE state_key = ?", (state_key,),
    )}


def advance_watch_progress(
    connection: sqlite3.Connection, state_key: str, fetched_rows: list[dict[str, Any]],
) -> dict[str, int]:
    state = {r["source"]: r["last_message_id"] for r in connection.execute(
        "SELECT source, last_message_id FROM watch_progress WHERE state_key = ?", (state_key,),
    )}
    known = fetch_known_posts(connection, fetched_rows)
    for source, last_id in list(state.items()):
        rows = sorted((r for r in fetched_rows if r["source"] == source and not r.get("tracked_only")),
                      key=lambda r: int(r["message_id"]))
        for row in rows:
            message_id = int(row["message_id"])
            if message_id <= last_id:
                continue
            saved = known.get((source, message_id))
            if saved is None or change_timestamp(saved) < change_timestamp(row):
                break
            if change_timestamp(saved) == change_timestamp(row) and saved["text"] != row["text"]:
                break
            last_id = message_id
        if last_id != state[source]:
            connection.execute("UPDATE watch_progress SET last_message_id = ? WHERE state_key = ? AND source = ?",
                               (last_id, state_key, source))
            state[source] = last_id
    return state


def fetch_tracked_posts(connection: sqlite3.Connection, since_date: str) -> dict[str, list[int]]:
    rows = connection.execute(
        """
        SELECT DISTINCT s.username, p.message_id FROM event_sources es
        JOIN events e ON e.id = es.event_id JOIN raw_posts p ON p.id = es.raw_post_id
        JOIN sources s ON s.id = p.source_id
        WHERE e.is_event = 1 AND e.status NOT IN ('repeat', 'not_event')
          AND (COALESCE(e.end_date, e.date) >= ? OR e.last_change_at >= ?
               OR (e.date IS NULL AND e.created_at >= ?))
        ORDER BY s.username, p.message_id
        """, (since_date, since_date, since_date),
    ).fetchall()
    tracked: dict[str, list[int]] = {}
    for row in rows:
        tracked.setdefault(row["username"], []).append(row["message_id"])
    return tracked


ADMIN_EDITABLE_FIELDS = {
    "event_state",
    "title",
    "date",
    "end_date",
    "time",
    "end_time",
    "place",
    "address",
    "category",
    "price",
    "status",
}


def fetch_events_by_status(
    connection: sqlite3.Connection,
    status: str = "draft",
) -> list[dict[str, Any]]:
    condition = "status = ?"
    parameters = (status,)
    if status == "exceptions":
        condition = "status = 'draft' OR conflict_post_id IS NOT NULL"
        parameters = ()
    elif status == "conflicts":
        condition = "conflict_post_id IS NOT NULL"
        parameters = ()
    rows = connection.execute(
        f"""
        SELECT id, title, date, end_date, time, place, address, category, price, status, event_state, conflict_post_id
        FROM events
        WHERE {condition}
        ORDER BY id DESC
        """,
        parameters,
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_event_detail(
    connection: sqlite3.Connection,
    event_id: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT
            e.id, e.title, e.date, e.end_date, e.time, e.end_time,
            e.place, e.address, e.category, e.price, e.confidence,
            e.reason, e.status, e.created_at,
            e.event_state, e.previous_date, e.last_change_at, e.last_change_post_id,
            e.conflict_at, e.conflict_post_id,
            cp.url AS last_change_url, xp.url AS conflict_url,
            p.id AS raw_post_id, p.message_id, p.url, p.text, p.published_at,
            p.city, s.username AS source
        FROM events e
        JOIN raw_posts p ON p.id = e.raw_post_id
        JOIN sources s ON s.id = p.source_id
        LEFT JOIN raw_posts cp ON cp.id = e.last_change_post_id
        LEFT JOIN raw_posts xp ON xp.id = e.conflict_post_id
        WHERE e.id = ?
        """,
        (event_id,),
    ).fetchone()
    if row is None:
        return None
    detail = dict(row)
    links = connection.execute(
        "SELECT url FROM post_links WHERE raw_post_id = ?",
        (detail["raw_post_id"],),
    ).fetchall()
    detail["links"] = [r["url"] for r in links]
    sources = connection.execute(
        """
        SELECT p.id AS raw_post_id, s.username AS source, p.message_id,
            p.url, p.text, p.published_at, p.edited_at, es.extracted_json
        FROM event_sources es
        JOIN raw_posts p ON p.id = es.raw_post_id
        JOIN sources s ON s.id = p.source_id
        WHERE es.event_id = ?
        ORDER BY p.published_at, p.id
        """,
        (event_id,),
    ).fetchall()
    detail["sources"] = []
    for source in sources:
        item = dict(source)
        extracted_json = item.pop("extracted_json")
        item["extracted_event"] = json.loads(extracted_json) if extracted_json is not None else None
        detail["sources"].append(item)
    return detail


def update_event(
    connection: sqlite3.Connection,
    event_id: int,
    fields: dict[str, Any],
    *, resolve_conflict: dict[str, Any] | None = None,
) -> bool:
    provided = set(fields)
    unknown = provided - ADMIN_EDITABLE_FIELDS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown editable fields: {names}")

    current = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if current is None:
        return False

    if fields.get("event_state", current["event_state"]) not in {"scheduled", "postponed", "cancelled"}:
        raise ValueError("Invalid event state")
    if resolve_conflict is not None and (
        current["conflict_post_id"] is None
        or resolve_conflict.get("post_id") != current["conflict_post_id"]
        or resolve_conflict.get("at") != current["conflict_at"]
    ):
        raise ValueError("Спорное сообщение изменилось. Обновите карточку перед проверкой")

    assignments = []
    values: list[Any] = []
    for name in ADMIN_EDITABLE_FIELDS:
        if name in fields:
            assignments.append(f"{name} = ?")
            values.append(fields[name])
    if not assignments and resolve_conflict is None:
        return connection.execute(
            "SELECT 1 FROM events WHERE id = ?", (event_id,)
        ).fetchone() is not None

    values.append(event_id)
    with connection:
        if assignments:
            connection.execute(f"UPDATE events SET {', '.join(assignments)} WHERE id = ?", values)
            connection.executemany(
                "INSERT OR IGNORE INTO event_field_locks (event_id, field) VALUES (?, ?)",
                [(event_id, name) for name in fields if fields[name] != current[name]],
            )
        if resolve_conflict is not None:
            connection.execute(
                """INSERT INTO event_conflict_resolutions (event_id, raw_post_id, version_at) VALUES (?, ?, ?)
                ON CONFLICT(event_id, raw_post_id) DO UPDATE SET version_at = excluded.version_at""",
                (event_id, current["conflict_post_id"], current["conflict_at"]),
            )
            connection.execute("UPDATE events SET conflict_post_id = NULL, conflict_at = NULL WHERE id = ?", (event_id,))
    return True


def merge_events(connection: sqlite3.Connection, event_id: int, target_id: int) -> bool:
    if event_id == target_id:
        raise ValueError("Choose a different event")
    source = fetch_event_detail(connection, event_id)
    target = fetch_event_detail(connection, target_id)
    if source is None or target is None:
        return False
    if {s["raw_post_id"] for s in source["sources"]} & {s["raw_post_id"] for s in target["sources"]}:
        raise ValueError("Нельзя объединить карточки с общим постом: потеряются извлечённые сведения. Лишнюю карточку можно скрыть")
    with connection:
        connection.execute(
            """INSERT INTO event_sources (event_id, raw_post_id, extracted_json, version_at)
            SELECT ?, raw_post_id, extracted_json, version_at FROM event_sources WHERE event_id = ?""",
            (target_id, event_id),
        )
        connection.execute(
            """INSERT INTO event_conflict_resolutions (event_id, raw_post_id, version_at)
            SELECT ?, raw_post_id, version_at FROM event_conflict_resolutions WHERE event_id = ?""",
            (target_id, event_id),
        )
        if source["conflict_post_id"] is not None and (
            target["conflict_at"] is None or source["conflict_at"] > target["conflict_at"]
        ):
            connection.execute("UPDATE events SET conflict_post_id = ?, conflict_at = ? WHERE id = ?",
                               (source["conflict_post_id"], source["conflict_at"], target_id))
        connection.executemany(
            "INSERT OR IGNORE INTO event_field_locks (event_id, field) VALUES (?, ?)",
            [(target_id, field) for field in ADMIN_EDITABLE_FIELDS if target[field] != source[field]],
        )
        connection.execute(
            """INSERT OR IGNORE INTO event_field_locks (event_id, field, reason)
            SELECT ?, field, reason FROM event_field_locks WHERE event_id = ?""",
            (target_id, event_id),
        )
        connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
    return True


def delete_event(connection: sqlite3.Connection, event_id: int) -> bool:
    with connection:
        cursor = connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
    return cursor.rowcount > 0


def _upsert_source(connection: sqlite3.Connection, username: str, city: str) -> int:
    connection.execute(
        """
        INSERT INTO sources (username, city)
        VALUES (?, ?)
        ON CONFLICT(username) DO UPDATE SET city = excluded.city
        """,
        (username, city),
    )
    return int(
        connection.execute(
            "SELECT id FROM sources WHERE username = ?",
            (username,),
        ).fetchone()["id"]
    )


def _upsert_raw_post(
    connection: sqlite3.Connection,
    source_id: int,
    row: dict[str, Any],
) -> int:
    url = row.get("post_url") or row["url"]
    text = row.get("raw_text", row.get("text", ""))
    city = row.get("city") or "Воронеж"
    connection.execute(
        """
        INSERT INTO raw_posts (source_id, message_id, published_at, city, url, text, edited_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id, message_id) DO UPDATE SET
            published_at = excluded.published_at,
            city = excluded.city,
            url = excluded.url,
            text = excluded.text,
            edited_at = excluded.edited_at
        """,
        (source_id, int(row["message_id"]), row["published_at"], city, url, text, row.get("edited_at")),
    )
    return int(
        connection.execute(
            "SELECT id FROM raw_posts WHERE source_id = ? AND message_id = ?",
            (source_id, int(row["message_id"])),
        ).fetchone()["id"]
    )


def _replace_post_links(
    connection: sqlite3.Connection,
    raw_post_id: int,
    links: list[str],
) -> None:
    connection.execute("DELETE FROM post_links WHERE raw_post_id = ?", (raw_post_id,))
    for link in links:
        connection.execute(
            "INSERT OR IGNORE INTO post_links (raw_post_id, url) VALUES (?, ?)",
            (raw_post_id, link),
        )


def _save_events(
    connection: sqlite3.Connection,
    raw_post_id: int,
    events: list[dict[str, Any]],
    batch_ids: dict[int, int],
    source_rules: dict,
    *, is_edit: bool = False, created_ids: set[int] | None = None,
) -> None:
    for event in events:
        event_id = _match_saved_extraction(connection, raw_post_id, event)
        same_extraction = event_id is not None
        if event_id is None:
            match_id = event.get("_match_id")
            event_id = batch_ids.get(match_id) if isinstance(match_id, int) and match_id < 0 else match_id
        if event_id is not None:
            current = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if current is None:
                raise ValueError("Matched event no longer exists")
            is_change = event.get("action", "announcement") != "announcement" or is_edit
            historical = (current["last_change_at"] is not None and (
                current["event_state"] != "scheduled" or event.get("date") == current["previous_date"]
            ))
            if current["last_change_at"] and event.get("date") != current["date"]:
                for saved in connection.execute(
                    "SELECT extracted_json FROM event_sources WHERE event_id = ? AND extracted_json IS NOT NULL",
                    (event_id,),
                ):
                    historical = historical or json.loads(saved[0]).get("date") == event.get("date")
            incompatible = (not same_extraction and not is_change and not historical and (
                event.get("date") != current["date"]
                or (event.get("time") and current["time"] and event["time"] != current["time"])
            ))
            if not incompatible:
                resolved = connection.execute(
                    """SELECT r.version_at, p.published_at, p.edited_at
                    FROM event_conflict_resolutions r JOIN raw_posts p ON p.id = r.raw_post_id
                    WHERE r.event_id = ? AND r.raw_post_id = ?""", (event_id, raw_post_id),
                ).fetchone()
                if resolved is None or change_timestamp(dict(resolved)) > resolved["version_at"]:
                    apply_event_update(connection, dict(current), raw_post_id, event,
                                       source_rules, is_edit=is_edit)
                    current = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
                    _fill_event(connection, dict(current), event)
                save_event_source(connection, event_id, raw_post_id, event)
                if "_batch_id" in event:
                    batch_ids[event["_batch_id"]] = event_id
                continue
        cursor = connection.execute(
            """
            INSERT INTO events (
                raw_post_id,
                is_event,
                title,
                date,
                end_date,
                time,
                end_time,
                place,
                address,
                category,
                price,
                confidence,
                reason,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw_post_id,
                1 if event["is_event"] else 0,
                event["title"],
                event["date"],
                event["end_date"],
                event["time"],
                event["end_time"],
                event["place"],
                event["address"],
                event["category"],
                event["price"],
                float(event["confidence"]),
                event["reason"],
                event["status"] if event.get("action", "announcement") == "announcement" else "draft",
            ),
        )
        if created_ids is not None:
            created_ids.add(cursor.lastrowid)
        if event.get("action", "announcement") != "announcement":
            state = "cancelled" if event["action"] == "cancel" else (
                "postponed" if event["action"] == "reschedule" and not event.get("date") else "scheduled"
            )
            post = dict(connection.execute("SELECT * FROM raw_posts WHERE id = ?", (raw_post_id,)).fetchone())
            connection.execute(
                "UPDATE events SET event_state = ?, last_change_at = ?, last_change_post_id = ?, previous_date = ? WHERE id = ?",
                (state, change_timestamp(post), raw_post_id, event.get("previous_date"), cursor.lastrowid),
            )
        save_event_source(connection, cursor.lastrowid, raw_post_id, event)
        if "_batch_id" in event:
            batch_ids[event["_batch_id"]] = cursor.lastrowid


def _match_saved_extraction(
    connection: sqlite3.Connection, raw_post_id: int, event: dict[str, Any],
) -> int | None:
    if not event.get("is_event"):
        return None
    rows = connection.execute(
        """
        SELECT es.event_id, es.extracted_json, e.* FROM event_sources es
        JOIN events e ON e.id = es.event_id WHERE es.raw_post_id = ?
        """, (raw_post_id,),
    ).fetchall()
    matches = []
    for row in rows:
        previous = json.loads(row["extracted_json"]) if row["extracted_json"] else dict(row)
        if (event.get("title")
                and all(event.get(k) == previous.get(k) for k in ("title", "date", "time", "place"))):
            matches.append(row["event_id"])
    return matches[0] if len(matches) == 1 else None


def _fill_event(
    connection: sqlite3.Connection, current: dict[str, Any], extracted: dict[str, Any],
) -> None:
    manual = {r[0] for r in connection.execute(
        "SELECT field FROM event_field_locks WHERE event_id = ?", (current["id"],)
    )}
    fields = {key: extracted[key] for key in ADMIN_EDITABLE_FIELDS - {"status"}
              if key not in manual and current[key] is None and extracted.get(key) is not None}
    if current["event_state"] != "scheduled" or (
        current["last_change_at"] and extracted.get("date") != current["date"]
    ):
        fields = {k: v for k, v in fields.items() if k not in {"date", "end_date", "time", "end_time"}}
    if fields:
        assignments = ", ".join(f"{key} = ?" for key in fields)
        connection.execute(f"UPDATE events SET {assignments} WHERE id = ?",
                           [*fields.values(), current["id"]])
