from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .database import get_db
from .models import AuthSession, User


password_hasher = PasswordHash.recommended()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def make_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), expected_hash)




def generate_setup_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "-".join(groups)

def generate_temporary_password(length: int = 14) -> str:
    # Guarantee uppercase, lowercase, and numeric characters, then shuffle.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    chars = [secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ"), secrets.choice("abcdefghijkmnopqrstuvwxyz"), secrets.choice("23456789")]
    chars.extend(secrets.choice(alphabet) for _ in range(max(0, length - len(chars))))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password, password_hash)
    except Exception:
        return False


def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.admin_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid platform administrator key is required.",
        )


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing assessment session token.")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing assessment session token.")
    return token


def create_lecturer_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(48)
    session = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=utcnow() + timedelta(hours=settings.lecturer_session_hours),
    )
    db.add(session)
    db.commit()
    return token


def optional_current_user(
    request: Request,
    db: Session = Depends(get_db),
    lecturer_session: str | None = Cookie(default=None, alias=settings.auth_cookie_name),
) -> User | None:
    """Return the signed-in lecturer when a valid session exists, otherwise None.

    This dependency is intended only for session-status checks such as
    ``GET /api/auth/me``. Protected routes must continue to use ``current_user``.
    """
    if not lecturer_session:
        return None
    session = db.scalar(
        select(AuthSession)
        .options(selectinload(AuthSession.user).selectinload(User.institution))
        .where(AuthSession.token_hash == hash_token(lecturer_session))
    )
    if not session or session.revoked_at is not None or _aware(session.expires_at) <= utcnow():
        return None
    user = session.user
    if user.account_status != "active":
        return None
    request.state.auth_session = session
    request.state.current_user = user
    return user


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    lecturer_session: str | None = Cookie(default=None, alias=settings.auth_cookie_name),
) -> User:
    if not lecturer_session:
        raise HTTPException(status_code=401, detail="Lecturer sign-in is required.")
    session = db.scalar(
        select(AuthSession)
        .options(selectinload(AuthSession.user).selectinload(User.institution))
        .where(AuthSession.token_hash == hash_token(lecturer_session))
    )
    if not session or session.revoked_at is not None or _aware(session.expires_at) <= utcnow():
        raise HTTPException(status_code=401, detail="Your lecturer session has expired. Sign in again.")
    user = session.user
    if user.account_status != "active":
        raise HTTPException(status_code=403, detail="This lecturer account is not active.")
    password_change_paths = {"/api/auth/me", "/api/auth/logout", "/api/auth/change-password"}
    if user.must_change_password and request.url.path not in password_change_paths:
        raise HTTPException(status_code=403, detail="Change your password before using the lecturer workspace.")
    request.state.auth_session = session
    request.state.current_user = user
    return user


def require_roles(*roles: str) -> Callable:
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="You do not have permission for this action.")
        return user

    return dependency
