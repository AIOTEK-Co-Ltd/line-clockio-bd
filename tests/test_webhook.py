"""Tests for app/routers/webhook.py."""

import hashlib
import hmac
from base64 import b64encode
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.check_in import CheckIn, CheckInType
from app.models.email_verification import EmailVerification
from app.models.employee import Employee
from app.routers.webhook import (
    _handle_email_submission,
    _handle_otp_verification,
    _handle_query,
    _verify_signature,
)

LINE_UID = "Uabc1234567890abcdef"
EMAIL = "alice@example.com"
TOKEN = "reply-token"


def _make_sig(body: bytes, secret: str) -> str:
    return b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def _add_otp(db, line_user_id: str, email: str, code: str = "123456") -> EmailVerification:
    ev = EmailVerification(
        line_user_id=line_user_id,
        email=email,
        otp_code=code,
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        used=False,
    )
    db.add(ev)
    db.commit()
    return ev


def _mock_settings(tz: str = "Asia/Taipei") -> MagicMock:
    s = MagicMock()
    s.timezone = tz
    return s


# ── _verify_signature ─────────────────────────────────────────────────────────

def test_verify_sig_valid():
    body = b'{"events":[]}'
    secret = "channel-secret"
    assert _verify_signature(body, _make_sig(body, secret), secret) is True


def test_verify_sig_wrong_signature():
    assert _verify_signature(b'body', "wrong==", "channel-secret") is False


def test_verify_sig_tampered_body():
    secret = "channel-secret"
    sig = _make_sig(b'original-body', secret)
    assert _verify_signature(b'tampered-body', sig, secret) is False


# ── _handle_email_submission ──────────────────────────────────────────────────

async def test_email_already_bound(db):
    """LINE UID already bound to an employee → no OTP issued."""
    db.add(Employee(email=EMAIL, line_user_id=LINE_UID))
    db.commit()

    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply, \
         patch("app.routers.webhook.send_otp_email", new_callable=AsyncMock) as mock_send:
        await _handle_email_submission(db, LINE_UID, EMAIL, TOKEN)

    mock_send.assert_not_called()
    assert "已完成綁定" in mock_reply.call_args[0][1]


async def test_email_bound_to_different_uid(db):
    """Email already bound to a different LINE UID → conflict reply, no OTP."""
    db.add(Employee(email=EMAIL, line_user_id="U-someone-else"))
    db.commit()

    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply, \
         patch("app.routers.webhook.send_otp_email", new_callable=AsyncMock) as mock_send:
        await _handle_email_submission(db, LINE_UID, EMAIL, TOKEN)

    mock_send.assert_not_called()
    assert "已被其他帳號綁定" in mock_reply.call_args[0][1]


async def test_email_submission_otp_sent(db):
    """Unbound email → OTP row written and email dispatched."""
    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply, \
         patch("app.routers.webhook.send_otp_email", new_callable=AsyncMock, return_value=True) as mock_send:
        await _handle_email_submission(db, LINE_UID, EMAIL, TOKEN)

    mock_send.assert_called_once()
    assert mock_send.call_args[0][0] == EMAIL
    assert "驗證碼傳送至" in mock_reply.call_args[0][1]


async def test_email_submission_mailgun_failure(db):
    """Mailgun request fails → failure reply sent; OTP row still persisted."""
    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply, \
         patch("app.routers.webhook.send_otp_email", new_callable=AsyncMock, return_value=False):
        await _handle_email_submission(db, LINE_UID, EMAIL, TOKEN)

    assert "傳送失敗" in mock_reply.call_args[0][1]


# ── _handle_otp_verification ──────────────────────────────────────────────────

async def test_otp_no_matching_record(db):
    """No matching (unused, unexpired) OTP → invalid reply."""
    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply, \
         patch("app.routers.webhook._get_line_display_name", new_callable=AsyncMock, return_value=None):
        await _handle_otp_verification(db, LINE_UID, "000000", TOKEN)

    assert "驗證碼無效" in mock_reply.call_args[0][1]


async def test_otp_hr_initiated_binds_employee(db):
    """HR-pre-loaded employee (no line_user_id) is bound on OTP success."""
    db.add(Employee(email=EMAIL, line_user_id=None))
    db.commit()
    _add_otp(db, LINE_UID, EMAIL)

    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply, \
         patch("app.routers.webhook._get_line_display_name", new_callable=AsyncMock, return_value="Alice"):
        await _handle_otp_verification(db, LINE_UID, "123456", TOKEN)

    assert "綁定完成" in mock_reply.call_args[0][1]
    emp = db.query(Employee).filter(Employee.email == EMAIL).first()
    assert emp.line_user_id == LINE_UID


async def test_otp_employee_initiated_creates_record(db):
    """No pre-existing employee → new Employee row created during verification."""
    _add_otp(db, LINE_UID, EMAIL)

    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply, \
         patch("app.routers.webhook._get_line_display_name", new_callable=AsyncMock, return_value=None):
        await _handle_otp_verification(db, LINE_UID, "123456", TOKEN)

    assert "綁定完成" in mock_reply.call_args[0][1]
    emp = db.query(Employee).filter(Employee.line_user_id == LINE_UID).first()
    assert emp is not None
    assert emp.email == EMAIL


