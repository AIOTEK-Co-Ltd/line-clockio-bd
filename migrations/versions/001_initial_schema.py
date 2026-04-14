"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-13
"""
import sqlalchemy as sa
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("line_user_id", sa.String(50), unique=True, nullable=False),
        sa.Column("email", sa.String(100), unique=True, nullable=False),
        sa.Column("display_name", sa.String(100)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_manager", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_employees_id", "employees", ["id"])
    op.create_index("ix_employees_line_user_id", "employees", ["line_user_id"])

    op.create_table(
        "check_ins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("type", sa.Enum("clock_in", "clock_out", name="checkintype"), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("latitude", sa.Float(precision=53), nullable=False),   # DOUBLE PRECISION
        sa.Column("longitude", sa.Float(precision=53), nullable=False),  # DOUBLE PRECISION
        sa.Column("ip_address", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_check_ins_id", "check_ins", ["id"])
    op.create_index(
        "ix_check_ins_employee_checked_at", "check_ins", ["employee_id", "checked_at"]
    )

    op.create_table(
        "email_verifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("line_user_id", sa.String(50), nullable=False),
        sa.Column("email", sa.String(100), nullable=False),
        sa.Column("otp_code", sa.String(6), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_email_verifications_id", "email_verifications", ["id"])
    op.create_index("ix_email_verifications_line_user_id", "email_verifications", ["line_user_id"])
    op.create_index("ix_email_verifications_email", "email_verifications", ["email"])


def downgrade() -> None:
    op.drop_index("ix_email_verifications_email", "email_verifications")
    op.drop_index("ix_email_verifications_line_user_id", "email_verifications")
    op.drop_index("ix_email_verifications_id", "email_verifications")
    op.drop_table("email_verifications")

    op.drop_index("ix_check_ins_employee_checked_at", "check_ins")
    op.drop_index("ix_check_ins_id", "check_ins")
    op.execute("DROP TYPE IF EXISTS checkintype")
    op.drop_table("check_ins")

    op.drop_index("ix_employees_line_user_id", "employees")
    op.drop_index("ix_employees_id", "employees")
    op.drop_table("employees")
