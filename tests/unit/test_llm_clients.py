"""The OpenAI-compatible HTTP calls, against an in-memory transport."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from app.services import corrections, multimodal
from app.services.multimodal import enhance_with_multimodal
from tests.unit.test_multimodal_and_tagging import question


def chat_response(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch):
    """Route httpx clients created by the services to a handler; returns the captured requests."""
    requests: list[dict] = []
    state: dict = {"reply": None}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append({"url": str(request.url), "headers": request.headers, "body": json.loads(request.content)})
        return state["reply"](requests[-1]["body"])

    transport = httpx.MockTransport(handler)
    real_async, real_sync = httpx.AsyncClient, httpx.Client
    monkeypatch.setattr(corrections.httpx, "AsyncClient", lambda **kw: real_async(transport=transport, **kw))
    monkeypatch.setattr(multimodal.httpx, "Client", lambda **kw: real_sync(transport=transport, **kw))
    monkeypatch.setattr(corrections.settings, "multimodal_api_key", "sk-test")
    monkeypatch.setattr(corrections.settings, "multimodal_api_base_url", "https://llm.example/v1/")

    def set_reply(fn):
        state["reply"] = fn

    return requests, set_reply


def test_correction_request_and_response_parsing(provider) -> None:
    requests, set_reply = provider
    set_reply(
        lambda body: chat_response(
            {"items": [{"tid": "q1", "correct_option_id": " B ", "explanation": "Perche' TCP."}, {"tid": ""}, "x"]}
        )
    )
    batch = [
        {"id": "id-1", "question_type": "multiple_choice", "stem": "Quale?", "options": [{"id": "b", "text": "TCP"}]},
        {"id": "id-2", "question_type": "open_text", "stem": "Spiega.", "options": [], "subparts": []},
    ]
    results, tid_to_qid = asyncio.run(corrections._call_correction_llm(batch))

    assert tid_to_qid == {"q1": "id-1", "q2": "id-2"}
    assert results == {"q1": {"correct_option_id": "b", "explanation_text": "Perche' TCP."}}
    sent = requests[0]
    assert sent["url"] == "https://llm.example/v1/chat/completions"
    assert sent["headers"]["authorization"] == "Bearer sk-test"
    assert sent["body"]["response_format"] == {"type": "json_object"}
    assert "temperature" not in sent["body"]  # GPT-5.x models reject it
    user_message = sent["body"]["messages"][1]["content"]
    assert '"tid": "q1"' in user_message and '"options"' in user_message


def test_correction_rejects_malformed_payload(provider) -> None:
    _, set_reply = provider
    set_reply(lambda body: chat_response({"items": "nope"}))
    with pytest.raises(ValueError):
        asyncio.run(corrections._call_correction_llm([{"id": "x", "question_type": "open_text", "stem": "s"}]))


def test_multimodal_pass_repairs_low_quality_extraction(provider, monkeypatch: pytest.MonkeyPatch) -> None:
    requests, set_reply = provider
    monkeypatch.setattr(multimodal.settings, "multimodal_enabled", True)
    monkeypatch.setattr(multimodal, "_extract_page_images", lambda *_: ["aW1n"])
    set_reply(
        lambda body: chat_response(
            {
                "questions": [
                    {
                        "section": "quiz",
                        "number_in_section": 1,
                        "question_type": "multiple_choice",
                        "stem": "Quale protocollo e' orientato alla connessione?",
                        "options": [
                            {"id": oid, "text": t} for oid, t in zip("abcd", ["UDP", "TCP", "IP", "ICMP"], strict=True)
                        ],
                    },
                    {"section": "nonsense"},  # invalid items are dropped
                ]
            }
        )
    )
    weak = [question(1, "Quale protocollo?", options=1)]
    outcome = enhance_with_multimodal(
        pdf_path=None, document_id=uuid4(), pages=["Quale protocollo?"], questions=weak, extraction_quality=0.2
    )

    assert outcome.used and outcome.updated_items == 1
    assert len(outcome.questions[0].options) == 4
    content = requests[0]["body"]["messages"][1]["content"]
    assert content[1]["image_url"]["url"] == "data:image/png;base64,aW1n"


def test_multimodal_skipped_when_quality_is_good(provider, monkeypatch: pytest.MonkeyPatch) -> None:
    requests, _ = provider
    monkeypatch.setattr(multimodal.settings, "multimodal_enabled", True)
    outcome = enhance_with_multimodal(
        pdf_path=None, document_id=uuid4(), pages=["x"], questions=[], extraction_quality=0.95
    )
    assert outcome.used is False
    assert requests == []


def test_multimodal_provider_errors_are_reported_not_raised(provider, monkeypatch: pytest.MonkeyPatch) -> None:
    _, set_reply = provider
    monkeypatch.setattr(multimodal.settings, "multimodal_enabled", True)
    monkeypatch.setattr(multimodal, "_extract_page_images", lambda *_: [])
    set_reply(lambda body: httpx.Response(503, json={"error": "overloaded"}))
    weak = [question(1, "Quale protocollo?", options=1)]
    outcome = enhance_with_multimodal(
        pdf_path=None, document_id=uuid4(), pages=["x"], questions=weak, extraction_quality=0.1
    )
    assert outcome.questions == weak
    assert any("Multimodal provider error" in w for w in outcome.warnings)
