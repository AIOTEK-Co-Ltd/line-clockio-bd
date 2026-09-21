from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.check_in import CheckIn, CheckInType
from app.services.time_utils import as_utc


class MakeupDayState(str, Enum):
    """Classification of an employee's punches on one local calendar date."""

    no_records = "no_records"
    missing_clock_in = "missing_clock_in"
    missing_clock_out = "missing_clock_out"
    complete = "complete"
    ambiguous = "ambiguous"


@dataclass(frozen=True)
class MakeupDayAssessment:
    """Punch records and the safe makeup suggestion derived from them."""

    state: MakeupDayState
    suggested_type: CheckInType | None
    records: tuple[CheckIn, ...]
    snapshot_token: str


def _snapshot_timestamp(record: CheckIn) -> str:
    """Return a canonical UTC timestamp for a record."""
    if record.checked_at is None:
        raise ValueError("CheckIn.checked_at must be set")
    return as_utc(record.checked_at).isoformat(timespec="microseconds")


def _snapshot_token(
    employee_id: int,
    local_date: date,
    records: tuple[CheckIn, ...],
) -> str:
    """Return a deterministic token for the assessment inputs."""
    snapshot_source = "\n".join(
        [f"employee={employee_id}|date={local_date.isoformat()}"]
        + [
            f"{record.id}|{record.type.value}|{_snapshot_timestamp(record)}"
            for record in records
        ]
    )
    return f"sha256:{hashlib.sha256(snapshot_source.encode()).hexdigest()}"


def _classify(records: tuple[CheckIn, ...]) -> tuple[MakeupDayState, CheckInType | None]:
    """Return the day state and safe suggested type for records."""
    clock_in_count = sum(record.type == CheckInType.clock_in for record in records)
    clock_out_count = sum(record.type == CheckInType.clock_out for record in records)
    if clock_in_count == 0 and clock_out_count == 0:
        return MakeupDayState.no_records, None
    if clock_in_count == 0 and clock_out_count == 1:
        return MakeupDayState.missing_clock_in, CheckInType.clock_in
    if clock_in_count == 1 and clock_out_count == 0:
        return MakeupDayState.missing_clock_out, CheckInType.clock_out
    if clock_in_count == 1 and clock_out_count == 1:
        return MakeupDayState.complete, None
    return MakeupDayState.ambiguous, None


def assess_makeup_day(
    db: Session,
    employee_id: int,
    local_date: date,
    tz: ZoneInfo,
) -> MakeupDayAssessment:
    """Classify one employee's punches within a timezone-aware local date."""
    start = datetime.combine(local_date, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(
        local_date + timedelta(days=1), time.min, tzinfo=tz
    ).astimezone(timezone.utc)
    records = tuple(
        db.query(CheckIn)
        .filter(
            CheckIn.employee_id == employee_id,
            CheckIn.checked_at >= start,
            CheckIn.checked_at < end,
        )
        .order_by(CheckIn.checked_at.asc(), CheckIn.id.asc())
        .all()
    )
    state, suggested_type = _classify(records)
    return MakeupDayAssessment(
        state=state,
        suggested_type=suggested_type,
        records=records,
        snapshot_token=_snapshot_token(employee_id, local_date, records),
    )


def _breaks_punch_order(
    records: tuple[CheckIn, ...],
    selected_type: CheckInType,
    requested_at: datetime,
) -> bool:
    """Return whether the makeup time falls outside the day's existing punch order.

    A clock_out before the earliest clock_in (or a clock_in after the latest
    clock_out) makes daily work time collapse to zero in compute_daily_summary().
    """
    requested_utc = as_utc(requested_at)
    if selected_type == CheckInType.clock_out:
        clock_ins = [
            as_utc(record.checked_at)
            for record in records
            if record.type == CheckInType.clock_in
        ]
        return bool(clock_ins) and requested_utc < min(clock_ins)
    clock_outs = [
        as_utc(record.checked_at)
        for record in records
        if record.type == CheckInType.clock_out
    ]
    return bool(clock_outs) and requested_utc > max(clock_outs)


def requires_exception_confirmation(
    assessment: MakeupDayAssessment,
    selected_type: CheckInType,
    requested_at: datetime,
) -> bool:
    """Return whether the selected type or time conflicts with the day assessment."""
    if assessment.state in (MakeupDayState.complete, MakeupDayState.ambiguous):
        return True
    if _breaks_punch_order(assessment.records, selected_type, requested_at):
        return True
    if assessment.suggested_type is None:
        return False
    return selected_type != assessment.suggested_type
