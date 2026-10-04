from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import text

from app.database import engine
from app.services.errors import NotFoundError

router = APIRouter(prefix="/reports", tags=["reports"])

# Written by scripts/simulate_archives.py (offline parser benchmark over exam archives).
SIMULATION_REPORT = Path("simulation_report.json")


@router.get("/simulation")
def get_simulation_report() -> dict:
    if not SIMULATION_REPORT.is_file():
        raise NotFoundError("simulation_report.json not found")
    return json.loads(SIMULATION_REPORT.read_text(encoding="utf-8"))


@router.get("/question-occurrences")
def get_question_occurrence_report(limit: int = 1000) -> dict:
    """Questions ranked by how many exam sessions they appeared in, with provenance."""
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  q.id, q.stem, q.question_type, q.section, q.number_in_section,
                  q.occurrences_count, q.source_files_json, q.confidence,
                  COALESCE(
                    (
                      SELECT jsonb_agg(
                        jsonb_build_object(
                          'source_file_name', o.source_file_name,
                          'source_section', o.source_section,
                          'source_number', o.source_number,
                          'document_id', o.document_id
                        )
                        ORDER BY o.source_file_name, o.source_section, o.source_number
                      )
                      FROM question_occurrences o
                      WHERE o.question_id = q.id
                    ),
                    '[]'::jsonb
                  ) AS occurrences
                FROM questions q
                WHERE q.is_discarded = false
                ORDER BY q.occurrences_count DESC, q.section, q.number_in_section
                LIMIT :limit
                """
            ),
            {"limit": max(1, min(limit, 5000))},
        ).mappings()
        items = [
            {
                "question_id": row["id"],
                "question_type": row["question_type"],
                "section": row["section"],
                "number_in_section": row["number_in_section"],
                "confidence": float(row["confidence"]),
                "occurrences_count": int(row["occurrences_count"] or 1),
                "source_files": row["source_files_json"] or [],
                "occurrences": row["occurrences"],
                "stem_preview": (row["stem"] or "")[:220],
            }
            for row in rows
        ]
    return {"total_questions": len(items), "items": items}
