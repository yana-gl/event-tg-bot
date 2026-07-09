from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
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
        """
    )
    connection.commit()


def save_parsed_row(connection: sqlite3.Connection, row: dict[str, Any]) -> int:
    with connection:
        source_id = _upsert_source(connection, row["source"], row.get("city") or "Воронеж")
        raw_post_id = _upsert_raw_post(connection, source_id, row)
        _replace_post_links(connection, raw_post_id, row.get("links", []))
        _replace_events(connection, raw_post_id, row.get("events", []))
    return raw_post_id


def save_parsed_rows(connection: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        save_parsed_row(connection, row)


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
    text = row.get("raw_text") or row["text"]
    city = row.get("city") or "Воронеж"
    connection.execute(
        """
        INSERT INTO raw_posts (source_id, message_id, published_at, city, url, text)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id, message_id) DO UPDATE SET
            published_at = excluded.published_at,
            city = excluded.city,
            url = excluded.url,
            text = excluded.text
        """,
        (source_id, int(row["message_id"]), row["published_at"], city, url, text),
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


def _replace_events(
    connection: sqlite3.Connection,
    raw_post_id: int,
    events: list[dict[str, Any]],
) -> None:
    connection.execute("DELETE FROM events WHERE raw_post_id = ?", (raw_post_id,))
    for event in events:
        connection.execute(
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
                event["status"],
            ),
        )
