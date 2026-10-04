"""Practice simulations: building question sets, persisting them and reading them back."""

from __future__ import annotations

import json
import random
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.database import engine
from app.schemas import CustomSimulationIn, SimulationFromQuestionsIn
from app.services import filters
from app.services.errors import InvalidRequestError, NotFoundError
from app.services.questions import QUESTION_SELECT, fetch_active_by_ids, question_payload
from app.services.sampling import interleave_by_topic, primary_topic

QUESTION_TYPES = ("multiple_choice", "open_text", "multi_part_open")

# Per-user attempt counters, joined when a priority mode orders the pool.
_ATTEMPTS_JOIN = """
    LEFT JOIN (
      SELECT question_id,
             COUNT(*) AS total_attempts,
             COUNT(*) FILTER (WHERE NOT is_correct) AS wrong_attempts
      FROM attempts
      WHERE user_id = :priority_user_id
      GROUP BY question_id
    ) a ON a.question_id = q.id
"""

_PRIORITY_ORDER = {
    "never_viewed": "ORDER BY (COALESCE(a.total_attempts, 0) = 0) DESC, random()",
    "frequently_mistaken": (
        "ORDER BY (COALESCE(a.wrong_attempts, 0)::float / GREATEST(COALESCE(a.total_attempts, 0), 1)) DESC, "
        "COALESCE(a.wrong_attempts, 0) DESC, random()"
    ),
}


def _pool_filter(payload: CustomSimulationIn) -> filters.WhereBuilder:
    """WHERE clause shared by every simulation mode.

    Documents and tag presets are sources: a question qualifies if it comes from any
    of them (OR). The free-text tag filter and `only_reviewed_correct` then narrow
    that union (AND).
    """
    where = filters.WhereBuilder("q.is_discarded = false")

    document_ids = list(dict.fromkeys(str(d) for d in [*payload.document_ids, payload.document_id] if d))
    presets = list(dict.fromkeys(p.strip() for p in [*payload.tag_presets, payload.tag_preset or ""] if p.strip()))

    sources: list[str] = []
    if document_ids:
        sources.append("q.document_id = ANY(CAST(:source_document_ids AS UUID[]))")
        where.params["source_document_ids"] = document_ids
    preset_fragment = filters.any_tag_preset(presets, param_prefix="source_preset")
    if preset_fragment:
        sources.append(preset_fragment[0])
        where.params.update(preset_fragment[1])
    if sources:
        where.add(" OR ".join(f"({s})" for s in sources))

    where.add_fragment(filters.any_tag(filters.parse_tag_values(payload.tag), param_prefix="tag_value"))

    if payload.only_reviewed_correct:
        if payload.user_id is None:
            raise InvalidRequestError("user_id is required when only_reviewed_correct is true")
        where.add(filters.has_correction(user_param="review_user_id"), review_user_id=str(payload.user_id))
    return where


def _priority(payload: CustomSimulationIn) -> tuple[str, str, dict[str, Any]]:
    """(join, order by, params) implementing `payload.priority_mode`; empty when it is "none"."""
    if payload.priority_mode == "none":
        return "", "", {}
    if payload.user_id is None:
        raise InvalidRequestError("user_id is required when priority_mode is set")
    return _ATTEMPTS_JOIN, _PRIORITY_ORDER[payload.priority_mode], {"priority_user_id": str(payload.user_id)}


def _persist(conn: Connection, payload: CustomSimulationIn, questions: list[dict], requested_total: int) -> str | None:
    """Store the generated set so it can be resumed later; anonymous runs are not stored."""
    if payload.user_id is None:
        return None
    simulation_id = str(uuid4())
    conn.execute(
        text(
            """
            INSERT INTO simulations
              (id, user_id, config_json, question_ids_json, requested_total, generated_total, exhaustive)
            VALUES
              (:id, :user_id, :config_json, :question_ids_json, :requested_total, :generated_total, :exhaustive)
            """
        ),
        {
            "id": simulation_id,
            "user_id": str(payload.user_id),
            "config_json": json.dumps(payload.model_dump(mode="json")),
            "question_ids_json": json.dumps([str(q["id"]) for q in questions]),
            "requested_total": requested_total,
            "generated_total": len(questions),
            "exhaustive": payload.exhaustive,
        },
    )
    return simulation_id


