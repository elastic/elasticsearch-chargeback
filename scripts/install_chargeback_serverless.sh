#!/usr/bin/env bash
# Install Chargeback Serverless hub assets (native design).
# Requires the chargeback_serverless Fleet package on the hub, and Kibana Workflows.
#
# Export before running:
#   ES_URL              Hub Elasticsearch URL
#   ES_API_KEY          Hub ES API key (manage indices, pipelines, docs)
#   KIBANA_URL          Hub Kibana URL
#   KIBANA_API_KEY      Hub Kibana API key (create/update workflows)
#   ORGANIZATION_ID     Elastic Cloud organization ID (billing)
#   BILLING_API_KEY     Elastic Cloud API key with billing read
#   USAGE_API_KEY       Elastic Cloud API key with project list + ES monitor
#
# Optional:
#   BILLING_API_BASE    default https://billing.elastic-cloud.com
#   CLOUD_API_BASE      default https://api.elastic-cloud.com
#   CONF_RATE           ECU rate for chargeback_conf_lookup (default 1)
#   CONF_RATE_UNIT      default ECU
#   CURL_INSECURE=1     skip TLS verification for all destinations
#   ALLOW_MISSING_WORKFLOWS=1  continue when Workflows API returns 404

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$ROOT/integration/assets/chargeback_serverless"
WF_USAGE="Chargeback Serverless - Usage"
WF_BILLING="Chargeback Serverless - Billing"

ES_URL="${ES_URL:-${ES_HOST:-https://127.0.0.1:9200}}"
KIBANA_URL="${KIBANA_URL:-${KIBANA_HOST:-https://127.0.0.1:5601}}"
ALLOW_MISSING_WORKFLOWS="${ALLOW_MISSING_WORKFLOWS:-0}"
BILLING_API_BASE="${BILLING_API_BASE:-https://billing.elastic-cloud.com}"
CLOUD_API_BASE="${CLOUD_API_BASE:-https://api.elastic-cloud.com}"
CONF_RATE="${CONF_RATE:-}"
CONF_RATE_UNIT="${CONF_RATE_UNIT:-}"
CONF_CURRENCY_SYMBOL="${CONF_CURRENCY_SYMBOL:-}"

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

# bash 3.2 + set -u safe empty-array pattern
ES_CURL_TLS_FLAG="$(curl_tls_args_for "$ES_URL")"
KBN_CURL_TLS_FLAG="$(curl_tls_args_for "$KIBANA_URL")"
es_curl() {
  if [[ -n "$ES_CURL_TLS_FLAG" ]]; then
    curl -sS "$ES_CURL_TLS_FLAG" "$@"
  else
    curl -sS "$@"
  fi
}
kbn_curl() {
  if [[ -n "$KBN_CURL_TLS_FLAG" ]]; then
    curl -sS "$KBN_CURL_TLS_FLAG" "$@"
  else
    curl -sS "$@"
  fi
}

: "${ES_URL:?Set ES_URL}"
: "${ES_API_KEY:?Set ES_API_KEY}"
: "${KIBANA_URL:?Set KIBANA_URL}"
: "${KIBANA_API_KEY:?Set KIBANA_API_KEY}"
: "${ORGANIZATION_ID:?Set ORGANIZATION_ID}"
: "${BILLING_API_KEY:?Set BILLING_API_KEY}"
: "${USAGE_API_KEY:?Set USAGE_API_KEY}"

es() {
  local method="$1" path="$2"
  shift 2
  es_curl --fail-with-body -X "$method" "${ES_URL%/}${path}" \
    -H "Authorization: ApiKey ${ES_API_KEY}" \
    -H "Content-Type: application/json" \
    "$@"
}

