import hashlib
import hmac
import json
import re
import secrets
from base64 import b64encode
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.check_in import CheckIn, CheckInType
from app.models.email_verification import EmailVerification
from app.models.employee import Employee
from app.services.mailgun import send_otp_email

router = APIRouter(tags=["webhook"])

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}$")
_OTP_RE = re.compile(r"^\d{6}$")


def _verify_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    """Verify LINE webhook HMAC-SHA256 signature."""
    expected = b64encode(
        hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, signature)


@router.post("/webhook")
async def webhook(
    request: Request,
    x_line_signature: str = Header(...),
    db: Session = Depends(get_db),
):
    body = await request.body()
    settings = get_settings()

    if not _verify_signature(body, x_line_signature, settings.line_channel_secret):
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(body)

    for event in payload.get("events", []):
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if msg.get("type") != "text":
            continue

        line_user_id: str = event["source"]["userId"]
        reply_token: str = event["replyToken"]
        text: str = msg["text"].strip()

        if _EMAIL_RE.match(text):
            await _handle_email_submission(db, line_user_id, text.lower(), reply_token)
        elif _OTP_RE.match(text):
            await _handle_otp_verification(db, line_user_id, text, reply_token)
        elif text.lower().startswith("query "):
            await _handle_query(db, line_user_id, text[6:].strip(), reply_token)

    return {"status": "ok"}


# ── Binding flow ──────────────────────────────────────────────────────────────

async def _handle_email_submission(
    db: Session, line_user_id: str, email: str, reply_token: str
) -> None:
    # Already bound via this LINE account?
    if db.query(Employee).filter(Employee.line_user_id == line_user_id).first():
        await _reply_text(reply_token, "您的 LINE 帳號已完成綁定，無需重複操作。")
        return

    # Email already bound to a different LINE account?
    existing = db.query(Employee).filter(Employee.email == email).first()
    if existing and existing.line_user_id:
        await _reply_text(
            reply_token,
            f"此 Email（{email}）已被其他帳號綁定，請聯繫管理員。",
        )
        return

    # TODO (P1): add per-LINE-UID rate limiting to prevent Mailgun spam on unbound emails

    # Invalidate any pending (unused) OTPs for this LINE UID, then issue a new one
    otp = f"{secrets.randbelow(1_000_000):06d}"
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)

    db.query(EmailVerification).filter(
        EmailVerification.line_user_id == line_user_id,
        EmailVerification.used.is_(False),
    ).update({"used": True})

    db.add(EmailVerification(
        line_user_id=line_user_id,
        email=email,
        otp_code=otp,
        expires_at=expires,
    ))
    db.commit()

    sent = await send_otp_email(email, otp)
    if sent:
        await _reply_text(
            reply_token,
            f"已將驗證碼傳送至 {email}，\n請在 10 分鐘內回傳 6 位數驗證碼。",
        )
    else:
        await _reply_text(reply_token, "Email 傳送失敗，請稍後再試或聯繫管理員。")


async def _handle_otp_verification(
    db: Session, line_user_id: str, otp_code: str, reply_token: str
) -> None:
    now = datetime.now(timezone.utc)

    verification = (
        db.query(EmailVerification)
        .filter(
            EmailVerification.line_user_id == line_user_id,
            EmailVerification.otp_code == otp_code,
            EmailVerification.used.is_(False),
            EmailVerification.expires_at > now,
        )
        .first()
    )

    if not verification:
        await _reply_text(reply_token, "驗證碼無效或已過期，請重新傳送您的公司 Email。")
        return

    verification.used = True

    # HR-initiated path: employee record already exists (email pre-loaded) but unbound
    employee = db.query(Employee).filter(Employee.email == verification.email).first()
    if employee:
        # Guard against race: another LINE user may have bound this email between
        # OTP issuance and verification (overwrite vulnerability fix)
        if employee.line_user_id and employee.line_user_id != line_user_id:
            db.commit()  # persist used=True so this OTP cannot be replayed
            await _reply_text(reply_token, "此 Email 已被其他 LINE 帳號綁定，請聯繫管理員。")
            return
        employee.line_user_id = line_user_id
    else:
        # Employee-initiated path: create new record now
        employee = Employee(line_user_id=line_user_id, email=verification.email)
        db.add(employee)

    # Fetch LINE display name as fallback for audit display
    display_name = await _get_line_display_name(line_user_id)
    if display_name:
        employee.display_name = display_name

    db.commit()
    await _reply_text(
        reply_token,
        "✅ 綁定完成！您現在可以開始打卡。\n請點選選單中的「上班打卡」或「下班打卡」。",
    )


# ── Manager LINE query ────────────────────────────────────────────────────────

async def _handle_query(
    db: Session, line_user_id: str, month_str: str, reply_token: str
) -> None:
    # Only managers may query
    manager = (
        db.query(Employee)
        .filter(
            Employee.line_user_id == line_user_id,
            Employee.is_manager.is_(True),
            Employee.is_active.is_(True),
        )
        .first()
    )
    if not manager:
        await _reply_text(reply_token, "此功能僅限管理員使用。")
        return

    # Parse YYYY-MM with basic sanity bounds
    try:
        year, month = map(int, month_str.split("-"))
        if not (2000 <= year <= 2100 and 1 <= month <= 12):
            raise ValueError("out of range")
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end = datetime(year + (month // 12), (month % 12) + 1, 1, tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        await _reply_text(reply_token, "格式錯誤，請輸入：query YYYY-MM（例：query 2026-04）")
        return

    check_ins = (
        db.query(CheckIn)
        .join(Employee)
        .filter(
            CheckIn.checked_at >= start,
            CheckIn.checked_at < end,
            Employee.is_active.is_(True),
        )
        .order_by(Employee.full_name, CheckIn.checked_at)
        .all()
    )

    if not check_ins:
        await _reply_text(reply_token, f"{month_str} 無任何打卡紀錄。")
        return

    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    summary: dict[str, dict] = defaultdict(
        lambda: {"days": set(), "clock_ins": 0, "clock_outs": 0}
    )
    for ci in check_ins:
        emp = ci.employee
        name = emp.full_name or emp.display_name or emp.email
        summary[name]["days"].add(ci.checked_at.astimezone(tz).date())
        if ci.type == CheckInType.clock_in:
            summary[name]["clock_ins"] += 1
        else:
            summary[name]["clock_outs"] += 1

    lines = [f"📊 {month_str} 出勤摘要", ""]
    for name, data in sorted(summary.items()):
        lines += [
            f"👤 {name}",
            f"   出勤天數：{len(data['days'])} 天",
            f"   上班：{data['clock_ins']} 次　下班：{data['clock_outs']} 次",
            "",
        ]

    msg = "\n".join(lines).rstrip()
    if len(msg) > 4900:
        msg = msg[:4900] + "\n⋯（請至後台查看完整紀錄）"

    await _reply_text(reply_token, msg)


# ── LINE API helpers ──────────────────────────────────────────────────────────

async def _reply_text(reply_token: str, text: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(
            "https://api.line.me/v2/bot/message/reply",
            headers={"Authorization": f"Bearer {settings.line_channel_access_token}"},
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
        )


async def _get_line_display_name(line_user_id: str) -> str | None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"https://api.line.me/v2/bot/profile/{line_user_id}",
            headers={"Authorization": f"Bearer {settings.line_channel_access_token}"},
        )
    return resp.json().get("displayName") if resp.status_code == 200 else None
