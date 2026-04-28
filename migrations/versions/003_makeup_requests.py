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
    # Use native_enum=False (VARCHAR) for both enum columns to avoid conflicts
    # with existing PostgreSQL enum types. Values are validated at the application layer.
    op.create_table(
        "makeup_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id"),
            nullable=False,
        ),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
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
    # Partial unique index: prevents duplicate pending requests for the same punch slot.
    # The WHERE clause is PostgreSQL-specific; SQLite silently degrades to a regular
    # unique index (acceptable — test env uses SQLite, production uses PostgreSQL).
    op.create_index(
        "ux_makeup_pending_slot",
        "makeup_requests",
        ["employee_id", "type", "requested_at"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ux_makeup_pending_slot", "makeup_requests")
    op.drop_index("ix_makeup_requests_status", "makeup_requests")
    op.drop_index("ix_makeup_requests_employee_id", "makeup_requests")
    op.drop_index("ix_makeup_requests_id", "makeup_requests")
    op.drop_table("makeup_requests")
