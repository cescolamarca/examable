from __future__ import annotations

import json
import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.config import settings
from app.correction_generation import (
    count_pending_candidates,
    mark_orphan_running_as_interrupted,
    schedule_job,
    serialize_job,
)
from app.database import engine, healthcheck
from app.maintenance import ensure_runtime_schema, run_cleanup_dedupe
from app.multimodal import enhance_with_multimodal
from app.parser import compute_sha256, extract_text_pages_with_fallback, parse_unisa_questions
from app.scheduler import next_due_after_attempt
from app.schemas import (
    AttemptIn,
    CorrectionJobFailureOut,
    CorrectionJobOut,
    CorrectionJobStartIn,
    CustomSimulationIn,
    NextQuestionResponse,
    ParseResponse,
    QuestionCorrectionSetIn,
    QuestionReviewSetIn,
    QuestionTagSetIn,
    TagCreateIn,
    UploadResponse,
)
from app.tagging import auto_tag_document, ensure_base_tags, ensure_intercorso_1_preset, ensure_module_1_preset

app = FastAPI(title="Examable API", version="0.1.0")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def _startup_migrations() -> None:
    ensure_runtime_schema()
    with engine.begin() as conn:
        ensure_base_tags(conn)
        ensure_module_1_preset(conn)
        ensure_intercorso_1_preset(conn)
    # Any "queued"/"running" rows can only be from a previous process — mark them so the
    # partial unique index allows new jobs immediately.
    mark_orphan_running_as_interrupted()


