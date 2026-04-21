from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import get_settings


def _build_engine():
    url = get_settings().database_url
    # SQLite (used in tests) does not support connection-pool size arguments
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(
        url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )


engine = _build_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
