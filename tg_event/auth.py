from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _secret_key(secret: str) -> bytes:
    if not secret:
        return b"tg-event-default-admin-secret"
    return secret.encode("utf-8")


def issue_token(user: str, secret: str, ttl_seconds: int = 7 * 24 * 3600) -> str:
    payload = {"user": user, "exp": int(time.time()) + ttl_seconds}
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_secret_key(secret), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url(signature)}"


def verify_token(token: str, secret: str) -> str | None:
    try:
        body_b64, signature_b64 = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(_secret_key(secret), body_b64.encode("ascii"), hashlib.sha256).digest()
    try:
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