"""Spaced repetition with the SM-2 algorithm (Wozniak, 1990), as popularised by Anki.

Each (user, question) pair carries an ease factor (EF) and the current interval.
After every answer, graded 0-5:

* EF += 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02), floored at 1.3;
* grade >= 3 (recalled): the interval grows 1 day -> 6 days -> previous * EF;
* grade < 3 (forgotten): the repetition count resets and the question comes back
  after a short relearning step, within the same study session.

Like Anki, EF is also lowered on lapses (the original SM-2 leaves it unchanged),
so questions that are often missed keep shorter intervals afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

DEFAULT_EASE = 2.5
MIN_EASE = 1.3
PASSING_GRADE = 3
FIRST_INTERVAL_DAYS = 1.0
SECOND_INTERVAL_DAYS = 6.0
MAX_INTERVAL_DAYS = 365.0
RELEARN_STEP = timedelta(minutes=10)

# The UI only reports right/wrong; map that to SM-2 grades ("good" / "hard fail").
GRADE_CORRECT = 4
GRADE_WRONG = 1


@dataclass(frozen=True)
class ReviewState:
    repetitions: int = 0
    interval_days: float = 0.0
    ease_factor: float = DEFAULT_EASE
    lapses: int = 0


@dataclass(frozen=True)
class ReviewOutcome:
    state: ReviewState
    due_at: datetime
    phase: str  # "review" after a success, "relearning" after a lapse


def grade_from_attempt(is_correct: bool, grade: int | None) -> int:
    """SM-2 grade for an attempt. An explicit grade is kept if it agrees with is_correct."""
    if grade is None:
        return GRADE_CORRECT if is_correct else GRADE_WRONG
    if is_correct:
        return max(PASSING_GRADE, min(5, grade))
    return max(0, min(PASSING_GRADE - 1, grade))


def updated_ease(ease_factor: float, grade: int) -> float:
    miss = 5 - grade
    return max(MIN_EASE, ease_factor + 0.1 - miss * (0.08 + miss * 0.02))


def review(state: ReviewState, grade: int, now: datetime) -> ReviewOutcome:
    """Apply one graded answer to `state` and return the new state and due date."""
    if not 0 <= grade <= 5:
        raise ValueError(f"grade must be between 0 and 5, got {grade}")
    ease = updated_ease(state.ease_factor, grade)

    if grade < PASSING_GRADE:
        new_state = ReviewState(repetitions=0, interval_days=0.0, ease_factor=ease, lapses=state.lapses + 1)
        return ReviewOutcome(new_state, now + RELEARN_STEP, "relearning")

    if state.repetitions == 0:
        interval = FIRST_INTERVAL_DAYS
    elif state.repetitions == 1:
        interval = SECOND_INTERVAL_DAYS
    else:
        # The previous interval can be 0 for rows migrated from the old scheduler.
        interval = max(state.interval_days, FIRST_INTERVAL_DAYS) * ease
    interval = min(round(interval, 2), MAX_INTERVAL_DAYS)

    new_state = ReviewState(
        repetitions=state.repetitions + 1, interval_days=interval, ease_factor=ease, lapses=state.lapses
    )
    return ReviewOutcome(new_state, now + timedelta(days=interval), "review")
