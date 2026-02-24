"""Authentication helpers: password hashing, session management."""

from __future__ import annotations

import bcrypt
from starlette.requests import Request

from src.db.database import Database
from src.db.models import User

SESSION_KEY = "user_id"


def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def login_session(request: Request, user: User) -> None:
    """Store user_id in the session cookie."""
    request.session[SESSION_KEY] = user.id


def logout_session(request: Request) -> None:
    """Clear the session."""
    request.session.clear()


async def get_current_user(request: Request, db: Database) -> User | None:
    """Return the logged-in User or None."""
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return None
    return await db.get_user(user_id)
