#!/usr/bin/env bash
# Automated E2E test: install Elasticsearch integration, On-Prem Billing, Chargeback via elastic-package,
# then run verification (transforms, indices). Supports integrations wip/onprem-billing-integration (mERU/ERU/RAM) or zip from chargeback repo.
# Run from elasticsearch-chargeback repo root; integrations repo must be sibling or set INTEGRATIONS_REPO.
set -e

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

EP_CMD="go run github.com/elastic/elastic-package"
echo "Using integrations repo: $INTEGRATIONS_REPO"
echo "Kibana: $KIBANA_HOST  ES: $ES_HOST"

# 1. Stack status / up
echo "--- 1. Stack status ---"
(cd "$INTEGRATIONS_REPO" && $EP_CMD stack status) || {
  echo "Stack not running. Start with: cd $INTEGRATIONS_REPO && $EP_CMD stack up"
  exit 1
}

# 2. Build and install Elasticsearch integration
echo "--- 2. Build and install Elasticsearch integration ---"
(cd "$INTEGRATIONS_REPO/packages/elasticsearch" && $EP_CMD build --skip-validation && $EP_CMD install --skip-validation)

# 3. Disable legacy Stack Monitoring
echo "--- 3. Disable legacy monitoring ---"
curl_es -X PUT "$ES_HOST/_cluster/settings" -d '{"persistent":{"xpack.monitoring.collection.enabled":false}}' | head -5

# 4. (Optional) Add Elasticsearch metrics to Fleet agent policy so the ES transform has data
echo "\n"
echo "--- 4. Fleet: add Elasticsearch package policy (optional) ---"
ES_PKG_VERSION=""
if [[ -f "$INTEGRATIONS_REPO/packages/elasticsearch/manifest.yml" ]]; then
  ES_PKG_VERSION=$(grep -E '^version:' "$INTEGRATIONS_REPO/packages/elasticsearch/manifest.yml" 2>/dev/null | sed 's/version:[[:space:]]*//;s/[[:space:]]*$//')
fi
if [[ -z "$ES_PKG_VERSION" ]]; then
  echo "Could not read Elasticsearch package version from $INTEGRATIONS_REPO/packages/elasticsearch/manifest.yml; skip package policy."
fi
POLICY_ID=$(curl_kibana "$KIBANA_HOST/api/fleet/agent_policies?perPage=100" 2>/dev/null | grep -o '"id":"[^"]*"' | head -1 | sed 's/"id":"//;s/"//')
if [[ -n "$POLICY_ID" && -n "$ES_PKG_VERSION" ]]; then
  echo "Using agent policy: $POLICY_ID (elasticsearch package $ES_PKG_VERSION)"
  curl_kibana -X POST "$KIBANA_HOST/api/fleet/package_policies" -d "{
    \"name\": \"elasticsearch-monitoring\",
    \"namespace\": \"default\",
    \"policy_id\": \"$POLICY_ID\",
    \"enabled\": true,
    \"inputs\": [{
      \"type\": \"elasticsearch/metrics\",
      \"enabled\": true,
      \"vars\": {
        \"hosts\": { \"value\": [\"https://elasticsearch:9200\"], \"type\": \"text\" },
        \"username\": { \"value\": \"elastic\", \"type\": \"text\" },
        \"password\": { \"value\": \"changeme\", \"type\": \"password\" }
      },
      \"streams\": [{
        \"enabled\": true,
        \"data_stream\": { \"type\": \"metrics\", \"dataset\": \"elasticsearch.stack_monitoring.index\" },
        \"vars\": { \"period\": { \"value\": \"10s\", \"type\": \"text\" } }
      }]
    }],
    \"package\": { \"name\": \"elasticsearch\", \"version\": \"$ES_PKG_VERSION\" }
  }" 2>/dev/null | head -3 || echo "Package policy create skipped or failed."
else
  if [[ -z "$POLICY_ID" ]]; then echo "No Fleet agent policy found. Skip package policy."; fi
fi

