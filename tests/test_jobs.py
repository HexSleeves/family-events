from datetime import UTC, datetime, timedelta

from src.db.database import create_database
from src.db.models import Job


def test_fail_stale_jobs_marks_old_running_jobs_failed(tmp_path):
    async def scenario() -> None:
        db = create_database(str(tmp_path / "jobs.db"))
        await db.connect()
        try:
            stale_job = Job(
                kind="tag",
                job_key="pipeline:tag",
                label="Tag job",
                owner_user_id="user-1",
                state="running",
                created_at=datetime.now(tz=UTC) - timedelta(hours=3),
                started_at=datetime.now(tz=UTC) - timedelta(hours=3),
            )
            fresh_job = Job(
                kind="scrape",
                job_key="pipeline:scrape",
                label="Scrape job",
                owner_user_id="user-1",
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
        owner_user_id="user-1",
        result_json='{"processed": 10, "total": 25, "summary": "10/25 processed"}',
    )

    assert job.progress == {
        "processed": 10,
        "total": 25,
        "summary": "10/25 processed",
    }


def test_cancel_running_job_marks_it_cancelled(tmp_path, monkeypatch):
    async def scenario() -> None:
        db_path = str(tmp_path / "jobs-cancel.db")
        db = create_database(db_path)
        await db.connect()
        try:
            job = Job(
                kind="tag",
                job_key="pipeline:tag",
                label="Tag job",
                owner_user_id="user-1",
                state="running",
            )
            await db.create_job(job)
        finally:
            await db.close()

        import src.web.jobs as jobs_module
        from src.web.jobs import job_registry

        monkeypatch.setattr(jobs_module, "Database", lambda: create_database(db_path))

        updated = await job_registry.cancel(job_id=job.id, owner_user_id="user-1")
        assert updated is not None
        assert updated.state == "cancelled"
        assert updated.detail == "Cancelled"
        assert updated.error == "Cancelled by user"
        assert updated.finished_at is not None

    import asyncio

    asyncio.run(scenario())
