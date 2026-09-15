# 補打卡類型建議與例外確認 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依指定日期的資料庫打卡紀錄建議補卡類型，允許經明確確認的例外，並在主管核准前再次驗證。

**Architecture:** 新增純後端 `makeup_validation` service，集中處理 `Asia/Taipei` 日界線、狀態分類、record snapshot token 及例外判斷；LIFF router 的 day-status、request、pending、review 都使用同一介面。`MakeupRequest` 保存送出當下狀態、系統建議與員工例外確認，前端傳本地日期／時間與所見 snapshot，最終時間換算與判斷仍由後端完成。Production 先以 Cloud Run migration job 套用 additive migration，成功後才部署 service revision。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、Jinja2、原生 JavaScript、pytest、ruff、Bun、GitHub Actions、GCP Cloud Run Jobs

## Global Constraints

- 所有日期邊界使用 `Settings.timezone`，預設 `Asia/Taipei`；DB 時間維持 timezone-aware UTC／TIMESTAMPTZ。
- 員工可忽略系統建議，但必須明確勾選確認；主管端必須看見並再次確認例外。
- 後端在申請與核准時重新查 DB，並比較 record snapshot token；不信任前端提供的建議值。
- snapshot 是 optimistic stale detection；本次不處理一般打卡的 DB constraint，也不宣稱消除最後一次查詢後的 TOCTOU 視窗。
- 補卡 instant 由後端以 `requested_local_date`、`requested_local_time` 與 `Settings.timezone` 組合；不得依賴裝置 timezone。
- 新版欄位在過渡期保持 optional；舊版 LIFF payload 回傳可由舊 JavaScript 顯示的純字串 refresh 訊息，不得直接 422。
- 新增 migration revision `005`，不得修改已部署的 `001`–`004`。
- 核准仍先 commit，再 best-effort 補傳 FTP；FTP 失敗不得回滾核准。
- 不修改 FTP 格式、檔名、全日重傳方式或 Daisy 既有資料。
- Python 指令使用 `uv`；不得改用 `pip`。
- 每個任務只修改列出的範圍，保留既有使用者變更。

---

### Task 1: 建立當日打卡狀態判斷服務

**Files:**
- Create: `app/services/makeup_validation.py`
- Create: `tests/test_makeup_validation.py`

**Interfaces:**
- Consumes: `Session`、`CheckIn`、`CheckInType`、本地 `date` 與 `ZoneInfo`。
- Produces: `MakeupDayState`、`MakeupDayAssessment`、`assess_makeup_day()`、`requires_exception_confirmation()` 與 deterministic `snapshot_token`。

- [ ] **Step 1: 建立五種狀態與例外判斷的 failing tests**

```python
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.check_in import CheckIn, CheckInType
from app.models.employee import Employee
from app.services.makeup_validation import (
    MakeupDayState,
    assess_makeup_day,
    requires_exception_confirmation,
)


TZ = ZoneInfo("Asia/Taipei")
DAY = date(2026, 8, 26)


def _employee(db, email: str = "day-state@aiotek.com.tw") -> Employee:
    employee = Employee(email=email, is_active=True)
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee


def _punch(db, employee_id: int, punch_type: CheckInType, local_time: str) -> None:
    hour, minute = map(int, local_time.split(":"))
    checked_at = datetime(2026, 8, 26, hour, minute, tzinfo=TZ).astimezone(timezone.utc)
    db.add(CheckIn(
        employee_id=employee_id,
        type=punch_type,
        checked_at=checked_at,
        latitude=0.0,
        longitude=0.0,
        ip_address="test",
    ))
    db.commit()


@pytest.mark.parametrize(
    ("punches", "expected_state", "expected_suggestion"),
    [
        ([], MakeupDayState.no_records, None),
        ([(CheckInType.clock_out, "18:00")], MakeupDayState.missing_clock_in, CheckInType.clock_in),
        ([(CheckInType.clock_in, "09:00")], MakeupDayState.missing_clock_out, CheckInType.clock_out),
        ([(CheckInType.clock_in, "09:00"), (CheckInType.clock_out, "18:00")], MakeupDayState.complete, None),
        ([(CheckInType.clock_in, "09:00"), (CheckInType.clock_in, "09:25")], MakeupDayState.ambiguous, None),
    ],
)
def test_assess_makeup_day_states(db, punches, expected_state, expected_suggestion):
    employee = _employee(db)
    for punch_type, local_time in punches:
        _punch(db, employee.id, punch_type, local_time)

    result = assess_makeup_day(db, employee.id, DAY, TZ)

    assert result.state == expected_state
    assert result.suggested_type == expected_suggestion
    assert [record.checked_at for record in result.records] == sorted(
        record.checked_at for record in result.records
    )


def test_assess_makeup_day_uses_local_calendar_boundary(db):
    employee = _employee(db)
    _punch(db, employee.id, CheckInType.clock_in, "00:30")
    db.add(CheckIn(
        employee_id=employee.id,
        type=CheckInType.clock_out,
        checked_at=datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc),
        latitude=0.0,
        longitude=0.0,
        ip_address="next-day",
    ))
    db.commit()

    result = assess_makeup_day(db, employee.id, DAY, TZ)

    assert result.state == MakeupDayState.missing_clock_out
    assert len(result.records) == 1


def test_requires_exception_only_for_override_or_unsafe_state(db):
    employee = _employee(db)
    _punch(db, employee.id, CheckInType.clock_in, "09:25")
    assessment = assess_makeup_day(db, employee.id, DAY, TZ)

    assert requires_exception_confirmation(assessment, CheckInType.clock_out) is False
    assert requires_exception_confirmation(assessment, CheckInType.clock_in) is True


def test_snapshot_changes_when_ambiguous_records_change(db):
    employee = _employee(db)
    _punch(db, employee.id, CheckInType.clock_in, "09:00")
    _punch(db, employee.id, CheckInType.clock_in, "09:25")
    before = assess_makeup_day(db, employee.id, DAY, TZ)

    _punch(db, employee.id, CheckInType.clock_out, "18:00")
    after = assess_makeup_day(db, employee.id, DAY, TZ)

    assert before.state == after.state == MakeupDayState.ambiguous
    assert before.snapshot_token != after.snapshot_token


def test_empty_snapshot_is_bound_to_employee_and_date(db):
    first = _employee(db)
    second = _employee(db, "day-state-second@aiotek.com.tw")

    first_day = assess_makeup_day(db, first.id, DAY, TZ)
    next_day = assess_makeup_day(db, first.id, date(2026, 8, 27), TZ)
    other_employee = assess_makeup_day(db, second.id, DAY, TZ)

    assert len({
        first_day.snapshot_token,
        next_day.snapshot_token,
        other_employee.snapshot_token,
    }) == 3
```

