from __future__ import annotations

import datetime
import enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.check_in import CheckInType

if TYPE_CHECKING:
    from app.models.employee import Employee


class MakeupRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class MakeupRequest(Base):
    __tablename__ = "makeup_requests"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )
    # Reuse the existing checkintype PostgreSQL enum; create_type=False prevents
    # SQLAlchemy from issuing a redundant CREATE TYPE in migrations.
    type: Mapped[CheckInType] = mapped_column(
        Enum(CheckInType, name="checkintype", create_type=False), nullable=False
    )
    requested_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[MakeupRequestStatus] = mapped_column(
        Enum(MakeupRequestStatus, name="makeuprequeststatus"),
        nullable=False,
        server_default="pending",
    )
    reviewed_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    employee: Mapped["Employee"] = relationship("Employee", foreign_keys=[employee_id])
    reviewer: Mapped[Optional["Employee"]] = relationship(
        "Employee", foreign_keys=[reviewed_by]
    )
