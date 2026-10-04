from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import engine


def _schedule(user_id: str, question_id: str) -> dict:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM schedule_state WHERE user_id = :u AND question_id = :q"),
            {"u": user_id, "q": question_id},
        ).mappings().one()
    return dict(row)


def test_default_user_is_stable(client: TestClient) -> None:
    first = client.get("/users/default").json()
    assert client.get("/users/default").json() == first


def test_next_question_prefers_due_then_new(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    first = client.get(f"/study/next/{user_id}").json()
    assert first["due_reason"] == "new"

    # A wrong answer is scheduled again within minutes, a correct one days later.
    client.post("/attempts", json={"user_id": user_id, "question_id": first["question_id"], "is_correct": False})
    state = _schedule(user_id, first["question_id"])
    assert state["lapses"] == 1
    assert state["due_at"] - datetime.now(tz=timezone.utc) < timedelta(hours=1)

    second = client.get(f"/study/next/{user_id}", params={"exclude_question_id": first["question_id"]}).json()
    assert second["question_id"] != first["question_id"]
    client.post("/attempts", json={"user_id": user_id, "question_id": second["question_id"], "is_correct": True})
    assert _schedule(user_id, second["question_id"])["due_at"] - datetime.now(tz=timezone.utc) > timedelta(hours=12)

    # Force the failed question to be due: it must come back before unseen ones.
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE schedule_state SET due_at = now() - interval '1 minute' WHERE question_id = :q"),
            {"q": first["question_id"]},
        )
    assert client.get(f"/study/next/{user_id}").json() == {"question_id": first["question_id"], "due_reason": "due"}


def test_next_question_filters(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    response = client.get(f"/study/next/{user_id}", params={"question_type": "multi_part_open"})
    question = client.get(f"/questions/{response.json()['question_id']}").json()
    assert question["question_type"] == "multi_part_open"

    response = client.get(f"/study/next/{user_id}", params={"tag": "dns", "prefer_new": True})
    question = client.get(f"/questions/{response.json()['question_id']}").json()
    assert "dns" in question["tags"]

    response = client.get(f"/study/next/{user_id}", params={"tag": "does-not-exist"})
    assert response.status_code == 404


def test_attempt_stats(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    question_id = client.get(f"/study/next/{user_id}").json()["question_id"]
    for correct in (True, False, True):
        client.post("/attempts", json={"user_id": user_id, "question_id": question_id, "is_correct": correct})
    stats = client.get(f"/attempts/stats/{user_id}").json()
    assert stats == [{"question_id": question_id, "total_attempts": 3, "correct_attempts": 2}]


def test_discarded_questions_are_never_served(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    question_id = client.get(f"/study/next/{user_id}", params={"question_type": "open_text"}).json()["question_id"]
    assert client.post(f"/questions/{question_id}/discard").json()["is_discarded"] is True

    for _ in range(3):
        served = client.get(f"/study/next/{user_id}", params={"question_type": "open_text"})
        if served.status_code == 404:
            break
        assert served.json()["question_id"] != question_id

    client.post(f"/questions/{question_id}/discard", params={"discarded": False})
    assert client.get(f"/questions/{question_id}").json()["is_discarded"] is False
