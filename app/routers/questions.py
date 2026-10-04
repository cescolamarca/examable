from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.database import engine
from app.schemas import (
    QuestionCorrectionRegenerateIn,
    QuestionCorrectionSetIn,
    QuestionReviewSetIn,
    QuestionTagSetIn,
)
from app.security import require_admin
from app.services import corrections
from app.services.errors import InvalidRequestError, NotFoundError
from app.services.questions import QUESTION_SELECT, question_detail
from app.services.tagging import manual_tag_slug

router = APIRouter(prefix="/questions", tags=["questions"])


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _require_question(conn: Any, question_id: UUID) -> None:
    if not conn.execute(text("SELECT 1 FROM questions WHERE id = :id"), {"id": str(question_id)}).first():
        raise NotFoundError("Question not found")


@router.get("/{question_id}")
def get_question(question_id: UUID) -> dict:
    with engine.begin() as conn:
        row = conn.execute(text(QUESTION_SELECT + " WHERE q.id = :id"), {"id": str(question_id)}).mappings().first()
    if not row:
        raise NotFoundError("Question not found")
    return question_detail(row)


@router.post("/{question_id}/discard")
def discard_question(question_id: UUID, discarded: bool = True) -> dict:
    """Hide (or restore) a badly extracted question everywhere."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                UPDATE questions
                SET is_discarded = :discarded,
                    discarded_at = CASE WHEN :discarded THEN now() ELSE NULL END
                WHERE id = :question_id
                RETURNING id, is_discarded, discarded_at
                """
            ),
            {"question_id": str(question_id), "discarded": discarded},
        ).mappings().first()
    if not row:
        raise NotFoundError("Question not found")
    return {
        "question_id": str(row["id"]),
        "is_discarded": bool(row["is_discarded"]),
        "discarded_at": _iso(row["discarded_at"]),
    }


@router.get("/{question_id}/review")
def get_question_review(question_id: UUID, user_id: UUID) -> dict:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT status, first_seen_at, reviewed_at
                FROM question_reviews
                WHERE question_id = :question_id AND user_id = :user_id
                """
            ),
            {"question_id": str(question_id), "user_id": str(user_id)},
        ).mappings().first()
    return {
        "question_id": str(question_id),
        "user_id": str(user_id),
        "status": row["status"] if row else None,
        "first_seen_at": _iso(row["first_seen_at"]) if row else None,
        "reviewed_at": _iso(row["reviewed_at"]) if row else None,
    }


@router.put("/{question_id}/review")
def set_question_review(question_id: UUID, payload: QuestionReviewSetIn) -> dict:
    with engine.begin() as conn:
        _require_question(conn, question_id)
        row = conn.execute(
            text(
                """
                INSERT INTO question_reviews (user_id, question_id, status)
                VALUES (:user_id, :question_id, :status)
                ON CONFLICT (user_id, question_id)
                DO UPDATE SET status = EXCLUDED.status, reviewed_at = now()
                RETURNING status, first_seen_at, reviewed_at
                """
            ),
            {"user_id": str(payload.user_id), "question_id": str(question_id), "status": payload.status},
        ).mappings().one()
    return {
        "question_id": str(question_id),
        "user_id": str(payload.user_id),
        "status": row["status"],
        "first_seen_at": _iso(row["first_seen_at"]),
        "reviewed_at": _iso(row["reviewed_at"]),
    }


def _correction_payload(question_id: UUID, user_id: UUID, row: Any) -> dict:
    payload = (row["answer_payload"] if row else None) or {}
    explanation = row["explanation_text"] if row else None
    correct_option = row["correct_option_id"] if row else None
    return {
        "question_id": str(question_id),
        "user_id": str(user_id),
        "correct_option_id": correct_option,
        "explanation_text": explanation,
        "answer_payload": payload,
        "first_seen_at": _iso(row["first_seen_at"]) if row else None,
        "reviewed_at": _iso(row["reviewed_at"]) if row else None,
        "has_correction": bool(correct_option or (explanation or "").strip() or payload),
    }


@router.get("/{question_id}/correction")
def get_question_correction(question_id: UUID, user_id: UUID) -> dict:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT correct_option_id, explanation_text, answer_payload, first_seen_at, reviewed_at
                FROM question_corrections
                WHERE question_id = :question_id AND user_id = :user_id
                """
            ),
            {"question_id": str(question_id), "user_id": str(user_id)},
        ).mappings().first()
    return _correction_payload(question_id, user_id, row)


