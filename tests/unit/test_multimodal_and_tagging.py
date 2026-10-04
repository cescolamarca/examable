from __future__ import annotations

from uuid import uuid4

from app.schemas import OptionItem, Quality, QuestionOut, SourceLoc
from app.services import multimodal
from app.services.tagging import _rule_suggest_tags, manual_tag_slug, slugify


def question(number: int, stem: str, options: int = 0, confidence: float = 0.6) -> QuestionOut:
    return QuestionOut(
        question_id=uuid4(),
        document_id=uuid4(),
        section="quiz",
        number_in_section=number,
        question_type="multiple_choice",
        stem=stem,
        options=[OptionItem(id="abcd"[i], text=f"opzione {i}") for i in range(options)],
        tags=["reti"],
        source_loc=SourceLoc(page_start=1, page_end=1),
        quality=Quality(confidence=confidence, needs_review=True),
    )


def test_llm_candidates_only_fill_gaps() -> None:
    existing = [question(1, "Quale protocollo?", options=2), question(2, "Domanda completa e corretta", options=4)]
    candidates = [
        question(1, "Quale protocollo di trasporto e' affidabile?", options=4),
        question(2, "Breve", options=3),
        question(3, "Domanda persa dal parser", options=4),
    ]
    merged, updates = multimodal._merge_questions(existing, candidates)

    by_number = {q.number_in_section: q for q in merged}
    assert len(by_number[1].options) == 4  # missing options recovered
    assert by_number[1].stem.startswith("Quale protocollo di trasporto")
    assert by_number[1].quality.needs_review is False
    assert by_number[2].stem == "Domanda completa e corretta"  # shorter LLM text ignored
    assert len(by_number[2].options) == 4
    assert 3 in by_number  # question the parser missed is added
    assert updates == 2


def test_multimodal_pass_is_skipped_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(multimodal.settings, "multimodal_enabled", False)
    questions = [question(1, "Stem", options=4)]
    outcome = multimodal.enhance_with_multimodal(
        pdf_path=None, document_id=uuid4(), pages=["x"], questions=questions, extraction_quality=0.1
    )
    assert outcome.used is False
    assert outcome.questions is questions


def test_rule_tags_score_by_keyword_matches() -> None:
    tags = dict(_rule_suggest_tags("Il three-way handshake di TCP usa ACK", [], []))
    assert tags["tcp"] > tags["reti"]
    assert "dns" not in tags


def test_slug_helpers() -> None:
    assert slugify("Qualità del Servizio (QoS)") == "qualita-del-servizio-qos"
    assert manual_tag_slug("  Qualità del Servizio ") == "qualità-del-servizio"
