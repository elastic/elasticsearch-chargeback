#!/usr/bin/env bash
# Diverse Chargeback E2E dataset: monitoring-indices, on-prem/cloud billing, node_stats.
# Source from run_e2e_tests.sh or run standalone against an existing stack with Chargeback installed.
#
# Usage:
#   ./scripts/seed_e2e_dataset.sh              # seed + reset Chargeback/on-prem transforms
#   ./scripts/seed_e2e_dataset.sh --seed-only  # seed sources only (no transform reset)
#
# Env: E2E_DAYS (default 21), ES_HOST, ELASTIC_USER, ELASTIC_PASSWORD, SKUTEST_DEPLOYMENT (default skutest)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -euo pipefail
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ES_HOST="${ES_HOST:-https://127.0.0.1:9200}"
USER="${ELASTIC_USER:-elastic}"
PASS="${ELASTIC_PASSWORD:-changeme}"
E2E_DAYS="${E2E_DAYS:-21}"
SKUTEST_DEPLOYMENT="${SKUTEST_DEPLOYMENT:-skutest}"

# shellcheck source=scripts/e2e_lib.sh
source "$SCRIPT_DIR/e2e_lib.sh"

# On-prem: id|chargeback_group|erus|base_ecu|idx_base|qry_base|store_gb|heap_pct|disk_pct|workload_profile
E2E_ONPREM_DEPLOYMENTS=(
  "dev|product|2|2000|3000|1500|10|45|40|balanced"
  "staging|product|3|3500|5000|2200|20|62|55|balanced"
  "prod|product|10|10000|12000|6000|50|82|72|balanced"
  "monitoring|monitoring|2|2000|2000|800|5|91|88|metrics_heavy"
  "analytics|research|4|4500|4000|9000|35|55|35|query_heavy"
  "security|platform|3|3200|2500|1200|25|70|65|logs_heavy"
)

# Data streams: name|index_weight|query_weight|store_weight (relative 1-20)
E2E_DATASTREAMS=(
  "logs-app|12|8|10"
  "logs-security|10|6|9"
  "metrics-system|8|5|7"
  "metrics-infra|7|4|8"
  "traces-apm|14|12|6"
  "synthetics-browser|5|9|4"
)

# Tiers: preference|index_mult|query_mult|store_mult
E2E_TIERS=(
  "data_hot,data_content|10|10|1"
  "data_warm|3|2|3"
  "data_cold|1|1|10"
  "data_frozen|1|1|18"
)

# Cloud fixture for issue #8 (single recent day, multi-SKU)
E2E_SKUTEST_SKUS=(
  "aws.es.datahot.general-purpose:1200"
  "aws.es.ml.general-purpose:400"
  "aws.kibana.general-purpose:300"
)

# Cloud-prod: daily SKUs with tier + platform + transfer/snapshot variety
E2E_CLOUD_PROD_SKUS=(
  "aws.es.datahot.general-purpose"
  "aws.es.datacontent.general-purpose"
  "aws.es.datawarm.general-purpose"
  "aws.es.datacold.general-purpose"
  "aws.es.ml.general-purpose"
  "aws.kibana.general-purpose"
  "aws.data-transfer.inter-region"
  "aws.snapshot-storage.standard"
)

e2e_curl_es() {
  curl -sS -k -u "$USER:$PASS" -H "Content-Type: application/json" "$@"
}

e2e_timestamp_days_ago() {
  local day=$1
  date -u -v-"${day}"d 2>/dev/null +%Y-%m-%dT00:00:00.000Z \
    || date -u -d "$day days ago" 2>/dev/null +%Y-%m-%dT00:00:00.000Z
}

