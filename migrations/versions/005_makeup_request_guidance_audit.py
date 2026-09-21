"""add makeup request guidance audit

Revision ID: 005
Revises: 004
"""

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "makeup_requests",
        sa.Column("day_state_at_submission", sa.String(32), nullable=True),
    )
    op.add_column(
        "makeup_requests",
        sa.Column("snapshot_token_at_submission", sa.String(80), nullable=True),
    )
    op.add_column(
        "makeup_requests",
        sa.Column("system_suggested_type", sa.String(20), nullable=True),
    )
    op.add_column(
        "makeup_requests",
        sa.Column(
            "exception_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("makeup_requests", "exception_confirmed")
    op.drop_column("makeup_requests", "system_suggested_type")
    op.drop_column("makeup_requests", "snapshot_token_at_submission")
    op.drop_column("makeup_requests", "day_state_at_submission")
