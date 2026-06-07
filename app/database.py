from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import settings


def _normalize_database_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and not url.startswith("postgresql+psycopg://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


engine: Engine = create_engine(_normalize_database_url(settings.database_url), future=True, pool_pre_ping=True)


def healthcheck() -> bool:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True
