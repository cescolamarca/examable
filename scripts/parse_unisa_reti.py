from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from uuid import uuid4

from pypdf import PdfReader

MCQ_START_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")
OPTION_RE = re.compile(r"^\s*([a-d])\.\s+(.*)$", re.IGNORECASE)
EXERCISE_RE = re.compile(r"^\s*ESERCIZIO\s+(\d+)", re.IGNORECASE)
THEORY_RE = re.compile(r"^\s*DOMANDA\s+TEORIA", re.IGNORECASE)
SUBPART_RE = re.compile(r"^\s*(\d+)\)\s+(.*)$")


def extract_text_pages(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    return [page.extract_text() or "" for page in reader.pages]


def clean_lines(raw_text: str) -> list[str]:
    lines = []
    for line in raw_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("-- ") and " of " in s:
            continue
        if "cognome" in s.lower() and "matricola" in s.lower():
            continue
        if s.lower().startswith("risposte"):
            continue
        lines.append(s)
    return lines


def parse_questions(document_id: str, pages: list[str]) -> list[dict]:
    text = "\n".join(clean_lines("\n".join(pages)))
    text = text.replace("\t", " ")
    text = re.sub(r"(?<!^)\s(\d{1,2}\.\s)", r"\n\1", text)
    text = re.sub(r"(?<!^)\s([a-d]\.\s)", r"\n\1", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    lines = text.splitlines()

    questions: list[dict] = []
    section = "quiz"
    counters = {"teoria": 0, "esercizio": 0}
    i = 0
    while i < len(lines):
        line = lines[i]

        if THEORY_RE.match(line):
            section = "teoria"
            i += 1
            stem = []
            while i < len(lines) and not EXERCISE_RE.match(lines[i]):
                stem.append(lines[i])
                i += 1
            counters["teoria"] += 1
            questions.append(
                {
                    "document_id": document_id,
                    "section": "teoria",
                    "number_in_section": counters["teoria"],
                    "question_type": "open_text",
                    "stem": " ".join(stem).strip(),
                    "options": [],
                    "subparts": [],
                }
            )
            continue

        if EXERCISE_RE.match(line):
            section = "esercizio"
            title = line
            i += 1
            ex_lines = []
            while i < len(lines) and not EXERCISE_RE.match(lines[i]):
                ex_lines.append(lines[i])
                i += 1
            subparts = []
            body = [title]
            for ln in ex_lines:
                m = SUBPART_RE.match(ln)
                if m:
                    subparts.append({"id": m.group(1), "prompt": m.group(2).strip()})
                else:
                    body.append(ln)
            counters["esercizio"] += 1
            questions.append(
                {
                    "document_id": document_id,
                    "section": "esercizio",
                    "number_in_section": counters["esercizio"],
                    "question_type": "multi_part_open" if subparts else "open_text",
                    "stem": " ".join(body).strip(),
                    "options": [],
                    "subparts": subparts,
                }
            )
            continue

        if section == "quiz":
            start = MCQ_START_RE.match(line)
            if start:
                q_number = int(start.group(1))
                stem_lines = [start.group(2)]
                i += 1
                options = []
                while i < len(lines):
                    if MCQ_START_RE.match(lines[i]) or THEORY_RE.match(lines[i]) or EXERCISE_RE.match(lines[i]):
                        break
                    opt = OPTION_RE.match(lines[i])
                    if opt:
                        options.append({"id": opt.group(1).lower(), "text": opt.group(2)})
                    elif not options:
                        stem_lines.append(lines[i])
                    i += 1

                questions.append(
                    {
                        "document_id": document_id,
                        "section": "quiz",
                        "number_in_section": q_number,
                        "question_type": "multiple_choice",
                        "stem": " ".join(stem_lines).strip(),
                        "options": options,
                        "subparts": [],
                    }
                )
                continue

        i += 1

    return questions


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse UNISA Reti PDF into normalized JSON.")
    parser.add_argument("pdf", type=Path, help="Path to source PDF")
    parser.add_argument("--out", type=Path, default=Path("parsed_questions.json"), help="Output JSON file")
    args = parser.parse_args()

    if not args.pdf.exists():
        raise SystemExit(f"PDF not found: {args.pdf}")

    pages = extract_text_pages(args.pdf)
    document_id = str(uuid4())
    questions = parse_questions(document_id=document_id, pages=pages)

    payload = {
        "document_id": document_id,
        "count": len(questions),
        "questions": questions,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Parsed {len(questions)} questions into {args.out}")


if __name__ == "__main__":
    main()
