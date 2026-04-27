from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.database import Base, engine
import app.models  # noqa: F401 — registers all ORM models with Base
from app.routers import webhook, liff, dashboard

_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="LINE Clockio",
    lifespan=lifespan,
    docs_url="/docs" if _settings.debug else None,
    redoc_url="/redoc" if _settings.debug else None,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.session_secret_key,
    https_only=not _settings.debug,  # Cloud Run terminates TLS at proxy; force https_only in prod
    max_age=8 * 3600,  # 8-hour session expiry
)

app.include_router(webhook.router)
app.include_router(liff.router)
app.include_router(dashboard.router)


@app.get("/health")
def health():
    return {"status": "ok"}