- [ ] **Step 2: 執行測試確認因模組尚未存在而失敗**

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_makeup_validation.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'app.services.makeup_validation'`.

- [ ] **Step 3: 實作最小判斷服務**

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.check_in import CheckIn, CheckInType


class MakeupDayState(str, Enum):
    """Classification of an employee's punches on one local calendar date."""

    no_records = "no_records"
    missing_clock_in = "missing_clock_in"
    missing_clock_out = "missing_clock_out"
    complete = "complete"
    ambiguous = "ambiguous"


@dataclass(frozen=True)
class MakeupDayAssessment:
    """Punch records and the safe makeup suggestion derived from them."""

    state: MakeupDayState
    suggested_type: CheckInType | None
    records: tuple[CheckIn, ...]
    snapshot_token: str


def _snapshot_timestamp(record: CheckIn) -> str:
    """Return a canonical UTC timestamp, including SQLite's naive test values."""
    checked_at = record.checked_at
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return checked_at.astimezone(timezone.utc).isoformat(timespec="microseconds")


def assess_makeup_day(
    db: Session,
    employee_id: int,
    local_date: date,
    tz: ZoneInfo,
) -> MakeupDayAssessment:
    """Classify one employee's punches within a timezone-aware local date."""
    start = datetime.combine(local_date, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    records = tuple(
        db.query(CheckIn)
        .filter(
            CheckIn.employee_id == employee_id,
            CheckIn.checked_at >= start,
            CheckIn.checked_at < end,
        )
        .order_by(CheckIn.checked_at.asc(), CheckIn.id.asc())
        .all()
    )
    snapshot_source = "\n".join(
        [f"employee={employee_id}|date={local_date.isoformat()}"]
        + [
            f"{record.id}|{record.type.value}|{_snapshot_timestamp(record)}"
            for record in records
        ]
    )
    snapshot_token = f"sha256:{hashlib.sha256(snapshot_source.encode()).hexdigest()}"
    clock_in_count = sum(record.type == CheckInType.clock_in for record in records)
    clock_out_count = sum(record.type == CheckInType.clock_out for record in records)

    if clock_in_count == 0 and clock_out_count == 0:
        return MakeupDayAssessment(MakeupDayState.no_records, None, records, snapshot_token)
    if clock_in_count == 0 and clock_out_count == 1:
        return MakeupDayAssessment(MakeupDayState.missing_clock_in, CheckInType.clock_in, records, snapshot_token)
    if clock_in_count == 1 and clock_out_count == 0:
        return MakeupDayAssessment(MakeupDayState.missing_clock_out, CheckInType.clock_out, records, snapshot_token)
    if clock_in_count == 1 and clock_out_count == 1:
        return MakeupDayAssessment(MakeupDayState.complete, None, records, snapshot_token)
    return MakeupDayAssessment(MakeupDayState.ambiguous, None, records, snapshot_token)


def requires_exception_confirmation(
    assessment: MakeupDayAssessment,
    selected_type: CheckInType,
) -> bool:
    """Return whether the selected type conflicts with a safe day assessment."""
    if assessment.state in (MakeupDayState.complete, MakeupDayState.ambiguous):
        return True
    if assessment.suggested_type is None:
        return False
    return selected_type != assessment.suggested_type
```

- [ ] **Step 4: 執行 service tests**

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_makeup_validation.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/makeup_validation.py tests/test_makeup_validation.py
git commit -m "feat(makeup): add day assessment service"
```

---

### Task 2: 新增補卡 audit 欄位與 migration

**Files:**
- Create: `migrations/versions/005_makeup_request_guidance_audit.py`
- Modify: `app/models/makeup_request.py`
- Modify: `migrations/env.py`
- Modify: `tests/test_makeup_validation.py`

**Interfaces:**
- Consumes: `MakeupDayState.value` 與 `CheckInType`。
- Produces: `MakeupRequest.day_state_at_submission`、`snapshot_token_at_submission`、`system_suggested_type`、`exception_confirmed`。

- [ ] **Step 1: 新增 model persistence failing test**

```python
from app.models.makeup_request import MakeupRequest, MakeupRequestStatus


def test_makeup_request_persists_guidance_audit(db):
    employee = _employee(db)
    request = MakeupRequest(
        employee_id=employee.id,
        type=CheckInType.clock_in,
        requested_at=datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc),
        reason="確認例外",
        status=MakeupRequestStatus.pending,
        day_state_at_submission=MakeupDayState.missing_clock_out.value,
        snapshot_token_at_submission="sha256:test-snapshot",
        system_suggested_type=CheckInType.clock_out,
        exception_confirmed=True,
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    assert request.day_state_at_submission == "missing_clock_out"
    assert request.snapshot_token_at_submission == "sha256:test-snapshot"
    assert request.system_suggested_type == CheckInType.clock_out
    assert request.exception_confirmed is True
```

- [ ] **Step 2: 執行單一測試確認欄位尚不存在**

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_makeup_validation.py::test_makeup_request_persists_guidance_audit -q`

Expected: FAIL with an invalid keyword argument for `day_state_at_submission`.

- [ ] **Step 3: 新增 SQLAlchemy model 欄位**

Add `Boolean` and `String` imports, then add:

```python
    day_state_at_submission: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    snapshot_token_at_submission: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True
    )
    system_suggested_type: Mapped[Optional[CheckInType]] = mapped_column(
        Enum(CheckInType, native_enum=False), nullable=True
    )
    exception_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
```

- [ ] **Step 4: 新增 revision `005`**

```python
"""add makeup request guidance audit

Revision ID: 005
Revises: 004
"""

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "makeup_requests",
        sa.Column("day_state_at_submission", sa.String(32), nullable=True),
    )
    op.add_column(
        "makeup_requests",
        sa.Column("snapshot_token_at_submission", sa.String(80), nullable=True),
    )
    op.add_column(
        "makeup_requests",
        sa.Column("system_suggested_type", sa.String(20), nullable=True),
    )
    op.add_column(
        "makeup_requests",
        sa.Column(
            "exception_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("makeup_requests", "exception_confirmed")
    op.drop_column("makeup_requests", "system_suggested_type")
    op.drop_column("makeup_requests", "snapshot_token_at_submission")
    op.drop_column("makeup_requests", "day_state_at_submission")
```

- [ ] **Step 5: 執行 model tests 與 migration 靜態檢查**

Before running Alembic, replace its dependency on the full application `Settings` with a migration-only settings model containing only `database_url` and the same `.env` support. This allows the production migration job to receive only `DATABASE_URL`; it must not need LINE, session, Mailgun or FTP secrets.

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class MigrationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str


config.set_main_option("sqlalchemy.url", MigrationSettings().database_url)
```

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_makeup_validation.py -q`

Expected: all tests pass.

