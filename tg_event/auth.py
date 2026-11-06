from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from threading import Lock
from typing import Any


class LoginLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, tuple[float, int]] = {}
        self._lock = Lock()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._attempts = {
                key: value for key, value in self._attempts.items() if now - value[0] < 60
            }
            started, count = self._attempts.get(client, (now, 0))
            if count >= 5 or (client not in self._attempts and len(self._attempts) >= 1024):
                return False
            self._attempts[client] = (started, count + 1)
            return True

    def reset(self, client: str) -> None:
        with self._lock:
            self._attempts.pop(client, None)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _secret_key(secret: str) -> bytes:
    if not secret.strip():
        raise ValueError("ADMIN_SECRET must not be empty")
    return secret.encode("utf-8")


def issue_token(user: str, secret: str, ttl_seconds: int = 7 * 24 * 3600) -> str:
    payload = {"user": user, "exp": int(time.time()) + ttl_seconds}
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_secret_key(secret), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url(signature)}"


def verify_token(token: str, secret: str) -> str | None:
    if not secret.strip():
        return None
    try:
        body_b64, signature_b64 = token.split(".", 1)
    except ValueError:
        return None
    try:
        expected = hmac.new(_secret_key(secret), body_b64.encode("ascii"), hashlib.sha256).digest()
        signature = _b64url_decode(signature_b64)
    except (ValueError, base64.binascii.Error):
        return None
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload: dict[str, Any] = json.loads(_b64url_decode(body_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    user = payload.get("user")
    exp = payload.get("exp")
    if not isinstance(user, str) or not isinstance(exp, int):
        return None
    if exp < int(time.time()):
        return None
    return user
