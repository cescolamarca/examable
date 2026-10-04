"""Question-bank cleanup: text normalisation, canonical fingerprints and duplicate merging."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from app.database import engine


def _clean_text(raw: str) -> str:
    s = unicodedata.normalize("NFKC", raw or "")
    s = s.replace("\ufffd", "")
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    return s.strip()


def _canonical_text(raw: str) -> str:
    s = _clean_text(raw).lower()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _canonical_option_text(raw: str) -> str:
    """Testo opzione: come pulizia base ma senza rimuovere : / . (URL e simili restano distinti)."""
    s = _clean_text(raw).lower()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _as_list(v: Any) -> list[dict[str, Any]]:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return x if isinstance(x, list) else []
        except Exception:
            return []
    return []


def _as_dict(v: Any) -> dict[str, Any]:
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return x if isinstance(x, dict) else {}
        except Exception:
            return {}
    return {}


def _clean_options(options: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for opt in _as_list(options):
        oid = str(opt.get("id", "")).strip().lower()
        txt = _clean_text(str(opt.get("text", "")))
        if oid and txt:
            out.append({"id": oid, "text": txt})
    out.sort(key=lambda x: x["id"])
    return out


def _clean_subparts(subparts: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for sp in _as_list(subparts):
        sid = str(sp.get("id", "")).strip()
        prompt = _clean_text(str(sp.get("prompt", "")))
        if sid and prompt:
            out.append({"id": sid, "prompt": prompt})
    out.sort(key=lambda x: x["id"])
    return out


LEADING_NUMBERING_RE = re.compile(r"^(?:(?:domanda|esercizio|quesito)\s+)?\d+\s+")


@dataclass
class QuestionRow:
    id: str
    question_type: str
    stem: str
    options: list[dict[str, str]]
    subparts: list[dict[str, str]]
    confidence: float
    is_discarded: bool
    solution: dict[str, Any]

    @property
    def fingerprint(self) -> str:
        # Drop only the leading numbering ("3.", "12)", "esercizio 2"): numbers inside
        # the text are data, and exercises that differ only in them are different.
        stem_part = LEADING_NUMBERING_RE.sub("", _canonical_text(self.stem))
        if self.question_type == "multiple_choice":
            # Sort by canonical text (not option id) so that transposed answer
            # orderings — same options, different A/B/C/D assignment — collapse
            # to the same fingerprint. The correct-answer letter is intentionally
            # excluded: it's irrelevant once option order is normalized, and
            # relying on it would split duplicates whenever one copy hasn't been
            # AI-corrected yet (solution_json still empty).
            opt_texts = sorted(_canonical_option_text(o["text"]) for o in self.options)
            options_part = "||".join(opt_texts)
        else:
            opts_sorted = sorted(self.options, key=lambda o: o["id"])
            options_part = "|".join(f"{o['id']}:{_canonical_option_text(o['text'])}" for o in opts_sorted)
        sub_sorted = sorted(self.subparts, key=lambda s: (s["id"], _canonical_text(s["prompt"])))
        subparts_part = "|".join(f"{s['id']}:{_canonical_text(s['prompt'])}" for s in sub_sorted)
        payload = f"{self.question_type}##{stem_part}##{options_part}##{subparts_part}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    @property
    def score(self) -> float:
        return float(self.confidence) + min(0.5, len(self.stem) / 2000.0)


def _merge_references(conn: Any, old_id: str, new_id: str) -> None:
    conn.execute(
        text(
            """
            INSERT INTO question_tags (question_id, tag_id, score, source)
            SELECT :new_id, tag_id, score, source
            FROM question_tags
            WHERE question_id = :old_id
            ON CONFLICT (question_id, tag_id)
            DO UPDATE SET score = GREATEST(question_tags.score, EXCLUDED.score)
            """
        ),
        {"old_id": old_id, "new_id": new_id},
    )
    conn.execute(text("DELETE FROM question_tags WHERE question_id = :old_id"), {"old_id": old_id})

    conn.execute(
        text(
            """
            INSERT INTO schedule_state (
              user_id, question_id, due_at, ease_factor, interval_days, lapses, reps, state, last_reviewed_at
            )
            SELECT user_id, :new_id, due_at, ease_factor, interval_days, lapses, reps, state, last_reviewed_at
            FROM schedule_state
            WHERE question_id = :old_id
            ON CONFLICT (user_id, question_id)
            DO UPDATE SET
              -- Keep the more conservative schedule of the two copies.
              due_at = LEAST(schedule_state.due_at, EXCLUDED.due_at),
              ease_factor = LEAST(schedule_state.ease_factor, EXCLUDED.ease_factor),
              interval_days = LEAST(schedule_state.interval_days, EXCLUDED.interval_days),
              lapses = GREATEST(schedule_state.lapses, EXCLUDED.lapses),
              reps = GREATEST(schedule_state.reps, EXCLUDED.reps),
              last_reviewed_at = GREATEST(schedule_state.last_reviewed_at, EXCLUDED.last_reviewed_at)
            """
        ),
        {"old_id": old_id, "new_id": new_id},
    )
    conn.execute(text("DELETE FROM schedule_state WHERE question_id = :old_id"), {"old_id": old_id})
    conn.execute(
        text("UPDATE attempts SET question_id = :new_id WHERE question_id = :old_id"),
        {"old_id": old_id, "new_id": new_id},
    )

    conn.execute(
        text(
            """
            INSERT INTO question_occurrences (question_id, document_id, source_file_name, source_section, source_number)
            SELECT :new_id, document_id, source_file_name, source_section, source_number
            FROM question_occurrences
            WHERE question_id = :old_id
            ON CONFLICT (question_id, document_id, source_section, source_number) DO NOTHING
            """
        ),
        {"old_id": old_id, "new_id": new_id},
    )
    conn.execute(text("DELETE FROM question_occurrences WHERE question_id = :old_id"), {"old_id": old_id})

    conn.execute(
        text(
            """
            INSERT INTO question_reviews (user_id, question_id, status, first_seen_at, reviewed_at)
            SELECT user_id, :new_id, status, first_seen_at, reviewed_at
            FROM question_reviews
            WHERE question_id = :old_id
            ON CONFLICT (user_id, question_id)
            DO UPDATE SET
              status = CASE
                WHEN question_reviews.status = 'correct' OR EXCLUDED.status = 'correct' THEN 'correct'
                ELSE 'wrong'
              END,
              first_seen_at = LEAST(question_reviews.first_seen_at, EXCLUDED.first_seen_at),
              reviewed_at = GREATEST(question_reviews.reviewed_at, EXCLUDED.reviewed_at)
            """
        ),
        {"old_id": old_id, "new_id": new_id},
    )
    conn.execute(text("DELETE FROM question_reviews WHERE question_id = :old_id"), {"old_id": old_id})

    conn.execute(
        text(
            """
            INSERT INTO question_corrections (
              user_id, question_id, correct_option_id, explanation_text, answer_payload, first_seen_at, reviewed_at
            )
            SELECT
              user_id, :new_id, correct_option_id, explanation_text, answer_payload, first_seen_at, reviewed_at
            FROM question_corrections
            WHERE question_id = :old_id
            ON CONFLICT (user_id, question_id)
            DO UPDATE SET
              correct_option_id = COALESCE(question_corrections.correct_option_id, EXCLUDED.correct_option_id),
              explanation_text = COALESCE(
                NULLIF(BTRIM(question_corrections.explanation_text), ''),
                EXCLUDED.explanation_text
              ),
              answer_payload = CASE
                WHEN question_corrections.answer_payload = '{}'::jsonb THEN EXCLUDED.answer_payload
                ELSE question_corrections.answer_payload
              END,
              first_seen_at = LEAST(question_corrections.first_seen_at, EXCLUDED.first_seen_at),
              reviewed_at = GREATEST(question_corrections.reviewed_at, EXCLUDED.reviewed_at)
            """
        ),
        {"old_id": old_id, "new_id": new_id},
    )
    conn.execute(text("DELETE FROM question_corrections WHERE question_id = :old_id"), {"old_id": old_id})


def refresh_occurrence_aggregates(conn: Any) -> None:
    conn.execute(
        text(
            """
            UPDATE questions q
            SET occurrences_count = agg.occurrences_count,
                source_files_json = agg.source_files_json
            FROM (
              SELECT
                question_id,
                COUNT(*)::INTEGER AS occurrences_count,
                jsonb_agg(DISTINCT source_file_name ORDER BY source_file_name) AS source_files_json
              FROM question_occurrences
              GROUP BY question_id
            ) agg
            WHERE q.id = agg.question_id
              AND (q.occurrences_count, q.source_files_json)
                  IS DISTINCT FROM (agg.occurrences_count, agg.source_files_json)
            """
        )
    )


def run_cleanup_dedupe() -> dict[str, Any]:
    with engine.begin() as conn:
        # Backfill provenance for questions that have none yet (legacy rows, bank imports).
        conn.execute(
            text(
                """
                INSERT INTO question_occurrences
                  (question_id, document_id, source_file_name, source_section, source_number)
                SELECT q.id, q.document_id, d.title, q.section, q.number_in_section
                FROM questions q
                JOIN documents d ON d.id = q.document_id
                WHERE NOT EXISTS (SELECT 1 FROM question_occurrences o WHERE o.question_id = q.id)
                ON CONFLICT (question_id, document_id, source_section, source_number) DO NOTHING
                """
            )
        )

        rows = conn.execute(
            text(
                """
                SELECT id, question_type, stem, options_json, subparts_json, confidence,
                       is_discarded, solution_json, dedupe_fingerprint
                FROM questions
                """
            )
        ).mappings()

        questions: list[QuestionRow] = []
        changed: list[dict[str, Any]] = []
        for r in rows:
            q = QuestionRow(
                id=str(r["id"]),
                question_type=str(r["question_type"]),
                stem=_clean_text(str(r["stem"])),
                options=_clean_options(r["options_json"]),
                subparts=_clean_subparts(r["subparts_json"]),
                confidence=float(r["confidence"]),
                is_discarded=bool(r["is_discarded"]),
                solution=_as_dict(r["solution_json"]),
            )
            questions.append(q)
            # Only rows whose normalised form differs are written back, so re-running
            # the cleanup after each upload costs one read of the bank, not a full rewrite.
            if (q.stem, q.options, q.subparts, q.fingerprint) != (
                r["stem"],
                r["options_json"],
                r["subparts_json"],
                r["dedupe_fingerprint"],
            ):
                changed.append(
                    {
                        "id": q.id,
                        "stem": q.stem,
                        "options_json": json.dumps(q.options, ensure_ascii=False),
                        "subparts_json": json.dumps(q.subparts, ensure_ascii=False),
                        "fingerprint": q.fingerprint,
                    }
                )

        if changed:
            conn.execute(
                text(
                    """
                    UPDATE questions
                    SET stem = :stem,
                        options_json = CAST(:options_json AS JSONB),
                        subparts_json = CAST(:subparts_json AS JSONB),
                        dedupe_fingerprint = :fingerprint
                    WHERE id = :id
                    """
                ),
                changed,
            )

        groups: dict[str, list[QuestionRow]] = defaultdict(list)
        for q in questions:
            groups[q.fingerprint].append(q)

        duplicates = {fp: vals for fp, vals in groups.items() if len(vals) > 1}
        merged_groups = 0
        deleted_rows = 0
        top_occurrences: list[dict[str, Any]] = []

        for fp, vals in duplicates.items():
            ordered = sorted(vals, key=lambda x: (x.is_discarded, -x.score))
            keeper = ordered[0]
            top_occurrences.append(
                {
                    "fingerprint": fp,
                    "occurrences": len(ordered),
                    "keeper_id": keeper.id,
                    "sample_stem": keeper.stem[:180],
                }
            )
            for dup in ordered[1:]:
                _merge_references(conn, old_id=dup.id, new_id=keeper.id)
                conn.execute(text("DELETE FROM questions WHERE id = :id"), {"id": dup.id})
                deleted_rows += 1
            merged_groups += 1

        refresh_occurrence_aggregates(conn)
        total_after = conn.execute(text("SELECT COUNT(*) FROM questions")).scalar_one()

    top_occurrences.sort(key=lambda x: x["occurrences"], reverse=True)
    return {
        "total_questions_after": int(total_after),
        "rows_normalised": len(changed),
        "duplicate_groups_merged": merged_groups,
        "duplicate_rows_deleted": deleted_rows,
        "top_occurrences": top_occurrences[:50],
    }


def detach_document_questions(conn: Any, document_id: str) -> int:
    """Remove a document's contribution to the bank without losing shared questions.

    After deduplication a question shared by several exam sessions is owned by one
    of them. Deleting (or re-processing) that owner must not drop the question for
    the other sessions, so shared questions are first moved to another document
    they occur in, keeping their attempts, schedules and corrections. Returns the
    number of questions that were deleted because no other document contains them.
    """
    shared = (
        conn.execute(
            text(
                """
            SELECT DISTINCT ON (o.question_id) o.question_id, o.document_id, o.source_number
            FROM question_occurrences o
            JOIN questions q ON q.id = o.question_id
            WHERE q.document_id = :document_id AND o.document_id <> :document_id
            ORDER BY o.question_id, o.created_at, o.source_file_name
            """
            ),
            {"document_id": document_id},
        )
        .mappings()
        .all()
    )
    for row in shared:
        # Reuse the number the question had in the new owner unless that slot is taken.
        conn.execute(
            text(
                """
                UPDATE questions q
                SET document_id = :new_document_id,
                    number_in_section = CASE
                      WHEN CAST(:number AS INTEGER) IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM questions x
                        WHERE x.document_id = :new_document_id AND x.section = q.section
                          AND x.number_in_section = CAST(:number AS INTEGER)
                      ) THEN CAST(:number AS INTEGER)
                      ELSE (
                        SELECT COALESCE(MAX(x.number_in_section), 0) + 1 FROM questions x
                        WHERE x.document_id = :new_document_id AND x.section = q.section
                      )
                    END
                WHERE q.id = :question_id
                """
            ),
            {
                "question_id": str(row["question_id"]),
                "new_document_id": str(row["document_id"]),
                "number": row["source_number"],
            },
        )
    conn.execute(text("DELETE FROM question_occurrences WHERE document_id = :id"), {"id": document_id})
    deleted = conn.execute(text("DELETE FROM questions WHERE document_id = :id"), {"id": document_id}).rowcount
    refresh_occurrence_aggregates(conn)
    return int(deleted or 0)
