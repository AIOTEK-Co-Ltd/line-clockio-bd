import logging
from datetime import date, datetime, time, timezone
from typing import NoReturn
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.services.checkin_query import build_checkin_query
from app.services.ftp_export import build_factory_lines, upload_factory_file
from app.services.makeup_validation import (
    MakeupDayAssessment,
    MakeupDayState,
    assess_makeup_day,
    requires_exception_confirmation,
)
from app.services.overtime import (
    MONTHLY_OT_LIMIT,
    compute_monthly_summaries,
    monthly_ot_total,
)

from app.config import get_settings
from app.database import get_db
from app.models.check_in import CheckIn, CheckInType
from app.models.employee import CARD_NUMBER_RE, Employee
from app.models.makeup_request import MakeupRequest, MakeupRequestStatus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["liff"])
templates = Jinja2Templates(directory="app/templates")

_LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


# ── Dependencies ──────────────────────────────────────────────────────────────

def _require_liff() -> None:
    """FastAPI dependency: raise 503 when LIFF credentials are not configured."""
    if not get_settings().liff_enabled:
        raise HTTPException(status_code=503, detail="LIFF is not configured on this server.")


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


def _get_manager(db: Session, line_user_id: str) -> Employee:
    employee = _get_employee(db, line_user_id)
    if not employee.is_manager:
        raise HTTPException(status_code=403, detail="Manager access required.")
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


class MakeupDayStatusRequest(BaseModel):
    id_token: str
    date: date


class MakeupRequestCreate(BaseModel):
    id_token: str
    type: str
    reason: str = Field(..., min_length=1, max_length=500)
    requested_local_date: date | None = None
    requested_local_time: time | None = None
    requested_at: datetime | None = None  # Legacy client detection only.
    observed_day_state: MakeupDayState | None = None
    observed_snapshot_token: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$",
    )
    exception_confirmed: bool = False


class MakeupReviewPayload(BaseModel):
    id_token: str
    request_id: int
    action: str  # "approve" or "reject"
    observed_day_state: MakeupDayState | None = None
    observed_snapshot_token: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$",
    )
    exception_confirmed: bool = False


class UpdateCardRequest(BaseModel):
    id_token: str
    card_number: str = Field(..., min_length=8, max_length=8, pattern=CARD_NUMBER_RE.pattern)


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/liff/")
async def liff_page(request: Request) -> HTMLResponse:
    """Serve LIFF with the configured application timezone for makeup dates."""
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "liff/checkin.html",
        {
            "liff_id": settings.liff_id,
            "app_base_url": settings.app_base_url,
            "timezone": settings.timezone,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/liff/status")
async def liff_status(
    payload: TokenRequest,
    db: Session = Depends(get_db),
    _: None = Depends(_require_liff),
):
    """Return today's clock-in / clock-out times, display name, and manager flag."""
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

    pending_count = 0
    if employee.is_manager:
        pending_count = (
            db.query(MakeupRequest)
            .filter(MakeupRequest.status == MakeupRequestStatus.pending)
            .count()
        )

    return {
        "clock_in_time": clock_in_time,
        "clock_out_time": clock_out_time,
        "display_name": employee.display_name or employee.full_name or employee.email,
        "is_manager": employee.is_manager,
        "pending_makeup_count": pending_count,
        "card_number": employee.card_number,
    }


@router.post("/liff/records")
async def liff_records(
    payload: TokenRequest,
    db: Session = Depends(get_db),
    _: None = Depends(_require_liff),
):
    """Return this month's daily attendance summaries with overtime calculation."""
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
        .order_by(CheckIn.checked_at)
        .limit(1000)
        .all()
    )

    weekday_labels = ["一", "二", "三", "四", "五", "六", "日"]
    summaries = compute_monthly_summaries(records, tz)
    total_ot = monthly_ot_total(summaries)

    def _fmt(dt: datetime | None) -> str | None:
        return dt.astimezone(tz).strftime("%H:%M") if dt else None

    return {
        "month": now.strftime("%Y年%m月"),
        "total_ot_counted_minutes": total_ot,
        "exceeds_monthly_limit": total_ot > MONTHLY_OT_LIMIT,
        "records": [
            {
                "date": s.date.strftime("%m/%d"),
                "weekday": weekday_labels[s.date.weekday()],
                "clock_in": _fmt(s.clock_in),
                "clock_out": _fmt(s.clock_out),
                "work_minutes": s.work_minutes,
                "regular_minutes": s.regular_minutes,
                "ot_tier1_minutes": s.ot_tier1_minutes,
                "ot_tier2_minutes": s.ot_tier2_minutes,
                "ot_counted_minutes": s.ot_counted_minutes,
                "ot_remainder_minutes": s.ot_remainder_minutes,
                "in_progress": s.in_progress,
                "exceeds_legal_limit": s.exceeds_legal_limit,
            }
            for s in summaries
        ],
    }


