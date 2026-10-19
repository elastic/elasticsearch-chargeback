#!/usr/bin/env bash
# Install the optional Chargeback companion Agent Builder add-on.
# Requires Chargeback integration 0.5.0+ and Agent Builder enabled on Kibana (9.2.0+).
#
# Export before running:
#   KIBANA_URL      Kibana URL of the monitoring cluster
#   KIBANA_API_KEY  API key that can manage Agent Builder assets
#
# Or for local elastic-package stacks (basic auth):
#   ELASTIC_PASSWORD  (default: changeme) — creates a short-lived ES API key accepted by Kibana
#
# Pass-through flags (e.g. --verify, --skip-workflows) are forwarded to install.py.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

KIBANA_URL="${KIBANA_URL:-${KIBANA_HOST:-https://127.0.0.1:5601}}"
ES_URL="${ES_URL:-${ES_HOST:-https://127.0.0.1:9200}}"

is_local_url() {
  case "$1" in
    *127.0.0.1*|*localhost*|*\[::1\]*|*:\/\/::1*) return 0 ;;
    *) return 1 ;;
  esac
}

CURL_TLS=()
if [[ "${CURL_INSECURE:-0}" == "1" ]] || is_local_url "$ES_URL" || is_local_url "$KIBANA_URL"; then
  CURL_TLS=(-k)
fi

if [[ -z "${KIBANA_API_KEY:-}" ]]; then
  if [[ -n "${ELASTIC_PASSWORD:-}" || -n "${PASS:-}" ]]; then
    PASS_BOOT="${ELASTIC_PASSWORD:-${PASS:-changeme}}"
    USER_BOOT="${ELASTIC_USER:-elastic}"
    echo "Creating short-lived Elasticsearch API key for Agent Builder install..."
    ENC=$(curl -sS "${CURL_TLS[@]}" -u "${USER_BOOT}:${PASS_BOOT}" -H "Content-Type: application/json" \
      -X POST "${ES_URL%/}/_security/api_key" \
      -d '{"name":"chargeback-agent-install","expiration":"2h"}' | jq -r '.encoded // empty')
    if [[ -z "$ENC" ]]; then
      echo "Failed to create Elasticsearch API key. Set KIBANA_API_KEY explicitly." >&2
      exit 1
    fi
    KIBANA_API_KEY="$ENC"
  fi
fi

: "${KIBANA_URL:?Set KIBANA_URL}"
: "${KIBANA_API_KEY:?Set KIBANA_API_KEY (or ELASTIC_PASSWORD for local stacks)}"

export KIBANA_URL KIBANA_API_KEY
exec python3 "$ROOT/integration/assets/agent/install.py" "$@"
