from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone


def normalize_channel(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Укажите имя или публичную ссылку на канал")
    name = value.strip().lower()
    name = re.sub(r"^(?:https?://)?t\.me/", "", name).rstrip("/").removeprefix("@")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
        raise ValueError("Нужны @username или публичная ссылка t.me/username, без номера поста")
    return name


def _validate_count(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 1000:
        raise ValueError("Для первой загрузки укажите целое число от 1 до 1000")


def list_channels(connection: sqlite3.Connection) -> list[dict]:
    result = []
    for row in connection.execute("SELECT * FROM channel_settings ORDER BY username"):
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        cursor = connection.execute("SELECT MIN(last_message_id) FROM watch_progress WHERE source=?", (item["username"],)).fetchone()[0]
        item["last_message_id"] = cursor
        item["initial_status"] = "existing" if item["mode"] == "legacy" else (
            "pending" if item["initial_start_id"] is None else
            "complete" if cursor is not None and cursor >= item["initial_end_id"] else "loading"
        )
        result.append(item)
    return result


def add_channel(connection: sqlite3.Connection, username: str, initial_posts: int = 20) -> str:
    name = normalize_channel(username)
    _validate_count(initial_posts)
    with connection:
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM channel_settings WHERE username=?", (name,)).fetchone():
            raise ValueError("Этот канал уже добавлен")
        connection.execute("INSERT INTO channel_settings (username, initial_posts) VALUES (?, ?)", (name, initial_posts))
    return name


def update_channel(connection: sqlite3.Connection, username: str, fields: dict) -> bool:
    name = normalize_channel(username)
    if set(fields) - {"enabled", "initial_posts"}:
        raise ValueError("Можно изменить только активность и число постов первой загрузки")
    if "enabled" in fields and type(fields["enabled"]) is not bool:
        raise ValueError("enabled должен быть логическим значением")
    if "initial_posts" in fields:
        _validate_count(fields["initial_posts"])
    with connection:
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM channel_settings WHERE username=?", (name,)).fetchone()
        if row is None:
            return False
        if "initial_posts" in fields and fields["initial_posts"] != row["initial_posts"] and (
            row["mode"] == "legacy" or row["initial_start_id"] is not None
        ):
            raise ValueError("Первая загрузка уже начата; повторный импорт не выполняется")
        if fields:
            assignments = ", ".join(f"{key}=?" for key in fields)
            connection.execute(f"UPDATE channel_settings SET {assignments} WHERE username=?", (*fields.values(), name))
    return True


def initialize_recent_channel(connection: sqlite3.Connection, username: str, state_key: str,
                              initial_posts: int, message_ids: list[int]) -> int:
    with connection:
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM channel_settings WHERE username=?", (username,)).fetchone()
        if not row["enabled"] or row["initial_posts"] != initial_posts:
            raise ValueError("Настройки канала изменились; загрузка продолжится в следующем цикле")
        start = row["initial_start_id"]
        if start is None:
            start = min(message_ids) - 1 if message_ids else 0
            end = max(message_ids) if message_ids else 0
            connection.execute("UPDATE channel_settings SET initial_start_id=?, initial_end_id=? WHERE username=?",
                               (start, end, username))
        connection.execute("UPDATE watch_progress SET last_message_id=MAX(last_message_id, ?), since_date='1970-01-01' WHERE state_key=? AND source=?",
                           (start, state_key, username))
        return connection.execute("SELECT last_message_id FROM watch_progress WHERE state_key=? AND source=?", (state_key, username)).fetchone()[0]


def record_channel_check(connection: sqlite3.Connection, username: str, *, started: bool = False,
                         error: str | None = None) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    with connection:
        if started:
            connection.execute("UPDATE channel_settings SET last_attempt_at=? WHERE username=?", (stamp, username))
        elif error is None:
            connection.execute("UPDATE channel_settings SET last_success_at=?, last_error=NULL WHERE username=?", (stamp, username))
        else:
            connection.execute("UPDATE channel_settings SET last_error=? WHERE username=?", (error, username))
