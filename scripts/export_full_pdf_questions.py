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

PDF_PATH = Path(r"c:\Users\nextc\Documents\domande_intercorso_reti_banca-piatto.pdf")
OUT_PATH = Path(r"c:\Users\nextc\Examable\extracted_questions_full_pdf.json")


def _canon(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def main() -> None:
    extraction = extract_text_pages_with_fallback(PDF_PATH)
    parsed = parse_unisa_questions(uuid4(), extraction.pages)
    mm = enhance_with_multimodal(
        pdf_path=PDF_PATH,
        document_id=uuid4(),
        pages=extraction.pages,
        questions=parsed,
        extraction_quality=extraction.quality_score,
    )
    rows: list[dict] = []
    for q in mm.questions:
        opts = "|".join(f"{o.id}:{_canon(o.text)}" for o in q.options)
        sub = "|".join(f"{s.id}:{_canon(s.prompt)}" for s in q.subparts)
        fp = hashlib.sha1(f"{q.question_type}##{_canon(q.stem)}##{opts}##{sub}".encode("utf-8")).hexdigest()
        rows.append(
            {
                "fingerprint": fp,
                "question_type": q.question_type,
                "section": q.section,
                "number_in_section": q.number_in_section,
                "stem": q.stem,
                "options": [o.model_dump() for o in q.options],
                "subparts": [s.model_dump() for s in q.subparts],
                "tags": list(q.tags),
                "confidence": q.quality.confidence,
                "source_file": PDF_PATH.name,
            }
        )
    payload = {
        "total_questions": len(rows),
        "extraction_method": extraction.method,
        "extraction_quality": extraction.quality_score,
        "multimodal_used": mm.used,
        "multimodal_updates": mm.updated_items,
        "questions": rows,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {OUT_PATH}")
    print(f"total_questions {len(rows)}")


if __name__ == "__main__":
    main()
