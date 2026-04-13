from fastapi import FastAPI
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
