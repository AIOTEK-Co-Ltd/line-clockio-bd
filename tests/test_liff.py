"""Tests for app/routers/liff.py — page serving and Pydantic model validation."""

import pytest
from datetime import date, datetime, timedelta, timezone
from pydantic import ValidationError
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from app.models.check_in import CheckIn, CheckInType
from app.models.employee import Employee
from app.models.makeup_request import MakeupRequest, MakeupRequestStatus
from app.routers.liff import CheckInRequest
from app.services.makeup_validation import assess_makeup_day

LINE_UID = "Uabc1234567890abcdef"


def _add_employee(db, display_name: str = "Alice") -> Employee:
    emp = Employee(
        email="alice@aiotek.com.tw",
        line_user_id=LINE_UID,
        display_name=display_name,
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
    # Override the server-default timestamp
    ci.checked_at = checked_at
    db.commit()
    db.refresh(ci)
    return ci


# ── GET /liff/ page ───────────────────────────────────────────────────────────

def test_liff_page_returns_200(client):
    """LIFF page is served with HTTP 200."""
    resp = client.get("/liff/")
    assert resp.status_code == 200


def test_liff_page_is_html(client):
    """Response Content-Type is text/html."""
    resp = client.get("/liff/")
    assert "text/html" in resp.headers["content-type"]


def test_liff_page_contains_liff_id(client):
    """The stub LIFF ID from conftest is injected into the page."""
    resp = client.get("/liff/")
    assert "test-liff-id" in resp.text


def test_liff_page_contains_api_url(client):
    """The APP_BASE_URL is injected so the JS fetch target is correct."""
    resp = client.get("/liff/")
    assert "http://localhost:8000" in resp.text


def test_liff_page_contains_checkin_buttons(client):
    """Both clock-in and clock-out buttons are present."""
    resp = client.get("/liff/")
    assert "上班打卡" in resp.text
    assert "下班打卡" in resp.text


def test_liff_page_loads_liff_sdk(client):
    """LIFF SDK script tag is present."""
    resp = client.get("/liff/")
    assert "line-scdn.net/liff" in resp.text


# ── POST /liff/* — liff_enabled guard (shared dependency) ────────────────────

def _disabled_settings():
    from app.config import Settings
    s = MagicMock(spec=Settings)
    s.liff_enabled = False
    return s


def test_checkin_503_when_liff_not_configured(client):
    """POST /liff/checkin returns 503 when LIFF credentials are not set."""
    with patch("app.routers.liff.get_settings", return_value=_disabled_settings()):
        resp = client.post(
            "/liff/checkin",
            json={"type": "clock_in", "latitude": 25.0, "longitude": 121.0, "id_token": "tok"},
        )
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"]


def test_status_503_when_liff_not_configured(client):
    """POST /liff/status returns 503 when LIFF credentials are not set."""
    with patch("app.routers.liff.get_settings", return_value=_disabled_settings()):
        resp = client.post("/liff/status", json={"id_token": "tok"})
    assert resp.status_code == 503


def test_records_503_when_liff_not_configured(client):
    """POST /liff/records returns 503 when LIFF credentials are not set."""
    with patch("app.routers.liff.get_settings", return_value=_disabled_settings()):
        resp = client.post("/liff/records", json={"id_token": "tok"})
    assert resp.status_code == 503


# ── CheckInRequest field bounds ───────────────────────────────────────────────

def test_valid_clock_in():
    req = CheckInRequest(type="clock_in", latitude=25.033, longitude=121.565, id_token="tok")
    assert req.type == "clock_in"


def test_valid_clock_out_extreme_coords():
    req = CheckInRequest(type="clock_out", latitude=-90.0, longitude=180.0, id_token="tok")
    assert req.type == "clock_out"


def test_latitude_above_max_rejected():
    with pytest.raises(ValidationError):
        CheckInRequest(type="clock_in", latitude=90.001, longitude=0.0, id_token="tok")


def test_latitude_below_min_rejected():
    with pytest.raises(ValidationError):
        CheckInRequest(type="clock_in", latitude=-90.001, longitude=0.0, id_token="tok")


def test_longitude_above_max_rejected():
    with pytest.raises(ValidationError):
        CheckInRequest(type="clock_in", latitude=0.0, longitude=180.001, id_token="tok")


def test_longitude_below_min_rejected():
    with pytest.raises(ValidationError):
        CheckInRequest(type="clock_in", latitude=0.0, longitude=-180.001, id_token="tok")


# ── POST /liff/status ─────────────────────────────────────────────────────────

def _mock_settings_liff(tz: str = "Asia/Taipei") -> MagicMock:
    s = MagicMock()
    s.timezone = tz
    s.liff_channel_id = "test-liff-channel-id"
    s.liff_enabled = True
    s.ftp_host = ""   # FTP disabled by default — prevents supplemental upload in most tests
    s.ftp_user = ""
    return s


def test_status_returns_display_name(client, db):
    """Status returns display_name when employee is bound."""
    emp = _add_employee(db, display_name="Alice")
    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/status", json={"id_token": "tok"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Alice"
    assert body["clock_in_time"] is None
    assert body["clock_out_time"] is None


def test_status_shows_todays_clock_in(client, db):
    """Status returns today's clock-in time when a record exists."""
    emp = _add_employee(db)
    now_utc = datetime.now(timezone.utc).replace(hour=1, minute=0, second=0, microsecond=0)
    _add_checkin(db, emp.id, CheckInType.clock_in, now_utc)
    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/status", json={"id_token": "tok"})

    assert resp.status_code == 200
    assert resp.json()["clock_in_time"] is not None


def test_status_403_for_unbound_user(db):
    """_get_employee raises 403 when LINE user has no active employee record."""
    from fastapi import HTTPException
    from app.routers.liff import _get_employee

    with pytest.raises(HTTPException) as exc:
        _get_employee(db, "nonexistent_uid")
    assert exc.value.status_code == 403


# ── POST /liff/records ────────────────────────────────────────────────────────

def test_records_returns_month_label(client, db):
    """Records endpoint returns a month label."""
    _add_employee(db)
    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/records", json={"id_token": "tok"})

    assert resp.status_code == 200
    body = resp.json()
    assert "月" in body["month"]
    assert isinstance(body["records"], list)


def test_records_includes_checkin_entries(client, db):
    """Records returns daily summaries with overtime fields."""
    emp = _add_employee(db)
    now_utc = datetime.now(timezone.utc)
    _add_checkin(db, emp.id, CheckInType.clock_in, now_utc)
    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/records", json={"id_token": "tok"})

    assert resp.status_code == 200
    body = resp.json()
    assert "total_ot_counted_minutes" in body
    assert "exceeds_monthly_limit" in body
    records = body["records"]
    assert len(records) == 1
    r = records[0]
    assert "date" in r
    assert "weekday" in r
    assert "clock_in" in r
    assert "clock_out" in r
    assert "ot_counted_minutes" in r
    assert r["in_progress"] is True   # only clock_in, no clock_out


def test_records_403_for_unbound_user(db):
    """_get_employee raises 403 when LINE user has no active employee record (shared with status test)."""
    from fastapi import HTTPException
    from app.routers.liff import _get_employee

    with pytest.raises(HTTPException) as exc:
        _get_employee(db, "ghost_uid")
    assert exc.value.status_code == 403


# ── POST /liff/status — is_manager field ──────────────────────────────────────

def test_status_returns_is_manager_false_for_regular_employee(client, db):
    """Status returns is_manager=False for a non-manager employee."""
    _add_employee(db)
    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/status", json={"id_token": "tok"})

    assert resp.status_code == 200
    assert resp.json()["is_manager"] is False
    assert resp.json()["pending_makeup_count"] == 0


def test_status_returns_is_manager_true_and_pending_count(client, db):
    """Status returns is_manager=True and correct pending_makeup_count for managers."""
    emp = _add_employee(db)
    emp.is_manager = True
    db.commit()

    # Add a pending makeup request
    req = MakeupRequest(
        employee_id=emp.id,
        type=CheckInType.clock_in,
        requested_at=datetime.now(timezone.utc) - timedelta(hours=3),
        reason="忘記打卡",
        status=MakeupRequestStatus.pending,
    )
    db.add(req)
    db.commit()
    db.refresh(req)  # anchors session connection before endpoint call

    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/status", json={"id_token": "tok"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_manager"] is True
    assert body["pending_makeup_count"] == 1


