"""Profile routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.onboarding import (
    build_interest_profile_from_form,
    normalize_city_list,
    validate_onboarding_form,
)
from src.web.auth import hash_password, validate_password, verify_password
from src.web.common import (
    change_theme,
    ctx,
    get_current_user_or_redirect,
    get_db,
    require_login_and_csrf,
    template_response,
    toast,
)

router = APIRouter()


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user, redirect = await get_current_user_or_redirect(request)
    if redirect:
        return redirect
    assert user is not None
    sources = await get_db(request).get_user_sources(user.id)
    return template_response(
        request,
        "profile.html",
        {**await ctx(request, active_page="profile"), "sources": sources},
    )


@router.post("/api/profile/onboarding", response_class=HTMLResponse)
async def api_update_onboarding(request: Request):
    db = get_db(request)
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None

    errors = validate_onboarding_form(form)
    if errors:
        return toast(errors[0], "error", body=_render_profile_status(request, errors[0], "error"))

    home_city = str(form.get("home_city", "")).strip()
    preferred_cities = normalize_city_list(
        str(form.get("preferred_cities", "")).strip(),
        fallback_home_city=home_city,
    )
    child_name = str(form.get("child_name", "")).strip()
    profile = build_interest_profile_from_form(
        form,
        home_city=home_city,
        preferred_cities=preferred_cities,
    )
    await db.update_user(
        user.id,
        home_city=home_city,
        preferred_cities=preferred_cities,
        child_name=child_name,
        interest_profile=profile,
        onboarding_complete=True,
    )
    return toast(
        "Child profile updated",
        body=_render_profile_status(request, "Child profile updated"),
    )


@router.post("/api/profile/theme", response_class=HTMLResponse)
async def api_update_theme(request: Request):
    db = get_db(request)
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    user_theme = user.theme
    theme = str(form.get("theme", user_theme)).strip()
    if theme not in ("light", "dark", "auto"):
        return toast(
            "Invalid theme",
            "error",
            status_code=422,
            body=_render_profile_status(request, "Invalid theme", "error"),
        )
    if theme == user_theme:
        return toast(
            "Theme already up to date",
            "info",
            body=_render_profile_status(request, "Theme already up to date", "info"),
        )
    await db.update_user(user.id, theme=theme)
    return change_theme(
        theme,
        body=_render_profile_status(
            request,
            f"Theme set to {theme.title() if theme != 'auto' else 'System'}",
        ),
    )


@router.post("/api/profile/notifications", response_class=HTMLResponse)
async def api_update_notifications(request: Request):
    db = get_db(request)
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    channels = form.getlist("channels")
    if not channels:
        channels = ["console"]
    email_to = str(form.get("email_to", "")).strip()
    sms_to = str(form.get("sms_to", "")).strip()
    child_name = str(form.get("child_name", "")).strip() or "Your Little One"
    if "email" in channels and not email_to:
        return toast(
            "Add a notification email to enable email delivery",
            "error",
            body=_render_profile_status(request, "Add a notification email to enable email delivery", "error"),
        )
    if "sms" in channels and not sms_to:
        return toast(
            "Add a phone number to enable SMS delivery",
            "error",
            body=_render_profile_status(request, "Add a phone number to enable SMS delivery", "error"),
        )
    await db.update_user(
        user.id,
        notification_channels=[str(channel) for channel in channels],
        email_to=email_to,
        sms_to=sms_to,
        child_name=child_name,
    )
    return toast(
        "Notification settings updated",
        body=_render_profile_status(request, "Notification settings updated"),
    )


@router.post("/api/profile/password", response_class=HTMLResponse)
async def api_update_password(request: Request):
    db = get_db(request)
    user, form, denied = await require_login_and_csrf(request)
    if denied:
        return denied
    assert user is not None and form is not None
    current = str(form.get("current_password", ""))
    new_pw = str(form.get("new_password", ""))
    confirm = str(form.get("confirm_password", ""))
    if not verify_password(current, user.password_hash):
        return toast(
            "Current password is incorrect",
            "error",
            body=_render_profile_status(request, "Current password is incorrect", "error"),
        )
    password_errors = validate_password(new_pw)
    if password_errors:
        return toast(
            password_errors[0],
            "error",
            body=_render_profile_status(request, password_errors[0], "error"),
        )
    if new_pw != confirm:
        return toast(
            "Passwords don't match",
            "error",
            body=_render_profile_status(request, "Passwords don't match", "error"),
        )
    await db.update_user(user.id, password_hash=hash_password(new_pw))
    return toast(
        "Password changed",
        body=_render_profile_status(request, "Password changed"),
    )


def _render_profile_status(request: Request, message: str, variant: str = "success") -> str:
    return template_response(
        request,
        "partials/_profile_status.html",
        {"request": request, "message": message, "variant": variant},
    ).body.decode()
