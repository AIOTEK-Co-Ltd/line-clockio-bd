"""OTP hashing and brute-force lockout

Revision ID: 002
Revises: 001
Create Date: 2026-04-16

Changes:
- email_verifications.otp_code  VARCHAR(6) → VARCHAR(64)  (stores SHA-256 hex digest)
- email_verifications.failed_attempts  INTEGER DEFAULT 0 NOT NULL  (lockout counter)
"""
import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "email_verifications",
        "otp_code",
        existing_type=sa.String(6),
        type_=sa.String(64),
        nullable=False,
    )
    op.add_column(
        "email_verifications",
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("email_verifications", "failed_attempts")
    op.alter_column(
        "email_verifications",
        "otp_code",
        existing_type=sa.String(64),
        type_=sa.String(6),
        nullable=False,
    )
