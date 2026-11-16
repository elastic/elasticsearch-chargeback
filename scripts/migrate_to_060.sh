#!/usr/bin/env bash
# Helper for upgrading Chargeback from 0.4.x / 0.5.0 to 0.6.0+.
#
# What it does:
#   1. Verifies all 8 versioned chargeback_lookup.* aliases exist (0.6.0+ installed).
#   2. Carries over any legacy chargeback_conf_lookup rate/currency/date settings
#      into chargeback_lookup.chargeback_conf (_id: config) if not already present.
#   3. Deletes retired pre-0.6.0 concrete *_lookup indices (while keeping
#      cluster_capacity_utilization_lookup) to reclaim cluster shards.
#
# Usage:
#   ES_URL="https://..." ES_API_KEY="..." ./scripts/migrate_to_060.sh [--dry-run] [--force]
#   ELASTIC_USER="elastic" ELASTIC_PASSWORD="changeme" ./scripts/migrate_to_060.sh --dry-run

set -euo pipefail

DRY_RUN=0
FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --force)   FORCE=1; shift ;;
    -h|--help)
      sed -n '2,15p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/keychain.sh" ]]; then
  # shellcheck source=scripts/keychain.sh
  source "$SCRIPT_DIR/keychain.sh"
  keychain_load_env
fi

ES_URL="${ES_URL:-${ES_HOST:-https://127.0.0.1:9200}}"
ES_URL="${ES_URL%/}"
ELASTIC_USER="${ELASTIC_USER:-elastic}"
ELASTIC_PASSWORD="${ELASTIC_PASSWORD:-changeme}"

url_hostname() {
  python3 -c "from urllib.parse import urlparse; print((urlparse('''$1''').hostname or '').lower())"
}

ES_CURL_TLS=()
if [[ "${CURL_INSECURE:-0}" == "1" ]]; then
  ES_CURL_TLS=(-k)
else
  case "$(url_hostname "$ES_URL")" in
    localhost|127.0.0.1|::1) ES_CURL_TLS=(-k) ;;
  esac
fi

curl_es() {
  if [[ -n "${ES_API_KEY:-}" ]]; then
    curl -sS "${ES_CURL_TLS[@]}" -H "Authorization: ApiKey $ES_API_KEY" -H "Content-Type: application/json" "$@"
  else
    curl -sS "${ES_CURL_TLS[@]}" -u "$ELASTIC_USER:$ELASTIC_PASSWORD" -H "Content-Type: application/json" "$@"
  fi
}

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required." >&2
  exit 1
fi

echo "=== Chargeback 0.6.0 Upgrade & Shard Reclamation ==="
echo "Elasticsearch: $ES_URL"
[[ "$DRY_RUN" -eq 1 ]] && echo "Mode: DRY-RUN (no cluster writes or deletions)"

echo ""
echo "--- 1. Verify 0.6.0 chargeback_lookup.* aliases ---"
REQUIRED_ALIASES=(
  billing_cluster_cost
  billing_realized_pool
  billing_credits
  chargeback_conf
  cluster_deployment_contribution
  cluster_datastream_contribution
  cluster_tier_contribution
  cluster_tier_and_datastream_contribution
)

MISSING_ALIASES=()
for a in "${REQUIRED_ALIASES[@]}"; do
  full_alias="chargeback_lookup.${a}"
  alias_json=$(curl_es "$ES_URL/_alias/$full_alias" 2>/dev/null || true)
  backing=$(echo "$alias_json" | jq -r 'keys[0] // empty' 2>/dev/null || true)
  if [[ -z "$backing" || "$backing" == "error" || "$backing" == "status" ]]; then
    echo "  $full_alias: MISSING"
    MISSING_ALIASES+=("$full_alias")
  else
    docs=$(curl_es "$ES_URL/$full_alias/_count" 2>/dev/null | jq -r '.count // 0' 2>/dev/null || echo 0)
    echo "  $full_alias -> $backing (${docs} docs)"
  fi
done

if [[ "${#MISSING_ALIASES[@]}" -gt 0 ]]; then
  echo ""
  echo "ERROR: Missing ${#MISSING_ALIASES[@]} required 0.6.0 alias(es): ${MISSING_ALIASES[*]}" >&2
  echo "Upgrade the Chargeback integration to 0.6.0 and let transforms run at least one checkpoint before running this script." >&2
  exit 1
fi

