#!/usr/bin/env bash
# Install the Chargeback Serverless usage collector add-on on the monitoring cluster.
# Requires Chargeback integration 0.5.0+. Does not put secrets in the workflow.
#
# Export before running:
#   ES_URL          Elasticsearch URL of the monitoring cluster
#   ES_API_KEY      API key with manage privileges for the staging and project indices
#   KIBANA_URL      Kibana URL of the monitoring cluster
#   KIBANA_API_KEY  API key that can create workflows
#
# Or for local elastic-package stacks (basic auth):
#   ELASTIC_PASSWORD  (default: changeme) — creates a short-lived ES API key used for both ES and Kibana
#
# Optional:
#   PROJECT_ID / SL_URL / SL_API_KEY — if all three are set, registers one project after install
#   CURL_INSECURE=1 — skip TLS verification (also auto-enabled for localhost / 127.0.0.1 / ::1)
#   ALLOW_MISSING_WORKFLOWS=1 — continue only when the Workflows API is missing (HTTP 404);
#                               other statuses (400/401/403/5xx) still fail

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$ROOT/integration/assets/serverless/assets"
WF_NAME="Chargeback - Serverless usage collector"

ES_URL="${ES_URL:-${ES_HOST:-https://127.0.0.1:9200}}"
KIBANA_URL="${KIBANA_URL:-${KIBANA_HOST:-https://127.0.0.1:5601}}"
ALLOW_MISSING_WORKFLOWS="${ALLOW_MISSING_WORKFLOWS:-0}"

is_local_url() {
  case "$1" in
    *127.0.0.1*|*localhost*|*\[::1\]*|*:\/\/::1*) return 0 ;;
    *) return 1 ;;
  esac
}

# Verify TLS by default. Skip only for explicit CURL_INSECURE=1 or local stack URLs.
CURL_TLS=()
if [[ "${CURL_INSECURE:-0}" == "1" ]] || is_local_url "$ES_URL" || is_local_url "$KIBANA_URL"; then
  CURL_TLS=(-k)
  if [[ "${CURL_INSECURE:-0}" != "1" ]]; then
    echo "TLS verification skipped for local URL (set CURL_INSECURE=0 and use a CA-trusted host to verify)."
  else
    echo "TLS verification skipped (CURL_INSECURE=1)."
  fi
fi

if [[ -z "${ES_API_KEY:-}" || -z "${KIBANA_API_KEY:-}" ]]; then
  if [[ -n "${ELASTIC_PASSWORD:-}" || -n "${PASS:-}" ]]; then
    PASS_BOOT="${ELASTIC_PASSWORD:-${PASS:-changeme}}"
    USER_BOOT="${ELASTIC_USER:-elastic}"
    echo "Creating short-lived Elasticsearch API key for add-on install..."
    ENC=$(curl -sS "${CURL_TLS[@]}" -u "${USER_BOOT}:${PASS_BOOT}" -H "Content-Type: application/json" \
      -X POST "${ES_URL%/}/_security/api_key" \
      -d '{"name":"chargeback-serverless-install","expiration":"2h"}' | jq -r '.encoded // empty')
    if [[ -z "$ENC" ]]; then
      echo "Failed to create Elasticsearch API key. Set ES_API_KEY and KIBANA_API_KEY explicitly." >&2
      exit 1
    fi
    ES_API_KEY="${ES_API_KEY:-$ENC}"
    KIBANA_API_KEY="${KIBANA_API_KEY:-$ENC}"
  fi
fi

: "${ES_URL:?Set ES_URL to the monitoring cluster Elasticsearch URL}"
: "${ES_API_KEY:?Set ES_API_KEY (or ELASTIC_PASSWORD for local stacks)}"
: "${KIBANA_URL:?Set KIBANA_URL}"
: "${KIBANA_API_KEY:?Set KIBANA_API_KEY (or ELASTIC_PASSWORD for local stacks)}"

es() {
  local method="$1" path="$2"
  shift 2
  curl -sS "${CURL_TLS[@]}" --fail-with-body -X "$method" "${ES_URL%/}${path}" \
    -H "Authorization: ApiKey ${ES_API_KEY}" \
    -H "Content-Type: application/json" \
    "$@"
}