Run: `uv run --with-requirements requirements-dev.txt python -m compileall -q migrations/versions/005_makeup_request_guidance_audit.py`

Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add app/models/makeup_request.py migrations/env.py migrations/versions/005_makeup_request_guidance_audit.py tests/test_makeup_validation.py
git commit -m "feat(makeup): persist exception audit"
```

---

### Task 3: 新增 day-status API 並驗證員工申請

**Files:**
- Modify: `app/routers/liff.py`
- Modify: `tests/test_liff.py`

**Interfaces:**
- Consumes: Task 1 的 assessment API、Task 2 的 audit 欄位。
- Produces: `POST /liff/makeup/day-status`、結構化 `409`、擴充後的 `MakeupRequestCreate`。

- [ ] **Step 1: 新增 day-status 與 request validation failing tests**

Add helpers that create punches at explicit local times, then add these cases:

```python
def test_makeup_day_status_suggests_missing_clock_out(client, db):
    emp = _add_employee(db)
    _add_checkin(
        db,
        emp.id,
        CheckInType.clock_in,
        datetime(2026, 8, 26, 1, 25, tzinfo=timezone.utc),
    )
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post(
            "/liff/makeup/day-status",
            json={"id_token": "tok", "date": "2026-08-26"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "missing_clock_out"
    assert body["suggested_type"] == "clock_out"
    assert body["records"] == [
        {"type": "clock_in", "type_label": "上班", "time": "09:25"}
    ]
    assert body["snapshot_token"].startswith("sha256:")


def test_makeup_request_requires_confirmation_for_wrong_type(client, db):
    emp = _add_employee(db)
    _add_checkin(
        db,
        emp.id,
        CheckInType.clock_in,
        datetime(2026, 8, 26, 1, 25, tzinfo=timezone.utc),
    )
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post("/liff/makeup/request", json={
            "id_token": "tok",
            "type": "clock_in",
            "requested_local_date": "2026-08-26",
            "requested_local_time": "09:00",
            "reason": "忘記打卡",
            "observed_day_state": "missing_clock_out",
            "observed_snapshot_token": assess_makeup_day(
                db, emp.id, date(2026, 8, 26), ZoneInfo("Asia/Taipei")
            ).snapshot_token,
            "exception_confirmed": False,
        })

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "exception_confirmation_required"
    assert db.query(MakeupRequest).count() == 0


def test_makeup_request_persists_confirmed_exception(client, db):
    emp = _add_employee(db)
    _add_checkin(
        db,
        emp.id,
        CheckInType.clock_in,
        datetime(2026, 8, 26, 1, 25, tzinfo=timezone.utc),
    )
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post("/liff/makeup/request", json={
            "id_token": "tok",
            "type": "clock_in",
            "requested_local_date": "2026-08-26",
            "requested_local_time": "09:00",
            "reason": "確認仍需補上班",
            "observed_day_state": "missing_clock_out",
            "observed_snapshot_token": assess_makeup_day(
                db, emp.id, date(2026, 8, 26), ZoneInfo("Asia/Taipei")
            ).snapshot_token,
            "exception_confirmed": True,
        })

    assert response.status_code == 200
    request = db.query(MakeupRequest).one()
    assert request.day_state_at_submission == "missing_clock_out"
    assert request.snapshot_token_at_submission.startswith("sha256:")
    assert request.system_suggested_type == CheckInType.clock_out
    assert request.exception_confirmed is True
```

Update every existing `/liff/makeup/request` payload that reaches request creation with the new local fields and an assessment-derived token:

```python
"requested_local_date": local_day.isoformat(),
"requested_local_time": "09:00",
"observed_day_state": assessment.state.value,
"observed_snapshot_token": assessment.snapshot_token,
"exception_confirmed": False,
```

Keep one explicit legacy test that sends only `requested_at` and asserts `409` with the plain-string refresh message. Replace the old naive-datetime test with invalid local date/time tests. The invalid type and future local time tests use the new fields; the duplicate-pending test must create the existing request with `day_state_at_submission="no_records"`, `snapshot_token_at_submission=assessment.snapshot_token`, and submit the same snapshot fields.

- [ ] **Step 2: 執行新增測試確認 API 尚未實作**

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_liff.py -k 'makeup_day_status or wrong_type or confirmed_exception' -q`

Expected: tests fail with 404 or missing request fields.

- [ ] **Step 3: 新增 payload、serializer 與 conflict helper**

```python
from datetime import date, datetime, time, timezone
from typing import NoReturn

from app.services.makeup_validation import (
    MakeupDayAssessment,
    MakeupDayState,
    assess_makeup_day,
    requires_exception_confirmation,
)


class MakeupDayStatusRequest(BaseModel):
    id_token: str
    date: date


class MakeupRequestCreate(BaseModel):
    id_token: str
    type: str
    reason: str = Field(..., min_length=1, max_length=500)
    requested_local_date: date | None = None
    requested_local_time: time | None = None
    requested_at: datetime | None = None  # legacy client detection only
    observed_day_state: MakeupDayState | None = None
    observed_snapshot_token: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    exception_confirmed: bool = False


def _serialize_makeup_day(
    assessment: MakeupDayAssessment,
    tz: ZoneInfo,
) -> dict[str, object]:
    return {
        "state": assessment.state.value,
        "suggested_type": (
            assessment.suggested_type.value if assessment.suggested_type else None
        ),
        "snapshot_token": assessment.snapshot_token,
        "records": [
            {
                "type": record.type.value,
                "type_label": "上班" if record.type == CheckInType.clock_in else "下班",
                "time": record.checked_at.astimezone(tz).strftime("%H:%M"),
            }
            for record in assessment.records
        ],
    }


def _raise_makeup_conflict(
    code: str,
    message: str,
    assessment: MakeupDayAssessment,
    tz: ZoneInfo,
) -> NoReturn:
    raise HTTPException(
        status_code=409,
        detail={
            "code": code,
            "message": message,
            "day_status": _serialize_makeup_day(assessment, tz),
        },
    )
```

- [ ] **Step 4: 實作 day-status 與 request 重新驗證**

```python
@router.post("/liff/makeup/day-status")
async def liff_makeup_day_status(
    payload: MakeupDayStatusRequest,
    db: Session = Depends(get_db),
    _: None = Depends(_require_liff),
):
    settings = get_settings()
    line_user_id = await _verify_line_token(payload.id_token, settings.liff_channel_id)
    employee = _get_employee(db, line_user_id)
    tz = ZoneInfo(settings.timezone)
    if payload.date > datetime.now(tz).date():
        raise HTTPException(status_code=400, detail="補打卡日期不能是未來日期。")
    assessment = assess_makeup_day(db, employee.id, payload.date, tz)
    return _serialize_makeup_day(assessment, tz)
```

Inside `liff_makeup_request`, after validating `type`, replace the existing naive-datetime and `requested_utc` block completely with the following legacy gate and server-side local-time conversion:

```python
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
    requested_local = datetime.combine(
        local_date,
        payload.requested_local_time,
        tzinfo=tz,
    )
    requested_utc = requested_local.astimezone(timezone.utc)
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
    requires_exception = requires_exception_confirmation(assessment, makeup_type)
    if requires_exception and not payload.exception_confirmed:
        _raise_makeup_conflict(
            "exception_confirmation_required",
            "補卡類型與當日紀錄不一致，請確認後再送出。",
            assessment,
            tz,
        )
```

When constructing `MakeupRequest`, add:

```python
        day_state_at_submission=assessment.state.value,
        snapshot_token_at_submission=assessment.snapshot_token,
        system_suggested_type=assessment.suggested_type,
        exception_confirmed=requires_exception and payload.exception_confirmed,
```

- [ ] **Step 5: 補齊 complete、ambiguous、no_records、stale 與未來日期 tests**

```python
@pytest.mark.parametrize(
    (
        "existing_types",
        "state",
        "selected_type",
        "confirmed",
        "expected_status",
        "expected_exception",
    ),
    [
        ([CheckInType.clock_in], "missing_clock_out", "clock_out", False, 200, False),
        ([CheckInType.clock_in, CheckInType.clock_out], "complete", "clock_in", False, 409, None),
        ([CheckInType.clock_in, CheckInType.clock_out], "complete", "clock_in", True, 200, True),
        ([CheckInType.clock_in, CheckInType.clock_in], "ambiguous", "clock_out", False, 409, None),
        ([CheckInType.clock_in, CheckInType.clock_in], "ambiguous", "clock_out", True, 200, True),
        ([], "no_records", "clock_out", False, 200, False),
    ],
)
def test_makeup_request_confirmation_policy(
    client,
    db,
    existing_types,
    state,
    selected_type,
    confirmed,
    expected_status,
    expected_exception,
):
    emp = _add_employee(db)
    for index, punch_type in enumerate(existing_types):
        _add_checkin(
            db,
            emp.id,
            punch_type,
            datetime(2026, 8, 26, index, 30, tzinfo=timezone.utc),
        )
    assessment = assess_makeup_day(
        db, emp.id, date(2026, 8, 26), ZoneInfo("Asia/Taipei")
    )
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post("/liff/makeup/request", json={
            "id_token": "tok",
            "type": selected_type,
            "requested_local_date": "2026-08-26",
            "requested_local_time": "12:00",
            "reason": "確認補卡規則",
            "observed_day_state": state,
            "observed_snapshot_token": assessment.snapshot_token,
            "exception_confirmed": confirmed,
        })

    assert response.status_code == expected_status
    if expected_status == 200:
        assert db.query(MakeupRequest).one().exception_confirmed is expected_exception


def test_makeup_request_rejects_stale_day_state(client, db):
    emp = _add_employee(db)
    _add_checkin(
        db,
        emp.id,
        CheckInType.clock_in,
        datetime(2026, 8, 26, 1, 25, tzinfo=timezone.utc),
    )
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post("/liff/makeup/request", json={
            "id_token": "tok",
            "type": "clock_out",
            "requested_local_date": "2026-08-26",
            "requested_local_time": "18:00",
            "reason": "畫面資料已過期",
            "observed_day_state": "no_records",
            "observed_snapshot_token": f"sha256:{'0' * 64}",
            "exception_confirmed": False,
        })

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_day_state"
    assert db.query(MakeupRequest).count() == 0


def test_makeup_request_rejects_legacy_client_with_displayable_message(client, db):
    _add_employee(db)
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post("/liff/makeup/request", json={
            "id_token": "tok",
            "type": "clock_in",
            "requested_at": "2026-08-26T09:00:00+08:00",
            "reason": "舊頁面",
        })

    assert response.status_code == 409
    assert isinstance(response.json()["detail"], str)
    assert "重新開啟" in response.json()["detail"]


def test_makeup_request_interprets_local_time_in_settings_timezone(client, db):
    emp = _add_employee(db)
    assessment = assess_makeup_day(
        db, emp.id, date(2026, 8, 26), ZoneInfo("Asia/Taipei")
    )
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post("/liff/makeup/request", json={
            "id_token": "tok",
            "type": "clock_in",
            "requested_local_date": "2026-08-26",
            "requested_local_time": "09:00",
            "reason": "時區測試",
            "observed_day_state": assessment.state.value,
            "observed_snapshot_token": assessment.snapshot_token,
        })

    assert response.status_code == 200
    stored = db.query(MakeupRequest).one()
    assert stored.requested_at.replace(tzinfo=timezone.utc) == datetime(
        2026, 8, 26, 1, 0, tzinfo=timezone.utc
    )


def test_makeup_day_status_rejects_future_date(client, db):
    _add_employee(db)
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post(
            "/liff/makeup/day-status",
            json={"id_token": "tok", "date": "2099-01-01"},
        )

    assert response.status_code == 400


def test_makeup_day_status_rejects_invalid_date(client):
    response = client.post(
        "/liff/makeup/day-status",
        json={"id_token": "tok", "date": "not-a-date"},
    )

    assert response.status_code == 422


def test_makeup_day_status_rejects_unbound_user(client, db):
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value="U-unbound",
    ):
        response = client.post(
            "/liff/makeup/day-status",
            json={"id_token": "tok", "date": "2026-08-26"},
        )

    assert response.status_code == 403


def test_makeup_day_status_rejects_inactive_employee(client, db):
    employee = _add_employee(db)
    employee.is_active = False
    db.commit()
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post(
            "/liff/makeup/day-status",
            json={"id_token": "tok", "date": "2026-08-26"},
        )

    assert response.status_code == 403
```

- [ ] **Step 6: 執行 LIFF request tests**

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_liff.py -k 'makeup_request or makeup_day_status' -q`

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/routers/liff.py tests/test_liff.py
git commit -m "feat(makeup): validate employee punch requests"
```

---

### Task 4: 在主管查詢與核准流程重新驗證

**Files:**
- Modify: `app/routers/liff.py`
- Modify: `tests/test_liff.py`

**Interfaces:**
- Consumes: `MakeupRequest` audit 欄位與 `assess_makeup_day()`。
- Produces: pending response 的 `current_day_status`，以及 manager approval 的 stale／exception confirmation gate。

- [ ] **Step 1: 新增 pending audit 與 review gate failing tests**

```python
def test_makeup_pending_returns_audit_and_current_day_status(client, db):
    manager = _add_employee(db)
    manager.is_manager = True
    _add_checkin(
        db,
        manager.id,
        CheckInType.clock_in,
        datetime(2026, 8, 26, 1, 25, tzinfo=timezone.utc),
    )
    request = MakeupRequest(
        employee_id=manager.id,
        type=CheckInType.clock_in,
        requested_at=datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc),
        reason="例外申請",
        status=MakeupRequestStatus.pending,
        day_state_at_submission="missing_clock_out",
        snapshot_token_at_submission=assess_makeup_day(
            db, manager.id, date(2026, 8, 26), ZoneInfo("Asia/Taipei")
        ).snapshot_token,
        system_suggested_type=CheckInType.clock_out,
        exception_confirmed=True,
    )
    db.add(request)
    db.commit()
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post("/liff/makeup/pending", json={"id_token": "tok"})

    item = response.json()["requests"][0]
    assert item["day_state_at_submission"] == "missing_clock_out"
    assert item["snapshot_token_at_submission"].startswith("sha256:")
    assert item["system_suggested_type"] == "clock_out"
    assert item["exception_confirmed"] is True
    assert item["current_day_status"]["state"] == "missing_clock_out"
    assert item["records_changed_since_submission"] is False


def test_makeup_review_requires_second_confirmation_for_exception(client, db):
    manager = _add_employee(db)
    manager.is_manager = True
    _add_checkin(
        db,
        manager.id,
        CheckInType.clock_in,
        datetime(2026, 8, 26, 1, 25, tzinfo=timezone.utc),
    )
    request = MakeupRequest(
        employee_id=manager.id,
        type=CheckInType.clock_in,
        requested_at=datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc),
        reason="例外申請",
        status=MakeupRequestStatus.pending,
        day_state_at_submission="missing_clock_out",
        snapshot_token_at_submission=assess_makeup_day(
            db, manager.id, date(2026, 8, 26), ZoneInfo("Asia/Taipei")
        ).snapshot_token,
        system_suggested_type=CheckInType.clock_out,
        exception_confirmed=True,
    )
    db.add(request)
    db.commit()
    current = assess_makeup_day(
        db, manager.id, date(2026, 8, 26), ZoneInfo("Asia/Taipei")
    )
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post("/liff/makeup/review", json={
            "id_token": "tok",
            "request_id": request.id,
            "action": "approve",
            "observed_day_state": "missing_clock_out",
            "observed_snapshot_token": current.snapshot_token,
            "exception_confirmed": False,
        })

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "exception_confirmation_required"
    assert db.query(CheckIn).filter_by(ip_address="makeup:approved").count() == 0
```

- [ ] **Step 2: 執行測試確認 response 與 gate 尚未存在**

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_liff.py -k 'pending_returns_audit or second_confirmation' -q`

Expected: tests fail on missing response keys or unexpected 200.

- [ ] **Step 3: 擴充 review payload 與 pending response**

```python
class MakeupReviewPayload(BaseModel):
    id_token: str
    request_id: int
    action: str
    observed_day_state: MakeupDayState | None = None
    observed_snapshot_token: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    exception_confirmed: bool = False
```

Add a serializer beside `_serialize_makeup_day()`:

```python
def _serialize_pending_makeup_request(
    db: Session,
    request: MakeupRequest,
    tz: ZoneInfo,
) -> dict[str, object]:
    local_date = request.requested_at.astimezone(tz).date()
    assessment = assess_makeup_day(db, request.employee_id, local_date, tz)
    return {
        "id": request.id,
        "employee_name": (
            request.employee.display_name
            or request.employee.full_name
            or request.employee.email
        ),
        "type": request.type.value,
        "type_label": "上班" if request.type == CheckInType.clock_in else "下班",
        "requested_at": request.requested_at.astimezone(tz).strftime("%m/%d %H:%M"),
        "reason": request.reason,
        "day_state_at_submission": request.day_state_at_submission,
        "snapshot_token_at_submission": request.snapshot_token_at_submission,
        "system_suggested_type": (
            request.system_suggested_type.value
            if request.system_suggested_type else None
        ),
        "exception_confirmed": request.exception_confirmed,
        "current_day_status": _serialize_makeup_day(assessment, tz),
        "records_changed_since_submission": (
            None
            if request.snapshot_token_at_submission is None
            else request.snapshot_token_at_submission != assessment.snapshot_token
        ),
    }
```

Return `{"requests": [_serialize_pending_makeup_request(db, request, tz) for request in requests]}` from the endpoint.

- [ ] **Step 4: 在 atomic update 前加入主管核准 gate**

Only run this block for `payload.action == "approve"`:

```python
    if (
        payload.observed_day_state is None
        or payload.observed_snapshot_token is None
    ):
        raise HTTPException(
            status_code=409,
            detail="系統已更新，請關閉並重新開啟打卡頁面。",
        )

    tz = ZoneInfo(settings.timezone)
    local_date = target.requested_at.astimezone(tz).date()
    current = assess_makeup_day(db, target.employee_id, local_date, tz)
    if (
        payload.observed_day_state != current.state
        or payload.observed_snapshot_token != current.snapshot_token
    ):
        _raise_makeup_conflict(
            "stale_day_state",
            "當日打卡紀錄已更新，請重新檢查後再核准。",
            current,
            tz,
        )

    needs_manager_confirmation = (
        target.day_state_at_submission is None
        or target.snapshot_token_at_submission is None
        or target.exception_confirmed
        or target.day_state_at_submission != current.state.value
        or target.snapshot_token_at_submission != current.snapshot_token
        or requires_exception_confirmation(current, target.type)
    )
    if needs_manager_confirmation and not payload.exception_confirmed:
        _raise_makeup_conflict(
            "exception_confirmation_required",
            "此申請與目前打卡紀錄不一致，請確認後再核准。",
            current,
            tz,
        )
```

Legacy database requests have `day_state_at_submission=None`, so a refreshed manager page always requires explicit manager confirmation. An old manager page receives the plain-string refresh response instead of `[object Object]`. Reject skips both checks.

- [ ] **Step 5: 補齊 stale、legacy、confirmed approval 與 concurrent tests**

For every existing successful approval fixture, calculate `current = assess_makeup_day(...)` after arranging its punches, then set:

```python
day_state_at_submission="no_records",
snapshot_token_at_submission=current.snapshot_token,
system_suggested_type=None,
exception_confirmed=False,
```

and send:

```python
"observed_day_state": "no_records",
"observed_snapshot_token": current.snapshot_token,
"exception_confirmed": False,
```

Add a confirmed path to `test_makeup_review_requires_second_confirmation_for_exception`:

```python
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        confirmed = client.post("/liff/makeup/review", json={
            "id_token": "tok",
            "request_id": request.id,
            "action": "approve",
            "observed_day_state": "missing_clock_out",
            "observed_snapshot_token": current.snapshot_token,
            "exception_confirmed": True,
        })

    assert confirmed.status_code == 200
    assert db.query(CheckIn).filter_by(ip_address="makeup:approved").count() == 1
```

Add:

```python
def test_makeup_review_requires_confirmation_for_legacy_request(client, db):
    manager = _add_employee(db)
    manager.is_manager = True
    request = MakeupRequest(
        employee_id=manager.id,
        type=CheckInType.clock_in,
        requested_at=datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc),
        reason="舊版申請",
        status=MakeupRequestStatus.pending,
    )
    db.add(request)
    db.commit()
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post("/liff/makeup/review", json={
            "id_token": "tok",
            "request_id": request.id,
            "action": "approve",
            "observed_day_state": "no_records",
            "observed_snapshot_token": assess_makeup_day(
                db, manager.id, date(2026, 8, 26), ZoneInfo("Asia/Taipei")
            ).snapshot_token,
            "exception_confirmed": False,
        })

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "exception_confirmation_required"


def test_makeup_review_rejects_stale_day_state(client, db):
    manager = _add_employee(db)
    manager.is_manager = True
    _add_checkin(
        db,
        manager.id,
        CheckInType.clock_in,
        datetime(2026, 8, 26, 1, 25, tzinfo=timezone.utc),
    )
    request = MakeupRequest(
        employee_id=manager.id,
        type=CheckInType.clock_out,
        requested_at=datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc),
        reason="補下班",
        status=MakeupRequestStatus.pending,
        day_state_at_submission="missing_clock_out",
        system_suggested_type=CheckInType.clock_out,
        exception_confirmed=False,
    )
    db.add(request)
    db.commit()
    _add_checkin(
        db,
        manager.id,
        CheckInType.clock_out,
        datetime(2026, 8, 26, 9, 30, tzinfo=timezone.utc),
    )
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings), patch(
        "app.routers.liff._verify_line_token",
        new_callable=AsyncMock,
        return_value=LINE_UID,
    ):
        response = client.post("/liff/makeup/review", json={
            "id_token": "tok",
            "request_id": request.id,
            "action": "approve",
            "observed_day_state": "missing_clock_out",
            "observed_snapshot_token": f"sha256:{'0' * 64}",
            "exception_confirmed": False,
        })

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_day_state"
    assert db.query(CheckIn).filter_by(ip_address="makeup:approved").count() == 0
