"""Rule-based parser that turns extracted exam text into structured questions."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from app.schemas import Quality, QuestionOut, SourceLoc

MCQ_START_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")
MCQ_PAREN_RE = re.compile(r"^\s*(\d+)\)\s+(.*)$")
DOMANDA_RE = re.compile(r"^\s*DOMANDA\s+(\d+)\s*$", re.IGNORECASE)
OPTION_RE = re.compile(r"^\s*([a-d])\.\s+(.*)$", re.IGNORECASE)
EXERCISE_RE = re.compile(r"^\s*ESERCIZIO\s+(\d+)", re.IGNORECASE)
THEORY_RE = re.compile(r"^\s*DOMANDA\s+TEORIA", re.IGNORECASE)
SUBPART_RE = re.compile(r"^\s*(\d+)\)\s+(.*)$")


def _clean_lines(raw_text: str) -> list[str]:
    lines = []
    for line in raw_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("-- ") and " of " in s:
            continue
        if s.lower().startswith("studente"):
            continue
        if "cognome" in s.lower() and "matricola" in s.lower():
            continue
        if s.lower().startswith("risposte"):
            continue
        if s.lower() in {"corrette", "teoria", "tot"}:
            continue
        lines.append(s)
    return lines


def _tag_question(stem: str) -> list[str]:
    s = stem.lower()
    tags = {"reti"}
    mapping = {
        "tcp": "tcp",
        "udp": "udp",
        "dns": "dns",
        "http": "http",
        "smtp": "smtp",
        "dhcp": "dhcp",
        "icmp": "icmp",
        "ipv4": "ipv4",
        "ipv6": "ipv6",
        "ethernet": "ethernet",
        "go-back-n": "arq",
        "stop-and-wait": "arq",
        "routing": "routing",
    }
    for key, tag in mapping.items():
        if key in s:
            tags.add(tag)
    return sorted(tags)


def _split_question_markers(text: str) -> str:
    text = text.replace("\t", " ")
    text = re.sub(r"(?i)(?<!\n)\bDOMANDA\s+(\d+)\b", r"\nDOMANDA \1", text)
    text = re.sub(r"(?<!\n)(\d{1,3}\)\s)", r"\n\1", text)
    text = re.sub(r"(?<!^)\s(\d{1,2}\.\s)", r"\n\1", text)
    text = re.sub(r"(?<!^)\s([a-d]\.\s)", r"\n\1", text, flags=re.IGNORECASE)
    return re.sub(r"[ ]{2,}", " ", text)


def _resolve_number(section: str, preferred: int, used: dict[str, set[int]]) -> int:
    taken = used.setdefault(section, set())
    number = preferred
    while number in taken:
        number += 1
    taken.add(number)
    return number


def _append_mcq(
    questions: list[QuestionOut],
    *,
    document_id: UUID,
    section: str,
    q_number: int,
    stem_lines: list[str],
    options: list[dict[str, str]],
    pages: list[str],
) -> None:
    stem = " ".join(stem_lines).strip()
    warnings: list[str] = []
    needs_review = False
    confidence = 0.95
    if len(options) < 2:
        warnings.append("Insufficient options detected")
        needs_review = True
        confidence = 0.6

    questions.append(
        QuestionOut(
            question_id=uuid4(),
            document_id=document_id,
            section=section,
            number_in_section=q_number,
            question_type="multiple_choice",
            stem=stem,
            options=options,
            tags=_tag_question(stem),
            source_loc=SourceLoc(page_start=1, page_end=len(pages)),
            quality=Quality(confidence=confidence, needs_review=needs_review, warnings=warnings),
        )
    )


def parse_unisa_questions(document_id: UUID, pages: list[str]) -> list[QuestionOut]:
    text = _split_question_markers("\n".join(_clean_lines("\n".join(pages))))
    lines = text.splitlines()

    questions: list[QuestionOut] = []
    section = "quiz"
    counters = {"quiz": 0, "teoria": 0, "esercizio": 0}
    used_numbers: dict[str, set[int]] = {"quiz": set(), "teoria": set(), "esercizio": set()}

    i = 0
    while i < len(lines):
        line = lines[i]

        if THEORY_RE.match(line):
            section = "teoria"
            i += 1
            stem_lines = []
            while i < len(lines) and not EXERCISE_RE.match(lines[i]):
                stem_lines.append(lines[i])
                i += 1
            stem = " ".join(stem_lines).strip()
            if stem:
                counters["teoria"] += 1
                questions.append(
                    QuestionOut(
                        question_id=uuid4(),
                        document_id=document_id,
                        section="teoria",
                        number_in_section=counters["teoria"],
                        question_type="open_text",
                        stem=stem,
                        tags=_tag_question(stem),
                        source_loc=SourceLoc(page_start=1, page_end=len(pages)),
                        quality=Quality(confidence=0.9, needs_review=False),
                    )
                )
            continue

        ex_match = EXERCISE_RE.match(line)
        if ex_match:
            section = "esercizio"
            exercise_title = line
            i += 1
            ex_lines = []
            while i < len(lines) and not EXERCISE_RE.match(lines[i]):
                ex_lines.append(lines[i])
                i += 1

            subparts = []
            stem_lines = [exercise_title]
            for ex_line in ex_lines:
                sp = SUBPART_RE.match(ex_line)
                if sp:
                    subparts.append({"id": sp.group(1), "prompt": sp.group(2).strip()})
                else:
                    stem_lines.append(ex_line)
            stem = " ".join(stem_lines).strip()
            counters["esercizio"] += 1
            q_type = "multi_part_open" if subparts else "open_text"
            questions.append(
                QuestionOut(
                    question_id=uuid4(),
                    document_id=document_id,
                    section="esercizio",
                    number_in_section=counters["esercizio"],
                    question_type=q_type,
                    stem=stem,
                    subparts=subparts,
                    tags=_tag_question(stem),
                    source_loc=SourceLoc(page_start=1, page_end=len(pages)),
                    quality=Quality(confidence=0.85, needs_review=False),
                )
            )
            continue

        domanda = DOMANDA_RE.match(line)
        if domanda:
            section = "quiz"
            q_number = int(domanda.group(1))
            i += 1
            stem_lines: list[str] = []
            options: list[dict[str, str]] = []
            while i < len(lines):
                if (
                    DOMANDA_RE.match(lines[i])
                    or MCQ_START_RE.match(lines[i])
                    or MCQ_PAREN_RE.match(lines[i])
                    or THEORY_RE.match(lines[i])
                    or EXERCISE_RE.match(lines[i])
                ):
                    break
                opt = OPTION_RE.match(lines[i])
                if opt:
                    options.append({"id": opt.group(1).lower(), "text": opt.group(2).strip()})
                elif not options:
                    stem_lines.append(lines[i].strip())
                i += 1

            counters["quiz"] = max(counters["quiz"] + 1, q_number)
            _append_mcq(
                questions,
                document_id=document_id,
                section="quiz",
                q_number=_resolve_number("quiz", q_number, used_numbers),
                stem_lines=stem_lines,
                options=options,
                pages=pages,
            )
            continue

        if section == "quiz":
            mcq = MCQ_START_RE.match(line) or MCQ_PAREN_RE.match(line)
            if mcq:
                q_number = int(mcq.group(1))
                stem_lines = [mcq.group(2).strip()]
                i += 1
                options = []
                while i < len(lines):
                    if (
                        MCQ_START_RE.match(lines[i])
                        or MCQ_PAREN_RE.match(lines[i])
                        or DOMANDA_RE.match(lines[i])
                        or THEORY_RE.match(lines[i])
                        or EXERCISE_RE.match(lines[i])
                    ):
                        break
                    opt = OPTION_RE.match(lines[i])
                    if opt:
                        options.append({"id": opt.group(1).lower(), "text": opt.group(2).strip()})
                    else:
                        if not options:
                            stem_lines.append(lines[i].strip())
                    i += 1

                counters["quiz"] = max(counters["quiz"] + 1, q_number)
                if len(options) >= 2:
                    _append_mcq(
                        questions,
                        document_id=document_id,
                        section="quiz",
                        q_number=_resolve_number("quiz", q_number, used_numbers),
                        stem_lines=stem_lines,
                        options=options,
                        pages=pages,
                    )
                else:
                    stem = " ".join(stem_lines).strip()
                    counters["teoria"] += 1
                    questions.append(
                        QuestionOut(
                            question_id=uuid4(),
                            document_id=document_id,
                            section="teoria",
                            number_in_section=_resolve_number("teoria", counters["teoria"], used_numbers),
                            question_type="open_text",
                            stem=stem,
                            tags=_tag_question(stem),
                            source_loc=SourceLoc(page_start=1, page_end=len(pages)),
                            quality=Quality(confidence=0.75, needs_review=True),
                        )
                    )
                continue

        i += 1

    return sorted(questions, key=lambda q: (q.section, q.number_in_section))