e2e_dep_field() {
  local dep=$1 field=$2
  local row
  for row in "${E2E_ONPREM_DEPLOYMENTS[@]}"; do
    if [[ "${row%%|*}" == "$dep" ]]; then
      local rest=${row#*|}
      local i=1
      for f in group erus base_ecu idx_base qry_base store_gb heap_pct disk_pct profile; do
        if [[ "$f" == "$field" ]]; then
          echo "${rest%%|*}"
          return 0
        fi
        rest=${rest#*|}
      done
    fi
  done
  return 1
}

e2e_onprem_deployment_ids() {
  local row
  for row in "${E2E_ONPREM_DEPLOYMENTS[@]}"; do
    echo "${row%%|*}"
  done
}

e2e_profile_ds_multiplier() {
  local profile=$1 ds=$2 kind=$3
  local _ds iw qw sw
  for _ds in "${E2E_DATASTREAMS[@]}"; do
  [[ "${_ds%%|*}" == "$ds" ]] || continue
    iw=${_ds#*|}; qw=${iw#*|}; sw=${qw#*|}
    iw=${iw%%|*}; qw=${qw%%|*}; sw=${sw%%|*}
    case "$profile:$kind" in
      balanced:index) echo "$iw" ;;
      balanced:query) echo "$qw" ;;
      balanced:store) echo "$sw" ;;
      logs_heavy:index)
        case "$ds" in logs-app|logs-security) echo $(( iw * 15 / 10 )) ;; *) echo $(( iw * 7 / 10 )) ;; esac ;;
      logs_heavy:query)
        case "$ds" in logs-app|logs-security) echo $(( qw * 12 / 10 )) ;; *) echo $(( qw * 8 / 10 )) ;; esac ;;
      logs_heavy:store)
        case "$ds" in logs-app|logs-security) echo $(( sw * 14 / 10 )) ;; *) echo $(( sw * 6 / 10 )) ;; esac ;;
      query_heavy:index) echo $(( iw * 8 / 10 )) ;;
      query_heavy:query)
        case "$ds" in traces-apm|synthetics-browser) echo $(( qw * 18 / 10 )) ;; *) echo $(( qw * 9 / 10 )) ;; esac ;;
      query_heavy:store) echo $(( sw * 7 / 10 )) ;;
      metrics_heavy:index)
        case "$ds" in metrics-system|metrics-infra) echo $(( iw * 16 / 10 )) ;; *) echo $(( iw * 6 / 10 )) ;; esac ;;
      metrics_heavy:query)
        case "$ds" in metrics-system|metrics-infra) echo $(( qw * 14 / 10 )) ;; *) echo $(( qw * 7 / 10 )) ;; esac ;;
      metrics_heavy:store)
        case "$ds" in metrics-system|metrics-infra) echo $(( sw * 12 / 10 )) ;; *) echo $(( sw * 8 / 10 )) ;; esac ;;
      *) echo 10 ;;
    esac
    return 0
  done
  echo 10
}

