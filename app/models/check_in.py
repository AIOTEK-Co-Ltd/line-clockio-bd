from __future__ import annotations

import datetime
import enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    from app.models.employee import Employee


class CheckInType(str, enum.Enum):
    clock_in = "clock_in"
    clock_out = "clock_out"


class CheckIn(Base):
    __tablename__ = "check_ins"
    __table_args__ = (
        Index("ix_check_ins_employee_checked_at", "employee_id", "checked_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id"), nullable=False, index=True
    )
    type: Mapped[CheckInType] = mapped_column(Enum(CheckInType), nullable=False)
    checked_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    latitude: Mapped[float] = mapped_column(Float(53), nullable=False)  # DOUBLE PRECISION
    longitude: Mapped[float] = mapped_column(Float(53), nullable=False)  # DOUBLE PRECISION
    ip_address: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    employee: Mapped["Employee"] = relationship("Employee", back_populates="check_ins")
