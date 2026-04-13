# LINE 打卡系統（line-clockio-bd）實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一套以 LINE Messaging API + LIFF 為介面的員工打卡系統，後端為 FastAPI + PostgreSQL，主管可透過 Web Dashboard 或 LINE 查詢出勤紀錄。

**Architecture:** FastAPI 處理所有後端邏輯，分為三個路由器：Webhook（LINE 文字互動）、LIFF（打卡，強制 Geolocation + IP）、Dashboard（主管後台 Jinja2）。資料庫使用 PostgreSQL（Railway），OTP 發送使用 Resend。

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL, LINE Messaging API, LIFF SDK 2, Resend, bcrypt, Jinja2, pytest, Railway

---

## Spec Gap 注意

規格的 managers 表用於 Web 登入，但主管也需要透過 LINE 查詢。解法：`employees` 表加 `is_manager` 欄位。主管先完成 LINE 綁定（成為 employee），之後由管理員在 DB 將 `is_manager` 設為 `true`。

---

## File Structure

```
line-clockio-bd/
├── app/
│   ├── __init__.py
│   ├── main.py                      # FastAPI entry point, middleware
│   ├── config.py                    # pydantic-settings
│   ├── database.py                  # SQLAlchemy engine + session + Base
│   ├── models/
│   │   ├── __init__.py
│   │   ├── employee.py              # employees 表
│   │   ├── check_in.py              # check_ins 表
│   │   ├── manager.py               # managers 表（Web Dashboard 帳號）
│   │   └── email_verification.py   # email_verifications 表（OTP）
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── webhook.py               # POST /webhook，LINE 文字互動
│   │   ├── liff.py                  # GET/POST /liff/checkin
│   │   └── dashboard.py            # /dashboard/* 主管後台
│   ├── services/
│   │   ├── __init__.py
│   │   ├── line_service.py          # LINE API 呼叫（reply, get_profile, verify token）
│   │   ├── otp_service.py           # OTP 產生、寄信、驗證
│   │   ├── checkin_service.py       # 打卡業務邏輯
│   │   ├── auth_service.py          # bcrypt hash/verify
│   │   └── report_service.py        # 月份出勤摘要產生
│   └── templates/
│       ├── liff/
│       │   └── checkin.html         # LIFF 打卡頁面
│       └── dashboard/
│           ├── login.html
│           └── index.html           # 紀錄列表 + CSV 匯出
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 001_initial_schema.py
├── tests/
│   ├── conftest.py
│   ├── test_webhook.py
│   ├── test_binding.py
│   ├── test_checkin.py
│   ├── test_auth.py
│   └── test_report.py
├── scripts/
│   └── create_manager.py            # 一次性：建立 Web Dashboard 主管帳號
├── alembic.ini
├── requirements.txt
├── .env.example
└── Procfile
```

---

## Task 1: Project Scaffold + Configuration

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/database.py`
- Create: `app/main.py`

- [ ] **Step 1: Create `requirements.txt`**

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
sqlalchemy==2.0.30
alembic==1.13.1
psycopg2-binary==2.9.9
httpx==0.27.0
bcrypt==4.1.3
python-multipart==0.0.9
jinja2==3.1.4
python-dotenv==1.0.1
pydantic-settings==2.3.1
resend==0.8.0
itsdangerous==2.2.0
pytest==8.2.0
pytest-asyncio==0.23.7
```

- [ ] **Step 2: Create `.env.example`**

```env
# LINE Messaging API
LINE_CHANNEL_ACCESS_TOKEN=your_channel_access_token
LINE_CHANNEL_SECRET=your_channel_secret

# LINE LIFF
LIFF_ID=your_liff_id
LIFF_CHANNEL_ID=your_liff_channel_id  # LINE Login channel ID linked to LIFF

# Database (Railway provides this)
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Email (Resend)
RESEND_API_KEY=re_xxxxxxxxxxxx
RESEND_FROM_EMAIL=noreply@your-verified-domain.com

# Session
SESSION_SECRET_KEY=generate_with_openssl_rand_hex_32

# App
APP_BASE_URL=https://your-app.railway.app
TIMEZONE=Asia/Taipei
```

- [ ] **Step 3: Create `app/config.py`**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    line_channel_access_token: str
    line_channel_secret: str
    liff_id: str
    liff_channel_id: str
    database_url: str
    resend_api_key: str
    resend_from_email: str
    session_secret_key: str
    app_base_url: str
    timezone: str = "Asia/Taipei"

    class Config:
        env_file = ".env"


settings = Settings()
```

- [ ] **Step 4: Create `app/database.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings


engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 5: Create `app/__init__.py`** (empty file)

- [ ] **Step 6: Create `app/main.py`**

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import webhook, liff, dashboard

app = FastAPI(title="LINE Clockio", docs_url=None, redoc_url=None)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    https_only=False,  # Railway terminates SSL at proxy; app receives HTTP
)

app.include_router(webhook.router)
app.include_router(liff.router)
app.include_router(dashboard.router)


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 7: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: packages install without errors.

- [ ] **Step 8: Commit**

```bash
git add requirements.txt .env.example app/
git commit -m "feat: project scaffold, config, database setup"
```

---

## Task 2: Database Models

**Files:**
- Create: `app/models/__init__.py`
- Create: `app/models/employee.py`
- Create: `app/models/check_in.py`
- Create: `app/models/manager.py`
- Create: `app/models/email_verification.py`

- [ ] **Step 1: Create `app/models/__init__.py`** (empty)

- [ ] **Step 2: Create `app/models/employee.py`**

```python
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func

from app.database import Base


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    line_user_id = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False)
    display_name = Column(String(100))
    is_active = Column(Boolean, default=True, nullable=False)
    is_manager = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 3: Create `app/models/check_in.py`**

```python
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class CheckIn(Base):
    __tablename__ = "check_ins"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    type = Column(String(10), nullable=False)  # 'clock_in' | 'clock_out'
    checked_at = Column(DateTime(timezone=True), server_default=func.now())
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    ip_address = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Create `app/models/manager.py`**