async def test_otp_race_condition_blocks_second_uid(db):
    """Race: another UID bound the email between OTP issue and verify.

    The losing UID must be rejected AND the OTP must be marked used
    to prevent replay attacks.
    """
    other_uid = "U-raced-ahead"
    db.add(Employee(email=EMAIL, line_user_id=other_uid))
    db.commit()
    _add_otp(db, LINE_UID, EMAIL)

    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply, \
         patch("app.routers.webhook._get_line_display_name", new_callable=AsyncMock, return_value=None):
        await _handle_otp_verification(db, LINE_UID, "123456", TOKEN)

    assert "已被其他 LINE 帳號綁定" in mock_reply.call_args[0][1]
    ev = db.query(EmailVerification).filter(EmailVerification.email == EMAIL).first()
    assert ev.used is True  # OTP must be invalidated to prevent replay


async def test_otp_same_uid_idempotent(db):
    """Submitting an OTP for an email already bound to the same UID succeeds."""
    db.add(Employee(email=EMAIL, line_user_id=LINE_UID))
    db.commit()
    _add_otp(db, LINE_UID, EMAIL)

    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply, \
         patch("app.routers.webhook._get_line_display_name", new_callable=AsyncMock, return_value=None):
        await _handle_otp_verification(db, LINE_UID, "123456", TOKEN)

    assert "綁定完成" in mock_reply.call_args[0][1]


# ── _handle_query ─────────────────────────────────────────────────────────────

async def test_query_non_manager_rejected(db):
    """Non-manager employee → access denied."""
    db.add(Employee(email=EMAIL, line_user_id=LINE_UID, is_manager=False, is_active=True))
    db.commit()

    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply:
        await _handle_query(db, LINE_UID, "2026-04", TOKEN)

    assert "僅限管理員" in mock_reply.call_args[0][1]


async def test_query_invalid_month_format(db):
    """Malformed month string → format error reply."""
    db.add(Employee(email=EMAIL, line_user_id=LINE_UID, is_manager=True, is_active=True))
    db.commit()

    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply:
        await _handle_query(db, LINE_UID, "not-a-date", TOKEN)

    assert "格式錯誤" in mock_reply.call_args[0][1]


async def test_query_year_out_of_range(db):
    """Year < 2000 → out-of-range guard triggers format error reply."""
    db.add(Employee(email=EMAIL, line_user_id=LINE_UID, is_manager=True, is_active=True))
    db.commit()

    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply:
        await _handle_query(db, LINE_UID, "1999-12", TOKEN)

    assert "格式錯誤" in mock_reply.call_args[0][1]


async def test_query_no_check_ins(db):
    """Valid manager, valid period, no records → no-records reply."""
    db.add(Employee(email=EMAIL, line_user_id=LINE_UID, is_manager=True, is_active=True))
    db.commit()

    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply:
        await _handle_query(db, LINE_UID, "2020-01", TOKEN)

    assert "無任何打卡紀錄" in mock_reply.call_args[0][1]


async def test_query_summary_with_check_ins(db):
    """Manager queries a month with real check-in data → formatted summary returned."""
    manager = Employee(
        email="manager@example.com", line_user_id=LINE_UID,
        is_manager=True, is_active=True, full_name="Manager Bob",
    )
    worker = Employee(
        email=EMAIL, line_user_id="U-worker",
        is_manager=False, is_active=True, full_name="Alice",
    )
    db.add_all([manager, worker])
    db.commit()
    db.refresh(worker)

    # Use UTC times that both fall on 2026-04-01 in Asia/Taipei (UTC+8):
    #   01:00 UTC = 09:00 Asia/Taipei  (clock-in)
    #   09:00 UTC = 17:00 Asia/Taipei  (clock-out)
    db.add(CheckIn(
        employee_id=worker.id,
        type=CheckInType.clock_in,
        latitude=25.033,
        longitude=121.565,
        ip_address="127.0.0.1",
        checked_at=datetime(2026, 4, 1, 1, 0, tzinfo=timezone.utc),
    ))
    db.add(CheckIn(
        employee_id=worker.id,
        type=CheckInType.clock_out,
        latitude=25.033,
        longitude=121.565,
        ip_address="127.0.0.1",
        checked_at=datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
    ))
    db.commit()

    with patch("app.routers.webhook._reply_text", new_callable=AsyncMock) as mock_reply, \
         patch("app.routers.webhook.get_settings", return_value=_mock_settings()):
        await _handle_query(db, LINE_UID, "2026-04", TOKEN)

    reply = mock_reply.call_args[0][1]
    assert "2026-04 出勤摘要" in reply
    assert "Alice" in reply
    assert "出勤天數：1" in reply
    assert "上班：1" in reply
    assert "下班：1" in reply