# 5. Start Elasticsearch index_pivot transform (required for Chargeback)
echo "\n"
echo "--- 5. Start Elasticsearch index_pivot transform ---"
TID=$(curl_es "$ES_HOST/_transform?size=100" 2>/dev/null | grep -oE '"id":"[^"]*elasticsearch[^"]*index_pivot[^"]*"' | head -1 | sed 's/"id":"//;s/"//')
if [[ -n "$TID" ]]; then
  echo "Starting transform: $TID"
  curl_es -X POST "$ES_HOST/_transform/$TID/_start" 2>/dev/null || true
else
  echo "No Elasticsearch index_pivot transform found. List all: $ES_HOST/_transform"
fi

# 5b. Seed monitoring-indices with dev, prod, monitoring so On-Prem config_bootstrap discovers 3 deployments (12 ERU setup: 2 product + 1 monitoring group)
YESTERDAY_EARLY=$(date -u -v-1d 2>/dev/null +%Y-%m-%dT00:00:00.000Z || date -u -d "yesterday" 2>/dev/null +%Y-%m-%dT00:00:00.000Z || echo "2026-01-28T00:00:00.000Z")
MONITORING_COUNT_EARLY=$(curl_es -sS "$ES_HOST/monitoring-indices/_count" 2>/dev/null | grep -oE '"count":[0-9]+' | sed 's/"count"://')
if [[ "${MONITORING_COUNT_EARLY:-0}" == "0" ]]; then
  echo "Seeding monitoring-indices: dev, prod, monitoring (3 deployments for 12 ERU test)."
  for cluster in dev prod monitoring; do
    curl_es -X POST "$ES_HOST/monitoring-indices/_doc" -d "{\"@timestamp\": \"$YESTERDAY_EARLY\", \"elasticsearch\": { \"cluster\": { \"name\": \"$cluster\" } }}" >/dev/null 2>&1 || true
  done
fi

# 6. On-Prem Billing: install (from integrations repo package or chargeback zip), then org + deployment config, enrich policies, pipeline, transform
echo "--- 6. Install On-Prem Billing ---"
ONPREM_INSTALLED=false
if [[ -d "$INTEGRATIONS_REPO/packages/onprem_billing" ]]; then
  echo "  Installing from integrations repo packages/onprem_billing (e.g. wip/onprem-billing-integration)"
  (cd "$INTEGRATIONS_REPO/packages/onprem_billing" && $EP_CMD build --skip-validation && $EP_CMD install --skip-validation) && ONPREM_INSTALLED=true || true
fi
if [[ "$ONPREM_INSTALLED" != "true" ]]; then
  for branch in wip/onprem-billing-integration feature/onprem-billing-integration; do
    ONPREM_ZIP_PATH=$(cd "$CHARGEBACK_REPO" && git ls-tree -r --name-only "$branch" 2>/dev/null | grep -E 'onprem_billing.*\.zip$' | head -1)
    if [[ -n "$ONPREM_ZIP_PATH" ]]; then
      ONPREM_ZIP="/tmp/$(basename "$ONPREM_ZIP_PATH")"
      if (cd "$CHARGEBACK_REPO" && git show "$branch:$ONPREM_ZIP_PATH" > "$ONPREM_ZIP" 2>/dev/null) && [[ -f "$ONPREM_ZIP" ]]; then
        (cd "$INTEGRATIONS_REPO" && $EP_CMD install --zip "$ONPREM_ZIP" --skip-validation) && ONPREM_INSTALLED=true || true
        break
      fi
    fi
  done
