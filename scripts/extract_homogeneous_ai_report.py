from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.multimodal import enhance_with_multimodal
from app.parser import extract_text_pages_with_fallback, parse_unisa_questions

PARTS_DIR = PROJECT_ROOT / "uploads" / "homogeneous_parts"
OUT_PATH = PROJECT_ROOT / "homogeneous_ai_occurrence_report.json"
PARTS = [
    PARTS_DIR / "intercorso_2021_traccia_A.pdf",
    PARTS_DIR / "intercorso_2021_traccia_B.pdf",
]


def _canon(raw: str) -> str:
    s = unicodedata.normalize("NFKC", raw or "")
    s = s.lower()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _fingerprint(q: dict) -> str:
    opts = "|".join(
        f"{str(o.get('id', '')).strip().lower()}:{_canon(str(o.get('text', '')))}"
        for o in q.get("options", [])
    )
    sub = "|".join(
        f"{str(s.get('id', '')).strip()}:{_canon(str(s.get('prompt', '')))}"
        for s in q.get("subparts", [])
    )
    payload = f"{q.get('question_type','')}##{_canon(q.get('stem',''))}##{opts}##{sub}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def main() -> None:
    all_questions: list[dict] = []
    source_stats: list[dict] = []
    for part_path in PARTS:
        if not part_path.exists():
            continue
        extraction = extract_text_pages_with_fallback(part_path)
        parsed = parse_unisa_questions(uuid4(), extraction.pages)
        mm = enhance_with_multimodal(
            pdf_path=part_path,
            document_id=uuid4(),
            pages=extraction.pages,
            questions=parsed,
            extraction_quality=extraction.quality_score,
        )
        questions = mm.questions
        source_stats.append(
            {
                "source_file": part_path.name,
                "pages": len(extraction.pages),
                "extraction_method": extraction.method,
                "extraction_quality": extraction.quality_score,
                "multimodal_used": mm.used,
                "multimodal_updates": mm.updated_items,
                "question_count": len(questions),
                "warnings": extraction.warnings + mm.warnings,
            }
        )
        for q in questions:
            q_dict = {
                "source_file": part_path.name,
                "section": q.section,
                "number_in_section": q.number_in_section,
                "question_type": q.question_type,
                "stem": q.stem,
                "options": [o.model_dump() if hasattr(o, "model_dump") else dict(o) for o in q.options],
                "subparts": [s.model_dump() if hasattr(s, "model_dump") else dict(s) for s in q.subparts],
                "tags": list(q.tags),
                "confidence": q.quality.confidence,
            }
            q_dict["fingerprint"] = _fingerprint(q_dict)
            all_questions.append(q_dict)

    grouped: dict[str, dict] = {}
    for q in all_questions:
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
                "source_file": q["source_file"],
                "section": q["section"],
                "number_in_section": q["number_in_section"],
            }
        )

    unique_questions = sorted(grouped.values(), key=lambda x: x["occurrences_count"], reverse=True)
    report = {
        "summary": {
            "source_parts": len(source_stats),
            "questions_total_extracted": len(all_questions),
            "questions_unique": len(unique_questions),
            "questions_duplicates": len(all_questions) - len(unique_questions),
        },
        "source_parts": source_stats,
        "questions": unique_questions,
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(str(OUT_PATH))


if __name__ == "__main__":
    main()
