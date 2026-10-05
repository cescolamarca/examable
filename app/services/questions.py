"""Reading questions from the bank in the shape the API returns them."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.database import engine
from app.services import filters

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
        "occurrences_count": int(row["occurrences_count"] or 1),
        "source_files": row["source_files_json"] or [],
    }


def question_detail(row: Mapping[str, Any]) -> dict[str, Any]:
    """Serialise a QUESTION_SELECT row including moderation fields."""
    return {**question_payload(row), "is_discarded": bool(row["is_discarded"])}


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


BANK_SORTS = {
    "frequency": "q.occurrences_count DESC, q.created_at DESC, q.id",
    "recent": "q.created_at DESC, q.section, q.number_in_section, q.id",
    "position": "q.document_id, q.section, q.number_in_section, q.id",
}


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_bank(
    *,
    user_id: UUID | None = None,
    search: str | None = None,
    document_id: UUID | None = None,
    tag: str | None = None,
    tag_preset: str | None = None,
    question_type: str | None = None,
    correction: str | None = None,
    include_discarded: bool = False,
    only_discarded: bool = False,
    sort: str = "frequency",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Page through the whole bank with full-text-ish search and the user's answer key."""
    where = filters.WhereBuilder()
    if only_discarded:
        where.add("q.is_discarded = true")
    elif not include_discarded:
        where.add("q.is_discarded = false")
    if search and search.strip():
        where.add(
            "(q.stem ILIKE :search OR CAST(q.options_json AS TEXT) ILIKE :search "
            "OR CAST(q.subparts_json AS TEXT) ILIKE :search)",
            search=_like_pattern(search.strip()),
        )
    if document_id is not None:
        where.add(filters.appears_in_documents("document_ids"), document_ids=[str(document_id)])
    if question_type:
        where.add("q.question_type = :question_type", question_type=question_type)
    where.add_fragment(filters.any_tag(filters.parse_tag_values(tag), param_prefix="tag_value"))
    if tag_preset:
        where.add_fragment(filters.any_tag_preset([tag_preset], param_prefix="tag_preset"))
    where.params["user_id"] = str(user_id) if user_id else None
    if correction in {"with", "without"}:
        where.add(filters.has_correction(user_param="user_id", negate=correction == "without"))

    order_sql = BANK_SORTS.get(sort, BANK_SORTS["frequency"])
    params = {**where.params, "limit": max(1, min(limit, 200)), "offset": max(0, offset)}
    with engine.begin() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) FROM questions q WHERE {where.sql}"), params).scalar_one()
        rows = conn.execute(
            text(f"{QUESTION_SELECT} WHERE {where.sql} ORDER BY {order_sql} LIMIT :limit OFFSET :offset"), params
        ).mappings()
        items = [question_detail(row) for row in rows]
        corrections = {}
        if user_id and items:
            corrections = {
                str(r["question_id"]): r
                for r in conn.execute(
                    text(
                        """
                        SELECT question_id, correct_option_id, explanation_text, answer_payload
                        FROM question_corrections
                        WHERE user_id = :user_id AND question_id = ANY(CAST(:ids AS UUID[]))
                        """
                    ),
                    {"user_id": str(user_id), "ids": [str(i["id"]) for i in items]},
                ).mappings()
            }
    for item in items:
        row = corrections.get(str(item["id"]))
        explanation = ((row["explanation_text"] if row else None) or "").strip() or None
        option = row["correct_option_id"] if row else None
        item["correction"] = {
            "correct_option_id": option,
            "explanation_text": explanation,
            "has_correction": bool(option or explanation or (row and row["answer_payload"])),
        }
    return {"total": int(total), "items": items}
