"""Tests for app/routers/dashboard.py — pure-function coverage."""

from app.routers.dashboard import _csv_safe


# ── _csv_safe ─────────────────────────────────────────────────────────────────

def test_csv_safe_none_returns_empty():
    assert _csv_safe(None) == ""


def test_csv_safe_empty_string():
    assert _csv_safe("") == ""


def test_csv_safe_normal_string_unchanged():
    assert _csv_safe("Hello World") == "Hello World"


def test_csv_safe_leading_equals_prefixed():
    assert _csv_safe("=SUM(A1:B2)") == "'=SUM(A1:B2)"


def test_csv_safe_leading_plus_prefixed():
    assert _csv_safe("+1234") == "'+1234"


def test_csv_safe_leading_minus_prefixed():
    assert _csv_safe("-DROP TABLE") == "'-DROP TABLE"


def test_csv_safe_leading_at_prefixed():
    assert _csv_safe("@user") == "'@user"