# ── POST /liff/makeup/request ─────────────────────────────────────────────────

def _makeup_payload(db, employee_id, local_day=date(2026, 8, 26), tz="Asia/Taipei"):
    assessment = assess_makeup_day(db, employee_id, local_day, ZoneInfo(tz))
    return {
        "id_token": "tok",
        "type": "clock_in",
        "requested_local_date": local_day.isoformat(),
        "requested_local_time": "09:00",
        "reason": "忘記打卡",
        "observed_day_state": assessment.state.value,
        "observed_snapshot_token": assessment.snapshot_token,
        "exception_confirmed": False,
    }


def _post_makeup(client, payload, endpoint="request", tz="Asia/Taipei"):
    with patch("app.routers.liff.get_settings", return_value=_mock_settings_liff(tz)), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        return client.post(f"/liff/makeup/{endpoint}", json=payload)


def test_makeup_request_success(client, db):
    """Employee can submit a makeup punch request for a past time."""
    emp = _add_employee(db)
    settings = _mock_settings_liff()
    payload = _makeup_payload(db, emp.id)

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/makeup/request", json=payload)

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert db.query(MakeupRequest).count() == 1


def test_makeup_request_rejects_future_time(client, db):
    """Makeup request for a future time is rejected with 400."""
    emp = _add_employee(db)
    settings = _mock_settings_liff()
    payload = _makeup_payload(db, emp.id, date(2099, 1, 1))

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/makeup/request", json=payload)

    assert resp.status_code == 400