```python
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func

from app.database import Base


class Manager(Base):
    __tablename__ = "managers"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    email = Column(String(100))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 5: Create `app/models/email_verification.py`**

```python
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func

from app.database import Base


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id = Column(Integer, primary_key=True, index=True)
    line_user_id = Column(String(50), nullable=False, index=True)
    email = Column(String(100), nullable=False)
    otp_code = Column(String(6), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 6: Commit**

```bash
git add app/models/
git commit -m "feat: database models (employee, check_in, manager, email_verification)"
```

---

## Task 3: Alembic Migration

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/001_initial_schema.py`

- [ ] **Step 1: Initialize Alembic**

```bash
alembic init migrations
```

Expected: creates `alembic.ini` and `migrations/` directory.

- [ ] **Step 2: Update `alembic.ini`** — replace the `sqlalchemy.url` line with a placeholder (actual URL is set in env.py)

Find line:
```
sqlalchemy.url = driver://user:pass@localhost/dbname
```
Replace with:
```
sqlalchemy.url =
```

- [ ] **Step 3: Replace `migrations/env.py`**

```python
import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

config = context.config
fileConfig(config.config_file_name)

# Import all models so Alembic sees them
from app.database import Base
from app.models import employee, check_in, manager, email_verification  # noqa: F401
from app.config import settings

config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Create `migrations/versions/001_initial_schema.py`**

```python
"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-13
"""
import sqlalchemy as sa
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("line_user_id", sa.String(50), unique=True, nullable=False),
        sa.Column("email", sa.String(100), unique=True, nullable=False),
        sa.Column("display_name", sa.String(100)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_manager", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_employees_line_user_id", "employees", ["line_user_id"])

    op.create_table(
        "check_ins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("type", sa.String(10), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("ip_address", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_check_ins_employee_id", "check_ins", ["employee_id"])

    op.create_table(
        "managers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(50), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("email", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "email_verifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("line_user_id", sa.String(50), nullable=False),
        sa.Column("email", sa.String(100), nullable=False),
        sa.Column("otp_code", sa.String(6), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_email_verifications_line_user_id", "email_verifications", ["line_user_id"])


def downgrade():
    op.drop_table("email_verifications")
    op.drop_table("managers")
    op.drop_table("check_ins")
    op.drop_table("employees")
```

- [ ] **Step 5: Run migration against dev PostgreSQL**

Copy `.env.example` to `.env` and fill in `DATABASE_URL` with your local/Railway PostgreSQL URL, then:

```bash
alembic upgrade head
```

Expected output:
```
INFO  [alembic.runtime.migration] Running upgrade  -> 001, initial schema
```

- [ ] **Step 6: Commit**

```bash
git add alembic.ini migrations/
git commit -m "feat: alembic migration — initial schema"
```

---

## Task 4: LINE Webhook Endpoint + Signature Verification

**Files:**
- Create: `app/routers/__init__.py`
- Create: `app/routers/webhook.py`
- Create: `tests/conftest.py`
- Create: `tests/test_webhook.py`

- [ ] **Step 1: Write failing test — `tests/conftest.py`**

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app

TEST_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 2: Write failing tests — `tests/test_webhook.py`**

```python
import base64
import hashlib
import hmac
import json

from app.config import settings


def make_signature(body: bytes) -> str:
    digest = hmac.new(settings.line_channel_secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def test_webhook_rejects_invalid_signature(client):
    body = b'{"events": []}'
    response = client.post(
        "/webhook",
        content=body,
        headers={"X-Line-Signature": "bad_signature"},
    )
    assert response.status_code == 400


def test_webhook_accepts_valid_empty_events(client):
    body = json.dumps({"events": []}).encode()
    response = client.post(
        "/webhook",
        content=body,
        headers={"X-Line-Signature": make_signature(body)},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_webhook.py -v
```

Expected: `ImportError` or `404` — routers not yet created.

- [ ] **Step 4: Create `app/routers/__init__.py`** (empty)

- [ ] **Step 5: Create `app/routers/webhook.py`**

```python
import base64
import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

router = APIRouter()


def verify_line_signature(body: bytes, signature: str) -> bool:
    digest = hmac.new(settings.line_channel_secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode() == signature


@router.post("/webhook")
async def webhook(
    request: Request,
    x_line_signature: str = Header(...),
    db: Session = Depends(get_db),
):
    body = await request.body()
    if not verify_line_signature(body, x_line_signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    data = json.loads(body)
    for event in data.get("events", []):
        await handle_event(event, db)

    return {"status": "ok"}


async def handle_event(event: dict, db: Session):
    if event.get("type") == "message" and event.get("message", {}).get("type") == "text":
        await handle_text_message(event, db)


async def handle_text_message(event: dict, db: Session):
    pass  # Implemented in Task 5
```

- [ ] **Step 6: Add placeholder routers to `app/main.py`** — ensure liff and dashboard routers exist (create empty files):

Create `app/routers/liff.py`:
```python
from fastapi import APIRouter
router = APIRouter()
```

Create `app/routers/dashboard.py`:
```python
from fastapi import APIRouter
router = APIRouter()
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest tests/test_webhook.py -v
```

Expected:
```
PASSED tests/test_webhook.py::test_webhook_rejects_invalid_signature
PASSED tests/test_webhook.py::test_webhook_accepts_valid_empty_events
```

- [ ] **Step 8: Commit**

```bash
git add app/routers/ tests/
git commit -m "feat: LINE webhook endpoint with signature verification"
```

---

## Task 5: Employee Binding Flow (OTP)

**Files:**
- Create: `app/services/__init__.py`
- Create: `app/services/line_service.py`
- Create: `app/services/otp_service.py`
- Modify: `app/routers/webhook.py`
- Create: `tests/test_binding.py`

- [ ] **Step 1: Write failing tests — `tests/test_binding.py`**

```python
import json
import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.models.email_verification import EmailVerification
from app.models.employee import Employee


def make_signature(body: bytes) -> str:
    digest = hmac.new(settings.line_channel_secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def make_text_event(line_user_id: str, text: str, reply_token: str = "tok") -> dict:
    return {
        "type": "message",
        "replyToken": reply_token,
        "source": {"userId": line_user_id, "type": "user"},
        "message": {"type": "text", "id": "1", "text": text},
    }


def post_webhook(client, events: list):
    body = json.dumps({"events": events}).encode()
    sig = make_signature(body)
    return client.post("/webhook", content=body, headers={"X-Line-Signature": sig})


def test_binding_sends_otp_for_valid_email(client, db):
    with patch("app.services.otp_service.send_otp_email") as mock_send, \
         patch("app.services.line_service.reply_text", new_callable=AsyncMock):

        response = post_webhook(client, [make_text_event("U001", "john@company.com")])

        assert response.status_code == 200
        mock_send.assert_called_once_with("john@company.com", mock_send.call_args[0][1])

        verification = db.query(EmailVerification).filter_by(line_user_id="U001").first()
        assert verification is not None
        assert verification.used is False


def test_binding_creates_employee_with_valid_otp(client, db):
    otp_record = EmailVerification(
        line_user_id="U001",
        email="john@company.com",
        otp_code="123456",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(otp_record)
    db.commit()

    with patch("app.services.line_service.get_user_profile", new_callable=AsyncMock,
               return_value={"displayName": "John"}), \
         patch("app.services.line_service.reply_text", new_callable=AsyncMock):

        post_webhook(client, [make_text_event("U001", "123456")])

        employee = db.query(Employee).filter_by(line_user_id="U001").first()
        assert employee is not None
        assert employee.email == "john@company.com"
        assert employee.display_name == "John"


def test_binding_rejects_expired_otp(client, db):
    otp_record = EmailVerification(
        line_user_id="U001",
        email="john@company.com",
        otp_code="123456",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),  # expired
    )
    db.add(otp_record)
    db.commit()

    with patch("app.services.line_service.reply_text", new_callable=AsyncMock) as mock_reply:
        post_webhook(client, [make_text_event("U001", "123456")])

        assert db.query(Employee).filter_by(line_user_id="U001").first() is None
        reply_text = mock_reply.call_args[0][1]
        assert "錯誤" in reply_text or "過期" in reply_text


def test_binding_ignores_invalid_text(client, db):
    with patch("app.services.line_service.reply_text", new_callable=AsyncMock) as mock_reply:
        post_webhook(client, [make_text_event("U001", "random text")])

        assert db.query(Employee).filter_by(line_user_id="U001").first() is None
        mock_reply.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_binding.py -v
```

Expected: `ImportError` — services not yet created.

- [ ] **Step 3: Create `app/services/__init__.py`** (empty)

- [ ] **Step 4: Create `app/services/line_service.py`**

```python
import httpx
from fastapi import HTTPException

from app.config import settings

_HEADERS = lambda: {
    "Authorization": f"Bearer {settings.line_channel_access_token}",
    "Content-Type": "application/json",
}


async def reply_text(reply_token: str, text: str) -> None:
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://api.line.me/v2/bot/message/reply",
            headers=_HEADERS(),
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
        )


async def get_user_profile(line_user_id: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.line.me/v2/bot/profile/{line_user_id}",
            headers=_HEADERS(),
        )
        return resp.json()


async def verify_liff_id_token(id_token: str) -> str:
    """Verify LIFF ID token with LINE API. Returns line_user_id (sub)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.line.me/oauth2/v2.1/verify",
            data={"id_token": id_token, "client_id": settings.liff_channel_id},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="LINE 身份驗證失敗")
    return resp.json()["sub"]
```

- [ ] **Step 5: Create `app/services/otp_service.py`**

```python
import random
import string
from datetime import datetime, timedelta, timezone

import resend
from sqlalchemy.orm import Session

from app.config import settings
from app.models.email_verification import EmailVerification

resend.api_key = settings.resend_api_key


def generate_otp() -> str:
    return "".join(random.choices(string.digits, k=6))


def create_otp(db: Session, line_user_id: str, email: str) -> str:
    otp = generate_otp()
    verification = EmailVerification(
        line_user_id=line_user_id,
        email=email,
        otp_code=otp,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(verification)
    db.commit()
    return otp


def send_otp_email(to_email: str, otp: str) -> None:
    resend.Emails.send({
        "from": settings.resend_from_email,
        "to": to_email,
        "subject": "LINE 打卡系統綁定驗證碼",
        "html": (
            f"<p>您的驗證碼為：<strong>{otp}</strong>（10 分鐘內有效）</p>"
            "<p>如非本人操作，請忽略此信。</p>"
        ),
    })


def verify_otp(db: Session, line_user_id: str, otp_code: str) -> str | None:
    """Returns bound email if OTP valid and unused, None otherwise."""
    now = datetime.now(timezone.utc)
    record = (
        db.query(EmailVerification)
        .filter(
            EmailVerification.line_user_id == line_user_id,
            EmailVerification.otp_code == otp_code,
            EmailVerification.expires_at > now,
            EmailVerification.used == False,  # noqa: E712
        )
        .first()
    )
    if not record:
        return None
    record.used = True
    db.commit()
    return record.email
```

- [ ] **Step 6: Replace `handle_text_message` in `app/routers/webhook.py`**

Add these imports at the top of the file:
```python
import re

from app.models.employee import Employee
from app.services import line_service, otp_service
```

Replace the existing `handle_text_message` and add helper functions:
```python
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
QUERY_REGEX = re.compile(r"^查詢\s*(\d{4})[/\-](\d{1,2})$")


async def handle_text_message(event: dict, db: Session):
    line_user_id = event["source"]["userId"]
    reply_token = event["replyToken"]
    text = event["message"]["text"].strip()

    employee = db.query(Employee).filter_by(line_user_id=line_user_id, is_active=True).first()

    if employee is None:
        await handle_binding_flow(line_user_id, reply_token, text, db)
    elif employee.is_manager and QUERY_REGEX.match(text):
        await handle_manager_query(reply_token, text, db)
    else:
        await line_service.reply_text(reply_token, "請使用打卡功能按鈕進行打卡。")


async def handle_binding_flow(line_user_id: str, reply_token: str, text: str, db: Session):
    if text.isdigit() and len(text) == 6:
        email = otp_service.verify_otp(db, line_user_id, text)
        if email is None:
            await line_service.reply_text(reply_token, "驗證碼錯誤或已過期，請重新傳送您的公司 email。")
            return
        profile = await line_service.get_user_profile(line_user_id)
        display_name = profile.get("displayName", "")
        employee = Employee(line_user_id=line_user_id, email=email, display_name=display_name)
        db.add(employee)
        db.commit()
        await line_service.reply_text(reply_token, f"綁定成功！歡迎 {display_name}，您可以開始打卡了。")
    elif EMAIL_REGEX.match(text):
        otp = otp_service.create_otp(db, line_user_id, text)
        otp_service.send_otp_email(text, otp)
        await line_service.reply_text(reply_token, f"驗證碼已發送至 {text}，請在 10 分鐘內回傳 6 位數驗證碼。")
    else:
        await line_service.reply_text(reply_token, "請傳送您的公司 email 完成帳號綁定。")


async def handle_manager_query(reply_token: str, text: str, db: Session):
    from app.services.report_service import generate_monthly_report

    match = QUERY_REGEX.match(text)
    year, month = int(match.group(1)), int(match.group(2))
    report = generate_monthly_report(db, year, month)
    await line_service.reply_text(reply_token, report)
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest tests/test_binding.py -v
```

Expected:
```
PASSED tests/test_binding.py::test_binding_sends_otp_for_valid_email
PASSED tests/test_binding.py::test_binding_creates_employee_with_valid_otp
PASSED tests/test_binding.py::test_binding_rejects_expired_otp
PASSED tests/test_binding.py::test_binding_ignores_invalid_text
```

- [ ] **Step 8: Commit**

```bash
git add app/services/ app/routers/webhook.py tests/test_binding.py
git commit -m "feat: employee binding flow with email OTP verification"
```

---

## Task 6: LIFF Check-in Page + API

**Files:**
- Create: `app/templates/liff/checkin.html`
- Create: `app/services/checkin_service.py`
- Modify: `app/routers/liff.py`
- Modify: `app/main.py` (add Jinja2Templates)
- Create: `tests/test_checkin.py`

- [ ] **Step 1: Write failing tests — `tests/test_checkin.py`**

```python
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.models.check_in import CheckIn
from app.models.employee import Employee


def test_liff_checkin_success(client, db):
    employee = Employee(line_user_id="U001", email="john@company.com", display_name="John")
    db.add(employee)
    db.commit()

    with patch("app.services.line_service.verify_liff_id_token", new_callable=AsyncMock, return_value="U001"):
        resp = client.post("/liff/checkin", json={
            "id_token": "tok",
            "type": "clock_in",
            "latitude": 25.0330,
            "longitude": 121.5654,
        })

    assert resp.status_code == 200
    assert "上班" in resp.json()["message"]
    checkin = db.query(CheckIn).filter_by(employee_id=employee.id).first()
    assert checkin is not None
    assert checkin.latitude == 25.0330
    assert checkin.type == "clock_in"


def test_liff_checkin_rejects_unbound_user(client, db):
    with patch("app.services.line_service.verify_liff_id_token", new_callable=AsyncMock, return_value="U_UNBOUND"):
        resp = client.post("/liff/checkin", json={
            "id_token": "tok",
            "type": "clock_in",
            "latitude": 25.0,
            "longitude": 121.0,
        })

    assert resp.status_code == 400
    assert "綁定" in resp.json()["detail"]


def test_liff_checkin_rejects_duplicate_within_2_hours(client, db):
    employee = Employee(line_user_id="U001", email="john@company.com", display_name="John")
    db.add(employee)
    db.commit()

    recent = CheckIn(
        employee_id=employee.id,
        type="clock_in",
        latitude=25.0,
        longitude=121.0,
        ip_address="1.2.3.4",
        checked_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    db.add(recent)
    db.commit()

    with patch("app.services.line_service.verify_liff_id_token", new_callable=AsyncMock, return_value="U001"):
        resp = client.post("/liff/checkin", json={
            "id_token": "tok",
            "type": "clock_in",
            "latitude": 25.0,
            "longitude": 121.0,
        })

    assert resp.status_code == 400
    assert "2 小時" in resp.json()["detail"]


def test_liff_checkin_rejects_invalid_type(client, db):
    with patch("app.services.line_service.verify_liff_id_token", new_callable=AsyncMock, return_value="U001"):
        resp = client.post("/liff/checkin", json={
            "id_token": "tok",
            "type": "bad_type",
            "latitude": 25.0,
            "longitude": 121.0,
        })

    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_checkin.py -v
```

Expected: `404` for `/liff/checkin` — not yet implemented.

- [ ] **Step 3: Create `app/services/checkin_service.py`**

```python
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.employee import Employee


def get_employee_by_line_id(db: Session, line_user_id: str) -> Employee | None:
    return db.query(Employee).filter_by(line_user_id=line_user_id, is_active=True).first()


def has_recent_checkin(db: Session, employee_id: int, checkin_type: str) -> bool:
    two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    return (
        db.query(CheckIn)
        .filter(
            CheckIn.employee_id == employee_id,
            CheckIn.type == checkin_type,
            CheckIn.checked_at >= two_hours_ago,
        )
        .first()
        is not None
    )


def create_checkin(
    db: Session,
    employee_id: int,
    checkin_type: str,
    latitude: float,
    longitude: float,
    ip_address: str,
) -> CheckIn:
    checkin = CheckIn(
        employee_id=employee_id,
        type=checkin_type,
        latitude=latitude,
        longitude=longitude,
        ip_address=ip_address,
    )
    db.add(checkin)
    db.commit()
    db.refresh(checkin)
    return checkin
```

- [ ] **Step 4: Create LIFF template — `app/templates/liff/checkin.html`**

```bash
mkdir -p app/templates/liff app/templates/dashboard
```

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>打卡</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background: #f0f4f8; padding: 1rem; }
        h1 { color: #333; font-size: 1.4rem; text-align: center; margin-bottom: 1.5rem; }
        #checkin-btn { background: #06c755; color: white; border: none; padding: 1rem 2.5rem; border-radius: 8px; font-size: 1.1rem; cursor: pointer; }
        #checkin-btn:disabled { background: #aaa; cursor: not-allowed; }
        .error { color: #e53e3e; }
        .success { color: #06c755; font-weight: bold; }
    </style>
</head>
<body>
    <h1 id="status">正在初始化...</h1>
    <button id="checkin-btn" style="display:none;">確認打卡</button>

    <script src="https://static.line-scdn.net/liff/edge/2/sdk.js"></script>
    <script>
        const LIFF_ID = "{{ liff_id }}";
        const API_BASE = "{{ app_base_url }}";
        const params = new URLSearchParams(window.location.search);
        const checkinType = params.get("type") || "clock_in";
        const typeLabel = checkinType === "clock_in" ? "上班打卡" : "下班打卡";

        const statusEl = document.getElementById("status");
        const btn = document.getElementById("checkin-btn");

        async function init() {
            try {
                await liff.init({ liffId: LIFF_ID });
                if (!liff.isLoggedIn()) { liff.login(); return; }
                statusEl.textContent = "準備" + typeLabel;
                btn.textContent = typeLabel;
                btn.style.display = "block";
            } catch (e) {
                statusEl.textContent = "初始化失敗，請關閉後重試";
                statusEl.className = "error";
            }
        }

        btn.addEventListener("click", () => {
            statusEl.textContent = "取得位置中...";
            btn.disabled = true;

            navigator.geolocation.getCurrentPosition(
                async (pos) => {
                    try {
                        const resp = await fetch(API_BASE + "/liff/checkin", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({
                                id_token: liff.getIDToken(),
                                type: checkinType,
                                latitude: pos.coords.latitude,
                                longitude: pos.coords.longitude,
                            }),
                        });
                        const data = await resp.json();
                        if (resp.ok) {
                            statusEl.textContent = "✓ " + data.message;
                            statusEl.className = "success";
                        } else {
                            statusEl.textContent = "✗ " + data.detail;
                            statusEl.className = "error";
                            btn.disabled = false;
                        }
                    } catch (e) {
                        statusEl.textContent = "網路錯誤，請重試";
                        statusEl.className = "error";
                        btn.disabled = false;
                    }
                },
                () => {
                    statusEl.textContent = "需要開啟定位權限才能打卡";
                    statusEl.className = "error";
                    btn.disabled = false;
                },
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
            );
        });

        init();
    </script>
</body>
</html>
```

- [ ] **Step 5: Replace `app/routers/liff.py`**

```python
import zoneinfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services import checkin_service
from app.services.line_service import verify_liff_id_token

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


class CheckinPayload(BaseModel):
    id_token: str
    type: str
    latitude: float
    longitude: float

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ("clock_in", "clock_out"):
            raise ValueError("type must be clock_in or clock_out")
        return v


@router.get("/liff/checkin")
async def liff_checkin_page(request: Request):
    return templates.TemplateResponse(
        "liff/checkin.html",
        {"request": request, "liff_id": settings.liff_id, "app_base_url": settings.app_base_url},
    )


@router.post("/liff/checkin")
async def liff_checkin(
    payload: CheckinPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    line_user_id = await verify_liff_id_token(payload.id_token)

    employee = checkin_service.get_employee_by_line_id(db, line_user_id)
    if not employee:
        raise HTTPException(status_code=400, detail="請先完成 LINE 帳號綁定")

    if checkin_service.has_recent_checkin(db, employee.id, payload.type):
        raise HTTPException(status_code=400, detail="2 小時內已打過相同類型的卡")

    ip_address = (
        request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()
    )

    checkin = checkin_service.create_checkin(
        db, employee.id, payload.type, payload.latitude, payload.longitude, ip_address
    )

    tz = zoneinfo.ZoneInfo(settings.timezone)
    local_time = checkin.checked_at.astimezone(tz)
    type_label = "上班" if payload.type == "clock_in" else "下班"
    return {"message": f"{type_label}打卡成功 {local_time.strftime('%H:%M')}"}
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/test_checkin.py -v
```

Expected:
```
PASSED tests/test_checkin.py::test_liff_checkin_success
PASSED tests/test_checkin.py::test_liff_checkin_rejects_unbound_user
PASSED tests/test_checkin.py::test_liff_checkin_rejects_duplicate_within_2_hours
PASSED tests/test_checkin.py::test_liff_checkin_rejects_invalid_type
```

- [ ] **Step 7: Commit**

```bash
git add app/services/checkin_service.py app/routers/liff.py app/templates/liff/ tests/test_checkin.py
git commit -m "feat: LIFF check-in page and API with geolocation + duplicate guard"
```

---

## Task 7: Manager Auth + Dashboard Login

**Files:**
- Create: `app/services/auth_service.py`
- Modify: `app/routers/dashboard.py`
- Create: `app/templates/dashboard/login.html`
- Create: `tests/test_auth.py`
- Create: `scripts/create_manager.py`

- [ ] **Step 1: Write failing tests — `tests/test_auth.py`**

```python
from app.models.manager import Manager
from app.services.auth_service import hash_password


def _create_manager(db, username="admin", password="secret123"):
    manager = Manager(username=username, password_hash=hash_password(password))
    db.add(manager)
    db.commit()
    return manager


def test_manager_login_success(client, db):
    _create_manager(db)
    resp = client.post("/dashboard/login", data={"username": "admin", "password": "secret123"},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/dashboard"


def test_manager_login_wrong_password(client, db):
    _create_manager(db)
    resp = client.post("/dashboard/login", data={"username": "admin", "password": "wrong"})
    assert resp.status_code == 200
    assert "錯誤" in resp.text


def test_dashboard_redirects_unauthenticated(client, db):
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert "/dashboard/login" in resp.headers["location"]


def test_logout_clears_session(client, db):
    _create_manager(db)
    client.post("/dashboard/login", data={"username": "admin", "password": "secret123"})
    resp = client.get("/dashboard/logout", follow_redirects=False)
    assert resp.status_code == 302
    # After logout, dashboard should redirect to login
    follow = client.get("/dashboard", follow_redirects=False)
    assert follow.status_code == 302
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_auth.py -v
```

Expected: `404` — dashboard routes not implemented.

- [ ] **Step 3: Create `app/services/auth_service.py`**

```python
import bcrypt
from sqlalchemy.orm import Session

from app.models.manager import Manager


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def authenticate_manager(db: Session, username: str, password: str) -> Manager | None:
    manager = db.query(Manager).filter_by(username=username).first()
    if not manager or not verify_password(password, manager.password_hash):
        return None
    return manager
```

- [ ] **Step 4: Create `app/templates/dashboard/login.html`**

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>主管登入</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; background: #f0f4f8; margin: 0; }
        .card { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,.1); width: 320px; }
        h1 { text-align: center; margin: 0 0 1.5rem; font-size: 1.3rem; color: #333; }
        label { display: block; margin-bottom: .3rem; font-size: .9rem; color: #555; }
        input { width: 100%; padding: .6rem; border: 1px solid #ddd; border-radius: 4px; margin-bottom: 1rem; font-size: 1rem; }
        button { width: 100%; padding: .7rem; background: #06c755; color: white; border: none; border-radius: 4px; font-size: 1rem; cursor: pointer; }
        .error { color: #e53e3e; font-size: .9rem; margin-bottom: 1rem; }
    </style>
</head>
<body>
    <div class="card">
        <h1>出勤管理後台</h1>
        {% if error %}<p class="error">{{ error }}</p>{% endif %}
        <form method="POST" action="/dashboard/login">
            <label>帳號</label>
            <input type="text" name="username" required autofocus>
            <label>密碼</label>
            <input type="password" name="password" required>
            <button type="submit">登入</button>
        </form>
    </div>
</body>
</html>
```

- [ ] **Step 5: Replace `app/routers/dashboard.py`** (login/logout + auth guard only for now)

```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.manager import Manager
from app.services.auth_service import authenticate_manager

router = APIRouter(prefix="/dashboard")
templates = Jinja2Templates(directory="app/templates")


def require_manager(request: Request, db: Session = Depends(get_db)) -> Manager:
    manager_id = request.session.get("manager_id")
    if not manager_id:
        # Raise a redirect — caller handles it
        raise _Unauthenticated()
    manager = db.query(Manager).filter_by(id=manager_id).first()
    if not manager:
        raise _Unauthenticated()
    return manager


class _Unauthenticated(Exception):
    pass


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("dashboard/login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    manager = authenticate_manager(db, username, password)
    if not manager:
        return templates.TemplateResponse(
            "dashboard/login.html",
            {"request": request, "error": "帳號或密碼錯誤"},
        )
    request.session["manager_id"] = manager.id
    return RedirectResponse("/dashboard", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/dashboard/login", status_code=302)


@router.get("")
async def dashboard_index(request: Request, db: Session = Depends(get_db)):
    try:
        require_manager(request, db)
    except _Unauthenticated:
        return RedirectResponse("/dashboard/login", status_code=302)
    return templates.TemplateResponse("dashboard/index.html", {"request": request, "records": [], "employees": []})
```

- [ ] **Step 6: Create placeholder `app/templates/dashboard/index.html`** (minimal, expanded in Task 8)

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head><meta charset="UTF-8"><title>出勤管理後台</title></head>
<body>
    <h1>出勤管理後台</h1>
    <a href="/dashboard/logout">登出</a>
    <p>紀錄載入中...</p>
</body>
</html>
```

- [ ] **Step 7: Create `scripts/create_manager.py`**

```python
#!/usr/bin/env python3
"""Usage: python scripts/create_manager.py <username> <password>"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app.models.manager import Manager
from app.services.auth_service import hash_password


def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/create_manager.py <username> <password>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]
    db = SessionLocal()
    try:
        if db.query(Manager).filter_by(username=username).first():
            print(f"Error: username '{username}' already exists")
            sys.exit(1)
        db.add(Manager(username=username, password_hash=hash_password(password)))
        db.commit()
        print(f"Manager '{username}' created successfully")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
pytest tests/test_auth.py -v
```

Expected:
```
PASSED tests/test_auth.py::test_manager_login_success
PASSED tests/test_auth.py::test_manager_login_wrong_password
PASSED tests/test_auth.py::test_dashboard_redirects_unauthenticated
PASSED tests/test_auth.py::test_logout_clears_session
```

- [ ] **Step 9: Commit**

```bash
git add app/services/auth_service.py app/routers/dashboard.py app/templates/dashboard/ scripts/ tests/test_auth.py
git commit -m "feat: manager auth (bcrypt + session), dashboard login/logout, create_manager script"
```

---

## Task 8: Dashboard Records View + CSV Export

**Files:**
- Modify: `app/routers/dashboard.py`
- Modify: `app/templates/dashboard/index.html`

- [ ] **Step 1: Replace `app/templates/dashboard/index.html`** with full template

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>出勤管理後台</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, sans-serif; margin: 0; padding: 1rem; background: #f0f4f8; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
        h1 { font-size: 1.3rem; color: #333; margin: 0; }
        .logout { color: #666; font-size: .9rem; text-decoration: none; }
        .filters { background: white; padding: 1rem; border-radius: 8px; margin-bottom: 1rem; display: flex; gap: 1rem; flex-wrap: wrap; align-items: flex-end; }
        .filters > div { display: flex; flex-direction: column; }
        .filters label { font-size: .85rem; color: #555; margin-bottom: .2rem; }
        .filters select, .filters input[type=date] { padding: .4rem; border: 1px solid #ddd; border-radius: 4px; }
        .btn { padding: .45rem 1rem; border: none; border-radius: 4px; cursor: pointer; font-size: .9rem; text-decoration: none; display: inline-block; }
        .btn-green { background: #06c755; color: white; }
        .btn-blue { background: #3182ce; color: white; }
        table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; font-size: .9rem; }
        th { background: #e2e8f0; padding: .6rem 1rem; text-align: left; color: #555; font-size: .85rem; }
        td { padding: .6rem 1rem; border-top: 1px solid #f0f4f8; }
        .badge { padding: .15rem .45rem; border-radius: 4px; font-size: .8rem; }
        .badge-in { background: #c6f6d5; color: #276749; }
        .badge-out { background: #fed7d7; color: #9b2c2c; }
        .empty { text-align: center; color: #aaa; padding: 2rem; }
    </style>
</head>
<body>
    <div class="header">
        <h1>出勤記錄管理</h1>
        <a href="/dashboard/logout" class="logout">登出</a>
    </div>

    <form class="filters" method="GET" action="/dashboard">
        <div>
            <label>員工</label>
            <select name="employee_id">
                <option value="">全部員工</option>
                {% for emp in employees %}
                <option value="{{ emp.id }}" {% if selected_employee_id == emp.id %}selected{% endif %}>{{ emp.display_name }}</option>
                {% endfor %}
            </select>
        </div>
        <div>
            <label>起始日期</label>
            <input type="date" name="date_from" value="{{ date_from or '' }}">
        </div>
        <div>
            <label>結束日期</label>
            <input type="date" name="date_to" value="{{ date_to or '' }}">
        </div>
        <button type="submit" class="btn btn-green">篩選</button>
        <a class="btn btn-blue"
           href="/dashboard/export?employee_id={{ selected_employee_id or '' }}&date_from={{ date_from or '' }}&date_to={{ date_to or '' }}">
           匯出 CSV
        </a>
    </form>

    <table>
        <thead>
            <tr>
                <th>員工姓名</th><th>Email</th><th>類型</th>
                <th>打卡時間 (UTC+8)</th><th>緯度</th><th>經度</th><th>IP 位址</th>
            </tr>
        </thead>
        <tbody>
            {% for checkin, employee in records %}
            <tr>
                <td>{{ employee.display_name }}</td>
                <td>{{ employee.email }}</td>
                <td>
                    {% if checkin.type == 'clock_in' %}
                    <span class="badge badge-in">上班</span>
                    {% else %}
                    <span class="badge badge-out">下班</span>
                    {% endif %}
                </td>
                <td>{{ checkin.checked_at.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S') }}</td>
                <td>{{ "%.6f" | format(checkin.latitude) }}</td>
                <td>{{ "%.6f" | format(checkin.longitude) }}</td>
                <td>{{ checkin.ip_address }}</td>
            </tr>
            {% else %}
            <tr><td colspan="7" class="empty">無符合條件的紀錄</td></tr>
            {% endfor %}
        </tbody>
    </table>
</body>
</html>
```

- [ ] **Step 2: Replace `dashboard_index` and add `export_csv` in `app/routers/dashboard.py`**

Add imports at top of the file (after existing imports):
```python
import csv
import io
import zoneinfo
from datetime import datetime

from fastapi.responses import StreamingResponse

from app.config import settings
from app.models.check_in import CheckIn
from app.models.employee import Employee
```

Replace the existing `dashboard_index` route and add `export_csv`:
```python
@router.get("")
async def dashboard_index(
    request: Request,
    employee_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
):
    try:
        require_manager(request, db)
    except _Unauthenticated:
        return RedirectResponse("/dashboard/login", status_code=302)

    tz = zoneinfo.ZoneInfo(settings.timezone)
    employees = db.query(Employee).filter_by(is_active=True).order_by(Employee.display_name).all()
    query = db.query(CheckIn, Employee).join(Employee)

    if employee_id:
        query = query.filter(CheckIn.employee_id == employee_id)
    if date_from:
        query = query.filter(CheckIn.checked_at >= datetime.fromisoformat(date_from).replace(tzinfo=tz))
    if date_to:
        dt_to = datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59, tzinfo=tz)
        query = query.filter(CheckIn.checked_at <= dt_to)

    records = query.order_by(CheckIn.checked_at.desc()).limit(500).all()

    return templates.TemplateResponse(
        "dashboard/index.html",
        {
            "request": request,
            "employees": employees,
            "records": records,
            "selected_employee_id": employee_id,
            "date_from": date_from,
            "date_to": date_to,
            "tz": tz,
        },
    )


@router.get("/export")
async def export_csv(
    request: Request,
    employee_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
):
    try:
        require_manager(request, db)
    except _Unauthenticated:
        return RedirectResponse("/dashboard/login", status_code=302)

    tz = zoneinfo.ZoneInfo(settings.timezone)
    query = db.query(CheckIn, Employee).join(Employee)

    if employee_id:
        query = query.filter(CheckIn.employee_id == employee_id)
    if date_from:
        query = query.filter(CheckIn.checked_at >= datetime.fromisoformat(date_from).replace(tzinfo=tz))
    if date_to:
        dt_to = datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59, tzinfo=tz)
        query = query.filter(CheckIn.checked_at <= dt_to)

    records = query.order_by(CheckIn.checked_at).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["員工姓名", "Email", "類型", "打卡時間", "緯度", "經度", "IP 位址"])
    for checkin, employee in records:
        local_time = checkin.checked_at.astimezone(tz)
        writer.writerow([
            employee.display_name,
            employee.email,
            "上班" if checkin.type == "clock_in" else "下班",
            local_time.strftime("%Y-%m-%d %H:%M:%S"),
            checkin.latitude,
            checkin.longitude,
            checkin.ip_address,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),  # utf-8-sig for Excel compatibility
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=attendance.csv"},
    )
```

- [ ] **Step 3: Run full test suite to confirm nothing broken**

```bash
pytest -v
```

Expected: all existing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add app/routers/dashboard.py app/templates/dashboard/index.html
git commit -m "feat: dashboard records view with filter and CSV export"
```

---

## Task 9: Manager LINE Query Command + Report Service

**Files:**
- Create: `app/services/report_service.py`
- Create: `tests/test_report.py`
  (webhook.py already calls `handle_manager_query` → `generate_monthly_report` from Task 5)

- [ ] **Step 1: Write failing tests — `tests/test_report.py`**

```python
import zoneinfo
from datetime import datetime

from app.models.check_in import CheckIn
from app.models.employee import Employee
from app.services.report_service import generate_monthly_report


def test_monthly_report_shows_attendance_days(db):
    tz = zoneinfo.ZoneInfo("Asia/Taipei")
    employee = Employee(line_user_id="U001", email="john@company.com", display_name="John")
    db.add(employee)
    db.commit()

    for day in [1, 3, 5]:  # 3 different days
        db.add(CheckIn(
            employee_id=employee.id,
            type="clock_in",
            latitude=25.0,
            longitude=121.0,
            ip_address="1.2.3.4",
            checked_at=datetime(2026, 4, day, 9, 0, 0, tzinfo=tz),
        ))
    db.commit()

    report = generate_monthly_report(db, 2026, 4)
    assert "John" in report
    assert "3 天" in report


def test_monthly_report_shows_no_attendance_for_empty_month(db):
    employee = Employee(line_user_id="U001", email="john@company.com", display_name="John")
    db.add(employee)
    db.commit()

    report = generate_monthly_report(db, 2026, 4)
    assert "John" in report
    assert "無出勤" in report


def test_monthly_report_includes_all_employees(db):
    tz = zoneinfo.ZoneInfo("Asia/Taipei")
    for i, name in enumerate(["Alice", "Bob"]):
        emp = Employee(line_user_id=f"U00{i}", email=f"{name.lower()}@co.com", display_name=name)
        db.add(emp)
    db.commit()

    report = generate_monthly_report(db, 2026, 4)
    assert "Alice" in report
    assert "Bob" in report
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_report.py -v
```

Expected: `ImportError` — report_service not yet created.

- [ ] **Step 3: Create `app/services/report_service.py`**

```python
import zoneinfo
from calendar import monthrange
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.models.check_in import CheckIn
from app.models.employee import Employee


def generate_monthly_report(db: Session, year: int, month: int) -> str:
    tz = zoneinfo.ZoneInfo(settings.timezone)
    _, last_day = monthrange(year, month)
    month_start = datetime(year, month, 1, tzinfo=tz)
    month_end = datetime(year, month, last_day, 23, 59, 59, tzinfo=tz)

    employees = (
        db.query(Employee).filter_by(is_active=True).order_by(Employee.display_name).all()
    )
    lines = [f"📊 {year}/{month:02d} 出勤摘要"]

    for employee in employees:
        clock_ins = (
            db.query(CheckIn)
            .filter(
                CheckIn.employee_id == employee.id,
                CheckIn.type == "clock_in",
                CheckIn.checked_at >= month_start,
                CheckIn.checked_at <= month_end,
            )
            .order_by(CheckIn.checked_at)
            .all()
        )

        if clock_ins:
            days = len({c.checked_at.astimezone(tz).date() for c in clock_ins})
            first = clock_ins[0].checked_at.astimezone(tz).strftime("%m/%d %H:%M")
            last = clock_ins[-1].checked_at.astimezone(tz).strftime("%m/%d %H:%M")
            lines.append(f"\n👤 {employee.display_name}\n   出勤 {days} 天｜首次 {first}｜最後 {last}")
        else:
            lines.append(f"\n👤 {employee.display_name}\n   本月無出勤紀錄")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_report.py -v
```

Expected:
```
PASSED tests/test_report.py::test_monthly_report_shows_attendance_days
PASSED tests/test_report.py::test_monthly_report_shows_no_attendance_for_empty_month
PASSED tests/test_report.py::test_monthly_report_includes_all_employees
```

- [ ] **Step 5: Run full test suite**

```bash
pytest -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/report_service.py tests/test_report.py
git commit -m "feat: monthly attendance report service for manager LINE query"
```

---

## Task 10: Deployment Config + Final Push

**Files:**
- Create: `Procfile`
- Modify: `README.md`

- [ ] **Step 1: Create `Procfile`**

```
web: alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

> This runs migration before starting the server on every Railway deploy — safe for idempotent Alembic migrations.

- [ ] **Step 2: Update `README.md`**

```markdown
# LINE Clockio BD

LINE 打卡系統後端。員工透過 LINE LIFF 打卡（強制 Geolocation + IP），主管可透過 Web Dashboard 或 LINE 指令查詢出勤紀錄。

## 快速開始

### 環境需求
- Python 3.11+
- PostgreSQL

### 本地開發

```bash
cp .env.example .env    # 填入環境變數
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

### 建立第一個主管帳號

```bash
python scripts/create_manager.py admin your_password
```

### 讓主管可以用 LINE 查詢

綁定主管的 LINE 後，在 PostgreSQL 執行：
```sql
UPDATE employees SET is_manager = true WHERE email = 'manager@company.com';
```

### 部署至 Railway

1. 建立 Railway 專案，新增 PostgreSQL plugin
2. 設定環境變數（參考 `.env.example`）
3. Push to GitHub → Railway 自動部署

## 文件

- 設計規格：`docs/superpowers/specs/2026-04-13-line-attendance-design.md`
- 實作計畫：`docs/superpowers/plans/2026-04-13-line-attendance-plan.md`
```

- [ ] **Step 3: Run full test suite one final time**

```bash
pytest -v
```

Expected: all tests pass, zero failures.

- [ ] **Step 4: Final commit + push**

```bash
git add Procfile README.md
git commit -m "feat: deployment config (Procfile) and updated README"
git push origin main
```

---

## Self-Review: Spec Coverage Check

| Spec requirement | Task |
|-----------------|------|
| 員工 email 綁定（OTP 驗證）| Task 5 |
| LIFF 打卡（強制 Geolocation）| Task 6 |
| IP 記錄（X-Forwarded-For）| Task 6 |
| 重複打卡防護（2 小時）| Task 6 |
| 主管 Web Dashboard 登入 | Task 7 |
| 出勤紀錄篩選（員工 / 日期）| Task 8 |
| CSV 匯出 | Task 8 |
| 主管 LINE 月份查詢 | Task 9 |
| LINE Webhook signature 驗證 | Task 4 |
| Alembic migration（TIMESTAMPTZ）| Task 3 |
| Railway 部署 | Task 10 |
| 主管帳號建立 script | Task 7 |
