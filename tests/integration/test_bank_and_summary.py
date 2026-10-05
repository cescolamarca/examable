from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import engine


def test_documents_report_how_many_questions_they_contain(client: TestClient, ingested: tuple[str, str]) -> None:
    counts = {d["title"]: d["questions_count"] for d in client.get("/documents").json()}
    # Session B keeps all 4 of its questions even though 2 are owned by session A after deduplication.
    assert counts == {"esame_2024_06_A.pdf": 5, "esame_2024_09_B.pdf": 4}
    assert len(client.get(f"/documents/{ingested[1]}/questions").json()) == 4


def test_bank_search_filters_and_pagination(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    everything = client.get("/questions", params={"user_id": user_id}).json()
    assert everything["total"] == 7
    # Most repeated questions first.
    assert [q["occurrences_count"] for q in everything["items"][:2]] == [2, 2]

    found = client.get("/questions", params={"search": "ipv4"}).json()
    assert {q["stem"] for q in found["items"]} == {
        "Quanti bit compongono un indirizzo IPv4?",
        "Quale record DNS associa un nome di dominio a un indirizzo IPv4?",
    }
    # Matches inside the options too, and LIKE wildcards are taken literally.
    assert client.get("/questions", params={"search": "centoventotto"}).json()["total"] == 1
    assert client.get("/questions", params={"search": "%"}).json()["total"] == 0

    assert client.get("/questions", params={"document_id": ingested[1]}).json()["total"] == 4
    assert client.get("/questions", params={"question_type": "open_text"}).json()["total"] == 2

    page = client.get("/questions", params={"limit": 3, "offset": 6}).json()
    assert (page["total"], len(page["items"])) == (7, 1)


def test_bank_includes_the_users_answer_key(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    question = client.get("/questions", params={"search": "IPv4?"}).json()["items"][0]
    client.put(
        f"/questions/{question['id']}/correction",
        json={"user_id": user_id, "correct_option_id": "a", "explanation_text": "32 bit"},
    )

    corrected = client.get("/questions", params={"user_id": user_id, "correction": "with"}).json()
    assert [q["id"] for q in corrected["items"]] == [question["id"]]
    assert corrected["items"][0]["correction"] == {
        "correct_option_id": "a",
        "explanation_text": "32 bit",
        "has_correction": True,
    }
    assert client.get("/questions", params={"user_id": user_id, "correction": "without"}).json()["total"] == 6


def test_discarded_questions_are_hidden_unless_requested(
    client: TestClient, ingested: tuple[str, str], user_id: str
) -> None:
    question = client.get("/questions").json()["items"][0]
    client.post(f"/questions/{question['id']}/discard")
    assert client.get("/questions").json()["total"] == 6
    assert client.get("/questions", params={"include_discarded": True}).json()["total"] == 7
    discarded = client.get("/questions", params={"only_discarded": True}).json()
    assert [q["id"] for q in discarded["items"]] == [question["id"]]


def test_study_summary(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    summary = client.get(f"/study/summary/{user_id}").json()
    assert (summary["total"], summary["due"], summary["new"], summary["answered_today"]) == (7, 0, 7, 0)
    assert summary["next_due_at"] is None

    first = client.get(f"/study/next/{user_id}").json()["question_id"]
    second = client.get(f"/study/next/{user_id}", params={"exclude_question_id": first}).json()["question_id"]
    client.post("/attempts", json={"user_id": user_id, "question_id": first, "is_correct": True})
    client.post("/attempts", json={"user_id": user_id, "question_id": second, "is_correct": False})

    summary = client.get(f"/study/summary/{user_id}").json()
    assert (summary["new"], summary["due"]) == (5, 0)  # the wrong answer comes back in 10 minutes
    assert (summary["answered_today"], summary["correct_today"]) == (2, 1)
    assert summary["next_due_at"] is not None

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE schedule_state SET due_at = now() - interval '1 minute' WHERE question_id = :q"),
            {"q": second},
        )
        conn.execute(text("UPDATE schedule_state SET interval_days = 30 WHERE question_id = :q"), {"q": first})
    summary = client.get(f"/study/summary/{user_id}").json()
    assert (summary["due"], summary["mastered"]) == (1, 1)

    only_b = client.get(f"/study/summary/{user_id}", params={"document_id": ingested[1]}).json()
    assert only_b["total"] == 4
