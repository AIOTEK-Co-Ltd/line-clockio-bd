from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class Manager(Base):
    """Manager authenticated via Google OAuth (company domain only)."""

    __tablename__ = "managers"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    google_email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    google_sub: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
