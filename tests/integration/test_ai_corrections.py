"""Correction jobs end to end, with the LLM call replaced by a deterministic fake."""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import corrections


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[list[str]]]:
    """Install a fake LLM; returns the list of batches it received (question stems)."""
    monkeypatch.setattr(settings, "multimodal_api_key", "test-key")
    batches: list[list[str]] = []

    def install(*, skip: str | None = None, fail: bool = False, delay: float = 0.0):
        async def fake_call(batch: list[dict]):
            import asyncio

            await asyncio.sleep(delay)
            batches.append([q["stem"] for q in batch])
            if fail:
                raise RuntimeError("provider unavailable")
            results, tid_to_qid = {}, {}
            for idx, q in enumerate(batch, start=1):
                tid = f"q{idx}"
                tid_to_qid[tid] = q["id"]
                if skip and skip in q["stem"]:
                    continue  # the model "forgets" this item
                # "z" is not an option of any question: it must be discarded, keeping the explanation.
                option = "z" if "DNS" in q["stem"] else "b"
                results[tid] = {"correct_option_id": option, "explanation_text": f"Spiegazione {idx}"}
            return results, tid_to_qid

        monkeypatch.setattr(corrections, "_call_correction_llm", fake_call)
        return batches

    return install


def wait_for(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/corrections/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def start(client: TestClient, user_id: str, **body) -> dict:
    response = client.post("/corrections/jobs", json={"user_id": user_id, **body})
    assert response.status_code == 200, response.text
    return response.json()


def test_frequency_job_corrects_the_whole_bank(
    client: TestClient, ingested: tuple[str, str], user_id: str, fake_llm
) -> None:
    batches = fake_llm(skip="congestione")
    job = wait_for(client, start(client, user_id, mode="frequency")["id"])

    assert job["status"] == "done"
    assert (job["total_questions"], job["processed_count"]) == (7, 7)
    assert (job["succeeded_count"], job["failed_count"]) == (6, 1)
    # Batch size 5: the most repeated questions are sent first.
    assert [len(b) for b in batches] == [5, 2]
    assert set(batches[0][:2]) == {
        "Quale protocollo del livello di trasporto offre un servizio orientato alla connessione?",
        "Quanti bit compongono un indirizzo IPv4?",
    }

    failures = client.get(f"/corrections/jobs/{job['id']}/failures").json()
    assert [f["error"] for f in failures] == ["LLM did not return an answer for this item"]
    assert "congestione" in failures[0]["stem_preview"]

    questions = {q["stem"]: q for d in ingested for q in client.get(f"/documents/{d}/questions").json()}
    dns = questions["Quale record DNS associa un nome di dominio a un indirizzo IPv4?"]
    dns_correction = client.get(f"/questions/{dns['id']}/correction", params={"user_id": user_id}).json()
    assert dns_correction["correct_option_id"] is None  # invented option id dropped
    assert dns_correction["explanation_text"].startswith("Spiegazione")

    # Everything that succeeded is now covered; a second job has nothing left but the failure.
    assert client.get("/corrections/coverage", params={"user_id": user_id}).json()["without_correction"] == 1
    assert client.get("/corrections/jobs/recent").json()[0]["id"] == job["id"]


def test_manual_corrections_are_not_overwritten_unless_requested(
    client: TestClient, ingested: tuple[str, str], user_id: str, fake_llm
) -> None:
    fake_llm()
    first, _ = ingested
    question = client.get(f"/documents/{first}/questions").json()[0]
    url = f"/questions/{question['id']}/correction"
    client.put(url, json={"user_id": user_id, "explanation_text": "Scritta a mano"})

    job = wait_for(client, start(client, user_id, mode="document", document_id=first)["id"])
    assert job["total_questions"] == 4  # the manually corrected question is skipped
    assert client.get(url, params={"user_id": user_id}).json()["explanation_text"] == "Scritta a mano"

    job = wait_for(client, start(client, user_id, mode="document", document_id=first, overwrite=True)["id"])
    assert job["total_questions"] == 5
    assert client.get(url, params={"user_id": user_id}).json()["explanation_text"].startswith("Spiegazione")


def test_provider_errors_fail_the_batch_not_the_job(
    client: TestClient, ingested: tuple[str, str], user_id: str, fake_llm
) -> None:
    fake_llm(fail=True)
    job = wait_for(client, start(client, user_id, mode="frequency")["id"])
    assert (job["status"], job["failed_count"], job["succeeded_count"]) == ("done", 7, 0)
    errors = {f["error"] for f in client.get(f"/corrections/jobs/{job['id']}/failures").json()}
    assert errors == {"LLM batch error: provider unavailable"}


def test_only_one_job_runs_at_a_time_and_jobs_can_be_cancelled(
    client: TestClient, ingested: tuple[str, str], user_id: str, fake_llm
) -> None:
    fake_llm(delay=0.3)
    job = start(client, user_id, mode="frequency")

    conflict = client.post("/corrections/jobs", json={"user_id": user_id, "mode": "frequency"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {"code": "another_job_running", "job_id": job["id"]}
    assert client.get("/corrections/jobs/current").json()["id"] == job["id"]

    assert client.post(f"/corrections/jobs/{job['id']}/cancel").json()["cancel_requested"] is True
    finished = wait_for(client, job["id"])
    assert finished["status"] == "cancelled"
    assert finished["processed_count"] < 7
    # Cancelling a finished job is a no-op that returns its state.
    assert client.post(f"/corrections/jobs/{job['id']}/cancel").json()["status"] == "cancelled"


def test_job_request_validation(client: TestClient, ingested: tuple[str, str], user_id: str, fake_llm) -> None:
    fake_llm()
    bad = [
        {"mode": "document"},
        {"mode": "frequency", "document_id": ingested[0]},
        {"mode": "frequency", "overwrite": True},
    ]
    for body in bad:
        assert client.post("/corrections/jobs", json={"user_id": user_id, **body}).status_code == 400
    unknown = "00000000-0000-0000-0000-000000000000"
    assert client.post("/corrections/jobs", json={"user_id": unknown, "mode": "frequency"}).status_code == 404
    assert client.get(f"/corrections/jobs/{unknown}").status_code == 404


def test_regenerate_single_question(client: TestClient, ingested: tuple[str, str], user_id: str, fake_llm) -> None:
    fake_llm()
    question = next(
        q for q in client.get(f"/documents/{ingested[0]}/questions").json() if q["stem"].startswith("Quale protocollo")
    )
    response = client.post(f"/questions/{question['id']}/correction/regenerate", json={"user_id": user_id})
    assert response.status_code == 200
    assert response.json()["correct_option_id"] == "b"
    stored = client.get(f"/questions/{question['id']}/correction", params={"user_id": user_id}).json()
    assert stored["correct_option_id"] == "b"
