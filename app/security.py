from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException, status

from .config import settings


def make_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), expected_hash)


def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.admin_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid administrator key is required.",
        )


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing assessment session token.")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing assessment session token.")
    return token
