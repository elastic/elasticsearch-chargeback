#!/usr/bin/env bash
# Shared helpers for Chargeback E2E scripts.
# Source after setting ES_HOST, KIBANA_HOST, ELASTIC_USER, ELASTIC_PASSWORD.

E2E_TRANSFORM_LIST_SIZE="${E2E_TRANSFORM_LIST_SIZE:-200}"
E2E_WAIT_INTERVAL="${E2E_WAIT_INTERVAL:-5}"
E2E_WAIT_ATTEMPTS="${E2E_WAIT_ATTEMPTS:-120}"
E2E_LOOKUP_MIN_DOCS="${E2E_LOOKUP_MIN_DOCS:-1}"

# All Chargeback lookup indices verified in E2E.
E2E_LOOKUP_INDICES=(
  billing_cluster_cost_lookup
  billing_realized_pool_lookup
  cluster_capacity_utilization_lookup
  chargeback_conf_lookup
  cluster_datastream_contribution_lookup
  cluster_deployment_contribution_lookup
  cluster_tier_and_datastream_contribution_lookup
  cluster_tier_contribution_lookup
)

# Billing-related transforms reset after billing/node_stats seed.
E2E_BILLING_TRANSFORM_PATTERNS=(
  '^logs-chargeback\.billing_realized_pool-'
  '^logs-chargeback\.cluster_capacity_utilization-'
  '^logs-chargeback\.billing_cluster_cost-'
  '^logs-chargeback\.chargeback_conf_lookup-'
)

# All Chargeback transform name prefixes (for full reset).
E2E_ALL_CHARGEBACK_TRANSFORM_PATTERNS=(
  '^logs-chargeback\.billing_realized_pool-'
  '^logs-chargeback\.cluster_capacity_utilization-'
  '^logs-chargeback\.billing_cluster_cost-'
  '^logs-chargeback\.chargeback_conf_lookup-'
  '^logs-chargeback\.cluster_deployment_contribution-'
  '^logs-chargeback\.cluster_datastream_contribution-'
  '^logs-chargeback\.cluster_tier_contribution-'
  '^logs-chargeback\.cluster_tier_and_datastream_contribution-'
  '^logs-chargeback\.cluster_tier_and_ds_contribution-'
)

E2E_CHARGEBACK_TRANSFORM_SYNC='{"frequency":"1m","sync":{"time":{"field":"@timestamp","delay":"1m"}}}'
E2E_CHARGEBACK_INGESTED_SYNC='{"frequency":"1m","sync":{"time":{"field":"event.ingested","delay":"1m"}}}'

e2e_require_commands() {
  local missing=()
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Required commands not found: ${missing[*]}" >&2
    exit 1
  fi
}

e2e_api() {
  if declare -F e2e_curl_es >/dev/null 2>&1; then
    e2e_curl_es "$@"
  elif declare -F curl_es >/dev/null 2>&1; then
    curl_es "$@"
  else
    curl -sS -k -u "${ELASTIC_USER:-elastic}:${ELASTIC_PASSWORD:-changeme}" \
      -H "Content-Type: application/json" "$@"
  fi
}

e2e_api_kibana() {
  if declare -F curl_kibana >/dev/null 2>&1; then
    curl_kibana "$@"
  else
    curl -sS -k -u "${ELASTIC_USER:-elastic}:${ELASTIC_PASSWORD:-changeme}" \
      -H "Content-Type: application/json" -H "kbn-xsrf: true" "$@"
  fi
}

e2e_es_count() {
  local index=$1
  local query=${2:-'{"query":{"match_all":{}}}'}
  e2e_api "$ES_HOST/$index/_count" -d "$query" 2>/dev/null | jq -r '.count // 0'
}

e2e_es_json_field() {
  local json=$1
  local filter=$2
  echo "$json" | jq -r "$filter" 2>/dev/null
}

