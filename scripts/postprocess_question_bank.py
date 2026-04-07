from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

IN_PATH = Path(r"c:\Users\nextc\Examable\pagewise_ai_questions_all.json")
OUT_BANK = Path(r"c:\Users\nextc\Examable\banca_domande_postprocessed.json")
OUT_REPORT = Path(r"c:\Users\nextc\Examable\banca_domande_postprocess_report.json")


def _canon(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").lower()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _canon_option_text(text: str) -> str:
    """Come _canon sullo stem, ma mantiene : / . _ ecc. così URL diverse restano distinte."""
    s = unicodedata.normalize("NFKC", text or "").strip().lower()
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def _slug_tag(tag: str) -> str:
    s = _canon(tag).replace(" ", "-")
    return s.strip("-")


def _looks_like_noise(stem: str) -> bool:
    s = (stem or "").strip()
    c = _canon(s)
    if not c:
        return True
    if len(c) < 12:
        return True
    noisy_patterns = [
        r"^altre prove$",
        r"^intercorso e simili$",
        r"^test di capitolo$",
        r"^pagina \d+$",
        r"^esame[_ \-]?\d+.*\.pdf$",
        r"^traccia[_ \-]?\d+.*\.pdf$",
    ]
    return any(re.match(p, c) for p in noisy_patterns)


def _normalize_options(options: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for o in options or []:
        oid = _canon(str(o.get("id", "")))
        txt = str(o.get("text", "")).strip()
        if not oid or not txt:
            continue
        out.append({"id": oid[:8], "text": txt})
    # dedupe by id, keep first
    seen: set[str] = set()
    final: list[dict[str, str]] = []
    for o in out:
        if o["id"] in seen:
            continue
        seen.add(o["id"])
        final.append(o)
    return final


def _canonicalize_mcq_options(options: list[dict[str, str]]) -> list[dict[str, str]]:
    """Stesso insieme di risposte con lettere a,b,c… in ordine stabile (testo canonico)."""
    if not options:
        return []
    letters = "abcdefghijklmnopqrstuvwxyz"
    sorted_opts = sorted(options, key=lambda o: (_canon_option_text(o["text"]), o["id"]))
    out: list[dict[str, str]] = []
    for i, o in enumerate(sorted_opts):
        oid = letters[i] if i < len(letters) else str(i + 1)
        out.append({"id": oid, "text": o["text"]})
    return out


def _normalize_subparts(parts: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for p in parts or []:
        pid = str(p.get("id", "")).strip()
        prompt = str(p.get("prompt", "")).strip()
        if pid and prompt:
            out.append({"id": pid, "prompt": prompt})
    return out


def _semantic_merge_payload(candidate: dict[str, Any]) -> str:
    """Chiave di merge: stesso quesito + stesso insieme di opzioni (per MCQ ignora ordine e id lettere)."""
    q_type = str(candidate.get("question_type", "open_text"))
    stem = _canon(str(candidate.get("stem", "")))
    stem = re.sub(r"\b\d+\b", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    sub_sorted = sorted(
        candidate.get("subparts", []),
        key=lambda s: (str(s.get("id", "")), _canon(str(s.get("prompt", "")))),
    )
    sub_blob = "|".join(f"{s['id']}:{_canon(s['prompt'])}" for s in sub_sorted)
    if q_type == "multiple_choice":
        opt_texts = sorted(_canon_option_text(o["text"]) for o in candidate.get("options", []))
        opts_blob = "||".join(opt_texts)
    else:
        opts_sorted = sorted(candidate.get("options", []), key=lambda o: o["id"])
        opts_blob = "|".join(f"{o['id']}:{_canon_option_text(o['text'])}" for o in opts_sorted)
    return f"{q_type}##{stem}##{opts_blob}##{sub_blob}"


def _merge_key_from_candidate(candidate: dict[str, Any]) -> str:
    return hashlib.sha1(_semantic_merge_payload(candidate).encode("utf-8")).hexdigest()


def main() -> None:
    src = json.loads(IN_PATH.read_text(encoding="utf-8"))
    rows = src.get("questions", [])
    total_in = len(rows)

    grouped: dict[str, dict[str, Any]] = {}
    removed_noise = 0
    removed_structural = 0

    for raw in rows:
        stem = str(raw.get("stem", "")).strip()
        if _looks_like_noise(stem):
            removed_noise += 1
            continue

        q_type = str(raw.get("question_type", "open_text")).strip()
        if q_type not in {"multiple_choice", "open_text", "multi_part_open"}:
            q_type = "open_text"
        options = _normalize_options(raw.get("options", []))
        if q_type == "multiple_choice":
            options = _canonicalize_mcq_options(options)
        subparts = _normalize_subparts(raw.get("subparts", []))

        # Structural quality filters.
        if q_type == "multiple_choice" and len(options) < 2:
            removed_structural += 1
            continue
        if q_type == "open_text" and len(_canon(stem)) < 20:
            removed_structural += 1
            continue

        candidate = {
            "question_type": q_type,
            "section": str(raw.get("section", "quiz")),
            "number_in_section": int(raw.get("number_in_section", 1) or 1),
            "stem": stem,
            "options": options,
            "subparts": subparts,
            "tags": sorted({_slug_tag(str(t)) for t in raw.get("tags", []) if str(t).strip()}),
            "confidence": float(raw.get("confidence", 0.75) or 0.75),
            "source_file": str(raw.get("source_file", "")),
            "page_number": int(raw.get("page_number", 0) or 0),
        }
        merge_key = _merge_key_from_candidate(candidate)
        if not merge_key:
            removed_structural += 1
            continue

        if merge_key not in grouped:
            grouped[merge_key] = {
                "question_id": merge_key,
                "fingerprint": merge_key,
                "question_type": candidate["question_type"],
                "section": candidate["section"],
                "stem": candidate["stem"],
                "options": candidate["options"],
                "subparts": candidate["subparts"],
                "tags": set(candidate["tags"]),
                "confidence_max": candidate["confidence"],
                "occurrences_count": 0,
                "occurrences": [],
            }

        g = grouped[merge_key]
        g["tags"].update(candidate["tags"])
        g["confidence_max"] = max(g["confidence_max"], candidate["confidence"])
        # prefer richer text/options when duplicates differ slightly
        if len(candidate["stem"]) > len(g["stem"]):
            g["stem"] = candidate["stem"]
        if len(candidate["options"]) > len(g["options"]):
            g["options"] = candidate["options"]
        if len(candidate["subparts"]) > len(g["subparts"]):
            g["subparts"] = candidate["subparts"]
        g["occurrences_count"] += 1
        g["occurrences"].append(
            {
                "source_file": candidate["source_file"],
                "page_number": candidate["page_number"],
                "section": candidate["section"],
                "number_in_section": candidate["number_in_section"],
            }
        )

    questions = []
    for q in grouped.values():
        q["tags"] = sorted(q["tags"])
        q["occurrences"] = sorted(
            q["occurrences"],
            key=lambda o: (o["source_file"], o["page_number"], o["section"], o["number_in_section"]),
        )
        questions.append(q)

    questions.sort(key=lambda q: (q["question_type"], -q["occurrences_count"], q["stem"][:60].lower()))

    bank = {
        "summary": {
            "input_rows": total_in,
            "removed_noise": removed_noise,
            "removed_structural": removed_structural,
            "output_unique_questions": len(questions),
            "output_total_occurrences": sum(q["occurrences_count"] for q in questions),
        },
        "questions": questions,
    }
    OUT_BANK.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_REPORT.write_text(json.dumps(bank["summary"], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(bank["summary"], ensure_ascii=False))
    print(str(OUT_BANK))


if __name__ == "__main__":
    main()
