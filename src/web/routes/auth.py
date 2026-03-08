"""Authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.config import settings
from src.db.models import User
from src.onboarding import (
    build_interest_profile_from_form,
    ensure_predefined_sources,
    normalize_city_list,
    recommended_source_keys_for_city,
    validate_onboarding_form,
)
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
    htmx_redirect_or_redirect,
    is_htmx_request,
    require_csrf,
    require_login_and_csrf,
    template_response,
)

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = await get_current_user(request, get_db(request))
    if user:
        return htmx_redirect_or_redirect(request, "/profile")
    return template_response(request, "login.html", await ctx(request, active_page="auth"))


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
        status_code = 422 if is_htmx_request(request) else 200
        return template_response(
            request,
            "login.html",
            {**await ctx(request, active_page="auth"), "error": "Invalid email or password.", "email": email},
            status_code=status_code,
        )

    login_session(request, user)
    rotate_csrf_token(request)
    return htmx_redirect_or_redirect(request, "/profile")


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    user = await get_current_user(request, get_db(request))
    if user:
        return htmx_redirect_or_redirect(request, "/profile")
    return template_response(request, "signup.html", await ctx(request, active_page="auth"))


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
    home_city = str(form.get("home_city", "")).strip()
    preferred_cities = normalize_city_list(
        str(form.get("preferred_cities", "")).strip(),
        fallback_home_city=home_city,
    )
    child_name = str(form.get("child_name", "")).strip()

    errors: list[str] = []
    if not email or "@" not in email:
        errors.append("Valid email is required.")
    if not display_name:
        errors.append("Display name is required.")
    errors.extend(validate_onboarding_form(form))
    errors.extend(validate_password(password))
    if password != confirm:
        errors.append("Passwords don't match.")
    if not errors and await db.get_user_by_email(email):
        errors.append("An account with this email already exists.")

    if errors:
        status_code = 422 if is_htmx_request(request) else 200
        return template_response(
            request,
            "signup.html",
            {
                **await ctx(request, active_page="auth"),
                "errors": errors,
                "email": email,
                "display_name": display_name,
                "home_city": home_city,
                "preferred_cities": ", ".join(preferred_cities),
                "child_name": child_name,
                "temperament": str(form.get("temperament", "")).strip(),
            },
            status_code=status_code,
        )

    interest_profile = build_interest_profile_from_form(
        form,
        home_city=home_city,
        preferred_cities=preferred_cities,
    )
    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        home_city=home_city,
        preferred_cities=preferred_cities,
        child_name=child_name,
        onboarding_complete=True,
        interest_profile=interest_profile,
    )
    await db.create_user(user)
    await ensure_predefined_sources(
        db,
        user=user,
        source_keys=form.getlist("predefined_sources") or recommended_source_keys_for_city(home_city),
    )
    login_session(request, user)
    rotate_csrf_token(request)
    return htmx_redirect_or_redirect(request, "/profile")


@router.post("/logout")
async def logout(request: Request):
    _user, _form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    logout_session(request)
    return htmx_redirect_or_redirect(request, "/")