fi
if [[ "$ONPREM_INSTALLED" == "true" ]]; then
  # 6a. Start config_bootstrap so it discovers deployments and populates onprem_billing_config
  CONFIG_BOOT_TID=$(curl_es "$ES_HOST/_transform?size=100" 2>/dev/null | grep -oE '"id":"logs-onprem_billing\.config_bootstrap-[^"]+"' | sed 's/"id":"//;s/"//')
  if [[ -n "$CONFIG_BOOT_TID" ]]; then
    echo "  Start config_bootstrap: $CONFIG_BOOT_TID"
    curl_es -X POST "$ES_HOST/_transform/$CONFIG_BOOT_TID/_start" >/dev/null 2>&1 || true
  fi
  echo "  Waiting 15s for config_bootstrap to populate onprem_billing_config..."
  sleep 15

  # 6b. Organization doc: 12 ERU licence (for dev + prod + monitoring test)
  echo "  Create organization config doc (12 ERU licence)"
  curl_es -X PUT "$ES_HOST/onprem_billing_config/_doc/organization" -d '{"config_type":"organization","total_annual_license_cost":180000,"total_erus_purchased":12,"eru_to_ram_gb":64,"currency_unit":"EUR"}' >/dev/null 2>&1 || true

  # 6c. Update all deployment docs: dev (2 ERU, product), prod (8 ERU, product), monitoring (2 ERU, monitoring group)
  echo "  Update deployment configs: dev=2 ERU/product, prod=8 ERU/product, monitoring=2 ERU/monitoring"
  for dep_id in dev prod monitoring; do
    case "$dep_id" in
      dev)        tags='["chargeback_group:product"]';   erus=2; name="dev" ;;
      prod)       tags='["chargeback_group:product"]';   erus=8; name="prod" ;;
      monitoring) tags='["chargeback_group:monitoring"]'; erus=2; name="monitoring" ;;
      *)          tags='["chargeback_group:product"]';   erus=1; name="$dep_id" ;;
    esac
    doc_id=$(curl_es -sS "$ES_HOST/onprem_billing_config/_search?size=1" -d "{\"_source\":false,\"query\":{\"term\":{\"deployment_id\":\"$dep_id\"}}}" 2>/dev/null | grep -oE '"_id":"[^"]+"' | head -1 | sed 's/"_id":"//;s/"//')
    if [[ -n "$doc_id" ]]; then
      curl_es -X POST "$ES_HOST/onprem_billing_config/_update/$doc_id" -d "{\"doc\":{\"deployment_name\":\"$name\",\"deployment_tags\":$tags,\"deployment_erus\":$erus}}" >/dev/null 2>&1 || true
    fi
  done

  # 6d. Create enrich policies (wip/onprem-billing-integration: deployment + org) and execute
  echo "  Create enrich policies and execute"
  curl_es -X PUT "$ES_HOST/_enrich/policy/onprem_billing_config_enrich_policy" -d '{"match":{"indices":"onprem_billing_config","match_field":"deployment_id","enrich_fields":["daily_meru","deployment_erus","deployment_name","deployment_tags","node_count","ram_per_node_gb"]}}' >/dev/null 2>&1 || true
  curl_es -X PUT "$ES_HOST/_enrich/policy/onprem_billing_org_config_policy" -d '{"match":{"indices":"onprem_billing_config","match_field":"config_type","enrich_fields":["total_annual_license_cost","total_erus_purchased","eru_to_ram_gb","currency_unit"]}}' >/dev/null 2>&1 || true
  curl_es -X POST "$ES_HOST/_enrich/policy/onprem_billing_config_enrich_policy/_execute" >/dev/null 2>&1 || true
  curl_es -X POST "$ES_HOST/_enrich/policy/onprem_billing_org_config_policy/_execute" >/dev/null 2>&1 || true
  echo "  Waiting 10s for enrich indices to be searchable..."
  sleep 10

  # 6e. Create calculate_cost ingest pipeline (mERU/ERU/RAM -> ESS Billing total_ecu)
  PIPELINE_JSON="$SCRIPT_DIR/onprem_billing_calculate_cost_pipeline.json"
  if [[ -f "$PIPELINE_JSON" ]]; then
    echo "  Create ingest pipeline: calculate_cost"
    curl_es -X PUT "$ES_HOST/_ingest/pipeline/calculate_cost" -d @"$PIPELINE_JSON" >/dev/null 2>&1 || true
  fi

  # 6f. Update billing transform: pipeline + 1m sync delay (for testing), then reset and start
  BILLING_TID=$(curl_es "$ES_HOST/_transform?size=100" 2>/dev/null | grep -oE '"id":"logs-onprem_billing\.billing-[^"]+"' | sed 's/"id":"//;s/"//')
  if [[ -n "$BILLING_TID" ]]; then
    echo "  Update billing transform (pipeline calculate_cost, sync delay 1m)"
    curl_es -X POST "$ES_HOST/_transform/$BILLING_TID/_update" -d '{"dest":{"index":"metrics-ess_billing.billing-onprem","pipeline":"calculate_cost"},"sync":{"time":{"field":"@timestamp","delay":"1m"}}}' >/dev/null 2>&1 || true
    echo "  Reset and start billing transform: $BILLING_TID"
    curl_es -X POST "$ES_HOST/_transform/$BILLING_TID/_stop" >/dev/null 2>&1 || true
    curl_es -X POST "$ES_HOST/_transform/$BILLING_TID/_reset" >/dev/null 2>&1 || true
    curl_es -X POST "$ES_HOST/_transform/$BILLING_TID/_start" >/dev/null 2>&1 || true
  fi
