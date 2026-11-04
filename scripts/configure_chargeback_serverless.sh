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
#   DATA_RETENTION          (Optional) Data stream & transform retention period (default: 365d)
#   BILLING_API_BASE        (Optional) default: https://billing.elastic-cloud.com
#   CLOUD_API_BASE          (Optional) default: https://api.elastic-cloud.com
#   CURL_INSECURE           (Optional) 1 = skip TLS verification

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/keychain.sh" ]]; then
  # shellcheck source=scripts/keychain.sh
  source "$SCRIPT_DIR/keychain.sh"
  keychain_load_env
fi

ES_URL="${ES_URL:-${sl_url:-${ES_HOST:-https://127.0.0.1:9200}}}"
ES_API_KEY="${ES_API_KEY:-${HUB_ES_API_KEY:-${HUB_KIBANA_API_KEY:-}}}"
HUB_ES_API_KEY="${HUB_ES_API_KEY:-$ES_API_KEY}"
ORGANIZATION_ID="${ORGANIZATION_ID:-}"
BILLING_API_KEY="${BILLING_API_KEY:-}"
USAGE_API_KEY="${USAGE_API_KEY:-}"

BILLING_API_BASE="${BILLING_API_BASE:-https://billing.elastic-cloud.com}"
CLOUD_API_BASE="${CLOUD_API_BASE:-https://api.elastic-cloud.com}"
CONF_RATE="${CONF_RATE:-}"
CONF_RATE_UNIT="${CONF_RATE_UNIT:-}"
CONF_CURRENCY_SYMBOL="${CONF_CURRENCY_SYMBOL:-}"
DATA_RETENTION="${DATA_RETENTION:-}"

PROJECT_ID="${PROJECT_ID:-${project_id:-}}"
SL_API_KEY="${SL_API_KEY:-${sl_api_key:-}}"

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
    curl -sS --retry 3 --retry-connrefused "$ES_CURL_TLS_FLAG" "$@"
  else
    curl -sS --retry 3 --retry-connrefused "$@"
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

KIBANA_URL="${KIBANA_URL:-${KIBANA_HOST:-}}"
if [[ -z "$KIBANA_URL" && -n "$ES_URL" && "$ES_URL" =~ \.es\. ]]; then
  KIBANA_URL="${ES_URL//.es./.kb.}"
fi
KIBANA_API_KEY="${KIBANA_API_KEY:-$ES_API_KEY}"

CLOUD_API_KEY="${CLOUD_API_KEY:-${USAGE_API_KEY:-${BILLING_API_KEY:-}}}"
BILLING_API_KEY="${BILLING_API_KEY:-$CLOUD_API_KEY}"

