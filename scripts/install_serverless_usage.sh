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
# Optional:
#   PROJECT_ID / SL_URL / SL_API_KEY — if all three are set, registers one project after install

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$ROOT/integration/assets/serverless/assets"

: "${ES_URL:?Set ES_URL to the monitoring cluster Elasticsearch URL}"
: "${ES_API_KEY:?Set ES_API_KEY}"
: "${KIBANA_URL:?Set KIBANA_URL}"
: "${KIBANA_API_KEY:?Set KIBANA_API_KEY}"

es() {
  local method="$1" path="$2"
  shift 2
  curl -sS -X "$method" "${ES_URL%/}${path}" \
    -H "Authorization: ApiKey ${ES_API_KEY}" \
    -H "Content-Type: application/json" \
    "$@"
}

echo "Installing ingest pipeline chargeback-serverless-usage..."
es PUT "/_ingest/pipeline/chargeback-serverless-usage" \
  --data-binary @"$ASSETS/pipelines/chargeback-serverless-usage.json" >/dev/null

echo "Creating staging index monitoring-indices-serverless (ignore if exists)..."
es PUT "/monitoring-indices-serverless" \
  --data-binary @"$ASSETS/indices/monitoring-indices-serverless.json" >/dev/null \
  || true

echo "Creating project registry .chargeback-serverless-projects (ignore if exists)..."
es PUT "/.chargeback-serverless-projects" \
  --data-binary @"$ASSETS/indices/chargeback-serverless-projects.json" >/dev/null \
  || true

echo "Importing looping collector workflow..."
WF_JSON="$(python3 -c "import json,sys; print(json.dumps({'yaml': open(sys.argv[1]).read()}))" \
  "$ASSETS/workflows/chargeback-serverless-usage-collector.yml")"
curl -sS -X POST "${KIBANA_URL%/}/api/workflows/workflow" \
  -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
  -H "kbn-xsrf: true" \
  -H "Content-Type: application/json" \
  -d "$WF_JSON" >/dev/null

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