@pytest.mark.parametrize("field,value", [
    ("requested_local_date", "not-a-date"),
    ("requested_local_date", "2026-02-30"),
    ("requested_local_time", "25:00"),
    ("requested_local_time", "09:00+08:00"),
    ("requested_local_time", "09:00Z"),
    ("observed_day_state", "unknown"),
    ("observed_snapshot_token", "invalid-token"),
])
def test_makeup_request_rejects_invalid_local_fields(client, db, field, value):
    """Malformed dates, times, states and snapshot tokens fail validation."""
    emp = _add_employee(db)
    payload = _makeup_payload(db, emp.id)
    payload[field] = value
    resp = _post_makeup(client, payload)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    if value in ("09:00+08:00", "09:00Z"):
        assert "timezone offset" in detail
    else:
        assert any(error["loc"][-1] == field for error in detail)
    assert db.query(MakeupRequest).count() == 0


def test_makeup_request_rejects_invalid_type(client, db):
    """Makeup request with an unrecognised type returns 400."""
    emp = _add_employee(db)
    settings = _mock_settings_liff()
    payload = _makeup_payload(db, emp.id)
    payload["type"] = "invalid_type"

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/makeup/request", json=payload)

    assert resp.status_code == 400
    assert "Invalid type" in resp.json()["detail"]


def test_makeup_request_rejects_duplicate_pending(client, db):
    """Second makeup request for the same slot while first is still pending returns 409."""
    emp = _add_employee(db)
    settings = _mock_settings_liff()
    past_time = datetime(2026, 8, 26, 1, tzinfo=timezone.utc)
    payload = _makeup_payload(db, emp.id)

    # Insert an existing pending request for the same slot
    existing = MakeupRequest(
        employee_id=emp.id,
        type=CheckInType.clock_in,
        requested_at=past_time,
        reason="第一次申請",
        status=MakeupRequestStatus.pending,
        day_state_at_submission="no_records",
        snapshot_token_at_submission=payload["observed_snapshot_token"],
    )
    db.add(existing)
    db.commit()
    db.refresh(existing)

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/makeup/request", json=payload)

    assert resp.status_code == 409
    assert "已存在" in resp.json()["detail"]
    assert db.query(MakeupRequest).count() == 1


