from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from sqlite3 import Connection
from typing import Any
from urllib.parse import urlparse

from tg_event.auth import issue_token, verify_token
from tg_event.config import Settings
from tg_event.database import (
    fetch_event_detail,
    fetch_events_by_status,
    update_event,
    delete_event,
)
from tg_event.event_schema import ALLOWED_CATEGORIES, EventStatus


EDITABLE_STATUSES = {
    EventStatus.DRAFT.value,
    EventStatus.PUBLISHED.value,
    EventStatus.REJECTED.value,
    EventStatus.NOT_EVENT.value,
}
EVENT_LIST_STATUSES = EDITABLE_STATUSES | {EventStatus.REPEAT.value}

_IGNORE_FIELDS = {"is_event", "confidence", "reason", "raw_post_id"}


class AdminHandler(BaseHTTPRequestHandler):
    settings: Settings
    db: Connection
    static_dir: Path

    server_version = "tg-event-admin/1.0"

    def log_message(self, format, *args) -> None:
        pass

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/login":
            self._send_json({"error": "method not allowed"}, status=405)
            return
        if path == "/api/events":
            self._handle_list()
            return
        if path.startswith("/api/events/"):
            self._handle_detail(path)
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/login":
            self._handle_login()
            return
        if path.startswith("/api/events/") and path.endswith("/delete"):
            self._handle_delete(path)
            return
        if path.startswith("/api/events/"):
            self._handle_update(path)
            return
        self._send_json({"error": "not found"}, status=404)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise _ApiError(400, "invalid json")

    def _handle_login(self) -> None:
        try:
            payload = self._read_body()
        except _ApiError as err:
            self._send_json({"error": err.message}, status=err.status)
            return
        user = (payload.get("user") or "").strip()
        password = (payload.get("password") or "").strip()
        if not user or not password:
            self._send_json({"error": "user and password required"}, status=400)
            return
        if user != self.settings.admin_user or password != self.settings.admin_password:
            self._send_json({"error": "invalid credentials"}, status=401)
            return
        token = issue_token(user, self.settings.admin_secret)
        self._send_json({"token": token, "user": user})

    def _require_auth(self) -> str | None:
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Bearer "):
            return None
        token = header.removeprefix("Bearer ").strip()
        return verify_token(token, self.settings.admin_secret)

    def _handle_list(self) -> None:
        user = self._require_auth()
        if user is None:
            self._send_json({"error": "unauthorized"}, status=401)
            return
        query = urlparse(self.path).query
        status = _query_value(query, "status") or "draft"
        if status not in EVENT_LIST_STATUSES:
            self._send_json({"error": "invalid status"}, status=400)
            return
        rows = fetch_events_by_status(self.db, status)
        self._send_json({"items": rows, "status": status})

    def _handle_detail(self, path: str) -> None:
        if self._require_auth() is None:
            self._send_json({"error": "unauthorized"}, status=401)
            return
        event_id = _path_id(path)
        if event_id is None:
            self._send_json({"error": "invalid id"}, status=404)
            return
        detail = fetch_event_detail(self.db, event_id)
        if detail is None:
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_json(detail)

    def _handle_update(self, path: str) -> None:
        if self._require_auth() is None:
            self._send_json({"error": "unauthorized"}, status=401)
            return
        event_id = _path_id(path)
        if event_id is None:
            self._send_json({"error": "invalid id"}, status=404)
            return
        try:
            payload = self._read_body()
        except _ApiError as err:
            self._send_json({"error": err.message}, status=err.status)
            return
        clean = {k: v for k, v in payload.items() if k not in _IGNORE_FIELDS}
        for empty_key in ("title", "place", "address", "price"):
            if clean.get(empty_key) == "":
                clean[empty_key] = None
        for empty_key in ("date", "end_date", "time", "end_time"):
            if clean.get(empty_key) == "":
                clean[empty_key] = None
        errors = _validate_fields(clean)
        if errors:
            self._send_json({"error": "validation", "details": errors}, status=400)
            return
        try:
            ok = update_event(self.db, event_id, clean)
        except ValueError as err:
            self._send_json({"error": str(err)}, status=400)
            return
        if not ok:
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_json({"ok": True})

    def _handle_delete(self, path: str) -> None:
        if self._require_auth() is None:
            self._send_json({"error": "unauthorized"}, status=401)
            return
        event_id = _path_id(path, suffix="/delete")
        if event_id is None:
            self._send_json({"error": "invalid id"}, status=404)
            return
        ok = delete_event(self.db, event_id)
        if not ok:
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_json({"ok": True})

    def _serve_static(self, path: str) -> None:
        file_path = self.static_dir / (path.lstrip("/") or "index.html")
        if file_path.is_dir():
            file_path = file_path / "index.html"
        if not file_path.exists() or not file_path.is_file():
            index = self.static_dir / "index.html"
            if index.exists():
                self._send_file(index)
                return
            self._send_text(
                "Admin UI not built. Run: cd admin-ui && npm install && npm run build",
                status=404,
            )
            return
        self._send_file(file_path)

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path) -> None:
        body = file_path.read_bytes()
        mime, _ = mimetypes.guess_type(str(file_path))
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _path_id(path: str, suffix: str = "") -> int | None:
    base = "/api/events/"
    rest = path.removeprefix(base)
    if suffix:
        if not rest.endswith(suffix):
            return None
        rest = rest[: -len(suffix)]
    try:
        return int(rest)
    except ValueError:
        return None


def _query_value(query: str, key: str) -> str | None:
    from urllib.parse import parse_qs

    values = parse_qs(query).get(key)
    return values[0] if values else None


def _validate_fields(fields: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "status" in fields and fields["status"] not in EDITABLE_STATUSES:
        errors.append("status must be one of: draft, published, rejected, not_event")
    if "category" in fields:
        category = fields["category"]
        if category is not None and category not in ALLOWED_CATEGORIES:
            errors.append("category must be one of: " + ", ".join(sorted(ALLOWED_CATEGORIES)))
    for key in ("title", "place", "address", "price", "date", "end_date", "time", "end_time"):
        if key in fields and fields[key] is not None and not isinstance(fields[key], str):
            errors.append(f"{key} must be a string or null")
    return errors


def make_handler(settings: Settings, connection: Connection, static_dir: Path) -> type:
    class _Bound(AdminHandler):
        pass

    _Bound.settings = settings
    _Bound.db = connection
    _Bound.static_dir = static_dir
    return _Bound


def serve(settings: Settings, connection: Connection, host: str, port: int, dev: bool) -> None:
    static_dir = settings.admin_static_dir
    if dev:
        static_dir = Path(__file__).resolve().parent / "_dev_fallback"
        static_dir.mkdir(parents=True, exist_ok=True)
        (static_dir / "index.html").write_text(
            "Admin API is running in --dev mode. Open the Vite dev server (admin-ui).",
            encoding="utf-8",
        )
    handler = make_handler(settings, connection, static_dir)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Admin server on http://{host}:{port} (dev={dev})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()