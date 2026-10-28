#!/usr/bin/env bash
# ==============================================================================
# E2E Deployment Script for Chargeback Serverless
#
# Automates:
#   1. Environment & credential resolution (from .env or interactive prompts)
#   2. Pre-flight health and connectivity checks (ES & Kibana)
#   3. Dashboard generation & asset sync
#   4. Package building with elastic-package
#   5. Fleet package upload to Kibana EPM API
#   6. Hub infrastructure install (lean index mappings, pipelines, workflows)
#   7. Post-configuration (Cloud org credentials, remote project harvesting keys, currency rates)
#   8. Workflow triggering (Billing & Usage runs)
#   9. Verification queries and health check reporting
#
# Usage:
#   ./scripts/deploy_chargeback_serverless.sh
#   ./scripts/deploy_chargeback_serverless.sh --skip-build
#   ./scripts/deploy_chargeback_serverless.sh --skip-workflows
#   ./scripts/deploy_chargeback_serverless.sh --verify-only
#   ./scripts/deploy_chargeback_serverless.sh --help
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INTEGRATIONS_REPO="${INTEGRATIONS_REPO:-$REPO_ROOT/../integrations}"
ASSETS_DIR="$REPO_ROOT/integration/assets/chargeback_serverless"
DIST_DIR="$ASSETS_DIR/dist"

SKIP_BUILD=0
SKIP_WORKFLOWS=0
VERIFY_ONLY=0
NON_INTERACTIVE=0

usage() {
  cat <<'EOF'
Usage: ./scripts/deploy_chargeback_serverless.sh [OPTIONS]

Options:
  --skip-build          Skip rebuilding package and use existing dist zip
  --skip-workflows      Deploy package and configuration without triggering workflows
  --verify-only         Run verification queries on existing deployment and exit
  --non-interactive     Fail immediately if required variables are missing instead of prompting
  -h, --help            Show this help message

Environment variables (loaded from .env if present):
  ES_URL / sl_url       Elasticsearch URL (Hub)
  KIBANA_URL            Kibana URL (Hub, defaults to sl_url with .kb. instead of .es.)
  ES_API_KEY            Elasticsearch Hub API key (or HUB_KIBANA_API_KEY / HUB_ES_API_KEY)
  KIBANA_API_KEY        Kibana Hub API key (or HUB_KIBANA_API_KEY)
  ORGANIZATION_ID       Elastic Cloud organization ID
  BILLING_API_KEY       Elastic Cloud Billing API key
  USAGE_API_KEY         Elastic Cloud Usage API key
  PROJECT_ID / project_id  (Optional) Remote project ID to register in project_api_keys
  SL_API_KEY / sl_api_key  (Optional) Least-privileged remote project API key
  CONF_RATE             (Optional) Chargeable unit rate (default: 1)
  CONF_RATE_UNIT        (Optional) Currency/unit code (default: ECU)
  CONF_CURRENCY_SYMBOL  (Optional) Currency symbol (e.g. $, R, €, £)
  CURL_INSECURE         (Optional) 1 = skip TLS verification
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    --skip-workflows) SKIP_WORKFLOWS=1; shift ;;
    --verify-only) VERIFY_ONLY=1; shift ;;
    --non-interactive) NON_INTERACTIVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

echo "================================================================="
echo "       Chargeback Serverless E2E Deployment Automation           "
echo "================================================================="