def test_makeup_day_status_suggests_missing_clock_out(client, db):
    emp = _add_employee(db)
    _add_checkin(db, emp.id, CheckInType.clock_in,
                 datetime(2026, 8, 26, 1, 25, tzinfo=timezone.utc))
    response = _post_makeup(client, {"id_token": "tok", "date": "2026-08-26"}, "day-status")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "missing_clock_out"
    assert body["suggested_type"] == "clock_out"
    assert body["records"] == [{"type": "clock_in", "type_label": "上班", "time": "09:25"}]
    assert body["snapshot_token"].startswith("sha256:")


def test_makeup_request_requires_confirmation_for_wrong_type(client, db):
    emp = _add_employee(db)
    _add_checkin(db, emp.id, CheckInType.clock_in,
                 datetime(2026, 8, 26, 1, 25, tzinfo=timezone.utc))
    payload = _makeup_payload(db, emp.id)
    # A forged client suggestion must never override the server assessment.
    payload["system_suggested_type"] = "clock_in"
    response = _post_makeup(client, payload)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "exception_confirmation_required"
    assert detail["message"]
    assert detail["day_status"]["suggested_type"] == "clock_out"
    assert db.query(MakeupRequest).count() == 0


def test_makeup_request_persists_confirmed_exception(client, db):
    emp = _add_employee(db)
    _add_checkin(db, emp.id, CheckInType.clock_in,
                 datetime(2026, 8, 26, 1, 25, tzinfo=timezone.utc))
    payload = _makeup_payload(db, emp.id)
    payload["exception_confirmed"] = True
    payload["system_suggested_type"] = "clock_in"
    response = _post_makeup(client, payload)
    assert response.status_code == 200
    request = db.query(MakeupRequest).one()
    assert request.day_state_at_submission == "missing_clock_out"
    assert request.snapshot_token_at_submission == payload["observed_snapshot_token"]
    assert request.system_suggested_type == CheckInType.clock_out
    assert request.exception_confirmed is True


@pytest.mark.parametrize("existing_types,state,selected_type,confirmed,expected_status,expected_exception", [
    ([CheckInType.clock_in], "missing_clock_out", "clock_out", False, 200, False),
    ([CheckInType.clock_out], "missing_clock_in", "clock_in", False, 200, False),
    ([CheckInType.clock_out], "missing_clock_in", "clock_out", False, 409, None),
    ([CheckInType.clock_in, CheckInType.clock_out], "complete", "clock_in", False, 409, None),
    ([CheckInType.clock_in, CheckInType.clock_out], "complete", "clock_in", True, 200, True),
    ([CheckInType.clock_in, CheckInType.clock_in], "ambiguous", "clock_out", False, 409, None),
    ([CheckInType.clock_in, CheckInType.clock_in], "ambiguous", "clock_out", True, 200, True),
    ([], "no_records", "clock_out", False, 200, False),
    ([], "no_records", "clock_in", True, 200, False),
])
def test_makeup_request_confirmation_policy(
    client, db, existing_types, state, selected_type, confirmed, expected_status, expected_exception,
):
    emp = _add_employee(db)
    for index, punch_type in enumerate(existing_types):
        _add_checkin(db, emp.id, punch_type,
                     datetime(2026, 8, 26, index, 30, tzinfo=timezone.utc))
    payload = _makeup_payload(db, emp.id)
    assert payload["observed_day_state"] == state
    payload.update(type=selected_type, exception_confirmed=confirmed)
    response = _post_makeup(client, payload)
    assert response.status_code == expected_status
    if expected_status == 200:
        assert db.query(MakeupRequest).one().exception_confirmed is expected_exception
    else:
        assert response.json()["detail"]["code"] == "exception_confirmation_required"
        assert db.query(MakeupRequest).count() == 0