```

Keep the existing second-review assertion `status_code in (404, 409)` and both supplemental FTP tests.

Also add a same-state stale test: submit an `ambiguous` request with two clock-ins, add a clock-out so the state remains `ambiguous` but the snapshot changes, and verify manager approval requires confirmation because `snapshot_token_at_submission != current.snapshot_token`.

Add an old-manager-page compatibility test that sends `action="approve"` without state/snapshot and asserts a plain-string `409` containing「重新開啟」; verify `action="reject"` without those fields still succeeds.

- [ ] **Step 6: 執行主管與 FTP tests**

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_liff.py -k 'makeup_pending or makeup_review or supplemental_ftp' -q`

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/routers/liff.py tests/test_liff.py
git commit -m "feat(makeup): guard manager approvals"
```

---

### Task 5: 更新員工補打卡 UI

**Files:**
- Modify: `app/routers/liff.py`
- Modify: `app/templates/liff/checkin.html`
- Modify: `tests/test_liff.py`

**Interfaces:**
- Consumes: day-status response與 request structured conflicts。
- Produces: 日期切換載入、既有紀錄、建議類型、完整寬度類型控制與行內例外確認。

- [ ] **Step 1: 新增 template contract failing test**

```python
def test_liff_page_contains_makeup_guidance_controls(client):
    settings = _mock_settings_liff()
    with patch("app.routers.liff.get_settings", return_value=settings):
        response = client.get("/liff/")

    assert response.status_code == 200
    assert 'id="makeup-day-status"' in response.text
    assert 'id="makeup-exception-confirmed"' in response.text
    assert 'loadMakeupDayStatus' in response.text
    assert 'const APP_TIMEZONE = "Asia/Taipei"' in response.text
