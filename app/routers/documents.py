from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import text

from app.database import engine
from app.schemas import ParseResponse, UploadResponse
from app.security import require_admin
from app.services import ingestion
from app.services.questions import QUESTION_SELECT, question_detail

router = APIRouter(tags=["documents"])


@router.get("/documents")
def list_documents(limit: int = 100) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  d.id, d.title, d.ingestion_status, d.pages, d.created_at, d.processed_at, d.ingestion_error,
                  (
                    SELECT COUNT(DISTINCT occ.question_id)
                    FROM question_occurrences occ
                    JOIN questions q ON q.id = occ.question_id AND q.is_discarded = false
                    WHERE occ.document_id = d.id
                  ) AS questions_count
                FROM documents d
                ORDER BY d.created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": max(1, min(limit, 500))},
        ).mappings()
        return [dict(row) for row in rows]


@router.post("/documents", response_model=UploadResponse, dependencies=[Depends(require_admin)])
def upload_document(file: UploadFile = File(...)) -> UploadResponse:
    return ingestion.store_upload(file.filename or "", file.file)


@router.post("/documents/{document_id}/process", response_model=ParseResponse, dependencies=[Depends(require_admin)])
def process_document(document_id: UUID) -> ParseResponse:
    return ingestion.process_document(document_id)


@router.get("/documents/{document_id}/questions")
def list_document_questions(
    document_id: UUID, limit: int = 500, question_type: str | None = None, include_discarded: bool = False
) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                QUESTION_SELECT
                + """
                WHERE EXISTS (
                    SELECT 1 FROM question_occurrences occ
                    WHERE occ.question_id = q.id AND occ.document_id = :document_id
                  )
                  AND (CAST(:question_type AS TEXT) IS NULL OR q.question_type = :question_type)
                  AND (:include_discarded OR q.is_discarded = false)
                ORDER BY q.section, q.number_in_section
                LIMIT :limit
                """
            ),
            {
                "document_id": str(document_id),
                "question_type": question_type.strip() if question_type else None,
                "include_discarded": include_discarded,
                "limit": max(1, min(limit, 2000)),
            },
        ).mappings()
        return [question_detail(row) for row in rows]


@router.get("/stats/kpi")
def get_kpis() -> dict:
    with engine.begin() as conn:
        row = (
            conn.execute(
                text(
                    """
                SELECT
                  (SELECT COUNT(*) FROM documents) AS total_documents,
                  (SELECT COUNT(*) FROM documents WHERE ingestion_status = 'processed') AS processed_documents,
                  (SELECT AVG(confidence) FROM questions WHERE is_discarded = false) AS avg_quality
                """
                )
            )
            .mappings()
            .one()
        )
    return {
        "total_documents": int(row["total_documents"]),
        "processed_documents": int(row["processed_documents"]),
        "avg_quality": float(row["avg_quality"]) if row["avg_quality"] is not None else None,
    }
