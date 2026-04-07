from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import UUID, uuid4

from pypdf import PdfReader

from app.schemas import QuestionOut, Quality, SourceLoc

MCQ_START_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")
OPTION_RE = re.compile(r"^\s*([a-d])\.\s+(.*)$", re.IGNORECASE)
EXERCISE_RE = re.compile(r"^\s*ESERCIZIO\s+(\d+)", re.IGNORECASE)
THEORY_RE = re.compile(r"^\s*DOMANDA\s+TEORIA", re.IGNORECASE)
SUBPART_RE = re.compile(r"^\s*(\d+)\)\s+(.*)$")


@dataclass
class ExtractionResult:
    pages: list[str]
    method: str
    warnings: list[str]
    quality_score: float


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quality_score(pages: list[str]) -> float:
    text = "\n".join(pages)
    compact = re.sub(r"\s+", "", text)
    marker_count = len(
        re.findall(r"(?im)^\s*\d+\.\s|DOMANDA\s+TEORIA|ESERCIZIO\s+\d+", text)
    )
    length_score = min(1.0, len(compact) / 7000.0)
    marker_score = min(1.0, marker_count / 10.0)
    return round(0.65 * length_score + 0.35 * marker_score, 3)


def _extract_with_pypdf_raw(pdf_path: Path) -> ExtractionResult:
    reader = PdfReader(str(pdf_path), strict=False)
    pages = [page.extract_text() or "" for page in reader.pages]
    warnings: list[str] = []
    quality = _quality_score(pages)
    if quality < 0.35:
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
    quality = _quality_score(pages)
    warnings: list[str] = []
    if quality < 0.35:
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

    quality = _quality_score(texts)
    return ExtractionResult(pages=texts, method="ocr", warnings=warnings, quality_score=quality)


def extract_text_pages_with_fallback(pdf_path: Path) -> ExtractionResult:
    attempts: list[ExtractionResult] = []

    pypdf_result = _extract_with_pypdf(pdf_path)
    attempts.append(pypdf_result)
    if pypdf_result.quality_score >= 0.45:
        return pypdf_result

    pdfminer_result = _extract_with_pdfminer(pdf_path)
    if pdfminer_result is not None:
        attempts.append(pdfminer_result)

    best = max(attempts, key=lambda x: x.quality_score)
    if best.quality_score >= 0.45:
        return best

    ocr_result = _extract_with_ocr_tools(pdf_path, page_count=max(1, len(pypdf_result.pages)))
    if ocr_result is not None:
        attempts.append(ocr_result)

    best = max(attempts, key=lambda x: x.quality_score)
    if best.quality_score < 0.45:
        best.warnings.append("Extraction quality is low; manual review recommended")
    return best


def extract_text_pages(pdf_path: Path) -> list[str]:
    # Backward-compatible wrapper.
    return extract_text_pages_with_fallback(pdf_path).pages


def _clean_lines(raw_text: str) -> list[str]:
    lines = []
    for line in raw_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("-- ") and " of " in s:
            continue
        if s.lower().startswith("studente"):
            continue
        if "cognome" in s.lower() and "matricola" in s.lower():
            continue
        if s.lower().startswith("risposte"):
            continue
        if s.lower() in {"corrette", "teoria", "tot"}:
            continue
        lines.append(s)
    return lines


def _tag_question(stem: str) -> list[str]:
    s = stem.lower()
    tags = {"reti"}
    mapping = {
        "tcp": "tcp",
        "udp": "udp",
        "dns": "dns",
        "http": "http",
        "smtp": "smtp",
        "dhcp": "dhcp",
        "icmp": "icmp",
        "ipv4": "ipv4",
        "ipv6": "ipv6",
        "ethernet": "ethernet",
        "go-back-n": "arq",
        "stop-and-wait": "arq",
        "routing": "routing",
    }
    for key, tag in mapping.items():
        if key in s:
            tags.add(tag)
    return sorted(tags)


@dataclass
class ParseContext:
    document_id: UUID
    page_start: int
    page_end: int


