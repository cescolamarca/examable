"""Baseline schema.

Written to be idempotent (IF NOT EXISTS everywhere) so it can be applied both to
an empty database and to deployments created before migrations existed, whose
schema was built by sql/schema.sql plus ad-hoc ALTERs at startup.

Revision ID: 0001
Revises:
Create Date: 2026-10-04
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

-- Uploaded exam PDFs. sha256 makes uploads idempotent.
CREATE TABLE IF NOT EXISTS documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL,
  course TEXT,
  academic_year TEXT,
  exam_date DATE,
  language VARCHAR(10) NOT NULL DEFAULT 'it',
  source_uri TEXT NOT NULL,
  sha256 CHAR(64) NOT NULL UNIQUE,
  pages INTEGER,
  ingestion_status VARCHAR(20) NOT NULL DEFAULT 'uploaded',
  ingestion_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at TIMESTAMPTZ
);

-- One row per distinct question; duplicates across sessions are merged into a
-- single row owned by one document, with provenance in question_occurrences.
CREATE TABLE IF NOT EXISTS questions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  section VARCHAR(20) NOT NULL CHECK (section IN ('quiz', 'teoria', 'esercizio')),
  number_in_section INTEGER NOT NULL CHECK (number_in_section > 0),
  question_type VARCHAR(30) NOT NULL CHECK (question_type IN ('multiple_choice', 'open_text', 'multi_part_open')),
  stem TEXT NOT NULL,
  options_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  subparts_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  assets_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  solution_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  difficulty NUMERIC(3,2) NOT NULL DEFAULT 0.50 CHECK (difficulty >= 0 AND difficulty <= 1),
  language VARCHAR(10) NOT NULL DEFAULT 'it',
  page_start INTEGER CHECK (page_start >= 1),
  page_end INTEGER CHECK (page_end >= 1),
  confidence NUMERIC(3,2) NOT NULL DEFAULT 0.90 CHECK (confidence >= 0 AND confidence <= 1),
  needs_review BOOLEAN NOT NULL DEFAULT false,
  is_discarded BOOLEAN NOT NULL DEFAULT false,
  discarded_at TIMESTAMPTZ,
  occurrences_count INTEGER NOT NULL DEFAULT 1 CHECK (occurrences_count >= 1),
  source_files_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  dedupe_fingerprint CHAR(40),
  schema_version VARCHAR(10) NOT NULL DEFAULT '1.0',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (document_id, section, number_in_section)
);
-- Columns added after the first deployments; no-ops on databases that have them.
ALTER TABLE questions ADD COLUMN IF NOT EXISTS is_discarded BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS discarded_at TIMESTAMPTZ;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS occurrences_count INTEGER NOT NULL DEFAULT 1 CHECK (occurrences_count >= 1);
ALTER TABLE questions ADD COLUMN IF NOT EXISTS source_files_json JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS dedupe_fingerprint CHAR(40);
CREATE INDEX IF NOT EXISTS idx_questions_dedupe_fingerprint ON questions (dedupe_fingerprint);
CREATE INDEX IF NOT EXISTS idx_questions_is_discarded ON questions (is_discarded);

CREATE TABLE IF NOT EXISTS question_occurrences (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  source_file_name TEXT NOT NULL,
  source_section VARCHAR(20),
  source_number INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (question_id, document_id, source_section, source_number)
);
CREATE INDEX IF NOT EXISTS idx_question_occurrences_question ON question_occurrences (question_id);
CREATE INDEX IF NOT EXISTS idx_question_occurrences_document ON question_occurrences (document_id);

CREATE TABLE IF NOT EXISTS tags (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL UNIQUE,
  slug TEXT NOT NULL UNIQUE,
  parent_id UUID REFERENCES tags(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS question_tags (
  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  score NUMERIC(3,2) NOT NULL DEFAULT 1.00 CHECK (score >= 0 AND score <= 1),
  source VARCHAR(20) NOT NULL DEFAULT 'rule' CHECK (source IN ('ai', 'rule', 'manual')),
  PRIMARY KEY (question_id, tag_id)
);

-- Named groups of tags matching the course modules ("Modulo 1", ...).
CREATE TABLE IF NOT EXISTS tag_presets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL UNIQUE,
  slug TEXT NOT NULL UNIQUE,
  description TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tag_preset_tags (
  preset_id UUID NOT NULL REFERENCES tag_presets(id) ON DELETE CASCADE,
  tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (preset_id, tag_id)
);

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email CITEXT UNIQUE NOT NULL,
  full_name TEXT,
  role VARCHAR(20) NOT NULL DEFAULT 'student' CHECK (role IN ('student', 'admin', 'reviewer')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A stored practice simulation (question list + config), so it can be resumed.
CREATE TABLE IF NOT EXISTS simulations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  question_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  requested_total INTEGER NOT NULL DEFAULT 0,
  generated_total INTEGER NOT NULL DEFAULT 0,
  exhaustive BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_simulations_user_created ON simulations (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS attempts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  answered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_correct BOOLEAN NOT NULL,
  answer_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  latency_ms INTEGER CHECK (latency_ms >= 0),
  grade SMALLINT CHECK (grade BETWEEN 0 AND 5),
  simulation_id UUID REFERENCES simulations(id) ON DELETE SET NULL
);
ALTER TABLE attempts ADD COLUMN IF NOT EXISTS simulation_id UUID REFERENCES simulations(id) ON DELETE SET NULL;

-- Per-user spaced-repetition state of each question.
CREATE TABLE IF NOT EXISTS schedule_state (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  due_at TIMESTAMPTZ NOT NULL,
  stability NUMERIC(10,4) NOT NULL DEFAULT 0.0,
  difficulty NUMERIC(10,4) NOT NULL DEFAULT 0.0,
  retrievability NUMERIC(10,4) NOT NULL DEFAULT 0.0,
  lapses INTEGER NOT NULL DEFAULT 0,
  reps INTEGER NOT NULL DEFAULT 0,
  state VARCHAR(20) NOT NULL DEFAULT 'learning' CHECK (state IN ('new', 'learning', 'review', 'relearning')),
  last_reviewed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, question_id)
);

CREATE TABLE IF NOT EXISTS question_reviews (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  status VARCHAR(20) NOT NULL CHECK (status IN ('correct', 'wrong')),
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_question_reviews_user_status ON question_reviews (user_id, status);

-- The user's answer key: manual or AI-generated correct option and explanation.
CREATE TABLE IF NOT EXISTS question_corrections (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  correct_option_id TEXT,
  explanation_text TEXT,
  answer_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_question_corrections_user ON question_corrections (user_id);

-- Background LLM jobs that generate corrections in batches.
CREATE TABLE IF NOT EXISTS correction_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode VARCHAR(20) NOT NULL CHECK (mode IN ('document', 'frequency')),
  document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
  status VARCHAR(20) NOT NULL
    CHECK (status IN ('queued', 'running', 'done', 'cancelled', 'interrupted', 'error')),
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
);
ALTER TABLE correction_jobs ADD COLUMN IF NOT EXISTS overwrite BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_correction_jobs_status ON correction_jobs (status);
CREATE INDEX IF NOT EXISTS idx_correction_jobs_document ON correction_jobs (document_id);
CREATE INDEX IF NOT EXISTS idx_correction_jobs_created ON correction_jobs (created_at DESC);
-- At most one job can be queued or running at any time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_correction_jobs_one_running
  ON correction_jobs ((1)) WHERE status IN ('queued', 'running');

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_questions_updated_at ON questions;
CREATE TRIGGER trg_questions_updated_at
BEFORE UPDATE ON questions
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_schedule_updated_at ON schedule_state;
CREATE TRIGGER trg_schedule_updated_at
BEFORE UPDATE ON schedule_state
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
"""

TABLES = [
    "correction_jobs",
    "question_corrections",
    "question_reviews",
    "schedule_state",
    "attempts",
    "simulations",
    "users",
    "tag_preset_tags",
    "tag_presets",
    "question_tags",
    "tags",
    "question_occurrences",
    "questions",
    "documents",
]


def upgrade() -> None:
    # Plain multi-statement SQL, sent as-is (no bind-parameter parsing).
    op.get_bind().exec_driver_sql(SCHEMA)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {', '.join(TABLES)} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