e2e_get_transform_ids() {
  local pattern=$1
  e2e_api "$ES_HOST/_transform?size=$E2E_TRANSFORM_LIST_SIZE" 2>/dev/null \
    | jq -r --arg re "$pattern" '.transforms[]?.id // empty | select(test($re))'
}

e2e_get_transform_id() {
  e2e_get_transform_ids "$1" | head -1
}

e2e_list_chargeback_transform_ids() {
  e2e_get_transform_ids '^logs-chargeback\.'
}

get_transform_id() {
  e2e_get_transform_id "$1"
}

get_running_stack_version() {
  e2e_api "$ES_HOST" 2>/dev/null | jq -r '.version.number // empty'
}

wait_for_stack_healthy() {
  local want=$1
  local attempts=${STACK_WAIT_ATTEMPTS:-$E2E_WAIT_ATTEMPTS}
  local interval=${STACK_WAIT_INTERVAL:-$E2E_WAIT_INTERVAL}
  local i running status
  for ((i = 1; i <= attempts; i++)); do
    running=$(get_running_stack_version 2>/dev/null || true)
    if [[ "$running" == "$want" ]]; then
      status=$(e2e_api "$ES_HOST/_cluster/health" 2>/dev/null | jq -r '.status // empty')
      if [[ "$status" == "green" || "$status" == "yellow" ]]; then
        echo "Stack $want is $status (attempt $i/$attempts)"
        return 0
      fi
    fi
    echo "Waiting for stack $want... (attempt $i/$attempts, running=${running:-none})"
    sleep "$interval"
  done
  echo "Timed out waiting for Elasticsearch/Kibana $want at $ES_HOST" >&2
  return 1
}

e2e_wait_for_min_docs() {
  local index=$1
  local min=${2:-1}
  local attempts=${3:-$E2E_WAIT_ATTEMPTS}
  local interval=${4:-$E2E_WAIT_INTERVAL}
  local query=${5:-'{"query":{"match_all":{}}}'}
  local i count
  for ((i = 1; i <= attempts; i++)); do
    count=$(e2e_es_count "$index" "$query")
    if [[ "${count:-0}" -ge "$min" ]]; then
      echo "  $index: ${count} doc(s) (>= $min, attempt $i/$attempts)"
      return 0
    fi
    echo "  Waiting for $index (have ${count:-0}, need $min)... attempt $i/$attempts"
    sleep "$interval"
  done
  echo "Timed out waiting for $index to reach $min documents" >&2
  return 1
}

e2e_bulk_ndjson() {
  local file=$1
  local user="${ELASTIC_USER:-elastic}"
  local pass="${ELASTIC_PASSWORD:-changeme}"
  local resp errors
  resp=$(curl -sS -k -u "$user:$pass" \
    -H "Content-Type: application/x-ndjson" \
    -X POST "$ES_HOST/_bulk?refresh=true" --data-binary @"$file")
  errors=$(echo "$resp" | jq -r 'if .errors == true then (.items[] | .index.error? // .create.error? // .delete.error? // empty | .reason) else empty end' 2>/dev/null | head -3)
  if [[ -n "$errors" ]]; then
    echo "Bulk upload to $ES_HOST failed:" >&2
    echo "$errors" >&2
    return 1
  fi
  return 0
}

e2e_transform_stop() {
  local tid=$1
  local optional=${2:-0}
  local resp
  resp=$(e2e_api -X POST "$ES_HOST/_transform/${tid}/_stop?wait_for_completion=true&timeout=60s" 2>/dev/null)
  if echo "$resp" | jq -e '.acknowledged == true' >/dev/null 2>&1; then
    return 0
  fi
  if [[ "$optional" -eq 1 ]]; then
    return 0
  fi
  if echo "$resp" | grep -qiE 'resource_not_found_exception|transform_config_not_found|already stopped|not started'; then
    return 0
  fi
  echo "Failed to stop transform $tid: $resp" >&2
  return 1
}

