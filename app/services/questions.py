"""Reading questions from the bank in the shape the API returns them."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import Connection, text

# Base SELECT for question payloads; tags are aggregated as a sorted list of slugs.
QUESTION_SELECT = """
    SELECT
      q.id, q.document_id, q.section, q.number_in_section, q.question_type, q.stem,
      q.options_json, q.subparts_json, q.solution_json, q.confidence, q.needs_review,
      q.occurrences_count, q.source_files_json, q.is_discarded,
      (
        SELECT COALESCE(jsonb_agg(t.slug ORDER BY t.slug), '[]'::jsonb)
        FROM question_tags qt
        JOIN tags t ON t.id = qt.tag_id
        WHERE qt.question_id = q.id
      ) AS tags_json
    FROM questions q
"""


def question_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Serialise a QUESTION_SELECT row for study and simulation responses."""
    return {
        "id": row["id"],
        "document_id": row["document_id"],
        "section": row["section"],
        "number_in_section": row["number_in_section"],
        "question_type": row["question_type"],
        "stem": row["stem"],
        "options": row["options_json"] or [],
        "subparts": row["subparts_json"] or [],
        "solution": row["solution_json"] or {},
        "confidence": float(row["confidence"]),
        "needs_review": bool(row["needs_review"]),
        "tags": row["tags_json"] or [],
    }


def question_detail(row: Mapping[str, Any]) -> dict[str, Any]:
    """Serialise a QUESTION_SELECT row including provenance and moderation fields."""
    return {
        **question_payload(row),
        "occurrences_count": int(row["occurrences_count"] or 1),
        "source_files": row["source_files_json"] or [],
        "is_discarded": bool(row["is_discarded"]),
    }


def fetch_active_by_ids(conn: Connection, question_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Non-discarded questions keyed by id; ids that are missing or discarded are skipped."""
    ids = list(dict.fromkeys(str(qid) for qid in question_ids))
    if not ids:
        return {}
    rows = conn.execute(
        text(QUESTION_SELECT + " WHERE q.id = ANY(CAST(:ids AS UUID[])) AND q.is_discarded = false"),
        {"ids": ids},
    ).mappings()
    return {str(row["id"]): question_payload(row) for row in rows}
