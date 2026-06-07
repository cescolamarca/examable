from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text

from app.config import settings
from app.database import engine

logger = logging.getLogger(__name__)


_RUNNING: dict[str, asyncio.Task] = {}


def _now_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _model_name() -> str:
    return (settings.correction_gen_model or settings.multimodal_model or "").strip() or "gpt-4.1-mini"


def serialize_job(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "mode": row["mode"],
        "document_id": str(row["document_id"]) if row["document_id"] else None,
        "status": row["status"],
        "total_questions": int(row["total_questions"] or 0),
        "processed_count": int(row["processed_count"] or 0),
        "succeeded_count": int(row["succeeded_count"] or 0),
        "failed_count": int(row["failed_count"] or 0),
        "skipped_count": int(row["skipped_count"] or 0),
        "current_question_id": str(row["current_question_id"]) if row["current_question_id"] else None,
        "cancel_requested": bool(row["cancel_requested"]),
        "model": row["model"],
        "batch_size": int(row["batch_size"] or 5),
        "error_message": row["error_message"],
        "started_at": _now_iso(row["started_at"]),
        "finished_at": _now_iso(row["finished_at"]),
        "created_at": _now_iso(row["created_at"]) or datetime.now(tz=timezone.utc).isoformat(),
    }


def count_pending_candidates(
    conn: Any, *, user_id: str, mode: str, document_id: str | None, cap: int
) -> int:
    sql = text(
        """
        SELECT COUNT(*) AS n
        FROM questions q
        LEFT JOIN question_corrections qc
          ON qc.question_id = q.id AND qc.user_id = :user_id
        WHERE q.is_discarded = false
          AND (qc.correct_option_id IS NULL)
          AND COALESCE(NULLIF(BTRIM(qc.explanation_text), ''), '') = ''
          AND COALESCE(qc.answer_payload, '{}'::jsonb) = '{}'::jsonb
          AND (CAST(:document_id AS UUID) IS NULL OR q.document_id = CAST(:document_id AS UUID))
        """
    )
    n = conn.execute(sql, {"user_id": user_id, "document_id": document_id}).scalar_one()
    return min(int(n or 0), max(0, int(cap)))


def _fetch_batch(
    conn: Any, *, user_id: str, mode: str, document_id: str | None, batch_size: int
) -> list[dict]:
    # Ordering: in frequency mode, occurrences_count DESC; else by section/number.
    if mode == "frequency":
        order_sql = "q.occurrences_count DESC, q.section, q.number_in_section, q.id"
    else:
        order_sql = "q.section, q.number_in_section, q.id"
    sql = text(
        f"""
        SELECT q.id, q.question_type, q.stem, q.options_json, q.subparts_json
        FROM questions q
        LEFT JOIN question_corrections qc
          ON qc.question_id = q.id AND qc.user_id = :user_id
        WHERE q.is_discarded = false
          AND (qc.correct_option_id IS NULL)
          AND COALESCE(NULLIF(BTRIM(qc.explanation_text), ''), '') = ''
          AND COALESCE(qc.answer_payload, '{{}}'::jsonb) = '{{}}'::jsonb
          AND (CAST(:document_id AS UUID) IS NULL OR q.document_id = CAST(:document_id AS UUID))
        ORDER BY {order_sql}
        LIMIT :batch_size
        """
    )
    rows = conn.execute(
        sql,
        {"user_id": user_id, "document_id": document_id, "batch_size": batch_size},
    ).mappings()
    out: list[dict] = []
    for r in rows:
        opts = r["options_json"] if isinstance(r["options_json"], list) else []
        subs = r["subparts_json"] if isinstance(r["subparts_json"], list) else []
        out.append(
            {
                "id": str(r["id"]),
                "question_type": r["question_type"],
                "stem": r["stem"] or "",
                "options": [
                    {"id": str(o.get("id", "")).strip(), "text": str(o.get("text", "")).strip()}
                    for o in opts
                    if isinstance(o, dict)
                ],
                "subparts": [
                    {"id": str(s.get("id", "")).strip(), "prompt": str(s.get("prompt", "")).strip()}
                    for s in subs
                    if isinstance(s, dict)
                ],
            }
        )
    return out


