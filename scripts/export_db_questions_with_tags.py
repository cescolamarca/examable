from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Export questions from DB with their tags.")
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "db_questions_with_tags.json",
        help="Output JSON file path",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit number of questions (0 = no limit)")
    parser.add_argument(
        "--include-discarded",
        action="store_true",
        help="Include questions where is_discarded=true",
    )
    args = parser.parse_args()

    where = []
    params: dict[str, object] = {}
    if not args.include_discarded:
        where.append("q.is_discarded = false")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    limit_sql = ""
    if args.limit and args.limit > 0:
        limit_sql = "LIMIT :limit"
        params["limit"] = int(args.limit)

    query = text(
        f"""
        SELECT
          q.id,
          q.document_id,
          q.section,
          q.number_in_section,
          q.question_type,
          q.stem,
          q.options_json,
          q.subparts_json,
          q.solution_json,
          q.confidence,
          q.needs_review,
          q.is_discarded,
          q.created_at,
          q.updated_at,
          (
            SELECT COALESCE(jsonb_agg(t.slug ORDER BY t.slug), '[]'::jsonb)
            FROM question_tags qt
            JOIN tags t ON t.id = qt.tag_id
            WHERE qt.question_id = q.id
          ) AS tags_json
        FROM questions q
        {where_sql}
        ORDER BY q.section, q.number_in_section
        {limit_sql}
        """
    )

    with engine.begin() as conn:
        rows = conn.execute(query, params).mappings().all()

    questions: list[dict] = []
    for row in rows:
        questions.append(
            {
                "id": str(row["id"]),
                "document_id": str(row["document_id"]),
                "section": row["section"],
                "number_in_section": int(row["number_in_section"]),
                "question_type": row["question_type"],
                "stem": row["stem"],
                "options": row["options_json"] or [],
                "subparts": row["subparts_json"] or [],
                "solution": row["solution_json"] or {},
                "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
                "needs_review": bool(row["needs_review"]),
                "is_discarded": bool(row["is_discarded"]),
                "tags": row["tags_json"] or [],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
        )

    payload = {
        "exported_at": datetime.now(tz=timezone.utc).isoformat(),
        "total_questions": len(questions),
        "include_discarded": bool(args.include_discarded),
        "questions": questions,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {args.out}")
    print(f"total_questions {len(questions)}")


if __name__ == "__main__":
    main()

