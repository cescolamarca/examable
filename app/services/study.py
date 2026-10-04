"""Study sessions: picking the next question, recording attempts and progress statistics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.database import engine
from app.schemas import AttemptIn, NextQuestionResponse
from app.services import filters
from app.services.errors import NotFoundError
from app.services.scheduler import next_due_after_attempt

DEFAULT_USER_EMAIL = "local@examable.internal"
MAX_EXCLUDED_IDS = 400


def get_or_create_default_user() -> dict[str, Any]:
    """The app runs in single-user mode: every client shares this account."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO users (id, email, full_name, role)
                VALUES (:id, :email, 'Local User', 'student')
                ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email
                RETURNING id, email
                """
            ),
            {"id": str(uuid4()), "email": DEFAULT_USER_EMAIL},
        ).mappings().one()
    return {"id": row["id"], "email": row["email"]}


def record_attempt(payload: AttemptIn) -> None:
    """Store the attempt and reschedule the question for this user."""
    now = datetime.now(tz=timezone.utc)
    user_id, question_id = str(payload.user_id), str(payload.question_id)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO attempts
                  (id, user_id, question_id, answered_at, is_correct, answer_payload, latency_ms, grade, simulation_id)
                VALUES
                  (:id, :user_id, :question_id, :answered_at, :is_correct, CAST(:answer_payload AS JSONB),
                   :latency_ms, :grade, :simulation_id)
                """
            ),
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "question_id": question_id,
                "answered_at": now,
                "is_correct": payload.is_correct,
                "answer_payload": json.dumps(payload.answer_payload, ensure_ascii=False),
                "latency_ms": payload.latency_ms,
                "grade": payload.grade,
                "simulation_id": str(payload.simulation_id) if payload.simulation_id else None,
            },
        )

        state = conn.execute(
            text("SELECT lapses, reps FROM schedule_state WHERE user_id = :user_id AND question_id = :question_id"),
            {"user_id": user_id, "question_id": question_id},
        ).first()
        lapses = state.lapses if state else 0
        reps = state.reps if state else 0

        conn.execute(
            text(
                """
                INSERT INTO schedule_state (user_id, question_id, due_at, lapses, reps, state, last_reviewed_at)
                VALUES (:user_id, :question_id, :due_at, :lapses, :reps, :state, :now)
                ON CONFLICT (user_id, question_id) DO UPDATE SET
                  due_at = EXCLUDED.due_at,
                  lapses = EXCLUDED.lapses,
                  reps = EXCLUDED.reps,
                  state = EXCLUDED.state,
                  last_reviewed_at = EXCLUDED.last_reviewed_at
                """
            ),
            {
                "user_id": user_id,
                "question_id": question_id,
                "due_at": next_due_after_attempt(payload.is_correct, lapses, reps),
                "lapses": lapses + (0 if payload.is_correct else 1),
                "reps": reps + (1 if payload.is_correct else 0),
                "state": "review" if payload.is_correct else "relearning",
                "now": now,
            },
        )


def _parse_uuid_list(raw: str | None, limit: int) -> list[str]:
    ids: list[str] = []
    for part in (raw or "").split(","):
        try:
            ids.append(str(UUID(part.strip())))
        except ValueError:
            continue
    return ids[:limit]


def study_filter(
    *,
    user_id: UUID,
    document_id: UUID | None = None,
    tag: str | None = None,
    tag_preset: str | None = None,
    question_type: str | None = None,
    review_filter: str = "all",
    exclude_ids: list[str] | None = None,
) -> filters.WhereBuilder:
    where = filters.WhereBuilder("q.is_discarded = false")
    where.params["user_id"] = str(user_id)
    if document_id is not None:
        where.add("q.document_id = :document_id", document_id=str(document_id))
    if question_type:
        where.add("q.question_type = :question_type", question_type=question_type.strip())
    if exclude_ids:
        where.add("q.id <> ALL(CAST(:exclude_ids AS UUID[]))", exclude_ids=exclude_ids)
    where.add_fragment(filters.any_tag(filters.parse_tag_values(tag), param_prefix="tag_value"))
    if tag_preset and tag_preset.strip():
        where.add_fragment(filters.any_tag_preset([tag_preset.strip()], param_prefix="tag_preset"))

    review_filter = (review_filter or "all").strip().lower()
    if review_filter == "unreviewed":
        where.add(filters.has_correction(user_param="user_id", negate=True))
    elif review_filter in {"reviewed_correct", "with_correction"}:
        where.add(filters.has_correction(user_param="user_id"))
    return where