def _uploads_root() -> Path:
    root = Path(settings.upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _slugify_tag(value: str) -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    return "-".join(part for part in raw.split("-") if part)


def _parse_tag_values(raw: str | None, *, limit: int = 30) -> list[str]:
    if not raw:
        return []
    # UI allows "reti, tcp-ip, routing" in a single input. Treat comma-separated values as OR.
    parts = [p.strip() for p in raw.replace("\n", ",").split(",")]
    values: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        # Be forgiving: users sometimes paste quoted CSV-like strings.
        if (part.startswith('"') and part.endswith('"')) or (part.startswith("'") and part.endswith("'")):
            part = part[1:-1].strip()
            if not part:
                continue
        if part in seen:
            continue
        values.append(part)
        seen.add(part)
        if len(values) >= limit:
            break
    return values


def _question_has_any_tag_sql(*, param_prefix: str, tag_values: list[str]) -> tuple[str, dict[str, str]]:
    clauses: list[str] = []
    params: dict[str, str] = {}
    for idx, value in enumerate(tag_values):
        key = f"{param_prefix}_{idx}"
        clauses.append(f"(t.slug = :{key} OR t.name = :{key} OR CAST(t.id AS TEXT) = :{key})")
        params[key] = value
    if not clauses:
        return "", {}
    sql = f"""
    EXISTS (
      SELECT 1
      FROM question_tags qt
      JOIN tags t ON t.id = qt.tag_id
      WHERE qt.question_id = q.id
        AND ({' OR '.join(clauses)})
    )
    """
    return sql, params


@app.get("/", response_class=HTMLResponse)
def ui_home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("study.html", {"request": request})


@app.get("/study", response_class=HTMLResponse)
def ui_study(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("study.html", {"request": request})


@app.get("/ingest", response_class=HTMLResponse)
def ui_ingest(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("ingest.html", {"request": request})


@app.get("/health")
def get_health() -> dict[str, str]:
    healthcheck()
    return {"status": "ok"}


@app.post("/admin/reset-db")
def admin_reset_db() -> dict:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                TRUNCATE TABLE
                  attempts,
                  schedule_state,
                  question_reviews,
                  question_tags,
                  question_occurrences,
                  questions,
                  documents,
                  tag_preset_tags,
                  tag_presets,
                  tags,
                  users
                RESTART IDENTITY CASCADE
                """
            )
        )
        ensure_base_tags(conn)
        ensure_module_1_preset(conn)
        ensure_intercorso_1_preset(conn)
    return {"status": "ok", "message": "database reset completed"}


@app.post("/admin/load-question-bank")
def admin_load_question_bank(
    path: str = r"c:\Users\nextc\Examable\banca_domande_postprocessed.json",
    title: str = "Intercorso 1 - banca postprocessed",
) -> dict:
    bank_path = Path(path)
    if not bank_path.exists():
        raise HTTPException(status_code=404, detail=f"Question bank not found: {path}")
    payload = json.loads(bank_path.read_text(encoding="utf-8"))
    questions = payload.get("questions", [])
    if not isinstance(questions, list) or not questions:
        raise HTTPException(status_code=400, detail="Invalid question bank: empty questions list")

    sha = hashlib.sha256(bank_path.read_bytes()).hexdigest()
    section_seq: defaultdict[str, int] = defaultdict(int)
    inserted_questions = 0
    inserted_occurrences = 0
    inserted_question_tags = 0

    with engine.begin() as conn:
        doc = conn.execute(
            text("SELECT id FROM documents WHERE sha256 = :sha"),
            {"sha": sha},
        ).mappings().first()
        document_id = str(doc["id"]) if doc else str(uuid4())

        if not doc:
            conn.execute(
                text(
                    """
                    INSERT INTO documents (id, title, source_uri, sha256, ingestion_status, pages, created_at, processed_at)
                    VALUES (:id, :title, :source_uri, :sha256, 'processed', NULL, now(), now())
                    """
                ),
                {
                    "id": document_id,
                    "title": title,
                    "source_uri": str(bank_path),
                    "sha256": sha,
                },
            )
        else:
            conn.execute(text("DELETE FROM questions WHERE document_id = :document_id"), {"document_id": document_id})
            conn.execute(
                text(
                    """
                    UPDATE documents
                    SET title = :title, source_uri = :source_uri, ingestion_status = 'processed',
                        ingestion_error = NULL, processed_at = now()
                    WHERE id = :id
                    """
                ),
                {"id": document_id, "title": title, "source_uri": str(bank_path)},
            )

        for item in questions:
            if not isinstance(item, dict):
                continue
            q_type = str(item.get("question_type") or "open_text").strip()
            if q_type not in {"multiple_choice", "open_text", "multi_part_open"}:
                q_type = "open_text"
            section = str(item.get("section") or "esercizio").strip()
            if section not in {"quiz", "teoria", "esercizio"}:
                section = "esercizio"
            stem = str(item.get("stem") or "").strip()
            if not stem:
                continue
            section_seq[section] += 1
            number_in_section = section_seq[section]
            fingerprint = str(item.get("fingerprint") or item.get("question_id") or "")
            stable_key = fingerprint or f"{section}:{number_in_section}:{stem[:50]}"
            question_id = str(uuid5(NAMESPACE_URL, stable_key))

            options = item.get("options") if isinstance(item.get("options"), list) else []
            subparts = item.get("subparts") if isinstance(item.get("subparts"), list) else []
            confidence = float(item.get("confidence_max") or 0.9)
            confidence = max(0.0, min(1.0, confidence))

            occurrences = item.get("occurrences") if isinstance(item.get("occurrences"), list) else []
            source_files = sorted(
                {
                    str(o.get("source_file")).strip()
                    for o in occurrences
                    if isinstance(o, dict) and str(o.get("source_file", "")).strip()
                }
            )
            if not source_files:
                source_files = [bank_path.name]

            conn.execute(
                text(
                    """
                    INSERT INTO questions (
                      id, document_id, section, number_in_section, question_type, stem,
                      options_json, subparts_json, assets_json, solution_json, difficulty,
                      language, page_start, page_end, confidence, needs_review,
                      occurrences_count, source_files_json, dedupe_fingerprint, schema_version
                    ) VALUES (
                      :id, :document_id, :section, :number_in_section, :question_type, :stem,
                      CAST(:options_json AS JSONB), CAST(:subparts_json AS JSONB), '[]'::jsonb, '{}'::jsonb, 0.50,
                      'it', NULL, NULL, :confidence, false,
                      :occurrences_count, CAST(:source_files_json AS JSONB), :dedupe_fingerprint, '1.0'
                    )
                    """
                ),
                {
                    "id": question_id,
                    "document_id": document_id,
                    "section": section,
                    "number_in_section": number_in_section,
                    "question_type": q_type,
                    "stem": stem,
                    "options_json": json.dumps(options, ensure_ascii=False),
                    "subparts_json": json.dumps(subparts, ensure_ascii=False),
                    "confidence": confidence,
                    "occurrences_count": max(1, int(item.get("occurrences_count") or len(occurrences) or 1)),
                    "source_files_json": json.dumps(source_files, ensure_ascii=False),
                    "dedupe_fingerprint": fingerprint[:40] if len(fingerprint) >= 40 else None,
                },
            )
            inserted_questions += 1

            for occ in occurrences:
                if not isinstance(occ, dict):
                    continue
                source_file_name = str(occ.get("source_file") or bank_path.name).strip() or bank_path.name
                source_section = str(occ.get("section") or section).strip() or section
                source_number_raw = occ.get("number_in_section")
                try:
                    source_number = int(source_number_raw) if source_number_raw is not None else number_in_section
                except Exception:
                    source_number = number_in_section
                conn.execute(
                    text(
                        """
                        INSERT INTO question_occurrences (
                          question_id, document_id, source_file_name, source_section, source_number
                        ) VALUES (
                          :question_id, :document_id, :source_file_name, :source_section, :source_number
                        )
                        ON CONFLICT (question_id, document_id, source_section, source_number) DO NOTHING
                        """
                    ),
                    {
                        "question_id": question_id,
                        "document_id": document_id,
                        "source_file_name": source_file_name,
                        "source_section": source_section,
                        "source_number": source_number,
                    },
                )
                inserted_occurrences += 1

            for raw_tag in item.get("tags", []):
                tag_name = str(raw_tag).strip()
                if not tag_name:
                    continue
                slug = _slugify_tag(tag_name)
                if not slug:
                    continue
                tag_row = conn.execute(
                    text(
                        """
                        INSERT INTO tags (name, slug)
                        VALUES (:name, :slug)
                        ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                        RETURNING id
                        """
                    ),
                    {"name": tag_name, "slug": slug},
                ).mappings().first()
                if not tag_row:
                    continue
                conn.execute(
                    text(
                        """
                        INSERT INTO question_tags (question_id, tag_id, score, source)
                        VALUES (:question_id, :tag_id, 1.0, 'manual')
                        ON CONFLICT (question_id, tag_id)
                        DO UPDATE SET score = 1.0, source = 'manual'
                        """
                    ),
                    {"question_id": question_id, "tag_id": str(tag_row["id"])},
                )
                inserted_question_tags += 1

    return {
        "status": "ok",
        "document_id": document_id,
        "source_path": str(bank_path),
        "inserted_questions": inserted_questions,
        "inserted_occurrences": inserted_occurrences,
        "inserted_question_tags": inserted_question_tags,
    }


@app.get("/documents")
def list_documents(limit: int = 100) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, title, ingestion_status, pages, created_at, processed_at, ingestion_error
                FROM documents
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": max(1, min(limit, 500))},
        ).mappings()
        return [dict(row) for row in rows]


@app.get("/stats/kpi")
def get_kpis() -> dict:
    with engine.begin() as conn:
        docs = conn.execute(
            text(
                """
                SELECT
                  COUNT(*) AS total_documents,
                  COUNT(*) FILTER (WHERE ingestion_status = 'processed') AS processed_documents
                FROM documents
                """
            )
        ).mappings().first()
        quality = conn.execute(
            text(
                """
                SELECT AVG(confidence) AS avg_quality
                FROM questions
                WHERE is_discarded = false
                """
            )
        ).mappings().first()

        avg_quality = float(quality["avg_quality"]) if quality and quality["avg_quality"] is not None else None
        return {
            "total_documents": int(docs["total_documents"] or 0) if docs else 0,
            "processed_documents": int(docs["processed_documents"] or 0) if docs else 0,
            "avg_quality": avg_quality,
        }


@app.get("/tags")
def list_tags(query: str | None = None, limit: int = 200) -> list[dict]:
    params: dict[str, str | int] = {"limit": max(1, min(limit, 500))}
    where = ""
    if query and query.strip():
        where = "WHERE t.slug ILIKE :q OR t.name ILIKE :q"
        params["q"] = f"%{query.strip()}%"
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.id, t.name, t.slug, t.parent_id
                FROM tags t
                """
                + where
                + """
                ORDER BY t.slug
                LIMIT :limit
                """
            ),
            params,
        ).mappings()
        return [dict(r) for r in rows]


@app.get("/tag-presets")
def list_tag_presets() -> list[dict]:
    with engine.begin() as conn:
        ensure_module_1_preset(conn)
        ensure_intercorso_1_preset(conn)
        rows = conn.execute(
            text(
                """
                SELECT p.id, p.name, p.slug, p.description,
                       COALESCE(jsonb_agg(t.slug ORDER BY t.slug) FILTER (WHERE t.slug IS NOT NULL), '[]'::jsonb) AS tags
                FROM tag_presets p
                LEFT JOIN tag_preset_tags pt ON pt.preset_id = p.id
                LEFT JOIN tags t ON t.id = pt.tag_id
                GROUP BY p.id, p.name, p.slug, p.description
                ORDER BY p.name
                """
            )
        ).mappings()
        return [dict(r) for r in rows]


@app.post("/tags")
def create_tag(payload: TagCreateIn) -> dict:
    raw_name = payload.name.strip()
    if not raw_name:
        raise HTTPException(status_code=400, detail="Tag name required")
    slug = (payload.slug or raw_name).strip().lower()
    slug = "".join(ch if ch.isalnum() else "-" for ch in slug)
    slug = "-".join(p for p in slug.split("-") if p)
    if not slug:
        raise HTTPException(status_code=400, detail="Invalid tag slug")
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO tags (name, slug, parent_id)
                VALUES (:name, :slug, :parent_id)
                ON CONFLICT (slug)
                DO UPDATE SET name = EXCLUDED.name, parent_id = EXCLUDED.parent_id
                RETURNING id, name, slug, parent_id
                """
            ),
            {"name": raw_name, "slug": slug, "parent_id": str(payload.parent_id) if payload.parent_id else None},
        ).mappings().first()
        return dict(row) if row else {}


@app.get("/questions/{question_id}/tags")
def list_question_tags(question_id: UUID) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.id, t.name, t.slug, qt.score, qt.source
                FROM question_tags qt
                JOIN tags t ON t.id = qt.tag_id
                WHERE qt.question_id = :question_id
                ORDER BY qt.score DESC, t.slug
                """
            ),
            {"question_id": str(question_id)},
        ).mappings()
        return [dict(r) for r in rows]


