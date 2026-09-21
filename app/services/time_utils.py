"""Shared timezone helpers."""

from __future__ import annotations

from datetime import datetime, timezone


def as_utc(value: datetime) -> datetime:
    """Return value as UTC, treating naive datetimes as UTC.

    SQLite returns naive values for timezone-aware columns, so callers that
    compare or convert stored timestamps must normalise first — otherwise
    astimezone() would interpret them in the host machine's local timezone.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
