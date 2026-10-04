"""Indexes for the hot read paths.

- schedule_state (user_id, due_at): "what is due next" for a user.
- attempts (user_id, question_id) and (simulation_id): per-user stats, priority
  modes and simulation resume.
- question_tags (tag_id): tag and preset filters probe by tag.
- questions (document_id): listing, re-processing and deleting a document.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

INDEXES = {
    "idx_schedule_state_user_due": "schedule_state (user_id, due_at)",
    "idx_attempts_user_question": "attempts (user_id, question_id)",
    "idx_attempts_simulation": "attempts (simulation_id) WHERE simulation_id IS NOT NULL",
    "idx_question_tags_tag": "question_tags (tag_id)",
    "idx_questions_document": "questions (document_id)",
}


def upgrade() -> None:
    for name, target in INDEXES.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")


def downgrade() -> None:
    for name in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