SYSTEM_PROMPT = (
    "Sei un assistente esperto che corregge domande d'esame universitarie italiane. "
    "Per ogni domanda fornita devi: "
    "se di tipo \"multiple_choice\", indicare l'id dell'opzione corretta (campo "
    "correct_option_id) e scrivere una spiegazione concisa (max 3 frasi) che giustifichi "
    "perche' quella opzione e' giusta e le altre no; "
    "se di tipo \"open_text\", scrivere una risposta modello sintetica (max 6 frasi); "
    "se di tipo \"multi_part_open\", scrivere una risposta modello che copra ognuno dei "
    "sottopunti, identificandoli per id. "
    "Rispondi SOLO con JSON valido nel formato richiesto. Non inventare informazioni che "
    "non sono inferibili dalla domanda. Se non riesci a determinare la risposta, imposta "
    "explanation a \"Impossibile determinare la risposta con le informazioni fornite.\" "
    "e ometti correct_option_id."
)


def _build_user_content(batch: list[dict]) -> tuple[str, dict[str, str]]:
    """Build the user message and a tid->question_id map."""
    tid_to_qid: dict[str, str] = {}
    items_payload: list[dict] = []
    for idx, q in enumerate(batch, start=1):
        tid = f"q{idx}"
        tid_to_qid[tid] = q["id"]
        item: dict[str, Any] = {
            "tid": tid,
            "question_type": q["question_type"],
            "stem": q["stem"],
        }
        if q["question_type"] == "multiple_choice" and q["options"]:
            item["options"] = q["options"]
        if q["question_type"] == "multi_part_open" and q["subparts"]:
            item["subparts"] = q["subparts"]
        items_payload.append(item)
    user_text = (
        "Ecco le domande. Rispondi SOLO con JSON nel formato:\n"
        '{"items":[{"tid":"q1","correct_option_id":"b","explanation":"..."},'
        '{"tid":"q2","explanation":"..."}]}\n'
        "Includi correct_option_id SOLO per le domande di tipo multiple_choice. "
        "Mantieni l'id opzione esattamente come ricevuto (es. 'a', 'b').\n\n"
        "DOMANDE:\n"
        + json.dumps({"items": items_payload}, ensure_ascii=False)
    )
    return user_text, tid_to_qid


async def _call_correction_llm(batch: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """Returns (results_by_tid, tid_to_question_id). Raises on transport/parse errors."""
    if not settings.multimodal_api_key:
        raise RuntimeError("Missing MULTIMODAL_API_KEY")

    user_text, tid_to_qid = _build_user_content(batch)
    # Note: GPT-5.x models reject temperature != 1, so we omit the field and let
    # the model use its default. response_format keeps output deterministic enough.
    payload = {
        "model": _model_name(),
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
    }
    url = settings.multimodal_api_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.multimodal_api_key}"}

    timeout = float(settings.correction_gen_timeout_seconds or 60.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    raw = data["choices"][0]["message"]["content"]
    parsed = json.loads(raw)
    items = parsed.get("items", [])
    if not isinstance(items, list):
        raise ValueError("LLM response 'items' is not a list")

    results: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("tid") or "").strip()
        if not tid:
            continue
        explanation = str(item.get("explanation") or "").strip()
        correct_option_id = item.get("correct_option_id")
        results[tid] = {
            "correct_option_id": str(correct_option_id).strip().lower() if correct_option_id else None,
            "explanation_text": explanation or None,
        }
    return results, tid_to_qid


def _append_failures(conn: Any, *, job_id: str, new_failures: list[dict]) -> None:
    if not new_failures:
        return
    conn.execute(
        text(
            """
            UPDATE correction_jobs
            SET failures_json = COALESCE(failures_json, '[]'::jsonb) || CAST(:added AS JSONB)
            WHERE id = :id
            """
        ),
        {"id": job_id, "added": json.dumps(new_failures, ensure_ascii=False)},
    )


