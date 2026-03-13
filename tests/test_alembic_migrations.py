from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def _script_directory() -> ScriptDirectory:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return ScriptDirectory.from_config(config)


def test_user_event_state_change_has_followup_revision() -> None:
    script = _script_directory()

    assert script.get_current_head() != "91dae90b6493"
    assert any(
        revision.down_revision == "91dae90b6493"
        for revision in script.walk_revisions()
    )
