"""FastAPI application: wiring of routers, startup tasks and error handling."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine, healthcheck
from app.migrate import upgrade_database
from app.routers import admin, corrections, documents, pages, questions, reports, simulations, study, tags
from app.security import admin_auth_enabled
from app.services.corrections import mark_orphan_running_as_interrupted
from app.services.errors import ServiceError
from app.services.tagging import seed_tags_and_presets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("examable")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    upgrade_database()
    with engine.begin() as conn:
        seed_tags_and_presets(conn)
    # Jobs run in-process, so queued/running rows can only be left over from a
    # previous process: mark them interrupted so new jobs can start.
    mark_orphan_running_as_interrupted()
    if not admin_auth_enabled():
        logger.warning("ADMIN_TOKEN is not set: upload, processing, AI and /admin endpoints are unprotected")
    yield


app = FastAPI(
    title="Examable API",
    version="1.0.0",
    description="Turns past-exam PDFs into a deduplicated question bank for practice and spaced repetition.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

for module in (pages, documents, questions, tags, study, simulations, corrections, reports, admin):
    app.include_router(module.router)


@app.exception_handler(ServiceError)
async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/health", tags=["meta"])
def get_health() -> JSONResponse:
    try:
        healthcheck()
    except SQLAlchemyError:
        logger.exception("Health check failed")
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return JSONResponse(content={"status": "ok"})
