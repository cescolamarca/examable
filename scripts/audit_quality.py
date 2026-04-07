from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
import sys
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.parser import extract_text_pages_with_fallback, parse_unisa_questions


def audit_one(pdf_path: Path) -> dict:
    result = {
        "pdf": str(pdf_path),
        "errors": [],
        "stats": {},
    }
    try:
        extraction = extract_text_pages_with_fallback(pdf_path)
        questions = parse_unisa_questions(uuid4(), extraction.pages)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"parse_failure: {exc}")
        return result

    by_type = Counter(q.question_type for q in questions)
    mcq = [q for q in questions if q.question_type == "multiple_choice"]
    bad_mcq = []
    for q in mcq:
        if len(q.options) != 4:
            bad_mcq.append(
                {
                    "n": q.number_in_section,
                    "options": len(q.options),
                    "stem_preview": q.stem[:120],
                }
            )

    low_conf = [q.number_in_section for q in questions if q.quality.confidence < 0.8]
    empty_stem = [q.number_in_section for q in questions if not q.stem.strip()]

    result["stats"] = {
        "quality": extraction.quality_score,
        "questions": len(questions),
        "type_breakdown": dict(by_type),
        "bad_mcq_count": len(bad_mcq),
        "low_conf_count": len(low_conf),
        "empty_stem_count": len(empty_stem),
    }
    if bad_mcq:
        result["errors"].append("mcq_not_4_options")
        result["bad_mcq_examples"] = bad_mcq[:5]
    if low_conf:
        result["errors"].append("low_confidence_questions")
    if empty_stem:
        result["errors"].append("empty_stem_questions")
    return result


def main() -> None:
    root = Path("simulation_runs/20260329_164730")
    pdfs = sorted(root.rglob("*.pdf"))
    report = [audit_one(p) for p in pdfs]

    summary = {
        "total": len(report),
        "failures": sum(1 for r in report if r["errors"]),
        "parse_failures": sum(1 for r in report if any(e.startswith("parse_failure") for e in r["errors"])),
        "mcq_shape_failures": sum(1 for r in report if "mcq_not_4_options" in r["errors"]),
    }

    out = {"summary": summary, "items": report}
    Path("quality_audit_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
