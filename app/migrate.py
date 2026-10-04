"""Applies the Alembic migrations in `migrations/` (run at startup and by `alembic upgrade head`)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def upgrade_database() -> None:
    command.upgrade(Config(str(ALEMBIC_INI)), "head")
