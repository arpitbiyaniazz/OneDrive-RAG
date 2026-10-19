# Observability: OpenTelemetry and Langfuse

Both are optional and off unless configured. OpenTelemetry covers the system (requests, database,
drive and model API calls, background jobs); Langfuse covers the answers (questions, model calls,
tokens and cost, user feedback). Each Langfuse trace carries the OpenTelemetry trace id, so a slow or
wrong answer can be followed into the infrastructure spans.

## What is recorded

### OpenTelemetry (`backend/app/core/telemetry/otel_setup.py`)

| Span | Source |
| --- | --- |
| Every API request except `/api/health` | FastAPI instrumentation |
| Every SQL statement (including pgvector searches) | SQLAlchemy instrumentation |
| Every outgoing HTTP call (Microsoft Graph, Google Drive, Mistral/OpenAI) | HTTPX instrumentation |
| `onedrive.get_file`, `google_drive.list_children`, ... | `trace_drive_call` around each drive operation |
| `worker.ingest`, `worker.sync` | `trace_worker` around each background job |

URL attributes are redacted before export: for API hosts only scheme, host and path are kept (no
query strings); for any other host only the host is kept. OneDrive's pre-authenticated download
redirects carry a bearer token in the URL and never leave the process.

### Langfuse (`backend/app/core/telemetry/langfuse_client.py`)

- Each chat answer is a `rag_chat` trace: the question, the answer, the intent, the rewritten search
  query, the number of sources, the chat session id, the prompt versions (`answer-v4`,
  `summary-part-v1`) and `otel_trace_id`.
- Model calls made while answering (follow-up rewriting, summary notes, the answer itself) are nested
  under it through Langfuse's OpenAI wrapper, which records tokens, latency and cost.
- Thumbs up/down in the UI (`POST /api/chat/feedback`) is stored on the message and sent as one
  `user_feedback` score per message (changing the rating updates it; clearing it sends nothing).

## Configuration

```env
# OpenTelemetry: send spans to an OTLP endpoint (collector, Jaeger, or a vendor)
OTEL_ENABLED=true
OTEL_SERVICE_NAME=cloud-drive-rag-backend
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_EXPORTER_OTLP_INSECURE=true            # plain gRPC; defaults to true for http:// endpoints
# OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=your-key
# OTEL_CONSOLE_EXPORTER=true                # print spans to the log instead (no endpoint needed)

# Langfuse
LANGFUSE_ENABLED=true
LANGFUSE_HOST=https://cloud.langfuse.com     # or http://localhost:3000 for the local stack
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

Without `OTEL_EXPORTER_OTLP_ENDPOINT` (and without the console exporter) spans are created but not
exported. Without Langfuse keys every Langfuse helper is a no-op. Tests disable both.

## Local stack (no cloud accounts)

```bash
bash scripts/start_observability_offline.sh      # or: docker compose -f observability/docker-compose.offline.yml up -d
```

| Service | URL | Notes |
| --- | --- | --- |
| OpenTelemetry Collector | `localhost:4317` (gRPC), `localhost:4318` (HTTP) | the backend sends spans here |
| Jaeger | http://localhost:16686 | traces, search by service `cloud-drive-rag-backend` |
| Prometheus | http://localhost:9090 | collector and Jaeger pipeline metrics (the backend exports traces only) |
| Langfuse (self-hosted v2) | http://localhost:3000 | create a project there and copy its API keys into `.env` |

The local Langfuse runs on its own Postgres container (`langfuse-db`). Never point it at the
application database: it would create its own tables, including `users`, next to the app's.
All ports are bound to 127.0.0.1. The Langfuse secrets in the compose file are for local use only.

## Cloud

Send spans straight to a vendor that accepts OTLP (set `OTEL_EXPORTER_OTLP_ENDPOINT`,
`OTEL_EXPORTER_OTLP_HEADERS` and `OTEL_EXPORTER_OTLP_INSECURE=false`), or run the gateway collector:

```bash
bash scripts/start_observability_online.sh       # or: docker compose -f observability/docker-compose.online.yml up -d
```

`observability/otel-collector.online.yaml` only prints spans by default; uncomment one exporter
(Honeycomb, Grafana Tempo, New Relic or any OTLP endpoint), set its variables in
`docker-compose.online.yml`, and add it to the traces pipeline. Use Langfuse Cloud keys for LLM traces.

```bash
bash scripts/stop_observability.sh               # stops either stack
```

## Checking it works

1. `python -m app.cli doctor` (database, migrations, provider keys).
2. Ask a question in the app, then search Jaeger for service `cloud-drive-rag-backend`: the request
   span contains the SQL, Mistral and drive calls.
3. In Langfuse, open the `rag_chat` trace for the same question; its metadata has `otel_trace_id`,
   which you can paste into Jaeger's trace search.

## Files

| File | Purpose |
| --- | --- |
| `observability/docker-compose.offline.yml` | local stack: collector, Jaeger, Prometheus, Langfuse with its own database |
| `observability/otel-collector.offline.yaml` | collector pipeline: traces to Jaeger, metrics to Prometheus |
| `observability/prometheus.offline.yml` | Prometheus scrape targets (collector, Jaeger) |
| `observability/docker-compose.online.yml` | gateway collector for a cloud APM |
| `observability/otel-collector.online.yaml` | gateway pipeline with ready-to-uncomment vendor exporters |
| `scripts/start_observability_offline.sh`, `scripts/start_observability_online.sh`, `scripts/stop_observability.sh` | start/stop helpers |
