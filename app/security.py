"""Shared-secret protection for endpoints that mutate the bank or spend LLM credits."""

from __future__ import annotations

import logging
import secrets

from fastapi import Header, HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)

ADMIN_HEADER = "X-Admin-Token"


def admin_auth_enabled() -> bool:
    return bool(settings.admin_token)


def require_admin(x_admin_token: str | None = Header(default=None, alias=ADMIN_HEADER)) -> None:
    """FastAPI dependency: checks `X-Admin-Token` when ADMIN_TOKEN is configured.

    Without ADMIN_TOKEN the check is skipped (local development); the app logs a
    warning at startup so an unprotected deployment does not go unnoticed.
    """
    if not admin_auth_enabled():
        return
    if not x_admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin token required",
            headers={"WWW-Authenticate": ADMIN_HEADER},
        )
    # Constant-time comparison so the token cannot be guessed byte by byte from timings.
    if not secrets.compare_digest(x_admin_token.encode(), str(settings.admin_token).encode()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin token")