```

- [ ] **Step 2: 執行 contract test 確認失敗**

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_liff.py::test_liff_page_contains_makeup_guidance_controls -q`

Expected: FAIL because the new IDs and JavaScript function are absent.

- [ ] **Step 3: 將 application timezone 傳入 template 並增加狀態區塊**

Update `liff_page` context:

```python
        {
            "liff_id": settings.liff_id,
            "app_base_url": settings.app_base_url,
            "timezone": settings.timezone,
        },
```

Replace the current radio block with:

```html
<div id="makeup-day-status" class="makeup-day-status" aria-live="polite"></div>
<div class="form-group">
  <label class="form-label">補卡類型</label>
  <div class="makeup-type-grid">
    <label class="makeup-type-choice">
      <input type="radio" name="makeup-type" value="clock_in" onchange="updateMakeupExceptionUI()">
      <span>上班打卡</span>
    </label>
    <label class="makeup-type-choice">
      <input type="radio" name="makeup-type" value="clock_out" onchange="updateMakeupExceptionUI()">
      <span>下班打卡</span>
    </label>
  </div>
  <div id="makeup-exception-warning" class="makeup-exception-warning" hidden>
    <div id="makeup-exception-message"></div>
    <label class="makeup-confirm-row">
      <input type="checkbox" id="makeup-exception-confirmed">
      <span>我已確認類型與時間正確</span>
    </label>
  </div>
</div>
```

Add `onchange="loadMakeupDayStatus()"` to `#makeup-date` and `id="makeup-submit"` to the submit button. Add:

