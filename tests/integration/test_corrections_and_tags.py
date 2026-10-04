from __future__ import annotations

from fastapi.testclient import TestClient


def _first_mcq(client: TestClient, document_id: str) -> dict:
    return next(
        q for q in client.get(f"/documents/{document_id}/questions").json() if q["question_type"] == "multiple_choice"
    )


def test_correction_roundtrip_and_coverage(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    first, _ = ingested
    question = _first_mcq(client, first)
    url = f"/questions/{question['id']}/correction"

    assert client.get(url, params={"user_id": user_id}).json()["has_correction"] is False
    assert client.put(url, json={"user_id": user_id}).status_code == 400

    saved = client.put(url, json={"user_id": user_id, "correct_option_id": "b"}).json()
    assert saved["correct_option_id"] == "b"
    # A later edit that only adds an explanation keeps the chosen option.
    client.put(url, json={"user_id": user_id, "explanation_text": "TCP e' orientato alla connessione."})
    stored = client.get(url, params={"user_id": user_id}).json()
    assert stored["correct_option_id"] == "b"
    assert stored["explanation_text"].startswith("TCP")

    coverage = client.get("/corrections/coverage", params={"user_id": user_id, "document_id": first}).json()
    assert coverage["with_correction"] == 1
    assert coverage["total"] == len(client.get(f"/documents/{first}/questions").json())

    stats = client.get(f"/reviews/stats/{user_id}").json()
    assert stats["with_correction"] == 1
    assert stats["with_correct_option"] == 1
    assert stats["total"] == 7

    only_corrected = client.post(
        "/simulations/custom",
        json={"user_id": user_id, "exhaustive": True, "only_reviewed_correct": True},
    ).json()
    assert [q["id"] for q in only_corrected["questions"]] == [question["id"]]


def test_ai_features_report_missing_configuration(client: TestClient, ingested: tuple[str, str], user_id: str) -> None:
    question = _first_mcq(client, ingested[0])
    response = client.post(f"/questions/{question['id']}/correction/regenerate", json={"user_id": user_id})
    assert response.status_code == 400
    response = client.post("/corrections/jobs", json={"user_id": user_id, "mode": "frequency"})
    assert response.status_code == 400
    assert client.get("/corrections/jobs/current").status_code == 204
    assert client.get("/corrections/jobs/recent").json() == []


def test_manual_tags(client: TestClient, ingested: tuple[str, str]) -> None:
    question = _first_mcq(client, ingested[0])
    created = client.post("/tags", json={"name": "Livello di trasporto"}).json()
    assert created["slug"] == "livello-di-trasporto"

    response = client.put(f"/questions/{question['id']}/tags", json={"tags": ["Livello di trasporto", "tcp"]})
    assert response.json() == {"status": "ok", "tags_set": 2}
    tags = client.get(f"/questions/{question['id']}/tags").json()
    assert {t["slug"] for t in tags} == {"livello-di-trasporto", "tcp"}
    assert {t["source"] for t in tags} == {"manual"}

    assert [t["slug"] for t in client.get("/tags", params={"query": "trasporto"}).json()] == ["livello-di-trasporto"]


def test_rule_based_tagging_and_presets(client: TestClient, ingested: tuple[str, str]) -> None:
    result = client.post(f"/tagging/recompute/document/{ingested[0]}").json()
    assert result["questions_tagged"] == 5
    assert result["questions_tagged_ai"] == 0

    presets = {p["slug"]: p for p in client.get("/tag-presets").json()}
    assert "modulo-1" in presets
    assert "tcp" in presets["modulo-1"]["tags"]
