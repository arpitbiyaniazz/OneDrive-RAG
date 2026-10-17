# Cloud Drive RAG Assistant

Chat with your own OneDrive and Google Drive documents. Answers are generated only from the indexed
files, and every statement links to the passage it came from.

```
OneDrive / Google Drive ──► connectors ──► parse ──► chunk ──► embed ──► Postgres + pgvector
                                                                              │
question ──► rewrite follow-ups ──► hybrid search (vector + full text, RRF) ──┘
         ──► grounded prompt ──► streamed answer with [n] source links
```

## What it does

- **Every common document type**: PDF (text, tables, page numbers, section titles from bookmarks),
  Word (headings, lists, tables in reading order, headers/footers), Excel and CSV (header row repeated in
  every chunk, real row numbers), PowerPoint (slides, tables, grouped shapes, speaker notes), Google
  Docs/Sheets/Slides, Markdown, HTML and plain text in any common encoding. Files that cannot be indexed
  (scanned PDFs without text, legacy `.doc/.xls/.ppt`, images, archives, empty files) are listed with the
  reason instead of being indexed as garbage.
- **Retrieval that finds every document**: semantic vectors (HNSW index) fused with Postgres full-text
  search, so exact names, codes and numbers match too. Questions that name a document focus on it,
  "summarize X" reads the whole document, and "which documents do I have?" answers from the catalog.
- **Grounded answers**: the system prompt (`backend/app/prompts.py`) requires citations, says plainly when
  the documents do not contain the answer, flags conflicting sources, and treats document text as data
  (prompt-injection resistant). Follow-up questions are rewritten using the conversation.
- **Production basics**: OAuth with PKCE and state validation, revocable sessions, per-user isolation on
  every query, durable background jobs in Postgres (safe across restarts and replicas), incremental sync
  that re-indexes changed files and purges deleted ones, Alembic migrations, health/readiness probes,
  request ids, OpenTelemetry tracing and Langfuse LLM tracing (both optional).

## Run locally

Prerequisites: Docker, Python 3.12+, Node 20+.

```bash
cp .env.example .env                 # set SECRET_KEY, TOKEN_ENCRYPTION_KEY, MISTRAL_API_KEY, OAuth apps
docker compose up -d postgres

cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload        # API on :8000, runs the background worker too

cd ../frontend
npm install && npm run dev           # UI on http://localhost:5173 (proxies /api to :8000)
```

Without OAuth apps, set `DEV_MOCK_ONEDRIVE=true` / `DEV_MOCK_GDRIVE=true` to use sandbox drives with sample
files (development only; production refuses to start with them).

Drive setup: [docs/AZURE_SETUP.md](docs/AZURE_SETUP.md), [docs/GOOGLE_SETUP.md](docs/GOOGLE_SETUP.md).
Put the documents to index in a top-level folder named `OneDrive-RAG` (configurable).

## Run the full stack in containers

```bash
docker compose up -d --build         # UI + API on http://localhost:8080
```

The backend image runs `alembic upgrade head` on start (`RUN_MIGRATIONS=false` to disable) and serves with
`WEB_CONCURRENCY` uvicorn workers; nginx serves the built UI and proxies `/api` with streaming enabled.
Set `ENVIRONMENT=production`: the server then refuses default secrets, sandbox drives and mock providers.

## Tests

```bash
cd backend
pytest                               # isolated onedrive_rag_test DB, sandbox drives, no network
RUN_LIVE_TESTS=1 pytest -m live -s   # end-to-end quality check with the real Mistral models
ruff check app tests migrations
```

The suite parses a generated corpus covering every supported format and edge case
(`tests/fixtures/corpus.py`) and checks that every planted fact is extracted, retrievable, and, in the
live run, answered with valid citations.

## Operations

| Task | Command |
| --- | --- |
| Re-check all documents of a user | `python -m app.cli sync --email user@example.com` (or the **Sync** button) |
| Change the embedding model | set `EMBEDDING_MODEL`/`EMBEDDING_DIMENSION`, run `python -m app.cli reset-embeddings`, then `python -m app.cli sync --all` |
| Process queued jobs without the API | `python -m app.cli run-jobs` |
| Liveness / readiness | `GET /api/health`, `GET /api/health/ready` |

The original requirements are kept in [docs/ORIGINAL_SPEC.md](docs/ORIGINAL_SPEC.md); observability options
are described in [docs/OBSERVABILITY_GUIDE.md](docs/OBSERVABILITY_GUIDE.md).
