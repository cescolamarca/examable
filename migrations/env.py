"""Alembic environment: runs migrations on the application's engine.

The schema is written in plain SQL (the app uses SQLAlchemy Core without ORM
models), so there is no metadata to autogenerate from.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import text

from app.database import engine

# Arbitrary constant: serialises migrations when several app instances start at once.
MIGRATION_LOCK_ID = 727_001


def run_migrations_online() -> None:
    with engine.connect() as connection:
        # Session-level lock: it outlives the commit below, which ends the implicit
        # transaction so Alembic can open one transaction per migration.
        connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        connection.commit()
        try:
            context.configure(connection=connection, transaction_per_migration=True)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})
            connection.commit()


if context.is_offline_mode():
    raise SystemExit("Offline SQL generation is not supported; run against a database.")
run_migrations_online()
