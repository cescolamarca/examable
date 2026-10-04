from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.scheduler import (
    MIN_EASE,
    RELEARN_STEP,
    ReviewState,
    grade_from_attempt,
    review,
    updated_ease,
)

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


def run(grades: list[int]) -> list[float]:
    state, intervals = ReviewState(), []
    for grade in grades:
        outcome = review(state, grade, NOW)
        state = outcome.state
        intervals.append(state.interval_days)
    return intervals


def test_intervals_follow_sm2_for_good_answers() -> None:
    # Grade 4 keeps EF at 2.5: 1, 6, 15, 37.5 days.
    assert run([4, 4, 4, 4]) == [1.0, 6.0, 15.0, 37.5]


def test_easy_answers_grow_faster_than_hard_ones() -> None:
    easy = run([5, 5, 5, 5])
    hard = run([3, 3, 3, 3])
    assert easy[-1] > run([4, 4, 4, 4])[-1] > hard[-1]


def test_lapse_resets_repetitions_and_relearns_soon() -> None:
    state = ReviewState(repetitions=3, interval_days=15.0, ease_factor=2.5)
    outcome = review(state, 1, NOW)
    assert outcome.phase == "relearning"
    assert outcome.due_at == NOW + RELEARN_STEP
    assert (outcome.state.repetitions, outcome.state.interval_days, outcome.state.lapses) == (0, 0.0, 1)
    assert outcome.state.ease_factor < 2.5

    # Relearning starts again from the first interval.
    again = review(outcome.state, 4, NOW)
    assert (again.phase, again.state.interval_days, again.due_at) == ("review", 1.0, NOW + timedelta(days=1))


def test_frequently_missed_questions_get_shorter_intervals() -> None:
    assert run([1, 4, 4, 4])[-1] < run([4, 4, 4])[-1]


def test_ease_factor_is_floored() -> None:
    assert updated_ease(1.3, 0) == MIN_EASE
    assert updated_ease(2.5, 4) == pytest.approx(2.5)
    assert updated_ease(2.5, 5) == pytest.approx(2.6)


def test_interval_is_capped_and_legacy_zero_intervals_recover() -> None:
    assert review(ReviewState(repetitions=9, interval_days=300, ease_factor=2.5), 5, NOW).state.interval_days == 365
    # Rows migrated from the old scheduler may have reps >= 2 but no interval.
    assert review(ReviewState(repetitions=4, interval_days=0.0), 4, NOW).state.interval_days == 2.5


@pytest.mark.parametrize(
    ("is_correct", "grade", "expected"),
    [(True, None, 4), (False, None, 1), (True, 5, 5), (True, 1, 3), (False, 4, 2), (False, 0, 0)],
)
def test_grade_from_attempt(is_correct: bool, grade: int | None, expected: int) -> None:
    assert grade_from_attempt(is_correct, grade) == expected


def test_invalid_grade() -> None:
    with pytest.raises(ValueError):
        review(ReviewState(), 6, NOW)
