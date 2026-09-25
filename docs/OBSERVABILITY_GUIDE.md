# Dual-Branch Observability Architecture (OpenTelemetry + Langfuse)

This document explains the architecture and operational flow of the **Dual-Branch Observability** system implemented in the OneDrive RAG Platform.

---

## 1. Architectural Overview

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

The system establishes a clean separation of concerns:
1. **OpenTelemetry Branch (Infrastructure & Distributed Telemetry)**:
   - Tracks distributed tracing across incoming HTTP requests, PostgreSQL queries, Microsoft Graph API HTTP calls, and background worker jobs.
   - Collects latency, error rates, and system metrics.
2. **Langfuse Branch (LLM Engineering & Observability)**:
   - Tracks LLM token usage, prompt versioning, semantic retrieval traces, and automated LLM evaluation scores.
3. **Correlation Bridge**:
   - Each Langfuse trace attaches the active OpenTelemetry `trace_id` as metadata (`otel_trace_id`), enabling seamless drill-down from an LLM trace into the corresponding backend infrastructure span in Jaeger or Grafana.

---

## 2. OpenTelemetry Branch Breakdown

### 2.1 API Request Tracing
- **Implementation**: `FastAPIInstrumentor` in `backend/app/core/telemetry/otel_setup.py`.
- **Captures**: HTTP route, status code, client IP, request duration, unhandled exceptions.

### 2.2 Database Latency & pgvector Queries
- **Implementation**: `SQLAlchemyInstrumentor` instrumenting the Asyncpg engine.
- **Captures**: Query execution duration, pgvector cosine distance operations (`DocumentChunk.embedding.cosine_distance()`), and connection pool checkout times.

### 2.3 Microsoft Graph & OneDrive API Calls
- **Implementation**: Context manager `trace_onedrive_call(operation_name, path_or_id)` and `HTTPXClientInstrumentor`.
- **Captures**: Microsoft Graph API latency, download stream throughput, rate limiting (HTTP 429 status codes), and network errors.

### 2.4 Worker Latency
- **Implementation**: `@trace_worker(job_name)` decorator.
- **Captures**: Background ingestion job processing times, chunk embedding generation times, and incremental sync durations.

---

## 3. Langfuse Branch Breakdown

### 3.1 LLM Traces & RAG Pipeline Traces
- **Implementation**: `@observe_rag(name, prompt_name, prompt_version, experiment_id)`.
- **Captures**:
  - Full user query
  - Retrieved documents and similarity scores
  - System prompt template used
  - Streaming generation latency (time to first token, tokens/sec)
  - Final response and citations

### 3.2 Token Usage & Cost Attribution
- Automatically calculates prompt tokens, completion tokens, and dollar cost based on model pricing (e.g. `gpt-4o-mini`, `text-embedding-3-small`).

### 3.3 Prompt Registry & Versioning
- Centralized in `app/core/telemetry/langfuse_client.py`.
- Maintains managed prompts:
  - `rag_grounded_system@v1`: Strict fact-grounded system prompt.
  - `rag_grounded_system@v2`: Enhanced anti-jailbreak prompt with structured `<untrusted_reference_document>` tags.

### 3.4 RAG Experiments & A/B Testing
- Allows tagging queries with `experiment_id` (e.g. `exp_vector_v1` vs. `exp_hybrid_v1`) to compare retrieval precision, latency, and user satisfaction side-by-side.

### 3.5 Automated Evaluations & Human Feedback
- Supports automated evaluation metrics logged directly to Langfuse via `log_evaluation_score(trace_id, name, value, comment)`:
  - `faithfulness` (0.0 to 1.0)
  - `context_recall` (0.0 to 1.0)
  - `citation_accuracy` (0.0 to 1.0)
  - `answer_relevance` (0.0 to 1.0)
- End-user feedback endpoint (`POST /api/chat/feedback`) captures `rating: 1` (thumbs up) or `rating: -1` (thumbs down) with user comments.

---

## 4. OpenTelemetry & Langfuse Correlation

