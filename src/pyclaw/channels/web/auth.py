from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import HTTPException, Request

from pyclaw.infra.settings import WebSettings


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _base64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def create_jwt(user_id: str, secret: str, expires_in: int = 86400) -> str:
    header = _base64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload = _base64url_encode(
        json.dumps({"sub": user_id, "iat": now, "exp": now + expires_in}).encode()
    )
    signature = _base64url_encode(
        hmac.new(
            secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256
        ).digest()
    )
    return f"{header}.{payload}.{signature}"


def verify_jwt(token: str, secret: str) -> str | None:
    """Return *user_id* (``sub`` claim) if token is valid, else ``None``."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts

    expected_sig = hmac.new(
        secret.encode(),
        f"{header_b64}.{payload_b64}".encode(),
        hashlib.sha256,
    ).digest()

    try:
        actual_sig = _base64url_decode(sig_b64)
    except Exception:
        return None

    if not hmac.compare_digest(expected_sig, actual_sig):
        return None

    try:
        payload = json.loads(_base64url_decode(payload_b64))
    except Exception:
        return None

    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and time.time() > exp:
        return None

    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None


def get_current_user(request: Request) -> str:
    """FastAPI dependency — extract Bearer token, verify, return user_id."""
    settings: WebSettings = request.app.state.web_settings
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header[7:]
    user_id = verify_jwt(token, settings.jwt_secret)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user_id


def verify_admin_token(request: Request) -> None:
    """FastAPI dependency — verify the ``X-Admin-Token`` header matches settings."""
    settings: WebSettings = request.app.state.web_settings
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="Admin access not configured")
    provided = request.headers.get("x-admin-token", "")
    if not hmac.compare_digest(provided, settings.admin_token):
        raise HTTPException(status_code=403, detail="Invalid admin token")