e2e_compute_monitoring_expected() {
  local n_clusters=${#E2E_ONPREM_DEPLOYMENTS[@]}
  local n_ds=${#E2E_DATASTREAMS[@]}
  local n_tiers=${#E2E_TIERS[@]}
  echo $(( E2E_DAYS * n_clusters * n_ds * n_tiers ))
}

e2e_seed_monitoring_indices() {
  local expected force=${SEED_FORCE:-0}
  expected=$(e2e_compute_monitoring_expected)
  MONITORING_EXPECTED=$expected
  export MONITORING_EXPECTED

  local count
  count=$(e2e_es_count monitoring-indices)
  if [[ "${count:-0}" -ge "$expected" && "$force" != "1" ]]; then
    echo "monitoring-indices: ${count} docs (target ${expected}); skip seed (set SEED_FORCE=1 to replace)."
    return 0
  fi

  echo "Seeding monitoring-indices: ${E2E_DAYS}d × ${#E2E_ONPREM_DEPLOYMENTS[@]} clusters × ${#E2E_DATASTREAMS[@]} data streams × ${#E2E_TIERS[@]} tiers (${expected} docs)."
  [[ "${count:-0}" -gt 0 ]] && e2e_curl_es -X DELETE "$ES_HOST/monitoring-indices" >/dev/null 2>&1 || true

  local bulk day ts cluster row cluster_id group _erus base_ecu idx_base qry_base store_gb heap disk profile
  bulk=$(mktemp)
  for day in $(seq 1 "$E2E_DAYS"); do
    TS=$(e2e_timestamp_days_ago "$day")
    # Weekend dip + slow upward trend
    local day_mod=$(( day % 7 ))
    local trend=$(( 92 + day % 15 ))
    local weekend=100
    [[ "$day_mod" -eq 0 || "$day_mod" -eq 6 ]] && weekend=72

    for row in "${E2E_ONPREM_DEPLOYMENTS[@]}"; do
      IFS='|' read -r cluster_id group _erus base_ecu idx_base qry_base store_gb heap disk profile <<< "$row"
      local store_bytes=$(( store_gb * 1073741824 ))
      for ds_row in "${E2E_DATASTREAMS[@]}"; do
        local ds=${ds_row%%|*}
        local ds_i ds_q ds_s tier_row tier_pref t_i t_q t_s
        ds_i=$(e2e_profile_ds_multiplier "$profile" "$ds" index)
        ds_q=$(e2e_profile_ds_multiplier "$profile" "$ds" query)
        ds_s=$(e2e_profile_ds_multiplier "$profile" "$ds" store)
        for tier_row in "${E2E_TIERS[@]}"; do
          IFS='|' read -r tier_pref t_i t_q t_s <<< "$tier_row"
          local v=$(( trend * weekend / 100 ))
          local idx=$(( idx_base * ds_i / 10 * t_i / 10 * v / 100 ))
          local qry=$(( qry_base * ds_q / 10 * t_q / 10 * v / 100 ))
          local sto=$(( store_bytes * ds_s / 10 * t_s / 10 ))
          printf '{"index":{"_index":"monitoring-indices"}}\n' >> "$bulk"
          printf '{"@timestamp":"%s","elasticsearch":{"cluster":{"name":"%s"},"index":{"datastream":"%s","tier_preference":"%s","total":{"indexing":{"index_time_in_millis":%d},"search":{"query_time_in_millis":%d},"store":{"size_in_bytes":%d}},"primaries":{"store":{"total_data_set_size_in_bytes":%d}}}}}\n' \
            "$TS" "$cluster_id" "$ds" "$tier_pref" "$idx" "$qry" "$sto" "$sto" >> "$bulk"
        done
      done
    done
  done
  e2e_bulk_ndjson "$bulk"
  rm -f "$bulk"
  echo "Seeded monitoring-indices (${expected} docs)."
}

e2e_configure_onprem_org() {
  local total_erus=0 row erus
  for row in "${E2E_ONPREM_DEPLOYMENTS[@]}"; do
    erus=$(echo "$row" | cut -d'|' -f3)
    total_erus=$(( total_erus + erus ))
  done
  local annual_cost=$(( total_erus * 15000 ))
  echo "Configure onprem_billing_config: ${total_erus} ERU licence, ${#E2E_ONPREM_DEPLOYMENTS[@]} deployments."
  e2e_curl_es -X PUT "$ES_HOST/onprem_billing_config/_doc/organization" \
    -d "{\"config_type\":\"organization\",\"total_annual_license_cost\":${annual_cost},\"total_erus_purchased\":${total_erus},\"eru_to_ram_gb\":64,\"currency_unit\":\"EUR\"}" \
    >/dev/null 2>&1 || true

  local dep_id group name tags meru doc_id
  for row in "${E2E_ONPREM_DEPLOYMENTS[@]}"; do
    IFS='|' read -r dep_id group erus _ _ _ _ _ _ _ <<< "$row"
    name="$dep_id"
    tags="[\"chargeback_group:${group}\"]"
    meru=$(( erus * 1000 ))
    doc_id=$(e2e_curl_es "$ES_HOST/onprem_billing_config/_search?size=1" \
      -d "{\"_source\":false,\"query\":{\"term\":{\"deployment_id\":\"$dep_id\"}}}" 2>/dev/null \
      | jq -r '.hits.hits[0]._id // empty')
    if [[ -n "$doc_id" ]]; then
      e2e_curl_es -X POST "$ES_HOST/onprem_billing_config/_update/$doc_id" \
        -d "{\"doc\":{\"deployment_name\":\"$name\",\"deployment_tags\":$tags,\"deployment_erus\":$erus,\"daily_meru\":$meru}}" \
        >/dev/null 2>&1 || true
    else
      e2e_curl_es -X PUT "$ES_HOST/onprem_billing_config/_doc/$dep_id" \
        -d "{\"deployment_id\":\"$dep_id\",\"deployment_name\":\"$name\",\"deployment_tags\":$tags,\"deployment_erus\":$erus,\"daily_meru\":$meru}" \
        >/dev/null 2>&1 || true
    fi
  done
}

# Re-discover deployments from monitoring-indices, then apply the 24 ERU org + per-deployment config.
e2e_refresh_onprem_config() {
  local tid
  tid=$(e2e_get_transform_id '^logs-onprem_billing\.config_bootstrap-')
  if [[ -z "$tid" ]]; then
    echo "  WARN: onprem_billing config_bootstrap not installed; configuring org/deployment docs only."
    e2e_configure_onprem_org
    return 0
  fi
  echo "Refresh onprem_billing_config from monitoring-indices (config_bootstrap)..."
  e2e_transform_stop "$tid" 1 || true
  e2e_transform_reset "$tid" || true
  e2e_transform_start "$tid" || true
  e2e_wait_for_onprem_config || echo "  WARN: onprem_billing_config not fully populated (config_bootstrap may still be running)."
  e2e_configure_onprem_org
}

e2e_seed_billing_onprem() {
  local index="metrics-ess_billing.billing-onprem"
  echo "Seeding $index: ${E2E_DAYS}d × ${#E2E_ONPREM_DEPLOYMENTS[@]} on-prem deployments."
  e2e_curl_es -X POST "$ES_HOST/$index/_delete_by_query?refresh=true&conflicts=proceed" \
    -d '{"query":{"match_all":{}}}' >/dev/null 2>&1 || true

  local bulk now_iso day ts row dep_id group base_ecu name tags ecu v
  bulk=$(mktemp)
  now_iso=$(date -u 2>/dev/null +%Y-%m-%dT%H:%M:%S.000Z || echo "2026-01-01T00:00:00.000Z")
  for day in $(seq 1 "$E2E_DAYS"); do
    ts=$(e2e_timestamp_days_ago "$day")
    for row in "${E2E_ONPREM_DEPLOYMENTS[@]}"; do
      IFS='|' read -r dep_id group _erus base_ecu _ _ _ _ _ _ <<< "$row"
      name="$dep_id"
      tags="[\"chargeback_group:${group}\"]"
      # Per-deployment phase offset so lines diverge on charts
      v=$(( 88 + (day + ${#dep_id}) % 24 ))
      ecu=$(( base_ecu * v / 100 ))
      printf '{"create":{"_index":"%s"}}\n' "$index" >> "$bulk"
      printf '{"@timestamp":"%s","event":{"ingested":"%s"},"ess":{"billing":{"deployment_id":"%s","deployment_name":"%s","total_ecu":%d,"deployment_tags":%s,"sku":"onprem_node","type":"capacity","kind":"elasticsearch","deployment_type":"onprem"}}}\n' \
        "$ts" "$now_iso" "$dep_id" "$name" "$ecu" "$tags" >> "$bulk"
    done
  done
  e2e_bulk_ndjson "$bulk"
  rm -f "$bulk"
  echo "Seeded $index."
}

e2e_seed_billing_cloud() {
  local cloud_index="metrics-ess_billing.billing-default"
  local now_iso ts_recent ts_day sku_line sku ecu base i dep_id

  now_iso=$(date -u 2>/dev/null +%Y-%m-%dT%H:%M:%S.000Z || echo "2026-01-01T00:00:00.000Z")
  ts_recent=$(date -u -v-3M 2>/dev/null +%Y-%m-%dT%H:%M:%S.000Z || date -u -d '3 minutes ago' 2>/dev/null +%Y-%m-%dT%H:%M:%S.000Z || e2e_timestamp_days_ago 1)

  echo "Seeding $cloud_index: skutest (#8 fixture) + cloud-prod (multi-SKU history)."
  e2e_curl_es -X POST "$ES_HOST/$cloud_index/_delete_by_query?refresh=true&conflicts=proceed" \
    -d '{"query":{"terms":{"ess.billing.deployment_id":["'"$SKUTEST_DEPLOYMENT"'","cloud-prod"]}}}' \
    >/dev/null 2>&1 || true

  # skutest: fixed recent snapshot for issue #8 allocatable-ECU check (datahot only = 1200)
  for sku_line in "${E2E_SKUTEST_SKUS[@]}"; do
    sku=${sku_line%%:*}
    ecu=${sku_line##*:}
    e2e_curl_es -X POST "$ES_HOST/$cloud_index/_doc?refresh=true" -d "{
      \"@timestamp\": \"$ts_recent\",
      \"event\": {\"ingested\": \"$now_iso\"},
      \"ess\": {\"billing\": {
        \"deployment_id\": \"$SKUTEST_DEPLOYMENT\",
        \"deployment_name\": \"$SKUTEST_DEPLOYMENT\",
        \"deployment_tags\": [\"chargeback_group:product\"],
        \"total_ecu\": $ecu,
        \"sku\": \"$sku\",
        \"type\": \"capacity\",
        \"kind\": \"elasticsearch\",
        \"deployment_type\": \"deployment\"
      }}
    }" >/dev/null 2>&1 || true
  done

  # skutest + cloud-prod: daily history with tier/platform/transfer SKUs
  local bulk
  bulk=$(mktemp)
  for day in $(seq 1 "$E2E_DAYS"); do
    ts_day=$(e2e_timestamp_days_ago "$day")
    local v=$(( 85 + day % 25 ))
    for dep_id in "$SKUTEST_DEPLOYMENT" cloud-prod; do
      local tags='["chargeback_group:product"]'
      [[ "$dep_id" == "cloud-prod" ]] && tags='["chargeback_group:product","env:production"]'
      i=0
      for sku in "${E2E_CLOUD_PROD_SKUS[@]}"; do
        i=$(( i + 1 ))
        case "$sku" in
          *datahot*)     base=1400 ;;
          *datacontent*) base=600 ;;
          *datawarm*)    base=900 ;;
          *datacold*)    base=400 ;;
          *datafrozen*)  base=200 ;;
          *ml*)          base=500 ;;
          *kibana*)      base=350 ;;
          *data-transfer*) base=80 ;;
          *snapshot*)    base=45 ;;
          *)             base=300 ;;
        esac
        # skutest history uses smaller amounts; recent fixture above carries the #8 proof
        [[ "$dep_id" == "$SKUTEST_DEPLOYMENT" && "$day" -gt 3 ]] && continue
        ecu=$(( base * v / 100 * (10 + i % 4) / 10 ))
        local kind="elasticsearch" btype="capacity"
        [[ "$sku" == *kibana* ]] && kind="kibana"
        [[ "$sku" == *data-transfer* || "$sku" == *snapshot* ]] && btype="usage"
        printf '{"create":{"_index":"%s"}}\n' "$cloud_index" >> "$bulk"
        printf '{"@timestamp":"%s","event":{"ingested":"%s"},"ess":{"billing":{"deployment_id":"%s","deployment_name":"%s","deployment_tags":%s,"total_ecu":%d,"sku":"%s","type":"%s","kind":"%s","deployment_type":"deployment"}}}\n' \
          "$ts_day" "$now_iso" "$dep_id" "$dep_id" "$tags" "$ecu" "$sku" "$btype" "$kind" >> "$bulk"
      done
    done
  done
  e2e_bulk_ndjson "$bulk"
  rm -f "$bulk"
  echo "Seeded $cloud_index (skutest + cloud-prod)."
}