@pytest.mark.parametrize("change", ["state", "snapshot", "same_state_records"])
def test_makeup_request_rejects_stale_day_state(client, db, change):
    emp = _add_employee(db)
    punch = _add_checkin(db, emp.id, CheckInType.clock_in,
                         datetime(2026, 8, 26, 1, 25, tzinfo=timezone.utc))
    payload = _makeup_payload(db, emp.id)
    payload.update(type="clock_out", exception_confirmed=True)
    if change == "state":
        payload["observed_day_state"] = "no_records"
    elif change == "snapshot":
        payload["observed_snapshot_token"] = f"sha256:{'0' * 64}"
    else:
        punch.checked_at = datetime(2026, 8, 26, 1, 30, tzinfo=timezone.utc)
        db.commit()
        db.refresh(punch)
    response = _post_makeup(client, payload)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "stale_day_state"
    assert detail["message"]
    assert detail["day_status"]["state"] == "missing_clock_out"
    current = assess_makeup_day(db, emp.id, date(2026, 8, 26), ZoneInfo("Asia/Taipei"))
    assert detail["day_status"]["snapshot_token"] == current.snapshot_token
    assert db.query(MakeupRequest).count() == 0


def test_makeup_request_rejects_legacy_client_with_displayable_message(client, db):
    _add_employee(db)
    response = _post_makeup(client, {
        "id_token": "tok", "type": "clock_in",
        "requested_at": "2026-08-26T09:00:00+08:00", "reason": "舊頁面",
    })
    assert response.status_code == 409
    assert response.json()["detail"] == "系統已更新，請關閉並重新開啟打卡頁面。"
    assert db.query(MakeupRequest).count() == 0


@pytest.mark.parametrize("field", [
    "requested_local_date", "requested_local_time", "observed_day_state", "observed_snapshot_token",
])
def test_makeup_request_missing_assessment_requires_refresh(client, db, field):
    emp = _add_employee(db)
    payload = _makeup_payload(db, emp.id)
    del payload[field]
    response = _post_makeup(client, payload)
    assert response.status_code == 409
    assert "重新開啟" in response.json()["detail"]
    assert db.query(MakeupRequest).count() == 0


@pytest.mark.parametrize("tz,utc_hour", [("Asia/Taipei", 1), ("Asia/Tokyo", 0)])
def test_makeup_request_interprets_local_time_in_settings_timezone(client, db, tz, utc_hour):
    emp = _add_employee(db)
    payload = _makeup_payload(db, emp.id, tz=tz)
    payload["requested_at"] = "2099-01-01T00:00:00Z"  # legacy timestamp is ignored
    response = _post_makeup(client, payload, tz=tz)
    assert response.status_code == 200
    stored = db.query(MakeupRequest).one()
    assert stored.requested_at.replace(tzinfo=timezone.utc) == datetime(
        2026, 8, 26, utc_hour, 0, tzinfo=timezone.utc
    )


def test_makeup_day_status_rejects_future_date(client, db):
    _add_employee(db)
    response = _post_makeup(client, {"id_token": "tok", "date": "2099-01-01"}, "day-status")
    assert response.status_code == 400


def test_makeup_day_status_rejects_invalid_date(client):
    response = _post_makeup(client, {"id_token": "tok", "date": "not-a-date"}, "day-status")
    assert response.status_code == 422


@pytest.mark.parametrize("active", [None, False])
def test_makeup_day_status_rejects_unbound_or_inactive_employee(client, db, active):
    if active is False:
        employee = _add_employee(db)
        employee.is_active = False
        db.commit()
        db.refresh(employee)
    else:
        db.query(Employee).all()  # anchor SQLite session before crossing threads
    response = _post_makeup(client, {"id_token": "tok", "date": "2026-08-26"}, "day-status")
    assert response.status_code == 403