@router.post("/liff/checkin")
async def liff_checkin(
    payload: CheckInRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_require_liff),
):
    settings = get_settings()

    try:
        checkin_type = CheckInType(payload.type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid type. Must be 'clock_in' or 'clock_out'.")

    line_user_id = await _verify_line_token(payload.id_token, settings.liff_channel_id)
    employee = _get_employee(db, line_user_id)

    tz = ZoneInfo(settings.timezone)

    today_start = _today_start_utc(tz)

    # Clock-out requires a clock-in on the same calendar day
    if checkin_type == CheckInType.clock_out:
        if not db.query(CheckIn).filter(
            CheckIn.employee_id == employee.id,
            CheckIn.type == CheckInType.clock_in,
            CheckIn.checked_at >= today_start,
        ).first():
            raise HTTPException(status_code=422, detail="今日尚未上班打卡，請先完成上班打卡。")

    # Prevent the same punch type from being recorded twice on the same calendar day
    if db.query(CheckIn).filter(
        CheckIn.employee_id == employee.id,
        CheckIn.type == checkin_type,
        CheckIn.checked_at >= today_start,
    ).first():
        label = "上班" if checkin_type == CheckInType.clock_in else "下班"
        raise HTTPException(status_code=409, detail=f"今日已完成{label}打卡，無法重複打卡。")

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


# ── Makeup punch endpoints ─────────────────────────────────────────────────────

def _makeup_time_utc(local_date: date, local_time: time, tz: ZoneInfo) -> datetime:
    """Convert a unique local time to UTC, rejecting DST gaps and folds."""
    requested_local = datetime.combine(local_date, local_time, tzinfo=tz)
    requested_utc = requested_local.astimezone(timezone.utc)
    if requested_utc.astimezone(tz) != requested_local:
        raise HTTPException(
            status_code=422,
            detail=f"補打卡時間在 {tz.key} 因時區調整而不存在，請選擇其他時間。",
        )
    if requested_local.replace(fold=1).astimezone(timezone.utc) != requested_utc:
        raise HTTPException(
            status_code=422,
            detail=f"補打卡時間在 {tz.key} 因時區調整而重複，無法確認實際時間，請選擇其他時間。",
        )
    return requested_utc


def _serialize_makeup_day(
    assessment: MakeupDayAssessment,
    tz: ZoneInfo,
) -> dict[str, object]:
    """Serialize the assessment with punch times in the configured timezone."""
    records = []
    for record in assessment.records:
        checked_at = record.checked_at
        # SQLite returns naive UTC values for timezone-aware columns.
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        records.append({
            "type": record.type.value,
            "type_label": "上班" if record.type == CheckInType.clock_in else "下班",
            "time": checked_at.astimezone(tz).strftime("%H:%M"),
        })
    return {
        "state": assessment.state.value,
        "suggested_type": assessment.suggested_type.value if assessment.suggested_type else None,
        "snapshot_token": assessment.snapshot_token,
        "records": records,
    }


def _raise_makeup_conflict(
    code: str,
    message: str,
    assessment: MakeupDayAssessment,
    tz: ZoneInfo,
) -> NoReturn:
    """Return a recoverable conflict with the current assessment."""
    raise HTTPException(status_code=409, detail={
        "code": code,
        "message": message,
        "day_status": _serialize_makeup_day(assessment, tz),
    })


def _serialize_pending_makeup_request(
    db: Session,
    request: MakeupRequest,
    tz: ZoneInfo,
) -> dict[str, object]:
    """Include submission audit and the employee's current local-day assessment."""
    requested_at = request.requested_at
    # SQLite returns naive UTC values for timezone-aware columns.
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    assessment = assess_makeup_day(
        db, request.employee_id, requested_at.astimezone(tz).date(), tz,
    )
    return {
        "id": request.id,
        "employee_name": (
            request.employee.display_name or request.employee.full_name or request.employee.email
        ),
        "type": request.type.value,
        "type_label": "上班" if request.type == CheckInType.clock_in else "下班",
        "requested_at": requested_at.astimezone(tz).strftime("%m/%d %H:%M"),
        "reason": request.reason,
        "day_state_at_submission": request.day_state_at_submission,
        "snapshot_token_at_submission": request.snapshot_token_at_submission,
        "system_suggested_type": (
            request.system_suggested_type.value if request.system_suggested_type else None
        ),
        "exception_confirmed": request.exception_confirmed,
        "current_day_status": _serialize_makeup_day(assessment, tz),
        "records_changed_since_submission": (
            None if request.snapshot_token_at_submission is None
            else request.snapshot_token_at_submission != assessment.snapshot_token
        ),
    }


@router.post("/liff/makeup/day-status")
async def liff_makeup_day_status(
    payload: MakeupDayStatusRequest,
    db: Session = Depends(get_db),
    _: None = Depends(_require_liff),
) -> dict[str, object]:
    """Return the verified employee's makeup assessment for a local date."""
    settings = get_settings()
    line_user_id = await _verify_line_token(payload.id_token, settings.liff_channel_id)
    employee = _get_employee(db, line_user_id)
    tz = ZoneInfo(settings.timezone)
    if payload.date > datetime.now(tz).date():
        raise HTTPException(status_code=400, detail="補打卡日期不能是未來日期。")
    assessment = assess_makeup_day(db, employee.id, payload.date, tz)
    return _serialize_makeup_day(assessment, tz)


@router.post("/liff/makeup/request")
async def liff_makeup_request(
    payload: MakeupRequestCreate,
    db: Session = Depends(get_db),
    _: None = Depends(_require_liff),
) -> dict[str, object]:
    """Validate the employee's observed day state and submit a makeup request."""
    settings = get_settings()
    line_user_id = await _verify_line_token(payload.id_token, settings.liff_channel_id)
    employee = _get_employee(db, line_user_id)

    try:
        makeup_type = CheckInType(payload.type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid type. Must be 'clock_in' or 'clock_out'.")

    if (
        payload.requested_local_date is None
        or payload.requested_local_time is None
        or payload.observed_day_state is None
        or payload.observed_snapshot_token is None
    ):
        raise HTTPException(
            status_code=409,
            detail="系統已更新，請關閉並重新開啟打卡頁面。",
        )
    if payload.requested_local_time.utcoffset() is not None:
        raise HTTPException(
            status_code=422,
            detail="requested_local_time must not include a timezone offset.",
        )
    tz = ZoneInfo(settings.timezone)
    local_date = payload.requested_local_date
    requested_utc = _makeup_time_utc(local_date, payload.requested_local_time, tz)
    if requested_utc >= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="補打卡時間不能是未來時間。")

    assessment = assess_makeup_day(db, employee.id, local_date, tz)
    if (
        payload.observed_day_state != assessment.state
        or payload.observed_snapshot_token != assessment.snapshot_token
    ):
        _raise_makeup_conflict(
            "stale_day_state",
            "當日打卡紀錄已更新，請重新確認補卡類型。",
            assessment,
            tz,
        )
    requires_exception = requires_exception_confirmation(
        assessment, makeup_type, requested_utc
    )
    if requires_exception and not payload.exception_confirmed:
        _raise_makeup_conflict(
            "exception_confirmation_required",
            "補卡類型與當日紀錄不一致，請確認後再送出。",
            assessment,
            tz,
        )

    # Prevent duplicate pending requests for the same punch slot
    duplicate_pending = db.query(MakeupRequest).filter(
        MakeupRequest.employee_id == employee.id,
        MakeupRequest.type == makeup_type,
        MakeupRequest.requested_at == requested_utc,
        MakeupRequest.status == MakeupRequestStatus.pending,
    ).first()
    if duplicate_pending:
        raise HTTPException(status_code=409, detail="相同時間的補打卡申請已存在，請等候審核。")

    req = MakeupRequest(
        employee_id=employee.id,
        type=makeup_type,
        requested_at=requested_utc,
        reason=payload.reason.strip(),
        status=MakeupRequestStatus.pending,
        day_state_at_submission=assessment.state.value,
        snapshot_token_at_submission=assessment.snapshot_token,
        system_suggested_type=assessment.suggested_type,
        exception_confirmed=requires_exception and payload.exception_confirmed,
    )
    db.add(req)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="相同時間的補打卡申請已存在，請等候審核。")

    return {"success": True, "message": "補打卡申請已送出，請等候管理員審核。"}