def _first_unseen(conn: Connection, where: filters.WhereBuilder, order_sql: str) -> UUID | None:
    row = conn.execute(
        text(
            f"""
            SELECT q.id
            FROM questions q
            LEFT JOIN schedule_state s ON s.question_id = q.id AND s.user_id = :user_id
            WHERE s.question_id IS NULL AND {where.sql}
            {order_sql}
            LIMIT 1
            """
        ),
        where.params,
    ).first()
    if not row:
        return None
    conn.execute(
        text(
            """
            INSERT INTO schedule_state (user_id, question_id, due_at, state)
            VALUES (:user_id, :question_id, now(), 'new')
            ON CONFLICT (user_id, question_id) DO NOTHING
            """
        ),
        {"user_id": where.params["user_id"], "question_id": str(row.id)},
    )
    return row.id


def _earliest_scheduled(conn: Connection, where: filters.WhereBuilder, *, only_due: bool) -> UUID | None:
    due_sql = "AND s.due_at <= now()" if only_due else ""
    row = conn.execute(
        text(
            f"""
            SELECT s.question_id
            FROM schedule_state s
            JOIN questions q ON q.id = s.question_id
            WHERE s.user_id = :user_id {due_sql} AND {where.sql}
            ORDER BY s.due_at ASC, q.needs_review ASC
            LIMIT 1
            """
        ),
        where.params,
    ).first()
    return row.question_id if row else None


def next_question(
    user_id: UUID,
    *,
    document_id: UUID | None = None,
    tag: str | None = None,
    tag_preset: str | None = None,
    question_type: str | None = None,
    exclude_question_id: UUID | None = None,
    exclude_question_ids: str | None = None,
    prefer_new: bool = False,
    shuffle_new: bool = False,
    review_filter: str = "all",
) -> NextQuestionResponse:
    """Pick what to study next: due reviews first, then unseen questions, then the earliest scheduled one.

    With `prefer_new` unseen questions are tried before due reviews.
    """
    exclude_ids = _parse_uuid_list(exclude_question_ids, MAX_EXCLUDED_IDS)
    if exclude_question_id is not None:
        exclude_ids.append(str(exclude_question_id))
    where = study_filter(
        user_id=user_id,
        document_id=document_id,
        tag=tag,
        tag_preset=tag_preset,
        question_type=question_type,
        review_filter=review_filter,
        exclude_ids=exclude_ids,
    )
    new_order = "ORDER BY random()" if shuffle_new else "ORDER BY q.section, q.number_in_section"

    with engine.begin() as conn:
        if prefer_new and (question_id := _first_unseen(conn, where, new_order)):
            return NextQuestionResponse(question_id=question_id, due_reason="new")
        if question_id := _earliest_scheduled(conn, where, only_due=True):
            return NextQuestionResponse(question_id=question_id, due_reason="due")
        if question_id := _first_unseen(conn, where, new_order):
            return NextQuestionResponse(question_id=question_id, due_reason="new")
        if question_id := _earliest_scheduled(conn, where, only_due=False):
            return NextQuestionResponse(question_id=question_id, due_reason="scheduled")
    raise NotFoundError("No questions available")


def attempt_stats(user_id: UUID) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT question_id,
                       COUNT(*) AS total_attempts,
                       COUNT(*) FILTER (WHERE is_correct) AS correct_attempts
                FROM attempts
                WHERE user_id = :user_id
                GROUP BY question_id
                """
            ),
            {"user_id": str(user_id)},
        ).mappings()
        return [
            {
                "question_id": row["question_id"],
                "total_attempts": int(row["total_attempts"]),
                "correct_attempts": int(row["correct_attempts"]),
            }
            for row in rows
        ]


def correction_stats(
    user_id: UUID,
    *,
    document_id: UUID | None = None,
    tag: str | None = None,
    tag_preset: str | None = None,
    question_type: str | None = None,
) -> dict:
    """How much of the (filtered) bank already has a correction for this user."""
    where = study_filter(
        user_id=user_id, document_id=document_id, tag=tag, tag_preset=tag_preset, question_type=question_type
    )
    with engine.begin() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT
                  COUNT(*)::INTEGER AS total,
                  COUNT(*) FILTER (WHERE {filters.HAS_CORRECTION_SQL})::INTEGER AS with_correction,
                  COUNT(*) FILTER (WHERE qc.correct_option_id IS NOT NULL)::INTEGER AS with_correct_option,
                  COUNT(*) FILTER (
                    WHERE NULLIF(BTRIM(qc.explanation_text), '') IS NOT NULL OR qc.answer_payload <> '{{}}'::jsonb
                  )::INTEGER AS with_explanation
                FROM questions q
                LEFT JOIN question_corrections qc ON qc.question_id = q.id AND qc.user_id = :user_id
                WHERE {where.sql}
                """
            ),
            where.params,
        ).mappings().one()
    total = row["total"]
    return {
        "total": total,
        "with_correction": row["with_correction"],
        "without_correction": total - row["with_correction"],
        "with_correct_option": row["with_correct_option"],
        "with_explanation": row["with_explanation"],
        "coverage_ratio": row["with_correction"] / total if total else 0.0,
    }