@router.put("/{question_id}/correction")
def set_question_correction(question_id: UUID, payload: QuestionCorrectionSetIn) -> dict:
    """Save the user's correction; fields left empty keep their previous value."""
    explanation = (payload.explanation_text or "").strip() or None
    correct_option_id = (payload.correct_option_id or "").strip() or None
    answer_payload = payload.answer_payload or {}
    if not (correct_option_id or explanation or answer_payload):
        raise InvalidRequestError("Provide a correct option or an explanation")

    with engine.begin() as conn:
        _require_question(conn, question_id)
        row = conn.execute(
            text(
                """
                INSERT INTO question_corrections (user_id, question_id, correct_option_id, explanation_text, answer_payload)
                VALUES (:user_id, :question_id, :correct_option_id, :explanation_text, CAST(:answer_payload AS JSONB))
                ON CONFLICT (user_id, question_id) DO UPDATE SET
                  correct_option_id = COALESCE(EXCLUDED.correct_option_id, question_corrections.correct_option_id),
                  explanation_text = COALESCE(EXCLUDED.explanation_text, question_corrections.explanation_text),
                  answer_payload = CASE
                    WHEN EXCLUDED.answer_payload = '{}'::jsonb THEN question_corrections.answer_payload
                    ELSE EXCLUDED.answer_payload
                  END,
                  reviewed_at = now()
                RETURNING correct_option_id, explanation_text, answer_payload, first_seen_at, reviewed_at
                """
            ),
            {
                "user_id": str(payload.user_id),
                "question_id": str(question_id),
                "correct_option_id": correct_option_id,
                "explanation_text": explanation,
                "answer_payload": json.dumps(answer_payload, ensure_ascii=False),
            },
        ).mappings().one()
    return _correction_payload(question_id, payload.user_id, row)


@router.post("/{question_id}/correction/regenerate", dependencies=[Depends(require_admin)])
async def regenerate_question_correction(question_id: UUID, payload: QuestionCorrectionRegenerateIn) -> dict:
    """Ask the LLM for a fresh correction, overwriting the stored one."""
    return await corrections.regenerate_for_user(user_id=payload.user_id, question_id=question_id)


@router.get("/{question_id}/tags")
def list_question_tags(question_id: UUID) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.id, t.name, t.slug, qt.score, qt.source
                FROM question_tags qt
                JOIN tags t ON t.id = qt.tag_id
                WHERE qt.question_id = :question_id
                ORDER BY qt.score DESC, t.slug
                """
            ),
            {"question_id": str(question_id)},
        ).mappings()
        return [dict(r) for r in rows]


@router.put("/{question_id}/tags")
def set_question_tags(question_id: UUID, payload: QuestionTagSetIn) -> dict:
    """Replace the question's tags with the given names, creating missing tags."""
    names = [str(t).strip() for t in payload.tags if str(t).strip()]
    with engine.begin() as conn:
        _require_question(conn, question_id)
        conn.execute(text("DELETE FROM question_tags WHERE question_id = :id"), {"id": str(question_id)})
        for name in names:
            tag_id = conn.execute(
                text(
                    """
                    INSERT INTO tags (name, slug) VALUES (:name, :slug)
                    ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                    """
                ),
                {"name": name, "slug": manual_tag_slug(name)},
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO question_tags (question_id, tag_id, score, source)
                    VALUES (:question_id, :tag_id, 1.0, 'manual')
                    ON CONFLICT (question_id, tag_id) DO UPDATE SET score = 1.0, source = 'manual'
                    """
                ),
                {"question_id": str(question_id), "tag_id": str(tag_id)},
            )
    return {"status": "ok", "tags_set": len(names)}
