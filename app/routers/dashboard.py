import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.employee import Employee

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_LINE_AUTH_URL = "https://access.line.me/oauth2/v2.1/authorize"
_LINE_TOKEN_URL = "https://api.line.me/oauth2/v2.1/token"
_LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


def _redirect_uri() -> str:
    return f"{get_settings().app_base_url}/dashboard/callback"


@router.get("/login")
async def login(request: Request):
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

    # Prevent CSRF: verify state matches what we set in /login
    stored_state = request.session.pop("oauth_state", None)
    if not state or state != stored_state:
        return RedirectResponse("/dashboard/login?error=invalid_state")

    if not code:
        return RedirectResponse("/dashboard/login?error=missing_code")

    settings = get_settings()

    # Exchange authorization code for tokens, then verify ID token (single connection)
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

    # Look up employee and confirm manager permission
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


@router.get("/")
async def dashboard_home(request: Request):
    """Dashboard home — protected, requires manager session."""
    if not request.session.get("manager_id"):
        return RedirectResponse("/dashboard/login")
    # Placeholder: full Jinja2 template to be implemented in Sprint
    return HTMLResponse(
        f"<h1>Dashboard</h1>"
        f"<p>Logged in (manager_id={request.session['manager_id']})</p>"
        f"<a href='/dashboard/logout'>Logout</a>"
    )
