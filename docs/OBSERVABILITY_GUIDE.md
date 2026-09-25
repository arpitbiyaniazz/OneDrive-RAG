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
    "enabled": true,
    "endpoint": "http://localhost:4317",
    "service_name": "onedrive-rag-backend",
    "status": "active"
  },
  "langfuse": {
    "enabled": true,
    "host": "https://cloud.langfuse.com",
    "status": "connected",
    "active_prompts": ["rag_grounded_system", "intent_classifier"],
    "experiments": ["exp_vector_v1", "exp_hybrid_v1"]
  },
  "correlation": {
    "strategy": "otel_trace_id_in_langfuse_metadata",
    "sample_current_trace_id": "00000000000000000000000000000000"
  }
}
```