@router.post("/liff/makeup/pending")
async def liff_makeup_pending(
    payload: TokenRequest,
    db: Session = Depends(get_db),
    _: None = Depends(_require_liff),
) -> dict[str, object]:
    """Manager: list pending requests with audit and current local-day status."""
    settings = get_settings()
    line_user_id = await _verify_line_token(payload.id_token, settings.liff_channel_id)
    _get_manager(db, line_user_id)  # raises 403 if not manager

    tz = ZoneInfo(settings.timezone)
    requests = (
        db.query(MakeupRequest)
        .options(joinedload(MakeupRequest.employee))
        .filter(MakeupRequest.status == MakeupRequestStatus.pending)
        .order_by(MakeupRequest.created_at.asc())
        .all()
    )

    return {
        "requests": [
            _serialize_pending_makeup_request(db, request, tz) for request in requests
        ]
    }


@router.post("/liff/makeup/review")
async def liff_makeup_review(
    payload: MakeupReviewPayload,
    db: Session = Depends(get_db),
    _: None = Depends(_require_liff),
) -> dict[str, object]:
    """Manager: revalidate approvals or reject a pending makeup request."""
    settings = get_settings()
    line_user_id = await _verify_line_token(payload.id_token, settings.liff_channel_id)
    manager = _get_manager(db, line_user_id)

    if payload.action not in ("approve", "reject"):
        raise HTTPException(
            status_code=400, detail="Action must be 'approve' or 'reject'."
        )

    # Pre-fetch for CheckIn field values (needed before the atomic update clears pending status)
    target = (
        db.query(MakeupRequest)
        .filter(
            MakeupRequest.id == payload.request_id,
            MakeupRequest.status == MakeupRequestStatus.pending,
        )
        .first()
    )
    if not target:
        raise HTTPException(status_code=404, detail="Pending request not found.")

    if payload.action == "approve":
        if payload.observed_day_state is None or payload.observed_snapshot_token is None:
            raise HTTPException(
                status_code=409, detail="系統已更新，請關閉並重新開啟打卡頁面。",
            )
        tz = ZoneInfo(settings.timezone)
        requested_at = target.requested_at
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=timezone.utc)
        current = assess_makeup_day(
            db, target.employee_id, requested_at.astimezone(tz).date(), tz,
        )
        if (
            payload.observed_day_state != current.state
            or payload.observed_snapshot_token != current.snapshot_token
        ):
            _raise_makeup_conflict(
                "stale_day_state", "當日打卡紀錄已更新，請重新檢查後再核准。", current, tz,
            )
        needs_manager_confirmation = (
            target.day_state_at_submission is None
            or target.snapshot_token_at_submission is None
            or target.exception_confirmed
            or target.day_state_at_submission != current.state.value
            or target.snapshot_token_at_submission != current.snapshot_token
            or requires_exception_confirmation(current, target.type, requested_at)
        )
        if needs_manager_confirmation and not payload.exception_confirmed:
            _raise_makeup_conflict(
                "exception_confirmation_required",
                "此申請與目前打卡紀錄不一致，請確認後再核准。", current, tz,
            )

    new_status = (
        MakeupRequestStatus.approved if payload.action == "approve"
        else MakeupRequestStatus.rejected
    )
    # Atomic UPDATE: the WHERE status='pending' guard means only one concurrent
    # reviewer can win — the second will see updated=0 and receive 409.
    updated = (
        db.query(MakeupRequest)
        .filter(
            MakeupRequest.id == payload.request_id,
            MakeupRequest.status == MakeupRequestStatus.pending,
        )
        .update(
            {
                "status": new_status,
                "reviewed_by": manager.id,
                "reviewed_at": datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
    )
    if updated == 0:
        raise HTTPException(status_code=409, detail="此申請已被其他管理員審核。")

    # Capture before commit — commit expires session objects and re-accessing
    # target.requested_at afterwards would trigger an unwanted lazy-load.
    makeup_dt = target.requested_at if payload.action == "approve" else None

    if payload.action == "approve":
        # Insert the attendance record at the employee's requested timestamp.
        # This intentionally bypasses the normal 2-hour duplicate guard — manager
        # approval is an explicit override, so the record is written as-is.
        db.add(CheckIn(
            employee_id=target.employee_id,
            type=target.type,
            checked_at=makeup_dt,
            latitude=0.0,
            longitude=0.0,
            ip_address="makeup:approved",
        ))
        msg = "已核准補打卡申請。"
    else:
        msg = "已拒絕補打卡申請。"

    db.commit()

    if makeup_dt is not None:
        _try_supplemental_ftp_export(db, makeup_dt)

    return {"success": True, "message": msg}


def _try_supplemental_ftp_export(db: Session, punch_dt: datetime) -> None:
    """Upload a corrected factory file for the makeup punch date.

    Called after a makeup approval so past-date punches reach the factory
    punch machine under the correct date rather than the current nightly file.
    Errors are logged but never propagate — approval is already committed.
    """
    settings = get_settings()
    if not settings.ftp_host or not settings.ftp_user:
        return
    try:
        tz = ZoneInfo(settings.timezone)
        local_date = punch_dt.astimezone(tz).date()
        date_str = local_date.strftime("%Y-%m-%d")
        check_ins = (
            build_checkin_query(db, tz, employee_id=None, date_from=date_str, date_to=date_str)
            .filter(Employee.card_number.isnot(None))
            .order_by(CheckIn.checked_at.asc())
            .all()
        )
        lines = build_factory_lines(check_ins, settings.factory_machine_id, tz)
        content = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
        filename = f"factory_{local_date.strftime('%Y%m%d')}.txt"
        upload_factory_file(
            host=settings.ftp_host,
            user=settings.ftp_user,
            password=settings.ftp_password,
            remote_dir=settings.ftp_remote_dir,
            filename=filename,
            content=content,
        )
        logger.info("Supplemental FTP export: %s (%d records)", filename, len(lines))
    except Exception:
        logger.exception("Supplemental FTP export failed for %s — approval was not rolled back", punch_dt)


# ── Card number ────────────────────────────────────────────────────────────────

@router.post("/liff/update_card")
async def liff_update_card(
    payload: UpdateCardRequest,
    db: Session = Depends(get_db),
    _: None = Depends(_require_liff),
):
    """Employee saves or updates their 8-character punch card number."""
    settings = get_settings()
    line_user_id = await _verify_line_token(payload.id_token, settings.liff_channel_id)
    employee = _get_employee(db, line_user_id)

    card = payload.card_number.upper()

    conflict = db.query(Employee).filter(
        Employee.card_number == card,
        Employee.id != employee.id,
    ).first()
    if conflict:
        raise HTTPException(status_code=409, detail="此卡號已被其他員工使用，請確認後重新輸入。")

    employee.card_number = card
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="此卡號已被其他員工使用，請確認後重新輸入。")

    return {"success": True, "card_number": card}
