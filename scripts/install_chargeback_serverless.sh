#!/usr/bin/env bash
# ==============================================================================
# Install Chargeback Serverless hub assets (Compatibility Wrapper)
#
# As of v0.2.0+, Chargeback Serverless operates as a native Elastic Agent CEL
# integration package with Fleet-managed transforms and lookup indices.
# This script forwards environment variables and CLI arguments to the modern
# E2E deployment automation script: ./scripts/deploy_chargeback_serverless.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Map legacy 0.1.x variable names if CLOUD_API_KEY is not explicitly set
if [[ -z "${CLOUD_API_KEY:-}" ]]; then
  if [[ -n "${BILLING_API_KEY:-}" ]]; then
    export CLOUD_API_KEY="$BILLING_API_KEY"
  elif [[ -n "${USAGE_API_KEY:-}" ]]; then
    export CLOUD_API_KEY="$USAGE_API_KEY"
  fi
fi

echo "Notice: Chargeback Serverless 0.2.0+ uses native Elastic Agent CEL integration."
echo "Delegating to ./scripts/deploy_chargeback_serverless.sh ..."
echo ""

exec "$SCRIPT_DIR/deploy_chargeback_serverless.sh" "$@"