e2e_seed_node_stats() {
  local index="metrics-elasticsearch.stack_monitoring.node_stats-default"
  local clusters=() row dep_id
  for row in "${E2E_ONPREM_DEPLOYMENTS[@]}"; do
    clusters+=("${row%%|*}")
  done
  clusters+=("$SKUTEST_DEPLOYMENT" "cloud-prod")

  # Metrics data streams only accept docs in the current backing index time range (not 21d history).
  # Seed recent samples so cluster_capacity_utilization can compute p95 per deployment for today.
  echo "Seeding $index: ${#clusters[@]} clusters × 3 nodes (recent timestamps, variable utilization)."
  local cluster_list
  cluster_list=$(printf '"%s",' "${clusters[@]}")
  cluster_list="[${cluster_list%,}]"
  e2e_curl_es -X POST "$ES_HOST/$index/_delete_by_query?refresh=true&conflicts=proceed" \
    -d "{\"query\":{\"terms\":{\"elasticsearch.cluster.name\":$cluster_list}}}" \
    >/dev/null 2>&1 || true

  local cluster_i ts ingested_ts cluster heap disk node_i heap_v disk_v total avail node_name
  ingested_ts=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
  cluster_i=0
  for cluster in "${clusters[@]}"; do
    cluster_i=$(( cluster_i + 1 ))
    if heap=$(e2e_dep_field "$cluster" heap_pct 2>/dev/null); then
      disk=$(e2e_dep_field "$cluster" disk_pct)
    else
      case "$cluster" in
        "$SKUTEST_DEPLOYMENT") heap=78; disk=60 ;;
        cloud-prod) heap=84; disk=70 ;;
        *) heap=75; disk=60 ;;
      esac
    fi
    # Stagger timestamps within the writable window (minutes ago per cluster)
    ts=$(date -u -v-$(( cluster_i * 8 + 5 ))M 2>/dev/null +%Y-%m-%dT%H:%M:%S.000Z \
      || date -u -d "$(( cluster_i * 8 + 5 )) minutes ago" 2>/dev/null +%Y-%m-%dT%H:%M:%S.000Z)
    for node_i in 1 2 3; do
      heap_v=$(( heap + node_i * 2 - 3 ))
      disk_v=$(( disk + node_i - 2 ))
      [[ "$heap_v" -lt 5 ]] && heap_v=5
      [[ "$heap_v" -gt 99 ]] && heap_v=99
      [[ "$disk_v" -lt 5 ]] && disk_v=5
      [[ "$disk_v" -gt 99 ]] && disk_v=99
      total=1000000
      avail=$(( total * (100 - disk_v) / 100 ))
      local node_name="e2e-${cluster}-node-${node_i}"
      e2e_curl_es -X POST "$ES_HOST/$index/_doc?refresh=false" -d "{
        \"@timestamp\": \"$ts\",
        \"event\": {\"ingested\": \"$ingested_ts\"},
        \"agent\": {\"id\": \"e2e-agent-${cluster}\"},
        \"host\": {\"name\": \"e2e-${cluster}\"},
        \"service\": {\"address\": \"https://${cluster}:9200\"},
        \"elasticsearch\": {
          \"cluster\": {\"name\": \"$cluster\", \"id\": \"e2e-cluster-${cluster}\"},
          \"node\": {
            \"id\": \"$node_name\",
            \"name\": \"$node_name\",
            \"roles\": [\"data_hot\", \"data_content\"],
            \"stats\": {
              \"jvm\": {\"mem\": {\"heap\": {\"used\": {\"pct\": $heap_v}}}},
              \"fs\": {\"total\": {\"total_in_bytes\": $total, \"available_in_bytes\": $avail}}
            }
          }
        }
      }" >/dev/null 2>&1 || true
    done
  done
  e2e_curl_es -X POST "$ES_HOST/$index/_refresh" >/dev/null 2>&1 || true
  echo "Seeded $index."
}