es_put_index() {
  local path="$1" file="$2"
  local http
  http=$(es_curl -o /tmp/cbs-es-put-index.json -w '%{http_code}' \
    -X PUT "${ES_URL%/}${path}" \
    -H "Authorization: ApiKey ${ES_API_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary @"$file" || true)
  if [[ "$http" == "200" || "$http" == "201" ]]; then
    return 0
  fi
  if command -v jq >/dev/null 2>&1 && jq -e '.error.type == "resource_already_exists_exception"' /tmp/cbs-es-put-index.json >/dev/null 2>&1; then
    local map_body
    map_body=$(jq '.mappings' "$file")
    es_curl -o /tmp/cbs-es-put-map.json -w '%{http_code}' \
      -X PUT "${ES_URL%/}${path}/_mapping" \
      -H "Authorization: ApiKey ${ES_API_KEY}" \
      -H "Content-Type: application/json" \
      -d "$map_body" >/dev/null || true
    echo "  already exists — updated mappings ($path)"
    return 0
  fi
  echo "Failed to create index ${path} (HTTP ${http:-000}): $(head -c 400 /tmp/cbs-es-put-index.json 2>/dev/null)" >&2
  exit 1
}

install_or_update_workflow() {
  local name="$1" yaml_file="$2"
  local wf_json http existing
  wf_json="$(python3 -c "import json,sys; print(json.dumps({'yaml': open(sys.argv[1]).read()}))" "$yaml_file")"

  existing=$(jq -r --arg n "$name" '
      (.results // .items // .)
      | if type=="array" then . else [] end
      | map(select(.name == $n) | .id)
      | .[]
    ' /tmp/cbs-wf-list.json 2>/dev/null || true)

  local count
  count=$(printf '%s\n' "$existing" | grep -c . || true)
  if [[ "$count" -gt 1 ]]; then
    echo "Found $count workflows named \"$name\". Delete duplicates, then re-run." >&2
    exit 1
  fi

  if [[ "$count" -eq 1 ]]; then
    local wid="$existing"
    http=$(kbn_curl -o /tmp/cbs-wf-install.json -w '%{http_code}' \
      -X PUT "${KIBANA_URL%/}/api/workflows/workflow/${wid}" \
      -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
      -H "kbn-xsrf: true" \
      -H "Content-Type: application/json" \
      -d "$wf_json" || true)
    if [[ "$http" == "200" || "$http" == "201" ]]; then
      echo "  updated \"$name\" (id $wid)"
    else
      echo "Failed to update workflow $wid (HTTP ${http:-000}): $(head -c 300 /tmp/cbs-wf-install.json 2>/dev/null)" >&2
      exit 1
    fi
  else
    http=$(kbn_curl -o /tmp/cbs-wf-install.json -w '%{http_code}' \
      -X POST "${KIBANA_URL%/}/api/workflows/workflow" \
      -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
      -H "kbn-xsrf: true" \
      -H "Content-Type: application/json" \
      -d "$wf_json" || true)
    if [[ "$http" == "200" || "$http" == "201" ]]; then
      echo "  created \"$name\""
    else
      echo "Failed to create workflow \"$name\" (HTTP ${http:-000}): $(head -c 300 /tmp/cbs-wf-install.json 2>/dev/null)" >&2
      exit 1
    fi
  fi
}

echo "Installing classification pipeline chargeback-serverless-billing..."
PIPE_BODY=$(jq 'del(.created_date_millis, .modified_date_millis, .version)' \
  "$ASSETS/pipelines/chargeback-serverless-billing.json")
es PUT "/_ingest/pipeline/chargeback-serverless-billing" -d "$PIPE_BODY" >/dev/null

echo "Creating indices (lean mappings; ignore if exists)..."
for idx in \
  chargeback-serverless-config \
  chargeback-serverless-state \
  chargeback-serverless-projects \
  billing_cluster_cost_lookup \
  billing_realized_pool_lookup \
  cluster_datastream_contribution_lookup \
  cluster_deployment_contribution_lookup \
  chargeback_conf_lookup
do
  es_put_index "/${idx}" "$ASSETS/indices/${idx}.json"
done

echo "Applying integration configuration..."
"$ROOT/scripts/configure_chargeback_serverless.sh"

echo "Importing workflows..."
WF_LIST_HTTP=$(kbn_curl -o /tmp/cbs-wf-list.json -w '%{http_code}' \
  -X GET "${KIBANA_URL%/}/api/workflows" \
  -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
  -H "kbn-xsrf: true" || true)

if [[ "$WF_LIST_HTTP" == "404" ]]; then
  if [[ "$ALLOW_MISSING_WORKFLOWS" == "1" ]]; then
    echo "  WARN: Workflows API missing (HTTP 404). Indices and pipeline installed; import YAML manually."
  else
    echo "Failed: Kibana Workflows API missing (HTTP 404). Hub needs Workflows (Observability Complete)." >&2
    echo "Set ALLOW_MISSING_WORKFLOWS=1 to continue without workflows." >&2
    exit 1
  fi
elif [[ "$WF_LIST_HTTP" != "200" ]]; then
  echo "Failed to list workflows (HTTP ${WF_LIST_HTTP:-000}): $(head -c 300 /tmp/cbs-wf-list.json 2>/dev/null)" >&2
  exit 1
else
  install_or_update_workflow "$WF_USAGE" "$ASSETS/workflows/chargeback-serverless-usage.yml"
  install_or_update_workflow "$WF_BILLING" "$ASSETS/workflows/chargeback-serverless-billing.yml"
fi

echo "Done."
echo "  1. If not done: upload chargeback_serverless-0.1.6.zip (see Instructions.md)."
echo "  2. Open dashboards: [Chargeback Serverless] Billing / Usage / Configuration."
echo "  3. Run both workflows once from Kibana Workflows."
echo "  4. Verify: GET billing_cluster_cost_lookup/_count"
echo "See integration/assets/chargeback_serverless/Instructions.md"
