from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import settings


engine: Engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)


def healthcheck() -> bool:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True
