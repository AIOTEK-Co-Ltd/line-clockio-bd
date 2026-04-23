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


@router.get("/liff/")
async def liff_page(request: Request):
    """Serve the LIFF clock-in mini-app page."""
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "liff/checkin.html",
        {"liff_id": settings.liff_id, "app_base_url": settings.app_base_url},
    )

_LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


class CheckInRequest(BaseModel):
    type: str        # "clock_in" | "clock_out"
    latitude: float  = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    id_token: str    # LIFF ID token for identity verification


@router.post("/liff/checkin")
async def liff_checkin(
    payload: CheckInRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()

    if not settings.liff_enabled:
        raise HTTPException(status_code=503, detail="LIFF is not configured on this server.")

    # Validate check-in type
    try:
        checkin_type = CheckInType(payload.type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid type. Must be 'clock_in' or 'clock_out'.")

    # Verify LIFF ID Token with LINE
    async with httpx.AsyncClient(timeout=10.0) as client:
        verify_resp = await client.post(
            _LINE_VERIFY_URL,
            data={"id_token": payload.id_token, "client_id": settings.liff_channel_id},
        )
    if verify_resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid LIFF ID token.")

    line_user_id: str | None = verify_resp.json().get("sub")
    if not line_user_id:
        raise HTTPException(status_code=401, detail="Cannot extract LINE user ID from token.")

    # Look up active, bound employee
    employee = (
        db.query(Employee)
        .filter(
            Employee.line_user_id == line_user_id,
            Employee.is_active.is_(True),
        )
        .first()
    )
    if not employee:
        raise HTTPException(
            status_code=403,
            detail="Employee not found or not active. Please complete account binding first.",
        )

    # Duplicate check-in prevention: same type within a 2-hour rolling window
    two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    duplicate = (
        db.query(CheckIn)
        .filter(
            CheckIn.employee_id == employee.id,
            CheckIn.type == checkin_type,
            CheckIn.checked_at > two_hours_ago,
        )
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate: a {payload.type} was already recorded within the last 2 hours.",
        )

    # Extract real client IP (Cloud Run sits behind a proxy)
    forwarded = request.headers.get("X-Forwarded-For")
    ip_address = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "unknown")
    )

    # Write check-in record
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

    # Format local time for display
    tz = ZoneInfo(settings.timezone)
    local_time = check_in.checked_at.astimezone(tz)
    time_str = local_time.strftime("%H:%M")
    type_label = "上班打卡" if checkin_type == CheckInType.clock_in else "下班打卡"

    return {
        "success": True,
        "type": payload.type,
        "message": f"{type_label}成功：{time_str}",
        "time": time_str,
    }