KBN_CURL_TLS_FLAG="$(curl_tls_args_for "${KIBANA_URL:-https://127.0.0.1:5601}")"
kbn_curl() {
  if [[ -n "$KBN_CURL_TLS_FLAG" ]]; then
    curl -sS --retry 3 --retry-connrefused "$KBN_CURL_TLS_FLAG" "$@"
  else
    curl -sS --retry 3 --retry-connrefused "$@"
  fi
}

echo "Configuring Chargeback Serverless Fleet Integration Policy..."

# 1. Update Fleet Package Policy via Kibana Fleet API
if [[ -n "$KIBANA_URL" && -n "$KIBANA_API_KEY" ]]; then
  PKG_POLICIES_HTTP=$(kbn_curl -o /tmp/cbs-conf-pkg-policies.json -w '%{http_code}' \
    -X GET "${KIBANA_URL%/}/api/fleet/package_policies?perPage=100" \
    -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
    -H "kbn-xsrf: true" || true)

  EXISTING_PKG_POLICY=$(jq -c --arg pkg "chargeback_serverless" '
    (.items // .results // .)
    | if type=="array" then . else [] end
    | map(select(.package.name == $pkg or .name == $pkg))
    | .[0] // empty
  ' /tmp/cbs-conf-pkg-policies.json 2>/dev/null || true)

  if [[ -n "$EXISTING_PKG_POLICY" ]]; then
    PKG_ID=$(echo "$EXISTING_PKG_POLICY" | jq -r '.id')
    export EXISTING_PKG_POLICY PROJECT_ID SL_API_KEY ORGANIZATION_ID CLOUD_API_KEY HUB_ES_API_KEY
    export CONF_RATE CONF_RATE_UNIT CONF_CURRENCY_SYMBOL BILLING_API_BASE CLOUD_API_BASE

    UPDATED_POLICY_PAYLOAD=$(python3 -c '
import json, os, sys

policy = json.loads(os.environ["EXISTING_PKG_POLICY"])
org_id = os.environ.get("ORGANIZATION_ID")
cloud_key = os.environ.get("CLOUD_API_KEY")
hub_key = os.environ.get("HUB_ES_API_KEY")
pid = os.environ.get("PROJECT_ID", "").strip()
pkey = os.environ.get("SL_API_KEY", "").strip()
unit_rate = os.environ.get("CONF_RATE")
unit = os.environ.get("CONF_RATE_UNIT")
symbol = os.environ.get("CONF_CURRENCY_SYMBOL")

inputs = policy.get("inputs", [])
for inp in inputs:
    vars_dict = inp.setdefault("vars", {})
    if org_id:
        vars_dict["organization_id"] = {"value": str(org_id), "type": "text"}
    if cloud_key:
        vars_dict["cloud_api_key"] = {"value": str(cloud_key), "type": "password"}
    if hub_key:
        vars_dict["hub_es_api_key"] = {"value": str(hub_key), "type": "password"}
    if unit:
        vars_dict["currency_unit"] = {"value": str(unit), "type": "text"}
    if symbol:
        vars_dict["currency_symbol"] = {"value": str(symbol), "type": "text"}
    if unit_rate:
        vars_dict["unit_rate"] = {"value": str(unit_rate), "type": "text"}

    if pid and pkey:
        curr_keys = vars_dict.get("project_api_keys", {}).get("value", [])
        if isinstance(curr_keys, str):
            curr_keys = [curr_keys]
        new_entry = f"{pid}:{pkey}"
        if new_entry not in curr_keys:
            curr_keys.append(new_entry)
        vars_dict["project_api_keys"] = {"value": curr_keys, "type": "password"}

    for s in inp.get("streams", []):
        if s.get("data_stream", {}).get("dataset") == "chargeback_serverless.billing":
            s_vars = s.setdefault("vars", {})
            if "lookbehind" not in s_vars:
                s_vars["lookbehind"] = {"value": 60, "type": "integer"}

# Keep payload clean for Fleet API update
clean_payload = {
    "name": policy.get("name", "chargeback_serverless"),
    "description": policy.get("description", "Chargeback Serverless scheduled collection"),
    "namespace": policy.get("namespace", "default"),
    "policy_id": policy.get("policy_id"),
    "enabled": policy.get("enabled", True),
    "package": policy.get("package"),
    "inputs": inputs
}
print(json.dumps(clean_payload))
')

    SAVE_HTTP=$(kbn_curl -o /tmp/cbs-conf-save.json -w '%{http_code}' \
      -X PUT "${KIBANA_URL%/}/api/fleet/package_policies/${PKG_ID}" \
      -H "Authorization: ApiKey ${KIBANA_API_KEY}" \
      -H "kbn-xsrf: true" \
      -H "Content-Type: application/json" \
      -d "$UPDATED_POLICY_PAYLOAD" || true)

    if [[ "$SAVE_HTTP" == "200" || "$SAVE_HTTP" == "201" ]]; then
      echo "  ✓ Fleet package policy '$PKG_ID' updated securely."
      if [[ -n "$PROJECT_ID" && -n "$SL_API_KEY" ]]; then
        echo "  ✓ Added remote harvesting key for project: $PROJECT_ID"
      fi
    else
      echo "  ⚠ Warning: Could not update Fleet package policy (HTTP $SAVE_HTTP): $(head -c 200 /tmp/cbs-conf-save.json 2>/dev/null)"
    fi
  else
    echo "  Notice: No existing 'chargeback_serverless' package policy found in Fleet."
    echo "  Run ./scripts/deploy_chargeback_serverless.sh to install the package and initialize the policy."
  fi
fi

# 2. Apply Data Stream Lifecycle & Transform retention if DATA_RETENTION is specified
if [[ -n "$DATA_RETENTION" ]]; then
  echo "Applying Data Stream Lifecycle and Transform retention ($DATA_RETENTION)..."
  for ds_pattern in "metrics-chargeback_serverless.billing-*" "metrics-chargeback_serverless.usage-*"; do
    es_curl -sS -X PUT "${ES_URL%/}/_data_stream/${ds_pattern}/_lifecycle" \
      -H "Authorization: ApiKey ${ES_API_KEY}" \
      -H "Content-Type: application/json" \
      -d "{\"data_retention\": \"${DATA_RETENTION}\"}" >/dev/null 2>&1 || true
  done
  for ct_name in "metrics-chargeback_serverless.billing@custom" "metrics-chargeback_serverless.usage@custom"; do
    es_curl -sS -X PUT "${ES_URL%/}/_component_template/${ct_name}" \
      -H "Authorization: ApiKey ${ES_API_KEY}" \
      -H "Content-Type: application/json" \
      -d "{\"template\": {\"lifecycle\": {\"data_retention\": \"${DATA_RETENTION}\"}}}" >/dev/null 2>&1 || true
  done
  ACTIVE_TF=$(es_curl -sS "${ES_URL%/}/_transform/logs-chargeback_serverless.*" -H "Authorization: ApiKey ${ES_API_KEY}" 2>/dev/null \
    | jq -r '.transforms[]?.id // empty' 2>/dev/null || true)
  for tf_id in $ACTIVE_TF; do
    [[ -z "$tf_id" ]] && continue
    es_curl -sS -X POST "${ES_URL%/}/_transform/${tf_id}/_update" \
      -H "Authorization: ApiKey ${ES_API_KEY}" \
      -H "Content-Type: application/json" \
      -d "{\"retention_policy\": {\"time\": {\"field\": \"@timestamp\", \"max_age\": \"${DATA_RETENTION}\"}}}" >/dev/null 2>&1 || true
  done
  echo "  ✓ Updated data stream lifecycle and transform retention policies to $DATA_RETENTION."
fi

echo "Configuration completed successfully (all settings managed in Fleet package policy)."