@app.put("/questions/{question_id}/tags")
def set_question_tags_manual(question_id: UUID, payload: QuestionTagSetIn) -> dict:
    names = [str(t).strip() for t in payload.tags if str(t).strip()]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM question_tags WHERE question_id = :question_id"), {"question_id": str(question_id)})
        for name in names:
            tag_row = conn.execute(
                text(
                    """
                    INSERT INTO tags (name, slug)
                    VALUES (:name, :slug)
                    ON CONFLICT (slug)
                    DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                    """
                ),
                {
                    "name": name,
                    "slug": "-".join(p for p in "".join(ch.lower() if ch.isalnum() else "-" for ch in name).split("-") if p),
                },
            ).first()
            if not tag_row:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO question_tags (question_id, tag_id, score, source)
                    VALUES (:question_id, :tag_id, 1.0, 'manual')
                    ON CONFLICT (question_id, tag_id)
                    DO UPDATE SET score = 1.0, source = 'manual'
                    """
                ),
                {"question_id": str(question_id), "tag_id": str(tag_row.id)},
            )
    return {"status": "ok", "tags_set": len(names)}


@app.post("/tagging/recompute/document/{document_id}")
def recompute_document_tags(document_id: UUID, use_ai: bool = False) -> dict:
    with engine.begin() as conn:
        stats = auto_tag_document(conn, str(document_id), use_ai=use_ai)
    return {"document_id": str(document_id), **stats}


@app.get("/documents/{document_id}/questions")
def list_document_questions(
    document_id: UUID, limit: int = 500, question_type: str | None = None, include_discarded: bool = False
) -> list[dict]:
    where_extra = ""
    params: dict[str, str | int] = {"document_id": str(document_id), "limit": max(1, min(limit, 2000))}
    if question_type:
        where_extra = " AND question_type = :question_type"
        params["question_type"] = question_type.strip()
    if not include_discarded:
        where_extra += " AND is_discarded = false"

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  id, section, number_in_section, question_type, stem, options_json, subparts_json,
                  confidence, needs_review, occurrences_count, source_files_json,
                  (
                    SELECT COALESCE(jsonb_agg(t.slug ORDER BY t.slug), '[]'::jsonb)
                    FROM question_tags qt
                    JOIN tags t ON t.id = qt.tag_id
                    WHERE qt.question_id = questions.id
                  ) AS tags_json
                FROM questions
                WHERE document_id = :document_id
                """
                + where_extra
                + """
                ORDER BY section, number_in_section
                LIMIT :limit
                """
            ),
            params,
        ).mappings()

        out = []
        for row in rows:
            out.append(
                {
                    "id": row["id"],
                    "section": row["section"],
                    "number_in_section": row["number_in_section"],
                    "question_type": row["question_type"],
                    "stem": row["stem"],
                    "options": row["options_json"] or [],
                    "subparts": row["subparts_json"] or [],
                    "confidence": float(row["confidence"]),
                    "needs_review": bool(row["needs_review"]),
                    "occurrences_count": int(row["occurrences_count"] or 1),
                    "source_files": row["source_files_json"] or [],
                    "tags": row["tags_json"] or [],
                }
            )
        return out


@app.get("/questions/{question_id}")
def get_question(question_id: UUID) -> dict:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                  id, document_id, section, number_in_section, question_type, stem,
                  options_json, subparts_json, solution_json, confidence, needs_review,
                  occurrences_count, source_files_json, is_discarded,
                  (
                    SELECT COALESCE(jsonb_agg(t.slug ORDER BY t.slug), '[]'::jsonb)
                    FROM question_tags qt
                    JOIN tags t ON t.id = qt.tag_id
                    WHERE qt.question_id = questions.id
                  ) AS tags_json
                FROM questions
                WHERE id = :question_id
                """
            ),
            {"question_id": str(question_id)},
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Question not found")

        return {
            "id": row["id"],
            "document_id": row["document_id"],
            "section": row["section"],
            "number_in_section": row["number_in_section"],
            "question_type": row["question_type"],
            "stem": row["stem"],
            "options": row["options_json"] or [],
            "subparts": row["subparts_json"] or [],
            "solution": row["solution_json"] or {},
            "confidence": float(row["confidence"]),
            "needs_review": bool(row["needs_review"]),
            "occurrences_count": int(row["occurrences_count"] or 1),
            "source_files": row["source_files_json"] or [],
            "tags": row["tags_json"] or [],
            "is_discarded": bool(row["is_discarded"]),
        }


@app.post("/questions/{question_id}/discard")
def discard_question(question_id: UUID, discarded: bool = True) -> dict:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                UPDATE questions
                SET is_discarded = :discarded,
                    discarded_at = CASE WHEN :discarded THEN now() ELSE NULL END
                WHERE id = :question_id
                RETURNING id, is_discarded, discarded_at
                """
            ),
            {"question_id": str(question_id), "discarded": discarded},
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Question not found")
    return {
        "question_id": str(row["id"]),
        "is_discarded": bool(row["is_discarded"]),
        "discarded_at": row["discarded_at"].isoformat() if row["discarded_at"] else None,
    }


@app.get("/questions/{question_id}/review")
def get_question_review(question_id: UUID, user_id: UUID) -> dict:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT status, first_seen_at, reviewed_at
                FROM question_reviews
                WHERE question_id = :question_id AND user_id = :user_id
                """
            ),
            {"question_id": str(question_id), "user_id": str(user_id)},
        ).mappings().first()
        if not row:
            return {
                "question_id": str(question_id),
                "user_id": str(user_id),
                "status": None,
                "first_seen_at": None,
                "reviewed_at": None,
            }
        return {
            "question_id": str(question_id),
            "user_id": str(user_id),
            "status": row["status"],
            "first_seen_at": row["first_seen_at"].isoformat() if row["first_seen_at"] else None,
            "reviewed_at": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
        }


@app.put("/questions/{question_id}/review")
def set_question_review(question_id: UUID, payload: QuestionReviewSetIn) -> dict:
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM questions WHERE id = :question_id"),
            {"question_id": str(question_id)},
        ).first()
        if not exists:
            raise HTTPException(status_code=404, detail="Question not found")
        row = conn.execute(
            text(
                """
                INSERT INTO question_reviews (user_id, question_id, status)
                VALUES (:user_id, :question_id, :status)
                ON CONFLICT (user_id, question_id)
                DO UPDATE SET
                  status = EXCLUDED.status,
                  reviewed_at = now()
                RETURNING status, first_seen_at, reviewed_at
                """
            ),
            {
                "user_id": str(payload.user_id),
                "question_id": str(question_id),
                "status": payload.status,
            },
        ).mappings().first()
    return {
        "question_id": str(question_id),
        "user_id": str(payload.user_id),
        "status": row["status"] if row else payload.status,
        "first_seen_at": row["first_seen_at"].isoformat() if row and row["first_seen_at"] else None,
        "reviewed_at": row["reviewed_at"].isoformat() if row and row["reviewed_at"] else None,
    }


@app.get("/questions/{question_id}/correction")
def get_question_correction(question_id: UUID, user_id: UUID) -> dict:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT correct_option_id, explanation_text, answer_payload, first_seen_at, reviewed_at
                FROM question_corrections
                WHERE question_id = :question_id AND user_id = :user_id
                """
            ),
            {"question_id": str(question_id), "user_id": str(user_id)},
        ).mappings().first()
    if not row:
        return {
            "question_id": str(question_id),
            "user_id": str(user_id),
            "correct_option_id": None,
            "explanation_text": None,
            "answer_payload": {},
            "first_seen_at": None,
            "reviewed_at": None,
            "has_correction": False,
        }
    has_correction = bool(
        row["correct_option_id"]
        or (str(row["explanation_text"] or "").strip())
        or (row["answer_payload"] and row["answer_payload"] != {})
    )
    return {
        "question_id": str(question_id),
        "user_id": str(user_id),
        "correct_option_id": row["correct_option_id"],
        "explanation_text": row["explanation_text"],
        "answer_payload": row["answer_payload"] or {},
        "first_seen_at": row["first_seen_at"].isoformat() if row["first_seen_at"] else None,
        "reviewed_at": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
        "has_correction": has_correction,
    }


@app.put("/questions/{question_id}/correction")
def set_question_correction(question_id: UUID, payload: QuestionCorrectionSetIn) -> dict:
    explanation = (payload.explanation_text or "").strip() or None
    correct_option_id = (payload.correct_option_id or "").strip() or None
    answer_payload = payload.answer_payload or {}
    has_correction = bool(correct_option_id or explanation or answer_payload)
    if not has_correction:
        raise HTTPException(status_code=400, detail="Provide a correct option or an explanation")

    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT question_type FROM questions WHERE id = :question_id"),
            {"question_id": str(question_id)},
        ).mappings().first()
        if not exists:
            raise HTTPException(status_code=404, detail="Question not found")

        row = conn.execute(
            text(
                """
                INSERT INTO question_corrections (
                  user_id, question_id, correct_option_id, explanation_text, answer_payload
                )
                VALUES (
                  :user_id, :question_id, :correct_option_id, :explanation_text, CAST(:answer_payload AS JSONB)
                )
                ON CONFLICT (user_id, question_id)
                DO UPDATE SET
                  correct_option_id = COALESCE(EXCLUDED.correct_option_id, question_corrections.correct_option_id),
                  explanation_text = COALESCE(EXCLUDED.explanation_text, question_corrections.explanation_text),
                  answer_payload = CASE
                    WHEN EXCLUDED.answer_payload = '{}'::jsonb THEN question_corrections.answer_payload
                    ELSE EXCLUDED.answer_payload
                  END,
                  reviewed_at = now()
                RETURNING correct_option_id, explanation_text, answer_payload, first_seen_at, reviewed_at
                """
            ),
            {
                "user_id": str(payload.user_id),
                "question_id": str(question_id),
                "correct_option_id": correct_option_id,
                "explanation_text": explanation,
                "answer_payload": json.dumps(answer_payload, ensure_ascii=False),
            },
        ).mappings().first()
    return {
        "question_id": str(question_id),
        "user_id": str(payload.user_id),
        "correct_option_id": row["correct_option_id"] if row else correct_option_id,
        "explanation_text": row["explanation_text"] if row else explanation,
        "answer_payload": row["answer_payload"] if row else answer_payload,
        "first_seen_at": row["first_seen_at"].isoformat() if row and row["first_seen_at"] else None,
        "reviewed_at": row["reviewed_at"].isoformat() if row and row["reviewed_at"] else None,
        "has_correction": True,
    }