def _save_correction(
    conn: Any, *, user_id: str, question_id: str, correct_option_id: str | None, explanation_text: str | None
) -> bool:
    """Insert/update the correction. Returns True on success.
    Uses COALESCE to never overwrite a non-empty existing value (defensive against
    concurrent manual edits)."""
    has_correction = bool(correct_option_id or (explanation_text or "").strip())
    if not has_correction:
        return False
    conn.execute(
        text(
            """
            INSERT INTO question_corrections (
              user_id, question_id, correct_option_id, explanation_text, answer_payload
            )
            VALUES (
              :user_id, :question_id, :correct_option_id, :explanation_text, '{}'::jsonb
            )
            ON CONFLICT (user_id, question_id) DO UPDATE SET
              correct_option_id = COALESCE(question_corrections.correct_option_id, EXCLUDED.correct_option_id),
              explanation_text = COALESCE(
                NULLIF(BTRIM(question_corrections.explanation_text), ''),
                EXCLUDED.explanation_text
              ),
              reviewed_at = now()
            """
        ),
        {
            "user_id": user_id,
            "question_id": question_id,
            "correct_option_id": correct_option_id,
            "explanation_text": explanation_text,
        },
    )
    return True


def _read_cancel_flag(job_id: str) -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT cancel_requested FROM correction_jobs WHERE id = :id"),
            {"id": job_id},
        ).first()
    return bool(row.cancel_requested) if row else True


def _set_status(
    job_id: str,
    *,
    status: str | None = None,
    error_message: str | None = None,
    started: bool = False,
    finished: bool = False,
    current_question_id: str | None | object = ...,
) -> None:
    sets: list[str] = []
    params: dict[str, Any] = {"id": job_id}
    if status is not None:
        sets.append("status = :status")
        params["status"] = status
    if error_message is not None:
        sets.append("error_message = :error_message")
        params["error_message"] = error_message
    if started:
        sets.append("started_at = COALESCE(started_at, now())")
    if finished:
        sets.append("finished_at = now()")
    if current_question_id is not ...:
        sets.append("current_question_id = CAST(:current_question_id AS UUID)")
        params["current_question_id"] = current_question_id
    if not sets:
        return
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE correction_jobs SET {', '.join(sets)} WHERE id = :id"), params)


