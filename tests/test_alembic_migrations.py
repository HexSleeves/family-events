from __future__ import annotations

import hashlib
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_HISTORICAL_REVISION_SHA256 = {
    "3d7f85fe4c1a": "b56170e6b3ad66bc7ec364782e8589713919f3227fe975e95777ea47911c70d5",
    "91dae90b6493": "510e74f2695350e6a460027dbb01feca26abf8f91ec7ec5c8c51a8bc600e1e9d",
}


def _script_directory() -> ScriptDirectory:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return ScriptDirectory.from_config(config)


def _revision_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_historical_revisions_keep_expected_successors() -> None:
    script = _script_directory()
    expected_successors = {
        "91dae90b6493": "3d7f85fe4c1a",
        "3d7f85fe4c1a": "8c0f9f8b1f6b",
    }

    for revision_id, successor_id in expected_successors.items():
        revision = script.get_revision(revision_id)

        assert revision is not None
        assert successor_id in revision.nextrev


def test_historical_revision_sources_are_immutable() -> None:
    script = _script_directory()
    historical_revision_ids = sorted(
        revision.revision
        for revision in script.walk_revisions()
        if revision.nextrev
    )

    assert set(_HISTORICAL_REVISION_SHA256) == set(historical_revision_ids)
    for revision_id in historical_revision_ids:
        revision = script.get_revision(revision_id)

        assert revision is not None
        assert _revision_sha256(Path(revision.path)) == _HISTORICAL_REVISION_SHA256[revision_id]
