import csv
import io
import secrets
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.check_in import CheckIn, CheckInType
from app.models.employee import Employee
from app.services.mailgun import send_invitation_email

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")

_LINE_AUTH_URL = "https://access.line.me/oauth2/v2.1/authorize"
_LINE_TOKEN_URL = "https://api.line.me/oauth2/v2.1/token"
_LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


def _redirect_uri() -> str:
    return f"{get_settings().app_base_url}/dashboard/callback"


def _require_manager(request: Request) -> bool:
    return bool(request.session.get("manager_id"))


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.get("/login")
async def login(request: Request, error: str | None = None):
    if _require_manager(request):
        return RedirectResponse("/dashboard/")
    return templates.TemplateResponse(
        "dashboard/login.html",
        {"request": request, "error": error},
    )


@router.get("/login/line")
async def login_line(request: Request):
    """Redirect manager to LINE Login authorization page."""
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    params = urlencode({
        "response_type": "code",
        "client_id": get_settings().liff_channel_id,
        "redirect_uri": _redirect_uri(),
        "state": state,
        "scope": "openid profile",
    })
    return RedirectResponse(f"{_LINE_AUTH_URL}?{params}")


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    """Handle LINE Login OAuth callback, verify identity, and create session."""
    if error:
        return RedirectResponse(f"/dashboard/login?error={error}")

    stored_state = request.session.pop("oauth_state", None)
    if not state or state != stored_state:
        return RedirectResponse("/dashboard/login?error=invalid_state")

    if not code:
        return RedirectResponse("/dashboard/login?error=missing_code")

    settings = get_settings()

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            _LINE_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _redirect_uri(),
                "client_id": settings.liff_channel_id,
                "client_secret": settings.liff_channel_secret,
            },
        )
        if token_resp.status_code != 200:
            return RedirectResponse("/dashboard/login?error=token_exchange_failed")

        id_token = token_resp.json().get("id_token")
        if not id_token:
            return RedirectResponse("/dashboard/login?error=missing_id_token")

        verify_resp = await client.post(
            _LINE_VERIFY_URL,
            data={"id_token": id_token, "client_id": settings.liff_channel_id},
        )
    if verify_resp.status_code != 200:
        return RedirectResponse("/dashboard/login?error=token_verify_failed")

    line_user_id = verify_resp.json().get("sub")
    if not line_user_id:
        return RedirectResponse("/dashboard/login?error=missing_sub")

    employee = (
        db.query(Employee)
        .filter(
            Employee.line_user_id == line_user_id,
            Employee.is_manager.is_(True),
            Employee.is_active.is_(True),
        )
        .first()
    )
    if not employee:
        return RedirectResponse("/dashboard/login?error=not_a_manager")

    request.session["manager_id"] = employee.id
    request.session["manager_line_id"] = line_user_id
    return RedirectResponse("/dashboard/")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/dashboard/login")


# ── Attendance list ───────────────────────────────────────────────────────────

@router.get("/")
async def dashboard_home(
    request: Request,
    db: Session = Depends(get_db),
    employee_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    if not _require_manager(request):
        return RedirectResponse("/dashboard/login")

    settings = get_settings()
    tz = ZoneInfo(settings.timezone)

    all_employees = (
        db.query(Employee)
        .filter(Employee.is_active.is_(True))
        .order_by(Employee.full_name, Employee.display_name)
        .all()
    )

    query = db.query(CheckIn).join(Employee).filter(Employee.is_active.is_(True))
    if employee_id:
        query = query.filter(CheckIn.employee_id == employee_id)
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=tz)
            query = query.filter(CheckIn.checked_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=tz
            )
            query = query.filter(CheckIn.checked_at <= dt_to)
        except ValueError:
            pass

    check_ins = query.order_by(CheckIn.checked_at.desc()).limit(500).all()

    return templates.TemplateResponse(
        "dashboard/index.html",
        {
            "request": request,
            "employees": all_employees,
            "check_ins": check_ins,
            "tz": tz,
            "filters": {
                "employee_id": employee_id,
                "date_from": date_from or "",
                "date_to": date_to or "",
            },
        },
    )


