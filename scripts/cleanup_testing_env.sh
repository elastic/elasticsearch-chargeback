#!/usr/bin/env bash
# Clean up testing environment: uninstall Chargeback, On-Prem Billing, Elasticsearch;
# remove Fleet package policy; delete lookup/monitoring indices; re-enable legacy monitoring.
# Run from elasticsearch-chargeback repo root; integrations repo must be sibling or set INTEGRATIONS_REPO.
# Tested with: Elasticsearch/Kibana 9.2.2, Chargeback integration 0.3.1.
set -e

# Versions this cleanup is intended for (for documentation and optional checks)
STACK_VERSION="${STACK_VERSION:-9.2.2}"
CHARGEBACK_VERSION="${CHARGEBACK_VERSION:-0.3.1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHARGEBACK_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
INTEGRATIONS_REPO="${INTEGRATIONS_REPO:-$(cd "$CHARGEBACK_REPO/../integrations" 2>/dev/null && pwd)}"
KIBANA_HOST="${KIBANA_HOST:-https://127.0.0.1:5601}"
ES_HOST="${ES_HOST:-https://127.0.0.1:9200}"
USER="${ELASTIC_USER:-elastic}"
PASS="${ELASTIC_PASSWORD:-changeme}"
curl_es() { curl -sS -k -u "$USER:$PASS" -H "Content-Type: application/json" "$@"; }
curl_kibana() { curl -sS -k -u "$USER:$PASS" -H "Content-Type: application/json" -H "kbn-xsrf: true" "$@"; }

if [[ ! -d "$INTEGRATIONS_REPO" ]]; then
  echo "INTEGRATIONS_REPO not found at $INTEGRATIONS_REPO. Set INTEGRATIONS_REPO to the path of the integrations repo."
  exit 1
fi

# Prefer installed elastic-package (e.g. v0.120.0) to avoid version warnings from go run
if command -v elastic-package >/dev/null 2>&1; then
  EP_CMD="elastic-package"
else
  EP_CMD="go run github.com/elastic/elastic-package"
fi
echo "Cleaning up testing env (stack $STACK_VERSION, chargeback $CHARGEBACK_VERSION). Using: $EP_CMD  Kibana: $KIBANA_HOST  ES: $ES_HOST"

# 1. Uninstall Chargeback
echo "--- 1. Uninstall Chargeback ---"
(cd "$INTEGRATIONS_REPO/packages/chargeback" && $EP_CMD uninstall 2>/dev/null) || echo "Chargeback uninstall skipped or failed."

# 2. Uninstall On-Prem Billing (remove package policies first, then package)
echo "--- 2. Uninstall On-Prem Billing ---"
# 2a. Delete any On-Prem Billing package policies (required before the package can be removed from Installed)
POLICIES_JSON=$(curl_kibana -sS "$KIBANA_HOST/api/fleet/package_policies?perPage=200" 2>/dev/null)
ONPREM_POLICY_IDS=""
if command -v jq >/dev/null 2>&1; then
  ONPREM_POLICY_IDS=$(echo "$POLICIES_JSON" | jq -r '.items[]? | select(.package.name == "onprem_billing") | .id' 2>/dev/null)
fi
for PID in $ONPREM_POLICY_IDS; do
  [[ -z "$PID" ]] && continue
  echo "  Delete On-Prem Billing package policy $PID"
  curl_kibana -sS -X DELETE "$KIBANA_HOST/api/fleet/package_policies/$PID" 2>/dev/null || true
done
if [[ -d "$INTEGRATIONS_REPO/packages/onprem_billing" ]]; then
  (cd "$INTEGRATIONS_REPO/packages/onprem_billing" && $EP_CMD uninstall 2>/dev/null) && echo "  On-Prem Billing uninstalled (elastic-package)." || echo "  On-Prem Billing elastic-package uninstall skipped or failed."
fi
# 2b. Remove the package from Fleet (GET version from installed list, then DELETE with force)
INSTALLED_JSON=$(curl_kibana -sS "$KIBANA_HOST/api/fleet/epm/packages/installed?perPage=200&nameQuery=onprem" 2>/dev/null)
ONPREM_VERSION=""
ONPREM_NAME=""
if command -v jq >/dev/null 2>&1; then
  ONPREM_VERSION=$(echo "$INSTALLED_JSON" | jq -r '.items[]? | select(.name == "onprem_billing" or .title == "On-Premises Billing") | .version' 2>/dev/null | head -1)
  ONPREM_NAME=$(echo "$INSTALLED_JSON" | jq -r '.items[]? | select(.name == "onprem_billing" or .title == "On-Premises Billing") | .name' 2>/dev/null | head -1)