# ------------------------------------------------------------------------------
# 1. Load .env and resolve configuration variables
# ------------------------------------------------------------------------------
ENV_FILE="$REPO_ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  echo "Loading environment from .env..."
  while IFS= read -r line || [[ -n "$line" ]]; do
    # Strip comments and empty lines
    line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [[ -z "$line" || "$line" =~ ^# ]]; then
      continue
    fi
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      val="${BASH_REMATCH[2]}"
      # Remove surrounding quotes if any
      val="$(echo "$val" | sed 's/^["'"'"']//;s/["'"'"']$//')"
      # Only export if not already explicitly set in environment
      if [[ -z "${!key:-}" ]]; then
        export "$key"="$val"
      fi
    fi
  done < "$ENV_FILE"
fi

# Derive URLs and Keys with sensible fallbacks
ES_URL="${ES_URL:-${sl_url:-${ES_HOST:-}}}"

if [[ -z "${KIBANA_URL:-}" ]]; then
  if [[ -n "${KIBANA_HOST:-}" ]]; then
    KIBANA_URL="$KIBANA_HOST"
  elif [[ -n "$ES_URL" && "$ES_URL" =~ \.es\. ]]; then
    KIBANA_URL="${ES_URL//.es./.kb.}"
  fi
fi

# HUB_KIBANA_API_KEY on serverless hub works for both Kibana and ES admin operations
HUB_KEY="${HUB_KIBANA_API_KEY:-${HUB_ES_API_KEY:-}}"
ES_API_KEY="${ES_API_KEY:-$HUB_KEY}"
KIBANA_API_KEY="${KIBANA_API_KEY:-$HUB_KEY}"
HUB_ES_API_KEY="${HUB_ES_API_KEY:-$ES_API_KEY}"

ORGANIZATION_ID="${ORGANIZATION_ID:-}"
BILLING_API_KEY="${BILLING_API_KEY:-}"
USAGE_API_KEY="${USAGE_API_KEY:-}"
PROJECT_ID="${PROJECT_ID:-${project_id:-}}"
SL_API_KEY="${SL_API_KEY:-${sl_api_key:-}}"

CONF_RATE="${CONF_RATE:-}"
CONF_RATE_UNIT="${CONF_RATE_UNIT:-}"
CONF_CURRENCY_SYMBOL="${CONF_CURRENCY_SYMBOL:-}"

# Helper to prompt for missing variables
prompt_var() {
  local var_name="$1"
  local prompt_text="$2"
  local is_secret="${3:-0}"
  local current_val="${!var_name:-}"

  if [[ -n "$current_val" ]]; then
    return 0
  fi

  if [[ "$NON_INTERACTIVE" -eq 1 || ! -t 0 ]]; then
    echo "ERROR: Required variable '$var_name' is missing." >&2
    exit 1
  fi

  if [[ "$is_secret" -eq 1 ]]; then
    read -r -s -p "$prompt_text: " input_val
    echo ""
  else
    read -r -p "$prompt_text: " input_val
  fi

  if [[ -z "$input_val" ]]; then
    echo "ERROR: '$var_name' cannot be empty." >&2
    exit 1
  fi
  export "$var_name"="$input_val"
}

prompt_var "ES_URL" "Enter Hub Elasticsearch URL (e.g. https://<id>.es.<region>.<cloud>.elastic.cloud)"
prompt_var "KIBANA_URL" "Enter Hub Kibana URL (e.g. https://<id>.kb.<region>.<cloud>.elastic.cloud)"
prompt_var "ES_API_KEY" "Enter Hub Elasticsearch API Key" 1
prompt_var "KIBANA_API_KEY" "Enter Hub Kibana API Key" 1
prompt_var "ORGANIZATION_ID" "Enter Elastic Cloud Organization ID"
prompt_var "BILLING_API_KEY" "Enter Elastic Cloud Billing API Key" 1
prompt_var "USAGE_API_KEY" "Enter Elastic Cloud Usage API Key" 1

export ES_URL KIBANA_URL ES_API_KEY KIBANA_API_KEY HUB_ES_API_KEY
export ORGANIZATION_ID BILLING_API_KEY USAGE_API_KEY PROJECT_ID SL_API_KEY
export CONF_RATE CONF_RATE_UNIT CONF_CURRENCY_SYMBOL

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

ES_CURL_TLS="$(curl_tls_args_for "$ES_URL")"
KBN_CURL_TLS="$(curl_tls_args_for "$KIBANA_URL")"

es_curl() {
  if [[ -n "$ES_CURL_TLS" ]]; then
    curl -sS --retry 3 --retry-connrefused "$ES_CURL_TLS" "$@"
  else
    curl -sS --retry 3 --retry-connrefused "$@"
  fi
}

kbn_curl() {
  if [[ -n "$KBN_CURL_TLS" ]]; then
    curl -sS --retry 3 --retry-connrefused "$KBN_CURL_TLS" "$@"
  else
    curl -sS --retry 3 --retry-connrefused "$@"
  fi
}

# ------------------------------------------------------------------------------
# 2. Pre-flight Checks
# ------------------------------------------------------------------------------
echo ""
echo ">>> Pre-flight connectivity checks..."
echo "  Target ES:     $ES_URL"
echo "  Target Kibana: $KIBANA_URL"

ES_CHECK=$(es_curl -o /tmp/cbs-es-check.json -w '%{http_code}' \
  "${ES_URL%/}/" \
  -H "Authorization: ApiKey ${ES_API_KEY}" || true)

if [[ "$ES_CHECK" != "200" ]]; then
  echo "FAILED: Cannot connect to Elasticsearch ($ES_URL, HTTP $ES_CHECK)." >&2
  head -c 300 /tmp/cbs-es-check.json 2>/dev/null >&2 || true
  exit 1
fi
ES_CLUSTER_NAME=$(jq -r '.cluster_name // "unknown"' /tmp/cbs-es-check.json 2>/dev/null || echo "unknown")
echo "  ✓ Connected to Elasticsearch (cluster: $ES_CLUSTER_NAME)"

KBN_CHECK=$(kbn_curl -o /tmp/cbs-kbn-check.json -w '%{http_code}' \
  "${KIBANA_URL%/}/api/status" \
  -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
  -H "kbn-xsrf: true" || true)

if [[ "$KBN_CHECK" != "200" ]]; then
  echo "FAILED: Cannot connect to Kibana API ($KIBANA_URL, HTTP $KBN_CHECK)." >&2
  head -c 300 /tmp/cbs-kbn-check.json 2>/dev/null >&2 || true
  exit 1
fi
echo "  ✓ Connected to Kibana API (status: available)"

# ------------------------------------------------------------------------------
# 3. Verify-only shortcut
# ------------------------------------------------------------------------------
run_verification() {
  echo ""
  echo ">>> Verifying lookup indices and project status..."
  local indices=(
    "billing_cluster_cost_lookup"
    "billing_realized_pool_lookup"
    "chargeback-serverless-projects"
    "chargeback-serverless-state"
    "cluster_datastream_contribution_lookup"
    "cluster_ai_model_contribution_lookup"
  )

  printf "\n%-42s | %-12s\n" "Index" "Doc Count"
  printf "%-42s-+-%-12s\n" "------------------------------------------" "------------"
  for idx in "${indices[@]}"; do
    local cnt
    cnt=$(es_curl -sS "${ES_URL%/}/${idx}/_count" -H "Authorization: ApiKey ${ES_API_KEY}" | jq -r '._shards.successful as $s | if $s > 0 then .count else "error" end' 2>/dev/null || echo "missing")
    printf "%-42s | %-12s\n" "$idx" "$cnt"
  done
  echo ""

  echo ">>> Project harvesting status (chargeback-serverless-state):"
  local state_json
  state_json=$(es_curl -sS "${ES_URL%/}/chargeback-serverless-state/_search?size=50" -H "Authorization: ApiKey ${ES_API_KEY}" 2>/dev/null || echo "{}")
  echo "$state_json" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    data = {}
hits = data.get('hits', {}).get('hits', [])
if not hits:
    print('  (No project state records found yet)')
for h in hits:
    src = h.get('_source', {})
    pid = src.get('project_id', 'unknown')
    pname = src.get('project_name', pid)
    status = src.get('last_status', 'unknown')
    indices_cnt = src.get('index_count', 0)
    print(f'  • {pname} ({pid}): status={status}, indices={indices_cnt}')
" || true

  echo ""
  echo ">>> Kibana Dashboards:"
  echo "  • Billing Overview:  ${KIBANA_URL%/}/app/dashboards#/view/chargeback_serverless-b6cecac0-ebe7-43de-bfc6-1ff287ad860a"
  echo "  • Usage Allocation:  ${KIBANA_URL%/}/app/dashboards#/view/chargeback_serverless-21588d0e-fb6a-4f76-ad2f-cd7b3d7a3d7c"
  echo "  • Configuration:     ${KIBANA_URL%/}/app/dashboards#/view/chargeback_serverless-7ec9743a-ccfa-43d2-8311-2f25893dadae"
  echo ""
}

if [[ "$VERIFY_ONLY" -eq 1 ]]; then
  run_verification
  exit 0
fi

# ------------------------------------------------------------------------------
# 4. Build package with package builder
# ------------------------------------------------------------------------------
PACKAGE_DIR="$INTEGRATIONS_REPO/packages/chargeback_serverless"
VERSION="$(grep -E '^version:' "$ASSETS_DIR/manifest.yml" 2>/dev/null | awk '{print $2}' || echo "0.1.6")"
ZIP_NAME="chargeback_serverless-${VERSION}.zip"
TARGET_ZIP="$DIST_DIR/$ZIP_NAME"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo ""
  echo ">>> Step 1: Re-generating latest dashboard definitions..."
  python3 "$REPO_ROOT/scripts/generate_dashboards.py" "$ASSETS_DIR/kibana"

  if [[ ! -d "$PACKAGE_DIR" ]]; then
    echo "ERROR: Package source not found at $PACKAGE_DIR" >&2
    echo "Set INTEGRATIONS_REPO to your elastic/integrations repository." >&2
    exit 1
  fi

  echo ""
  echo ">>> Step 2: Syncing Kibana assets to integrations package..."
  cp -r "$ASSETS_DIR/kibana/"* "$PACKAGE_DIR/kibana/"

  echo ""
  echo ">>> Step 3: Building package with elastic-package ($VERSION)..."
  if command -v elastic-package >/dev/null 2>&1; then
    EP_CMD="elastic-package"
  else
    EP_CMD="go run github.com/elastic/elastic-package"
  fi

  (cd "$PACKAGE_DIR" && $EP_CMD build --skip-validation)

  BUILT_ZIP="$INTEGRATIONS_REPO/build/packages/$ZIP_NAME"
  if [[ ! -f "$BUILT_ZIP" ]]; then
    # Fallback check under package directory
    if [[ -f "$PACKAGE_DIR/build/packages/$ZIP_NAME" ]]; then
      BUILT_ZIP="$PACKAGE_DIR/build/packages/$ZIP_NAME"
    else
      echo "ERROR: Built package zip missing: $BUILT_ZIP" >&2
      exit 1
    fi
  fi

  mkdir -p "$DIST_DIR"
  cp "$BUILT_ZIP" "$TARGET_ZIP"
  echo "  ✓ Copied built zip to $TARGET_ZIP ($(ls -lh "$TARGET_ZIP" | awk '{print $5}'))"
else
  echo ""
  echo ">>> Step 1-3: Skipped package build (--skip-build)"
  if [[ ! -f "$TARGET_ZIP" ]]; then
    echo "ERROR: Target zip not found at $TARGET_ZIP. Run without --skip-build." >&2
    exit 1
  fi
  echo "  Using existing zip: $TARGET_ZIP"
fi

# ------------------------------------------------------------------------------
# 5. Call Kibana API to load the integration
# ------------------------------------------------------------------------------
echo ""
echo ">>> Step 4: Loading package zip into Kibana Fleet EPM..."
UPLOAD_HTTP=$(kbn_curl -o /tmp/cbs-upload.json -w '%{http_code}' \
  -X POST "${KIBANA_URL%/}/api/fleet/epm/packages" \
  -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
  -H "kbn-xsrf: true" \
  -H "elastic-api-version: 2023-10-31" \
  -H "Content-Type: application/zip" \
  --data-binary @"$TARGET_ZIP" || true)

if [[ "$UPLOAD_HTTP" != "200" && "$UPLOAD_HTTP" != "201" ]]; then
  echo "FAILED to upload package to Fleet EPM (HTTP $UPLOAD_HTTP):" >&2
  cat /tmp/cbs-upload.json >&2
  echo ""
  exit 1
fi
echo "  ✓ Fleet package $ZIP_NAME uploaded and activated successfully."

# ------------------------------------------------------------------------------
# 6. Run Hub infrastructure install and post configuration
# ------------------------------------------------------------------------------
echo ""
echo ">>> Step 5: Running Hub infrastructure install & post-configuration..."
"$REPO_ROOT/scripts/install_chargeback_serverless.sh"

# ------------------------------------------------------------------------------
# 7. Trigger Kibana Workflows
# ------------------------------------------------------------------------------
if [[ "$SKIP_WORKFLOWS" -eq 0 ]]; then
  echo ""
  echo ">>> Step 6: Triggering Kibana Workflows..."

  trigger_workflow() {
    local wf_id="$1"
    local wf_name="$2"
    local run_http
    run_http=$(kbn_curl -o /tmp/cbs-wf-run.json -w '%{http_code}' \
      -X POST "${KIBANA_URL%/}/api/workflows/workflow/${wf_id}/run" \
      -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
      -H "kbn-xsrf: true" \
      -H "Content-Type: application/json" \
      -d '{"inputs": {}}' || true)

    if [[ "$run_http" == "200" || "$run_http" == "201" ]]; then
      local exec_id
      exec_id=$(jq -r '.workflowExecutionId // "started"' /tmp/cbs-wf-run.json 2>/dev/null || echo "started")
      echo "  ✓ Triggered $wf_name (execution: $exec_id)"
    else
      echo "  ⚠ Warning: Failed to trigger $wf_name (HTTP $run_http): $(head -c 200 /tmp/cbs-wf-run.json 2>/dev/null)"
    fi
  }

  trigger_workflow "chargeback-serverless-billing" "Billing Workflow"
  trigger_workflow "chargeback-serverless-usage" "Usage Workflow"

  echo "Waiting 6 seconds for initial ingestion..."
  sleep 6
else
  echo ""
  echo ">>> Step 6: Skipped workflow execution (--skip-workflows)"
fi

# ------------------------------------------------------------------------------
# 8. Post-deployment Verification & Summary
# ------------------------------------------------------------------------------
run_verification

echo "================================================================="
echo "                Deployment Completed Successfully!               "
echo "================================================================="
