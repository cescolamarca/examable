CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";

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

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email CITEXT UNIQUE NOT NULL,
  full_name TEXT,
  role VARCHAR(20) NOT NULL DEFAULT 'student' CHECK (role IN ('student', 'admin', 'reviewer')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attempts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  answered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_correct BOOLEAN NOT NULL,
  answer_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  latency_ms INTEGER CHECK (latency_ms >= 0),
  grade SMALLINT CHECK (grade BETWEEN 0 AND 5)
);

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