fi
if [[ -z "$ONPREM_VERSION" ]]; then
  if echo "$INSTALLED_JSON" | grep -q '"name":"onprem_billing"'; then
    ONPREM_VERSION=$(echo "$INSTALLED_JSON" | grep -oE '"name":"onprem_billing"[^}]*"version":"[^"]+"' | grep -oE '"version":"[^"]+"' | head -1 | sed 's/"version":"//;s/"//')
    ONPREM_NAME="onprem_billing"
  fi
fi
[[ -z "$ONPREM_NAME" ]] && ONPREM_NAME="onprem_billing"
if [[ -n "$ONPREM_VERSION" ]]; then
  resp=$(curl_kibana -sS -X DELETE "$KIBANA_HOST/api/fleet/epm/packages/$ONPREM_NAME/$ONPREM_VERSION?force=true" 2>/dev/null)
  if echo "$resp" | grep -q '"items"'; then
    echo "On-Prem Billing ($ONPREM_NAME@$ONPREM_VERSION) removed via Fleet API."
  else
    echo "On-Prem Billing DELETE response: $resp"
  fi
else
  echo "On-Prem Billing not in installed packages list (skip Fleet API delete)."
fi

# 2b. Remove On-Prem enrich policies and calculate_cost pipeline (created by e2e script; wip/onprem-billing-integration uses both)
echo "--- 2b. Remove On-Prem enrich policies and pipeline ---"
curl_es -X DELETE "$ES_HOST/_enrich/policy/onprem_billing_config_enrich_policy" >/dev/null 2>&1 && echo "Deleted enrich policy onprem_billing_config_enrich_policy." || echo "Enrich policy skip or already gone."
curl_es -X DELETE "$ES_HOST/_enrich/policy/onprem_billing_org_config_policy" >/dev/null 2>&1 && echo "Deleted enrich policy onprem_billing_org_config_policy." || echo "Org enrich policy skip or already gone."
curl_es -X DELETE "$ES_HOST/_ingest/pipeline/calculate_cost" >/dev/null 2>&1 && echo "Deleted pipeline calculate_cost." || echo "Pipeline skip or already gone."

# 3. Uninstall Elasticsearch integration
echo "--- 3. Uninstall Elasticsearch integration ---"
(cd "$INTEGRATIONS_REPO/packages/elasticsearch" && $EP_CMD uninstall 2>/dev/null) || echo "Elasticsearch uninstall skipped or failed."

# 4. Remove Fleet package policy (elasticsearch-monitoring)
echo "--- 4. Remove Fleet package policy elasticsearch-monitoring ---"
POLICY_IDS=$(curl_kibana "$KIBANA_HOST/api/fleet/package_policies?perPage=200" 2>/dev/null | grep -oE '"id":"[a-f0-9-]+"[^}]*"name":"elasticsearch-monitoring"' | grep -oE '"id":"[a-f0-9-]+"' | sed 's/"id":"//;s/"//')
for PID in $POLICY_IDS; do
  echo "Delete package policy $PID"
  curl_kibana -X DELETE "$KIBANA_HOST/api/fleet/package_policies/$PID" 2>/dev/null || true
done
if [[ -z "$POLICY_IDS" ]]; then
  echo "No elasticsearch-monitoring package policy found."
fi

# 5. Delete indices created by testing
echo "--- 5. Delete testing indices ---"
for idx in billing_cluster_cost_lookup chargeback_conf_lookup cluster_datastream_contribution_lookup cluster_deployment_contribution_lookup cluster_tier_and_datastream_contribution_lookup cluster_tier_contribution_lookup monitoring-indices onprem_billing_config; do
  resp=$(curl_es -sS -X DELETE "$ES_HOST/$idx" 2>/dev/null)
  if echo "$resp" | grep -q '"acknowledged":true'; then
    echo "Deleted $idx"
  elif echo "$resp" | grep -q 'index_not_found_exception'; then
    echo "Already gone: $idx"
  else
    echo "Skip $idx"
  fi
done

# 6. Re-enable legacy monitoring (optional)
echo "--- 6. Re-enable legacy monitoring ---"
resp=$(curl_es -sS -X PUT "$ES_HOST/_cluster/settings" -d '{"persistent":{"xpack.monitoring.collection.enabled":true}}' 2>/dev/null)
echo "$resp" | grep -q '"acknowledged":true' && echo "Legacy monitoring re-enabled." || true

echo "--- Cleanup done. ---"
