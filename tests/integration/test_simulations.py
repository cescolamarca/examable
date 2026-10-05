from __future__ import annotations

from fastapi.testclient import TestClient


def test_custom_simulation_respects_requested_mix(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    response = client.post(
        "/simulations/custom",
        json={"user_id": user_id, "multiple_choice_count": 3, "open_text_count": 5},
    )
    assert response.status_code == 200
    body = response.json()
    types = [q["question_type"] for q in body["questions"]]
    assert types.count("multiple_choice") == 3
    # Only two open questions exist, so the shortfall is reported instead of padded.
    assert types.count("open_text") == 2
    assert body["shortage_by_type"] == {"open_text": 3}
    assert body["simulation_id"]


def test_simulation_requires_at_least_one_question(client: TestClient, ingested: tuple[str, str]) -> None:
    assert client.post("/simulations/custom", json={}).status_code == 400


def test_exhaustive_simulation_filtered_by_document(
    client: TestClient, ingested: tuple[str, str], user_id: str
) -> None:
    _, second = ingested
    body = client.post(
        "/simulations/custom",
        json={"user_id": user_id, "exhaustive": True, "document_ids": [second], "randomize": False},
    ).json()
    # Session B contains 4 questions; 2 of them are owned by session A after deduplication.
    assert body["generated_total"] == 4
    assert {q["id"] for q in body["questions"]} == {
        q["id"] for q in client.get(f"/documents/{second}/questions").json()
    }


def test_tag_filter_accepts_comma_separated_values(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    body = client.post(
        "/simulations/custom",
        json={"user_id": user_id, "exhaustive": True, "tag": "dns, nat"},
    ).json()
    assert body["generated_total"] >= 2
    assert all({"dns", "nat"} & set(q["tags"]) for q in body["questions"])


def test_simulation_history_resume_and_delete(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    created = client.post("/simulations/custom", json={"user_id": user_id, "multiple_choice_count": 2}).json()
    sim_id = created["simulation_id"]
    answered = created["questions"][0]["id"]
    client.post(
        "/attempts",
        json={"user_id": user_id, "question_id": answered, "is_correct": True, "simulation_id": sim_id},
    )

    history = client.get("/simulations", params={"user_id": user_id}).json()
    assert [(s["id"], s["answered_count"], s["correct_count"]) for s in history] == [(sim_id, 1, 1)]

    detail = client.get(f"/simulations/{sim_id}").json()
    assert [q["id"] for q in detail["questions"]] == [q["id"] for q in created["questions"]]
    assert detail["questions"][0]["attempt"]["is_correct"] is True
    assert detail["questions"][1]["attempt"] is None

    assert client.delete(f"/simulations/{sim_id}").status_code == 200
    assert client.get(f"/simulations/{sim_id}").status_code == 404
    assert client.delete(f"/simulations/{sim_id}").status_code == 404


def test_simulation_from_wrong_answers_keeps_order(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    pool = client.post("/simulations/custom", json={"user_id": user_id, "multiple_choice_count": 3}).json()["questions"]
    ids = [q["id"] for q in pool][::-1]
    body = client.post("/simulations/from-questions", json={"user_id": user_id, "question_ids": ids + ids[:1]}).json()
    assert [q["id"] for q in body["questions"]] == ids


def test_priority_modes(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    pool = client.post("/simulations/custom", json={"user_id": user_id, "exhaustive": True, "randomize": False}).json()[
        "questions"
    ]
    mistaken = pool[0]["id"]
    client.post("/attempts", json={"user_id": user_id, "question_id": mistaken, "is_correct": False})

    body = client.post(
        "/simulations/custom",
        json={"user_id": user_id, "exhaustive": True, "priority_mode": "frequently_mistaken"},
    ).json()
    assert body["questions"][0]["id"] == mistaken

    body = client.post(
        "/simulations/custom",
        json={"user_id": user_id, "exhaustive": True, "priority_mode": "never_viewed"},
    ).json()
    assert body["questions"][-1]["id"] == mistaken

    response = client.post("/simulations/custom", json={"exhaustive": True, "priority_mode": "never_viewed"})
    assert response.status_code == 400
