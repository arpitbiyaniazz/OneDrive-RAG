#!/usr/bin/env bash

# ==============================================================================
# Stop All Observability Stacks (Offline and Online)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Stopping any running observability stacks..."

cd "$ROOT_DIR/observability"

# Stop offline stack if running
if [ -f "docker-compose.offline.yml" ]; then
  docker compose -f docker-compose.offline.yml down || true
fi

# Stop online stack if running
if [ -f "docker-compose.online.yml" ]; then
  docker compose -f docker-compose.online.yml down || true
fi

echo "Observability containers stopped successfully."
