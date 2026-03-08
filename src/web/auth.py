"""Authentication helpers: password hashing, session management."""

from __future__ import annotations

import secrets
import string

import bcrypt
from starlette.requests import Request

from src.db.database import Database
from src.db.models import User

SESSION_KEY = "user_id"
CSRF_SESSION_KEY = "csrf_token"
_MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def ensure_csrf_token(request: Request) -> str:
    """Return the session CSRF token, creating one if needed."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return str(token)


async def verify_csrf(request: Request) -> bool:
    """Validate CSRF token from header or form body against session token."""
    expected = request.session.get(CSRF_SESSION_KEY)
    if not expected:
        return False

    provided = request.headers.get("X-CSRF-Token", "").strip()
    if not provided:
        form = await request.form()
        provided = str(form.get("csrf_token", "")).strip()

    return bool(provided) and secrets.compare_digest(str(expected), provided)


def validate_password(password: str) -> list[str]:
    """Return password validation errors."""
    errors: list[str] = []
    if len(password) < _MIN_PASSWORD_LENGTH:
        errors.append(f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.")
    if not any(ch.isalpha() for ch in password):
        errors.append("Password must include at least one letter.")
    if not any(ch.isdigit() for ch in password):
        errors.append("Password must include at least one number.")
    if any(ch not in string.printable or ch in "\r\n\t" for ch in password):
        errors.append("Password contains unsupported whitespace or control characters.")
    return errors


def login_session(request: Request, user: User) -> None:
    """Store user_id in the session cookie."""
    request.session[SESSION_KEY] = user.id
    ensure_csrf_token(request)


def logout_session(request: Request) -> None:
    """Clear the session."""
    request.session.clear()


async def get_current_user(request: Request, db: Database) -> User | None:
    """Return the logged-in User or None."""
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return None
    return await db.get_user(user_id)