def _fetch_correction_job(conn, job_id: str) -> dict | None:
    row = conn.execute(
        text(
            """
            SELECT id, user_id, mode, document_id, status, total_questions,
                   processed_count, succeeded_count, failed_count, skipped_count,
                   current_question_id, cancel_requested, model, batch_size,
                   error_message, started_at, finished_at, created_at
            FROM correction_jobs
            WHERE id = :id
            """
        ),
        {"id": job_id},
    ).mappings().first()
    if not row:
        return None
    return serialize_job(dict(row))


@app.post("/corrections/jobs", response_model=CorrectionJobOut)
async def start_correction_job(payload: CorrectionJobStartIn) -> CorrectionJobOut:
    mode = payload.mode
    document_id = str(payload.document_id) if payload.document_id else None
    if mode == "document" and not document_id:
        raise HTTPException(status_code=400, detail="document_id required when mode='document'")
    if mode == "frequency" and document_id:
        raise HTTPException(status_code=400, detail="document_id must be null when mode='frequency'")

    if not settings.correction_gen_enabled:
        raise HTTPException(status_code=400, detail="Correction generation is disabled (CORRECTION_GEN_ENABLED=false)")
    if not settings.multimodal_api_key:
        raise HTTPException(status_code=400, detail="Missing MULTIMODAL_API_KEY; set it in the environment")

    user_id_str = str(payload.user_id)
    cap = max(0, int(settings.correction_gen_max_questions_per_job or 0))
    model = (settings.correction_gen_model or settings.multimodal_model or "gpt-4.1-mini").strip()
    batch_size = max(1, int(settings.correction_gen_batch_size or 5))

    with engine.begin() as conn:
        user_exists = conn.execute(
            text("SELECT 1 FROM users WHERE id = :id"), {"id": user_id_str}
        ).first()
        if not user_exists:
            raise HTTPException(status_code=404, detail="user not found")
        if document_id is not None:
            doc_exists = conn.execute(
                text("SELECT 1 FROM documents WHERE id = :id"), {"id": document_id}
            ).first()
            if not doc_exists:
                raise HTTPException(status_code=404, detail="document not found")

        total = count_pending_candidates(
            conn, user_id=user_id_str, mode=mode, document_id=document_id, cap=cap
        )
        if total == 0:
            raise HTTPException(
                status_code=400,
                detail="No pending questions to process (all already have a correction or none match)",
            )

        try:
            inserted = conn.execute(
                text(
                    """
                    INSERT INTO correction_jobs (
                      user_id, mode, document_id, status, total_questions, model, batch_size
                    )
                    VALUES (
                      :user_id, :mode, CAST(:document_id AS UUID), 'queued', :total, :model, :batch_size
                    )
                    RETURNING id, user_id, mode, document_id, status, total_questions,
                              processed_count, succeeded_count, failed_count, skipped_count,
                              current_question_id, cancel_requested, model, batch_size,
                              error_message, started_at, finished_at, created_at
                    """
                ),
                {
                    "user_id": user_id_str,
                    "mode": mode,
                    "document_id": document_id,
                    "total": total,
                    "model": model,
                    "batch_size": batch_size,
                },
            ).mappings().first()
        except Exception as exc:  # noqa: BLE001 — likely the partial unique index
            existing = conn.execute(
                text(
                    "SELECT id FROM correction_jobs WHERE status IN ('queued','running') LIMIT 1"
                )
            ).first()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "another_job_running",
                        "job_id": str(existing.id),
                    },
                ) from exc
            raise HTTPException(status_code=500, detail=f"Failed to start job: {exc}") from exc

    if not inserted:
        raise HTTPException(status_code=500, detail="Job insert returned no row")

    job_dict = serialize_job(dict(inserted))
    schedule_job(job_dict["id"])
    return CorrectionJobOut(**job_dict)


@app.get("/corrections/jobs/current")
def get_current_correction_job(response: Response) -> CorrectionJobOut | None:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, user_id, mode, document_id, status, total_questions,
                       processed_count, succeeded_count, failed_count, skipped_count,
                       current_question_id, cancel_requested, model, batch_size,
                       error_message, started_at, finished_at, created_at
                FROM correction_jobs
                WHERE status IN ('queued','running')
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
        ).mappings().first()
    if not row:
        response.status_code = 204
        return None
    return CorrectionJobOut(**serialize_job(dict(row)))


@app.get("/corrections/jobs/recent")
def list_recent_correction_jobs(limit: int = 20) -> list[CorrectionJobOut]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, user_id, mode, document_id, status, total_questions,
                       processed_count, succeeded_count, failed_count, skipped_count,
                       current_question_id, cancel_requested, model, batch_size,
                       error_message, started_at, finished_at, created_at
                FROM correction_jobs
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": max(1, min(limit, 100))},
        ).mappings()
        return [CorrectionJobOut(**serialize_job(dict(r))) for r in rows]


@app.get("/corrections/jobs/{job_id}", response_model=CorrectionJobOut)
def get_correction_job(job_id: UUID) -> CorrectionJobOut:
    with engine.begin() as conn:
        job = _fetch_correction_job(conn, str(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return CorrectionJobOut(**job)


@app.post("/corrections/jobs/{job_id}/cancel", response_model=CorrectionJobOut)
def cancel_correction_job(job_id: UUID) -> CorrectionJobOut:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                UPDATE correction_jobs
                SET cancel_requested = true
                WHERE id = :id AND status IN ('queued','running')
                RETURNING id, user_id, mode, document_id, status, total_questions,
                          processed_count, succeeded_count, failed_count, skipped_count,
                          current_question_id, cancel_requested, model, batch_size,
                          error_message, started_at, finished_at, created_at
                """
            ),
            {"id": str(job_id)},
        ).mappings().first()
        if row:
            return CorrectionJobOut(**serialize_job(dict(row)))
        existing = _fetch_correction_job(conn, str(job_id))
    if not existing:
        raise HTTPException(status_code=404, detail="job not found")
    # Already terminal — return current state without error so the UI can reconcile.
    return CorrectionJobOut(**existing)


@app.get("/corrections/jobs/{job_id}/failures")
def get_correction_job_failures(job_id: UUID) -> list[CorrectionJobFailureOut]:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT failures_json FROM correction_jobs WHERE id = :id"),
            {"id": str(job_id)},
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    failures = row["failures_json"] if isinstance(row["failures_json"], list) else []
    out: list[CorrectionJobFailureOut] = []
    for f in failures:
        if not isinstance(f, dict):
            continue
        qid = f.get("question_id")
        try:
            qid_uuid = UUID(str(qid))
        except Exception:
            continue
        out.append(
            CorrectionJobFailureOut(
                question_id=qid_uuid,
                error=str(f.get("error", ""))[:500],
                stem_preview=str(f.get("stem_preview", ""))[:240],
            )
        )
    return out


@app.get("/corrections/coverage")
def get_correction_coverage(user_id: UUID, document_id: UUID | None = None) -> dict:
    params: dict[str, str] = {"user_id": str(user_id)}
    where_extra = ""
    if document_id is not None:
        where_extra = " AND q.document_id = :document_id"
        params["document_id"] = str(document_id)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT
                  COUNT(*)::INTEGER AS total,
                  COUNT(*) FILTER (
                    WHERE qc.correct_option_id IS NOT NULL
                       OR COALESCE(NULLIF(BTRIM(qc.explanation_text), ''), NULL) IS NOT NULL
                       OR qc.answer_payload <> '{{}}'::jsonb
                  )::INTEGER AS with_correction
                FROM questions q
                LEFT JOIN question_corrections qc
                  ON qc.question_id = q.id AND qc.user_id = :user_id
                WHERE q.is_discarded = false {where_extra}
                """
            ),
            params,
        ).mappings().first()
    total = int(row["total"] or 0) if row else 0
    with_correction = int(row["with_correction"] or 0) if row else 0
    return {
        "total": total,
        "with_correction": with_correction,
        "without_correction": max(0, total - with_correction),
    }