e2e_transform_reset() {
  local tid=$1
  local resp
  resp=$(e2e_api -X POST "$ES_HOST/_transform/${tid}/_reset" 2>/dev/null)
  if echo "$resp" | jq -e '.acknowledged == true' >/dev/null 2>&1; then
    return 0
  fi
  if echo "$resp" | jq -e --arg id "$tid" '.id == $id' >/dev/null 2>&1; then
    return 0
  fi
  echo "Failed to reset transform $tid: $resp" >&2
  return 1
}

# GET /_transform/{id} returns { transforms: [ {...} ] }; unwrap for jq.
e2e_transform_cfg_jq() {
  jq 'if .transforms then .transforms[0] else . end'
}

e2e_transform_update() {
  local tid=$1
  local body=$2
  local resp
  resp=$(e2e_api -X POST "$ES_HOST/_transform/${tid}/_update" -d "$body" 2>/dev/null)
  if echo "$resp" | jq -e '.acknowledged == true' >/dev/null 2>&1; then
    return 0
  fi
  # ES 9.x returns the updated transform definition on success.
  if echo "$resp" | jq -e --arg id "$tid" '.id == $id' >/dev/null 2>&1; then
    return 0
  fi
  if echo "$resp" | jq -e '.sync.time.delay == "1m" or .frequency == "1m"' >/dev/null 2>&1; then
    return 0
  fi
  if echo "$resp" | grep -qiE 'resource_not_found_exception|illegal_argument_exception.*same.*config'; then
    return 0
  fi
  echo "Failed to update transform $tid: $(echo "$resp" | head -c 500)" >&2
  return 1
}

e2e_transform_start() {
  local tid=$1
  local resp
  resp=$(e2e_api -X POST "$ES_HOST/_transform/${tid}/_start" 2>/dev/null)
  if echo "$resp" | jq -e '.acknowledged == true' >/dev/null 2>&1; then
    e2e_api -X POST "$ES_HOST/_transform/${tid}/_schedule_now" >/dev/null 2>&1 || true
    return 0
  fi
  if echo "$resp" | jq -e --arg id "$tid" '.id == $id' >/dev/null 2>&1; then
    e2e_api -X POST "$ES_HOST/_transform/${tid}/_schedule_now" >/dev/null 2>&1 || true
    return 0
  fi
  if echo "$resp" | grep -qi 'already started'; then
    e2e_api -X POST "$ES_HOST/_transform/${tid}/_schedule_now" >/dev/null 2>&1 || true
    return 0
  fi
  echo "Failed to start transform $tid: $(echo "$resp" | head -c 500)" >&2
  return 1
}

e2e_transform_patch_for_e2e() {
  local tid=$1
  local cfg field patch delay
  cfg=$(e2e_api "$ES_HOST/_transform/$tid" 2>/dev/null | e2e_transform_cfg_jq)
  field=$(echo "$cfg" | jq -r '.sync.time.field // empty')
  if [[ "$tid" == *cluster_capacity_utilization* ]]; then
    # @timestamp sync + daily date_histogram skips intraday docs in continuous mode; use event.ingested in E2E.
    patch="$E2E_CHARGEBACK_INGESTED_SYNC"
  elif [[ "$field" == "event.ingested" ]]; then
    patch="$E2E_CHARGEBACK_INGESTED_SYNC"
  elif [[ -n "$field" ]]; then
    patch="$E2E_CHARGEBACK_TRANSFORM_SYNC"
  else
    e2e_transform_update "$tid" '{"frequency":"1m"}' 2>/dev/null || true
    return 0
  fi
  e2e_transform_update "$tid" "$patch" || return 1
  delay=$(e2e_api "$ES_HOST/_transform/$tid" 2>/dev/null | e2e_transform_cfg_jq | jq -r '.sync.time.delay // empty')
  if [[ "$delay" != "1m" && "$delay" != "60s" ]]; then
    echo "  Retrying sync patch for $tid (delay was ${delay:-unknown})" >&2
    e2e_transform_update "$tid" "$patch" || return 1
    delay=$(e2e_api "$ES_HOST/_transform/$tid" 2>/dev/null | e2e_transform_cfg_jq | jq -r '.sync.time.delay // empty')
    if [[ "$delay" != "1m" && "$delay" != "60s" ]]; then
      echo "Failed to set 1m sync delay on $tid (still ${delay:-unknown})" >&2
      return 1
    fi
  fi
}

