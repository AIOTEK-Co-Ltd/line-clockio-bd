"""FTP upload utility and factory file builder for factory punch export."""
from __future__ import annotations

import ftplib
import io
import logging
from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.employee import Employee
from app.services.checkin_query import build_checkin_query
from app.services.time_utils import as_utc

logger = logging.getLogger(__name__)


def build_factory_lines(
    check_ins: list[CheckIn],
    machine_id: str,
    tz: ZoneInfo,
) -> list[str]:
    """Convert CheckIn rows to factory punch file lines (machine,card,date,time)."""
    lines = []
    for ci in check_ins:
        local_dt = as_utc(ci.checked_at).astimezone(tz)
        lines.append(
            f"{machine_id},"
            f"{ci.employee.card_number},"
            f"{local_dt.strftime('%Y/%m/%d')},"
            f"{local_dt.strftime('%H:%M:%S')}"
        )
    return lines


def build_factory_day_file(
    db: Session,
    local_date: date,
    tz: ZoneInfo,
    machine_id: str,
) -> tuple[str, bytes, int]:
    """Return (filename, content, record_count) for one local date's punch file.

    Only active employees with a card number are included. An empty file is a
    valid result — the factory FTP system expects one file per day regardless
    of whether anyone punched in, so callers must not add a skip guard.
    """
    date_str = local_date.strftime("%Y-%m-%d")
    check_ins = (
        build_checkin_query(db, tz, employee_id=None, date_from=date_str, date_to=date_str)
        .filter(Employee.card_number.isnot(None))
        .order_by(CheckIn.checked_at.asc())
        .all()
    )
    lines = build_factory_lines(check_ins, machine_id, tz)
    content = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    filename = f"factory_{local_date.strftime('%Y%m%d')}.txt"
    return filename, content, len(lines)


def upload_factory_file(
    host: str,
    user: str,
    password: str,
    remote_dir: str,
    filename: str,
    content: bytes,
) -> None:
    """Upload content as filename to the factory FTP server."""
    with ftplib.FTP(host, timeout=30) as ftp:
        ftp.login(user, password)
        if remote_dir and remote_dir != "/":
            # Windows FTP servers use CP950 (Traditional Chinese); sending UTF-8 causes 451 error
            ftp.sock.sendall(f"CWD {remote_dir}\r\n".encode("cp950"))
            ftp.getresp()
        ftp.storbinary(f"STOR {filename}", io.BytesIO(content))
    logger.info("Factory FTP upload complete: %s → %s:%s", filename, host, remote_dir)