@app.get("/reviews/stats/{user_id}")
def get_review_stats(
    user_id: UUID,
    document_id: UUID | None = None,
    tag: str | None = None,
    tag_preset: str | None = None,
    question_type: str | None = None,
) -> dict:
    clauses: list[str] = []
    params: dict[str, str] = {"user_id": str(user_id)}
    if document_id is not None:
        clauses.append("q.document_id = :document_id")
        params["document_id"] = str(document_id)
    if question_type:
        clauses.append("q.question_type = :question_type")
        params["question_type"] = question_type.strip()
    tag_values = _parse_tag_values(tag)
    if tag_values:
        tag_sql, tag_params = _question_has_any_tag_sql(param_prefix="tag_value", tag_values=tag_values)
        if tag_sql:
            clauses.append(tag_sql)
            params.update(tag_params)
    if tag_preset and tag_preset.strip():
        clauses.append(
            """
            EXISTS (
              SELECT 1
              FROM question_tags qt2
              JOIN tag_preset_tags ppt ON ppt.tag_id = qt2.tag_id
              JOIN tag_presets tp ON tp.id = ppt.preset_id
              WHERE qt2.question_id = q.id
                AND (tp.slug = :tag_preset OR tp.name = :tag_preset OR CAST(tp.id AS TEXT) = :tag_preset)
            )
            """
        )
        params["tag_preset"] = tag_preset.strip()

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    if where_sql:
        where_sql += " AND q.is_discarded = false"
    else:
        where_sql = "WHERE q.is_discarded = false"
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                  COUNT(*)::INTEGER AS total,
                  COUNT(*) FILTER (
                    WHERE qc.correct_option_id IS NOT NULL
                       OR COALESCE(NULLIF(BTRIM(qc.explanation_text), ''), NULL) IS NOT NULL
                       OR qc.answer_payload <> '{}'::jsonb
                  )::INTEGER AS with_correction,
                  COUNT(*) FILTER (WHERE qc.correct_option_id IS NOT NULL)::INTEGER AS with_correct_option,
                  COUNT(*) FILTER (
                    WHERE COALESCE(NULLIF(BTRIM(qc.explanation_text), ''), NULL) IS NOT NULL
                       OR qc.answer_payload <> '{}'::jsonb
                  )::INTEGER AS with_explanation
                FROM questions q
                LEFT JOIN question_corrections qc
                  ON qc.question_id = q.id AND qc.user_id = :user_id
                """
                + where_sql
            ),
            params,
        ).mappings().first()
    total = int(row["total"] or 0) if row else 0
    with_correction = int(row["with_correction"] or 0) if row else 0
    with_correct_option = int(row["with_correct_option"] or 0) if row else 0
    with_explanation = int(row["with_explanation"] or 0) if row else 0
    without_correction = max(0, total - with_correction)
    coverage_ratio = (with_correction / total) if total else 0.0
    return {
        "total": total,
        "with_correction": with_correction,
        "without_correction": without_correction,
        "with_correct_option": with_correct_option,
        "with_explanation": with_explanation,
        "coverage_ratio": coverage_ratio,
    }


def _create_exhaustive_simulation(payload, _random) -> dict:
    """Return ALL questions matching the filters, shuffled with tag interleaving."""
    clauses: list[str] = ["q.is_discarded = false"]
    params: dict[str, str | int] = {}
    if payload.document_id is not None:
        clauses.append("q.document_id = :document_id")
        params["document_id"] = str(payload.document_id)
    tag_values = _parse_tag_values(payload.tag)
    if tag_values:
        tag_sql, tag_params = _question_has_any_tag_sql(param_prefix="tag_value", tag_values=tag_values)
        if tag_sql:
            clauses.append(tag_sql)
            params.update(tag_params)
    if payload.tag_preset and payload.tag_preset.strip():
        clauses.append(
            """
            EXISTS (
              SELECT 1
              FROM question_tags qt2
              JOIN tag_preset_tags ppt ON ppt.tag_id = qt2.tag_id
              JOIN tag_presets tp ON tp.id = ppt.preset_id
              WHERE qt2.question_id = q.id
                AND (tp.slug = :tag_preset OR tp.name = :tag_preset OR CAST(tp.id AS TEXT) = :tag_preset)
            )
            """
        )
        params["tag_preset"] = payload.tag_preset.strip()
    if payload.only_reviewed_correct:
        if payload.user_id is None:
            raise HTTPException(
                status_code=400,
                detail="user_id is required when only_reviewed_correct is true",
            )
        clauses.append(
            """
            EXISTS (
              SELECT 1
              FROM question_corrections qc
              WHERE qc.question_id = q.id
                AND qc.user_id = :review_user_id
                AND (
                  qc.correct_option_id IS NOT NULL
                  OR COALESCE(NULLIF(BTRIM(qc.explanation_text), ''), NULL) IS NOT NULL
                  OR qc.answer_payload <> '{}'::jsonb
                )
            )
            """
        )
        params["review_user_id"] = str(payload.user_id)

    where_sql = " AND ".join(clauses)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  q.id, q.document_id, q.section, q.number_in_section, q.question_type, q.stem,
                  q.options_json, q.subparts_json, q.solution_json, q.confidence, q.needs_review,
                  (
                    SELECT COALESCE(jsonb_agg(t.slug ORDER BY t.slug), '[]'::jsonb)
                    FROM question_tags qt
                    JOIN tags t ON t.id = qt.tag_id
                    WHERE qt.question_id = q.id
                  ) AS tags_json
                FROM questions q
                WHERE
                """
                + where_sql
                + """
                ORDER BY random()
                """
            ),
            params,
        ).mappings()
        pool = list(rows)

    # Tag-interleaved shuffle for balanced topic distribution.
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in pool:
        tag_list = row["tags_json"] if isinstance(row["tags_json"], list) else []
        primary_tag = tag_list[0] if tag_list else "__untagged__"
        buckets[primary_tag].append(row)
    for bucket in buckets.values():
        _random.shuffle(bucket)
    results: list[dict] = []
    bucket_keys = list(buckets.keys())
    _random.shuffle(bucket_keys)
    round_idx = 0
    remaining = True
    while remaining:
        remaining = False
        for key in bucket_keys:
            bucket = buckets[key]
            if round_idx < len(bucket):
                results.append(bucket[round_idx])
                remaining = True
        round_idx += 1

    questions_out = []
    for row in results:
        questions_out.append(
            {
                "id": row["id"],
                "document_id": row["document_id"],
                "section": row["section"],
                "number_in_section": row["number_in_section"],
                "question_type": row["question_type"],
                "stem": row["stem"],
                "options": row["options_json"] or [],
                "subparts": row["subparts_json"] or [],
                "solution": row["solution_json"] or {},
                "confidence": float(row["confidence"]),
                "needs_review": bool(row["needs_review"]),
                "tags": row["tags_json"] or [],
            }
        )

    return {
        "requested_total": len(questions_out),
        "generated_total": len(questions_out),
        "requested_by_type": {},
        "shortage_by_type": {},
        "exhaustive": True,
        "questions": questions_out,
    }