def parse_unisa_questions(document_id: UUID, pages: list[str]) -> list[QuestionOut]:
    text = "\n".join(_clean_lines("\n".join(pages)))
    text = text.replace("\t", " ")
    text = re.sub(r"(?<!^)\s(\d{1,2}\.\s)", r"\n\1", text)
    text = re.sub(r"(?<!^)\s([a-d]\.\s)", r"\n\1", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    lines = text.splitlines()

    questions: list[QuestionOut] = []
    section = "quiz"
    counters = {"quiz": 0, "teoria": 0, "esercizio": 0}

    i = 0
    while i < len(lines):
        line = lines[i]

        if THEORY_RE.match(line):
            section = "teoria"
            i += 1
            stem_lines = []
            while i < len(lines) and not EXERCISE_RE.match(lines[i]):
                stem_lines.append(lines[i])
                i += 1
            stem = " ".join(stem_lines).strip()
            if stem:
                counters["teoria"] += 1
                questions.append(
                    QuestionOut(
                        question_id=uuid4(),
                        document_id=document_id,
                        section="teoria",
                        number_in_section=counters["teoria"],
                        question_type="open_text",
                        stem=stem,
                        tags=_tag_question(stem),
                        source_loc=SourceLoc(page_start=1, page_end=len(pages)),
                        quality=Quality(confidence=0.9, needs_review=False),
                    )
                )
            continue

        ex_match = EXERCISE_RE.match(line)
        if ex_match:
            section = "esercizio"
            exercise_title = line
            i += 1
            ex_lines = []
            while i < len(lines) and not EXERCISE_RE.match(lines[i]):
                ex_lines.append(lines[i])
                i += 1

            subparts = []
            stem_lines = [exercise_title]
            for ex_line in ex_lines:
                sp = SUBPART_RE.match(ex_line)
                if sp:
                    subparts.append({"id": sp.group(1), "prompt": sp.group(2).strip()})
                else:
                    stem_lines.append(ex_line)
            stem = " ".join(stem_lines).strip()
            counters["esercizio"] += 1
            q_type = "multi_part_open" if subparts else "open_text"
            questions.append(
                QuestionOut(
                    question_id=uuid4(),
                    document_id=document_id,
                    section="esercizio",
                    number_in_section=counters["esercizio"],
                    question_type=q_type,
                    stem=stem,
                    subparts=subparts,
                    tags=_tag_question(stem),
                    source_loc=SourceLoc(page_start=1, page_end=len(pages)),
                    quality=Quality(confidence=0.85, needs_review=False),
                )
            )
            continue

        if section == "quiz":
            mcq = MCQ_START_RE.match(line)
            if mcq:
                q_number = int(mcq.group(1))
                stem_lines = [mcq.group(2).strip()]
                i += 1
                options = []
                while i < len(lines):
                    if MCQ_START_RE.match(lines[i]) or THEORY_RE.match(lines[i]) or EXERCISE_RE.match(lines[i]):
                        break
                    opt = OPTION_RE.match(lines[i])
                    if opt:
                        options.append({"id": opt.group(1).lower(), "text": opt.group(2).strip()})
                    else:
                        # Continued question line.
                        if not options:
                            stem_lines.append(lines[i].strip())
                    i += 1

                stem = " ".join(stem_lines).strip()
                warnings = []
                needs_review = False
                confidence = 0.95
                if len(options) < 2:
                    warnings.append("Insufficient options detected")
                    needs_review = True
                    confidence = 0.6

                counters["quiz"] = max(counters["quiz"] + 1, q_number)
                questions.append(
                    QuestionOut(
                        question_id=uuid4(),
                        document_id=document_id,
                        section="quiz",
                        number_in_section=q_number,
                        question_type="multiple_choice",
                        stem=stem,
                        options=options,
                        tags=_tag_question(stem),
                        source_loc=SourceLoc(page_start=1, page_end=len(pages)),
                        quality=Quality(confidence=confidence, needs_review=needs_review, warnings=warnings),
                    )
                )
                continue

        i += 1

    return sorted(questions, key=lambda q: (q.section, q.number_in_section))