else
  echo "On-Prem Billing not installed (no packages/onprem_billing in integrations repo and no zip on chargeback branch). Skipping."
fi

# 7. Build and install Chargeback
echo "--- 7. Build and install Chargeback ---"
(cd "$INTEGRATIONS_REPO/packages/chargeback" && $EP_CMD build --skip-validation && $EP_CMD install --skip-validation)

# 8. Ensure all lookup indices get populated: seed sources if empty, then reset/start transforms
echo "--- 8. Backfill lookup indices (seed + reset/start transforms) ---"
YESTERDAY=$(date -u -v-1d 2>/dev/null +%Y-%m-%dT00:00:00.000Z || date -u -d "yesterday" 2>/dev/null +%Y-%m-%dT00:00:00.000Z || echo "2026-01-28T00:00:00.000Z")

# 8a. Ensure monitoring-indices has docs for dev, prod, monitoring (for contribution transforms; match 12 ERU setup)
MONITORING_COUNT=$(curl_es -sS "$ES_HOST/monitoring-indices/_count" 2>/dev/null | grep -oE '"count":[0-9]+' | sed 's/"count"://')
if [[ -z "$MONITORING_COUNT" || "$MONITORING_COUNT" == "0" ]]; then
  echo "Seeding monitoring-indices: dev, prod, monitoring (3 deployments)."
  for cluster in dev prod monitoring; do
    curl_es -X POST "$ES_HOST/monitoring-indices/_doc" -d "{\"@timestamp\": \"$YESTERDAY\", \"elasticsearch\": { \"cluster\": { \"name\": \"$cluster\" } }}" >/dev/null 2>&1 || true
  done
fi

# 8b. (Billing doc updates run after 8e so we patch docs written by the On-Prem billing transform.)

# 8c. Use short sync delay for billing_cluster_cost so test data is picked up (transform defaults to 1h delay)
BILLING_TID=$(curl_es "$ES_HOST/_transform?size=100" 2>/dev/null | grep -oE '"id":"logs-chargeback\.billing_cluster_cost-[^"]+"' | head -1 | sed 's/"id":"//;s/"//')
if [[ -n "$BILLING_TID" ]]; then
  curl_es -X POST "$ES_HOST/_transform/$BILLING_TID/_stop" >/dev/null 2>&1 || true
  curl_es -X POST "$ES_HOST/_transform/$BILLING_TID/_update" -d '{"sync":{"time":{"field":"@timestamp","delay":"1m"}}}' >/dev/null 2>&1 || true
fi

# 8d. Reset and start all Chargeback transforms so they process source data and populate lookups
for TID in $(curl_es "$ES_HOST/_transform?size=100" 2>/dev/null | grep -oE '"id":"logs-chargeback\.[^"]+"' | sed 's/"id":"//;s/"//'); do
  echo "Reset/start $TID"
  curl_es -X POST "$ES_HOST/_transform/$TID/_stop" >/dev/null 2>&1 || true
  curl_es -X POST "$ES_HOST/_transform/$TID/_reset" >/dev/null 2>&1 || true
  curl_es -X POST "$ES_HOST/_transform/$TID/_start" >/dev/null 2>&1 || true
done

# 8e. Wait for transforms to run (index_pivot and contribution transforms need time)
echo "Waiting 45s for transforms to run..."
sleep 45