def test_makeup_day_status_rejects_invalid_line_token(client, db):
    _add_employee(db)
    with patch("app.routers.liff.get_settings", return_value=_mock_settings_liff()), \
         patch("app.routers.liff.httpx.AsyncClient") as http_client:
        http_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=MagicMock(status_code=400)
        )
        response = client.post("/liff/makeup/day-status", json={
            "id_token": "invalid", "date": "2026-08-26", "user_id": LINE_UID,
        })
    assert response.status_code == 401


def test_makeup_day_status_503_when_liff_not_configured(client):
    with patch("app.routers.liff.get_settings", return_value=_disabled_settings()):
        response = client.post("/liff/makeup/day-status", json={
            "id_token": "tok", "date": "2026-08-26",
        })
    assert response.status_code == 503


# ── POST /liff/makeup/pending ─────────────────────────────────────────────────

def test_makeup_pending_requires_manager(client, db):
    """Non-manager employees cannot access the pending list."""
    _add_employee(db)
    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/makeup/pending", json={"id_token": "tok"})

    assert resp.status_code == 403


def test_makeup_pending_returns_pending_requests(client, db):
    """Manager sees all pending makeup requests."""
    emp = _add_employee(db)
    emp.is_manager = True
    db.commit()

    req = MakeupRequest(
        employee_id=emp.id,
        type=CheckInType.clock_in,
        requested_at=datetime.now(timezone.utc) - timedelta(hours=3),
        reason="測試原因",
        status=MakeupRequestStatus.pending,
    )
    db.add(req)
    db.commit()
    db.refresh(req)  # anchors session connection before endpoint call

    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/makeup/pending", json={"id_token": "tok"})

    assert resp.status_code == 200
    reqs = resp.json()["requests"]
    assert len(reqs) == 1
    assert reqs[0]["type"] == "clock_in"
    assert reqs[0]["reason"] == "測試原因"


# ── POST /liff/makeup/review ──────────────────────────────────────────────────

def test_makeup_review_approve_creates_checkin(client, db):
    """Approving a makeup request inserts a CheckIn record."""
    emp = _add_employee(db)
    emp.is_manager = True
    db.commit()

    req = MakeupRequest(
        employee_id=emp.id,
        type=CheckInType.clock_in,
        requested_at=datetime.now(timezone.utc) - timedelta(hours=3),
        reason="忘記打卡",
        status=MakeupRequestStatus.pending,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    req_id = req.id

    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/makeup/review", json={
            "id_token": "tok",
            "request_id": req_id,
            "action": "approve",
        })

    assert resp.status_code == 200
    assert resp.json()["success"] is True

    db.expire_all()
    updated = db.query(MakeupRequest).filter_by(id=req_id).first()
    assert updated.status == MakeupRequestStatus.approved
    assert updated.reviewed_by == emp.id

    checkin = db.query(CheckIn).filter_by(employee_id=emp.id).first()
    assert checkin is not None
    assert checkin.ip_address == "makeup:approved"


def test_makeup_review_reject_does_not_create_checkin(client, db):
    """Rejecting a makeup request does not insert a CheckIn record."""
    emp = _add_employee(db)
    emp.is_manager = True
    db.commit()

    req = MakeupRequest(
        employee_id=emp.id,
        type=CheckInType.clock_out,
        requested_at=datetime.now(timezone.utc) - timedelta(hours=1),
        reason="忘記打卡",
        status=MakeupRequestStatus.pending,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    req_id = req.id

    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/makeup/review", json={
            "id_token": "tok",
            "request_id": req_id,
            "action": "reject",
        })

    assert resp.status_code == 200
    db.expire_all()
    updated = db.query(MakeupRequest).filter_by(id=req_id).first()
    assert updated.status == MakeupRequestStatus.rejected
    assert db.query(CheckIn).count() == 0


def test_makeup_review_requires_manager(client, db):
    """Non-manager cannot review makeup requests."""
    _add_employee(db)
    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/makeup/review", json={
            "id_token": "tok",
            "request_id": 1,
            "action": "approve",
        })

    assert resp.status_code == 403


