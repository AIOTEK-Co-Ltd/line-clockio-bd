"""Unit tests for the overtime calculation service (app/services/overtime.py).

All times below are GROSS (raw clock-in to clock-out).  The service deducts
LUNCH_DEDUCTION_MINUTES (60 min) before computing net work time and overtime,
so an 8 h net workday requires 9 h gross (e.g. 09:00 → 18:00).

All tests use plain CheckIn-like objects — no DB required.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo


from app.services.overtime import (
    LUNCH_DEDUCTION_MINUTES,
    MONTHLY_OT_LIMIT,
    compute_daily_summary,
    compute_monthly_summaries,
    monthly_ot_total,
)

TZ = ZoneInfo("Asia/Taipei")


def _dt(hour: int, minute: int = 0, day: int = 1) -> datetime:
    """Return a timezone-aware datetime in Asia/Taipei on 2026-04-{day}."""
    return datetime(2026, 4, day, hour, minute, tzinfo=TZ)


class _FakeCheckIn:
    """Minimal stand-in for CheckIn ORM row."""
    def __init__(self, type_, checked_at: datetime):
        self.type = type_
        self.checked_at = checked_at


def _make(type_str: str, hour: int, minute: int = 0, day: int = 1) -> _FakeCheckIn:
    from app.models.check_in import CheckInType
    return _FakeCheckIn(CheckInType(type_str), _dt(hour, minute, day))


# ── compute_daily_summary ──────────────────────────────────────────────────────

def test_lunch_deduction_constant():
    assert LUNCH_DEDUCTION_MINUTES == 60


def test_no_records_returns_zero():
    s = compute_daily_summary(date(2026, 4, 1), [])
    assert s.work_minutes == 0
    assert s.ot_counted_minutes == 0
    assert not s.in_progress
    assert not s.exceeds_legal_limit


def test_only_clock_in_is_in_progress():
    records = [_make("clock_in", 9)]
    s = compute_daily_summary(date(2026, 4, 1), records)
    assert s.in_progress
    assert s.work_minutes == 0
    assert s.ot_counted_minutes == 0


def test_only_clock_out_no_work():
    records = [_make("clock_out", 18)]
    s = compute_daily_summary(date(2026, 4, 1), records)
    assert not s.in_progress
    assert s.work_minutes == 0


def test_exactly_8_hours_net_no_overtime():
    # 09:00 → 18:00 = 9 h gross → 8 h net, no OT
    records = [_make("clock_in", 9), _make("clock_out", 18)]
    s = compute_daily_summary(date(2026, 4, 1), records)
    assert s.work_minutes == 480
    assert s.ot_counted_minutes == 0
    assert s.regular_minutes == 480


def test_under_30_min_overtime_not_counted():
    # 09:00 → 18:29 = 9 h 29 m gross → 8 h 29 m net → 29 min OT raw, not counted
    records = [_make("clock_in", 9), _make("clock_out", 18, 29)]
    s = compute_daily_summary(date(2026, 4, 1), records)
    assert s.ot_counted_minutes == 0
    assert s.ot_remainder_minutes == 29


def test_exactly_30_min_overtime_counts():
    # 09:00 → 18:30 = 9 h 30 m gross → 8 h 30 m net → 30 min tier-1 OT
    records = [_make("clock_in", 9), _make("clock_out", 18, 30)]
    s = compute_daily_summary(date(2026, 4, 1), records)
    assert s.ot_counted_minutes == 30
    assert s.ot_tier1_minutes == 30
    assert s.ot_tier2_minutes == 0
    assert s.ot_remainder_minutes == 0


def test_90_min_overtime_all_tier1():
    # 09:00 → 19:30 = 10 h 30 m gross → 9 h 30 m net → 90 min tier-1
    records = [_make("clock_in", 9), _make("clock_out", 19, 30)]
    s = compute_daily_summary(date(2026, 4, 1), records)
    assert s.ot_tier1_minutes == 90
    assert s.ot_tier2_minutes == 0
    assert s.ot_counted_minutes == 90


def test_tier1_full_and_tier2_starts():
    # 09:00 → 20:30 = 11 h 30 m gross → 10 h 30 m net → 120 min tier-1 + 30 min tier-2
    records = [_make("clock_in", 9), _make("clock_out", 20, 30)]
    s = compute_daily_summary(date(2026, 4, 1), records)
    assert s.ot_tier1_minutes == 120
    assert s.ot_tier2_minutes == 30
    assert s.ot_counted_minutes == 150


def test_remainder_dropped_when_tier2_partially_filled():
    # 09:00 → 20:45 = 11 h 45 m gross → 10 h 45 m net
    # OT raw=165, counted=150 (120 t1 + 30 t2), remainder=15
    records = [_make("clock_in", 9), _make("clock_out", 20, 45)]
    s = compute_daily_summary(date(2026, 4, 1), records)
    assert s.ot_tier1_minutes == 120
    assert s.ot_tier2_minutes == 30
    assert s.ot_counted_minutes == 150
    assert s.ot_remainder_minutes == 15


def test_exceeds_legal_limit():
    # 09:00 → 23:01 = 14 h 1 m gross → 13 h 1 m net → OT raw=241 > 240 (4 h)
    records = [_make("clock_in", 9), _make("clock_out", 23, 1)]
    s = compute_daily_summary(date(2026, 4, 1), records)
    assert s.exceeds_legal_limit


def test_exactly_12_hours_net_not_exceeded():
    # 09:00 → 22:00 = 13 h gross → 12 h net → OT raw=240 == limit, not exceeded
    records = [_make("clock_in", 9), _make("clock_out", 22)]
    s = compute_daily_summary(date(2026, 4, 1), records)
    assert not s.exceeds_legal_limit


def test_multiple_punches_uses_first_in_last_out():
    # Extra clock_in at 10 h ignored; span is 09:00 → 19:00 = 10 h gross → 9 h net
    records = [
        _make("clock_in", 9),
        _make("clock_in", 10),
        _make("clock_out", 18),
        _make("clock_out", 19),
    ]
    s = compute_daily_summary(date(2026, 4, 1), records)
    assert s.work_minutes == 540  # 9 h net


# ── compute_monthly_summaries ──────────────────────────────────────────────────

def test_monthly_groups_by_date():
    records = [
        _make("clock_in",  9,  0, day=1),
        _make("clock_out", 18, 0, day=1),
        _make("clock_in",  8, 30, day=2),
        _make("clock_out", 17, 30, day=2),
    ]
    summaries = compute_monthly_summaries(records, TZ)
    assert len(summaries) == 2
    # sorted descending
    assert summaries[0].date > summaries[1].date


def test_monthly_total():
    # day1: 09:00 → 18:30 = 9 h 30 m gross → 8 h 30 m net → 30 min OT
    # day2: 09:00 → 19:00 = 10 h gross → 9 h net → 60 min OT
    records = [
        _make("clock_in",  9,  0, day=1),
        _make("clock_out", 18, 30, day=1),
        _make("clock_in",  9,  0, day=2),
        _make("clock_out", 19,  0, day=2),
    ]
    summaries = compute_monthly_summaries(records, TZ)
    assert monthly_ot_total(summaries) == 90


def test_monthly_ot_limit_constant():
    assert MONTHLY_OT_LIMIT == 2760  # 46 h × 60
