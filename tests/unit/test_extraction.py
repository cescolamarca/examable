from __future__ import annotations

from pathlib import Path

import pytest

from app.services import extraction
from app.services.extraction import ACCEPTABLE_QUALITY, ExtractionResult, quality_score
from tests.sample_exams import SESSION_A, write_pdf

EXAM_PAGE = "\n".join(SESSION_A)


def test_short_clean_exam_scores_high() -> None:
    # Regression: the old length-based score gave this page ~0.3 and sent it to OCR.
    assert quality_score([EXAM_PAGE]) > 0.9


@pytest.mark.parametrize(
    "pages",
    [
        [""],
        ["   \n  ", ""],
        ["(cid:12)(cid:45)(cid:3) (cid:77)(cid:9) " * 40],  # unmapped font glyphs
        [" ~|l1 ;.,` ^^ ~~ I|I \\ /.. ~ " * 30],  # OCR noise
        ["Ã¨ lâ€™indirizzo Ã  ¿Â§ " * 40],  # mojibake
    ],
)
def test_unusable_text_scores_below_threshold(pages: list[str]) -> None:
    assert quality_score(pages) < ACCEPTABLE_QUALITY


def test_mostly_scanned_document_is_sent_to_ocr() -> None:
    assert quality_score([EXAM_PAGE, "", "", ""]) < ACCEPTABLE_QUALITY


def test_a_single_blank_page_is_tolerated() -> None:
    assert quality_score([EXAM_PAGE, EXAM_PAGE, ""]) == quality_score([EXAM_PAGE, EXAM_PAGE])


def test_clean_text_without_question_markers_is_accepted_but_not_perfect() -> None:
    score = quality_score(["Testo pulito ma senza alcun marcatore di domanda riconoscibile. " * 20])
    assert ACCEPTABLE_QUALITY <= score < 0.72


def test_real_pdf_is_extracted_with_pypdf(tmp_path: Path) -> None:
    result = extraction.extract_text_pages_with_fallback(write_pdf(tmp_path / "exam.pdf", SESSION_A))
    assert result.method == "pypdf"
    assert result.warnings == []
    assert "DOMANDA 1" in result.pages[0]


def _result(method: str, score: float) -> ExtractionResult:
    return ExtractionResult(pages=[method], method=method, warnings=[], quality_score=score)


def test_fallback_chain_returns_the_best_extractor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake(method: str, score: float):
        def run(*_args, **_kwargs):
            calls.append(method)
            return _result(method, score)

        return run

    monkeypatch.setattr(extraction, "_extract_with_pypdf", fake("pypdf", 0.2))
    monkeypatch.setattr(extraction, "_extract_with_pdfminer", fake("pdfminer", 0.8))
    monkeypatch.setattr(extraction, "_extract_with_ocr_tools", fake("ocr", 0.9))
    assert extraction.extract_text_pages_with_fallback(tmp_path / "x.pdf").method == "pdfminer"
    assert calls == ["pypdf", "pdfminer"]  # good enough: OCR is not attempted

    calls.clear()
    monkeypatch.setattr(extraction, "_extract_with_pdfminer", fake("pdfminer", 0.1))
    assert extraction.extract_text_pages_with_fallback(tmp_path / "x.pdf").method == "ocr"
    assert calls == ["pypdf", "pdfminer", "ocr"]


def test_low_quality_everywhere_is_flagged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(extraction, "_extract_with_pypdf", lambda *_: _result("pypdf", 0.3))
    monkeypatch.setattr(extraction, "_extract_with_pdfminer", lambda *_: _result("pdfminer", 0.1))
    monkeypatch.setattr(extraction, "_extract_with_ocr_tools", lambda *_a, **_k: None)  # tesseract missing
    result = extraction.extract_text_pages_with_fallback(tmp_path / "x.pdf")
    assert result.method == "pypdf"
    assert "manual review recommended" in result.warnings[-1]