def test_makeup_review_concurrent_approve_returns_409(client, db):
    """Second approval of the same request returns 409 and creates only one CheckIn."""
    emp = _add_employee(db)
    emp.is_manager = True
    db.commit()

    req = MakeupRequest(
        employee_id=emp.id,
        type=CheckInType.clock_in,
        requested_at=datetime.now(timezone.utc) - timedelta(hours=3),
        reason="忘記打卡",
        status=MakeupRequestStatus.pending,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    req_id = req.id

    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp1 = client.post("/liff/makeup/review", json={
            "id_token": "tok", "request_id": req_id, "action": "approve",
        })
        assert resp1.status_code == 200

        # Re-anchor the SQLite in-memory session before the second call
        db.expire_all()
        _ = db.query(MakeupRequest).filter_by(id=req_id).first()

        # Simulate second concurrent reviewer hitting the same request.
        # Sequential test: pre-fetch finds status=approved → 404.
        # True concurrent race: atomic UPDATE returns 0 → 409.
        # Either way the request must NOT produce a second CheckIn.
        resp2 = client.post("/liff/makeup/review", json={
            "id_token": "tok", "request_id": req_id, "action": "approve",
        })
        assert resp2.status_code in (404, 409)

    # Exactly one CheckIn was created despite two approve attempts
    db.expire_all()
    assert db.query(CheckIn).filter_by(employee_id=emp.id).count() == 1


# ── POST /liff/checkin — clock-out guard ──────────────────────────────────────

def test_checkin_clock_out_without_clock_in_returns_422(client, db):
    """Clock-out is blocked with 422 when no clock-in exists for today."""
    _add_employee(db)
    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/checkin", json={
            "type": "clock_out",
            "latitude": 25.0,
            "longitude": 121.0,
            "id_token": "tok",
        })

    assert resp.status_code == 422
    assert "上班打卡" in resp.json()["detail"]


# ── POST /liff/update_card ────────────────────────────────────────────────────

def test_update_card_sets_card_number(client, db):
    """Employee can set their card number via LIFF."""
    _add_employee(db)
    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/update_card", json={"id_token": "tok", "card_number": "A1234567"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["card_number"] == "A1234567"

    emp = db.query(Employee).filter(Employee.line_user_id == LINE_UID).first()
    assert emp.card_number == "A1234567"


def test_update_card_uppercases_input(client, db):
    """Card number is stored uppercase regardless of input case."""
    _add_employee(db)
    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/update_card", json={"id_token": "tok", "card_number": "ab123456"})

    assert resp.status_code == 200
    assert resp.json()["card_number"] == "AB123456"


def test_update_card_conflict_returns_409(client, db):
    """Card number already held by another employee returns 409."""
    _add_employee(db)
    # A second employee already holds the target card number
    other = Employee(
        email="other@aiotek.com.tw",
        line_user_id="Uother",
        card_number="TAKEN123",
        is_active=True,
    )
    db.add(other)
    db.flush()  # keep session transaction open so the same connection is reused

    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/update_card", json={"id_token": "tok", "card_number": "TAKEN123"})

    assert resp.status_code == 409
    assert "已被其他員工使用" in resp.json()["detail"]


def test_update_card_self_update_allowed(client, db):
    """Employee can re-submit the same card number they already own (idempotent)."""
    emp = _add_employee(db)
    emp.card_number = "MINE1234"
    db.flush()  # keep session transaction open so the same connection is reused

    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/update_card", json={"id_token": "tok", "card_number": "MINE1234"})

    assert resp.status_code == 200
    assert resp.json()["card_number"] == "MINE1234"


def test_update_card_invalid_format_rejected(client, db):
    """Card numbers that don't match 8 alphanumeric chars are rejected by Pydantic."""
    _add_employee(db)
    settings = _mock_settings_liff()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/update_card", json={"id_token": "tok", "card_number": "SHORT"})

    assert resp.status_code == 422


