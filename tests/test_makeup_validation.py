from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.check_in import CheckIn, CheckInType
from app.models.employee import Employee
from app.models.makeup_request import MakeupRequest, MakeupRequestStatus
from app.services.makeup_validation import (
    MakeupDayState,
    assess_makeup_day,
    requires_exception_confirmation,
)


TZ = ZoneInfo("Asia/Taipei")
DAY = date(2026, 8, 26)


def _employee(db, email: str = "day-state@aiotek.com.tw") -> Employee:
    employee = Employee(email=email, is_active=True)
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee


def _punch(db, employee_id: int, punch_type: CheckInType, local_time: str) -> None:
    hour, minute = map(int, local_time.split(":"))
    checked_at = datetime(2026, 8, 26, hour, minute, tzinfo=TZ).astimezone(timezone.utc)
    db.add(
        CheckIn(
            employee_id=employee_id,
            type=punch_type,
            checked_at=checked_at,
            latitude=0.0,
            longitude=0.0,
            ip_address="test",
        )
    )
    db.commit()


def test_makeup_request_persists_guidance_audit(db):
    employee = _employee(db)
    request = MakeupRequest(
        employee_id=employee.id,
        type=CheckInType.clock_in,
        requested_at=datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc),
        reason="確認例外",
        status=MakeupRequestStatus.pending,
        day_state_at_submission=MakeupDayState.missing_clock_out.value,
        snapshot_token_at_submission="sha256:test-snapshot",
        system_suggested_type=CheckInType.clock_out,
        exception_confirmed=True,
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    assert request.day_state_at_submission == "missing_clock_out"
    assert request.snapshot_token_at_submission == "sha256:test-snapshot"
    assert request.system_suggested_type == CheckInType.clock_out
    assert request.exception_confirmed is True


@pytest.mark.parametrize(
    ("punches", "expected_state", "expected_suggestion"),
    [
        ([], MakeupDayState.no_records, None),
        (
            [(CheckInType.clock_out, "18:00")],
            MakeupDayState.missing_clock_in,
            CheckInType.clock_in,
        ),
        (
            [(CheckInType.clock_in, "09:00")],
            MakeupDayState.missing_clock_out,
            CheckInType.clock_out,
        ),
        (
            [(CheckInType.clock_in, "09:00"), (CheckInType.clock_out, "18:00")],
            MakeupDayState.complete,
            None,
        ),
        (
            [(CheckInType.clock_in, "09:00"), (CheckInType.clock_in, "09:25")],
            MakeupDayState.ambiguous,
            None,
        ),
    ],
)
def test_assess_makeup_day_states(db, punches, expected_state, expected_suggestion):
    employee = _employee(db)
    for punch_type, local_time in punches:
        _punch(db, employee.id, punch_type, local_time)

    result = assess_makeup_day(db, employee.id, DAY, TZ)

    assert result.state == expected_state
    assert result.suggested_type == expected_suggestion
    assert [record.checked_at for record in result.records] == sorted(
        record.checked_at for record in result.records
    )


def test_assess_makeup_day_uses_local_calendar_boundary(db):
    employee = _employee(db)
    _punch(db, employee.id, CheckInType.clock_in, "00:30")
    db.add(
        CheckIn(
            employee_id=employee.id,
            type=CheckInType.clock_out,
            checked_at=datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc),
            latitude=0.0,
            longitude=0.0,
            ip_address="next-day",
        )
    )
    db.commit()

    result = assess_makeup_day(db, employee.id, DAY, TZ)

    assert result.state == MakeupDayState.missing_clock_out
    assert len(result.records) == 1


def test_requires_exception_only_for_override_or_unsafe_state(db):
    employee = _employee(db)
    _punch(db, employee.id, CheckInType.clock_in, "09:25")
    assessment = assess_makeup_day(db, employee.id, DAY, TZ)

    assert requires_exception_confirmation(
        assessment, CheckInType.clock_out, _utc("18:00")
    ) is False
    assert requires_exception_confirmation(
        assessment, CheckInType.clock_in, _utc("09:00")
    ) is True


def test_snapshot_changes_when_ambiguous_records_change(db):
    employee = _employee(db)
    _punch(db, employee.id, CheckInType.clock_in, "09:00")
    _punch(db, employee.id, CheckInType.clock_in, "09:25")
    before = assess_makeup_day(db, employee.id, DAY, TZ)

    _punch(db, employee.id, CheckInType.clock_out, "18:00")
    after = assess_makeup_day(db, employee.id, DAY, TZ)

    assert before.state == after.state == MakeupDayState.ambiguous
    assert before.snapshot_token != after.snapshot_token


def test_empty_snapshot_is_bound_to_employee_and_date(db):
    first = _employee(db)
    second = _employee(db, "day-state-second@aiotek.com.tw")

    first_day = assess_makeup_day(db, first.id, DAY, TZ)
    next_day = assess_makeup_day(db, first.id, date(2026, 8, 27), TZ)
    other_employee = assess_makeup_day(db, second.id, DAY, TZ)

    assert len(
        {
            first_day.snapshot_token,
            next_day.snapshot_token,
            other_employee.snapshot_token,
        }
    ) == 3


def _utc(local_time: str) -> datetime:
    """Return the UTC instant for a local time on DAY."""
    hour, minute = map(int, local_time.split(":"))
    return datetime(2026, 8, 26, hour, minute, tzinfo=TZ).astimezone(timezone.utc)


def test_requires_exception_when_clock_out_precedes_existing_clock_in(db):
    employee = _employee(db)
    _punch(db, employee.id, CheckInType.clock_in, "09:00")
    assessment = assess_makeup_day(db, employee.id, DAY, TZ)

    assert requires_exception_confirmation(
        assessment, CheckInType.clock_out, _utc("06:00")
    ) is True
    assert requires_exception_confirmation(
        assessment, CheckInType.clock_out, _utc("18:00")
    ) is False


def test_requires_exception_when_clock_in_follows_existing_clock_out(db):
    employee = _employee(db)
    _punch(db, employee.id, CheckInType.clock_out, "18:00")
    assessment = assess_makeup_day(db, employee.id, DAY, TZ)

    assert requires_exception_confirmation(
        assessment, CheckInType.clock_in, _utc("20:00")
    ) is True
    assert requires_exception_confirmation(
        assessment, CheckInType.clock_in, _utc("09:00")
    ) is False


def test_order_guard_uses_earliest_in_and_latest_out(db):
    """Boundary punches, not arbitrary ones, define the valid window."""
    employee = _employee(db)
    _punch(db, employee.id, CheckInType.clock_in, "09:00")
    _punch(db, employee.id, CheckInType.clock_in, "13:00")
    assessment = assess_makeup_day(db, employee.id, DAY, TZ)

    # ambiguous already forces confirmation; the guard must not relax it
    assert requires_exception_confirmation(
        assessment, CheckInType.clock_out, _utc("18:00")
    ) is True