# Create an index; ignore only resource_already_exists_exception. Fail on auth/mapping/other errors.
es_put_index() {
  local path="$1" file="$2"
  local http
  http=$(curl -sS "${CURL_TLS[@]}" -o /tmp/chargeback-es-put-index.json -w '%{http_code}' \
    -X PUT "${ES_URL%/}${path}" \
    -H "Authorization: ApiKey ${ES_API_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary @"$file" || true)
  if [[ "$http" == "200" || "$http" == "201" ]]; then
    return 0
  fi
  if jq -e '.error.type == "resource_already_exists_exception"' /tmp/chargeback-es-put-index.json >/dev/null 2>&1; then
    echo "  already exists — leaving mappings as-is"
    return 0
  fi
  echo "Failed to create index ${path} (HTTP ${http:-000}): $(head -c 400 /tmp/chargeback-es-put-index.json 2>/dev/null)" >&2
  exit 1
}

echo "Installing ingest pipeline chargeback-serverless-usage..."
# Strip system-managed fields if the asset was exported via GET _ingest/pipeline.
PIPE_BODY=$(jq 'del(.created_date_millis, .modified_date_millis, .version)' \
  "$ASSETS/pipelines/chargeback-serverless-usage.json")
es PUT "/_ingest/pipeline/chargeback-serverless-usage" -d "$PIPE_BODY" >/dev/null

echo "Creating staging index monitoring-indices-serverless (ignore if exists)..."
es_put_index "/monitoring-indices-serverless" \
  "$ASSETS/indices/monitoring-indices-serverless.json"

echo "Creating project registry .chargeback-serverless-projects (ignore if exists)..."
es_put_index "/.chargeback-serverless-projects" \
  "$ASSETS/indices/chargeback-serverless-projects.json"

echo "Importing looping collector workflow..."
WF_JSON="$(python3 -c "import json,sys; print(json.dumps({'yaml': open(sys.argv[1]).read()}))" \
  "$ASSETS/workflows/chargeback-serverless-usage-collector.yml")"

WF_LIST_HTTP=$(curl -sS "${CURL_TLS[@]}" -o /tmp/chargeback-wf-list.json -w '%{http_code}' \
  -X GET "${KIBANA_URL%/}/api/workflows" \
  -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
  -H "kbn-xsrf: true" || true)

if [[ "$WF_LIST_HTTP" == "404" ]]; then
  if [[ "$ALLOW_MISSING_WORKFLOWS" == "1" ]]; then
    echo "  WARN: Kibana Workflows API missing (HTTP 404). Pipeline and indices installed; import the workflow YAML manually on a Workflows-enabled Kibana."
  else
    echo "Failed: Kibana Workflows API missing (HTTP 404)." >&2
    echo "Set ALLOW_MISSING_WORKFLOWS=1 to continue when the Workflows API is absent (local stacks)." >&2
    exit 1
  fi
elif [[ "$WF_LIST_HTTP" != "200" ]]; then
  echo "Failed to list workflows (HTTP ${WF_LIST_HTTP:-000}): $(head -c 300 /tmp/chargeback-wf-list.json 2>/dev/null)" >&2
  echo "ALLOW_MISSING_WORKFLOWS=1 only ignores a missing Workflows API (HTTP 404), not auth or server errors." >&2
  exit 1
else
  EXISTING_IDS=()
  while IFS= read -r wid; do
    [[ -n "$wid" ]] && EXISTING_IDS+=("$wid")
  done < <(jq -r --arg n "$WF_NAME" '
      (.results // .items // .)
      | if type=="array" then . else [] end
      | map(select(.name == $n) | .id)
      | .[]
    ' /tmp/chargeback-wf-list.json 2>/dev/null || true)

  if [[ "${#EXISTING_IDS[@]}" -gt 1 ]]; then
    echo "Found ${#EXISTING_IDS[@]} workflows named \"$WF_NAME\" (${EXISTING_IDS[*]})." >&2
    echo "Delete the duplicates in Kibana Workflows, then re-run this installer." >&2
    exit 1
  fi

  if [[ "${#EXISTING_IDS[@]}" -eq 1 ]]; then
    WID="${EXISTING_IDS[0]}"
    WF_HTTP=$(curl -sS "${CURL_TLS[@]}" -o /tmp/chargeback-wf-install.json -w '%{http_code}' \
      -X PUT "${KIBANA_URL%/}/api/workflows/workflow/${WID}" \
      -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
      -H "kbn-xsrf: true" \
      -H "Content-Type: application/json" \
      -d "$WF_JSON" || true)
    if [[ "$WF_HTTP" == "200" || "$WF_HTTP" == "201" ]]; then
      echo "  workflow updated (id $WID, HTTP $WF_HTTP)."
    else
      echo "Failed to update workflow $WID (HTTP ${WF_HTTP:-000}): $(head -c 300 /tmp/chargeback-wf-install.json 2>/dev/null)" >&2
      exit 1
    fi
  else
    WF_HTTP=$(curl -sS "${CURL_TLS[@]}" -o /tmp/chargeback-wf-install.json -w '%{http_code}' \
      -X POST "${KIBANA_URL%/}/api/workflows/workflow" \
      -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
      -H "kbn-xsrf: true" \
      -H "Content-Type: application/json" \
      -d "$WF_JSON" || true)
    if [[ "$WF_HTTP" == "200" || "$WF_HTTP" == "201" ]]; then
      echo "  workflow created (HTTP $WF_HTTP)."
    else
      echo "Failed to create workflow (HTTP ${WF_HTTP:-000}): $(head -c 300 /tmp/chargeback-wf-install.json 2>/dev/null)" >&2
      exit 1
    fi
  fi
fi

if [[ -n "${PROJECT_ID:-}" && -n "${SL_URL:-}" && -n "${SL_API_KEY:-}" ]]; then
  echo "Registering Serverless project ${PROJECT_ID}..."
  es PUT "/.chargeback-serverless-projects/_doc/${PROJECT_ID}" -d "$(python3 -c "
import json, os
print(json.dumps({
  'project_id': os.environ['PROJECT_ID'],
  'sl_url': os.environ['SL_URL'].rstrip('/'),
  'sl_api_key': os.environ['SL_API_KEY'],
}))
")" >/dev/null
  echo "Project registered. Re-PUT the same _id to replace credentials."
else
  echo "No PROJECT_ID/SL_URL/SL_API_KEY set. Register projects with Dev Tools:"
  cat <<'EOF'
PUT .chargeback-serverless-projects/_doc/<project_id>
{
  "project_id": "<project_id>",
  "sl_url": "https://<project>.es.<region>.<csp>.elastic.cloud",
  "sl_api_key": "<encoded key>"
}
EOF
fi

echo "Done. Run the workflow once from Kibana Workflows, then check:"
echo "  GET monitoring-indices-serverless/_count"
echo "See integration/assets/serverless/Instructions.md for RBAC and verification."
