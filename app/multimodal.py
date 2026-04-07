from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from app.config import settings
from app.schemas import OptionItem, Quality, QuestionOut, SourceLoc, SubpartItem


@dataclass
class MultimodalOutcome:
    questions: list[QuestionOut]
    used: bool = False
    updated_items: int = 0
    warnings: list[str] = field(default_factory=list)


def _needs_multimodal(quality_score: float, questions: list[QuestionOut]) -> bool:
    return quality_score < settings.multimodal_min_quality


def _extract_page_images(pdf_path: Path, max_pages: int) -> list[str]:
    if not shutil.which("pdftoppm"):
        # Fallback: render directly via PyMuPDF if available in runtime.
        try:
            import fitz  # type: ignore
        except Exception:
            return []

        image_b64: list[str] = []
        doc = fitz.open(str(pdf_path))
        try:
            page_total = min(max_pages, len(doc))
            for idx in range(page_total):
                page = doc[idx]
                pix = page.get_pixmap(dpi=160, alpha=False)
                image_b64.append(base64.b64encode(pix.tobytes("png")).decode("utf-8"))
        finally:
            doc.close()
        return image_b64

    image_b64: list[str] = []
    with tempfile.TemporaryDirectory(prefix="examable_mm_") as tmp:
        tmp_dir = Path(tmp)
        for page_idx in range(1, max_pages + 1):
            base = tmp_dir / f"p_{page_idx}"
            cmd = [
                "pdftoppm",
                "-f",
                str(page_idx),
                "-l",
                str(page_idx),
                "-singlefile",
                "-png",
                str(pdf_path),
                str(base),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                break
            image_path = base.with_suffix(".png")
            if not image_path.exists():
                break
            image_b64.append(base64.b64encode(image_path.read_bytes()).decode("utf-8"))
    return image_b64


def _build_prompt(text_pages: list[str], questions: list[QuestionOut]) -> str:
    short_text = "\n\n".join(text_pages[: settings.multimodal_max_pages])[:15000]
    weak = [
        {
            "section": q.section,
            "number_in_section": q.number_in_section,
            "question_type": q.question_type,
            "stem_preview": q.stem[:180],
            "option_count": len(q.options),
        }
        for q in questions
        if (q.question_type == "multiple_choice" and len(q.options) < 4) or q.quality.needs_review
    ]
    return (
        "Extract exam questions from the provided PDF context.\n"
        "Return JSON only with this shape: "
        '{"questions":[{"section":"quiz|teoria|esercizio","number_in_section":1,'
        '"question_type":"multiple_choice|open_text|multi_part_open","stem":"...",'
        '"options":[{"id":"a","text":"..."}],"subparts":[{"id":"1","prompt":"..."}],"tags":["..."]}]}\n'
        "Keep source ordering. Do not invent content. Use Italian language for extracted text.\n\n"
        f"CURRENT LOW-CONFIDENCE ITEMS:\n{json.dumps(weak, ensure_ascii=False)}\n\n"
        f"TEXT CONTEXT:\n{short_text}"
    )


def _call_multimodal_llm(
    prompt: str,
    image_b64: list[str],
) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    if not settings.multimodal_api_key:
        return [], ["Multimodal skipped: missing MULTIMODAL_API_KEY"]

    content: list[dict] = [{"type": "text", "text": prompt}]
    for raw in image_b64:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{raw}"},
            }
        )

    payload = {
        "model": settings.multimodal_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "You are a precise document extraction engine. Return strict JSON only.",
            },
            {"role": "user", "content": content},
        ],
    }

    url = settings.multimodal_api_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.multimodal_api_key}"}

    try:
        with httpx.Client(timeout=25.0) as client:
            response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        raw = data["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
        items = parsed.get("questions", [])
        if not isinstance(items, list):
            return [], ["Multimodal output malformed: `questions` is not a list"]
        return items, warnings
    except Exception as exc:  # noqa: BLE001
        return [], [f"Multimodal provider error: {exc}"]


def _normalize_question(
    item: dict,
    document_id: UUID,
    pages_count: int,
) -> QuestionOut | None:
    try:
        options = [
            OptionItem(id=str(opt.get("id", "")).strip(), text=str(opt.get("text", "")).strip())
            for opt in item.get("options", [])
            if str(opt.get("id", "")).strip() and str(opt.get("text", "")).strip()
        ]
        subparts = [
            SubpartItem(id=str(sp.get("id", "")).strip(), prompt=str(sp.get("prompt", "")).strip())
            for sp in item.get("subparts", [])
            if str(sp.get("id", "")).strip() and str(sp.get("prompt", "")).strip()
        ]
        return QuestionOut(
            question_id=uuid4(),
            document_id=document_id,
            section=item.get("section", "quiz"),
            number_in_section=int(item.get("number_in_section", 1)),
            question_type=item.get("question_type", "open_text"),
            stem=str(item.get("stem", "")).strip(),
            options=options,
            subparts=subparts,
            tags=[str(t).strip() for t in item.get("tags", []) if str(t).strip()] or ["reti"],
            source_loc=SourceLoc(page_start=1, page_end=max(1, pages_count)),
            quality=Quality(confidence=0.75, needs_review=False),
        )
    except Exception:
        return None


def _merge_questions(existing: list[QuestionOut], candidates: list[QuestionOut]) -> tuple[list[QuestionOut], int]:
    merged: dict[tuple[str, int], QuestionOut] = {(q.section, q.number_in_section): q for q in existing}
    updates = 0

    for cand in candidates:
        key = (cand.section, cand.number_in_section)
        if key not in merged:
            merged[key] = cand
            updates += 1
            continue

        cur = merged[key]
        changed = False
        if len(cand.stem) > len(cur.stem) * 1.15:
            cur.stem = cand.stem
            changed = True

        if cur.question_type == "multiple_choice" and len(cur.options) < len(cand.options):
            cur.options = cand.options
            changed = True

        if len(cur.subparts) < len(cand.subparts):
            cur.subparts = cand.subparts
            changed = True

        tags = sorted(set(cur.tags).union(cand.tags))
        if tags != cur.tags:
            cur.tags = tags
            changed = True

        if changed:
            cur.quality = Quality(
                confidence=min(0.95, cur.quality.confidence + 0.1),
                needs_review=False,
                warnings=cur.quality.warnings,
            )
            updates += 1

    out = list(merged.values())
    out.sort(key=lambda q: (q.section, q.number_in_section))
    return out, updates


def enhance_with_multimodal(
    pdf_path: Path,
    document_id: UUID,
    pages: list[str],
    questions: list[QuestionOut],
    extraction_quality: float,
) -> MultimodalOutcome:
    if not settings.multimodal_enabled:
        return MultimodalOutcome(questions=questions, warnings=["Multimodal disabled"])
    if not _needs_multimodal(extraction_quality, questions):
        return MultimodalOutcome(questions=questions, warnings=["Multimodal skipped: quality is sufficient"])

    image_b64 = _extract_page_images(pdf_path, settings.multimodal_max_pages)
    prompt = _build_prompt(text_pages=pages, questions=questions)
    raw_items, warnings = _call_multimodal_llm(prompt=prompt, image_b64=image_b64)
    candidates = [
        q
        for item in raw_items
        if (q := _normalize_question(item=item, document_id=document_id, pages_count=len(pages))) is not None
    ]
    if not candidates:
        return MultimodalOutcome(questions=questions, used=True, warnings=warnings + ["No multimodal candidates returned"])

    merged, updates = _merge_questions(existing=questions, candidates=candidates)
    return MultimodalOutcome(questions=merged, used=True, updated_items=updates, warnings=warnings)