e2e_seed_all_sources() {
  e2e_seed_monitoring_indices
  e2e_seed_billing_onprem
  e2e_seed_billing_cloud
  e2e_seed_node_stats
}

e2e_print_dataset_summary() {
  echo ""
  echo "=== Diverse E2E dataset summary ==="
  echo "Horizon:        ${E2E_DAYS} days"
  echo "On-prem:        $(e2e_onprem_deployment_ids | tr '\n' ' ')"
  echo "Groups:         product, monitoring, research, platform"
  echo "Data streams:   $(printf '%s ' "${E2E_DATASTREAMS[@]%%|*}")"
  echo "Tiers:          $(printf '%s ' "${E2E_TIERS[@]%%|*}")"
  echo "Cloud:          ${SKUTEST_DEPLOYMENT} (issue #8), cloud-prod (multi-SKU + transfer/snapshot)"
  echo "Monitoring docs target: $(e2e_compute_monitoring_expected)"
  echo "Dashboard:      last 30 days recommended"
  echo ""
}

seed_e2e_main() {
  local reset=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --seed-only) reset=0; shift ;;
      -h|--help)
        sed -n '1,12p' "$0"
        exit 0
        ;;
      *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
  done

  e2e_require_commands jq curl

  # Allow parent to supply curl_es
  if declare -F curl_es >/dev/null 2>&1; then
    e2e_curl_es() { curl_es "$@"; }
  fi

  e2e_print_dataset_summary
  e2e_seed_monitoring_indices
  e2e_refresh_onprem_config
  e2e_ensure_onprem_billing_event_ingested_mapping
  e2e_seed_billing_onprem
  e2e_seed_billing_cloud
  e2e_seed_node_stats
  if [[ "$reset" -eq 1 ]]; then
    e2e_prepare_all_chargeback_transforms
    e2e_reset_billing_transforms
    e2e_wait_for_contribution_lookups
    e2e_wait_for_lookup_indices "$E2E_LOOKUP_MIN_DOCS"
    e2e_assert_deployment_groups
  fi
  echo "Seed complete."
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  seed_e2e_main "$@"
fi
