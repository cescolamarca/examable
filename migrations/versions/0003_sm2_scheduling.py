"""SM-2 scheduling state.

Adds the per-question ease factor and current interval used by
app.services.scheduler, backfilling the interval from what the previous
fixed-ladder scheduler had chosen (due_at - last_reviewed_at). Drops the FSRS
columns (stability, difficulty, retrievability), which were never written.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE schedule_state ADD COLUMN IF NOT EXISTS ease_factor NUMERIC(4,2) NOT NULL DEFAULT 2.50")
    op.execute(
        "ALTER TABLE schedule_state ADD COLUMN IF NOT EXISTS interval_days NUMERIC(8,2) NOT NULL DEFAULT 0"
    )
    op.execute(
        """
        UPDATE schedule_state
        SET interval_days = LEAST(365, ROUND((EXTRACT(EPOCH FROM (due_at - last_reviewed_at)) / 86400)::numeric, 2))
        WHERE state = 'review' AND last_reviewed_at IS NOT NULL AND due_at > last_reviewed_at
        """
    )
    op.execute("ALTER TABLE schedule_state ADD CONSTRAINT schedule_state_ease_factor_check CHECK (ease_factor >= 1.3)")
    op.execute("ALTER TABLE schedule_state DROP COLUMN IF EXISTS stability")
    op.execute("ALTER TABLE schedule_state DROP COLUMN IF EXISTS difficulty")
    op.execute("ALTER TABLE schedule_state DROP COLUMN IF EXISTS retrievability")


def downgrade() -> None:
    for column in ("stability", "difficulty", "retrievability"):
        op.execute(f"ALTER TABLE schedule_state ADD COLUMN IF NOT EXISTS {column} NUMERIC(10,4) NOT NULL DEFAULT 0.0")
    op.execute("ALTER TABLE schedule_state DROP CONSTRAINT IF EXISTS schedule_state_ease_factor_check")
    op.execute("ALTER TABLE schedule_state DROP COLUMN IF EXISTS interval_days")
    op.execute("ALTER TABLE schedule_state DROP COLUMN IF EXISTS ease_factor")
