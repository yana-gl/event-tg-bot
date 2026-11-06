from __future__ import annotations

import hmac
import json
import mimetypes
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from sqlite3 import Connection
from threading import RLock
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

from tg_event.admin_service import (
    EDITABLE_STATUSES,
    ValidationError,
    merge_events,
    update_event,
)
from tg_event.auth import LoginLimiter, issue_token, verify_token
from tg_event.config import Settings
from tg_event.channels import add_channel, list_channels, update_channel
from tg_event.database import (
    connect_database,
    fetch_event_detail,
    fetch_events_by_status,
    delete_event,
)
from tg_event.event_schema import EventStatus


EVENT_LIST_STATUSES = EDITABLE_STATUSES | {EventStatus.REPEAT.value, "exceptions", "conflicts"}


class AdminHandler(BaseHTTPRequestHandler):
    settings: Settings
    db: Connection | None
    db_lock: RLock
    static_dir: Path
    login_limiter: LoginLimiter

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
        if path == "/api/channels":
            self._handle_channels()
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
        if path == "/api/channels" or path.startswith("/api/channels/"):
            self._handle_channels(update=True)
            return
        if path.startswith("/api/events/") and path.endswith("/delete"):
            self._handle_delete(path)
            return
        if path.startswith("/api/events/") and path.endswith("/merge"):
            self._handle_merge(path)
            return
        if path.startswith("/api/events/"):
            self._handle_update(path)
            return
        self._send_json({"error": "not found"}, status=404)

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise _ApiError(400, "invalid Content-Length")
        if length < 0:
            raise _ApiError(400, "invalid Content-Length")
        try:
            raw = self.rfile.read(length) if length else b""
        except OverflowError:
            raise _ApiError(400, "invalid Content-Length")
        if len(raw) != length:
            raise _ApiError(400, "incomplete body")
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise _ApiError(400, "invalid json")
        if not isinstance(payload, dict):
            raise _ApiError(400, "expected JSON object")
        return payload

    @contextmanager
    def _database(self) -> Iterator[Connection]:
        if self.db is None:
            with closing(connect_database(self.settings.database_path)) as connection:
                yield connection
        else:
            with self.db_lock:
                yield self.db

    def _handle_channels(self, update: bool = False) -> None:
        if self._require_auth() is None:
            self._send_json({"error": "unauthorized"}, status=401)
            return
        if not update:
            with self._database() as connection:
                items = list_channels(connection)
            self._send_json({"items": items})
            return
        try:
            payload = self._read_body()
            path = urlparse(self.path).path
            if path == "/api/channels":
                if set(payload) - {"username", "initial_posts"}:
                    raise ValueError("Укажите канал и число постов первой загрузки")
                with self._database() as connection:
                    name = add_channel(connection, payload.get("username"), payload.get("initial_posts", 20))
            else:
                name = path.removeprefix("/api/channels/")
                with self._database() as connection:
                    ok = update_channel(connection, name, payload)
                if not ok:
                    self._send_json({"error": "Канал не найден"}, status=404)
                    return
        except _ApiError as err:
            self._send_json({"error": err.message}, status=err.status)
            return
        except ValueError as err:
            self._send_json({"error": str(err)}, status=400)
            return
        self._send_json({"ok": True, "username": name})

    def _handle_login(self) -> None:
        client = self.client_address[0]
        if client in {"127.0.0.1", "::1"}:
            client = self.headers.get("X-Real-IP") or client
        if not self.login_limiter.allow(client):
            self._send_json({"error": "Слишком много попыток входа. Повторите через минуту.",
                             "retry_after": 60}, status=429)
            return
        try:
            payload = self._read_body()
        except _ApiError as err:
            self._send_json({"error": err.message}, status=err.status)
            return
        if not isinstance(payload, dict):
            self._send_json({"error": "user and password required"}, status=400)
            return
        user = payload.get("user", "")
        password = payload.get("password", "")
        if not isinstance(user, str) or not isinstance(password, str):
            self._send_json({"error": "user and password must be strings"}, status=400)
            return
        user, password = user.strip(), password.strip()
        if not user or not password:
            self._send_json({"error": "user and password required"}, status=400)
            return
        valid_user = hmac.compare_digest(user.encode("utf-8"), self.settings.admin_user.encode("utf-8"))
        valid_password = hmac.compare_digest(password.encode("utf-8"), self.settings.admin_password.encode("utf-8"))
        if not valid_user or not valid_password:
            self._send_json({"error": "invalid credentials"}, status=401)
            return
        token = issue_token(user, self.settings.admin_secret)
        self.login_limiter.reset(client)
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
        status = _query_value(query, "status") or "exceptions"
        if status not in EVENT_LIST_STATUSES:
            self._send_json({"error": "invalid status"}, status=400)
            return
        with self._database() as connection:
            rows = fetch_events_by_status(connection, status)
        self._send_json({"items": rows, "status": status})

    def _handle_detail(self, path: str) -> None:
        if self._require_auth() is None:
            self._send_json({"error": "unauthorized"}, status=401)
            return
        event_id = _path_id(path)
        if event_id is None:
            self._send_json({"error": "invalid id"}, status=404)
            return
        with self._database() as connection:
            detail = fetch_event_detail(connection, event_id)
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
            with self._database() as connection:
                ok = update_event(connection, event_id, payload)
        except _ApiError as err:
            self._send_json({"error": err.message}, status=err.status)
            return
        except ValidationError as err:
            self._send_json({"error": str(err), "details": err.details}, status=400)
            return
        except ValueError as err:
            self._send_json({"error": str(err)}, status=400)
            return
        if not ok:
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_json({"ok": True})

    def _handle_merge(self, path: str) -> None:
        if self._require_auth() is None:
            self._send_json({"error": "unauthorized"}, status=401)
            return
        event_id = _path_id(path, suffix="/merge")
        if event_id is None:
            self._send_json({"error": "invalid id"}, status=404)
            return
        try:
            payload = self._read_body()
            with self._database() as connection:
                target_id = merge_events(connection, event_id, payload)
        except _ApiError as err:
            self._send_json({"error": err.message}, status=err.status)
            return
        except ValueError as err:
            self._send_json({"error": str(err)}, status=400)
            return
        if target_id is None:
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_json({"ok": True, "target_id": target_id})

    def _handle_delete(self, path: str) -> None:
        if self._require_auth() is None:
            self._send_json({"error": "unauthorized"}, status=401)
            return
        event_id = _path_id(path, suffix="/delete")
        if event_id is None:
            self._send_json({"error": "invalid id"}, status=404)
            return
        with self._database() as connection:
            ok = delete_event(connection, event_id)
        if not ok:
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_json({"ok": True})

    def _serve_static(self, path: str) -> None:
        static_dir = self.static_dir.resolve()
        file_path = (static_dir / (unquote(path).lstrip("/") or "index.html")).resolve()
        if file_path.is_dir():
            file_path = (file_path / "index.html").resolve()
        if not file_path.is_relative_to(static_dir):
            self._send_text("Forbidden", status=403)
            return
        if not file_path.exists() or not file_path.is_file():
            index = (static_dir / "index.html").resolve()
            if index.is_relative_to(static_dir) and index.is_file():
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
        event_id = int(rest)
    except ValueError:
        return None
    return event_id if -(2 ** 63) <= event_id <= 2 ** 63 - 1 else None


def _query_value(query: str, key: str) -> str | None:
    from urllib.parse import parse_qs

    values = parse_qs(query).get(key)
    return values[0] if values else None


def make_handler(settings: Settings, connection: Connection | None, static_dir: Path) -> type:
    class _Bound(AdminHandler):
        pass

    _Bound.settings = settings
    _Bound.login_limiter = LoginLimiter()
    _Bound.db = connection
    _Bound.db_lock = RLock()
    _Bound.static_dir = static_dir
    return _Bound


def serve(settings: Settings, host: str, port: int, dev: bool) -> None:
    static_dir = settings.admin_static_dir
    if dev:
        static_dir = Path(__file__).resolve().parent / "_dev_fallback"
        static_dir.mkdir(parents=True, exist_ok=True)
        (static_dir / "index.html").write_text(
            "Admin API is running in --dev mode. Open the Vite dev server (admin-ui).",
            encoding="utf-8",
        )
    handler = make_handler(settings, None, static_dir)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Admin server on http://{host}:{port} (dev={dev})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
