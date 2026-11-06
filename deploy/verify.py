from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tg_event.config import Settings


def request(path: str, payload: dict | None = None, token: str | None = None) -> tuple[int, bytes]:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    req = Request("http://127.0.0.1:8080" + path, headers=headers, data=data)
    try:
        with urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def main() -> None:
    settings = Settings.from_env("/etc/tg-event.env")
    for path, expected in (("/", 200), ("/channels", 200), ("/api/events", 401),
                           ("/../../requirements.txt", 403), ("/%2e%2e/%2e%2e/requirements.txt", 403)):
        status, _ = request(path)
        if status != expected:
            raise SystemExit(f"Failed: {path}, expected {expected}, got {status}")
    status, body = request("/api/login", {"user": settings.admin_user, "password": settings.admin_password})
    if status != 200:
        raise SystemExit(f"Admin login failed: HTTP {status}")
    token = json.loads(body)["token"]
    for path in ("/api/events?status=exceptions", "/api/channels"):
        status, body = request(path, token=token)
        if status != 200:
            raise SystemExit(f"Failed: {path}, HTTP {status}")
        print(f"OK: {path}, {len(json.loads(body)['items'])} items")
    print("OK: frontend, authentication, API and static path restrictions")


if __name__ == "__main__":
    main()