def test_update_card_status_includes_card_number(client, db):
    """After setting card number, /liff/status reflects it."""
    emp = _add_employee(db)
    emp.card_number = "CARD1234"
    db.flush()  # keep session transaction open so the same connection is reused

    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID):
        resp = client.post("/liff/status", json={"id_token": "tok"})

    assert resp.status_code == 200
    assert resp.json()["card_number"] == "CARD1234"


# ── Makeup approval: supplemental FTP export ──────────────────────────────────

def _mock_settings_with_ftp(tz: str = "Asia/Taipei") -> MagicMock:
    s = _mock_settings_liff(tz)
    s.ftp_host = "61.219.81.20"
    s.ftp_user = "testuser"
    s.ftp_password = "testpass"
    s.ftp_remote_dir = "/"
    s.factory_machine_id = "0000000005"
    return s


def _add_employee_with_card(db, card: str = "AB123456") -> Employee:
    emp = Employee(
        email="ftp@aiotek.com.tw",
        line_user_id=LINE_UID,
        display_name="FTP Employee",
        card_number=card,
        is_active=True,
        is_manager=True,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def test_makeup_approve_triggers_supplemental_ftp_export(client, db):
    """Approving a makeup punch for a past date uploads a factory file for that date."""
    from zoneinfo import ZoneInfo
    emp = _add_employee_with_card(db)

    tz = ZoneInfo("Asia/Taipei")
    past_dt = datetime(2026, 6, 16, 8, 40, 0, tzinfo=tz)  # 6/16 08:40 local

    req = MakeupRequest(
        employee_id=emp.id,
        type=CheckInType.clock_in,
        requested_at=past_dt.astimezone(timezone.utc),
        reason="忘記打卡",
        status=MakeupRequestStatus.pending,
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    settings = _mock_settings_with_ftp()

    # Mock build_checkin_query so _try_supplemental_ftp_export doesn't hit the
    # in-memory SQLite after commit (which would open a fresh, table-less connection).
    mock_query = MagicMock()
    mock_query.filter.return_value.order_by.return_value.all.return_value = []

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID), \
         patch("app.routers.liff.build_checkin_query", return_value=mock_query), \
         patch("app.routers.liff.upload_factory_file") as mock_upload:
        resp = client.post("/liff/makeup/review", json={
            "id_token": "tok",
            "request_id": req.id,
            "action": "approve",
        })

    assert resp.status_code == 200
    mock_upload.assert_called_once()
    assert mock_upload.call_args[1]["filename"] == "factory_20260616.txt"


def test_makeup_approve_ftp_failure_does_not_break_approval(client, db):
    """FTP upload failure after makeup approval is logged but does not roll back the approval."""
    from zoneinfo import ZoneInfo
    emp = _add_employee_with_card(db, card="CD789012")

    tz = ZoneInfo("Asia/Taipei")
    past_dt = datetime(2026, 6, 10, 9, 0, 0, tzinfo=tz)

    req = MakeupRequest(
        employee_id=emp.id,
        type=CheckInType.clock_in,
        requested_at=past_dt.astimezone(timezone.utc),
        reason="補打卡",
        status=MakeupRequestStatus.pending,
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    mock_query = MagicMock()
    mock_query.filter.return_value.order_by.return_value.all.return_value = []

    settings = _mock_settings_with_ftp()

    with patch("app.routers.liff.get_settings", return_value=settings), \
         patch("app.routers.liff._verify_line_token", new_callable=AsyncMock, return_value=LINE_UID), \
         patch("app.routers.liff.build_checkin_query", return_value=mock_query), \
         patch("app.routers.liff.upload_factory_file", side_effect=Exception("FTP down")):
        resp = client.post("/liff/makeup/review", json={
            "id_token": "tok",
            "request_id": req.id,
            "action": "approve",
        })

    # Approval must succeed even when FTP is down
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    db.expire_all()
    checkin = db.query(CheckIn).filter_by(employee_id=emp.id, ip_address="makeup:approved").first()
    assert checkin is not None
