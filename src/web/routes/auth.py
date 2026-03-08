"""Authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.config import settings
from src.db.models import User
from src.web.auth import (
    get_current_user,
    hash_password,
    login_session,
    logout_session,
    rotate_csrf_token,
    validate_password,
    verify_password,
)
from src.web.common import (
    check_rate_limit,
    ctx,
    get_db,
    get_templates,
    require_csrf,
    require_login_and_csrf,
)

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = await get_current_user(request, get_db(request))
    if user:
        return RedirectResponse("/profile", status_code=302)
    return get_templates(request).TemplateResponse("login.html", await ctx(request, active_page="auth"))


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    db = get_db(request)
    form, denied = await require_csrf(request)
    if denied:
        return denied
    if throttled := check_rate_limit(
        request,
        "login_submit",
        limit=settings.auth_rate_limit_max_requests,
        window=settings.auth_rate_limit_window_seconds,
        message="Too many login attempts. Try again later.",
    ):
        return throttled

    assert form is not None
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))

    user = await db.get_user_by_email(email)
    if not user or not verify_password(password, user.password_hash):
        return get_templates(request).TemplateResponse(
            "login.html",
            {**await ctx(request, active_page="auth"), "error": "Invalid email or password."},
        )

    login_session(request, user)
    rotate_csrf_token(request)
    return RedirectResponse("/profile", status_code=302)


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    user = await get_current_user(request, get_db(request))
    if user:
        return RedirectResponse("/profile", status_code=302)
    return get_templates(request).TemplateResponse("signup.html", await ctx(request, active_page="auth"))


@router.post("/signup", response_class=HTMLResponse)
async def signup_submit(request: Request):
    db = get_db(request)
    form, denied = await require_csrf(request)
    if denied:
        return denied
    if throttled := check_rate_limit(
        request,
        "signup_submit",
        limit=settings.auth_rate_limit_max_requests,
        window=settings.auth_rate_limit_window_seconds,
        message="Too many signup attempts. Try again later.",
    ):
        return throttled

    assert form is not None
    email = str(form.get("email", "")).strip().lower()
    display_name = str(form.get("display_name", "")).strip()
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm_password", ""))

    errors: list[str] = []
    if not email or "@" not in email:
        errors.append("Valid email is required.")
    if not display_name:
        errors.append("Display name is required.")
    errors.extend(validate_password(password))
    if password != confirm:
        errors.append("Passwords don't match.")
    if not errors and await db.get_user_by_email(email):
        errors.append("An account with this email already exists.")

    if errors:
        return get_templates(request).TemplateResponse(
            "signup.html",
            {
                **await ctx(request, active_page="auth"),
                "errors": errors,
                "email": email,
                "display_name": display_name,
            },
        )

    user = User(email=email, display_name=display_name, password_hash=hash_password(password))
    await db.create_user(user)
    login_session(request, user)
    rotate_csrf_token(request)
    return RedirectResponse("/profile", status_code=302)


@router.post("/logout")
async def logout(request: Request):
    _user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    logout_session(request)
    return RedirectResponse("/", status_code=302)
