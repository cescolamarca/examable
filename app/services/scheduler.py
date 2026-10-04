from datetime import datetime, timedelta, timezone


def next_due_after_attempt(is_correct: bool, prior_lapses: int, prior_reps: int) -> datetime:
    now = datetime.now(tz=timezone.utc)
    if not is_correct:
        # Aggressive repetition after error.
        if prior_lapses == 0:
            return now + timedelta(minutes=10)
        if prior_lapses < 3:
            return now + timedelta(hours=1)
        return now + timedelta(days=1)

    # Simple growth curve for correct answers.
    if prior_reps == 0:
        return now + timedelta(days=1)
    if prior_reps == 1:
        return now + timedelta(days=3)
    if prior_reps == 2:
        return now + timedelta(days=7)
    return now + timedelta(days=min(30, 7 + prior_reps * 2))
