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
# Optional:
#   ES_URL          used only when minting the short-lived API key (default https://127.0.0.1:9200)
#   CURL_INSECURE=1 skip TLS verification for all destinations
#
# Pass-through flags (e.g. --verify, --skip-workflows) are forwarded to install.py.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

KIBANA_URL="${KIBANA_URL:-${KIBANA_HOST:-https://127.0.0.1:5601}}"
ES_URL="${ES_URL:-${ES_HOST:-https://127.0.0.1:9200}}"

# Exact loopback hostnames only (not substrings like localhost.example.com).
url_hostname() {
  python3 -c "from urllib.parse import urlparse; print((urlparse('''$1''').hostname or '').lower())"
}

curl_tls_args_for() {
  if [[ "${CURL_INSECURE:-0}" == "1" ]]; then
    printf '%s' '-k'
    return
  fi
  case "$(url_hostname "$1")" in
    localhost|127.0.0.1|::1) printf '%s' '-k' ;;
  esac
}

ES_CURL_TLS=()
[[ "$(curl_tls_args_for "$ES_URL")" == "-k" ]] && ES_CURL_TLS=(-k)

if [[ -z "${KIBANA_API_KEY:-}" ]]; then
  if [[ -n "${ELASTIC_PASSWORD:-}" || -n "${PASS:-}" ]]; then
    PASS_BOOT="${ELASTIC_PASSWORD:-${PASS:-changeme}}"
    USER_BOOT="${ELASTIC_USER:-elastic}"
    echo "Creating short-lived Elasticsearch API key for Agent Builder install..."
    ENC=$(curl -sS "${ES_CURL_TLS[@]}" -u "${USER_BOOT}:${PASS_BOOT}" -H "Content-Type: application/json" \
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