def _extraction_order(question: dict) -> tuple[str, str, int]:
    return str(question["document_id"]), question["section"], question["number_in_section"]


def _by_topic(question: dict) -> str:
    return primary_topic(question["tags"])


def _exhaustive(conn: Connection, payload: CustomSimulationIn, rng: random.Random) -> dict:
    """Every question matching the filters."""
    where = _pool_filter(payload)
    join_sql, order_sql, priority_params = _priority(payload)
    rows = conn.execute(
        text(f"{QUESTION_SELECT} {join_sql} WHERE {where.sql} {order_sql or 'ORDER BY random()'}"),
        {**where.params, **priority_params},
    ).mappings()
    pool = [question_payload(row) for row in rows]

    if payload.priority_mode != "none":
        questions = pool  # priority order wins over topic balancing
    elif payload.randomize:
        questions = interleave_by_topic(pool, topic=_by_topic, rng=rng)
    else:
        questions = sorted(pool, key=_extraction_order)

    return {
        "requested_total": len(questions),
        "generated_total": len(questions),
        "requested_by_type": {},
        "shortage_by_type": {},
        "exhaustive": True,
        "simulation_id": _persist(conn, payload, questions, requested_total=len(questions)),
        "questions": questions,
    }


def _by_type(conn: Connection, payload: CustomSimulationIn, rng: random.Random) -> dict:
    """A fixed number of questions per type, reporting any shortfall."""
    requested = {
        "multiple_choice": payload.multiple_choice_count,
        "open_text": payload.open_text_count,
        "multi_part_open": payload.multi_part_open_count,
    }
    requested_total = sum(requested.values())
    if requested_total <= 0:
        raise InvalidRequestError("Select at least one question")

    base = _pool_filter(payload)
    join_sql, order_sql, priority_params = _priority(payload)

    questions: list[dict] = []
    shortage: dict[str, int] = {}
    for question_type, wanted in requested.items():
        if wanted <= 0:
            continue
        params = {**base.params, **priority_params, "question_type": question_type}
        where_sql = f"q.question_type = :question_type AND {base.sql}"
        if order_sql:
            # Priority modes take the top rows directly, without topic balancing.
            params["pool_limit"] = wanted
            sql = f"{QUESTION_SELECT} {join_sql} WHERE {where_sql} {order_sql} LIMIT :pool_limit"
            selected = [question_payload(r) for r in conn.execute(text(sql), params).mappings()]
        else:
            # Over-fetch a random pool so the round-robin has topics to choose from.
            params["pool_limit"] = max(wanted * 6, 600)
            sql = f"{QUESTION_SELECT} WHERE {where_sql} ORDER BY random() LIMIT :pool_limit"
            pool = [question_payload(r) for r in conn.execute(text(sql), params).mappings()]
            selected = interleave_by_topic(pool, topic=_by_topic, rng=rng, limit=wanted)

        if len(selected) < wanted:
            shortage[question_type] = wanted - len(selected)
        questions.extend(selected)

    if payload.randomize:
        rng.shuffle(questions)
    else:
        questions.sort(key=_extraction_order)

    return {
        "requested_total": requested_total,
        "generated_total": len(questions),
        "requested_by_type": requested,
        "shortage_by_type": shortage,
        "simulation_id": _persist(conn, payload, questions, requested_total=requested_total),
        "questions": questions,
    }


