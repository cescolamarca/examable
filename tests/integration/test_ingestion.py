from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import upload_and_process
from tests.sample_exams import SESSION_C, write_scanned_pdf


def _questions(client: TestClient, document_id: str) -> list[dict]:
    response = client.get(f"/documents/{document_id}/questions")
    assert response.status_code == 200
    return response.json()


def test_health_and_pages(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    for path in ("/", "/study", "/ingest"):
        response = client.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


def test_process_extracts_every_question_type(client: TestClient, sample_exams: tuple[Path, Path]) -> None:
    document_id = upload_and_process(client, sample_exams[0])

    questions = _questions(client, document_id)
    by_type = sorted(q["question_type"] for q in questions)
    assert by_type == ["multi_part_open", "multiple_choice", "multiple_choice", "multiple_choice", "open_text"]

    mcq = next(q for q in questions if q["stem"].startswith("Quale protocollo"))
    assert [o["id"] for o in mcq["options"]] == ["a", "b", "c", "d"]
    assert "tcp" in mcq["tags"]

    exercise = next(q for q in questions if q["question_type"] == "multi_part_open")
    assert [s["id"] for s in exercise["subparts"]] == ["1", "2"]

    documents = client.get("/documents").json()
    assert documents[0]["ingestion_status"] == "processed"
    assert documents[0]["pages"] == 1


def test_repeated_questions_are_merged_across_sessions(client: TestClient, ingested: tuple[str, str]) -> None:
    first, second = ingested
    # Each session lists all its questions: 5 + 4, two of which appear in both sessions...
    assert (len(_questions(client, first)), len(_questions(client, second))) == (5, 4)
    # ...but the bank stores them once.
    assert client.get("/questions").json()["total"] == 7

    report = client.get("/reports/question-occurrences").json()
    repeated = [item for item in report["items"] if item["occurrences_count"] == 2]
    assert len(repeated) == 2
    for item in repeated:
        assert sorted(item["source_files"]) == ["esame_2024_06_A.pdf", "esame_2024_09_B.pdf"]
        assert len(item["occurrences"]) == 2


def test_dedupe_is_idempotent(client: TestClient, ingested: tuple[str, str]) -> None:
    report = client.post("/admin/cleanup-dedupe").json()
    assert report["duplicate_groups_merged"] == 0
    assert report["total_questions_after"] == 7
    assert report["rows_normalised"] == 0  # nothing left to rewrite


def test_duplicate_upload_is_rejected_without_leaving_files(
    client: TestClient, sample_exams: tuple[Path, Path], tmp_path: Path
) -> None:
    from app.config import settings

    upload_and_process(client, sample_exams[0])
    files_before = set(Path(settings.upload_dir).iterdir())

    with sample_exams[0].open("rb") as fh:
        response = client.post("/documents", files={"file": ("copy.pdf", fh, "application/pdf")})

    assert response.status_code == 409
    assert set(Path(settings.upload_dir).iterdir()) == files_before


def test_upload_rejects_non_pdf(client: TestClient) -> None:
    response = client.post("/documents", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 400

    response = client.post("/documents", files={"file": ("fake.pdf", b"not a pdf", "application/pdf")})
    assert response.status_code == 400


def test_kpis(client: TestClient, ingested: tuple[str, str]) -> None:
    kpi = client.get("/stats/kpi").json()
    assert kpi["total_documents"] == 2
    assert kpi["processed_documents"] == 2
    assert 0 < kpi["avg_quality"] <= 1


def test_deleting_a_session_keeps_questions_shared_with_other_sessions(
    client: TestClient, ingested: tuple[str, str], user_id: str
) -> None:
    first, second = ingested
    shared = next(q for q in _questions(client, first) if q["occurrences_count"] == 2)
    client.put(f"/questions/{shared['id']}/correction", json={"user_id": user_id, "correct_option_id": "b"})

    response = client.post("/admin/delete-documents", json={"document_ids": [first, first]})
    assert response.status_code == 200
    # Session A had 5 questions: the 2 shared with B survive, 3 are deleted.
    assert [d["questions"] for d in response.json()["deleted"]] == [3]
    assert response.json()["missing"] == [first]
    assert [d["id"] for d in client.get("/documents").json()] == [second]

    remaining = _questions(client, second)
    assert len(remaining) == 4
    moved = next(q for q in remaining if q["id"] == shared["id"])
    assert (moved["occurrences_count"], moved["source_files"]) == (1, ["esame_2024_09_B.pdf"])
    # Progress on the shared question is preserved.
    correction = client.get(f"/questions/{shared['id']}/correction", params={"user_id": user_id}).json()
    assert correction["correct_option_id"] == "b"


def test_reprocessing_a_session_is_idempotent(client: TestClient, ingested: tuple[str, str]) -> None:
    first, second = ingested
    assert client.post(f"/documents/{first}/process").status_code == 200
    assert (len(_questions(client, first)), len(_questions(client, second))) == (5, 4)
    assert client.get("/questions").json()["total"] == 7
    report = client.get("/reports/question-occurrences").json()
    assert sorted(i["occurrences_count"] for i in report["items"]) == [1, 1, 1, 1, 1, 2, 2]


@pytest.mark.skipif(not (shutil.which("pdftoppm") and shutil.which("tesseract")), reason="needs OCR tools")
def test_scanned_session_is_deduplicated_against_text_sessions(
    client: TestClient, ingested: tuple[str, str], tmp_path: Path
) -> None:
    scan = write_scanned_pdf(tmp_path / "reti_2023_02_scansione.pdf", SESSION_C)
    document_id = upload_and_process(client, scan)
    assert client.get("/documents").json()[0]["id"] == document_id

    report = client.get("/reports/question-occurrences").json()
    # 5 + 4 + 4 extracted questions; the scan repeats one question of each text session.
    assert report["total_questions"] == 9
    repeated = {item["stem_preview"].split()[1] for item in report["items"] if item["occurrences_count"] == 2}
    assert repeated == {"protocollo", "bit", "record", "meccanismo"}