@app.post("/simulations/custom")
def create_custom_simulation(payload: CustomSimulationIn) -> dict:
    import random as _random

    # ---------- exhaustive mode ----------
    if payload.exhaustive:
        return _create_exhaustive_simulation(payload, _random)

    requested_by_type = {
        "multiple_choice": payload.multiple_choice_count,
        "open_text": payload.open_text_count,
        "multi_part_open": payload.multi_part_open_count,
    }
    requested_total = sum(requested_by_type.values())
    if requested_total <= 0:
        raise HTTPException(status_code=400, detail="Select at least one question")

    results: list[dict] = []
    shortage: dict[str, int] = {}
    with engine.begin() as conn:
        for q_type, wanted in requested_by_type.items():
            if wanted <= 0:
                continue
            clauses = ["q.question_type = :question_type", "q.is_discarded = false"]
            params: dict[str, str | int] = {"question_type": q_type}
            if payload.document_id is not None:
                clauses.append("q.document_id = :document_id")
                params["document_id"] = str(payload.document_id)
            tag_values = _parse_tag_values(payload.tag)
            if tag_values:
                tag_sql, tag_params = _question_has_any_tag_sql(param_prefix="tag_value", tag_values=tag_values)
                if tag_sql:
                    clauses.append(tag_sql)
                    params.update(tag_params)
            if payload.tag_preset and payload.tag_preset.strip():
                clauses.append(
                    """
                    EXISTS (
                      SELECT 1
                      FROM question_tags qt2
                      JOIN tag_preset_tags ppt ON ppt.tag_id = qt2.tag_id
                      JOIN tag_presets tp ON tp.id = ppt.preset_id
                      WHERE qt2.question_id = q.id
                        AND (tp.slug = :tag_preset OR tp.name = :tag_preset OR CAST(tp.id AS TEXT) = :tag_preset)
                    )
                    """
                )
                params["tag_preset"] = payload.tag_preset.strip()
            if payload.only_reviewed_correct:
                if payload.user_id is None:
                    raise HTTPException(
                        status_code=400,
                        detail="user_id is required when only_reviewed_correct is true",
                    )
                clauses.append(
                    """
                    EXISTS (
                      SELECT 1
                      FROM question_corrections qc
                      WHERE qc.question_id = q.id
                        AND qc.user_id = :review_user_id
                        AND (
                          qc.correct_option_id IS NOT NULL
                          OR COALESCE(NULLIF(BTRIM(qc.explanation_text), ''), NULL) IS NOT NULL
                          OR qc.answer_payload <> '{}'::jsonb
                        )
                    )
                    """
                )
                params["review_user_id"] = str(payload.user_id)

            where_sql = " AND ".join(clauses)

            # ---------- tag-distributed sampling ----------
            # Fetch all eligible questions with their first tag for bucketing.
            pool_limit = max(wanted * 6, 600)
            params["pool_limit"] = pool_limit
            pool_rows = conn.execute(
                text(
                    """
                    SELECT
                      q.id, q.document_id, q.section, q.number_in_section, q.question_type, q.stem,
                      q.options_json, q.subparts_json, q.solution_json, q.confidence, q.needs_review,
                      (
                        SELECT COALESCE(jsonb_agg(t.slug ORDER BY t.slug), '[]'::jsonb)
                        FROM question_tags qt
                        JOIN tags t ON t.id = qt.tag_id
                        WHERE qt.question_id = q.id
                      ) AS tags_json
                    FROM questions q
                    WHERE
                    """
                    + where_sql
                    + """
                    ORDER BY random()
                    LIMIT :pool_limit
                    """
                ),
                params,
            ).mappings()
            pool = list(pool_rows)

            if len(pool) <= wanted:
                # Not enough questions — just take them all.
                selected = pool
            else:
                # Bucket by primary tag for equal coverage across topics.
                buckets: dict[str, list[dict]] = defaultdict(list)
                for row in pool:
                    tag_list = row["tags_json"] if isinstance(row["tags_json"], list) else []
                    primary_tag = tag_list[0] if tag_list else "__untagged__"
                    buckets[primary_tag].append(row)
                # Shuffle within each bucket.
                for bucket in buckets.values():
                    _random.shuffle(bucket)
                # Round-robin pick from buckets until we have enough.
                selected: list[dict] = []
                bucket_keys = list(buckets.keys())
                _random.shuffle(bucket_keys)
                round_idx = 0
                while len(selected) < wanted:
                    picked_this_round = False
                    for key in bucket_keys:
                        if len(selected) >= wanted:
                            break
                        bucket = buckets[key]
                        if round_idx < len(bucket):
                            selected.append(bucket[round_idx])
                            picked_this_round = True
                    round_idx += 1
                    if not picked_this_round:
                        break

            if len(selected) < wanted:
                shortage[q_type] = wanted - len(selected)
            for row in selected:
                results.append(
                    {
                        "id": row["id"],
                        "document_id": row["document_id"],
                        "section": row["section"],
                        "number_in_section": row["number_in_section"],
                        "question_type": row["question_type"],
                        "stem": row["stem"],
                        "options": row["options_json"] or [],
                        "subparts": row["subparts_json"] or [],
                        "solution": row["solution_json"] or {},
                        "confidence": float(row["confidence"]),
                        "needs_review": bool(row["needs_review"]),
                        "tags": row["tags_json"] or [],
                    }
                )

    # Shuffle the final list so question types are interleaved.
    _random.shuffle(results)

    return {
        "requested_total": requested_total,
        "generated_total": len(results),
        "requested_by_type": requested_by_type,
        "shortage_by_type": shortage,
        "questions": results,
    }


@app.get("/users/default")
def get_default_user() -> dict:
    default_email = "local@examable.internal"
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, email FROM users WHERE email = :email"),
            {"email": default_email},
        ).mappings().first()
        if row:
            return {"id": row["id"], "email": row["email"]}

        new_id = str(uuid4())
        conn.execute(
            text(
                """
                INSERT INTO users (id, email, full_name, role)
                VALUES (:id, :email, :full_name, 'student')
                """
            ),
            {"id": new_id, "email": default_email, "full_name": "Local User"},
        )
        return {"id": new_id, "email": default_email}


@app.get("/reports/simulation")
def get_simulation_report() -> dict:
    path = Path("simulation_report.json")
    if not path.exists():
        raise HTTPException(status_code=404, detail="simulation_report.json not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/reports/question-occurrences")