# onprem billing data stream omits event.ingested in package mappings; required for Chargeback sync.
e2e_ensure_onprem_billing_event_ingested_mapping() {
  e2e_api -X PUT "$ES_HOST/metrics-ess_billing.billing-onprem/_mapping" -d '{
    "properties": {
      "event": {
        "properties": {
          "ingested": {"type": "date"}
        }
      }
    }
  }' >/dev/null 2>&1 || true
}

# Fleet 0.4.0 zip may ship an older capacity_utilization pipeline; apply fixed YAML from integrations.
e2e_patch_capacity_utilization_pipeline() {
  local repo=${INTEGRATIONS_REPO:-}
  local yml name body
  if [[ -z "$repo" || ! -d "$repo/packages/chargeback" ]]; then
    echo "  WARN: INTEGRATIONS_REPO unset; skipping capacity_utilization pipeline patch" >&2
    return 0
  fi
  for name in 0.4.0-capacity_utilization 0.4.1-capacity_utilization capacity_utilization; do
    yml="$repo/packages/chargeback/elasticsearch/ingest_pipeline/${name}.yml"
    [[ -f "$yml" ]] || continue
    body=$(ruby -ryaml -rjson -e 'puts JSON.generate(YAML.load_file(ARGV[0]))' "$yml") || return 1
    e2e_api -X PUT "$ES_HOST/_ingest/pipeline/$name" -d "$body" >/dev/null || return 1
    echo "  Patched ingest pipeline: $name"
  done
}

# Configure E2E sync, optionally reset dest, and start one transform.
e2e_prepare_transform() {
  local tid=$1
  local do_reset=${2:-1}
  echo "  Prepare transform: $tid"
  e2e_transform_stop "$tid" 1 || return 1
  if [[ "$do_reset" -eq 1 ]]; then
    e2e_transform_reset "$tid" || return 1
  fi
  e2e_transform_patch_for_e2e "$tid" || return 1
  e2e_transform_start "$tid" || return 1
}

e2e_reset_transforms_by_patterns() {
  local do_reset=$1
  shift
  local patterns=("$@")
  local pattern tid
  for pattern in "${patterns[@]}"; do
    while IFS= read -r tid; do
      [[ -z "$tid" ]] && continue
      e2e_prepare_transform "$tid" "$do_reset" || return 1
    done < <(e2e_get_transform_ids "$pattern")
  done
}

e2e_prepare_all_chargeback_transforms() {
  echo "Configure, reset, and start all Chargeback transforms (E2E sync @timestamp 1m)..."
  e2e_reset_transforms_by_patterns 1 "${E2E_ALL_CHARGEBACK_TRANSFORM_PATTERNS[@]}"
}

e2e_reset_billing_transforms() {
  echo "Reset billing-related Chargeback transforms after billing seed..."
  e2e_reset_transforms_by_patterns 1 "${E2E_BILLING_TRANSFORM_PATTERNS[@]}"
}

e2e_wait_for_lookup_indices() {
  local min=${1:-$E2E_LOOKUP_MIN_DOCS}
  local idx
  echo "Waiting for lookup indices (>= $min doc each)..."
  for idx in "${E2E_LOOKUP_INDICES[@]}"; do
    e2e_wait_for_min_docs "$idx" "$min" || return 1
  done
}

e2e_wait_for_contribution_lookups() {
  echo "Waiting for contribution lookups from monitoring-indices..."
  e2e_wait_for_min_docs cluster_deployment_contribution_lookup 1
  e2e_wait_for_min_docs cluster_datastream_contribution_lookup 1
  e2e_wait_for_min_docs cluster_tier_contribution_lookup 1
}