echo ""
echo "--- 2. Check legacy chargeback_conf_lookup carry-over ---"
if curl_es -o /dev/null -w '%{http_code}' "$ES_URL/chargeback_conf_lookup" | grep -q '^200$'; then
  LEGACY_DOCS=$(curl_es "$ES_URL/chargeback_conf_lookup/_search?size=20" 2>/dev/null)
  LEGACY_CANDIDATE=$(echo "$LEGACY_DOCS" | jq -c '
    (.hits.hits // [])
    | map(select(._id != "config_onprem"))
    | sort_by(if ._id == "config" then 0 else 1 end)
    | .[0]._source // empty
  ' 2>/dev/null || true)
  if [[ -n "$LEGACY_CANDIDATE" ]]; then
    DEST_CONF=$(curl_es "$ES_URL/chargeback_lookup.chargeback_conf/_doc/config" 2>/dev/null || true)
    DEST_FOUND=$(echo "$DEST_CONF" | jq -r '.found // false' 2>/dev/null || echo false)
    if [[ "$DEST_FOUND" == "true" ]]; then
      DEST_RATE=$(echo "$DEST_CONF" | jq -r '._source.conf_chargeable_unit_rate // empty' 2>/dev/null || true)
      DEST_UNIT=$(echo "$DEST_CONF" | jq -r '._source.conf_chargeable_unit_rate_unit // empty' 2>/dev/null || true)
      echo "  chargeback_lookup.chargeback_conf/_doc/config already exists (rate=${DEST_RATE:-?}, unit=${DEST_UNIT:-?})."
    else
      UPSERT_DOC=$(echo "$LEGACY_CANDIDATE" | jq -c '{
        doc: {
          config_join_key: "chargeback_config",
          conf_chargeable_unit_rate: (.conf_chargeable_unit_rate // .conf_ecu_rate // 0.85),
          conf_chargeable_unit_rate_unit: (.conf_chargeable_unit_rate_unit // .conf_ecu_rate_unit // "EUR"),
          conf_indexing_weight: (.conf_indexing_weight // 20),
          conf_query_weight: (.conf_query_weight // 20),
          conf_storage_weight: (.conf_storage_weight // 40),
          conf_utilization_memory_weight: (.conf_utilization_memory_weight // 70),
          conf_utilization_storage_weight: (.conf_utilization_storage_weight // 30),
          conf_utilization_floor: (.conf_utilization_floor // 0.10),
          conf_start_date: (.conf_start_date // 1262304000000),
          conf_end_date: (.conf_end_date // 2430518399999)
        },
        doc_as_upsert: true
      }')
      if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "  [dry-run] Would upsert legacy chargeback_conf_lookup config into chargeback_lookup.chargeback_conf/_doc/config:"
        echo "  $UPSERT_DOC"
      else
        curl_es -X POST "$ES_URL/chargeback_lookup.chargeback_conf/_update/config?refresh=true" -d "$UPSERT_DOC" >/dev/null
        echo "  Migrated legacy chargeback_conf_lookup settings to chargeback_lookup.chargeback_conf/_doc/config."
      fi
    fi
  else
    echo "  Legacy chargeback_conf_lookup exists with no non-onprem docs; nothing to carry over."
  fi
else
  echo "  Legacy chargeback_conf_lookup not present (nothing to carry over)."
fi

echo ""
echo "--- 3. Reclaim shards from retired pre-0.6.0 *_lookup indices ---"
RETIRED_INDICES=(
  billing_cluster_cost_lookup
  billing_realized_pool_lookup
  cluster_deployment_contribution_lookup
  cluster_tier_contribution_lookup
  cluster_datastream_contribution_lookup
  cluster_tier_and_datastream_contribution_lookup
  chargeback_conf_lookup
)

TO_DELETE=()
TOTAL_SHARDS=0
for idx in "${RETIRED_INDICES[@]}"; do
  cat_json=$(curl_es "$ES_URL/_cat/indices/$idx?h=index,pri,rep,docs.count,store.size&format=json" 2>/dev/null || true)
  if echo "$cat_json" | jq -e 'type == "array" and length > 0' >/dev/null 2>&1; then
    pri=$(echo "$cat_json" | jq -r '.[0].pri | tonumber')
    rep=$(echo "$cat_json" | jq -r '.[0].rep | tonumber')
    docs=$(echo "$cat_json" | jq -r '.[0]["docs.count"] // "0"')
    size=$(echo "$cat_json" | jq -r '.[0]["store.size"] // "0b"')
    shards=$(( pri * (1 + rep) ))
    TOTAL_SHARDS=$(( TOTAL_SHARDS + shards ))
    TO_DELETE+=("$idx")
    echo "  $idx: ${docs} docs, ${size}, ${shards} shard(s) (${pri}p/${rep}r)"
  fi
done

if [[ "${#TO_DELETE[@]}" -eq 0 ]]; then
  echo "  No retired pre-0.6.0 concrete *_lookup indices found (cluster is already clean)."
  exit 0
fi

echo "  Total shards to reclaim: ${TOTAL_SHARDS} (cluster_capacity_utilization_lookup is preserved)."
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "  [dry-run] Would delete: ${TO_DELETE[*]}"
  exit 0
fi

if [[ "$FORCE" -ne 1 ]]; then
  read -r -p "Delete ${#TO_DELETE[@]} retired lookup index/indices to reclaim ${TOTAL_SHARDS} shard(s)? [y/N] " ans
  case "$ans" in
    y|Y|yes|YES) ;;
    *)
      echo "Aborted."
      exit 1
      ;;
  esac
fi

for idx in "${TO_DELETE[@]}"; do
  del_resp=$(curl_es -X DELETE "$ES_URL/$idx" 2>/dev/null || true)
  if echo "$del_resp" | jq -e '.acknowledged == true' >/dev/null 2>&1; then
    echo "  Deleted $idx"
  else
    echo "  WARN: Failed to delete $idx: $del_resp" >&2
  fi
done

echo ""
echo "Done. Reclaimed ${TOTAL_SHARDS} shard(s)."
