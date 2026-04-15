import hashlib
import hmac
import json
import re
import secrets
from base64 import b64encode
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
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
            await _reply_text(reply_token, "查詢功能開發中，請使用網頁後台查詢出勤紀錄。")

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


# ── LINE API helpers ──────────────────────────────────────────────────────────

async def _reply_text(reply_token: str, text: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://api.line.me/v2/bot/message/reply",
            headers={"Authorization": f"Bearer {settings.line_channel_access_token}"},
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
        )


async def _get_line_display_name(line_user_id: str) -> str | None:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.line.me/v2/bot/profile/{line_user_id}",
            headers={"Authorization": f"Bearer {settings.line_channel_access_token}"},
        )
    return resp.json().get("displayName") if resp.status_code == 200 else None