def _update_counters(
    job_id: str,
    *,
    processed_add: int = 0,
    succeeded_add: int = 0,
    failed_add: int = 0,
    skipped_add: int = 0,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE correction_jobs
                SET processed_count = processed_count + :processed_add,
                    succeeded_count = succeeded_count + :succeeded_add,
                    failed_count = failed_count + :failed_add,
                    skipped_count = skipped_count + :skipped_add
                WHERE id = :id
                """
            ),
            {
                "id": job_id,
                "processed_add": processed_add,
                "succeeded_add": succeeded_add,
                "failed_add": failed_add,
                "skipped_add": skipped_add,
            },
        )


async def run_correction_job(job_id: UUID | str) -> None:
    job_id_str = str(job_id)
    try:
        # Load job header.
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, user_id, mode, document_id, batch_size, total_questions, status
                    FROM correction_jobs
                    WHERE id = :id
                    """
                ),
                {"id": job_id_str},
            ).mappings().first()
        if not row:
            logger.warning("correction_jobs row %s vanished before worker start", job_id_str)
            return

        user_id = str(row["user_id"])
        mode = row["mode"]
        document_id = str(row["document_id"]) if row["document_id"] else None
        batch_size = max(1, int(row["batch_size"] or settings.correction_gen_batch_size))
        total_target = int(row["total_questions"] or 0)

        _set_status(job_id_str, status="running", started=True)

        while True:
            if _read_cancel_flag(job_id_str):
                _set_status(job_id_str, status="cancelled", finished=True, current_question_id=None)
                return

            # Safety: stop once we've processed at least the target count to respect the cap.
            with engine.begin() as conn:
                processed_row = conn.execute(
                    text("SELECT processed_count FROM correction_jobs WHERE id = :id"),
                    {"id": job_id_str},
                ).first()
                processed_now = int(processed_row.processed_count) if processed_row else 0
            if total_target > 0 and processed_now >= total_target:
                _set_status(job_id_str, status="done", finished=True, current_question_id=None)
                return

            remaining = total_target - processed_now if total_target > 0 else batch_size
            this_batch_size = max(1, min(batch_size, remaining))

            with engine.begin() as conn:
                batch = _fetch_batch(
                    conn,
                    user_id=user_id,
                    mode=mode,
                    document_id=document_id,
                    batch_size=this_batch_size,
                )
            if not batch:
                _set_status(job_id_str, status="done", finished=True, current_question_id=None)
                return

            # Mark current question for UI live feedback.
            _set_status(job_id_str, current_question_id=batch[0]["id"])

            # Call the LLM. Failure of the whole call marks all items in batch as failed.
            try:
                results, tid_to_qid = await _call_correction_llm(batch)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Correction job %s: batch LLM call failed", job_id_str)
                failures = [
                    {
                        "question_id": q["id"],
                        "error": f"LLM batch error: {exc}",
                        "stem_preview": (q["stem"] or "")[:180],
                    }
                    for q in batch
                ]
                with engine.begin() as conn:
                    _append_failures(conn, job_id=job_id_str, new_failures=failures)
                _update_counters(
                    job_id_str,
                    processed_add=len(batch),
                    failed_add=len(batch),
                )
                continue

            # Apply per-item.
            qid_to_question = {q["id"]: q for q in batch}
            new_failures: list[dict] = []
            saved = 0
            skipped = 0
            for tid, qid in tid_to_qid.items():
                q = qid_to_question.get(qid)
                if q is None:
                    continue
                item = results.get(tid)
                if not item:
                    new_failures.append(
                        {
                            "question_id": qid,
                            "error": "LLM did not return an answer for this item",
                            "stem_preview": (q["stem"] or "")[:180],
                        }
                    )
                    continue
                correct_option_id = item.get("correct_option_id")
                explanation_text = item.get("explanation_text")
                # For non-MC questions, drop any correct_option_id that snuck in.
                if q["question_type"] != "multiple_choice":
                    correct_option_id = None
                # For MC, only accept option ids that actually exist on this question.
                if q["question_type"] == "multiple_choice" and correct_option_id:
                    known = {str(o.get("id", "")).strip().lower() for o in q["options"]}
                    if correct_option_id.lower() not in known:
                        correct_option_id = None

                try:
                    with engine.begin() as conn:
                        ok = _save_correction(
                            conn,
                            user_id=user_id,
                            question_id=qid,
                            correct_option_id=correct_option_id,
                            explanation_text=explanation_text,
                        )
                    if ok:
                        saved += 1
                    else:
                        new_failures.append(
                            {
                                "question_id": qid,
                                "error": "LLM returned empty answer",
                                "stem_preview": (q["stem"] or "")[:180],
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Correction job %s: save failed for %s", job_id_str, qid)
                    new_failures.append(
                        {
                            "question_id": qid,
                            "error": f"DB save failed: {exc}",
                            "stem_preview": (q["stem"] or "")[:180],
                        }
                    )

            if new_failures:
                with engine.begin() as conn:
                    _append_failures(conn, job_id=job_id_str, new_failures=new_failures)

            _update_counters(
                job_id_str,
                processed_add=len(batch),
                succeeded_add=saved,
                failed_add=len(new_failures),
                skipped_add=skipped,
            )

    except asyncio.CancelledError:
        logger.info("Correction job %s task cancelled", job_id_str)
        try:
            _set_status(job_id_str, status="cancelled", finished=True, current_question_id=None)
        except Exception:  # noqa: BLE001
            pass
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Correction job %s crashed", job_id_str)
        try:
            _set_status(
                job_id_str,
                status="error",
                finished=True,
                error_message=str(exc),
                current_question_id=None,
            )
        except Exception:  # noqa: BLE001
            pass
    finally:
        _RUNNING.pop(job_id_str, None)


def schedule_job(job_id: UUID | str) -> None:
    """Spawn the worker on the running event loop."""
    job_id_str = str(job_id)
    if job_id_str in _RUNNING and not _RUNNING[job_id_str].done():
        return
    task = asyncio.create_task(run_correction_job(job_id_str))
    _RUNNING[job_id_str] = task


def mark_orphan_running_as_interrupted() -> None:
    """Called at startup. Any row left in queued/running is from a previous process."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE correction_jobs
                SET status = 'interrupted',
                    finished_at = now(),
                    error_message = COALESCE(error_message, 'Process restarted')
                WHERE status IN ('queued', 'running')
                """
            )
        )
