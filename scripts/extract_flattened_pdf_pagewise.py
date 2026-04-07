from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


@dataclass
class ExtractedQuestion:
    page_number: int
    question_type: str
    section: str
    number_in_section: int
    stem: str
    options: list[dict[str, str]]
    subparts: list[dict[str, str]]
    tags: list[str]
    confidence: float


def _canon(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").lower()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _safe_int(value: Any, default: int) -> int:
    try:
        v = int(value)
        return v if v > 0 else default
    except Exception:
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        v = float(value)
        if v < 0:
            return 0.0
        if v > 1:
            return 1.0
        return v
    except Exception:
        return default


def _fingerprint(q: ExtractedQuestion) -> str:
    options_blob = "|".join(f"{o.get('id','')}:{_canon(o.get('text',''))}" for o in q.options)
    subparts_blob = "|".join(f"{s.get('id','')}:{_canon(s.get('prompt',''))}" for s in q.subparts)
    payload = f"{q.question_type}##{_canon(q.stem)}##{options_blob}##{subparts_blob}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def render_page_png_b64(pdf_path: Path, page_index: int, dpi: int = 220) -> str:
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        png_bytes = pix.tobytes("png")
        return base64.b64encode(png_bytes).decode("utf-8")
    finally:
        doc.close()


def build_prompt(page_number: int) -> str:
    return (
        "Estrai tutte le domande presenti nella pagina di una prova/esame di Reti di Calcolatori.\n"
        "L'input e' una singola immagine di pagina PDF (spesso scannerizzata).\n"
        "Rispondi SOLO con JSON valido con questa struttura:\n"
        "{\n"
        '  "page_number": <int>,\n'
        '  "is_question_page": <true|false>,\n'
        '  "questions": [\n'
        "    {\n"
        '      "question_type": "multiple_choice|open_text|multi_part_open",\n'
        '      "section": "quiz|teoria|esercizio",\n'
        '      "number_in_section": <int>,\n'
        '      "stem": "<testo domanda>",\n'
        '      "options": [{"id":"a","text":"..."},{"id":"b","text":"..."}],\n'
        '      "subparts": [{"id":"1","prompt":"..."}],\n'
        '      "tags": ["..."],\n'
        '      "confidence": <0..1>\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "- Mantieni il testo originale in italiano.\n"
        "- Non inventare domande non visibili.\n"
        "- Se la pagina e' indice/copertina/bianca: is_question_page=false e questions=[].\n"
        f"- Questa richiesta riguarda pagina {page_number}.\n"
    )


def call_llm_extract_page(page_number: int, image_b64: str, retries: int = 2) -> dict:
    if not settings.multimodal_api_key:
        raise RuntimeError("Missing MULTIMODAL_API_KEY")
    url = settings.multimodal_api_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.multimodal_api_key}"}
    payload = {
        "model": settings.multimodal_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Sei un motore OCR+estrazione strutturata. Restituisci solo JSON valido."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": build_prompt(page_number)},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            },
        ],
    }
    last_err: Exception | None = None
    for _ in range(retries + 1):
        try:
            with httpx.Client(timeout=45.0) as client:
                resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("Model output is not a JSON object")
            return parsed
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise RuntimeError(f"Failed page {page_number}: {last_err}") from last_err


def normalize_questions(page_number: int, payload: dict) -> list[ExtractedQuestion]:
    items = payload.get("questions", [])
    if not isinstance(items, list):
        return []
    out: list[ExtractedQuestion] = []
    for idx, q in enumerate(items, start=1):
        if not isinstance(q, dict):
            continue
        q_type = str(q.get("question_type", "open_text")).strip()
        if q_type not in {"multiple_choice", "open_text", "multi_part_open"}:
            q_type = "open_text"
        section = str(q.get("section", "quiz")).strip()
        if section not in {"quiz", "teoria", "esercizio"}:
            section = "quiz"
        stem = str(q.get("stem", "")).strip()
        if not stem:
            continue

        options: list[dict[str, str]] = []
        for o in q.get("options", []):
            if not isinstance(o, dict):
                continue
            oid = str(o.get("id", "")).strip().lower()
            otext = str(o.get("text", "")).strip()
            if oid and otext:
                options.append({"id": oid, "text": otext})
        subparts: list[dict[str, str]] = []
        for s in q.get("subparts", []):
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id", "")).strip()
            sprompt = str(s.get("prompt", "")).strip()
            if sid and sprompt:
                subparts.append({"id": sid, "prompt": sprompt})
        tags = [str(t).strip() for t in q.get("tags", []) if str(t).strip()]
        conf = _safe_float(q.get("confidence", 0.75), 0.75)
        out.append(
            ExtractedQuestion(
                page_number=page_number,
                question_type=q_type,
                section=section,
                number_in_section=_safe_int(q.get("number_in_section"), idx),
                stem=stem,
                options=options,
                subparts=subparts,
                tags=tags,
                confidence=conf,
            )
        )
    return out


