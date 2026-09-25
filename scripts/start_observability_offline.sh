#!/usr/bin/env bash
set -e

# ==============================================================================
# Start Offline Observability Stack (Jaeger + Prometheus + OTel + Local Langfuse)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "========================================================"
echo " Starting OFFLINE Observability Stack (100% Local)"
echo "========================================================"

cd "$ROOT_DIR/observability"

# Start the offline stack via docker compose
docker compose -f docker-compose.offline.yml up -d

echo ""
echo "Waiting for services to initialize..."
sleep 3

echo ""
echo "========================================================"
echo " Local Observability Services are Ready!"
echo "========================================================"
echo " 1. Jaeger Tracing Dashboard:     http://localhost:16686"
echo " 2. Prometheus Metrics:           http://localhost:9090"
echo " 3. OpenTelemetry OTLP Receiver:  localhost:4317 (gRPC) / 4318 (HTTP)"
echo " 4. Local Langfuse Dashboard:     http://localhost:3000"
echo " 5. Backend Status Endpoint:      http://localhost:8000/api/telemetry/status"
echo "========================================================"
echo ""
echo "To route backend traces to this local stack, ensure your .env has:"
echo "  OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317"
echo "  OTEL_EXPORTER_OTLP_INSECURE=true"
echo "  LANGFUSE_HOST=http://localhost:3000"
echo ""
echo "To stop this offline stack, run:"
echo "  bash scripts/stop_observability.sh"
echo "========================================================"
