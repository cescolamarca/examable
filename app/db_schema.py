"""Schema bootstrap: creates the base schema and applies additive changes at startup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.database import engine


def ensure_runtime_schema() -> None:
    with engine.begin() as conn:
        has_documents = conn.execute(text("SELECT to_regclass('public.documents')")).scalar()
        if has_documents is None:
            schema_path = Path("sql/schema.sql")
            if schema_path.exists():
                conn.exec_driver_sql(schema_path.read_text(encoding="utf-8"))

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
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS correction_jobs (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  mode VARCHAR(20) NOT NULL CHECK (mode IN ('document','frequency')),
                  document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
                  status VARCHAR(20) NOT NULL
                    CHECK (status IN ('queued','running','done','cancelled','interrupted','error')),
                  total_questions INTEGER NOT NULL DEFAULT 0,
                  processed_count INTEGER NOT NULL DEFAULT 0,
                  succeeded_count INTEGER NOT NULL DEFAULT 0,
                  failed_count INTEGER NOT NULL DEFAULT 0,
                  skipped_count INTEGER NOT NULL DEFAULT 0,
                  current_question_id UUID,
                  failures_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                  cancel_requested BOOLEAN NOT NULL DEFAULT false,
                  overwrite BOOLEAN NOT NULL DEFAULT false,
                  model VARCHAR(100) NOT NULL,
                  batch_size INTEGER NOT NULL DEFAULT 5,
                  error_message TEXT,
                  started_at TIMESTAMPTZ,
                  finished_at TIMESTAMPTZ,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_correction_jobs_status
                ON correction_jobs (status)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_correction_jobs_document
                ON correction_jobs (document_id)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_correction_jobs_created
                ON correction_jobs (created_at DESC)
                """
            )
        )
        # Partial unique index: at most one row in (queued|running) state.
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_correction_jobs_one_running
                ON correction_jobs ((1)) WHERE status IN ('queued','running')
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
                    WHERE table_schema = 'public' AND table_name = 'correction_jobs'
                  ) THEN
                    ALTER TABLE correction_jobs
                    ADD COLUMN IF NOT EXISTS overwrite BOOLEAN NOT NULL DEFAULT false;
                  END IF;
                END
                $$;
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS simulations (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                  question_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                  requested_total INTEGER NOT NULL DEFAULT 0,
                  generated_total INTEGER NOT NULL DEFAULT 0,
                  exhaustive BOOLEAN NOT NULL DEFAULT false
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_simulations_user_created
                ON simulations (user_id, created_at DESC)
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
                    WHERE table_schema = 'public' AND table_name = 'attempts'
                  ) THEN
                    ALTER TABLE attempts
                    ADD COLUMN IF NOT EXISTS simulation_id UUID REFERENCES simulations(id) ON DELETE SET NULL;
                  END IF;
                END
                $$;
                """
            )
        )
        ensure_tagging_schema(conn)


def ensure_tagging_schema(conn: Any) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tag_presets (
              id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
              name TEXT NOT NULL UNIQUE,
              slug TEXT NOT NULL UNIQUE,
              description TEXT,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tag_preset_tags (
              preset_id UUID NOT NULL REFERENCES tag_presets(id) ON DELETE CASCADE,
              tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
              PRIMARY KEY (preset_id, tag_id)
            )
            """
        )
    )