def run_pipeline(pdf_path: Path, out_dir: Path, start_page: int = 1, end_page: int | None = None) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    total_pages = doc.page_count
    doc.close()
    sp = max(1, start_page)
    ep = min(total_pages, end_page if end_page is not None else total_pages)

    per_page: list[dict] = []
    all_questions: list[ExtractedQuestion] = []
    for page_num in range(sp, ep + 1):
        image_b64 = render_page_png_b64(pdf_path, page_num - 1)
        payload = call_llm_extract_page(page_num, image_b64=image_b64, retries=2)
        questions = normalize_questions(page_num, payload)
        all_questions.extend(questions)
        per_page.append(
            {
                "page_number": page_num,
                "is_question_page": bool(payload.get("is_question_page", len(questions) > 0)),
                "questions_count": len(questions),
                "raw_output": payload,
            }
        )

    grouped: dict[str, dict] = {}
    flat_rows: list[dict] = []
    for q in all_questions:
        fp = _fingerprint(q)
        flat_rows.append(
            {
                "fingerprint": fp,
                "page_number": q.page_number,
                "question_type": q.question_type,
                "section": q.section,
                "number_in_section": q.number_in_section,
                "stem": q.stem,
                "options": q.options,
                "subparts": q.subparts,
                "tags": q.tags,
                "confidence": q.confidence,
                "source_file": pdf_path.name,
            }
        )
        if fp not in grouped:
            grouped[fp] = {
                "fingerprint": fp,
                "question_type": q.question_type,
                "stem": q.stem,
                "tags": q.tags,
                "confidence_max": q.confidence,
                "occurrences_count": 0,
                "occurrences": [],
            }
        grouped[fp]["occurrences_count"] += 1
        grouped[fp]["confidence_max"] = max(grouped[fp]["confidence_max"], q.confidence)
        grouped[fp]["occurrences"].append(
            {
                "page_number": q.page_number,
                "section": q.section,
                "number_in_section": q.number_in_section,
                "source_file": pdf_path.name,
            }
        )

    unique_questions = sorted(grouped.values(), key=lambda x: x["occurrences_count"], reverse=True)
    report = {
        "summary": {
            "pdf": str(pdf_path),
            "page_range": [sp, ep],
            "pages_processed": ep - sp + 1,
            "questions_total_extracted": len(flat_rows),
            "questions_unique": len(unique_questions),
            "questions_duplicates": len(flat_rows) - len(unique_questions),
        },
        "pages": per_page,
        "questions": unique_questions,
    }
    report_path = out_dir / "pagewise_ai_occurrence_report.json"
    flat_path = out_dir / "pagewise_ai_questions_all.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    flat_path.write_text(json.dumps({"total_questions": len(flat_rows), "questions": flat_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path, flat_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Page-wise image AI extraction for flattened PDFs")
    parser.add_argument("--pdf", type=str, default=str(Path(r"c:\Users\nextc\Documents\domande_intercorso_reti_banca-piatto.pdf")))
    parser.add_argument("--out-dir", type=str, default=str(PROJECT_ROOT))
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report_path, flat_path = run_pipeline(
        pdf_path=Path(args.pdf),
        out_dir=Path(args.out_dir),
        start_page=args.start_page,
        end_page=args.end_page,
    )
    print(str(report_path))
    print(str(flat_path))


if __name__ == "__main__":
    main()