When a user query is received:
1. OpenTelemetry generates or extracts `trace_id` (e.g. `3a8f9c1b72e0a4f5...`).
2. `get_current_trace_id()` retrieves this 32-character hex ID.
3. `@observe_rag` injects `otel_trace_id` into the Langfuse metadata payload.
4. Engineers inspecting a slow or anomalous trace in Langfuse can directly copy the `otel_trace_id` and paste it into Jaeger or Datadog to view the underlying database or Microsoft Graph network call.

---

## 5. Verification & Telemetry Health Endpoint

The system exposes a live diagnostic endpoint at:
```http
GET /api/telemetry/status
```

Response payload:
```json
{
  "opentelemetry": {
    "service_name": "onedrive-rag-backend",
    "tracer_active": true,
    "otlp_endpoint": "http://localhost:4317"
  },
  "langfuse": {
    "host": "https://cloud.langfuse.com",
    "client_active": true,
    "configured": true
  }
}
```

---

## 6. Offline Observability Setup (100% Local & Free)

Offline mode requires **no external API keys or cloud subscriptions**. It runs completely on your local machine using Docker containers or local console logging.

```
                           OFFLINE RAG APPLICATION
                                      │
                    ┌─────────────────┴─────────────────┐
                    │                                   │
                    ▼                                   ▼
        OpenTelemetry Collector                     Local Langfuse
           (Port 4317 / 4318)                     (http://localhost:3000)
                    │                                   │
         ┌──────────┴──────────┐                        ├── LLM Traces
         │                     │                        ├── Prompt Versions
         ▼                     ▼                        └── Offline Evals
       Jaeger              Prometheus
(http://localhost:16686) (http://localhost:9090)
```

### 6.1 Start Local Observability Stack
Run the pre-configured script from the repository root:
```bash
bash scripts/start_observability_offline.sh
```
Or start via Docker Compose directly:
```bash
docker compose -f observability/docker-compose.offline.yml up -d
```

### 6.2 Configure Backend for Offline Telemetry
Merge `observability/.env.offline` into your `.env`:
```env
# OpenTelemetry -> Local Jaeger
OTEL_SERVICE_NAME=onedrive-rag-backend
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_EXPORTER_OTLP_INSECURE=true
OTEL_CONSOLE_EXPORTER=false

# Langfuse -> Local Self-Hosted Langfuse Instance
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-local-test
LANGFUSE_SECRET_KEY=sk-lf-local-test
```

> [!TIP]
> **Zero-Docker Console Mode**: If you do not wish to run Docker containers at all, set `OTEL_CONSOLE_EXPORTER=true` and leave `OTEL_EXPORTER_OTLP_ENDPOINT=` empty. OpenTelemetry spans will be printed directly to your backend terminal logs.

