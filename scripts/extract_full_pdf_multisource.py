from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import fitz
from pypdf import PdfReader, PdfWriter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.multimodal import enhance_with_multimodal
from app.parser import extract_text_pages_with_fallback, parse_unisa_questions

SRC_PDF = Path(r"c:\Users\nextc\Documents\domande_intercorso_reti_banca-piatto.pdf")
TMP_DIR = PROJECT_ROOT / "uploads" / "full_segments"
OUT_REPORT = PROJECT_ROOT / "full_pdf_ai_occurrence_report.json"
OUT_FLAT = PROJECT_ROOT / "extracted_questions_full_pdf_all.json"


@dataclass
class Segment:
    start_page: int
    end_page: int
    kind: str  # text | image | empty


def _canon(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _fingerprint(question: dict) -> str:
    options = "|".join(f"{o.get('id','')}:{_canon(o.get('text',''))}" for o in question.get("options", []))
    subparts = "|".join(f"{s.get('id','')}:{_canon(s.get('prompt',''))}" for s in question.get("subparts", []))
    payload = f"{question.get('question_type','')}##{_canon(question.get('stem',''))}##{options}##{subparts}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def detect_segments(pdf_path: Path) -> list[Segment]:
    doc = fitz.open(str(pdf_path))
    page_kinds: list[str] = []
    try:
        for i in range(doc.page_count):
            page = doc[i]
            txt = (page.get_text("text") or "").strip()
            imgs = page.get_images(full=True) or []
            if len(txt) >= 80:
                page_kinds.append("text")
            elif imgs:
                page_kinds.append("image")
            else:
                page_kinds.append("empty")
    finally:
        doc.close()

    segments: list[Segment] = []
    if not page_kinds:
        return segments
    cur_kind = page_kinds[0]
    start = 1
    for idx, kind in enumerate(page_kinds[1:], start=2):
        if kind != cur_kind:
            segments.append(Segment(start_page=start, end_page=idx - 1, kind=cur_kind))
            start = idx
            cur_kind = kind
    segments.append(Segment(start_page=start, end_page=len(page_kinds), kind=cur_kind))
    return segments


def write_segment_pdf(src_pdf: Path, seg: Segment, out_path: Path) -> None:
    reader = PdfReader(str(src_pdf), strict=False)
    writer = PdfWriter()
    for p in range(seg.start_page - 1, seg.end_page):
        writer.add_page(reader.pages[p])
    with out_path.open("wb") as f:
        writer.write(f)


def main() -> None:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    segments = detect_segments(SRC_PDF)
    results: list[dict] = []
    all_rows: list[dict] = []

    for idx, seg in enumerate(segments, start=1):
        segment_name = f"segment_{idx:02d}_{seg.kind}_p{seg.start_page:03d}-{seg.end_page:03d}"
        seg_pdf = TMP_DIR / f"{segment_name}.pdf"
        write_segment_pdf(SRC_PDF, seg, seg_pdf)

        extraction = extract_text_pages_with_fallback(seg_pdf)
        parsed = parse_unisa_questions(uuid4(), extraction.pages)
        mm = enhance_with_multimodal(
            pdf_path=seg_pdf,
            document_id=uuid4(),
            pages=extraction.pages,
            questions=parsed,
            extraction_quality=extraction.quality_score,
        )
        questions = mm.questions

        results.append(
            {
                "segment": segment_name,
                "kind": seg.kind,
                "page_start": seg.start_page,
                "page_end": seg.end_page,
                "pages_count": seg.end_page - seg.start_page + 1,
                "extraction_method": extraction.method,
                "extraction_quality": extraction.quality_score,
                "multimodal_used": mm.used,
                "multimodal_updates": mm.updated_items,
                "questions_count": len(questions),
                "warnings": extraction.warnings + mm.warnings,
            }
        )

        for q in questions:
            row = {
                "source_segment": segment_name,
                "page_start": seg.start_page,
                "page_end": seg.end_page,
                "question_type": q.question_type,
                "section": q.section,
                "number_in_section": q.number_in_section,
                "stem": q.stem,
                "options": [o.model_dump() for o in q.options],
                "subparts": [s.model_dump() for s in q.subparts],
                "tags": list(q.tags),
                "confidence": q.quality.confidence,
            }
            row["fingerprint"] = _fingerprint(row)
            all_rows.append(row)

    grouped: dict[str, dict] = {}
    for q in all_rows:
        fp = q["fingerprint"]
        if fp not in grouped:
            grouped[fp] = {
                "fingerprint": fp,
                "question_type": q["question_type"],
                "stem": q["stem"],
                "tags": q["tags"],
                "confidence_max": q["confidence"],
                "occurrences_count": 0,
                "occurrences": [],
            }
        grouped[fp]["occurrences_count"] += 1
        grouped[fp]["confidence_max"] = max(grouped[fp]["confidence_max"], q["confidence"])
        grouped[fp]["occurrences"].append(
            {
                "source_segment": q["source_segment"],
                "page_start": q["page_start"],
                "page_end": q["page_end"],
                "section": q["section"],
                "number_in_section": q["number_in_section"],
            }
        )

    unique_questions = sorted(grouped.values(), key=lambda x: x["occurrences_count"], reverse=True)
    report = {
        "summary": {
            "segments": len(results),
            "questions_total_extracted": len(all_rows),
            "questions_unique": len(unique_questions),
            "questions_duplicates": len(all_rows) - len(unique_questions),
        },
        "segments": results,
        "questions": unique_questions,
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FLAT.write_text(json.dumps({"total_questions": len(all_rows), "questions": all_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(str(OUT_REPORT))
    print(str(OUT_FLAT))


if __name__ == "__main__":
    main()