```css
.makeup-day-status {
  background: var(--card); border: 1px solid rgba(255,255,255,.35);
  border-radius: var(--radius); padding: 12px 14px; margin-bottom: 16px;
}
.makeup-record-row { display: flex; justify-content: space-between; padding: 4px 0; }
.makeup-record-empty { color: var(--grey-sub); }
.makeup-suggestion { margin-top: 8px; font-weight: 700; color: var(--text); }
.makeup-type-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.makeup-type-choice { min-height: 44px; cursor: pointer; }
.makeup-type-choice input { position: absolute; opacity: 0; }
.makeup-type-choice span {
  min-height: 44px; display: flex; align-items: center; justify-content: center;
  border: 1.5px solid rgba(255,255,255,.4); border-radius: var(--radius);
  background: rgba(255,255,255,.9); color: var(--text); font-weight: 700;
}
.makeup-type-choice input:checked + span { border-color: var(--black); box-shadow: inset 0 0 0 1px var(--black); }
.makeup-exception-warning {
  margin-top: 10px; padding: 12px; border-radius: var(--radius);
  background: rgba(255,210,120,.92); color: var(--black);
}
.makeup-confirm-row { display: flex; align-items: flex-start; gap: 8px; margin-top: 9px; }
.makeup-confirm-row input { width: 20px; height: 20px; flex: 0 0 auto; }
.makeup-status-error button { min-height: 44px; margin-left: 8px; }
@media (max-width: 320px) { .makeup-type-grid { grid-template-columns: 1fr; } }
```

- [ ] **Step 4: 實作員工端狀態載入與 rendering**

```javascript
const APP_TIMEZONE = {{ timezone | tojson }};
let _makeupDayStatus = null;
let _makeupStatusRequestId = 0;

function todayInAppTimezone() {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: APP_TIMEZONE,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date()).map(part => [part.type, part.value])
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function selectMakeupType(type) {
  document.querySelectorAll('input[name="makeup-type"]').forEach(input => {
    input.checked = input.value === type;
  });
}

function renderMakeupDayStatus(status) {
  const records = status.records.length
    ? status.records.map(record =>
        `<div class="makeup-record-row"><span>${esc(record.type_label)}</span><strong>${esc(record.time)}</strong></div>`
      ).join("")
    : '<div class="makeup-record-empty">當日沒有打卡紀錄，請確認補卡類型與時間。</div>';
  const suggestion = status.suggested_type
    ? `<div class="makeup-suggestion">系統建議：補${status.suggested_type === "clock_in" ? "上班" : "下班"}卡</div>`
    : "";
  document.getElementById("makeup-day-status").innerHTML = records + suggestion;
  selectMakeupType(status.suggested_type);
  updateMakeupExceptionUI();
}

async function loadMakeupDayStatus() {
  const date = document.getElementById("makeup-date").value;
  const requestId = ++_makeupStatusRequestId;
  _makeupDayStatus = null;
  document.getElementById("makeup-submit").disabled = true;
  document.getElementById("makeup-day-status").textContent = "正在載入當日紀錄…";
  try {
    const status = await apiPost("/liff/makeup/day-status", { id_token: idToken, date });
    if (requestId !== _makeupStatusRequestId) return;
    _makeupDayStatus = status;
    renderMakeupDayStatus(status);
    document.getElementById("makeup-submit").disabled = false;
  } catch (error) {
    if (requestId !== _makeupStatusRequestId) return;
    document.getElementById("makeup-day-status").innerHTML =
      '<div class="makeup-status-error">載入失敗。<button type="button" onclick="loadMakeupDayStatus()">重試</button></div>';
  }
}
```

- [ ] **Step 5: 實作例外判斷與 structured conflict handling**

```javascript
function selectedMakeupType() {
  return document.querySelector('input[name="makeup-type"]:checked')?.value || null;
}

function makeupSelectionNeedsConfirmation() {
  if (!_makeupDayStatus) return false;
  if (["complete", "ambiguous"].includes(_makeupDayStatus.state)) return true;
  return !!_makeupDayStatus.suggested_type &&
    selectedMakeupType() !== _makeupDayStatus.suggested_type;
}

function updateMakeupExceptionUI() {
  const warning = document.getElementById("makeup-exception-warning");
  const needsConfirmation = makeupSelectionNeedsConfirmation();
  warning.hidden = !needsConfirmation;
  if (!needsConfirmation) {
    document.getElementById("makeup-exception-confirmed").checked = false;
    return;
  }
  document.getElementById("makeup-exception-message").textContent =
    _makeupDayStatus.state === "complete"
      ? "當日打卡已完整，仍要提出補卡申請嗎？"
      : _makeupDayStatus.state === "ambiguous"
        ? "當日有多筆紀錄，系統無法判斷缺卡類型。"
        : "選擇與系統建議不同，請再次確認。";
}

function applyMakeupConflict(detail) {
  if (!detail || typeof detail !== "object" || !detail.day_status) return false;
  _makeupDayStatus = detail.day_status;
  renderMakeupDayStatus(_makeupDayStatus);
  document.getElementById("makeup-exception-confirmed").checked = false;
  toast(`${SVG_WARN} ${esc(detail.message)}`, 4000);
  return true;
}
```

Replace `showMakeupForm()` with:

```javascript
function showMakeupForm() {
  const today = todayInAppTimezone();
  const dateInput = document.getElementById("makeup-date");
  dateInput.max = today;
  dateInput.value = today;
  document.getElementById("makeup-time").value = "";
  document.getElementById("makeup-reason").value = "";
  document.getElementById("makeup-exception-confirmed").checked = false;
  selectMakeupType(null);
  showScreen("screen-makeup-form", "補打卡申請", true);
  loadMakeupDayStatus();
}
```

Replace the current direct radio lookup with `const type = selectedMakeupType();`. Remove the current device-timezone `Date` construction and its `toISOString()` use; the browser may validate that both fields exist, but the server is authoritative for future-time validation. Before the current submit loading block, add:

```javascript
if (!_makeupDayStatus) { toast(`${SVG_WARN} 請先載入當日打卡紀錄`); return; }
if (!type) { toast(`${SVG_WARN} 請選擇補卡類型`); return; }
if (makeupSelectionNeedsConfirmation() &&
    !document.getElementById("makeup-exception-confirmed").checked) {
  toast(`${SVG_WARN} 請確認補卡類型與時間`);
  return;
}
```

Update request payload:

```javascript
requested_local_date: date,
requested_local_time: time,
observed_day_state: _makeupDayStatus.state,
observed_snapshot_token: _makeupDayStatus.snapshot_token,
exception_confirmed: document.getElementById("makeup-exception-confirmed").checked,
```

Before sending, reject missing status, missing selected type, or a required unchecked confirmation. In the catch block, call `applyMakeupConflict(e.detail)` before falling back to a string toast.

- [ ] **Step 6: 執行 LIFF tests 與 template smoke check**

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_liff.py -q`

Expected: all tests pass.

Run: `uv run --with-requirements requirements-dev.txt ruff check app/`

Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add app/routers/liff.py app/templates/liff/checkin.html tests/test_liff.py
git commit -m "feat(liff): clarify makeup punch selection"
```

---

### Task 6: 更新主管審核 UI 與二次確認

**Files:**
- Modify: `app/templates/liff/checkin.html`
- Modify: `tests/test_liff.py`

**Interfaces:**
- Consumes: pending audit/current status 與 review structured conflicts。
- Produces: 主管可見的異常資訊、stale refresh、具體二次確認文案。

- [ ] **Step 1: 擴充 template contract failing test**

```python
def test_liff_page_contains_manager_makeup_warning_handlers(client):
    response = client.get("/liff/")

    assert response.status_code == 200
    assert "renderMakeupReviewCard" in response.text
    assert "confirmExceptionalApproval" in response.text
    assert "current_day_status" in response.text
```

- [ ] **Step 2: 執行 test 確認 handler 尚未存在**

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_liff.py::test_liff_page_contains_manager_makeup_warning_handlers -q`

Expected: FAIL on missing handler names.

- [ ] **Step 3: 將 pending request 保存於前端並 render audit**

```javascript
const _pendingMakeupRequests = new Map();

function renderCurrentPunches(status) {
  if (!status.records.length) return "當日尚無打卡紀錄";
  return status.records
    .map(record => `${esc(record.type_label)} ${esc(record.time)}`)
    .join("、");
}