e2e_wait_for_onprem_config() {
  local min_deps=${#E2E_ONPREM_DEPLOYMENTS[@]}
  [[ "$min_deps" -gt 0 ]] || min_deps=1
  echo "Waiting for onprem_billing_config deployment docs (need >= $min_deps)..."
  e2e_wait_for_min_docs onprem_billing_config "$min_deps" \
    "${E2E_WAIT_ATTEMPTS}" "$E2E_WAIT_INTERVAL" \
    '{"query":{"bool":{"must_not":[{"term":{"config_type":"organization"}}]}}}'
}

e2e_list_lookup_indices() {
  local cat_resp
  cat_resp=$(e2e_api "$ES_HOST/_cat/indices/*lookup*?format=json" 2>/dev/null)
  if [[ -n "$cat_resp" ]]; then
    echo "$cat_resp" | jq -r '.[].index' 2>/dev/null | sort -u
    return 0
  fi
  printf '%s\n' "${E2E_LOOKUP_INDICES[@]}"
}

# Verify deployment_group was extracted by transforms (not patched manually).
e2e_assert_deployment_groups() {
  local index="billing_cluster_cost_lookup"
  local ok=1 row dep_id group matched total

  echo "Assert deployment_group in $index (from billing deployment_tags)..."
  for row in "${E2E_ONPREM_DEPLOYMENTS[@]}"; do
    IFS='|' read -r dep_id group _ <<< "$row"
    matched=$(e2e_es_count "$index" \
      "{\"query\":{\"bool\":{\"must\":[{\"term\":{\"deployment_id\":\"$dep_id\"}},{\"term\":{\"deployment_group\":\"$group\"}}]}}}")
    total=$(e2e_es_count "$index" "{\"query\":{\"term\":{\"deployment_id\":\"$dep_id\"}}}")
    if [[ "${total:-0}" -gt 0 && "${matched:-0}" -eq 0 ]]; then
      echo "  FAIL: $dep_id has $total doc(s) but none with deployment_group=$group" >&2
      ok=0
    elif [[ "${matched:-0}" -gt 0 ]]; then
      echo "  PASS: $dep_id deployment_group=$group ($matched doc(s))"
    fi
  done
  for dep_id in "$SKUTEST_DEPLOYMENT" cloud-prod; do
    matched=$(e2e_es_count "$index" \
      "{\"query\":{\"bool\":{\"must\":[{\"term\":{\"deployment_id\":\"$dep_id\"}},{\"term\":{\"deployment_group\":\"product\"}}]}}}")
    total=$(e2e_es_count "$index" "{\"query\":{\"term\":{\"deployment_id\":\"$dep_id\"}}}")
    if [[ "${total:-0}" -gt 0 && "${matched:-0}" -eq 0 ]]; then
      echo "  FAIL: $dep_id has docs but deployment_group!=product" >&2
      ok=0
    elif [[ "${matched:-0}" -gt 0 ]]; then
      echo "  PASS: $dep_id deployment_group=product ($matched doc(s))"
    fi
  done
  [[ "$ok" -eq 1 ]]
}

e2e_fleet_first_policy_id() {
  e2e_api_kibana "$KIBANA_HOST/api/fleet/agent_policies?perPage=100" 2>/dev/null \
    | jq -r '.items[0].id // empty'
}

e2e_fleet_policy_ids_by_name() {
  local name=$1
  e2e_api_kibana "$KIBANA_HOST/api/fleet/package_policies?perPage=200" 2>/dev/null \
    | jq -r --arg n "$name" '.items[]? | select(.name == $n) | .id'
}

e2e_fleet_onprem_policy_ids() {
  e2e_api_kibana "$KIBANA_HOST/api/fleet/package_policies?perPage=200" 2>/dev/null \
    | jq -r '.items[]? | select(.package.name == "onprem_billing") | .id'
}
