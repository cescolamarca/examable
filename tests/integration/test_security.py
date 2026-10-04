from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings

TOKEN = "s3cret-token"


@pytest.fixture
def admin_token(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    yield TOKEN


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/admin/reset-db"),
        ("post", "/admin/cleanup-dedupe"),
        ("post", "/documents/00000000-0000-0000-0000-000000000000/process"),
        ("post", "/tagging/recompute/document/00000000-0000-0000-0000-000000000000"),
        ("post", "/corrections/jobs/00000000-0000-0000-0000-000000000000/cancel"),
    ],
)
def test_protected_endpoints_require_the_token(client: TestClient, admin_token: str, method: str, path: str) -> None:
    assert client.request(method, path).status_code == 401
    assert client.request(method, path, headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert client.request(method, path, headers={"X-Admin-Token": admin_token}).status_code not in (401, 403)


def test_upload_with_token(client: TestClient, admin_token: str, sample_exams: tuple[Path, Path]) -> None:
    pdf = sample_exams[0]
    with pdf.open("rb") as fh:
        files = {"file": (pdf.name, fh.read(), "application/pdf")}
    assert client.post("/documents", files=files).status_code == 401
    assert client.post("/documents", files=files, headers={"X-Admin-Token": admin_token}).status_code == 200


def test_study_endpoints_stay_open(client: TestClient, ingested: tuple[str, str], admin_token: str) -> None:
    user_id = client.get("/users/default").json()["id"]
    assert client.get(f"/study/next/{user_id}").status_code == 200
    assert client.post("/simulations/custom", json={"multiple_choice_count": 1}).status_code == 200


def test_reset_db_clears_data_and_reseeds_tags(client: TestClient, ingested: tuple[str, str]) -> None:
    assert client.post("/admin/reset-db").status_code == 200
    assert client.get("/documents").json() == []
    assert any(t["slug"] == "tcp" for t in client.get("/tags").json())
    assert {p["slug"] for p in client.get("/tag-presets").json()} >= {"modulo-1", "intercorso-1"}


def test_question_bank_import(client: TestClient) -> None:
    bank = {
        "questions": [
            {
                "question_type": "multiple_choice",
                "section": "quiz",
                "stem": "Quale protocollo usa la porta 53?",
                "options": [{"id": "a", "text": "DNS"}, {"id": "b", "text": "HTTP"}],
                "fingerprint": "f" * 40,
                "tags": ["DNS", "Livello applicazione"],
                "occurrences": [{"source_file": "intercorso_2021.pdf", "section": "quiz", "number_in_section": 4}],
            },
            {"stem": "   "},
        ]
    }
    import json

    raw = json.dumps(bank).encode()
    response = client.post("/admin/load-question-bank", files={"file": ("bank.json", raw, "application/json")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["inserted_questions"], body["inserted_occurrences"], body["inserted_question_tags"]) == (1, 1, 2)

    # Importing the same file again replaces the document instead of duplicating it.
    again = client.post("/admin/load-question-bank", files={"file": ("bank.json", raw, "application/json")}).json()
    assert again["document_id"] == body["document_id"]
    assert len(client.get(f"/documents/{body['document_id']}/questions").json()) == 1

    bad = client.post("/admin/load-question-bank", files={"file": ("bank.json", b"{}", "application/json")})
    assert bad.status_code == 400
