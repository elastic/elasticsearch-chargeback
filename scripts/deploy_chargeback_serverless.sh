#!/usr/bin/env bash
# ==============================================================================
# E2E Deployment Script for Chargeback Serverless
#
# Automates:
#   1. Environment & credential resolution (from macOS Keychain or interactive prompts)
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
SKIP_POLICY=0
SKIP_COLLECTOR=0
VERIFY_ONLY=0
NON_INTERACTIVE=0

usage() {
  cat <<'EOF'
Usage: ./scripts/deploy_chargeback_serverless.sh [OPTIONS]

Options:
  --skip-build          Skip rebuilding package and use existing dist zip
  --skip-policy         Upload package without creating/updating Fleet package policy
  --skip-collector      Skip running local one-shot CEL collector pass
  --verify-only         Run verification queries on existing deployment and exit
  --non-interactive     Fail immediately if required variables are missing instead of prompting
  -h, --help            Show this help message

Environment variables (loaded from macOS Keychain, or .env if present):
  ES_URL / sl_url       Elasticsearch URL (Hub)
  KIBANA_URL            Kibana URL (Hub, defaults to sl_url with .kb. instead of .es.)
  ES_API_KEY            Elasticsearch Hub API key (or HUB_KIBANA_API_KEY / HUB_ES_API_KEY)
  KIBANA_API_KEY        Kibana Hub API key (or HUB_KIBANA_API_KEY)
  ORGANIZATION_ID       Elastic Cloud organization ID
  CLOUD_API_KEY         Elastic Cloud API key (Billing read and Serverless project read)
  HUB_ES_API_KEY        (Optional) Dedicated Hub ES API key if different from ES_API_KEY
  PROJECT_ID            (Optional) Remote project ID to register in project_api_keys
  SL_API_KEY            (Optional) Least-privileged remote project API key
  LOOKBEHIND            (Optional) Historical billing lookbehind days (default: 30)
  CONF_RATE             (Optional) Chargeable unit rate (default: 1.0)
  CONF_RATE_UNIT        (Optional) Currency/unit code (default: USD)
  CONF_CURRENCY_SYMBOL  (Optional) Currency symbol (e.g. $, R, €, £)
  CURL_INSECURE         (Optional) 1 = skip TLS verification
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    --skip-policy) SKIP_POLICY=1; shift ;;
    --skip-collector) SKIP_COLLECTOR=1; shift ;;
    --skip-workflows) SKIP_POLICY=1; shift ;; # backward-compat alias
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
# 1. Resolve credentials (macOS Keychain or environment)
# ------------------------------------------------------------------------------
if [[ -f "$REPO_ROOT/scripts/keychain.sh" ]]; then
  # shellcheck source=scripts/keychain.sh
  source "$REPO_ROOT/scripts/keychain.sh"
  keychain_load_env
fi

# Fallback: load .env only if present
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
CLOUD_API_KEY="${CLOUD_API_KEY:-${BILLING_API_KEY:-}}"
BILLING_API_KEY="${BILLING_API_KEY:-$CLOUD_API_KEY}"
PROJECT_ID="${PROJECT_ID:-${project_id:-}}"
SL_API_KEY="${SL_API_KEY:-${sl_api_key:-}}"
LOOKBEHIND="${LOOKBEHIND:-30}"

CONF_RATE="${CONF_RATE:-1.0}"
CONF_RATE_UNIT="${CONF_RATE_UNIT:-USD}"
CONF_CURRENCY_SYMBOL="${CONF_CURRENCY_SYMBOL:-$}"

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
  if type keychain_set >/dev/null 2>&1 && keychain_is_macos; then
    keychain_set "$var_name" "$input_val" >/dev/null 2>&1 || true
  fi
}

prompt_var "ES_URL" "Enter Hub Elasticsearch URL (e.g. https://<id>.es.<region>.<cloud>.elastic.cloud)"
prompt_var "KIBANA_URL" "Enter Hub Kibana URL (e.g. https://<id>.kb.<region>.<cloud>.elastic.cloud)"
prompt_var "ES_API_KEY" "Enter Hub Elasticsearch API Key" 1
prompt_var "KIBANA_API_KEY" "Enter Hub Kibana API Key" 1
prompt_var "ORGANIZATION_ID" "Enter Elastic Cloud Organization ID"
prompt_var "CLOUD_API_KEY" "Enter Elastic Cloud API Key (Billing & Project read access)" 1

