from datetime import UTC, datetime, timedelta

from src.db.database import create_database
from src.db.models import Job, User
from src.web.auth import hash_password

TEST_OWNER_USER_ID = "00000000-0000-0000-0000-000000000001"
TEST_SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000002"


async def _create_test_user(db, *, user_id: str, email: str) -> None:
    await db.create_user(
        User(
            id=user_id,
            email=email,
            display_name=email.split("@", 1)[0],
            password_hash=hash_password("Password123"),
        )
    )


def test_fail_stale_jobs_marks_old_running_jobs_failed(isolated_postgres_database_url: str):
    async def scenario() -> None:
        db = create_database(database_url=isolated_postgres_database_url)
        await db.connect()
        try:
            await _create_test_user(db, user_id=TEST_OWNER_USER_ID, email="jobs-owner@example.com")
            stale_job = Job(
                kind="tag",
                job_key="pipeline:tag",
                label="Tag job",
                owner_user_id=TEST_OWNER_USER_ID,
                state="running",
                created_at=datetime.now(tz=UTC) - timedelta(hours=3),
                started_at=datetime.now(tz=UTC) - timedelta(hours=3),
            )
            fresh_job = Job(
                kind="scrape",
                job_key="pipeline:scrape",
                label="Scrape job",
                owner_user_id=TEST_OWNER_USER_ID,
                state="running",
                created_at=datetime.now(tz=UTC),
                started_at=datetime.now(tz=UTC),
            )
            await db.create_job(stale_job)
            await db.create_job(fresh_job)

            updated = await db.fail_stale_jobs(max_age_seconds=3600)

            assert updated == 1
            stale = await db.get_job(stale_job.id)
            fresh = await db.get_job(fresh_job.id)
            assert stale is not None
            assert stale.state == "failed"
            assert stale.finished_at is not None
            assert "max runtime" in stale.error or "worker stopped unexpectedly" in stale.error
            assert fresh is not None
            assert fresh.state == "running"
        finally:
            await db.close()

    import asyncio

    asyncio.run(scenario())


def test_job_progress_property_parses_result_json():
    job = Job(
        kind="tag",
        job_key="pipeline:tag",
        label="Tag job",
        owner_user_id=TEST_OWNER_USER_ID,
        result_json='{"processed": 10, "total": 25, "summary": "10/25 processed"}',
    )

    assert job.progress == {
        "processed": 10,
        "total": 25,
        "summary": "10/25 processed",
    }


def test_cancel_running_job_marks_it_cancelled(
    isolated_postgres_database_url: str,
    monkeypatch,
):
    async def scenario() -> None:
        db = create_database(database_url=isolated_postgres_database_url)
        await db.connect()
        try:
            await _create_test_user(db, user_id=TEST_OWNER_USER_ID, email="jobs-owner@example.com")
            job = Job(
                kind="tag",
                job_key="pipeline:tag",
                label="Tag job",
                owner_user_id=TEST_OWNER_USER_ID,
                state="running",
            )
            await db.create_job(job)
        finally:
            await db.close()

        import src.web.jobs as jobs_module
        from src.web.jobs import job_registry

        monkeypatch.setattr(
            jobs_module,
            "Database",
            lambda: create_database(database_url=isolated_postgres_database_url),
        )

        updated = await job_registry.cancel(job_id=job.id, owner_user_id=TEST_OWNER_USER_ID)
        assert updated is not None
        assert updated.state == "cancelled"
        assert updated.detail == "Cancelled"
        assert updated.error == "Cancelled by user"
        assert updated.finished_at is not None

    import asyncio

    asyncio.run(scenario())


def test_list_jobs_supports_system_owned_records(isolated_postgres_database_url: str):
    async def scenario() -> None:
        db = create_database(database_url=isolated_postgres_database_url)
        await db.connect()
        try:
            await _create_test_user(
                db, user_id=TEST_SYSTEM_USER_ID, email="jobs-system@example.com"
            )
            system_job = Job(
                kind="pipeline",
                job_key="scheduled:pipeline:scrape-tag",
                label="Scheduled scrape + tag job",
                owner_user_id=TEST_SYSTEM_USER_ID,
                state="succeeded",
            )
            await db.create_job(system_job)

            jobs = await db.list_jobs(owner_user_id=TEST_SYSTEM_USER_ID, limit=10)
            kinds = await db.list_job_kinds(owner_user_id=TEST_SYSTEM_USER_ID)

            assert len(jobs) == 1
            assert jobs[0].label == "Scheduled scrape + tag job"
            assert kinds == ["pipeline"]
        finally:
            await db.close()

    import asyncio

    asyncio.run(scenario())


def test_recover_stale_jobs_marks_old_running_jobs_failed(
    isolated_postgres_database_url: str,
    monkeypatch,
):
    async def scenario() -> None:
        db = create_database(database_url=isolated_postgres_database_url)
        await db.connect()
        try:
            await _create_test_user(db, user_id=TEST_OWNER_USER_ID, email="jobs-owner@example.com")
            stale_job = Job(
                kind="pipeline",
                job_key="pipeline:scrape-tag",
                label="Scrape + tag job",
                owner_user_id=TEST_OWNER_USER_ID,
                state="running",
                created_at=datetime.now(tz=UTC) - timedelta(hours=2),
                started_at=datetime.now(tz=UTC) - timedelta(hours=2),
            )
            await db.create_job(stale_job)
        finally:
            await db.close()

        import src.web.jobs as jobs_module
        from src.web.jobs import JobRegistry

        monkeypatch.setattr(
            jobs_module,
            "Database",
            lambda: create_database(database_url=isolated_postgres_database_url),
        )
        registry = JobRegistry()

        updated = await registry.recover_stale_jobs()
        assert updated == 1

        async with create_database(database_url=isolated_postgres_database_url) as check_db:
            recovered = await check_db.get_job(stale_job.id)
            assert recovered is not None
            assert recovered.state == "failed"
            assert recovered.finished_at is not None

    import asyncio

    asyncio.run(scenario())


def test_start_unique_reuses_existing_running_job_without_creating_duplicate(
    isolated_postgres_database_url: str,
    monkeypatch,
):
    async def scenario() -> None:
        db = create_database(database_url=isolated_postgres_database_url)
        await db.connect()
        try:
            await _create_test_user(db, user_id=TEST_OWNER_USER_ID, email="jobs-owner@example.com")
            existing_job = Job(
                kind="pipeline",
                job_key="pipeline:scrape-tag",
                label="Scrape + tag job",
                owner_user_id=TEST_OWNER_USER_ID,
                state="running",
                detail="Running…",
            )
            await db.create_job(existing_job)
        finally:
            await db.close()

        import src.web.jobs as jobs_module
        from src.web.jobs import JobRegistry

        monkeypatch.setattr(
            jobs_module,
            "Database",
            lambda: create_database(database_url=isolated_postgres_database_url),
        )
        registry = JobRegistry()

        async def runner(_job):
            raise AssertionError("runner should not execute when duplicate job exists")

        job, created = await registry.start_unique(
            kind="pipeline",
            job_key="pipeline:scrape-tag",
            label="Scrape + tag job",
            owner_user_id=TEST_OWNER_USER_ID,
            source_id=None,
            runner=runner,
        )

        assert created is False
        assert job.id == existing_job.id

        async with create_database(database_url=isolated_postgres_database_url) as check_db:
            jobs = await check_db.list_jobs(owner_user_id=TEST_OWNER_USER_ID, limit=10)
            assert len(jobs) == 1
            assert jobs[0].id == existing_job.id
            assert jobs[0].state == "running"

    import asyncio

    asyncio.run(scenario())
