"""makeup_requests table

Revision ID: 003
Revises: 002
Create Date: 2026-04-27
"""
import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE makeuprequeststatus AS ENUM ('pending', 'approved', 'rejected')"
    )

    op.create_table(
        "makeup_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id"),
            nullable=False,
        ),
        sa.Column(
            "type",
            sa.Enum("clock_in", "clock_out", name="checkintype", create_type=False),
            nullable=False,
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                name="makeuprequeststatus",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "reviewed_by",
            sa.Integer(),
            sa.ForeignKey("employees.id"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_makeup_requests_id", "makeup_requests", ["id"])
    op.create_index(
        "ix_makeup_requests_employee_id", "makeup_requests", ["employee_id"]
    )
    op.create_index("ix_makeup_requests_status", "makeup_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_makeup_requests_status", "makeup_requests")
    op.drop_index("ix_makeup_requests_employee_id", "makeup_requests")
    op.drop_index("ix_makeup_requests_id", "makeup_requests")
    op.drop_table("makeup_requests")
    op.execute("DROP TYPE IF EXISTS makeuprequeststatus")
