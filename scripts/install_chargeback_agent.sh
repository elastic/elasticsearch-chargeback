#!/usr/bin/env bash
# Install the optional Chargeback companion Agent Builder add-on.
# Requires Chargeback integration 0.5.0+ and Agent Builder enabled on Kibana.
#
# Export before running:
#   KIBANA_URL      Kibana URL of the monitoring cluster
#   KIBANA_API_KEY  API key that can manage Agent Builder assets

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

: "${KIBANA_URL:?Set KIBANA_URL}"
: "${KIBANA_API_KEY:?Set KIBANA_API_KEY}"

export KIBANA_URL KIBANA_API_KEY
exec python3 "$ROOT/agent/install.py" "$@"
