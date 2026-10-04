"""Bulk import of a pre-built question bank (the JSON produced by `scripts/postprocess_question_bank.py`)."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import Connection, text

from app.database import engine
from app.services.dedupe import detach_document_questions
from app.services.errors import InvalidRequestError
from app.services.tagging import ensure_intercorso_1_preset, manual_tag_slug

SECTIONS = {"quiz", "teoria", "esercizio"}
QUESTION_TYPES = {"multiple_choice", "open_text", "multi_part_open"}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _upsert_document(conn: Connection, *, title: str, source_name: str, sha: str) -> str:
    """Create the bank's document, or reset it when the same file is imported again."""
    existing = conn.execute(text("SELECT id FROM documents WHERE sha256 = :sha"), {"sha": sha}).first()
    if existing:
        document_id = str(existing.id)
        detach_document_questions(conn, document_id)
        conn.execute(
            text(
                """
                UPDATE documents
                SET title = :title, source_uri = :source_uri, ingestion_status = 'processed',
                    ingestion_error = NULL, processed_at = now()
                WHERE id = :id
                """
            ),
            {"id": document_id, "title": title, "source_uri": source_name},
        )
        return document_id

    document_id = str(uuid4())
    conn.execute(
        text(
            """
            INSERT INTO documents (id, title, source_uri, sha256, ingestion_status, processed_at)
            VALUES (:id, :title, :source_uri, :sha256, 'processed', now())
            """
        ),
        {"id": document_id, "title": title, "source_uri": source_name, "sha256": sha},
    )
    return document_id


def _insert_tags(conn: Connection, question_id: str, raw_tags: list) -> int:
    inserted = 0
    for raw_tag in raw_tags:
        name = str(raw_tag).strip()
        slug = manual_tag_slug(name)
        if not slug:
            continue
        tag_id = conn.execute(
            text(
                """
                INSERT INTO tags (name, slug) VALUES (:name, :slug)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """
            ),
            {"name": name, "slug": slug},
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO question_tags (question_id, tag_id, score, source)
                VALUES (:question_id, :tag_id, 1.0, 'manual')
                ON CONFLICT (question_id, tag_id) DO UPDATE SET score = 1.0, source = 'manual'
                """
            ),
            {"question_id": question_id, "tag_id": str(tag_id)},
        )
        inserted += 1
    return inserted


def import_question_bank(raw: bytes, *, source_name: str, title: str) -> dict:
    """Load every question of the bank as one synthetic document.

    Question ids are derived from the bank fingerprints (uuid5), so re-importing the
    same bank keeps ids stable.
    """
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRequestError(f"Invalid question bank: {exc}") from exc
    questions = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(questions, list) or not questions:
        raise InvalidRequestError("Invalid question bank: empty questions list")

    section_seq: defaultdict[str, int] = defaultdict(int)
    counts = {"inserted_questions": 0, "inserted_occurrences": 0, "inserted_question_tags": 0}

    with engine.begin() as conn:
        document_id = _upsert_document(
            conn, title=title, source_name=source_name, sha=hashlib.sha256(raw).hexdigest()
        )
        for item in questions:
            if not isinstance(item, dict):
                continue
            stem = str(item.get("stem") or "").strip()
            if not stem:
                continue
            question_type = str(item.get("question_type") or "").strip()
            if question_type not in QUESTION_TYPES:
                question_type = "open_text"
            section = str(item.get("section") or "").strip()
            if section not in SECTIONS:
                section = "esercizio"
            section_seq[section] += 1
            number = section_seq[section]

            fingerprint = str(item.get("fingerprint") or item.get("question_id") or "")
            question_id = str(uuid5(NAMESPACE_URL, fingerprint or f"{section}:{number}:{stem[:50]}"))
            occurrences = [o for o in _list(item.get("occurrences")) if isinstance(o, dict)]
            source_files = sorted(
                {str(o.get("source_file")).strip() for o in occurrences if str(o.get("source_file") or "").strip()}
            ) or [source_name]

            conn.execute(
                text(
                    """
                    INSERT INTO questions (
                      id, document_id, section, number_in_section, question_type, stem,
                      options_json, subparts_json, confidence, occurrences_count,
                      source_files_json, dedupe_fingerprint
                    ) VALUES (
                      :id, :document_id, :section, :number, :question_type, :stem,
                      CAST(:options AS JSONB), CAST(:subparts AS JSONB), :confidence, :occurrences_count,
                      CAST(:source_files AS JSONB), :fingerprint
                    )
                    """
                ),
                {
                    "id": question_id,
                    "document_id": document_id,
                    "section": section,
                    "number": number,
                    "question_type": question_type,
                    "stem": stem,
                    "options": json.dumps(_list(item.get("options")), ensure_ascii=False),
                    "subparts": json.dumps(_list(item.get("subparts")), ensure_ascii=False),
                    "confidence": max(0.0, min(1.0, float(item.get("confidence_max") or 0.9))),
                    "occurrences_count": max(1, int(item.get("occurrences_count") or len(occurrences) or 1)),
                    "source_files": json.dumps(source_files, ensure_ascii=False),
                    "fingerprint": fingerprint[:40] if len(fingerprint) >= 40 else None,
                },
            )
            counts["inserted_questions"] += 1

            for occ in occurrences:
                try:
                    source_number = int(occ.get("number_in_section", number))
                except (TypeError, ValueError):
                    source_number = number
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
                        "question_id": question_id,
                        "document_id": document_id,
                        "source_file_name": str(occ.get("source_file") or "").strip() or source_name,
                        "source_section": str(occ.get("section") or "").strip() or section,
                        "source_number": source_number,
                    },
                )
                counts["inserted_occurrences"] += 1

            counts["inserted_question_tags"] += _insert_tags(conn, question_id, _list(item.get("tags")))

        ensure_intercorso_1_preset(conn)

    return {"status": "ok", "document_id": document_id, "source": source_name, **counts}
