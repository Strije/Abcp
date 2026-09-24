"""Токен сессии приложения: base64url(JSON {uid, exp}) + "." + HMAC-SHA256.

Пароль клиента в токене не хранится — только его id в ABCP и срок действия.
"""
import base64
import hashlib
import hmac
import json
import time


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(secret: bytes, uid: str, ttl: int, now: float | None = None) -> str:
    payload = _b64(json.dumps({"uid": uid, "exp": int((now or time.time()) + ttl)}).encode())
    sig = _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify(secret: bytes, token: str, now: float | None = None) -> str | None:
    """uid клиента или None, если токен подделан/просрочен."""
    try:
        payload, sig = token.split(".", 1)
        expected = _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_unb64(payload))
        if data["exp"] < (now or time.time()):
            return None
        return str(data["uid"])
    except Exception:
        return None