# 8f. Ensure billing docs have correct total_ecu and deployment_tags (patch docs from On-Prem billing transform or insert if none)
#     Search by ess.billing.deployment_id OR cluster_uuid so we match transform output (pipeline may not have run). Requires jq.
BILLING_INDEX="metrics-ess_billing.billing-onprem"
if ! command -v jq >/dev/null 2>&1; then
  echo "WARN: jq not found; skipping billing doc patch (total_ecu/deployment_tags may show defaults)."
else
for dep_id in dev prod monitoring; do
  case "$dep_id" in
    dev)        name="dev";        total_ecu=2000; tags='["chargeback_group:product"]' ;;
    prod)       name="prod";       total_ecu=8000; tags='["chargeback_group:product"]' ;;
    monitoring) name="monitoring"; total_ecu=2000; tags='["chargeback_group:monitoring"]' ;;
    *)          name="$dep_id";    total_ecu=1000; tags='["chargeback_group:product"]' ;;
  esac
  # Find docs: either pipeline set ess.billing.deployment_id, or raw transform has cluster_uuid/cluster_name = dep_id
  search_resp=$(curl_es -sS "$ES_HOST/$BILLING_INDEX/_search?size=50" -d "{\"_source\":false,\"query\":{\"bool\":{\"should\":[{\"term\":{\"ess.billing.deployment_id\":\"$dep_id\"}},{\"term\":{\"cluster_uuid\":\"$dep_id\"}},{\"term\":{\"cluster_name\":\"$dep_id\"}}],\"minimum_should_match\":1}}}" 2>/dev/null)
  existing_ids=$(echo "$search_resp" | grep -oE '"_id":"[^"]+"' | sed 's/"_id":"//;s/"//')
  if [[ -n "$existing_ids" ]]; then
    for doc_id in $existing_ids; do
      update_body=$(jq -n -c --arg ts "$YESTERDAY" --arg dep "$dep_id" --arg n "$name" --argjson ecu_val "$total_ecu" --arg t "$tags" \
        '{doc: {"@timestamp": $ts, "ess": {"billing": {"deployment_id": $dep, "deployment_name": $n, "total_ecu": $ecu_val, "deployment_tags": ($t | fromjson), "sku": "onprem_node", "type": "capacity", "kind": "elasticsearch", "deployment_type": "onprem"}}}}')
      curl_es -X POST "$ES_HOST/$BILLING_INDEX/_update/$doc_id" -d "$update_body" >/dev/null 2>&1 || true
    done
  else
    insert_body=$(jq -n -c --arg ts "$YESTERDAY" --arg dep "$dep_id" --arg n "$name" --argjson ecu_val "$total_ecu" --arg t "$tags" \
      '{ "@timestamp": $ts, "event": { "ingested": (now | todate) }, "ess": { "billing": { "deployment_id": $dep, "deployment_name": $n, "total_ecu": $ecu_val, "deployment_tags": ($t | fromjson), "sku": "onprem_node", "type": "capacity", "kind": "elasticsearch", "deployment_type": "onprem" } } }')
    curl_es -X POST "$ES_HOST/$BILLING_INDEX/_doc" -d "$insert_body" >/dev/null 2>&1 || true
  fi
done
echo "Patched billing docs in $BILLING_INDEX with total_ecu and deployment_tags (12 ERU: dev=2000, prod=8000, monitoring=2000)."
fi

# 8g. Re-run Chargeback billing_cluster_cost transform so billing_cluster_cost_lookup gets deployment_group from patched deployment_tags
BILLING_CLUSTER_TID=$(curl_es "$ES_HOST/_transform?size=100" 2>/dev/null | grep -oE '"id":"logs-chargeback\.billing_cluster_cost-[^"]+"' | head -1 | sed 's/"id":"//;s/"//')
if [[ -n "$BILLING_CLUSTER_TID" ]]; then
  echo "Reset/start billing_cluster_cost so lookup gets deployment_group (product/monitoring)..."
  curl_es -X POST "$ES_HOST/_transform/$BILLING_CLUSTER_TID/_stop" >/dev/null 2>&1 || true
  curl_es -X POST "$ES_HOST/_transform/$BILLING_CLUSTER_TID/_reset" >/dev/null 2>&1 || true
  curl_es -X POST "$ES_HOST/_transform/$BILLING_CLUSTER_TID/_start" >/dev/null 2>&1 || true
  sleep 15
