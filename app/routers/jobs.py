"""Internal job endpoints — triggered by Cloud Scheduler, not exposed to end users."""
from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.ftp_export import build_factory_day_file, upload_factory_file

router = APIRouter(prefix="/internal", tags=["internal"])
logger = logging.getLogger(__name__)


def _require_internal(x_internal_secret: str = Header(default="")):
    """Validate the secret token sent by Cloud Scheduler."""
    secret = get_settings().internal_secret
    if not secret:
        raise HTTPException(status_code=500, detail="Internal secret not configured.")
    if not hmac.compare_digest(x_internal_secret, secret):
        raise HTTPException(status_code=401, detail="Unauthorized.")


@router.post("/jobs/factory_export")
async def job_factory_export(
    db: Session = Depends(get_db),
    _: None = Depends(_require_internal),
):
    """
    Generate and FTP-upload the factory punch file for the previous calendar day.

    Cloud Scheduler should call this daily (e.g. 23:30 Asia/Taipei).
    Override the date window by passing ?date=YYYY-MM-DD as a query param for
    manual backfills (not yet wired — add if needed).
    """
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    now_local = datetime.now(tz)

    # Export yesterday's punches so all clock-outs are captured
    yesterday = (now_local - timedelta(days=1)).date()
    date_str = yesterday.strftime("%Y-%m-%d")

    # Empty files are intentional — factory FTP system expects one file per day
    # regardless of whether anyone punched in. Do not add a skip guard.
    filename, content, record_count = build_factory_day_file(
        db, yesterday, tz, settings.factory_machine_id
    )

    logger.info("Factory export job: %d records for %s", record_count, date_str)

    try:
        upload_factory_file(
            host=settings.ftp_host,
            user=settings.ftp_user,
            password=settings.ftp_password,
            remote_dir=settings.ftp_remote_dir,
            filename=filename,
            content=content,
        )
    except Exception:
        logger.exception("Factory FTP upload failed for %s", date_str)
        raise HTTPException(status_code=502, detail="FTP upload failed.")

    return {"status": "ok", "filename": filename, "records": record_count}