export ES_URL KIBANA_URL ES_API_KEY KIBANA_API_KEY HUB_ES_API_KEY
export ORGANIZATION_ID CLOUD_API_KEY BILLING_API_KEY PROJECT_ID SL_API_KEY LOOKBEHIND
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
  echo ">>> Verifying Chargeback Serverless data streams..."
  local streams=(
    "metrics-chargeback_serverless.billing-default"
    "metrics-chargeback_serverless.usage-default"
  )

  printf "\n%-50s | %-12s\n" "Data Stream" "Doc Count"
  printf "%-50s-+-%-12s\n" "--------------------------------------------------" "------------"
  for ds in "${streams[@]}"; do
    local cnt
    cnt=$(es_curl -sS "${ES_URL%/}/${ds}/_count" -H "Authorization: ApiKey ${ES_API_KEY}" | jq -r '._shards.successful as $s | if $s > 0 then .count else "error" end' 2>/dev/null || echo "not yet created")
    printf "%-50s | %-12s\n" "$ds" "$cnt"
  done
  echo ""

  echo ">>> Recent Ingestion Summary (ES|QL):"
  local esql_body='{"query": "FROM metrics-chargeback_serverless.billing-* | STATS billing_records = count(), latest_bill = MAX(@timestamp) | KEEP billing_records, latest_bill"}'
  es_curl -sS "${ES_URL%/}/_query" \
    -H "Authorization: ApiKey ${ES_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$esql_body" 2>/dev/null | jq -r '
      if .values and (.values | length > 0) then
        "  • Billing records: " + (.values[0][0] | tostring) + " (latest: " + ((.values[0][1] // "none") | tostring) + ")"
      else
        "  • Billing: waiting for first scheduled collection run"
      end
    ' 2>/dev/null || true

  local esql_usage='{"query": "FROM metrics-chargeback_serverless.usage-* | STATS usage_records = count(), projects_monitored = COUNT_DISTINCT(project_name), latest_usage = MAX(@timestamp) | KEEP usage_records, projects_monitored, latest_usage"}'
  es_curl -sS "${ES_URL%/}/_query" \
    -H "Authorization: ApiKey ${ES_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$esql_usage" 2>/dev/null | jq -r '
      if .values and (.values | length > 0) then
        "  • Usage records:   " + (.values[0][0] | tostring) + " across " + (.values[0][1] | tostring) + " projects (latest: " + ((.values[0][2] // "none") | tostring) + ")"
      else
        "  • Usage: waiting for first scheduled collection run"
      end
    ' 2>/dev/null || true

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
if [[ -f "$PACKAGE_DIR/manifest.yml" ]]; then
  VERSION="$(grep -E '^version:' "$PACKAGE_DIR/manifest.yml" 2>/dev/null | awk '{print $2}' | tr -d '"' || echo "0.2.0")"
else
  TARGET_ZIP=$(ls -1t "$DIST_DIR"/chargeback_serverless-*.zip 2>/dev/null | head -1 || echo "$DIST_DIR/chargeback_serverless-0.2.0.zip")
  VERSION="$(basename "$TARGET_ZIP" | sed -E 's/chargeback_serverless-([0-9.]+)\.zip/\1/')"
fi
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
# 5. Call Kibana Fleet API to load the package
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
# 6. Configure Fleet Integration Policy
# ------------------------------------------------------------------------------
if [[ "$SKIP_POLICY" -eq 0 ]]; then
  echo ""
  echo ">>> Step 5: Configuring Fleet Integration Policy ($ZIP_NAME)..."

  # Find an agent policy or create one
  AGENT_POLICIES_HTTP=$(kbn_curl -o /tmp/cbs-agent-policies.json -w '%{http_code}' \
    -X GET "${KIBANA_URL%/}/api/fleet/agent_policies?perPage=50" \
    -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
    -H "kbn-xsrf: true" || true)

  AGENT_POLICY_ID=$(jq -r '(.items // .results // .)[0].id // empty' /tmp/cbs-agent-policies.json 2>/dev/null || true)

  if [[ -z "$AGENT_POLICY_ID" ]]; then
    echo "  No existing Agent Policy found in Fleet. Creating 'Fleet Serverless Policy'..."
    CREATE_AP_HTTP=$(kbn_curl -o /tmp/cbs-ap-create.json -w '%{http_code}' \
      -X POST "${KIBANA_URL%/}/api/fleet/agent_policies" \
      -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
      -H "kbn-xsrf: true" \
      -H "Content-Type: application/json" \
      -d '{"name": "Fleet Serverless Policy", "namespace": "default"}' || true)
    AGENT_POLICY_ID=$(jq -r '.item.id // .id // empty' /tmp/cbs-ap-create.json 2>/dev/null || true)
    if [[ -z "$AGENT_POLICY_ID" ]]; then
      echo "ERROR: Failed to find or create a Fleet Agent Policy (HTTP $CREATE_AP_HTTP)." >&2
      cat /tmp/cbs-ap-create.json >&2
      exit 1
    fi
  fi
  echo "  ✓ Target Fleet Agent Policy: $AGENT_POLICY_ID"

  # Check for existing package policy
  PKG_POLICIES_HTTP=$(kbn_curl -o /tmp/cbs-pkg-policies.json -w '%{http_code}' \
    -X GET "${KIBANA_URL%/}/api/fleet/package_policies?perPage=100" \
    -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
    -H "kbn-xsrf: true" || true)

  EXISTING_PKG_ID=$(jq -r --arg pkg "chargeback_serverless" '
    (.items // .results // .)
    | if type=="array" then . else [] end
    | map(select(.package.name == $pkg or .name == $pkg) | .id)
    | .[0] // empty
  ' /tmp/cbs-pkg-policies.json 2>/dev/null || true)

  export AGENT_POLICY_ID VERSION EXISTING_PKG_ID
  PROJECT_KEYS_ARR="[]"
  if [[ -n "${PROJECT_ID:-}" && -n "${SL_API_KEY:-}" ]]; then
    PROJECT_KEYS_ARR="[\"${PROJECT_ID}:${SL_API_KEY}\"]"
  fi
  export PROJECT_KEYS_ARR

  POLICY_PAYLOAD=$(python3 -c '
import json, os, sys

agent_policy_id = os.environ.get("AGENT_POLICY_ID", "")
version = os.environ.get("VERSION", "0.2.0")
org_id = os.environ.get("ORGANIZATION_ID", "")
cloud_api_key = os.environ.get("CLOUD_API_KEY", "") or os.environ.get("BILLING_API_KEY", "")
hub_es_api_key = os.environ.get("HUB_ES_API_KEY", "") or os.environ.get("ES_API_KEY", "")
lookbehind = int(os.environ.get("LOOKBEHIND", "30") or 30)
currency_unit = os.environ.get("CONF_RATE_UNIT", "USD") or "USD"
currency_symbol = os.environ.get("CONF_CURRENCY_SYMBOL", "$") or "$"
unit_rate = os.environ.get("CONF_RATE", "1.0") or "1.0"
billing_api_base = os.environ.get("BILLING_API_BASE", "https://billing.elastic-cloud.com")
cloud_api_base = os.environ.get("CLOUD_API_BASE", "https://api.elastic-cloud.com")

proj_keys_raw = os.environ.get("PROJECT_KEYS_ARR", "[]")
try:
    proj_keys = json.loads(proj_keys_raw)
except Exception:
    proj_keys = []

existing_json_file = "/tmp/cbs-pkg-policies.json"
existing_pkg_id = os.environ.get("EXISTING_PKG_ID", "")
if existing_pkg_id and os.path.exists(existing_json_file):
    try:
        with open(existing_json_file) as f:
            data = json.load(f)
        items = data.get("items") or data.get("results") or (data if isinstance(data, list) else [])
        for it in items:
            if it.get("id") == existing_pkg_id:
                old_inputs = it.get("inputs", [])
                if isinstance(old_inputs, list):
                    for inp in old_inputs:
                        old_keys = inp.get("vars", {}).get("project_api_keys", {}).get("value")
                        if old_keys and not proj_keys:
                            proj_keys = old_keys if isinstance(old_keys, list) else [old_keys]
    except Exception:
        pass

payload = {
    "name": "chargeback_serverless",
    "description": "Chargeback Serverless scheduled billing and usage collection via Elastic Agent CEL",
    "namespace": "default",
    "policy_id": agent_policy_id,
    "enabled": True,
    "package": {
        "name": "chargeback_serverless",
        "version": version
    },
    "inputs": [
        {
            "type": "cel",
            "enabled": True,
            "vars": {
                "organization_id": { "value": str(org_id), "type": "text" },
                "cloud_api_key": { "value": str(cloud_api_key), "type": "password" },
                "hub_es_api_key": { "value": str(hub_es_api_key), "type": "password" },
                "project_api_keys": { "value": proj_keys, "type": "password" },
                "lookbehind": { "value": lookbehind, "type": "integer" },
                "currency_unit": { "value": str(currency_unit), "type": "text" },
                "currency_symbol": { "value": str(currency_symbol), "type": "text" },
                "unit_rate": { "value": str(unit_rate), "type": "text" },
                "billing_api_base": { "value": str(billing_api_base), "type": "text" },
                "cloud_api_base": { "value": str(cloud_api_base), "type": "text" }
            },
            "streams": [
                {
                    "data_stream": { "type": "metrics", "dataset": "chargeback_serverless.billing" },
                    "enabled": True
                },
                {
                    "data_stream": { "type": "metrics", "dataset": "chargeback_serverless.usage" },
                    "enabled": True
                }
            ]
        }
    ]
}

print(json.dumps(payload))
')

  if [[ -n "$EXISTING_PKG_ID" ]]; then
    echo "  Updating existing package policy '$EXISTING_PKG_ID'..."
    PKG_SAVE_HTTP=$(kbn_curl -o /tmp/cbs-pkg-save.json -w '%{http_code}' \
      -X PUT "${KIBANA_URL%/}/api/fleet/package_policies/${EXISTING_PKG_ID}" \
      -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
      -H "kbn-xsrf: true" \
      -H "Content-Type: application/json" \
      -d "$POLICY_PAYLOAD" || true)
  else
    echo "  Creating new package policy 'chargeback_serverless'..."
    PKG_SAVE_HTTP=$(kbn_curl -o /tmp/cbs-pkg-save.json -w '%{http_code}' \
      -X POST "${KIBANA_URL%/}/api/fleet/package_policies" \
      -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
      -H "kbn-xsrf: true" \
      -H "Content-Type: application/json" \
      -d "$POLICY_PAYLOAD" || true)
  fi

  if [[ "$PKG_SAVE_HTTP" != "200" && "$PKG_SAVE_HTTP" != "201" ]]; then
    echo "ERROR: Failed to save Fleet package policy (HTTP $PKG_SAVE_HTTP):" >&2
    cat /tmp/cbs-pkg-save.json >&2
    echo ""
    exit 1
  fi
  echo "  ✓ Fleet package policy active. Elastic Agent CEL streams scheduled."
else
  echo ""
  echo ">>> Step 5: Skipped policy configuration (--skip-policy)"
fi

# ------------------------------------------------------------------------------
# 6. Execute One-Shot CEL Collection Pass (Immediate Dashboard Verification)
# ------------------------------------------------------------------------------
if [[ "$SKIP_COLLECTOR" -eq 0 ]]; then
  echo ""
  echo ">>> Step 6: Running local CEL collection pass for immediate dashboard data..."
  python3 "$REPO_ROOT/scripts/run_chargeback_serverless_collector.py" || {
    echo "  ⚠ Local collector pass encountered a warning/error (continuing to verification)."
  }
else
  echo ""
  echo ">>> Step 6: Skipped local collector pass (--skip-collector)"
fi

# ------------------------------------------------------------------------------
# 7. Post-deployment Verification & Summary
# ------------------------------------------------------------------------------
run_verification

echo "================================================================="
echo "                Deployment Completed Successfully!               "
echo "================================================================="