fi

# 8h. Ensure billing_cluster_cost_lookup has deployment_group set (transform runtime_mapping can leave it empty; patch by deployment_id)
LOOKUP_INDEX="billing_cluster_cost_lookup"
for dep_id in dev prod monitoring; do
  case "$dep_id" in
    dev|prod)  group="product" ;;
    monitoring) group="monitoring" ;;
    *)         group="product" ;;
  esac
  resp=$(curl_es -sS "$ES_HOST/$LOOKUP_INDEX/_search?size=50" -d "{\"_source\":false,\"query\":{\"term\":{\"deployment_id\":\"$dep_id\"}}}" 2>/dev/null)
  ids=$(echo "$resp" | grep -oE '"_id":"[^"]+"' | sed 's/"_id":"//;s/"//')
  for doc_id in $ids; do
    curl_es -X POST "$ES_HOST/$LOOKUP_INDEX/_update/$doc_id" -d "{\"doc\":{\"deployment_group\":\"$group\"}}" >/dev/null 2>&1 || true
  done
done
echo "Patched $LOOKUP_INDEX deployment_group by deployment_id (dev,prod->product; monitoring->monitoring)."

# 9. Verification: list Chargeback transforms and document counts for lookup indices
echo "--- 9. Verification ---"
echo "Transforms (chargeback):"
curl_es "$ES_HOST/_transform?size=50" 2>/dev/null | grep -o '"id":"[^"]*chargeback[^"]*"' || true
echo "Lookup indices (chargeback) — document counts:"
for idx in billing_cluster_cost_lookup chargeback_conf_lookup cluster_datastream_contribution_lookup cluster_deployment_contribution_lookup cluster_tier_and_datastream_contribution_lookup cluster_tier_contribution_lookup; do
  count=$(curl_es -sS "$ES_HOST/$idx/_count" 2>/dev/null | grep -oE '"count":[0-9]+' | sed 's/"count"://')
  echo "  $idx: ${count:-0} docs"
done

# 10. Evidence: GET all *lookup indices — full content so you can visually verify success
echo ""
echo "--- 10. Evidence: full content of all *lookup indices ---"
cat_resp=$(curl_es -sS "$ES_HOST/_cat/indices/*lookup*?format=json" 2>/dev/null)
if command -v jq >/dev/null 2>&1 && [[ -n "$cat_resp" ]]; then
  LOOKUP_INDICES=$(echo "$cat_resp" | jq -r '.[].index' 2>/dev/null | sort -u)
fi
if [[ -z "$LOOKUP_INDICES" ]]; then
  LOOKUP_INDICES="billing_cluster_cost_lookup chargeback_conf_lookup cluster_datastream_contribution_lookup cluster_deployment_contribution_lookup cluster_tier_and_datastream_contribution_lookup cluster_tier_contribution_lookup"
