# Examable

[![CI](https://github.com/cescolamarca/examable/actions/workflows/ci.yml/badge.svg)](https://github.com/cescolamarca/examable/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-PostgreSQL-009688)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**Examable turns a folder of past-exam PDFs into a deduplicated question bank, then helps you study it with
practice simulations and spaced repetition.**

University courses reuse their exam questions: the same multiple-choice question shows up in several sessions,
with a different number and the answers shuffled. Examable extracts every question from the PDFs (OCR included,
for scans), recognises the repeats, tracks which sessions each question came from and how often it recurs,
and schedules your reviews with the SM-2 algorithm. It was built for the *Reti di Calcolatori* exams at the
University of Salerno, so the UI is in Italian.

![Practice simulation with feedback on a wrong answer](docs/images/simulation.png)

## Features

- **Ingestion pipeline**: pypdf → pdfminer → Tesseract OCR fallback chain, driven by a quality score, with an
  optional multimodal LLM pass that repairs low-quality extractions.
- **Question parser** for multiple-choice, open and multi-part questions, robust to the usual PDF and OCR damage
  (glued lines, missing spaces, page headers).
- **Cross-session deduplication** through a canonical fingerprint that ignores numbering, case, accents and
  answer order. Every merged question keeps its provenance, so the bank can rank questions by how often they
  appeared.
- **Spaced repetition** with SM-2: per-question ease factor, growing intervals, same-session relearning.
- **Practice simulations** with a chosen mix of question types, topic-balanced sampling, "never seen" and
  "most mistaken" priority modes, resumable history and a retry mode for wrong answers.
- **AI answer keys**: background batch jobs that generate the correct option and an explanation for each
  question through any OpenAI-compatible API, with progress, cancellation and per-question failure reports.
- **Topic tags** from keyword rules (optionally an LLM) and presets matching the course modules.

- **A focused UI** in vanilla JS (no build step): a study home with today's review and one-click simulations,
  a distraction-free player with keyboard shortcuts and an end-of-session summary, a searchable question bank
  where answer keys are fixed in place, and drag-and-drop upload. Light and dark themes, usable on a phone.

| Study home | Question bank | Documents |
| --- | --- | --- |
| ![Study home](docs/images/home.png) | ![Question bank](docs/images/bank.png) | ![Documents](docs/images/documents.png) |

## Quick start

```bash
docker compose up --build
```

Open <http://localhost:8000/ingest> and drop the three PDFs in [`examples/`](examples) on the page. They are
two text exams and one image-only scan of an older session: the scan goes through OCR, and the 13 extracted
questions collapse to 9 unique ones, 4 of which appear in two sessions. Then study at <http://localhost:8000>.
The interactive API documentation is at <http://localhost:8000/docs>.

## Architecture

```mermaid
flowchart LR
    UI["Browser UI<br/>(vanilla JS)"] -->|JSON over HTTP| API

    subgraph API["FastAPI app"]
        R["routers/<br/>HTTP layer"] --> S["services/<br/>domain logic"]
        S --> J["in-process<br/>correction jobs (asyncio)"]
    end

    S -->|SQLAlchemy Core| DB[("PostgreSQL<br/>14 tables, Alembic")]
    S --> OCR["pdftoppm + Tesseract"]
    S -. optional .-> LLM["OpenAI-compatible LLM"]
    J -. optional .-> LLM
```

Routers only translate HTTP to service calls. Services hold the logic and raise domain errors
(`NotFoundError`, `ConflictError`, ...) that a single exception handler maps to status codes. SQL is written by
hand on top of SQLAlchemy Core: the queries rely on Postgres features (`FILTER`, `jsonb_agg`, partial unique
indexes, arrays), and the shared filters (tags, presets, "has a correction") are composable fragments in
[`app/services/filters.py`](app/services/filters.py).

### Ingestion pipeline

```mermaid
flowchart TD
    U[Upload PDF] --> V{"PDF header, size limit,<br/>SHA-256 not seen before?"}
    V -- no --> X[400 / 409 / 413]
    V -- yes --> P1[pypdf]
    P1 --> Q1{quality ≥ 0.45?}
    Q1 -- no --> P2[pdfminer] --> Q2{best ≥ 0.45?}
    Q2 -- no --> P3[Tesseract OCR]
    Q1 -- yes --> PARSE
    Q2 -- yes --> PARSE
    P3 --> PARSE[Rule-based parser]
    PARSE --> MM{"quality below 0.72 and<br/>LLM pass enabled?"}
    MM -- yes --> LLM["Multimodal LLM<br/>fills missing options and questions"] --> SAVE
    MM -- no --> SAVE[Save questions + provenance]
    SAVE --> TAG[Rule-based tags] --> DD[Deduplicate across sessions]
```

## Design notes

**Deduplication.** Each question is reduced to a canonical form: Unicode NFKC, accents and punctuation removed,
lower case, leading numbering dropped ("3.", "12)", "Esercizio 2"), and for multiple-choice questions the options
sorted by text, so that the same question with its answers shuffled collapses to one form. The SHA-1 of that
form is the fingerprint. Within a group of duplicates the copy with the highest confidence and the most complete
text is kept, and the others are merged into it in one transaction: attempts, review schedules, corrections, tags
and provenance rows all move to the keeper. Numbers inside the text are kept on purpose, because exercises that
differ only in their data are different exercises. Exact matching on a canonical form is predictable and never
merges two different questions; the cost is that a question whose OCR text contains a misread character is not
merged (see the roadmap).

**Extraction quality score.** Deciding whether to fall back to the next extractor needs a cheap estimate of
how usable the text is. The score combines the share of readable characters (pdfminer's `(cid:NN)` glyphs,
mojibake and OCR noise lower it), the share of word-like tokens and the presence of question markers, multiplied
by page coverage so that a mostly scanned PDF still reaches OCR. An earlier version was dominated by text length,
which sent short but perfectly extracted exams to OCR and to the paid LLM pass; the tests in
[`tests/unit/test_extraction.py`](tests/unit/test_extraction.py) pin each case down.

**Spaced repetition.** [`app/services/scheduler.py`](app/services/scheduler.py) implements SM-2 as a pure
function. After each answer (graded 0-5; the UI's right/wrong maps to 4/1) the ease factor is updated with
`EF += 0.1 - (5 - q)(0.08 + (5 - q) 0.02)`, floored at 1.3, and the interval grows 1 day → 6 days → previous ×
EF. Wrong answers come back after 10 minutes in the same session. As in Anki, lapses also lower the ease factor,
so often-missed questions keep shorter intervals.

**Topic-balanced simulations.** Plain random sampling over-represents the topics with the most past questions.
Simulations shuffle questions within each topic and pick round-robin across topics
([`app/services/sampling.py`](app/services/sampling.py)), so even a 10-question run covers as many topics as
possible.

**Background jobs without a queue.** Generating answer keys for a few thousand questions takes minutes of LLM
calls. Jobs run as asyncio tasks inside the API process and store their state and progress in Postgres. A partial
unique index (`ON correction_jobs ((1)) WHERE status IN ('queued', 'running')`) guarantees that only one job runs
at a time, and jobs left running by a crashed process are marked `interrupted` at startup. For a single-machine
deployment this avoids running a broker and a worker; moving to a real queue would only replace `schedule_job`.

**Performance.** The deduplication pass runs after every upload. It used to rewrite every row of the bank; it
now writes back only rows whose normalised form changed, and refreshes provenance counters only where they
differ. On a 5,000-question bank the pass went from 3.2 s to 0.8 s. Migration `0002` adds the indexes used by the
hot read paths (due reviews per user, attempts per simulation, tag filters).

**Schema migrations.** The schema is managed by Alembic. The baseline migration is idempotent, so it applies to
an empty database and to deployments created before migrations existed (checked by diffing `pg_dump
--schema-only` of a migrated legacy database against a fresh one). Migrations run at startup under a Postgres
advisory lock, so several instances starting together cannot race.

**Frontend.** The UI is plain ES modules served by FastAPI, with no build step. Every page renders through a
tagged `html` template that escapes interpolated values by default, because question text comes from PDFs and
LLM output. The study player is one component for both daily review and simulations: answers are recorded as
attempts (which feed SM-2), a question without an answer key can be fixed in place, and simulations are
resumable through a deep link (`/?sim=<id>&q=<n>`).

**Security.** With `ADMIN_TOKEN` set, uploads, processing, the AI endpoints and `/admin/*` require the
`X-Admin-Token` header (compared in constant time); the UI asks for it once. Uploads are checked for the PDF
header and a size limit, deduplicated by SHA-256, and written to disk inside the transaction that inserts their row, so a
duplicate or a failed write leaves neither an orphan file nor a dangling row.
The container runs as an unprivileged user.

## Data model

```mermaid
erDiagram
    documents ||--o{ questions : owns
    documents ||--o{ question_occurrences : "appears in"
    questions ||--o{ question_occurrences : "seen in"
    questions ||--o{ question_tags : ""
    tags ||--o{ question_tags : ""
    tag_presets ||--o{ tag_preset_tags : ""
    tags ||--o{ tag_preset_tags : ""
    users ||--o{ attempts : ""
    questions ||--o{ attempts : ""
    simulations ||--o{ attempts : groups
    users ||--o{ simulations : ""
    users ||--o{ schedule_state : ""
    questions ||--o{ schedule_state : "SM-2 state"
    users ||--o{ question_corrections : ""
    questions ||--o{ question_corrections : "answer key"
    users ||--o{ correction_jobs : ""

    documents {
        uuid id PK
        text title
        char64 sha256 UK
        text ingestion_status
    }
    questions {
        uuid id PK
        uuid document_id FK
        text question_type
        text stem
        jsonb options_json
        char40 dedupe_fingerprint
        int occurrences_count
        bool is_discarded
    }
    question_occurrences {
        uuid question_id FK
        uuid document_id FK
        text source_file_name
        int source_number
    }
    schedule_state {
        uuid user_id PK
        uuid question_id PK
        timestamptz due_at
        numeric ease_factor
        numeric interval_days
        int reps
        int lapses
    }
    attempts {
        uuid id PK
        bool is_correct
        smallint grade
        uuid simulation_id FK
    }
    question_corrections {
        uuid user_id PK
        uuid question_id PK
        text correct_option_id
        text explanation_text
    }
    correction_jobs {
        uuid id PK
        text status
        int processed_count
        jsonb failures_json
    }
```

`question_reviews` (legacy right/wrong flags) is omitted from the diagram.

## Development

Requirements: Python 3.12, PostgreSQL 16, and optionally `poppler-utils` and `tesseract-ocr` (with the `ita`
language) for the OCR fallback.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                      # adjust DATABASE_URL if needed
docker compose up -d db                   # or use a local PostgreSQL
uvicorn app.main:app --reload             # applies migrations at startup
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://examable:examable@localhost:5432/examable` | Database connection |
| `UPLOAD_DIR` | `uploads` | Where uploaded PDFs are stored |
| `MAX_UPLOAD_MB` | `25` | Upload size limit |
| `ADMIN_TOKEN` | unset | Protects mutating and AI endpoints; always set it in production |
| `MULTIMODAL_ENABLED` | `false` | Enables the LLM repair pass during ingestion |
| `MULTIMODAL_API_KEY` | unset | Key for the OpenAI-compatible API (LLM pass and answer keys) |
| `MULTIMODAL_API_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `MULTIMODAL_MODEL` / `CORRECTION_GEN_MODEL` | `gpt-4.1-mini` / same | Models for extraction and answer keys |

### Tests

```bash
createdb examable_test                    # integration tests drop and recreate its schema
pytest --cov                              # 109 tests, ~90% line coverage
ruff check . && ruff format --check .
```

Unit tests cover the parser, the fingerprint, the quality score and the fallback chain, SM-2, sampling, the SQL
filter builders and the LLM clients (against `httpx.MockTransport`). Integration tests run the real app against
PostgreSQL with exam PDFs generated by [`tests/sample_exams.py`](tests/sample_exams.py): upload, OCR,
cross-session deduplication, deleting a session without losing shared questions, study scheduling, simulations,
correction jobs (with a fake LLM), tags and authentication. A browser test (Playwright) uploads the sample
exams, sets an answer key in the bank and completes a simulation from the keyboard, failing on any JavaScript
error; it is skipped when Playwright is not installed (`python -m playwright install chromium`). Set
`TEST_DATABASE_URL` to use another database.

CI runs lint, the test suite with a coverage gate, and a Docker build followed by a `docker compose` smoke test
on every push.

### Project layout

```
app/
  main.py            app factory: lifespan (migrations, seeding), routers, error handling
  routers/           HTTP endpoints, one module per resource
  services/          extraction, parser, multimodal, dedupe, ingestion, tagging,
                     scheduler (SM-2), sampling, simulations, study, corrections
  templates/         Jinja pages sharing one layout (study, bank, documents)
  static/js/         ES modules: core.js (API, escaping-by-default templates, dialogs), one module per page
  static/css/        design tokens, components, light/dark themes
migrations/          Alembic migrations
tests/               unit/ and integration/ suites, sample exam generator
scripts/             offline tools: bulk ingestion, parser benchmark over archives,
                     question-bank pipeline for image-only PDFs, exports
examples/            sample exam PDFs
```

## Deployment

The app is deployed on Fly.io from the [`Dockerfile`](Dockerfile) ([`fly.toml`](fly.toml)): uploads live on a
mounted volume, the database is an external PostgreSQL, and migrations run when the app starts.

```bash
fly secrets set DATABASE_URL=... ADMIN_TOKEN=... MULTIMODAL_API_KEY=...
fly deploy
```

## Roadmap

- **Accounts.** The app runs in single-user mode: every client shares one user, and study actions are not
  authenticated. Real accounts would scope progress per user.
- **Near-duplicate detection.** OCR variants of the same question (one misread character) are not merged.
  MinHash over character shingles could flag likely duplicates for one-click review instead of merging blindly.
- **Stable ids on re-processing.** Re-processing a document regenerates the ids of the questions only it
  contains, which loses their progress; questions shared with other sessions are already preserved.
- **Tagging precision.** The keyword tagger also reads the answer options, so distractors can add wrong tags.
- **Other exam layouts.** The parser targets one course's layout; other layouts currently rely on the LLM pass.

## License

[MIT](LICENSE)