### 6.3 Local Dashboards
- **Jaeger Traces UI**: [http://localhost:16686](http://localhost:16686) — inspect spans, search by service name (`onedrive-rag-backend`), view query latencies, and trace ID lookups.
- **Prometheus Metrics**: [http://localhost:9090](http://localhost:9090) — query `onedrive_rag_` counters and pipeline duration histograms.
- **Local Langfuse Web UI**: [http://localhost:3000](http://localhost:3000) — view offline generation traces and prompt version history.

---

## 7. Online Observability Setup (Cloud SaaS & Production)

Online mode streams telemetry to **Langfuse Cloud** and enterprise Cloud APMs (Datadog, Honeycomb, Grafana Cloud, New Relic) for production monitoring, team collaboration, and automated LLM evaluation.

```
                           ONLINE RAG APPLICATION
                                      │
                    ┌─────────────────┴─────────────────┐
                    │                                   │
                    ▼                                   ▼
        OpenTelemetry Collector                   Langfuse Cloud
         (Cloud SaaS Gateway)               (https://cloud.langfuse.com)
                    │                                   │
         ┌──────────┼──────────┐                        ├── Multi-tenant LLM Traces
         ▼          ▼          ▼                        ├── Token Usage & Costs ($)
      Honeycomb  Datadog  Grafana Cloud                 ├── Prompt Version Registry
                                                        └── Automated LLM-as-a-judge
```

### 7.1 Start Cloud Gateway (Optional)
If using the collector gateway to ship spans to Cloud APMs:
```bash
bash scripts/start_observability_online.sh
```
Or start via Docker Compose:
```bash
docker compose -f observability/docker-compose.online.yml up -d
```

### 7.2 Configure Backend for Online Telemetry
Merge `observability/.env.online` into your `.env`:
```env
# 1. Langfuse Cloud (Get free API keys at https://cloud.langfuse.com)
LANGFUSE_ENABLED=true
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=pk-lf-your-actual-public-key
LANGFUSE_SECRET_KEY=sk-lf-your-actual-secret-key

# 2. OpenTelemetry -> Cloud APM
# Option A: Route through local online collector gateway
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_EXPORTER_OTLP_INSECURE=true

# Option B: Direct HTTPS to Cloud APM (e.g. Honeycomb)
# OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io:443
# OTEL_EXPORTER_OTLP_INSECURE=false
# OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=your-honeycomb-api-key
```

### 7.3 Stopping Observability Stacks
To shut down any running offline or online containers:
```bash
bash scripts/stop_observability.sh
```

---

## 8. Setup Files Reference

| File | Mode | Description |
| :--- | :--- | :--- |
| [`observability/docker-compose.offline.yml`](file:///Users/arpitbiyaniaz/Documents/Agents/OneDrive%7CRAG/observability/docker-compose.offline.yml) | **Offline** | Docker stack with Jaeger, Prometheus, OTel Collector, & Local Langfuse |
| [`observability/otel-collector.offline.yaml`](file:///Users/arpitbiyaniaz/Documents/Agents/OneDrive%7CRAG/observability/otel-collector.offline.yaml) | **Offline** | OTel collector pipeline routing spans to local Jaeger & Prometheus |
| [`observability/prometheus.offline.yml`](file:///Users/arpitbiyaniaz/Documents/Agents/OneDrive%7CRAG/observability/prometheus.offline.yml) | **Offline** | Prometheus scrape configuration for collector, backend, and Jaeger |
| [`observability/.env.offline`](file:///Users/arpitbiyaniaz/Documents/Agents/OneDrive%7CRAG/observability/.env.offline) | **Offline** | Ready-to-copy `.env` settings for 100% local telemetry |
| [`observability/docker-compose.online.yml`](file:///Users/arpitbiyaniaz/Documents/Agents/OneDrive%7CRAG/observability/docker-compose.online.yml) | **Online** | Lightweight OTel collector gateway forwarding to Cloud APMs |
| [`observability/otel-collector.online.yaml`](file:///Users/arpitbiyaniaz/Documents/Agents/OneDrive%7CRAG/observability/otel-collector.online.yaml) | **Online** | OTel collector configuration for Honeycomb, Datadog, Grafana Cloud |
| [`observability/.env.online`](file:///Users/arpitbiyaniaz/Documents/Agents/OneDrive%7CRAG/observability/.env.online) | **Online** | Ready-to-copy `.env` settings for Langfuse Cloud & Cloud APMs |
| [`scripts/start_observability_offline.sh`](file:///Users/arpitbiyaniaz/Documents/Agents/OneDrive%7CRAG/scripts/start_observability_offline.sh) | **Offline** | 1-click startup script for local Jaeger, Prometheus, and Langfuse |
| [`scripts/start_observability_online.sh`](file:///Users/arpitbiyaniaz/Documents/Agents/OneDrive%7CRAG/scripts/start_observability_online.sh) | **Online** | 1-click startup script for cloud telemetry gateway |
| [`scripts/stop_observability.sh`](file:///Users/arpitbiyaniaz/Documents/Agents/OneDrive%7CRAG/scripts/stop_observability.sh) | **Both** | Stops all running observability stacks cleanly |