# ── CSV export ────────────────────────────────────────────────────────────────

@router.get("/export")
async def export_csv(
    request: Request,
    db: Session = Depends(get_db),
    employee_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    if not _require_manager(request):
        return RedirectResponse("/dashboard/login")

    settings = get_settings()
    tz = ZoneInfo(settings.timezone)

    query = db.query(CheckIn).join(Employee).filter(Employee.is_active.is_(True))
    if employee_id:
        query = query.filter(CheckIn.employee_id == employee_id)
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=tz)
            query = query.filter(CheckIn.checked_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=tz
            )
            query = query.filter(CheckIn.checked_at <= dt_to)
        except ValueError:
            pass

    check_ins = query.order_by(CheckIn.checked_at.asc()).all()
    exported_at = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "員工編號", "姓名", "Email",
        "打卡類型", "打卡時間(UTC+8)",
        "GPS緯度", "GPS經度", "IP位址",
        "匯出時間",
    ])
    for ci in check_ins:
        emp = ci.employee
        writer.writerow([
            emp.employee_number or "",
            emp.full_name or emp.display_name or "",
            emp.email,
            "上班打卡" if ci.type == CheckInType.clock_in else "下班打卡",
            ci.checked_at.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S"),
            ci.latitude,
            ci.longitude,
            ci.ip_address,
            exported_at,
        ])

    output.seek(0)
    filename = f"attendance_{datetime.now(tz).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",  # utf-8-sig for Excel compatibility
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Employee binding status ───────────────────────────────────────────────────

@router.get("/employees")
async def employee_list(
    request: Request,
    db: Session = Depends(get_db),
    imported: int | None = None,
    created: int | None = None,
    updated: int | None = None,
    errors: int | None = None,
):
    if not _require_manager(request):
        return RedirectResponse("/dashboard/login")

    employees = (
        db.query(Employee)
        .order_by(
            Employee.employee_number.is_(None),  # NULLs last
            Employee.employee_number,
            Employee.email,
        )
        .all()
    )
    return templates.TemplateResponse(
        "dashboard/employees.html",
        {
            "request": request,
            "employees": employees,
            "import_result": {
                "shown": imported == 1,
                "created": created or 0,
                "updated": updated or 0,
                "errors": errors or 0,
            },
        },
    )


# ── HR batch import ───────────────────────────────────────────────────────────

@router.post("/import")
async def hr_import(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not _require_manager(request):
        return RedirectResponse("/dashboard/login")

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # handles BOM from Excel exports
    except UnicodeDecodeError:
        text = content.decode("big5", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    created = updated = errors = 0

    for row in reader:
        emp_no = (row.get("員工編號") or row.get("employee_number") or "").strip()
        full_name = (row.get("姓名") or row.get("full_name") or "").strip()
        email = (row.get("Email") or row.get("email") or "").strip().lower()

        if not email:
            errors += 1
            continue

        existing = db.query(Employee).filter(Employee.email == email).first()
        if existing:
            if emp_no:
                existing.employee_number = emp_no
            if full_name:
                existing.full_name = full_name
            updated += 1
        else:
            db.add(Employee(
                employee_number=emp_no or None,
                full_name=full_name or None,
                email=email,
            ))
            db.flush()
            await send_invitation_email(email, full_name or email)
            created += 1

    db.commit()
    return RedirectResponse(
        f"/dashboard/employees?imported=1&created={created}&updated={updated}&errors={errors}",
        status_code=303,
    )


# ── Re-send invitation ────────────────────────────────────────────────────────

@router.post("/employees/{emp_id}/invite")
async def resend_invite(
    emp_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not _require_manager(request):
        return RedirectResponse("/dashboard/login")

    employee = db.query(Employee).filter(Employee.id == emp_id).first()
    if employee and not employee.line_user_id:
        await send_invitation_email(
            employee.email,
            employee.full_name or employee.display_name or employee.email,
        )
    return RedirectResponse("/dashboard/employees", status_code=303)
