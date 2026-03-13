"""add user event state followup migration

Revision ID: 3d7f85fe4c1a
Revises: 91dae90b6493
Create Date: 2026-03-13 16:10:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3d7f85fe4c1a"
down_revision: Union[str, Sequence[str], None] = "91dae90b6493"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

uuid = postgresql.UUID(as_uuid=False)


def _relation_exists(name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(sa.text("SELECT to_regclass(:name)"), {"name": name})
    return result.scalar_one_or_none() is not None


def upgrade() -> None:
    """Upgrade schema."""
    if not _relation_exists("user_event_state"):
        op.create_table(
            "user_event_state",
            sa.Column("user_id", uuid, nullable=False),
            sa.Column("event_id", uuid, nullable=False),
            sa.Column("saved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("attended", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("attended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "event_id", name="uq_user_event_state_user_event"),
        )

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_event_state_flags "
        "ON user_event_state (user_id, saved, attended)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_event_state_updated "
        "ON user_event_state (user_id, updated_at)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS idx_user_event_state_updated")
    op.execute("DROP INDEX IF EXISTS idx_user_event_state_flags")
    op.execute("DROP TABLE IF EXISTS user_event_state")