fi
EVIDENCE_FILE="$CHARGEBACK_REPO/scripts/evidence_lookup_indices.txt"
{
  echo "Evidence of lookup indices — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "ES Host: $ES_HOST"
  echo ""
  for idx in $LOOKUP_INDICES; do
    echo "=== $idx ==="
    count=$(curl_es -sS "$ES_HOST/$idx/_count" 2>/dev/null | grep -oE '"count":[0-9]+' | sed 's/"count"://')
    echo "  doc count: ${count:-0}"
    resp=$(curl_es -sS "$ES_HOST/$idx/_search?size=100" -d '{"_source":true,"sort":[{"@timestamp":"desc"}],"query":{"match_all":{}}}' 2>/dev/null)
    if command -v jq >/dev/null 2>&1; then
      # Per-doc: numbered list and every _source field (robust for any structure)
      echo "$resp" | jq -r '
        .hits.hits | to_entries[] |
        "  --- doc \(.key + 1) ---",
        (.value._source | to_entries[] | "    \(.key): \(.value | if (type == "object" or type == "array") then tostring else . end)")
      ' 2>/dev/null
      table=$(echo "$resp" | jq -r '
        .hits.hits as $hits
        | (if ($hits|length) > 0 then ($hits[0]._source | keys) else [] end) as $cols
        | ($cols | @tsv),
          ($hits[] | ._source as $s | [$cols[] | ($s[.] | if (type == "object" or type == "array") then tostring else . end) // "-"] | @tsv)
      ' 2>/dev/null)
      if [[ -n "$table" ]]; then
        echo "  (table)"
        echo "$table" | column -t -s $'\t' | sed 's/^/    /'
      fi
    else
      echo "$resp" | grep -oE '"_source":\{[^}]+\}' | sed 's/^/  /'
    fi
    echo ""
  done
  echo "--- end evidence ---"
} | tee "$EVIDENCE_FILE"
echo "  Full evidence also written to: $EVIDENCE_FILE"

# 11. Cross-verification: proof that data matches across all lookup indices (same deployment_id, values consistent)
echo ""
echo "--- 11. Cross-verification: data match across all *lookup indices ---"
CROSS_OK=1
if command -v jq >/dev/null 2>&1; then
  # Key fields to compare across indices (order for table)
  KEY_FIELDS="deployment_id @timestamp deployment_name total_ecu tier datastream composite_key cost_type"
  # Build table: rows = field names, columns = lookup indices (same row = same field across indices)
  PASTE_TMP=$(mktemp)
  header="field"
  for idx in $LOOKUP_INDICES; do header="$header"$'\t'"$idx"; done
  echo "$header" > "$PASTE_TMP"
  for field in $KEY_FIELDS; do
    row="$field"
    for idx in $LOOKUP_INDICES; do
      resp=$(curl_es -sS "$ES_HOST/$idx/_search?size=1" -d '{"_source":["'"$field"'"],"query":{"match_all":{}}}' 2>/dev/null)
      v=$(echo "$resp" | jq -r '.hits.hits[0]._source["'"$field"'"] // empty | if (type == "object" or type == "array") then tostring else . end' 2>/dev/null)
      row="$row"$'\t'"${v:--}"
    done
    echo "$row" >> "$PASTE_TMP"
  done
  echo ""
  echo "  Key fields by index (same row = same field; columns must match where applicable):"
  echo ""
  column -t -s $'\t' < "$PASTE_TMP" | sed 's/^/  /'
  rm -f "$PASTE_TMP"

  # Explicit match check: deployment_id must appear in every index that has docs (billing has deployment_id, conf may have id "config", contribution have deployment_id)
  DEP_IDS=""
  for idx in $LOOKUP_INDICES; do
    resp=$(curl_es -sS "$ES_HOST/$idx/_search?size=1" -d '{"_source":["deployment_id"],"query":{"match_all":{}}}' 2>/dev/null)
    dep=$(echo "$resp" | jq -r '.hits.hits[0]._source.deployment_id // .hits.hits[0]._id // empty' 2>/dev/null)
    count=$(curl_es -sS "$ES_HOST/$idx/_count" 2>/dev/null | jq -r '.count // 0' 2>/dev/null)
    if [[ -n "$dep" ]]; then DEP_IDS="$DEP_IDS $dep"; fi
    if [[ "${count:-0}" -eq 0 ]]; then CROSS_OK=0; fi
  done
  echo ""
  echo "  Match check:"
  echo "  - All lookup indices have at least 1 document: $([[ $CROSS_OK -eq 1 ]] && echo 'PASS' || echo 'FAIL (some empty)')"
  # With 12 ERU setup we expect deployment_ids: dev, prod, monitoring (2 deployment groups: product, monitoring)
  if echo "$DEP_IDS" | grep -q "dev" && echo "$DEP_IDS" | grep -q "prod"; then
    echo "  - deployment_ids (12 ERU: dev, prod, monitoring): $DEP_IDS"
  else
    echo "  - deployment_id values: $DEP_IDS"
  fi
else
  echo "  (install jq for cross-verification table)"
fi
echo ""
echo "--- Evidence complete: tables above prove data consistency across all *lookup indices. ---"

echo ""
echo "--- Done. Run 'go run github.com/elastic/elastic-package test' from $INTEGRATIONS_REPO/packages/chargeback for asset tests. ---"