def get_question_occurrence_report(limit: int = 1000) -> dict:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  q.id,
                  q.stem,
                  q.question_type,
                  q.section,
                  q.number_in_section,
                  q.occurrences_count,
                  q.source_files_json,
                  q.confidence
                FROM questions q
                WHERE q.is_discarded = false
                ORDER BY q.occurrences_count DESC, q.section, q.number_in_section
                LIMIT :limit
                """
            ),
            {"limit": max(1, min(limit, 5000))},
        ).mappings()
        items: list[dict] = []
        for row in rows:
            occurrences = conn.execute(
                text(
                    """
                    SELECT source_file_name, source_section, source_number, document_id
                    FROM question_occurrences
                    WHERE question_id = :question_id
                    ORDER BY source_file_name, source_section, source_number
                    """
                ),
                {"question_id": str(row["id"])},
            ).mappings()
            items.append(
                {
                    "question_id": row["id"],
                    "question_type": row["question_type"],
                    "section": row["section"],
                    "number_in_section": row["number_in_section"],
                    "confidence": float(row["confidence"]),
                    "occurrences_count": int(row["occurrences_count"] or 1),
                    "source_files": row["source_files_json"] or [],
                    "occurrences": [dict(o) for o in occurrences],
                    "stem_preview": (row["stem"] or "")[:220],
                }
            )
        return {"total_questions": len(items), "items": items}


@app.post("/documents", response_model=UploadResponse)
async def upload_document(file: UploadFile = File(...)) -> UploadResponse:
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    doc_id = uuid4()
    dest = _uploads_root() / f"{doc_id}.pdf"
    raw = await file.read()
    dest.write_bytes(raw)
    sha = compute_sha256(dest)

    with engine.begin() as conn:
        exists = conn.execute(text("SELECT id FROM documents WHERE sha256 = :sha"), {"sha": sha}).first()
        if exists:
            raise HTTPException(status_code=409, detail="Document already ingested")

        conn.execute(
            text(
                """
                INSERT INTO documents (id, title, source_uri, sha256, ingestion_status, created_at)
                VALUES (:id, :title, :source_uri, :sha256, 'uploaded', now())
                """
            ),
            {
                "id": str(doc_id),
                "title": file.filename,
                "source_uri": str(dest),
                "sha256": sha,
            },
        )

    return UploadResponse(document_id=doc_id, source_uri=str(dest), status="uploaded")


@app.post("/documents/{document_id}/process", response_model=ParseResponse)
def process_document(document_id: UUID) -> ParseResponse:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT source_uri, title FROM documents WHERE id = :id"),
            {"id": str(document_id)},
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        source_uri = row.source_uri
        source_title = row.title
        conn.execute(
            text("UPDATE documents SET ingestion_status = 'processing' WHERE id = :id"),
            {"id": str(document_id)},
        )

    path = Path(source_uri)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Source file missing")

    try:
        extraction = extract_text_pages_with_fallback(path)
        pages = extraction.pages
        extracted = parse_unisa_questions(document_id=document_id, pages=pages)
        multimodal = enhance_with_multimodal(
            pdf_path=path,
            document_id=document_id,
            pages=pages,
            questions=extracted,
            extraction_quality=extraction.quality_score,
        )
        extracted = multimodal.questions
    except Exception as exc:  # noqa: BLE001
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE documents SET ingestion_status = 'error', ingestion_error = :err WHERE id = :id"
                ),
                {"id": str(document_id), "err": str(exc)},
            )
        raise HTTPException(status_code=500, detail=f"Parsing failed: {exc}") from exc

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM questions WHERE document_id = :id"), {"id": str(document_id)})
        for q in extracted:
            conn.execute(
                text(
                    """
                    INSERT INTO questions (
                        id, document_id, section, number_in_section, question_type, stem,
                        options_json, subparts_json, assets_json, solution_json, difficulty,
                        language, page_start, page_end, confidence, needs_review,
                        occurrences_count, source_files_json, dedupe_fingerprint, schema_version
                    )
                    VALUES (
                        :id, :document_id, :section, :number_in_section, :question_type, :stem,
                        :options_json, :subparts_json, :assets_json, :solution_json, :difficulty,
                        :language, :page_start, :page_end, :confidence, :needs_review,
                        :occurrences_count, CAST(:source_files_json AS JSONB), :dedupe_fingerprint, :schema_version
                    )
                    """
                ),
                {
                    "id": str(q.question_id),
                    "document_id": str(q.document_id),
                    "section": q.section,
                    "number_in_section": q.number_in_section,
                    "question_type": q.question_type,
                    "stem": q.stem,
                    "options_json": json.dumps([o.model_dump() for o in q.options]),
                    "subparts_json": json.dumps([s.model_dump() for s in q.subparts]),
                    "assets_json": json.dumps(q.assets),
                    # Keep only user-provided corrections; do not persist AI-extracted solutions.
                    "solution_json": json.dumps({}),
                    "difficulty": q.difficulty,
                    "language": q.language,
                    "page_start": q.source_loc.page_start,
                    "page_end": q.source_loc.page_end,
                    "confidence": q.quality.confidence,
                    "needs_review": q.quality.needs_review,
                    "occurrences_count": 1,
                    "source_files_json": json.dumps([source_title], ensure_ascii=False),
                    "dedupe_fingerprint": None,
                    "schema_version": q.schema_version,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO question_occurrences (
                      question_id, document_id, source_file_name, source_section, source_number
                    )
                    VALUES (:question_id, :document_id, :source_file_name, :source_section, :source_number)
                    ON CONFLICT (question_id, document_id, source_section, source_number) DO NOTHING
                    """
                ),
                {
                    "question_id": str(q.question_id),
                    "document_id": str(q.document_id),
                    "source_file_name": source_title,
                    "source_section": q.section,
                    "source_number": q.number_in_section,
                },
            )

        conn.execute(
            text(
                """
                UPDATE documents
                SET ingestion_status = 'processed',
                    pages = :pages,
                    ingestion_error = :ingestion_error,
                    processed_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": str(document_id),
                "pages": len(pages),
                "ingestion_error": "; ".join(extraction.warnings + multimodal.warnings)
                if extraction.warnings or multimodal.warnings
                else None,
            },
        )

        # Assign automatic tags (rule-based by default) on newly ingested questions.
        _ = auto_tag_document(conn, str(document_id), use_ai=False)

    _ = run_cleanup_dedupe()

    return ParseResponse(
        document_id=document_id,
        extracted=len(extracted),
        extraction_method=extraction.method,
        extraction_quality=extraction.quality_score,
        extraction_warnings=extraction.warnings,
        multimodal_used=multimodal.used,
        multimodal_updates=multimodal.updated_items,
        multimodal_warnings=multimodal.warnings,
    )


@app.post("/attempts")
def create_attempt(payload: AttemptIn) -> dict[str, str]:
    now = datetime.now(tz=timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO attempts (id, user_id, question_id, answered_at, is_correct, answer_payload, latency_ms, grade)
                VALUES (:id, :user_id, :question_id, :answered_at, :is_correct, :answer_payload, :latency_ms, :grade)
                """
            ),
            {
                "id": str(uuid4()),
                "user_id": str(payload.user_id),
                "question_id": str(payload.question_id),
                "answered_at": now,
                "is_correct": payload.is_correct,
                "answer_payload": json.dumps(payload.answer_payload),
                "latency_ms": payload.latency_ms,
                "grade": payload.grade,
            },
        )

        state = conn.execute(
            text(
                """
                SELECT lapses, reps
                FROM schedule_state
                WHERE user_id = :user_id AND question_id = :question_id
                """
            ),
            {"user_id": str(payload.user_id), "question_id": str(payload.question_id)},
        ).first()

        lapses = state.lapses if state else 0
        reps = state.reps if state else 0
        new_due = next_due_after_attempt(payload.is_correct, lapses, reps)
        new_lapses = lapses + (0 if payload.is_correct else 1)
        new_reps = reps + (1 if payload.is_correct else 0)
        new_state = "review" if payload.is_correct else "relearning"

        conn.execute(
            text(
                """
                INSERT INTO schedule_state (user_id, question_id, due_at, lapses, reps, state, last_reviewed_at)
                VALUES (:user_id, :question_id, :due_at, :lapses, :reps, :state, :last_reviewed_at)
                ON CONFLICT (user_id, question_id)
                DO UPDATE SET
                  due_at = EXCLUDED.due_at,
                  lapses = EXCLUDED.lapses,
                  reps = EXCLUDED.reps,
                  state = EXCLUDED.state,
                  last_reviewed_at = EXCLUDED.last_reviewed_at
                """
            ),
            {
                "user_id": str(payload.user_id),
                "question_id": str(payload.question_id),
                "due_at": new_due,
                "lapses": new_lapses,
                "reps": new_reps,
                "state": new_state,
                "last_reviewed_at": now,
            },
        )

    return {"status": "saved"}