def create_custom(payload: CustomSimulationIn, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    with engine.begin() as conn:
        if payload.exhaustive:
            return _exhaustive(conn, payload, rng)
        return _by_type(conn, payload, rng)


def create_from_questions(payload: SimulationFromQuestionsIn, rng: random.Random | None = None) -> dict:
    """A simulation over explicit questions (e.g. the wrong answers of a previous run).

    Discarded questions are dropped; the requested order is kept unless `randomize`.
    """
    ordered_ids = list(dict.fromkeys(str(qid) for qid in payload.question_ids))
    if not ordered_ids:
        raise InvalidRequestError("No question ids provided")

    with engine.begin() as conn:
        by_id = fetch_active_by_ids(conn, ordered_ids)
        questions = [by_id[qid] for qid in ordered_ids if qid in by_id]
        if not questions:
            raise InvalidRequestError("None of the questions are available")
        if payload.randomize:
            (rng or random.Random()).shuffle(questions)
        config = CustomSimulationIn(user_id=payload.user_id, randomize=payload.randomize)
        simulation_id = _persist(conn, config, questions, requested_total=len(questions))

    return {
        "requested_total": len(ordered_ids),
        "generated_total": len(questions),
        "requested_by_type": {},
        "shortage_by_type": {},
        "simulation_id": simulation_id,
        "questions": questions,
    }


def list_for_user(user_id: UUID, limit: int = 20) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  s.id, s.created_at, s.config_json, s.requested_total, s.generated_total, s.exhaustive,
                  COALESCE(a.answered_count, 0) AS answered_count,
                  COALESCE(a.correct_count, 0) AS correct_count
                FROM simulations s
                LEFT JOIN (
                  SELECT simulation_id,
                         COUNT(*) AS answered_count,
                         COUNT(*) FILTER (WHERE is_correct) AS correct_count
                  FROM attempts
                  WHERE user_id = :user_id AND simulation_id IS NOT NULL
                  GROUP BY simulation_id
                ) a ON a.simulation_id = s.id
                WHERE s.user_id = :user_id
                ORDER BY s.created_at DESC
                LIMIT :limit
                """
            ),
            {"user_id": str(user_id), "limit": max(1, min(limit, 100))},
        ).mappings()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"].isoformat(),
                "requested_total": row["requested_total"],
                "generated_total": row["generated_total"],
                "exhaustive": row["exhaustive"],
                "config": row["config_json"] or {},
                "answered_count": int(row["answered_count"]),
                "correct_count": int(row["correct_count"]),
            }
            for row in rows
        ]


def get(simulation_id: UUID) -> dict:
    """A stored simulation with each question's latest attempt, ready to be resumed."""
    with engine.begin() as conn:
        sim = conn.execute(
            text(
                """
                SELECT id, created_at, config_json, question_ids_json, requested_total, generated_total, exhaustive
                FROM simulations
                WHERE id = :id
                """
            ),
            {"id": str(simulation_id)},
        ).mappings().first()
        if not sim:
            raise NotFoundError("Simulation not found")

        question_ids = [str(q) for q in (sim["question_ids_json"] or [])]
        questions_by_id = fetch_active_by_ids(conn, question_ids)

        attempts: dict[str, dict] = {}
        for row in conn.execute(
            text(
                """
                SELECT question_id, is_correct, answer_payload
                FROM attempts
                WHERE simulation_id = :simulation_id
                ORDER BY answered_at ASC
                """
            ),
            {"simulation_id": str(simulation_id)},
        ).mappings():
            # Later attempts overwrite earlier ones: the latest answer wins.
            attempts[str(row["question_id"])] = {
                "is_correct": row["is_correct"],
                "answer_payload": row["answer_payload"] or {},
            }

    questions = [
        {**questions_by_id[qid], "attempt": attempts.get(qid)} for qid in question_ids if qid in questions_by_id
    ]
    return {
        "id": sim["id"],
        "created_at": sim["created_at"].isoformat(),
        "requested_total": sim["requested_total"],
        "generated_total": sim["generated_total"],
        "exhaustive": sim["exhaustive"],
        "config": sim["config_json"] or {},
        "answered_count": len(attempts),
        "correct_count": sum(1 for a in attempts.values() if a["is_correct"]),
        "questions": questions,
    }


def delete(simulation_id: UUID) -> None:
    with engine.begin() as conn:
        result = conn.execute(text("DELETE FROM simulations WHERE id = :id"), {"id": str(simulation_id)})
    if result.rowcount == 0:
        raise NotFoundError("Simulation not found")
