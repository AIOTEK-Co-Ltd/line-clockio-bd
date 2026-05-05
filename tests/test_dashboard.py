"""Tests for app/routers/dashboard.py — pure-function and endpoint coverage."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.models.check_in import CheckIn, CheckInType
from app.models.employee import Employee
from app.routers.dashboard import _csv_safe


# ── Helpers ───────────────────────────────────────────────────────────────────

def _add_manager(db) -> Employee:
    emp = Employee(
        email="manager@aiotek.com.tw",
        line_user_id="Umanager",
        display_name="Manager",
        is_active=True,
        is_manager=True,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _add_employee_with_card(db, email: str, card: str) -> Employee:
    emp = Employee(
        email=email,
        line_user_id=f"U{email}",
        display_name=email,
        card_number=card,
        is_active=True,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _add_checkin(db, employee_id: int, ctype: CheckInType, checked_at: datetime) -> CheckIn:
    ci = CheckIn(
        employee_id=employee_id,
        type=ctype,
        latitude=25.0,
        longitude=121.0,
        ip_address="127.0.0.1",
    )
    db.add(ci)
    db.flush()
    ci.checked_at = checked_at
    db.commit()
    db.refresh(ci)
    return ci


def _mock_settings(machine_id: str = "0000000005") -> MagicMock:
    s = MagicMock()
    s.timezone = "Asia/Taipei"
    s.factory_machine_id = machine_id
    return s


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


# ── GET /dashboard/export/factory ─────────────────────────────────────────────

def test_factory_export_unauthenticated_redirects(client):
    """Unauthenticated request is redirected to login."""
    resp = client.get("/dashboard/export/factory", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "/dashboard/login" in resp.headers["location"]


def test_factory_export_returns_txt(client, db):
    """Authenticated manager gets a text/plain file download."""
    emp = _add_employee_with_card(db, "bob@aiotek.com.tw", "A1234567")
    ts = datetime(2026, 5, 1, 1, 0, 0, tzinfo=timezone.utc)  # 09:00 UTC+8
    _add_checkin(db, emp.id, CheckInType.clock_in, ts)

    settings = _mock_settings()
    with patch("app.routers.dashboard._is_manager", return_value=True), \
         patch("app.routers.dashboard.get_settings", return_value=settings):
        resp = client.get("/dashboard/export/factory")

    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    assert ".txt" in resp.headers["content-disposition"]


def test_factory_export_line_format(client, db):
    """Each line matches machine_id,card,YYYY/MM/DD,HH:MM:SS format."""
    emp = _add_employee_with_card(db, "carol@aiotek.com.tw", "B2345678")
    ts = datetime(2026, 5, 1, 1, 30, 0, tzinfo=timezone.utc)
    _add_checkin(db, emp.id, CheckInType.clock_in, ts)

    settings = _mock_settings("0000000005")
    with patch("app.routers.dashboard._is_manager", return_value=True), \
         patch("app.routers.dashboard.get_settings", return_value=settings):
        resp = client.get("/dashboard/export/factory")

    lines = [ln for ln in resp.text.strip().splitlines() if ln]
    assert len(lines) == 1
    parts = lines[0].split(",")
    assert parts[0] == "0000000005"
    assert parts[1] == "B2345678"
    # YYYY/MM/DD
    date_parts = parts[2].split("/")
    assert len(date_parts) == 3 and len(date_parts[0]) == 4
    # HH:MM:SS
    time_parts = parts[3].split(":")
    assert len(time_parts) == 3 and all(len(p) == 2 for p in time_parts)


def test_factory_export_uses_settings_machine_id(client, db):
    """Machine ID comes from settings, not a hardcoded constant."""
    emp = _add_employee_with_card(db, "dave@aiotek.com.tw", "C3456789")
    ts = datetime(2026, 5, 1, 1, 0, 0, tzinfo=timezone.utc)
    _add_checkin(db, emp.id, CheckInType.clock_in, ts)

    settings = _mock_settings(machine_id="9999999999")
    with patch("app.routers.dashboard._is_manager", return_value=True), \
         patch("app.routers.dashboard.get_settings", return_value=settings):
        resp = client.get("/dashboard/export/factory")

    assert resp.text.startswith("9999999999,")


def test_factory_export_excludes_employees_without_card(client, db):
    """Employees without a card number are silently excluded from the export."""
    emp_no_card = Employee(
        email="nocard@aiotek.com.tw",
        line_user_id="Unocard",
        is_active=True,
    )
    db.add(emp_no_card)
    db.commit()
    db.refresh(emp_no_card)
    ts = datetime(2026, 5, 1, 1, 0, 0, tzinfo=timezone.utc)
    _add_checkin(db, emp_no_card.id, CheckInType.clock_in, ts)

    settings = _mock_settings()
    with patch("app.routers.dashboard._is_manager", return_value=True), \
         patch("app.routers.dashboard.get_settings", return_value=settings):
        resp = client.get("/dashboard/export/factory")

    assert resp.status_code == 200
    assert resp.text == ""


def test_factory_export_empty_file_when_no_records(client, db):
    """Export with no matching records returns an empty file body."""
    # Prime the session in the main thread so the portal thread reuses the same connection.
    db.execute(__import__("sqlalchemy").text("SELECT 1"))
    settings = _mock_settings()
    with patch("app.routers.dashboard._is_manager", return_value=True), \
         patch("app.routers.dashboard.get_settings", return_value=settings):
        resp = client.get("/dashboard/export/factory")

    assert resp.status_code == 200
    assert resp.text == ""
