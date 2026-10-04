"""PDF text extraction with quality scoring and a pypdf -> pdfminer -> OCR fallback chain."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass
class ExtractionResult:
    pages: list[str]
    method: str
    warnings: list[str]
    quality_score: float


# Below this score a result is "low quality": try the next extractor.
ACCEPTABLE_QUALITY = 0.45

QUESTION_MARKER_RE = re.compile(
    r"(?im)^\s*\d+\.\s|^\s*\d+\)\s|^\s*DOMANDA\s+\d+|DOMANDA\s+TEORIA|ESERCIZIO\s+\d+"
)
CID_GLYPH_RE = re.compile(r"\(cid:\d+\)")  # unmapped glyphs in pdfminer output
WORD_RE = re.compile(r"[^\W\d_]{2,}|\d+")
COMMON_PUNCTUATION = set(".,;:!?'\"()[]{}-/%+=<>*_\u2019\u201c\u201d")
CHARS_PER_PAGE_WITH_TEXT = 80


def quality_score(pages: list[str]) -> float:
    """Score (0-1) how usable extracted text is, independently of how long it is.

    The previous heuristic was dominated by length (characters / 7000), so a short
    but perfectly extracted exam scored ~0.2 and triggered OCR and the paid LLM
    pass for nothing. The score now combines:

    * readability: share of letters, digits and common punctuation; replacement
      characters, mojibake and pdfminer "(cid:NN)" glyphs lower it;
    * words: share of whitespace-separated tokens that are words or numbers
      (OCR noise produces fragments mixing letters and symbols);
    * structure: whether question markers (DOMANDA n, ESERCIZIO n, "1.") exist;
    * coverage: the share of pages that yielded text. It multiplies the rest, so
      a PDF whose pages are mostly scans still falls through to OCR even when
      the few text pages are clean.
    """
    text = "\n".join(pages)
    chars = re.sub(r"\s+", "", text)
    if not chars:
        return 0.0

    cid_chars = sum(len(m) for m in CID_GLYPH_RE.findall(text))
    readable = sum(1 for ch in chars if ch.isalnum() or ch in COMMON_PUNCTUATION) - cid_chars
    readability = max(0, readable) / len(chars)

    tokens = CID_GLYPH_RE.sub(" \x00 ", text).split()
    words = sum(1 for t in tokens if WORD_RE.fullmatch(t.strip(".,;:!?'\"()[]")))
    word_ratio = words / len(tokens) if tokens else 0.0

    # One blank page (cover or back side) is normal and is not penalised.
    page_text = sum(min(1.0, len(re.sub(r"\s+", "", p)) / CHARS_PER_PAGE_WITH_TEXT) for p in pages)
    coverage = min(1.0, page_text / max(1, len(pages) - 1))
    structure = min(1.0, len(QUESTION_MARKER_RE.findall(text)) / 3)

    return round((0.35 * readability + 0.25 * word_ratio + 0.4 * structure) * coverage, 3)


def _extract_with_pypdf_raw(pdf_path: Path) -> ExtractionResult:
    reader = PdfReader(str(pdf_path), strict=False)
    pages = [page.extract_text() or "" for page in reader.pages]
    warnings: list[str] = []
    quality = quality_score(pages)
    if quality < ACCEPTABLE_QUALITY:
        warnings.append("Low text quality with pypdf extraction")
    return ExtractionResult(pages=pages, method="pypdf", warnings=warnings, quality_score=quality)


def _extract_with_pypdf(pdf_path: Path) -> ExtractionResult:
    # Some malformed PDFs can hang in parser internals. Protect the batch with timeout.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_extract_with_pypdf_raw, pdf_path)
        try:
            return future.result(timeout=20)
        except FuturesTimeoutError:
            return ExtractionResult(
                pages=[],
                method="pypdf_timeout",
                warnings=["pypdf extraction timeout"],
                quality_score=0.0,
            )


def _extract_with_pdfminer_raw(pdf_path: Path) -> ExtractionResult | None:
    try:
        from pdfminer.high_level import extract_text
    except Exception:
        return None

    # Form-feed is a common page separator in pdfminer output.
    raw = extract_text(str(pdf_path)) or ""
    pages = [p for p in raw.split("\f") if p is not None]
    quality = quality_score(pages)
    warnings: list[str] = []
    if quality < ACCEPTABLE_QUALITY:
        warnings.append("Low text quality with pdfminer extraction")
    return ExtractionResult(pages=pages, method="pdfminer", warnings=warnings, quality_score=quality)


def _extract_with_pdfminer(pdf_path: Path) -> ExtractionResult | None:
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_extract_with_pdfminer_raw, pdf_path)
        try:
            return future.result(timeout=20)
        except FuturesTimeoutError:
            return ExtractionResult(
                pages=[],
                method="pdfminer_timeout",
                warnings=["pdfminer extraction timeout"],
                quality_score=0.0,
            )


def _extract_with_ocr_tools(pdf_path: Path, page_count: int) -> ExtractionResult | None:
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        return None

    warnings: list[str] = ["Using OCR fallback (pdftoppm+tesseract)"]
    texts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="examable_ocr_") as tmp:
        tmp_dir = Path(tmp)
        for page_idx in range(1, page_count + 1):
            image_base = tmp_dir / f"page_{page_idx}"
            ppm_cmd = [
                "pdftoppm",
                "-f",
                str(page_idx),
                "-l",
                str(page_idx),
                "-singlefile",
                "-png",
                str(pdf_path),
                str(image_base),
            ]
            ppm_proc = subprocess.run(ppm_cmd, capture_output=True, text=True, check=False)
            if ppm_proc.returncode != 0:
                warnings.append(f"OCR image conversion failed on page {page_idx}")
                texts.append("")
                continue

            image_path = image_base.with_suffix(".png")
            ocr_cmd = ["tesseract", str(image_path), "stdout", "-l", "ita+eng"]
            ocr_proc = subprocess.run(ocr_cmd, capture_output=True, text=True, check=False)
            if ocr_proc.returncode != 0:
                # Try English only if Italian model is unavailable.
                ocr_cmd = ["tesseract", str(image_path), "stdout", "-l", "eng"]
                ocr_proc = subprocess.run(ocr_cmd, capture_output=True, text=True, check=False)
            if ocr_proc.returncode != 0:
                warnings.append(f"OCR failed on page {page_idx}")
                texts.append("")
            else:
                texts.append(ocr_proc.stdout or "")

    quality = quality_score(texts)
    return ExtractionResult(pages=texts, method="ocr", warnings=warnings, quality_score=quality)


def extract_text_pages_with_fallback(pdf_path: Path) -> ExtractionResult:
    attempts: list[ExtractionResult] = []

    pypdf_result = _extract_with_pypdf(pdf_path)
    attempts.append(pypdf_result)
    if pypdf_result.quality_score >= ACCEPTABLE_QUALITY:
        return pypdf_result

    pdfminer_result = _extract_with_pdfminer(pdf_path)
    if pdfminer_result is not None:
        attempts.append(pdfminer_result)

    best = max(attempts, key=lambda x: x.quality_score)
    if best.quality_score >= ACCEPTABLE_QUALITY:
        return best

    ocr_result = _extract_with_ocr_tools(pdf_path, page_count=max(1, len(pypdf_result.pages)))
    if ocr_result is not None:
        attempts.append(ocr_result)

    best = max(attempts, key=lambda x: x.quality_score)
    if best.quality_score < ACCEPTABLE_QUALITY:
        best.warnings.append("Extraction quality is low; manual review recommended")
    return best
