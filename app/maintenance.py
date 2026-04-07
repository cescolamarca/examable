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


def ensure_runtime_schema() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                  IF EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'questions'
                  ) THEN
                    ALTER TABLE questions
                    ADD COLUMN IF NOT EXISTS occurrences_count INTEGER NOT NULL DEFAULT 1;
                  END IF;
                END
                $$;
                """
            )
        )
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                  IF EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'questions'
                  ) THEN
                    ALTER TABLE questions
                    ADD COLUMN IF NOT EXISTS source_files_json JSONB NOT NULL DEFAULT '[]'::jsonb;
                  END IF;
                END
                $$;
                """
            )
        )
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                  IF EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'questions'
                  ) THEN
                    ALTER TABLE questions
                    ADD COLUMN IF NOT EXISTS dedupe_fingerprint CHAR(40);
                  END IF;
                END
                $$;
                """
            )
        )
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                  IF EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'questions'
                  ) THEN
                    ALTER TABLE questions
                    ADD COLUMN IF NOT EXISTS is_discarded BOOLEAN NOT NULL DEFAULT false;
                  END IF;
                END
                $$;
                """
            )
        )
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                  IF EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'questions'
                  ) THEN
                    ALTER TABLE questions
                    ADD COLUMN IF NOT EXISTS discarded_at TIMESTAMPTZ;
                  END IF;
                END
                $$;
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_questions_dedupe_fingerprint
                ON questions (dedupe_fingerprint)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_questions_is_discarded
                ON questions (is_discarded)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS question_occurrences (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                  source_file_name TEXT NOT NULL,
                  source_section VARCHAR(20),
                  source_number INTEGER,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  UNIQUE (question_id, document_id, source_section, source_number)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_question_occurrences_question
                ON question_occurrences (question_id)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_question_occurrences_document
                ON question_occurrences (document_id)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS question_reviews (
                  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                  status VARCHAR(20) NOT NULL CHECK (status IN ('correct', 'wrong')),
                  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  PRIMARY KEY (user_id, question_id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_question_reviews_user_status
                ON question_reviews (user_id, status)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS question_corrections (
                  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                  correct_option_id TEXT,
                  explanation_text TEXT,
                  answer_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  PRIMARY KEY (user_id, question_id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_question_corrections_user
                ON question_corrections (user_id)
                """
            )
        )


def _clean_text(raw: str) -> str:
    s = unicodedata.normalize("NFKC", raw or "")
    s = s.replace("\uFFFD", "")
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


@dataclass
class QuestionRow:
    id: str
    question_type: str
    stem: str
    options: list[dict[str, str]]
    subparts: list[dict[str, str]]
    confidence: float

    @property
    def fingerprint(self) -> str:
        stem_part = _canonical_text(self.stem)
        stem_part = re.sub(r"\b\d+\b", " ", stem_part)
        stem_part = re.sub(r"\s+", " ", stem_part).strip()
        if self.question_type == "multiple_choice":
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
              user_id, question_id, due_at, stability, difficulty, retrievability, lapses, reps, state, last_reviewed_at
            )
            SELECT user_id, :new_id, due_at, stability, difficulty, retrievability, lapses, reps, state, last_reviewed_at
            FROM schedule_state
            WHERE question_id = :old_id
            ON CONFLICT (user_id, question_id)
            DO UPDATE SET
              due_at = LEAST(schedule_state.due_at, EXCLUDED.due_at),
              lapses = GREATEST(schedule_state.lapses, EXCLUDED.lapses),
              reps = GREATEST(schedule_state.reps, EXCLUDED.reps),
              last_reviewed_at = GREATEST(schedule_state.last_reviewed_at, EXCLUDED.last_reviewed_at)
            """
        ),
        {"old_id": old_id, "new_id": new_id},
    )
    conn.execute(text("DELETE FROM schedule_state WHERE question_id = :old_id"), {"old_id": old_id})
    conn.execute(text("UPDATE attempts SET question_id = :new_id WHERE question_id = :old_id"), {"old_id": old_id, "new_id": new_id})

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


def _refresh_occurrence_aggregates(conn: Any) -> None:
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
            """
        )
    )


def run_cleanup_dedupe() -> dict[str, Any]:
    ensure_runtime_schema()

    with engine.begin() as conn:
        # Backfill provenance rows for legacy questions that do not have occurrences yet.
        conn.execute(
            text(
                """
                INSERT INTO question_occurrences (question_id, document_id, source_file_name, source_section, source_number)
                SELECT q.id, q.document_id, d.title, q.section, q.number_in_section
                FROM questions q
                JOIN documents d ON d.id = q.document_id
                ON CONFLICT (question_id, document_id, source_section, source_number) DO NOTHING
                """
            )
        )

        rows = conn.execute(
            text("SELECT id, question_type, stem, options_json, subparts_json, confidence FROM questions")
        ).mappings()

        questions: list[QuestionRow] = []
        for r in rows:
            questions.append(
                QuestionRow(
                    id=str(r["id"]),
                    question_type=str(r["question_type"]),
                    stem=_clean_text(str(r["stem"])),
                    options=_clean_options(r["options_json"]),
                    subparts=_clean_subparts(r["subparts_json"]),
                    confidence=float(r["confidence"]),
                )
            )

        # Persist cleaned fields + fingerprint.
        for q in questions:
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
                {
                    "id": q.id,
                    "stem": q.stem,
                    "options_json": json.dumps(q.options, ensure_ascii=False),
                    "subparts_json": json.dumps(q.subparts, ensure_ascii=False),
                    "fingerprint": q.fingerprint,
                },
            )

        groups: dict[str, list[QuestionRow]] = defaultdict(list)
        for q in questions:
            groups[q.fingerprint].append(q)

        duplicates = {fp: vals for fp, vals in groups.items() if len(vals) > 1}
        merged_groups = 0
        deleted_rows = 0
        top_occurrences: list[dict[str, Any]] = []

        for fp, vals in duplicates.items():
            ordered = sorted(vals, key=lambda x: x.score, reverse=True)
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

        _refresh_occurrence_aggregates(conn)
        total_after = conn.execute(text("SELECT COUNT(*) FROM questions")).scalar_one()

    top_occurrences.sort(key=lambda x: x["occurrences"], reverse=True)
    return {
        "total_questions_after": int(total_after),
        "duplicate_groups_merged": merged_groups,
        "duplicate_rows_deleted": deleted_rows,
        "top_occurrences": top_occurrences[:50],
    }