function renderMakeupReviewCard(request) {
  const suggestion = request.system_suggested_type
    ? `系統原建議：${request.system_suggested_type === "clock_in" ? "上班" : "下班"}打卡`
    : request.day_state_at_submission
      ? "系統原建議：無"
      : "舊版申請，無送出時判斷紀錄";
  const exception = request.exception_confirmed
    ? '<div class="review-warning">員工已確認與系統建議不同</div>'
    : "";
  const recordsChanged = request.records_changed_since_submission
    ? '<div class="review-warning">申請送出後，當日打卡紀錄已更新</div>'
    : "";
  return `
    <div class="review-card" id="review-card-${request.id}">
      <div class="review-header">
        <span class="review-name">${esc(request.employee_name)}</span>
        <span class="type-badge ${request.type === "clock_in" ? "badge-in" : "badge-out"}">${esc(request.type_label)}打卡</span>
      </div>
      <div class="review-time">${SVG_CAL_SM} ${esc(request.requested_at)}</div>
      <div class="review-day-status">目前紀錄：${renderCurrentPunches(request.current_day_status)}</div>
      <div class="review-suggestion">${esc(suggestion)}</div>
      ${exception}
      ${recordsChanged}
      <div class="review-reason">${SVG_CHAT_SM} ${esc(request.reason)}</div>
      <div class="review-actions">
        <button class="btn-approve" onclick="reviewRequest(${request.id},'approve',this)">✓ 核准</button>
        <button class="btn-reject" onclick="reviewRequest(${request.id},'reject',this)">✕ 拒絕</button>
      </div>
    </div>`;
}
```

In `showMakeupReview()`, replace the current `data.requests.map` rendering with:

```javascript
_pendingMakeupRequests.clear();
data.requests.forEach(request => _pendingMakeupRequests.set(request.id, request));
list.innerHTML = data.requests.map(renderMakeupReviewCard).join("");
```

- [ ] **Step 4: 實作 stale refresh 與例外二次確認**

```javascript
function approvalConfirmationMessage(request, dayStatus) {
  const requestedType = request.type === "clock_in" ? "上班" : "下班";
  return `申請補${requestedType}卡 ${request.requested_at}；目前紀錄：${
    dayStatus.records.map(record => `${record.type_label} ${record.time}`).join("、") || "無"
  }。確定仍要核准？`;
}

