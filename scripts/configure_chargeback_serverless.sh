#!/usr/bin/env bash
# Configure Chargeback Serverless credentials, project keys, and currency rates.
# Can be run standalone or invoked by install / deploy automation.
#
# Environment variables:
#   ES_URL                  Elasticsearch endpoint (Hub)
#   ES_API_KEY              Hub ES API key (or HUB_ES_API_KEY)
#   ORGANIZATION_ID         Elastic Cloud organization ID
#   BILLING_API_KEY         Elastic Cloud Billing API key
#   USAGE_API_KEY           Elastic Cloud Usage API key
#   HUB_ES_API_KEY          (Optional) Dedicated Hub ES API key if different from ES_API_KEY
#   PROJECT_ID              (Optional) Remote project ID to register
#   SL_API_KEY              (Optional) Least-privileged remote project API key for PROJECT_ID
#   CONF_RATE               (Optional) Chargeable unit rate (default: 1)
#   CONF_RATE_UNIT          (Optional) Rate unit name (default: ECU)
#   CONF_CURRENCY_SYMBOL    (Optional) Currency symbol (e.g. $, R, €, £)
#   BILLING_API_BASE        (Optional) default: https://billing.elastic-cloud.com
#   CLOUD_API_BASE          (Optional) default: https://api.elastic-cloud.com
#   CURL_INSECURE           (Optional) 1 = skip TLS verification

set -euo pipefail

ES_URL="${ES_URL:-${ES_HOST:-https://127.0.0.1:9200}}"
ES_API_KEY="${ES_API_KEY:-${HUB_ES_API_KEY:-${HUB_KIBANA_API_KEY:-}}}"
HUB_ES_API_KEY="${HUB_ES_API_KEY:-$ES_API_KEY}"
ORGANIZATION_ID="${ORGANIZATION_ID:-}"
BILLING_API_KEY="${BILLING_API_KEY:-}"
USAGE_API_KEY="${USAGE_API_KEY:-}"

BILLING_API_BASE="${BILLING_API_BASE:-https://billing.elastic-cloud.com}"
CLOUD_API_BASE="${CLOUD_API_BASE:-https://api.elastic-cloud.com}"
CONF_RATE="${CONF_RATE:-1}"
CONF_RATE_UNIT="${CONF_RATE_UNIT:-ECU}"
CONF_CURRENCY_SYMBOL="${CONF_CURRENCY_SYMBOL:-}"

PROJECT_ID="${PROJECT_ID:-${project_id:-}}"
SL_API_KEY="${SL_API_KEY:-${sl_api_key:-}}"

if [[ -z "$CONF_CURRENCY_SYMBOL" ]]; then
  case "$CONF_RATE_UNIT" in
    ZAR) CONF_CURRENCY_SYMBOL="R" ;;
    USD) CONF_CURRENCY_SYMBOL="$" ;;
    EUR) CONF_CURRENCY_SYMBOL="€" ;;
    GBP) CONF_CURRENCY_SYMBOL="£" ;;
    *)   CONF_CURRENCY_SYMBOL="$CONF_RATE_UNIT" ;;
  esac
fi

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

ES_CURL_TLS_FLAG="$(curl_tls_args_for "$ES_URL")"
es_curl() {
  if [[ -n "$ES_CURL_TLS_FLAG" ]]; then
    curl -sS "$ES_CURL_TLS_FLAG" "$@"
  else
    curl -sS "$@"
  fi
}

es() {
  local method="$1" path="$2"
  shift 2
  es_curl --fail-with-body -X "$method" "${ES_URL%/}${path}" \
    -H "Authorization: ApiKey ${ES_API_KEY}" \
    -H "Content-Type: application/json" \
    "$@"
}

: "${ES_URL:?Set ES_URL}"
: "${ES_API_KEY:?Set ES_API_KEY (or HUB_ES_API_KEY / HUB_KIBANA_API_KEY)}"
: "${ORGANIZATION_ID:?Set ORGANIZATION_ID}"
: "${BILLING_API_KEY:?Set BILLING_API_KEY}"
: "${USAGE_API_KEY:?Set USAGE_API_KEY}"

echo "Configuring Chargeback Serverless integration credentials..."

# 1. Fetch existing config doc to preserve existing project_api_keys and lists
EXISTING_CONFIG="{}"
CONFIG_GET_HTTP=$(es_curl -o /tmp/cbs-existing-config.json -w '%{http_code}' \
  -X GET "${ES_URL%/}/chargeback-serverless-config/_doc/config" \
  -H "Authorization: ApiKey ${ES_API_KEY}" || true)

if [[ "$CONFIG_GET_HTTP" == "200" ]] && [[ -f /tmp/cbs-existing-config.json ]]; then
  EXISTING_CONFIG=$(jq '._source // {}' /tmp/cbs-existing-config.json 2>/dev/null || echo "{}")
fi

CONFIG_DOC=$(python3 -c "
import json, sys, os
from datetime import datetime, timezone

existing = json.loads(sys.argv[1])
project_keys = existing.get('project_api_keys', {})

# If PROJECT_ID and SL_API_KEY are provided, merge them
pid = os.environ.get('PROJECT_ID', '').strip()
pkey = os.environ.get('SL_API_KEY', '').strip()
if pid and pkey:
    project_keys[pid] = pkey

doc = {
    'organization_id': os.environ.get('ORGANIZATION_ID'),
    'billing_api_base': os.environ.get('BILLING_API_BASE'),
    'cloud_api_base': os.environ.get('CLOUD_API_BASE'),
    'billing_api_key': os.environ.get('BILLING_API_KEY'),
    'usage_api_key': os.environ.get('USAGE_API_KEY'),
    'hub_es_api_key': os.environ.get('HUB_ES_API_KEY'),
    'project_api_keys': project_keys,
    'include_project_ids': existing.get('include_project_ids', []),
    'exclude_project_ids': existing.get('exclude_project_ids', []),
    'updated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
}
print(json.dumps(doc))
" "$EXISTING_CONFIG")

es PUT "/chargeback-serverless-config/_doc/config" -d "$CONFIG_DOC" >/dev/null
echo "  ✓ chargeback-serverless-config updated (Org ID: $ORGANIZATION_ID)"

if [[ -n "$PROJECT_ID" && -n "$SL_API_KEY" ]]; then
  echo "  ✓ Registered remote project key for project: $PROJECT_ID"
fi

# 2. Update chargeback_conf_lookup
echo "Setting chargeback_conf_lookup currency rate window..."
CONF_DOC=$(jq -n \
  --argjson rate "$CONF_RATE" \
  --arg unit "$CONF_RATE_UNIT" \
  --arg sym "$CONF_CURRENCY_SYMBOL" \
  '{
    "@timestamp": "1970-01-01T00:00:00.000Z",
    config_join_key: "config",
    conf_start_date: "2020-01-01T00:00:00.000Z",
    conf_end_date: "2099-12-31T23:59:59.999Z",
    conf_chargeable_unit_rate: $rate,
    conf_chargeable_unit_rate_unit: $unit,
    conf_currency_symbol: $sym
  }')
es PUT "/chargeback_conf_lookup/_doc/config" -d "$CONF_DOC" >/dev/null
echo "  ✓ chargeback_conf_lookup updated ($CONF_CURRENCY_SYMBOL 1 = $CONF_RATE $CONF_RATE_UNIT)"

echo "Configuration completed successfully."