@app.get("/study/next/{user_id}", response_model=NextQuestionResponse)
def next_question(
    user_id: UUID,
    document_id: UUID | None = None,
    tag: str | None = None,
    tag_preset: str | None = None,
    question_type: str | None = None,
    exclude_question_id: UUID | None = None,
    exclude_question_ids: str | None = None,
    prefer_new: bool = False,
    shuffle_new: bool = False,
    review_filter: str = "all",
) -> NextQuestionResponse:
    now = datetime.now(tz=timezone.utc)
    due_filter_clauses: list[str] = ["q.is_discarded = false"]
    fallback_filter_clauses: list[str] = ["q.is_discarded = false"]
    params: dict[str, str | datetime] = {"user_id": str(user_id), "now": now}

    if document_id is not None:
        due_filter_clauses.append("q.document_id = :document_id")
        fallback_filter_clauses.append("q.document_id = :document_id")
        params["document_id"] = str(document_id)

    if question_type:
        due_filter_clauses.append("q.question_type = :question_type")
        fallback_filter_clauses.append("q.question_type = :question_type")
        params["question_type"] = question_type.strip()

    if exclude_question_id is not None:
        due_filter_clauses.append("q.id <> :exclude_question_id")
        fallback_filter_clauses.append("q.id <> :exclude_question_id")
        params["exclude_question_id"] = str(exclude_question_id)

    if exclude_question_ids:
        raw_ids = [part.strip() for part in exclude_question_ids.split(",") if part.strip()]
        parsed_ids: list[str] = []
        for rid in raw_ids:
            try:
                parsed_ids.append(str(UUID(rid)))
            except Exception:
                continue
        # Keep query size bounded; enough for normal review sessions.
        parsed_ids = parsed_ids[:400]
        if parsed_ids:
            placeholders: list[str] = []
            for idx, qid in enumerate(parsed_ids):
                key = f"exclude_qid_{idx}"
                placeholders.append(f":{key}")
                params[key] = qid
            not_in_clause = f"q.id NOT IN ({', '.join(placeholders)})"
            due_filter_clauses.append(not_in_clause)
            fallback_filter_clauses.append(not_in_clause)

    if tag:
        tag_values = _parse_tag_values(tag)
        if tag_values:
            tag_sql, tag_params = _question_has_any_tag_sql(param_prefix="tag_value", tag_values=tag_values)
            if tag_sql:
                due_filter_clauses.append(tag_sql)
                fallback_filter_clauses.append(tag_sql)
                params.update(tag_params)

    if tag_preset:
        due_filter_clauses.append(
            """
            EXISTS (
              SELECT 1
              FROM question_tags qt2
              JOIN tag_preset_tags ppt ON ppt.tag_id = qt2.tag_id
              JOIN tag_presets tp ON tp.id = ppt.preset_id
              WHERE qt2.question_id = q.id
                AND (tp.slug = :tag_preset OR tp.name = :tag_preset OR CAST(tp.id AS TEXT) = :tag_preset)
            )
            """
        )
        fallback_filter_clauses.append(
            """
            EXISTS (
              SELECT 1
              FROM question_tags qt2
              JOIN tag_preset_tags ppt ON ppt.tag_id = qt2.tag_id
              JOIN tag_presets tp ON tp.id = ppt.preset_id
              WHERE qt2.question_id = q.id
                AND (tp.slug = :tag_preset OR tp.name = :tag_preset OR CAST(tp.id AS TEXT) = :tag_preset)
            )
            """
        )
        params["tag_preset"] = tag_preset.strip()

    normalized_review_filter = (review_filter or "all").strip().lower()
    if normalized_review_filter == "unreviewed":
        due_filter_clauses.append(
            """
            NOT EXISTS (
              SELECT 1
              FROM question_corrections qc
              WHERE qc.question_id = q.id
                AND qc.user_id = :user_id
                AND (
                  qc.correct_option_id IS NOT NULL
                  OR COALESCE(NULLIF(BTRIM(qc.explanation_text), ''), NULL) IS NOT NULL
                  OR qc.answer_payload <> '{}'::jsonb
                )
            )
            """
        )
        fallback_filter_clauses.append(
            """
            NOT EXISTS (
              SELECT 1
              FROM question_corrections qc
              WHERE qc.question_id = q.id
                AND qc.user_id = :user_id
                AND (
                  qc.correct_option_id IS NOT NULL
                  OR COALESCE(NULLIF(BTRIM(qc.explanation_text), ''), NULL) IS NOT NULL
                  OR qc.answer_payload <> '{}'::jsonb
                )
            )
            """
        )
    elif normalized_review_filter in {"reviewed_correct", "with_correction"}:
        due_filter_clauses.append(
            """
            EXISTS (
              SELECT 1
              FROM question_corrections qc
              WHERE qc.question_id = q.id
                AND qc.user_id = :user_id
                AND (
                  qc.correct_option_id IS NOT NULL
                  OR COALESCE(NULLIF(BTRIM(qc.explanation_text), ''), NULL) IS NOT NULL
                  OR qc.answer_payload <> '{}'::jsonb
                )
            )
            """
        )
        fallback_filter_clauses.append(
            """
            EXISTS (
              SELECT 1
              FROM question_corrections qc
              WHERE qc.question_id = q.id
                AND qc.user_id = :user_id
                AND (
                  qc.correct_option_id IS NOT NULL
                  OR COALESCE(NULLIF(BTRIM(qc.explanation_text), ''), NULL) IS NOT NULL
                  OR qc.answer_payload <> '{}'::jsonb
                )
            )
            """
        )

    due_where_extra = f" AND {' AND '.join(due_filter_clauses)}" if due_filter_clauses else ""
    fallback_where_extra = f" AND {' AND '.join(fallback_filter_clauses)}" if fallback_filter_clauses else ""
    new_question_order = "ORDER BY random()" if shuffle_new else "ORDER BY q.section, q.number_in_section"

    with engine.begin() as conn:
        if prefer_new:
            preferred_new = conn.execute(
                text(
                    """
                    SELECT q.id
                    FROM questions q
                    LEFT JOIN schedule_state s
                      ON s.question_id = q.id AND s.user_id = :user_id
                    WHERE s.question_id IS NULL
                    """
                    + fallback_where_extra
                    + f"""
                    {new_question_order}
                    LIMIT 1
                    """
                ),
                params,
            ).first()
            if preferred_new:
                conn.execute(
                    text(
                        """
                        INSERT INTO schedule_state (user_id, question_id, due_at, state)
                        VALUES (:user_id, :question_id, :due_at, 'new')
                        ON CONFLICT (user_id, question_id) DO NOTHING
                        """
                    ),
                    {"user_id": str(user_id), "question_id": str(preferred_new.id), "due_at": now},
                )
                return NextQuestionResponse(question_id=preferred_new.id, due_reason="new")

        row = conn.execute(
            text(
                """
                SELECT s.question_id, s.due_at
                FROM schedule_state s
                JOIN questions q ON q.id = s.question_id
                WHERE s.user_id = :user_id
                  AND s.due_at <= :now
                """
                + due_where_extra
                + """
                ORDER BY s.due_at ASC, q.needs_review ASC
                LIMIT 1
                """
            ),
            params,
        ).first()

        if row:
            return NextQuestionResponse(question_id=row.question_id, due_reason="due")

        # Fallback: unseen question.
        fallback = conn.execute(
            text(
                """
                SELECT q.id
                FROM questions q
                LEFT JOIN schedule_state s
                  ON s.question_id = q.id AND s.user_id = :user_id
                WHERE s.question_id IS NULL
                """
                + fallback_where_extra
                + f"""
                {new_question_order}
                LIMIT 1
                """
            ),
            params,
        ).first()

        if fallback:
            conn.execute(
                text(
                    """
                    INSERT INTO schedule_state (user_id, question_id, due_at, state)
                    VALUES (:user_id, :question_id, :due_at, 'new')
                    ON CONFLICT (user_id, question_id) DO NOTHING
                    """
                ),
                {"user_id": str(user_id), "question_id": str(fallback.id), "due_at": now},
            )
            return NextQuestionResponse(question_id=fallback.id, due_reason="new")

        # Third fallback: return the earliest scheduled question even if not due yet.
        scheduled = conn.execute(
            text(
                """
                SELECT s.question_id
                FROM schedule_state s
                JOIN questions q ON q.id = s.question_id
                WHERE s.user_id = :user_id
                """
                + due_where_extra
                + """
                ORDER BY s.due_at ASC, q.needs_review ASC
                LIMIT 1
                """
            ),
            params,
        ).first()

        if scheduled:
            return NextQuestionResponse(question_id=scheduled.question_id, due_reason="scheduled")

        raise HTTPException(status_code=404, detail="No questions available")