async function confirmExceptionalApproval(request, dayStatus) {
  return showConfirm(approvalConfirmationMessage(request, dayStatus), "確認核准");
}
```

Change the signature to:

```javascript
async function reviewRequest(
  id,
  action,
  btnEl,
  exceptionConfirmed = false,
  observedDayState = null,
  observedSnapshotToken = null,
) {
```

Always send the displayed state for approve:

```javascript
const request = _pendingMakeupRequests.get(id);
const state = observedDayState || request?.current_day_status?.state || null;
const snapshotToken = observedSnapshotToken ||
  request?.current_day_status?.snapshot_token || null;
const data = await apiPost("/liff/makeup/review", {
  id_token: idToken,
  request_id: id,
  action,
  observed_day_state: action === "approve" ? state : null,
  observed_snapshot_token: action === "approve" ? snapshotToken : null,
  exception_confirmed: action === "approve" && exceptionConfirmed,
});
```

Handle structured 409 responses before the generic toast:

```javascript
if (e.detail?.code === "stale_day_state") {
  toast(`${SVG_WARN} ${esc(e.detail.message)}`, 4000);
  await showMakeupReview();
  return;
}
if (e.detail?.code === "exception_confirmation_required") {
  setLoading(false);
  const confirmed = await confirmExceptionalApproval(request, e.detail.day_status);
  if (confirmed) {
    await reviewRequest(
      id,
      action,
      btnEl,
      true,
      e.detail.day_status.state,
      e.detail.day_status.snapshot_token,
    );
    return;
  }
}
```

Re-enable both action buttons on every canceled or failed path:

```javascript
btnEl?.parentElement.querySelectorAll("button").forEach(button => {
  button.disabled = false;
});
```

Add:

```css
.review-day-status, .review-suggestion {
  font-size: 13px; color: var(--grey-sub); margin-bottom: 8px;
  overflow-wrap: anywhere;
}
.review-warning {
  padding: 9px 10px; margin-bottom: 10px; border-radius: var(--radius);
  background: rgba(255,210,120,.92); color: var(--black); font-weight: 700;
}
```

- [ ] **Step 5: 執行 LIFF tests**

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_liff.py -q`

Expected: all tests pass, including supplemental FTP and concurrent review cases.

- [ ] **Step 6: Commit**

```bash
git add app/templates/liff/checkin.html tests/test_liff.py
git commit -m "feat(liff): surface makeup review warnings"
```

---

### Task 7: 更新文件並完成整體驗證

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `Dockerfile`
- Modify: `.github/workflows/ci.yml`
- Modify: `deploy.sh`
- Create: `tests/test_deploy_contract.py`

**Interfaces:**
- Consumes: Tasks 1–6 的最終行為。
- Produces: 人類交接規則、agent 不可破壞規則與完整驗證證據。

- [ ] **Step 1: 更新 README 重要業務規則**

Add after the existing makeup rule:

```markdown
- 補打卡會依員工在所選本地日期的既有 `clock_in`／`clock_out` 建議缺少的類型；員工可以改選，但必須確認例外，主管核准時也會重新檢查並二次確認異常狀態。
```

Update the deployment section to state that CI runs the one-task `line-clockio-migrate` Cloud Run Job and waits for success before deploying the service; application instances no longer run Alembic during startup.

- [ ] **Step 2: 更新 AGENTS 不可破壞規則**

Add:

```markdown
- 補打卡類型建議以 `Settings.timezone` 的本地日期查詢 DB；前端只負責顯示，request 與 approve 都必須重新判斷。與建議不同、當日完整或紀錄異常時，需保留員工 audit 並要求主管二次確認。
- Production migration 必須由單一 Cloud Run migration job 在 service deploy 前完成；Cloud Run service container startup 不得自行執行 Alembic。
```

- [ ] **Step 3: 執行 focused tests**

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_makeup_validation.py tests/test_liff.py tests/test_jobs.py -q`

Expected: all tests pass; only known warnings may remain.

- [ ] **Step 4: 執行完整 unit tests 與 lint**

Run: `uv run --with-requirements requirements-dev.txt pytest -q`

Expected: all tests pass.

Run: `uv run --with-requirements requirements-dev.txt ruff check app/`

Expected: exit 0.

Run: `uv run --with-requirements requirements-dev.txt ruff check app/ tests/`

Expected: only the documented pre-existing `F841` may remain; no new violations are allowed.

- [ ] **Step 5: 在空白 PostgreSQL 驗證 migration upgrade／downgrade**

```bash
docker run --rm --name line-clockio-migration-test \
  -e POSTGRES_PASSWORD=test-password \
  -e POSTGRES_DB=line_clockio_migration \
  -p 55432:5432 -d postgres:16
trap 'docker stop line-clockio-migration-test >/dev/null 2>&1 || true' EXIT
for attempt in {1..30}; do
  docker exec line-clockio-migration-test pg_isready -U postgres && break
  sleep 1
done
docker exec line-clockio-migration-test pg_isready -U postgres
env DATABASE_URL=postgresql://postgres:test-password@127.0.0.1:55432/line_clockio_migration \
  uv run --with-requirements requirements-dev.txt alembic upgrade head
env DATABASE_URL=postgresql://postgres:test-password@127.0.0.1:55432/line_clockio_migration \
  uv run --with-requirements requirements-dev.txt alembic downgrade 004
env DATABASE_URL=postgresql://postgres:test-password@127.0.0.1:55432/line_clockio_migration \
  uv run --with-requirements requirements-dev.txt alembic upgrade head
docker stop line-clockio-migration-test
trap - EXIT
```

Expected: all three commands exit 0; downgrade removes only the four `005` columns.

- [ ] **Step 6: 將 production migration 從 service startup 移到 release gate**

先以 read-only 指令核對目前 production service；將輸出記錄在 PR，不要輸出任何 secret 值：

```bash
gcloud run services describe line-clockio \
  --project aiotek-bot \
  --region asia-east1 \
  --format='yaml(spec.template.spec.serviceAccountName,spec.template.metadata.annotations,status.traffic)'
gcloud sql instances describe line-clockio-db-new \
  --project aiotek-bot \
  --format='yaml(connectionName,settings.ipConfiguration)'
```

若實際 instance、runtime service account 或 network attachment 與 repository 不同，停止並先修正 plan／workflow；不得猜測 production topology。確認後：

1. 把 `Dockerfile` 的 `CMD` 改為只啟動 Uvicorn，移除每個 Cloud Run instance startup 都執行的 `alembic upgrade head`。
2. 在 deploy job 中、service deploy 之前，以同一 commit source 建立或更新 `line-clockio-migrate` Cloud Run Job。
3. Migration job 使用已核對的 runtime service account、Cloud SQL connection 與必要 network 設定，只注入 `DATABASE_URL`。
4. 執行 migration job 並使用 `--wait`；失敗時 workflow 必須立即停止，不得繼續 `gcloud run deploy`。
5. Migration 成功後才部署 `line-clockio` service。
6. 為 deploy job 設定 GitHub Actions concurrency group，`cancel-in-progress: false`，避免兩次 main push 同時執行 migration。
7. 同步修改 `deploy.sh`：它已先 build/push `${IMAGE}`，因此 migration job 與 service 都使用該 image；migration execution `--wait` 失敗時由既有 `set -e` 中止。不得留下可繞過 migration gate 的正式手動部署路徑。

Workflow command shape：

```bash
gcloud run jobs deploy line-clockio-migrate \
  --source . \
  --region asia-east1 \
  --project aiotek-bot \
  --service-account "${RUNTIME_SERVICE_ACCOUNT}" \
  --set-cloudsql-instances "${CLOUD_SQL_CONNECTION_NAME}" \
  --set-secrets DATABASE_URL=DATABASE_URL:latest \
  --command alembic \
  --args upgrade,head \
  --tasks 1 \
  --parallelism 1 \
  --max-retries 0 \
  --quiet
gcloud run jobs execute line-clockio-migrate \
  --region asia-east1 \
  --project aiotek-bot \
  --wait
```

In `.github/workflows/ci.yml`, `--source .` and the following service deployment run from the same checked-out commit. In `deploy.sh`, replace `--source .` with `--image "${IMAGE}"` for the migration job so migration and service use the exact image pushed by that script.

If the inspected service uses Direct VPC or a connector, add the equivalent supported network flags to the job. Verify the GitHub deploy service account can create/update/execute the job and act as the runtime service account; missing IAM is a deployment blocker, not a reason to bypass the migration gate.

Add a contract test proving `Dockerfile` no longer contains `alembic upgrade head`，且 CI workflow 與 `deploy.sh` 都在 service deploy 前執行並等待 `line-clockio-migrate` 成功：

```python
from pathlib import Path


def test_service_container_does_not_run_migrations_on_startup():
    assert "alembic upgrade head" not in Path("Dockerfile").read_text()


def test_ci_waits_for_migration_before_service_deploy():
    workflow = Path(".github/workflows/ci.yml").read_text()
    migration = "gcloud run jobs execute line-clockio-migrate"
    service = "gcloud run deploy line-clockio"

    assert migration in workflow
    assert workflow.index(migration) < workflow.index(service)
    assert "cancel-in-progress: false" in workflow


def test_manual_deploy_waits_for_migration_before_service_deploy():
    script = Path("deploy.sh").read_text()
    migration = "gcloud run jobs execute line-clockio-migrate"
    service = 'gcloud run deploy "${SERVICE}"'

    assert migration in script
    assert script.index(migration) < script.index(service)
    assert "--wait" in script[script.index(migration):script.index(service)]
```

Run: `uv run --with-requirements requirements-dev.txt pytest tests/test_deploy_contract.py -q`

Expected: all three contract tests pass.

Run: `docker build --platform linux/amd64 -t line-clockio:makeup-guidance-test .`

Expected: image builds successfully with the Uvicorn-only service command. Building the image must not execute Alembic or contact production resources.

- [ ] **Step 7: 執行本機 HTTP 與 JavaScript smoke test**

Run with local non-production settings:

```bash
env LINE_CHANNEL_ACCESS_TOKEN=smoke \
  LINE_CHANNEL_SECRET=smoke \
  LIFF_ID=smoke \
  LIFF_CHANNEL_ID=smoke \
  DATABASE_URL=sqlite:////tmp/line-clockio-smoke.db \
  SESSION_SECRET_KEY=smoke-only-session-key \
  APP_BASE_URL=http://127.0.0.1:8000 \
  uv run --with-requirements requirements-dev.txt \
  uvicorn app.main:app --host 127.0.0.1 --port 8000
```

From another terminal:

```bash
SMOKE_DIR="$(mktemp -d)"
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/liff/ -o "${SMOKE_DIR}/liff.html"
bun -e 'const html = await Bun.file(process.argv[1]).text(); const blocks = [...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)].map(match => match[1]); await Bun.write(process.argv[2], blocks.join("\n"));' \
  "${SMOKE_DIR}/liff.html" "${SMOKE_DIR}/liff-inline.js"
bun build "${SMOKE_DIR}/liff-inline.js" \
  --target=browser --outfile="${SMOKE_DIR}/liff-inline.bundle.js"
```

Expected: both requests return HTTP 200 and `bun build` exits 0. The temporary directory may be removed after inspection. Do not connect this smoke test to production DB, LINE, Mailgun or FTP.

- [ ] **Step 8: 執行 LINE LIFF 手機驗收**

Verify these exact flows using an explicitly authorized test employee and manager:

1. One clock-in suggests clock-out.
2. One clock-out suggests clock-in.
3. Overriding the suggestion shows inline confirmation and manager warning.
4. Complete and ambiguous days require confirmation.
5. No-record day allows either type without an exception marker.
6. A stale employee form reloads current records instead of submitting.
7. A stale or exceptional manager approval requires concrete second confirmation.

Expected: all seven flows match the spec at 320px width and on a physical LINE client. Do not create punches for real employees.

- [ ] **Step 9: Commit deployment and documentation changes**

```bash
git add README.md AGENTS.md Dockerfile .github/workflows/ci.yml deploy.sh tests/test_deploy_contract.py
git commit -m "ci(deploy): gate service rollout on migrations"
```

- [ ] **Step 10: 最終重新驗證**

After all source, deployment, test, and documentation edits, rerun:

```bash
uv run --with-requirements requirements-dev.txt pytest -q
uv run --with-requirements requirements-dev.txt ruff check app/
uv run --with-requirements requirements-dev.txt ruff check tests/test_deploy_contract.py
```

Expected: pytest and both lint commands pass. This is the completion evidence; do not rely only on the earlier pre-deployment-edit run.

- [ ] **Step 11: Final diff and secret check**

Run:

```bash
git status --short --branch
git diff --check origin/main...HEAD
git diff --stat origin/main...HEAD
if git diff origin/main...HEAD -- . ':!docs/superpowers/**' | \
  rg -n '^\+.*(FTP_PASSWORD=|LINE_CHANNEL_ACCESS_TOKEN=|SESSION_SECRET_KEY=|"private_key")'; then
  echo "Potential credential assignment found; inspect before continuing."
  exit 1
fi
```

Expected: only intended files are changed; `git diff --check` and the guarded secret scan exit 0 with no matches, proving no credential assignment or service-account JSON was introduced outside the checked-in plan examples. Review any match manually.

Use `superpowers:verification-before-completion` before reporting implementation complete. Then follow the repository Superpowers workflow: open a draft PR, run fresh-context review, resolve findings under the three-round convergence rule, mark the PR ready only after review passes, and integrate only by squash merge with branch deletion.
