"""Maintenance endpoints. Not used by the UI; always behind the admin token."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import text

from app.database import engine
from app.schemas import AdminDeleteDocumentsIn
from app.security import require_admin
from app.services import ingestion
from app.services.dedupe import run_cleanup_dedupe
from app.services.question_bank import import_question_bank
from app.services.tagging import INTERCORSO_1_BANK_TITLE, seed_tags_and_presets

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

DATA_TABLES = [
    "attempts",
    "schedule_state",
    "question_reviews",
    "question_corrections",
    "simulations",
    "correction_jobs",
    "question_tags",
    "question_occurrences",
    "questions",
    "documents",
    "tag_preset_tags",
    "tag_presets",
    "tags",
    "users",
]


@router.post("/reset-db")
def reset_db() -> dict:
    """Delete every row and re-seed the built-in tags and presets."""
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {', '.join(DATA_TABLES)} RESTART IDENTITY CASCADE"))
        seed_tags_and_presets(conn)
    return {"status": "ok", "message": "database reset completed"}


@router.post("/delete-documents")
def delete_documents(payload: AdminDeleteDocumentsIn) -> dict:
    """Delete documents and their PDFs; questions shared with other sessions are kept."""
    return ingestion.delete_documents(payload.document_ids)


@router.post("/cleanup-dedupe")
def cleanup_dedupe() -> dict:
    return run_cleanup_dedupe()


@router.post("/load-question-bank")
def load_question_bank(file: UploadFile = File(...), title: str = Form(INTERCORSO_1_BANK_TITLE)) -> dict:
    """Import a post-processed question bank JSON file as a single synthetic document."""
    return import_question_bank(file.file.read(), source_name=file.filename or "question_bank.json", title=title)
