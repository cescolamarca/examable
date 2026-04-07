# Examable (MVP)

Piattaforma MVP per:
- ingestione PDF di esami/esercizi,
- estrazione domande in formato standardizzato,
- tagging automatico base,
- scheduling delle domande da ripetere.

## Avvio con Docker

```bash
docker compose up
```

Servizi:
- API: `http://localhost:8000`
- Postgres: `localhost:5432`
- Redis: `localhost:6379`
- Web UI: `http://localhost:8000/`

## API principali

- `GET /health`
- `GET /` (Web UI Allenamento)
- `GET /study` (Web UI Allenamento)
- `GET /ingest` (Web UI Ingest)
- `POST /documents` (multipart `file=.pdf`)
- `POST /documents/{document_id}/process`
- `GET /documents`
- `GET /stats/kpi`
- `GET /documents/{document_id}/questions` (filtro query opzionale: `question_type`)
- `GET /questions/{question_id}`
- `GET /questions/{question_id}/tags`
- `PUT /questions/{question_id}/tags`
- `GET /tags`
- `GET /tag-presets`
- `POST /tags`
- `GET /reports/simulation`
- `GET /users/default`
- `POST /attempts`
- `GET /study/next/{user_id}` (supporta filtri query: `document_id`, `tag`, `tag_preset`, `question_type`)
- `POST /simulations/custom` (simulazione custom per tipo/tag/tag_preset/documento)
- `POST /tagging/recompute/document/{document_id}` (`use_ai=true` opzionale)

## Routine automatica su ingest

Dopo ogni `POST /documents/{document_id}/process`, il backend esegue automaticamente:

- pulizia testo domande/opzioni/sottopunti,
- dedupe globale tramite fingerprint semantico,
- aggiornamento occorrenze e file sorgente.

Campi salvati su `questions`:

- `occurrences_count`
- `source_files_json`
- `dedupe_fingerprint`

Provenienza dettagliata in `question_occurrences` (file, sezione, numero domanda).

Swagger: `http://localhost:8000/docs`

## Script parser locale

```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/parse_unisa_reti.py "C:\path\esame.pdf" --out parsed.json
```

## Note

- Il parser è ottimizzato per layout simili ai due PDF UNISA condivisi.
- Per PDF molto diversi è consigliato aggiungere fallback OCR + estrazione LLM guidata da schema JSON.
- Su Windows con ambienti Python MSYS/MinGW alcune dipendenze API possono richiedere toolchain C/Rust; in quel caso usa `docker compose up` per partire senza setup locale.
- La pipeline di estrazione testo prova in ordine: `pypdf` -> `pdfminer` -> OCR (`pdftoppm+tesseract`, se installati nel sistema).
- L'endpoint `/documents/{document_id}/process` restituisce anche metodo usato, quality score e warning di estrazione.
- In caso di estrazione debole, il sistema puo' fare un secondo passaggio multimodale (provider OpenAI-compatible) con merge automatico sui campi mancanti.

## Configurazione multimodale (opzionale)

Imposta variabili ambiente:

- `MULTIMODAL_ENABLED=true`
- `MULTIMODAL_API_KEY=<token>`
- `MULTIMODAL_API_BASE_URL=https://api.openai.com/v1` (o endpoint compatibile)
- `MULTIMODAL_MODEL=gpt-4.1-mini`
- `MULTIMODAL_MIN_QUALITY=0.72`
- `MULTIMODAL_MAX_PAGES=8`

Quando attivo, `POST /documents/{document_id}/process` restituisce anche:

- `multimodal_used`
- `multimodal_updates`
- `multimodal_warnings`
