#!/usr/bin/env bash
set -e

# ==============================================================================
# Start Online Observability Gateway (Langfuse Cloud + Cloud APMs)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "========================================================"
echo " Starting ONLINE Observability Gateway (Cloud SaaS Mode)"
echo "========================================================"

cd "$ROOT_DIR/observability"

# Start the online collector gateway via docker compose
docker compose -f docker-compose.online.yml up -d

echo ""
echo "========================================================"
echo " Cloud Observability Gateway is Active!"
echo "========================================================"
echo " 1. Langfuse Cloud Console:       https://cloud.langfuse.com"
echo " 2. OTel Cloud Gateway Ingest:    localhost:4317 (gRPC) / 4318 (HTTP)"
echo " 3. Collector Health Check:       http://localhost:13133"
echo " 4. Backend Status Endpoint:      http://localhost:8000/api/telemetry/status"
echo "========================================================"
echo ""
echo "Ensure your root .env contains your live Langfuse credentials:"
echo "  LANGFUSE_ENABLED=true"
echo "  LANGFUSE_HOST=https://cloud.langfuse.com"
echo "  LANGFUSE_PUBLIC_KEY=pk-lf-..."
echo "  LANGFUSE_SECRET_KEY=sk-lf-..."
echo ""
echo "To stop this online gateway, run:"
echo "  bash scripts/stop_observability.sh"
echo "========================================================"
