from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.check_in import CheckIn, CheckInType
from app.models.employee import Employee
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

    assert requires_exception_confirmation(assessment, CheckInType.clock_out) is False
    assert requires_exception_confirmation(assessment, CheckInType.clock_in) is True


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
