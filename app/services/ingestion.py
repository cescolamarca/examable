"""Document ingestion: storing uploads and turning a PDF into questions in the bank."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.schemas import ParseResponse, QuestionOut, UploadResponse
from app.services.dedupe import detach_document_questions, run_cleanup_dedupe
from app.services.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PayloadTooLargeError,
    UnprocessableDocumentError,
)
from app.services.extraction import extract_text_pages_with_fallback
from app.services.multimodal import enhance_with_multimodal
from app.services.parser import parse_unisa_questions
from app.services.tagging import auto_tag_document, ensure_module_2_preset

logger = logging.getLogger(__name__)

# The PDF header must appear within the first 1024 bytes (ISO 32000-1, 7.5.2).
PDF_MAGIC = b"%PDF-"


def uploads_root() -> Path:
    root = Path(settings.upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def store_upload(filename: str, stream: BinaryIO) -> UploadResponse:
    """Validate and store an uploaded PDF; identical files (same SHA-256) are rejected."""
    if not filename.lower().endswith(".pdf"):
        raise InvalidRequestError("Only PDF files are supported")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise PayloadTooLargeError(f"File exceeds the {settings.max_upload_mb} MB upload limit")
    if PDF_MAGIC not in data[:1024]:
        raise InvalidRequestError("The file is not a valid PDF")

    document_id = uuid4()
    dest = uploads_root() / f"{document_id}.pdf"
    with engine.begin() as conn:
        inserted = conn.execute(
            text(
                """
                INSERT INTO documents (id, title, source_uri, sha256, ingestion_status)
                VALUES (:id, :title, :source_uri, :sha256, 'uploaded')
                ON CONFLICT (sha256) DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": str(document_id),
                "title": Path(filename).name,
                "source_uri": str(dest),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        ).first()
        if inserted is None:
            raise ConflictError("Document already ingested")
        # Written inside the transaction: if the write fails the row is rolled back too.
        dest.write_bytes(data)
    return UploadResponse(document_id=document_id, source_uri=str(dest), status="uploaded")


def _set_failed(document_id: UUID, error: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE documents SET ingestion_status = 'error', ingestion_error = :err WHERE id = :id"),
            {"id": str(document_id), "err": error},
        )


def _save_questions(document_id: UUID, source_title: str, questions: list[QuestionOut], pages: int, warnings: list[str]) -> None:
    with engine.begin() as conn:
        # Re-processing replaces this document's questions (shared ones move to another session).
        detach_document_questions(conn, str(document_id))
        for q in questions:
            conn.execute(
                text(
                    """
                    INSERT INTO questions (
                      id, document_id, section, number_in_section, question_type, stem,
                      options_json, subparts_json, assets_json, solution_json, difficulty,
                      language, page_start, page_end, confidence, needs_review,
                      occurrences_count, source_files_json, schema_version
                    ) VALUES (
                      :id, :document_id, :section, :number_in_section, :question_type, :stem,
                      CAST(:options_json AS JSONB), CAST(:subparts_json AS JSONB), CAST(:assets_json AS JSONB),
                      '{}'::jsonb, :difficulty,
                      :language, :page_start, :page_end, :confidence, :needs_review,
                      1, CAST(:source_files_json AS JSONB), :schema_version
                    )
                    """
                ),
                {
                    "id": str(q.question_id),
                    "document_id": str(document_id),
                    "section": q.section,
                    "number_in_section": q.number_in_section,
                    "question_type": q.question_type,
                    "stem": q.stem,
                    "options_json": json.dumps([o.model_dump() for o in q.options], ensure_ascii=False),
                    "subparts_json": json.dumps([s.model_dump() for s in q.subparts], ensure_ascii=False),
                    "assets_json": json.dumps(q.assets, ensure_ascii=False),
                    "difficulty": q.difficulty,
                    "language": q.language,
                    "page_start": q.source_loc.page_start,
                    "page_end": q.source_loc.page_end,
                    "confidence": q.quality.confidence,
                    "needs_review": q.quality.needs_review,
                    "source_files_json": json.dumps([source_title], ensure_ascii=False),
                    "schema_version": q.schema_version,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO question_occurrences
                      (question_id, document_id, source_file_name, source_section, source_number)
                    VALUES (:question_id, :document_id, :source_file_name, :source_section, :source_number)
                    ON CONFLICT (question_id, document_id, source_section, source_number) DO NOTHING
                    """
                ),
                {
                    "question_id": str(q.question_id),
                    "document_id": str(document_id),
                    "source_file_name": source_title,
                    "source_section": q.section,
                    "source_number": q.number_in_section,
                },
            )
        conn.execute(
            text(
                """
                UPDATE documents
                SET ingestion_status = 'processed', pages = :pages,
                    ingestion_error = :warnings, processed_at = now()
                WHERE id = :id
                """
            ),
            {"id": str(document_id), "pages": pages, "warnings": "; ".join(warnings) or None},
        )
        auto_tag_document(conn, str(document_id), use_ai=False)
        ensure_module_2_preset(conn)


def process_document(document_id: UUID) -> ParseResponse:
    """Run the full pipeline: extract text, parse, optional LLM repair, persist, tag, deduplicate."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                UPDATE documents SET ingestion_status = 'processing'
                WHERE id = :id
                RETURNING source_uri, title
                """
            ),
            {"id": str(document_id)},
        ).first()
    if not row:
        raise NotFoundError("Document not found")

    path = Path(row.source_uri)
    if not path.is_file():
        _set_failed(document_id, "Source file missing")
        raise NotFoundError("Source file missing")

    try:
        extraction = extract_text_pages_with_fallback(path)
        questions = parse_unisa_questions(document_id=document_id, pages=extraction.pages)
        multimodal = enhance_with_multimodal(
            pdf_path=path,
            document_id=document_id,
            pages=extraction.pages,
            questions=questions,
            extraction_quality=extraction.quality_score,
        )
        questions = multimodal.questions
    except Exception as exc:
        logger.exception("Parsing failed for document %s", document_id)
        message = str(exc) or type(exc).__name__
        _set_failed(document_id, message)
        raise UnprocessableDocumentError(f"Parsing failed: {message}") from exc

    _save_questions(
        document_id,
        row.title,
        questions,
        pages=len(extraction.pages),
        warnings=extraction.warnings + multimodal.warnings,
    )
    run_cleanup_dedupe()

    return ParseResponse(
        document_id=document_id,
        extracted=len(questions),
        extraction_method=extraction.method,
        extraction_quality=extraction.quality_score,
        extraction_warnings=extraction.warnings,
        multimodal_used=multimodal.used,
        multimodal_updates=multimodal.updated_items,
        multimodal_warnings=multimodal.warnings,
    )


def delete_documents(document_ids: list[UUID]) -> dict:
    """Delete documents and their stored PDFs, keeping questions other sessions share."""
    deleted: list[dict] = []
    missing: list[str] = []
    with engine.begin() as conn:
        for document_id in map(str, document_ids):
            row = conn.execute(
                text("SELECT title, source_uri FROM documents WHERE id = :id"), {"id": document_id}
            ).mappings().first()
            if not row:
                missing.append(document_id)
                continue
            removed = detach_document_questions(conn, document_id)
            conn.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})
            Path(str(row["source_uri"])).unlink(missing_ok=True)
            deleted.append({"id": document_id, "title": row["title"], "questions": removed})
    return {"deleted": deleted, "missing": missing}
