"""Authentication routes."""

from __future__ import annotations

from urllib.parse import quote

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
    get_current_user_or_redirect,
    get_db,
    htmx_redirect_or_redirect,
    require_csrf,
    require_login_and_csrf,
    template_response,
)

router = APIRouter()


async def _start_signup_scrape_tag_job(*, database_url: str) -> None:
    from src.db.database import create_database
    from src.scheduler import ensure_system_user, run_scrape_then_tag
    from src.web.jobs import job_registry

    async def runner(job) -> dict[str, int | str]:
        async with create_database(database_url=database_url) as job_db:
            await job.update(
                detail="Preparing scrape + tag run…",
                result={
                    "phase": "scrape",
                    "processed": 0,
                    "total": 2,
                    "summary": "Scraping sources…",
                },
            )
            return await run_scrape_then_tag(
                job_db,
                include_stale=False,
                progress_callback=lambda progress: job.update(
                    detail=progress.get("summary", "Running…"), result=progress
                ),
            )

    async with create_database(database_url=database_url) as db:
        system_user = await ensure_system_user(db)

    await job_registry.start_unique(
        kind="pipeline",
        job_key="pipeline:scrape-tag",
        label="Scrape + tag job",
        owner_user_id=system_user.id,
        source_id=None,
        runner=runner,
        database_url=database_url,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user, _redirect = await get_current_user_or_redirect(request, "/profile")
    if user:
        return htmx_redirect_or_redirect(request, "/profile")
    return template_response(
        request,
        "login.html",
        await ctx(
            request,
            active_page="auth",
            email=str(request.query_params.get("email", "")).strip().lower(),
        ),
    )


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
        return template_response(
            request,
            "login.html",
            {
                **await ctx(request, active_page="auth"),
                "error": "Invalid email or password.",
                "email": email,
            },
            status_code=200,
        )

    login_session(request, user)
    rotate_csrf_token(request)
    return htmx_redirect_or_redirect(request, "/profile")


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    user, _redirect = await get_current_user_or_redirect(request, "/profile")
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
    existing_account_login_url = ""

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
        errors.append("An account with this email already exists. Log in instead.")
        existing_account_login_url = f"/login?email={quote(email)}"

    if errors:
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
                "existing_account_login_url": existing_account_login_url,
            },
            status_code=200,
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
    predefined_source_keys = [
        value for value in form.getlist("predefined_sources") if isinstance(value, str)
    ]
    selected_source_keys = predefined_source_keys or recommended_source_keys_for_city(home_city)
    await ensure_predefined_sources(
        db,
        user=user,
        source_keys=selected_source_keys,
    )
    if selected_source_keys:
        await _start_signup_scrape_tag_job(database_url=db.database_url)
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
