# OneDrive Metadata-Aware RAG Platform

A production-ready enterprise RAG (Retrieval-Augmented Generation) chatbot designed for Microsoft OneDrive documents, featuring **Dual-Branch Observability (OpenTelemetry + Langfuse)**, multi-tenant isolation, grounded streaming responses with clickable citations, and incremental synchronization.

---

## 🌟 Key Architecture & Capabilities

```
                            RAG APPLICATION
                                  │
                      ┌───────────┴───────────┐
                      │                       │
                      ▼                       ▼
                OpenTelemetry              Langfuse
                      │                       │
                      │                       ├── LLM traces
                      │                       ├── RAG traces
                      │                       ├── Token usage & cost
                      │                       ├── Evaluations
                      │                       ├── Prompt versions
                      │                       └── RAG experiments
                      │
                      ├── API traces
                      ├── DB latency
                      ├── OneDrive API calls
                      ├── Worker latency
                      └── System metrics
```

- **Dual-Branch Observability**:
  - **OpenTelemetry**: Distributed tracing for FastAPI HTTP endpoints, SQLAlchemy / pgvector query latencies, Microsoft Graph API network calls, and async background workers.
  - **Langfuse**: LLM traces, token usage, cost attribution, prompt versioning (`v1`, `v2`), A/B experiments, and automated evaluation metrics (`faithfulness`, `context_recall`, `citation_accuracy`).
  - **Correlation Bridge**: Each Langfuse trace includes the active OpenTelemetry `trace_id` as metadata for unified infrastructure-to-LLM debugging.
- **Microsoft OneDrive & Google Drive Multi-Cloud Integrations**:
  - Full Microsoft Entra ID and Google Cloud OAuth 2.0 flow with PKCE and AES-256 token encryption at rest.
  - Multi-format document extractors: **PDF** (PyMuPDF), **DOCX** (python-docx), **XLSX** (openpyxl), **PPTX** (python-pptx), and **TXT**.
  - On-the-fly conversion of native Google Workspace documents: **Google Docs** (.docx), **Google Sheets** (.xlsx), and **Google Slides** (.pptx).
  - Built-in `MockOneDriveProvider` and `MockGoogleDriveProvider` allowing 100% full-stack sandbox development and testing without requiring live cloud credentials.
- **pgvector & Structure-Aware Chunking**:
  - Semantic vector storage with 1536-dimensional embeddings.
  - Preserves document hierarchy, page numbers, section headers, and folder paths.
  - Strict SQL multi-tenancy (`WHERE user_id = :current_user_id`).
- **Grounded Streaming Chat & Anti-Jailbreak Protection**:
  - Real-time Server-Sent Events (SSE) token streaming.
  - Prompt injection quarantine: external document text is quarantined within `<untrusted_reference_document>` tags.
  - Verified clickable citations linking directly back to OneDrive file URLs.
- **Incremental Synchronization**:
  - SHA-256 hash-based change detection.
  - Automatically indexes new files, updates modified documents, purges deleted documents, and skips unchanged files.
- **Automated RAG Evaluation Suite**:
  - Automated evaluation harness in `backend/app/evals/` benchmarking Faithfulness (Groundedness), Context Recall, Citation Accuracy, and Answer Relevancy.

---

## 🚀 Quick Start

### 1. Prerequisites
- Docker & Docker Compose
- Python 3.11+ (Python 3.14 verified)
- Node.js 18+

### 2. Environment Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

### 3. Start Database & Cache
```bash
docker compose up -d postgres redis
```
This starts PostgreSQL 16 with pgvector extension enabled on port `5432` and Redis on port `6379`.

### 4. Setup Backend
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run migrations and initialize tables
python -m app.db.init_db

# Run complete test suite (31 tests)
pytest tests/ -v

# Start FastAPI server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Setup Frontend
```bash
cd frontend
npm install
npm run build
npm run dev
```
Open [http://localhost:5173](http://localhost:5173) in your browser.

---

## 🧪 Running Evaluations

To run the automated RAG evaluation benchmark and log evaluation scores to Langfuse:
```bash
cd backend
.venv/bin/python -m app.evals.run_evals v2
```

---

## 📁 Repository Structure

```
.
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI routers (auth, onedrive, ingestion, chat, sync, health)
│   │   ├── core/             # Configuration, security, cryptography
│   │   │   └── telemetry/    # OpenTelemetry & Langfuse setup & decorators
│   │   ├── db/               # Database engine & pgvector initialization
│   │   ├── evals/            # Automated evaluation metrics, dataset & runner
│   │   ├── models/           # SQLAlchemy models (User, Document, DocumentChunk, ChatSession)
│   │   ├── processors/       # Modular extractors (PDF, DOCX, XLSX, PPTX, TXT)
│   │   └── services/         # Microsoft Graph, Chunker, Embedding, RAG, Sync
│   └── tests/                # Comprehensive test suite (31 tests)
├── frontend/
│   ├── src/
│   │   ├── components/       # FolderTree, ChatBox, KnowledgeBaseTable, IngestionModal
│   │   ├── services/         # API clients
│   │   └── App.tsx           # Modern Glassmorphic Dashboard
│   └── package.json
├── docs/
│   ├── AZURE_SETUP.md        # Step-by-step Azure Entra ID registration guide
│   ├── GOOGLE_SETUP.md       # Step-by-step Google Cloud Console & Drive API guide
│   └── OBSERVABILITY_GUIDE.md # Detailed dual-branch telemetry documentation
├── docker-compose.yml        # PostgreSQL (pgvector) & Redis services
└── README.md
```

---

## 🔒 Security & Multi-Tenancy

- **Token Protection**: Refresh tokens are encrypted with AES-256 (Fernet) before writing to the database and never exposed to the client.
- **Tenant Isolation**: Every vector similarity query enforces database-level tenant filtering (`user_id`).
- **Prompt Injection Defense**: Reference content is enclosed in `<untrusted_reference_document>` tags with strict system instructions prohibiting the execution of text-embedded commands.
