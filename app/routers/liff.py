from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.check_in import CheckIn, CheckInType
from app.models.employee import Employee

router = APIRouter(tags=["liff"])
templates = Jinja2Templates(directory="app/templates")

_LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


# ── Shared helpers ─────────────────────────────────────────────────────────────

async def _verify_line_token(id_token: str, client_id: str) -> str:
    """Verify a LIFF ID token and return the LINE user ID (sub claim)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            _LINE_VERIFY_URL,
            data={"id_token": id_token, "client_id": client_id},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid LIFF ID token.")
    line_user_id = resp.json().get("sub")
    if not line_user_id:
        raise HTTPException(status_code=401, detail="Cannot extract LINE user ID from token.")
    return line_user_id


def _get_employee(db: Session, line_user_id: str) -> Employee:
    employee = (
        db.query(Employee)
        .filter(Employee.line_user_id == line_user_id, Employee.is_active.is_(True))
        .first()
    )
    if not employee:
        raise HTTPException(
            status_code=403,
            detail="Employee not found or not active. Please complete account binding first.",
        )
    return employee


def _today_start_utc(tz: ZoneInfo) -> datetime:
    return (
        datetime.now(tz)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )


# ── Pydantic models ────────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    id_token: str


class CheckInRequest(BaseModel):
    type: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    id_token: str


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/liff/")
async def liff_page(request: Request):
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "liff/checkin.html",
        {"liff_id": settings.liff_id, "app_base_url": settings.app_base_url},
    )


@router.post("/liff/status")
async def liff_status(payload: TokenRequest, db: Session = Depends(get_db)):
    """Return today's clock-in / clock-out times for the authenticated employee."""
    settings = get_settings()
    line_user_id = await _verify_line_token(payload.id_token, settings.liff_channel_id)
    employee = _get_employee(db, line_user_id)

    tz = ZoneInfo(settings.timezone)
    today_start = _today_start_utc(tz)

    records = (
        db.query(CheckIn)
        .filter(
            CheckIn.employee_id == employee.id,
            CheckIn.checked_at >= today_start,
        )
        .order_by(CheckIn.checked_at)
        .all()
    )

    clock_in_time = clock_out_time = None
    for r in records:
        t = r.checked_at.astimezone(tz).strftime("%H:%M")
        if r.type == CheckInType.clock_in and clock_in_time is None:
            clock_in_time = t
        elif r.type == CheckInType.clock_out:
            clock_out_time = t

    return {
        "clock_in_time": clock_in_time,
        "clock_out_time": clock_out_time,
        "display_name": employee.display_name or employee.full_name or employee.email,
    }


@router.post("/liff/records")
async def liff_records(payload: TokenRequest, db: Session = Depends(get_db)):
    """Return this month's attendance records for the authenticated employee."""
    settings = get_settings()
    line_user_id = await _verify_line_token(payload.id_token, settings.liff_channel_id)
    employee = _get_employee(db, line_user_id)

    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    records = (
        db.query(CheckIn)
        .filter(
            CheckIn.employee_id == employee.id,
            CheckIn.checked_at >= month_start,
        )
        .order_by(CheckIn.checked_at.desc())
        .limit(200)
        .all()
    )

    return {
        "month": now.strftime("%Y年%m月"),
        "records": [
            {
                "type": r.type.value,
                "type_label": "上班" if r.type == CheckInType.clock_in else "下班",
                "time": r.checked_at.astimezone(tz).strftime("%m/%d %H:%M"),
                "date": r.checked_at.astimezone(tz).strftime("%m/%d"),
                "weekday": ["一","二","三","四","五","六","日"][r.checked_at.astimezone(tz).weekday()],
            }
            for r in records
        ],
    }


@router.post("/liff/checkin")
async def liff_checkin(
    payload: CheckInRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()

    if not settings.liff_enabled:
        raise HTTPException(status_code=503, detail="LIFF is not configured on this server.")

    try:
        checkin_type = CheckInType(payload.type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid type. Must be 'clock_in' or 'clock_out'.")

    line_user_id = await _verify_line_token(payload.id_token, settings.liff_channel_id)
    employee = _get_employee(db, line_user_id)

    tz = ZoneInfo(settings.timezone)

    # Clock-out requires a clock-in on the same calendar day
    if checkin_type == CheckInType.clock_out:
        today_start = _today_start_utc(tz)
        if not db.query(CheckIn).filter(
            CheckIn.employee_id == employee.id,
            CheckIn.type == CheckInType.clock_in,
            CheckIn.checked_at >= today_start,
        ).first():
            raise HTTPException(status_code=422, detail="今日尚未上班打卡，請先完成上班打卡。")

    # Duplicate check-in prevention: same type within 2-hour window
    two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    if db.query(CheckIn).filter(
        CheckIn.employee_id == employee.id,
        CheckIn.type == checkin_type,
        CheckIn.checked_at > two_hours_ago,
    ).first():
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate: a {payload.type} was already recorded within the last 2 hours.",
        )

    forwarded = request.headers.get("X-Forwarded-For")
    ip_address = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "unknown")
    )

    check_in = CheckIn(
        employee_id=employee.id,
        type=checkin_type,
        latitude=payload.latitude,
        longitude=payload.longitude,
        ip_address=ip_address,
    )
    db.add(check_in)
    db.commit()
    db.refresh(check_in)

    local_time = check_in.checked_at.astimezone(tz)
    time_str = local_time.strftime("%H:%M")
    type_label = "上班打卡" if checkin_type == CheckInType.clock_in else "下班打卡"

    return {
        "success": True,
        "type": payload.type,
        "message": f"{type_label}成功：{time_str}",
        "time": time_str,
    }
