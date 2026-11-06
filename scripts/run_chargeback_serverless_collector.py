#!/usr/bin/env bash
''''exec python3 "$0" "$@" # '''
# ==============================================================================
# One-Shot CEL Collector Runner for Chargeback Serverless
#
# Simulates the Elastic Agent CEL programs locally during development and pre-EPR
# verification. Pulls live Elastic Cloud Billing and Control Plane APIs,
# harvests remote project stats using least-privileged keys, and indexes directly
# into metrics-chargeback_serverless.billing-default and metrics-chargeback_serverless.usage-default.
# ==============================================================================

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

def get_env(key, default=""):
    return os.environ.get(key, default).strip()

ORG_ID = get_env("ORGANIZATION_ID")
CLOUD_API_KEY = get_env("CLOUD_API_KEY") or get_env("USAGE_API_KEY") or get_env("BILLING_API_KEY")
ES_URL = get_env("ES_URL") or get_env("sl_url") or get_env("ES_HOST")
ES_API_KEY = get_env("ES_API_KEY") or get_env("HUB_ES_API_KEY") or get_env("HUB_KIBANA_API_KEY")
HUB_ES_API_KEY = get_env("HUB_ES_API_KEY") or ES_API_KEY

BILLING_API_BASE = get_env("BILLING_API_BASE", "https://billing.elastic-cloud.com").rstrip("/")
CLOUD_API_BASE = get_env("CLOUD_API_BASE", "https://api.elastic-cloud.com").rstrip("/")

LOOKBEHIND = int(get_env("LOOKBEHIND", "60") or 60)
CONF_RATE = float(get_env("CONF_RATE", "1.0") or 1.0)
CONF_RATE_UNIT = get_env("CONF_RATE_UNIT", "USD") or "USD"
CONF_CURRENCY_SYMBOL = get_env("CONF_CURRENCY_SYMBOL", "$") or "$"

# Project keys mapping: project_id -> api_key
PROJECT_KEYS = {}
project_keys_env = get_env("PROJECT_API_KEYS")
if project_keys_env:
    try:
        parsed = json.loads(project_keys_env)
        if isinstance(parsed, list):
            for item in parsed:
                if ":" in str(item):
                    pid, pkey = str(item).split(":", 1)
                    PROJECT_KEYS[pid.strip()] = pkey.strip()
        elif isinstance(parsed, str):
            for item in parsed.split(","):
                if ":" in item:
                    pid, pkey = item.split(":", 1)
                    PROJECT_KEYS[pid.strip()] = pkey.strip()
    except Exception:
        for item in project_keys_env.split(","):
            if ":" in item:
                pid, pkey = item.split(":", 1)
                PROJECT_KEYS[pid.strip()] = pkey.strip()

single_pid = get_env("PROJECT_ID")
single_pkey = get_env("SL_API_KEY")
if single_pid and single_pkey:
    PROJECT_KEYS[single_pid] = single_pkey

CURL_INSECURE = get_env("CURL_INSECURE", "0") == "1"
INCLUDE_HOSTED_BILLING = get_env("INCLUDE_HOSTED_BILLING", "0") == "1"


def is_serverless_sku(sku):
    if INCLUDE_HOSTED_BILLING:
        return True
    sl = (sku or "").strip().lower()
    if not sl:
        return False
    # Exclude Elastic Cloud Hosted (ECH) / Enterprise deployment SKUs
    if any(h in sl for h in [
        "cloud-enterprise",
        "cloud_enterprise",
        ".es.data",
        ".es.master",
        ".es.ml",
        ".es.coordinating",
        ".es.ingest",
        ".kibana.",
        ".apm.",
        ".appsearch.",
        ".enterprisesearch.",
        ".integrations_server.",
        ".fleet_server.",
    ]):
        return False
    return any(k in sl for k in [
        "serverless",
        "-vcu",
        "observability-",
        "security-",
        "search-",
        "elasticsearch-",
        "synthetic",
        "agent-builder",
        "inference",
    ])


SEMVER_RE = re.compile(r"^v?\d+\.\d+(?:\.\d+.*)?$")


def parse_datastream_parts(ds_name):
    ds_parts = (ds_name or "").split("-")
    if len(ds_parts) <= 1:
        return ("other", ds_name or "other", "default")
    raw_first = ds_parts[0]
    raw_last = ds_parts[-1]
    if SEMVER_RE.match(raw_last):
        # Beat or versioned index name (e.g. metricbeat-8.11.3, filebeat-7.17.22, apm-8.12.0)
        if raw_first == "metricbeat" or raw_first.startswith("metrics"):
            ds_type = "metrics"
        elif raw_first in ("filebeat", "winlogbeat", "auditbeat", "journalbeat", "functionbeat", "packetbeat") or raw_first.startswith("logs"):
            ds_type = "logs"
        elif raw_first == "heartbeat":
            ds_type = "synthetics"
        elif raw_first == "apm" or raw_first.startswith("traces"):
            ds_type = "traces"
        else:
            ds_type = "other"
        ds_dataset = "-".join(ds_parts[:-1]) if len(ds_parts) >= 2 else ds_name
        ds_namespace = "default"
        return (ds_type, ds_dataset, ds_namespace)
    else:
        ds_type = raw_first
        ds_dataset = "-".join(ds_parts[1:-1]) if len(ds_parts) >= 3 else (ds_parts[0] if len(ds_parts) == 2 else ds_name)
        ds_namespace = raw_last
        return (ds_type, ds_dataset, ds_namespace)


LOOKUP_INDEX_SCHEMAS = {
    "serverless_billing_cost_lookup": {
        "@timestamp": {"type": "date"},
        "period_start": {"type": "date"},
        "composite_key": {"type": "keyword"},
        "project_id": {"type": "keyword"},
        "project_name": {"type": "keyword"},
        "project_type": {"type": "keyword"},
        "sku": {"type": "keyword"},
        "cost_type": {"type": "keyword"},
        "cost_category": {"type": "keyword"},
        "allocation_driver": {"type": "keyword"},
        "is_allocatable": {"type": "boolean"},
        "total_ecu": {"type": "double"},
        "total_chargeable_units": {"type": "double"},
        "quantity_value": {"type": "double"},
        "conf_chargeable_unit_rate": {"type": "double"},
        "conf_chargeable_unit_rate_unit": {"type": "keyword"},
        "conf_currency_symbol": {"type": "keyword"},
    },
    "serverless_billing_realized_pool_lookup": {
        "@timestamp": {"type": "date"},
        "period_start": {"type": "date"},
        "composite_key": {"type": "keyword"},
        "project_id": {"type": "keyword"},
        "project_name": {"type": "keyword"},
        "project_type": {"type": "keyword"},
        "indexing_pool_ecu": {"type": "double"},
        "query_pool_ecu": {"type": "double"},
        "storage_pool_ecu": {"type": "double"},
        "ai_pool_ecu": {"type": "double"},
        "synthetics_pool_ecu": {"type": "double"},
        "synthetics_pings_pool_ecu": {"type": "double"},
        "synthetics_exec_pool_ecu": {"type": "double"},
        "synthetics_pings_qty": {"type": "double"},
        "synthetics_exec_qty": {"type": "double"},
        "total_allocatable_ecu": {"type": "double"},
        "unallocatable_ecu": {"type": "double"},
        "total_project_ecu": {"type": "double"},
        "conf_chargeable_unit_rate": {"type": "double"},
        "conf_chargeable_unit_rate_unit": {"type": "keyword"},
        "conf_currency_symbol": {"type": "keyword"},
    },
    "serverless_datastream_contribution_lookup": {
        "@timestamp": {"type": "date"},
        "period_start": {"type": "date"},
        "composite_key": {"type": "keyword"},
        "project_id": {"type": "keyword"},
        "project_name": {"type": "keyword"},
        "project_type": {"type": "keyword"},
        "datastream": {"type": "keyword"},
        "ds_type": {"type": "keyword"},
        "ds_dataset": {"type": "keyword"},
        "ds_namespace": {"type": "keyword"},
        "datastream_sum_indexing_time": {"type": "double"},
        "datastream_sum_query_time": {"type": "double"},
        "datastream_sum_data_set_store_size": {"type": "double"},
        "conf_chargeable_unit_rate": {"type": "double"},
        "conf_chargeable_unit_rate_unit": {"type": "keyword"},
        "conf_currency_symbol": {"type": "keyword"},
    },
    "serverless_project_contribution_lookup": {
        "@timestamp": {"type": "date"},
        "period_start": {"type": "date"},
        "composite_key": {"type": "keyword"},
        "project_id": {"type": "keyword"},
        "project_name": {"type": "keyword"},
        "project_type": {"type": "keyword"},
        "project_sum_indexing_time": {"type": "double"},
        "project_sum_query_time": {"type": "double"},
        "project_sum_data_set_store_size": {"type": "double"},
        "project_datastream_count": {"type": "long"},
    },
    "serverless_ai_model_contribution_lookup": {
        "@timestamp": {"type": "date"},
        "period_start": {"type": "date"},
        "composite_key": {"type": "keyword"},
        "project_id": {"type": "keyword"},
        "project_name": {"type": "keyword"},
        "project_type": {"type": "keyword"},
        "model_id": {"type": "keyword"},
        "task_type": {"type": "keyword"},
        "inference_count": {"type": "double"},
        "average_inference_time_ms": {"type": "double"},
        "total_inference_time_ms": {"type": "double"},
        "conf_chargeable_unit_rate": {"type": "double"},
        "conf_chargeable_unit_rate_unit": {"type": "keyword"},
        "conf_currency_symbol": {"type": "keyword"},
    },
    "serverless_synthetics_monitor_contribution_lookup": {
        "@timestamp": {"type": "date"},
        "period_start": {"type": "date"},
        "composite_key": {"type": "keyword"},
        "project_id": {"type": "keyword"},
        "project_name": {"type": "keyword"},
        "project_type": {"type": "keyword"},
        "monitor_id": {"type": "keyword"},
        "monitor_name": {"type": "keyword"},
        "monitor_type": {"type": "keyword"},
        "monitor_namespace": {"type": "keyword"},
        "check_count": {"type": "double"},
        "up_count": {"type": "double"},
        "down_count": {"type": "double"},
        "locations_count": {"type": "double"},
        "average_duration_ms": {"type": "double"},
        "conf_chargeable_unit_rate": {"type": "double"},
        "conf_chargeable_unit_rate_unit": {"type": "keyword"},
        "conf_currency_symbol": {"type": "keyword"},
    },
    "serverless_project_status_lookup": {
        "@timestamp": {"type": "date"},
        "period_start": {"type": "date"},
        "composite_key": {"type": "keyword"},
        "project_id": {"type": "keyword"},
        "project_name": {"type": "keyword"},
        "project_type": {"type": "keyword"},
        "optimized_for": {"type": "keyword"},
        "es_url": {"type": "keyword"},
        "last_status": {"type": "keyword"},
        "api_key_type": {"type": "keyword"},
        "synthetics_status": {"type": "keyword"},
        "index_count": {"type": "long"},
        "datastream_count": {"type": "long"},
        "ai_model_count": {"type": "long"},
        "synthetics_monitor_count": {"type": "long"},
        "updated_at": {"type": "date"},
        "conf_chargeable_unit_rate": {"type": "double"},
        "conf_chargeable_unit_rate_unit": {"type": "keyword"},
        "conf_currency_symbol": {"type": "keyword"},
    },
}


def ensure_lookup_index(es_url, api_key, index_name, properties=None):
    base = es_url.rstrip("/")
    hdrs = {"Authorization": f"ApiKey {api_key}", "Content-Type": "application/json"}
    props = properties or LOOKUP_INDEX_SCHEMAS.get(index_name, {})
    # Install persistent index template so transform resets always recreate in mode: lookup
    tmpl_body = json.dumps({
        "index_patterns": [index_name],
        "priority": 500,
        "template": {
            "settings": {"index": {"mode": "lookup"}},
            "mappings": {"properties": props}
        }
    })
    try:
        req = urllib.request.Request(f"{base}/_index_template/chargeback_serverless_{index_name}", data=tmpl_body.encode("utf-8"), headers=hdrs, method="PUT")
        with urllib.request.urlopen(req, timeout=10, context=create_ssl_context(base)):
            pass
    except Exception:
        pass
    try:
        _, info = http_get(f"{base}/{index_name}", hdrs, timeout=10, retries=1)
        idx_obj = (info or {}).get(index_name) or next(iter((info or {}).values()), {})
        mode = (((idx_obj.get("settings") or {}).get("index") or {}).get("mode")) or ""
        if mode == "lookup":
            return
        # Recreate if existing index is not in lookup mode
        req = urllib.request.Request(f"{base}/{index_name}", headers=hdrs, method="DELETE")
        with urllib.request.urlopen(req, timeout=10, context=create_ssl_context(base)):
            pass
    except Exception:
        pass
    body = json.dumps({
        "settings": {"index": {"mode": "lookup"}},
        "mappings": {"properties": props}
    })
    try:
        req = urllib.request.Request(f"{base}/{index_name}", data=body.encode("utf-8"), headers=hdrs, method="PUT")
        with urllib.request.urlopen(req, timeout=15, context=create_ssl_context(base)):
            pass
    except Exception:
        pass


def ensure_all_lookup_indices(es_url, api_key):
    for idx_name, props in LOOKUP_INDEX_SCHEMAS.items():
        ensure_lookup_index(es_url, api_key, idx_name, props)

def create_ssl_context(target_url=""):
    if CURL_INSECURE or "localhost" in target_url or "127.0.0.1" in target_url:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None

def http_get(url, headers=None, timeout=30, retries=5):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            ctx = create_ssl_context(url)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.getcode(), json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise e
        except Exception as e:
            last_err = e
            time.sleep(1.0)
    raise last_err

def http_post_bulk(url, headers, bulk_body_str, timeout=60, retries=5):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=bulk_body_str.encode("utf-8"), headers=headers)
            ctx = create_ssl_context(url)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.getcode(), json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise e
        except Exception as e:
            last_err = e
            time.sleep(1.0)
    raise last_err

def classify_sku(sku, kind, item_type):
    s = (sku or "")
    if not s:
        return ("other", "other", False, "none")

    if "synthetic" in s:
        return ("synthetics", "platform", True, "synthetics")
    if "agent-builder" in s:
        return ("agent-builder", "platform", True, "ai")

    # Ingest / indexing VCU (any project type, current or future)
    if any(x in s for x in ["-ingest-vcu_", "ingest-vcu", "-ingest-vcu"]):
        return ("ingest-vcu", "data_tier", True, "indexing")
    # Search / query VCU (any project type, current or future)
    if any(x in s for x in ["-search-vcu_", "search-vcu", "-search-vcu"]):
        return ("search-vcu", "data_tier", True, "query")
    # ML / AI VCU (any project type, current or future)
    if any(x in s for x in ["-ml-vcu_", "ml-vcu", "-ml-vcu"]):
        return ("ml-vcu", "platform", True, "ai")
    # Retained storage (any project type, current or future)
    if any(x in s for x in ["retained", "storage", "retention"]):
        return ("retained", "data_tier", True, "storage")
    # Solution-specific ingest/data tier
    if any(x in s for x in ["ingest", "logs", "metrics", "traces", "apm", "siem"]):
        return ("ingest", "data_tier", True, "indexing")

    if s.startswith("serverless.support-"):
        return ("serverless_premium_support", "support", False, "none")
    if any(x in s for x in ["data-transfer", "data-out", "egress"]):
        return ("data-transfer", "transfer", False, "none")
    if "snapshot" in s:
        return ("snapshots", "snapshot", False, "none")
    if any(x in s for x in ["inference", "llm"]):
        return ("inference", "platform", True, "ai")
    if "workflow" in s:
        return ("workflows", "platform", False, "none")
    if "cross-project" in s or "cps" in s:
        return ("cross-project-search", "platform", False, "none")

    return ("other", "other", False, "none")

DS_INDEX_RE = re.compile(
    r"^(?:partial-)?(?:restored-)?(?:shrink-.{4}-)?(?:\.ds-)?([a-zA-Z_0-9\-.]+?)(?:-\d{4}\.\d{2}(?:\.\d{2})?)?(?:-\d+)?$"
)

def delete_existing_window(es_url, api_key, target_stream, from_iso, to_iso):
    url = f"{es_url.rstrip('/')}/{target_stream}/_delete_by_query?conflicts=proceed&refresh=true"
    headers = {
        "Authorization": f"ApiKey {api_key}",
        "Content-Type": "application/json"
    }
    body = json.dumps({
        "query": {
            "range": {
                "@timestamp": {
                    "gte": from_iso,
                    "lte": to_iso
                }
            }
        }
    })
    try:
        http_post_bulk(url, headers, body, timeout=30)
    except Exception:
        pass

def main():
    print("=================================================================")
    print("       Chargeback Serverless Local CEL Collection Pass           ")
    print("=================================================================")

    if not ORG_ID or not CLOUD_API_KEY or not ES_URL or not ES_API_KEY:
        print("ERROR: Required environment variables missing (ORGANIZATION_ID, CLOUD_API_KEY, ES_URL, ES_API_KEY).", file=sys.stderr)
        sys.exit(1)

    cloud_headers = {
        "Authorization": f"ApiKey {CLOUD_API_KEY}",
        "Content-Type": "application/json"
    }

    es_headers = {
        "Authorization": f"ApiKey {ES_API_KEY}",
        "Content-Type": "application/x-ndjson"
    }

    # 1. Discover Serverless Projects
    print("\n>>> Step 1: Discovering Serverless projects from Control Plane...")
    projects = []
    seen_project_ids = set()
    custom_types = [t.strip() for t in get_env("PROJECT_TYPES", "").split(",") if t.strip()]
    project_types = list(dict.fromkeys(["elasticsearch", "observability", "security", "search"] + custom_types))

    # Also try generic /api/v1/serverless/projects if supported by the control plane
    try:
        generic_url = f"{CLOUD_API_BASE}/api/v1/serverless/projects"
        status, data = http_get(generic_url, cloud_headers)
        items = data.get("items", [])
        for item in items:
            ptype = item.get("type") or item.get("project_type") or "serverless"
            item["project_type"] = ptype
            pid = item.get("id")
            if pid and pid not in seen_project_ids:
                seen_project_ids.add(pid)
                projects.append(item)
            if ptype not in project_types:
                project_types.append(ptype)
        if items:
            print(f"  • Discovered {len(items)} project(s) via generic endpoint")
    except Exception:
        pass

    for ptype in project_types:
        url = f"{CLOUD_API_BASE}/api/v1/serverless/projects/{ptype}"
        try:
            status, data = http_get(url, cloud_headers)
            items = data.get("items", [])
            for item in items:
                item["project_type"] = ptype
                pid = item.get("id")
                if pid and pid not in seen_project_ids:
                    seen_project_ids.add(pid)
                    projects.append(item)
            print(f"  • Discovered {len(items)} {ptype} project(s)")
        except Exception as e:
            # Endpoint may return 404 for project types not active on the cluster, which is normal
            pass

    project_map = {p["id"]: p for p in projects}
    active_project_ids = set(project_map.keys())
    active_project_map = project_map
    print(f"  Total projects discovered across org: {len(projects)}")

    # 2. Collect Historical Billing Metrics (Day-by-Day, single figure per day per project+sku)
    now = datetime.now(timezone.utc)
    print(f"\n>>> Step 2: Collecting daily Billing metrics (last {LOOKBEHIND} days up to yesterday)...")

    # Exclude today: only collect completed UTC days up to yesterday
    yesterday = (now - timedelta(days=1)).date()
    end_date = yesterday
    start_date = end_date - timedelta(days=LOOKBEHIND - 1)
    today_start_iso = now.strftime("%Y-%m-%dT00:00:00.000Z")

    print(f"  Window: {start_date} to {end_date} (inclusive, {LOOKBEHIND} days; excluding today {now.date()})")

    # Verify whether CLOUD_API_KEY has Organization Billing Admin / Owner privileges.
    # Without Billing Admin or Organization Owner role, Elastic Cloud Billing v2 API
    # (/costs/instances and /costs/items) filters results to only currently active,
    # accessible instances and silently omits deleted projects/deployments.
    try:
        http_get(f"{BILLING_API_BASE}/api/v1/billing/organization/{ORG_ID}/history", cloud_headers, timeout=10)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print(
                "  ⚠ WARNING: CLOUD_API_KEY does not have the Organization 'Billing Admin' (or 'Organization Owner') role.\n"
                "    Elastic Cloud Billing API filters /costs/instances and /costs/items to only currently active, accessible\n"
                "    projects for non-Billing-Admin keys — deleted projects and deployments are hidden by Elastic Cloud!\n"
                "    Grant 'Billing Admin' or 'Organization Owner' to your Cloud API key in Cloud Console -> Organization -> API keys."
            )
    except Exception:
        pass

    billing_docs = []
    latest_billing_instances = []
    all_known_projects = {
        p["id"]: {
            "id": p["id"],
            "name": p.get("name") or p["id"],
            "project_type": p.get("project_type", "elasticsearch"),
        }
        for p in projects
        if p.get("id")
    }
    day_emitted_projects = {}

    # Pre-scan existing Elasticsearch metrics/lookup indices before deleting the window
    # so deleted/decommissioned Serverless projects and their historical billing/usage records are preserved,
    # while purging any legacy Elastic Cloud Hosted (ECH) deployment records unless INCLUDE_HOSTED_BILLING=1.
    preserved_deleted_billing = {}  # (day_str, pid, sku) -> doc
    preserved_deleted_usage = []
    hosted_project_ids = set()
    search_hdrs = {"Authorization": f"ApiKey {ES_API_KEY}", "Content-Type": "application/json"}

    if not INCLUDE_HOSTED_BILLING:
        try:
            _, sku_agg_resp = http_post_bulk(
                f"{ES_URL.rstrip('/')}/metrics-chargeback_serverless.billing-*/_search",
                search_hdrs,
                json.dumps({
                    "size": 0,
                    "aggs": {
                        "by_proj": {
                            "terms": {"field": "project_id", "size": 500},
                            "aggs": {"skus": {"terms": {"field": "sku", "size": 50}}}
                        }
                    }
                }),
                timeout=15
            )
            for b in ((sku_agg_resp.get("aggregations") or {}).get("by_proj") or {}).get("buckets") or []:
                pid = b.get("key")
                if not pid or pid in active_project_ids:
                    continue
                skus = [sb.get("key") for sb in ((b.get("skus") or {}).get("buckets") or []) if sb.get("key") and sb.get("key") != "none"]
                if skus and not any(is_serverless_sku(s) for s in skus):
                    hosted_project_ids.add(pid)
            if hosted_project_ids:
                print(f"  • Excluding and purging {len(hosted_project_ids)} non-Serverless Hosted deployment(s) from Chargeback Serverless indices")
                del_q = json.dumps({"query": {"terms": {"project_id": list(hosted_project_ids)}}})
                for target_idx in [
                    "metrics-chargeback_serverless.billing-default",
                    "metrics-chargeback_serverless.usage-default",
                    "serverless_billing_cost_lookup",
                    "serverless_billing_realized_pool_lookup",
                    "serverless_datastream_contribution_lookup",
                    "serverless_project_contribution_lookup",
                    "serverless_ai_model_contribution_lookup",
                    "serverless_synthetics_monitor_contribution_lookup",
                    "serverless_project_status_lookup",
                ]:
                    try:
                        http_post_bulk(f"{ES_URL.rstrip('/')}/{target_idx}/_delete_by_query?conflicts=proceed&refresh=true", search_hdrs, del_q, timeout=20)
                    except Exception:
                        pass
        except Exception:
            pass

    for idx_pat in ["serverless_project_status_lookup", "metrics-chargeback_serverless.billing-*", "metrics-chargeback_serverless.usage-*"]:
        try:
            _, agg_resp = http_post_bulk(
                f"{ES_URL.rstrip('/')}/{idx_pat}/_search",
                search_hdrs,
                json.dumps({
                    "size": 0,
                    "aggs": {
                        "by_proj": {
                            "terms": {"field": "project_id", "size": 500},
                            "aggs": {
                                "names": {"terms": {"field": "project_name", "size": 5}},
                                "types": {"terms": {"field": "project_type", "size": 2}}
                            }
                        }
                    }
                }),
                timeout=15
            )
            for b in ((agg_resp.get("aggregations") or {}).get("by_proj") or {}).get("buckets") or []:
                pid = b.get("key")
                if not pid or pid in hosted_project_ids:
                    continue
                nb = ((b.get("names") or {}).get("buckets") or [])
                tb = ((b.get("types") or {}).get("buckets") or [])
                pname = next((x["key"] for x in nb if x.get("key") and x["key"] != pid), pid)
                ptype = tb[0]["key"] if tb and tb[0].get("key") else "elasticsearch"
                if pid not in all_known_projects:
                    all_known_projects[pid] = {"id": pid, "name": pname, "project_type": ptype}
                elif pname != pid and all_known_projects[pid].get("name") == pid:
                    all_known_projects[pid]["name"] = pname
        except Exception:
            pass

    inactive_known_ids = [pid for pid in all_known_projects if pid not in active_project_ids and pid not in hosted_project_ids]
    if inactive_known_ids:
        try:
            _, old_b_resp = http_post_bulk(
                f"{ES_URL.rstrip('/')}/metrics-chargeback_serverless.billing-*/_search",
                search_hdrs,
                json.dumps({
                    "size": 5000,
                    "query": {
                        "bool": {
                            "filter": [
                                {"terms": {"project_id": inactive_known_ids}},
                                {"range": {"@timestamp": {"gte": f"{start_date.strftime('%Y-%m-%d')}T00:00:00.000Z", "lte": f"{end_date.strftime('%Y-%m-%d')}T23:59:59.999Z"}}}
                            ],
                            "must_not": [{"term": {"sku": "none"}}]
                        }
                    }
                }),
                timeout=15
            )
            for h in (old_b_resp.get("hits") or {}).get("hits") or []:
                src = h.get("_source") or {}
                d_str = (src.get("@timestamp") or "")[:10]
                pid = src.get("project_id")
                sku = src.get("sku")
                if d_str and pid and sku and is_serverless_sku(sku):
                    preserved_deleted_billing[(d_str, pid, sku)] = src
        except Exception:
            pass
        try:
            _, old_u_resp = http_post_bulk(
                f"{ES_URL.rstrip('/')}/metrics-chargeback_serverless.usage-*/_search",
                search_hdrs,
                json.dumps({
                    "size": 5000,
                    "query": {
                        "bool": {
                            "filter": [
                                {"terms": {"project_id": inactive_known_ids}},
                                {"range": {"@timestamp": {"gte": f"{start_date.strftime('%Y-%m-%d')}T00:00:00.000Z", "lte": f"{end_date.strftime('%Y-%m-%d')}T23:59:59.999Z"}}}
                            ],
                            "must_not": [{"term": {"doc_type": "project_status"}}]
                        }
                    }
                }),
                timeout=15
            )
            for h in (old_u_resp.get("hits") or {}).get("hits") or []:
                src = h.get("_source") or {}
                if src.get("project_id") and src.get("project_id") not in hosted_project_ids:
                    preserved_deleted_usage.append(src)
        except Exception:
            pass

    current_date = start_date
    while current_date <= end_date:
        day_start = datetime(current_date.year, current_date.month, current_date.day, 0, 0, 0, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        day_from_str = day_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        day_to_str = day_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        day_timestamp = day_start.strftime("%Y-%m-%dT00:00:00.000Z")
        day_str = current_date.strftime("%Y-%m-%d")
        emitted_set = day_emitted_projects.setdefault(day_str, set())

        billing_url = f"{BILLING_API_BASE}/api/v2/billing/organizations/{ORG_ID}/costs/instances?from={urllib.parse.quote(day_from_str)}&to={urllib.parse.quote(day_to_str)}&include_names=true"
        items_url = f"{BILLING_API_BASE}/api/v2/billing/organizations/{ORG_ID}/costs/items?from={urllib.parse.quote(day_from_str)}&to={urllib.parse.quote(day_to_str)}"
        try:
            status, bdata = http_get(billing_url, cloud_headers)
            raw_instances = bdata.get("instances", []) or []
            day_instances = []
            for inst in raw_instances:
                inst_id = inst.get("id")
                itype = (inst.get("type") or "").lower()
                if itype in [
                    "kibana", "enterprise_search", "integrations_server", "appsearch", "fleet_server", "apm"
                ]:
                    continue
                if inst_id and inst_id in active_project_ids:
                    day_instances.append(inst)
                    continue
                inst_items = inst.get("product_line_items") or []
                serverless_items = [li for li in inst_items if is_serverless_sku(li.get("sku", ""))]
                if serverless_items:
                    inst_copy = dict(inst)
                    inst_copy["product_line_items"] = serverless_items
                    day_instances.append(inst_copy)
                    continue
                # If an instance has 0 line items, only keep it if it is explicitly a serverless project type
                if not inst_items and itype in ["serverless", "observability", "security", "search"] and inst_id and inst_id not in hosted_project_ids:
                    day_instances.append(inst)

            if day_instances:
                latest_billing_instances = day_instances

            day_line_count = 0
            day_sku_ecu = {}
            day_sku_qty = {}
            for inst in day_instances:
                raw_id = inst.get("id")
                proj = active_project_map.get(raw_id, {}) if raw_id else {}

                # Infer project_type from active project, instance type, or product line item SKUs
                ptype = proj.get("project_type") or inst.get("type")
                if not ptype or ptype in ["serverless", "deployment"]:
                    skus = " ".join((li.get("sku") or "").lower() for li in (inst.get("product_line_items") or []))
                    if "observability." in skus or "observability-" in skus:
                        ptype = "observability"
                    elif "security." in skus or "security-" in skus:
                        ptype = "security"
                    elif "search." in skus or "search-" in skus:
                        ptype = "search"
                    elif "elasticsearch." in skus or "elasticsearch-" in skus or "-vcu" in skus:
                        ptype = "elasticsearch"
                    else:
                        ptype = ptype or "elasticsearch"

                inst_id = str(raw_id).strip() if raw_id else f"unassociated_{ptype}"
                inst_name = inst.get("name") or (inst_id if raw_id else f"Unassociated ({ptype})")
                proj_name = proj.get("name")
                pname = proj_name if (proj_name and proj_name != inst_id) else (inst_name or proj_name or inst_id)

                existing_known = all_known_projects.get(inst_id)
                if not existing_known:
                    all_known_projects[inst_id] = {"id": inst_id, "name": pname, "project_type": ptype}
                elif pname and pname != inst_id and existing_known.get("name") == inst_id:
                    existing_known["name"] = pname
                    existing_known["project_type"] = ptype

                line_items = inst.get("product_line_items", []) or []
                for li in line_items:
                    sku = li.get("sku", "")
                    if not is_serverless_sku(sku):
                        continue
                    kind = li.get("kind", "")
                    itype = li.get("type", "")
                    total_ecu = float(li.get("total_ecu") or 0.0)
                    qty = li.get("quantity") or {}
                    qty_val = float(qty.get("value") or 0.0) if isinstance(qty, dict) else 0.0

                    cost_type, cost_cat, is_alloc, driver = classify_sku(sku, kind, itype)

                    doc = {
                        "@timestamp": day_timestamp,
                        "period_start": day_timestamp,
                        "event": {"ingested": (now - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                        "composite_key": f"{day_str}_{inst_id}",
                        "project_id": inst_id,
                        "project_name": pname,
                        "project_type": ptype,
                        "sku": sku,
                        "total_ecu": total_ecu,
                        "total_chargeable_units": total_ecu,
                        "quantity_value": qty_val,
                        "cost_type": cost_type,
                        "cost_category": cost_cat,
                        "is_allocatable": is_alloc,
                        "allocation_driver": driver,
                        "conf_chargeable_unit_rate": CONF_RATE,
                        "conf_chargeable_unit_rate_unit": CONF_RATE_UNIT,
                        "conf_currency_symbol": CONF_CURRENCY_SYMBOL
                    }
                    billing_docs.append(doc)
                    emitted_set.add(inst_id)
                    day_sku_ecu[sku] = day_sku_ecu.get(sku, 0.0) + total_ecu
                    day_sku_qty[sku] = day_sku_qty.get(sku, 0.0) + qty_val
                    day_line_count += 1

            # Reconcile against /costs/items so any RBAC-filtered deleted Serverless project or org-level Serverless line items are captured
            try:
                _, items_data = http_get(items_url, cloud_headers)
                for prod in items_data.get("products", []) or []:
                    prod_type = prod.get("type") or "elasticsearch"
                    if prod_type in ["serverless", "deployment"]:
                        prod_type = "elasticsearch"
                    for li in prod.get("product_line_items", []) or []:
                        sku = li.get("sku", "")
                        if not sku or not is_serverless_sku(sku):
                            continue
                        item_ecu = float(li.get("total_ecu") or 0.0)
                        qty = li.get("quantity") or {}
                        item_qty = float(qty.get("value") or 0.0) if isinstance(qty, dict) else 0.0
                        diff_ecu = round(item_ecu - day_sku_ecu.get(sku, 0.0), 6)
                        diff_qty = max(0.0, round(item_qty - day_sku_qty.get(sku, 0.0), 6))
                        if diff_ecu > 1e-4 or (sku not in day_sku_ecu and item_qty > 0):
                            # Check if there is a known inactive/deleted Serverless project of this product type
                            cand_pids = [
                                kp_id for kp_id, kp_info in all_known_projects.items()
                                if kp_id not in active_project_ids and kp_info.get("project_type") == prod_type
                            ]
                            if len(cand_pids) == 1:
                                target_pid = cand_pids[0]
                                target_pname = all_known_projects[target_pid].get("name") or target_pid
                            else:
                                target_pid = f"deleted_or_unassociated_{prod_type}"
                                target_pname = f"Deleted / Unassociated ({prod_type})"
                                all_known_projects.setdefault(target_pid, {
                                    "id": target_pid,
                                    "name": target_pname,
                                    "project_type": prod_type,
                                })
                            cost_type, cost_cat, is_alloc, driver = classify_sku(sku, li.get("kind", ""), li.get("type", ""))
                            billing_docs.append({
                                "@timestamp": day_timestamp,
                                "period_start": day_timestamp,
                                "event": {"ingested": (now - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                                "composite_key": f"{day_str}_{target_pid}",
                                "project_id": target_pid,
                                "project_name": target_pname,
                                "project_type": prod_type,
                                "sku": sku,
                                "total_ecu": max(0.0, diff_ecu),
                                "total_chargeable_units": max(0.0, diff_ecu),
                                "quantity_value": diff_qty,
                                "cost_type": cost_type,
                                "cost_category": cost_cat,
                                "is_allocatable": is_alloc,
                                "allocation_driver": driver,
                                "conf_chargeable_unit_rate": CONF_RATE,
                                "conf_chargeable_unit_rate_unit": CONF_RATE_UNIT,
                                "conf_currency_symbol": CONF_CURRENCY_SYMBOL,
                            })
                            emitted_set.add(target_pid)
                            day_sku_ecu[sku] = day_sku_ecu.get(sku, 0.0) + max(0.0, diff_ecu)
                            day_line_count += 1
            except Exception:
                pass

            # Re-attach any preserved historical non-zero billing records for deleted projects on this day
            api_emitted_pids = set(emitted_set)
            for (p_day, p_pid, p_sku), p_doc in preserved_deleted_billing.items():
                if p_day == day_str and p_pid not in api_emitted_pids:
                    p_doc_copy = dict(p_doc)
                    p_doc_copy["event"] = {"ingested": (now - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")}
                    billing_docs.append(p_doc_copy)
                    emitted_set.add(p_pid)
                    day_line_count += 1

            if day_instances or day_line_count:
                print(f"  • {day_str}: {len(day_instances)} project instance(s), {day_line_count} line items")
        except Exception as e:
            print(f"  ⚠ Failed to fetch billing instances for {current_date}: {e}")

        current_date += timedelta(days=1)

    # Emit 0-ECU placeholder rows for discovered or inactive/decommissioned projects on days without billing line items
    fill_date = start_date
    while fill_date <= end_date:
        day_str = fill_date.strftime("%Y-%m-%d")
        day_timestamp = f"{day_str}T00:00:00.000Z"
        emitted_set = day_emitted_projects.get(day_str, set())
        for pid, pinfo in all_known_projects.items():
            if pid not in emitted_set:
                pname = pinfo.get("name") or pid
                ptype = pinfo.get("project_type") or "elasticsearch"
                billing_docs.append({
                    "@timestamp": day_timestamp,
                    "period_start": day_timestamp,
                    "event": {"ingested": (now - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                    "composite_key": f"{day_str}_{pid}",
                    "project_id": pid,
                    "project_name": pname,
                    "project_type": ptype,
                    "sku": "none",
                    "total_ecu": 0.0,
                    "total_chargeable_units": 0.0,
                    "quantity_value": 0.0,
                    "cost_type": "other",
                    "cost_category": "other",
                    "is_allocatable": False,
                    "allocation_driver": "none",
                    "conf_chargeable_unit_rate": CONF_RATE,
                    "conf_chargeable_unit_rate_unit": CONF_RATE_UNIT,
                    "conf_currency_symbol": CONF_CURRENCY_SYMBOL,
                })
        fill_date += timedelta(days=1)

    billing_docs.sort(key=lambda d: (d["@timestamp"], d["project_id"], d["sku"]))

    # Purge any records of today or future from target indices, plus any error records
    delete_existing_window(ES_URL, ES_API_KEY, "metrics-chargeback_serverless.billing-default", today_start_iso, "now+1d")
    delete_existing_window(ES_URL, ES_API_KEY, "metrics-chargeback_serverless.usage-default", today_start_iso, "now+1d")
    try:
        http_post_bulk(
            f"{ES_URL.rstrip('/')}/metrics-chargeback_serverless.billing-default/_delete_by_query?conflicts=proceed&refresh=true",
            es_headers,
            json.dumps({"query": {"exists": {"field": "error.code"}}}),
            timeout=30
        )
    except Exception:
        pass
    for lk_idx in [
        "serverless_billing_cost_lookup",
        "serverless_billing_realized_pool_lookup",
        "serverless_datastream_contribution_lookup",
        "serverless_project_contribution_lookup",
        "serverless_ai_model_contribution_lookup",
        "serverless_synthetics_monitor_contribution_lookup",
        "serverless_project_status_lookup"
    ]:
        delete_existing_window(ES_URL, ES_API_KEY, lk_idx, today_start_iso, "now+1d")

    # Detect whether continuous Fleet transforms exist so we don't create duplicate _id docs in lookup indices
    active_transforms = []
    try:
        _, tf_resp = http_get(f"{ES_URL.rstrip('/')}/_transform/logs-chargeback_serverless.*", {"Authorization": f"ApiKey {ES_API_KEY}"})
        active_transforms = [t.get("id") for t in (tf_resp.get("transforms") or []) if t.get("id")]
    except Exception:
        active_transforms = []
    has_billing_cost_tf = any("billing_cost" in tid for tid in active_transforms)
    has_proj_status_tf = any("project_status" in tid or "proj_status" in tid for tid in active_transforms)

    if billing_docs:
        min_ts = billing_docs[0]["@timestamp"]
        max_ts = billing_docs[-1]["@timestamp"]
        delete_existing_window(ES_URL, ES_API_KEY, "metrics-chargeback_serverless.billing-default", min_ts, max_ts)
        delete_existing_window(ES_URL, ES_API_KEY, "serverless_billing_cost_lookup", min_ts, max_ts)
        delete_existing_window(ES_URL, ES_API_KEY, "serverless_billing_realized_pool_lookup", min_ts, max_ts)
        print(f"  • Ingesting {len(billing_docs)} daily billing metric documents into metrics-chargeback_serverless.billing-default...")
        bulk_lines = []
        for d in billing_docs:
            bulk_lines.append(json.dumps({"create": {}}))
            bulk_lines.append(json.dumps(d))
        bulk_payload = "\n".join(bulk_lines) + "\n"

        target_es_bulk = f"{ES_URL.rstrip('/')}/metrics-chargeback_serverless.billing-default/_bulk"
        try:
            status, bulk_resp = http_post_bulk(target_es_bulk, es_headers, bulk_payload)
            errors = bulk_resp.get("errors", False)
            if errors:
                err_items = [item for item in bulk_resp.get("items", []) if "error" in item.get("create", {})]
                print(f"  ⚠ Bulk index reported {len(err_items)} errors: {err_items[:1]}")
            else:
                print(f"  ✓ Successfully indexed {len(billing_docs)} daily billing records into metrics stream.")
        except Exception as e:
            print(f"  ⚠ Failed to bulk index billing metrics: {e}")

        # Only direct-write to serverless_billing_cost_lookup when the continuous Fleet transform is not installed
        if not has_billing_cost_tf:
            ensure_lookup_index(ES_URL, ES_API_KEY, "serverless_billing_cost_lookup", {
                "@timestamp": {"type": "date"},
                "period_start": {"type": "date"},
                "composite_key": {"type": "keyword"},
                "project_id": {"type": "keyword"},
                "project_name": {"type": "keyword"},
                "project_type": {"type": "keyword"},
                "sku": {"type": "keyword"},
                "cost_type": {"type": "keyword"},
                "cost_category": {"type": "keyword"},
                "allocation_driver": {"type": "keyword"},
                "is_allocatable": {"type": "boolean"},
                "total_ecu": {"type": "double"},
                "total_chargeable_units": {"type": "double"},
                "quantity_value": {"type": "double"},
                "conf_chargeable_unit_rate": {"type": "double"},
                "conf_chargeable_unit_rate_unit": {"type": "keyword"},
                "conf_currency_symbol": {"type": "keyword"},
            })
            print(f"  • Ingesting {len(billing_docs)} daily billing records directly into serverless_billing_cost_lookup (no transform detected)...")
            lk_bulk_lines = []
            for d in billing_docs:
                doc_id = f"{d['composite_key']}_{d['sku']}"
                lk_bulk_lines.append(json.dumps({"index": {"_id": doc_id}}))
                lk_bulk_lines.append(json.dumps(d))
            lk_bulk_payload = "\n".join(lk_bulk_lines) + "\n"
            target_lk_bulk = f"{ES_URL.rstrip('/')}/serverless_billing_cost_lookup/_bulk"
            try:
                status, bulk_resp = http_post_bulk(target_lk_bulk, es_headers, lk_bulk_payload)
                errors = bulk_resp.get("errors", False)
                if errors:
                    err_items = [item for item in bulk_resp.get("items", []) if "error" in item.get("index", {})]
                    print(f"  ⚠ Bulk index to serverless_billing_cost_lookup reported {len(err_items)} errors: {err_items[:1]}")
                else:
                    print(f"  ✓ Successfully indexed {len(billing_docs)} daily billing records into serverless_billing_cost_lookup.")
            except Exception as e:
                print(f"  ⚠ Failed to direct-index into serverless_billing_cost_lookup: {e}")
    else:
        print("  Notice: No Serverless billing line items found for the given window.")

    # 3. Harvest Project Usage & AI Metrics
    print("\n>>> Step 3: Harvesting granular usage metrics across Serverless projects...")

    u_rate = CONF_RATE
    u_unit = CONF_RATE_UNIT
    u_sym = CONF_CURRENCY_SYMBOL
    if billing_docs:
        b_unit = billing_docs[0].get("conf_chargeable_unit_rate_unit")
        if b_unit and (CONF_RATE_UNIT == "USD" or not get_env("CONF_RATE_UNIT")):
            u_unit = b_unit
            u_sym = billing_docs[0].get("conf_currency_symbol", u_sym)
            u_rate = billing_docs[0].get("conf_chargeable_unit_rate", u_rate)

    # Calculate latest daily realized pool per project
    pools = {}
    for inst in latest_billing_instances:
        pid = inst.get("id")
        idx_e = qry_e = stg_e = ai_e = 0.0
        for li in inst.get("product_line_items", []):
            sku = (li.get("sku") or "").lower()
            ecu = float(li.get("total_ecu") or 0.0)
            if "ingest" in sku or "indexing" in sku:
                idx_e += ecu
            elif "search" in sku or "query" in sku:
                qry_e += ecu
            elif "storage" in sku or "retained" in sku:
                stg_e += ecu
            elif "ml" in sku or "inference" in sku or "llm" in sku or "agent-builder" in sku:
                ai_e += ecu
        pools[pid] = {
            "indexing_ecu": idx_e,
            "query_ecu": qry_e,
            "storage_ecu": stg_e,
            "ai_ecu": ai_e,
            "total_ecu": idx_e + qry_e + stg_e + ai_e
        }

    # Pre-fetch any existing Fleet Elastic Agent 'ok' harvests from metrics-chargeback_serverless.usage-*
    # so keys configured in Fleet UI are preserved across the historical backfill window.
    agent_harvest_cache = {}
    try:
        search_url = f"{ES_URL.rstrip('/')}/metrics-chargeback_serverless.usage-*/_search"
        search_hdrs = {"Authorization": f"ApiKey {ES_API_KEY}", "Content-Type": "application/json"}
        _, ps_resp = http_post_bulk(search_url, search_hdrs, json.dumps({
            "size": 100,
            "sort": [{"updated_at": {"order": "desc", "unmapped_type": "date"}}, {"@timestamp": {"order": "desc"}}],
            "query": {"bool": {"filter": [{"term": {"doc_type": "project_status"}}, {"term": {"last_status": "ok"}}]}}
        }), timeout=15)
        for h in (ps_resp.get("hits") or {}).get("hits") or []:
            src = h.get("_source") or {}
            h_pid = src.get("project_id")
            h_ts = src.get("@timestamp")
            h_ing = (src.get("event") or {}).get("ingested") or ""
            if h_pid and h_pid not in agent_harvest_cache:
                _, det_resp = http_post_bulk(search_url, search_hdrs, json.dumps({
                    "size": 2000,
                    "query": {"bool": {"filter": [{"term": {"project_id": h_pid}}, {"term": {"@timestamp": h_ts}}]}}
                }), timeout=15)
                all_det = [dh.get("_source") or {} for dh in ((det_resp.get("hits") or {}).get("hits") or [])]
                if "agent" in src:
                    all_det = [d for d in all_det if "agent" in d]
                if h_ing:
                    same_batch = [d for d in all_det if ((d.get("event") or {}).get("ingested") or "")[:16] == h_ing[:16]]
                    if same_batch:
                        all_det = same_batch
                agent_harvest_cache[h_pid] = {
                    "status_doc": src,
                    "docs": all_det,
                }
    except Exception:
        pass

    usage_docs = []
    for p in projects:
        pid = p.get("id")
        pname = p.get("name", pid)
        ptype = p.get("project_type", "elasticsearch")
        endpoints = p.get("endpoints", {})
        es_endpoint = endpoints.get("elasticsearch", "")

        pool = pools.get(pid, {
            "indexing_ecu": 0.0,
            "query_ecu": 0.0,
            "storage_ecu": 0.0,
            "ai_ecu": 0.0,
            "total_ecu": 0.0
        })

        # Target key: prefer explicit PROJECT_KEYS[pid], then CLOUD_API_KEY
        # (since a unified Elastic Cloud API key with "Cloud, Elasticsearch, and Kibana API" access can query all Serverless projects),
        # and finally HUB_ES_API_KEY on the local hub.
        is_hub = bool(ES_URL and (pid in ES_URL or (es_endpoint and es_endpoint.rstrip("/") in ES_URL.rstrip("/"))))
        candidate_keys = []
        if PROJECT_KEYS.get(pid):
            candidate_keys.append(PROJECT_KEYS[pid])
        if CLOUD_API_KEY and CLOUD_API_KEY not in candidate_keys:
            candidate_keys.append(CLOUD_API_KEY)
        if is_hub and HUB_ES_API_KEY and HUB_ES_API_KEY not in candidate_keys:
            candidate_keys.append(HUB_ES_API_KEY)

        indices = []
        harvest_status = "empty_or_unauthorized"
        target_key = None
        headers = {}
        cat_url = f"{es_endpoint.rstrip('/')}/_cat/indices?h=index,dataset.size,indexing.index_time,search.query_time&format=json&bytes=b&time=ms&expand_wildcards=open,hidden"
        for cand_key in candidate_keys:
            cand_headers = {
                "Authorization": f"ApiKey {cand_key}",
                "Content-Type": "application/json"
            }
            try:
                status, cat_data = http_get(cat_url, cand_headers, timeout=15)
                indices = cat_data if isinstance(cat_data, list) else []
                target_key = cand_key
                headers = cand_headers
                harvest_status = "ok"
                break
            except Exception:
                continue

        # Query _ml/trained_models/_stats and /_inference endpoints
        ml_models = []
        seen_model_ids = set()
        if harvest_status == "ok" and target_key:
            ml_url = f"{es_endpoint.rstrip('/')}/_ml/trained_models/_stats"
            try:
                status, ml_data = http_get(ml_url, headers, timeout=15)
                for m in (ml_data.get("trained_model_stats") or []):
                    mid = m.get("model_id")
                    if mid and mid not in seen_model_ids:
                        seen_model_ids.add(mid)
                        ml_models.append(m)
            except Exception:
                pass
            inf_url = f"{es_endpoint.rstrip('/')}/_inference"
            try:
                status, inf_data = http_get(inf_url, headers, timeout=15)
                for ep in (inf_data.get("endpoints") or []):
                    mid = ep.get("inference_id") or ep.get("model_id")
                    if mid and mid not in seen_model_ids:
                        seen_model_ids.add(mid)
                        ml_models.append({
                            "model_id": mid,
                            "task_type": ep.get("task_type") or "inference_api",
                            "pipeline_count": 0,
                            "inference_stats": {"inference_count": 0},
                            "deployment_stats": {"nodes": []},
                        })
            except Exception:
                pass

        # Query /.ds-synthetics-*-*,synthetics-*/_search across the window by day for monitor-level breakdown
        synth_monitors_by_day = {}
        all_discovered_monitors = {}
        if harvest_status == "ok" and target_key:
            syn_query = {
                "size": 0,
                "query": {
                    "bool": {
                        "filter": [
                            {"exists": {"field": "monitor.id"}},
                            {"range": {"@timestamp": {"gte": f"{start_date.strftime('%Y-%m-%d')}T00:00:00.000Z", "lt": f"{(end_date + timedelta(days=1)).strftime('%Y-%m-%d')}T00:00:00.000Z"}}}
                        ],
                        "must_not": [{"terms": {"event.dataset": ["browser.network", "browser.screenshot"]}}]
                    }
                },
                "aggs": {
                    "by_day": {
                        "date_histogram": {
                            "field": "@timestamp",
                            "calendar_interval": "1d",
                            "time_zone": "UTC",
                            "min_doc_count": 1
                        },
                        "aggs": {
                            "monitors": {
                                "terms": {"field": "monitor.id", "size": 500},
                                "aggs": {
                                    "monitor_name": {"terms": {"field": "monitor.name", "size": 1}},
                                    "monitor_type": {"terms": {"field": "monitor.type", "size": 1}},
                                    "monitor_namespace": {"terms": {"field": "data_stream.namespace", "size": 1}},
                                    "up_checks": {"filter": {"term": {"monitor.status": "up"}}},
                                    "down_checks": {"filter": {"term": {"monitor.status": "down"}}},
                                    "locations": {"cardinality": {"field": "observer.geo.name"}},
                                    "avg_duration_us": {"avg": {"field": "monitor.duration.us"}}
                                }
                            }
                        }
                    }
                }
            }
            syn_url = f"{es_endpoint.rstrip('/')}/.ds-synthetics-*-*,synthetics-*/_search?ignore_unavailable=true&expand_wildcards=open,hidden"
            try:
                status, syn_data = http_post_bulk(syn_url, headers, json.dumps(syn_query), timeout=20)
                day_buckets = ((syn_data.get("aggregations") or {}).get("by_day") or {}).get("buckets") or []
                for db in day_buckets:
                    d_key = str(db.get("key_as_string") or "")[:10]
                    if not d_key:
                        continue
                    mon_buckets = ((db.get("monitors") or {}).get("buckets") or [])
                    day_list = synth_monitors_by_day.setdefault(d_key, [])
                    for b in mon_buckets:
                        mid = str(b.get("key") or "unknown")
                        mname_b = ((b.get("monitor_name") or {}).get("buckets") or [])
                        mtype_b = ((b.get("monitor_type") or {}).get("buckets") or [])
                        mns_b = ((b.get("monitor_namespace") or {}).get("buckets") or [])
                        mname = str(mname_b[0].get("key")) if mname_b and mname_b[0].get("key") else mid
                        mtype = str(mtype_b[0].get("key")) if mtype_b and mtype_b[0].get("key") else "http"
                        mns = str(mns_b[0].get("key")) if mns_b and mns_b[0].get("key") else "default"
                        chk_cnt = int(b.get("doc_count") or 0)
                        up_cnt = int((b.get("up_checks") or {}).get("doc_count") or 0)
                        dn_cnt = int((b.get("down_checks") or {}).get("doc_count") or 0)
                        loc_cnt = int((b.get("locations") or {}).get("value") or 1)
                        avg_us = (b.get("avg_duration_us") or {}).get("value")
                        avg_ms = round(float(avg_us) / 1000.0, 2) if avg_us is not None else 0.0
                        m_entry = {
                            "monitor_id": mid,
                            "monitor_name": mname,
                            "monitor_type": mtype,
                            "monitor_namespace": mns,
                            "check_count": chk_cnt,
                            "up_count": up_cnt,
                            "down_count": dn_cnt,
                            "locations_count": loc_cnt,
                            "average_duration_ms": avg_ms
                        }
                        day_list.append(m_entry)
                        all_discovered_monitors[mid] = m_entry
            except Exception:
                pass

        # Aggregate datastreams (1 figure per day per datastream)
        ds_map = {}
        for row in indices:
            idx_name = row.get("index", "")
            if idx_name.startswith(".") and not idx_name.startswith(".ds-"):
                continue

            m_ds = DS_INDEX_RE.match(idx_name)
            ds_name = m_ds.group(1) if m_ds else idx_name

            ds_type, ds_dataset, ds_namespace = parse_datastream_parts(ds_name)

            size_b = int(row.get("dataset.size") or 0)
            idx_time = int(row.get("indexing.index_time") or 0)
            qry_time = int(row.get("search.query_time") or 0)

            if ds_name not in ds_map:
                ds_map[ds_name] = {
                    "ds": ds_name,
                    "ds_type": ds_type,
                    "ds_dataset": ds_dataset,
                    "ds_namespace": ds_namespace,
                    "size": 0,
                    "idx_time": 0,
                    "qry_time": 0
                }
            ds_map[ds_name]["size"] += size_b
            ds_map[ds_name]["idx_time"] += idx_time
            ds_map[ds_name]["qry_time"] += qry_time

        # If local key was not in env/Keychain, check if Fleet Elastic Agent already harvested this project via Fleet UI secret
        harvested_idx_count = len(indices)
        harvested_ds_count = 0
        if harvest_status != "ok" and pid in agent_harvest_cache:
            cached = agent_harvest_cache[pid]
            st_doc = cached["status_doc"]
            harvest_status = "ok"
            harvested_idx_count = int(st_doc.get("index_count") or 0)
            harvested_ds_count = int(st_doc.get("datastream_count") or 0)
            for cdoc in cached["docs"]:
                dtype = cdoc.get("doc_type")
                if dtype == "datastream":
                    raw_ds = cdoc.get("datastream", "")
                    m_ds = DS_INDEX_RE.match(raw_ds)
                    ds_name = m_ds.group(1) if m_ds else raw_ds
                    if not ds_name:
                        continue
                    p_type, p_dataset, p_ns = parse_datastream_parts(ds_name)
                    ds_type = cdoc.get("ds_type") or p_type
                    ds_dataset = cdoc.get("ds_dataset") or p_dataset
                    raw_cached_ns = cdoc.get("ds_namespace") or p_ns
                    ds_namespace = "default" if SEMVER_RE.match(str(raw_cached_ns)) else raw_cached_ns
                    if ds_name not in ds_map:
                        ds_map[ds_name] = {
                            "ds": ds_name,
                            "ds_type": ds_type,
                            "ds_dataset": ds_dataset,
                            "ds_namespace": ds_namespace,
                            "size": 0,
                            "idx_time": 0,
                            "qry_time": 0,
                        }
                    ds_map[ds_name]["size"] += int(cdoc.get("datastream_sum_data_set_store_size") or 0)
                    ds_map[ds_name]["idx_time"] += int(cdoc.get("datastream_sum_indexing_time") or 0)
                    ds_map[ds_name]["qry_time"] += int(cdoc.get("datastream_sum_query_time") or 0)
                elif dtype == "ai_model":
                    mid = cdoc.get("model_id", "unknown")
                    if mid not in seen_model_ids:
                        seen_model_ids.add(mid)
                        ml_models.append({
                            "model_id": mid,
                            "task_type": cdoc.get("task_type"),
                            "pipeline_count": 1 if cdoc.get("task_type") == "ingest_inference" else 0,
                            "inference_stats": {"inference_count": int(cdoc.get("inference_count") or 0)},
                            "deployment_stats": {"nodes": [{"average_inference_time_ms": float(cdoc.get("average_inference_time_ms") or 0.0)}]},
                        })
                elif dtype == "synthetics_monitor":
                    mid = cdoc.get("monitor_id", "unknown")
                    m_entry = {
                        "monitor_id": mid,
                        "monitor_name": cdoc.get("monitor_name", mid),
                        "monitor_type": cdoc.get("monitor_type", "http"),
                        "monitor_namespace": cdoc.get("monitor_namespace", "default"),
                        "check_count": int(cdoc.get("check_count") or 0),
                        "up_count": int(cdoc.get("up_count") or 0),
                        "down_count": int(cdoc.get("down_count") or 0),
                        "locations_count": int(cdoc.get("locations_count") or 1),
                        "average_duration_ms": float(cdoc.get("average_duration_ms") or 0.0),
                    }
                    all_discovered_monitors[mid] = m_entry
            if not harvested_idx_count:
                harvested_idx_count = len(ds_map)

        # Index daily synthetics and AI billing per day for this project
        proj_day_synth = {}
        proj_day_ai = {}
        for bdoc in billing_docs:
            if bdoc.get("project_id") == pid:
                d_ts = bdoc.get("@timestamp", "")[:10]
                sku_l = (bdoc.get("sku") or "").lower()
                if "synthetic" in sku_l:
                    entry = proj_day_synth.setdefault(d_ts, {"pings_qty": 0.0, "pings_ecu": 0.0, "exec_qty": 0.0, "exec_ecu": 0.0})
                    if "ping" in sku_l:
                        entry["pings_qty"] += float(bdoc.get("quantity_value") or 0.0)
                        entry["pings_ecu"] += float(bdoc.get("total_ecu") or 0.0)
                    else:
                        entry["exec_qty"] += float(bdoc.get("quantity_value") or 0.0)
                        entry["exec_ecu"] += float(bdoc.get("total_ecu") or 0.0)
                if bdoc.get("allocation_driver") == "ai" and float(bdoc.get("total_ecu") or 0.0) > 0:
                    proj_day_ai[d_ts] = proj_day_ai.get(d_ts, 0.0) + float(bdoc.get("total_ecu") or 0.0)

        # If project has AI billing in the window and 0 discovered models, register a fallback Serverless Inference Service model
        if proj_day_ai and not ml_models:
            ml_models.append({
                "model_id": "serverless-inference-service",
                "task_type": "inference_api",
                "pipeline_count": 0,
                "inference_stats": {"inference_count": 1},
                "deployment_stats": {"nodes": []},
            })

        ts_now = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Emit daily records across the entire window (start_date to end_date = yesterday)
        u_curr = start_date
        while u_curr <= end_date:
            usage_day_str = u_curr.strftime("%Y-%m-%d")
            usage_day_ts = f"{usage_day_str}T00:00:00.000Z"
            comp_key = f"{usage_day_str}_{pid}"

            for ds_name, v in ds_map.items():
                doc = {
                    "@timestamp": usage_day_ts,
                    "period_start": usage_day_ts,
                    "event": {"ingested": (now - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                    "composite_key": comp_key,
                    "doc_type": "datastream",
                    "project_id": pid,
                    "project_name": pname,
                    "project_type": ptype,
                    "datastream": ds_name,
                    "ds_type": v["ds_type"],
                    "ds_dataset": v.get("ds_dataset", v["ds_type"]),
                    "ds_namespace": v["ds_namespace"],
                    "datastream_sum_indexing_time": v["idx_time"],
                    "datastream_sum_query_time": v["qry_time"],
                    "datastream_sum_data_set_store_size": v["size"],
                    "conf_chargeable_unit_rate": u_rate,
                    "conf_chargeable_unit_rate_unit": u_unit,
                    "conf_currency_symbol": u_sym
                }
                usage_docs.append(doc)

            # Emit AI models
            for m in ml_models:
                mid = m.get("model_id", "unknown")
                counts = m.get("inference_stats", {})
                inf_count = int(counts.get("inference_count") or 0)
                nodes = (m.get("deployment_stats") or {}).get("nodes") or []
                avg_inf_ms = float(nodes[0].get("average_inference_time_ms") or 0.0) if nodes else 0.0
                total_inf_ms = int(inf_count * avg_inf_ms)
                task_type = m.get("task_type") or ("ingest_inference" if int(m.get("pipeline_count") or 0) > 0 else "query_inference")
                doc = {
                    "@timestamp": usage_day_ts,
                    "period_start": usage_day_ts,
                    "event": {"ingested": (now - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                    "composite_key": comp_key,
                    "doc_type": "ai_model",
                    "project_id": pid,
                    "project_name": pname,
                    "project_type": ptype,
                    "model_id": mid,
                    "task_type": task_type,
                    "inference_count": inf_count,
                    "average_inference_time_ms": avg_inf_ms,
                    "total_inference_time_ms": total_inf_ms,
                    "conf_chargeable_unit_rate": u_rate,
                    "conf_chargeable_unit_rate_unit": u_unit,
                    "conf_currency_symbol": u_sym
                }
                usage_docs.append(doc)

            # Emit Synthetics monitors for this day (exact daily harvest, or project monitors / billing SKU fallback)
            day_monitors = list(synth_monitors_by_day.get(usage_day_str, []))
            if not day_monitors and usage_day_str in proj_day_synth:
                if all_discovered_monitors:
                    day_monitors = list(all_discovered_monitors.values())
                else:
                    s_info = proj_day_synth[usage_day_str]
                    exec_runs = int(round(s_info["exec_qty"]))
                    raw_p = s_info["pings_qty"]
                    ping_runs = int(round(raw_p * 1000.0)) if (0 < raw_p < 100 and s_info["pings_ecu"] > raw_p) else int(round(raw_p))
                    if exec_runs > 0 or s_info["exec_ecu"] > 0:
                        runs_val = max(1, exec_runs)
                        day_monitors.append({
                            "monitor_id": "billing-derived-browser",
                            "monitor_name": "Browser Monitors (Billing SKU)",
                            "monitor_type": "browser",
                            "monitor_namespace": "default",
                            "check_count": runs_val,
                            "up_count": runs_val,
                            "down_count": 0,
                            "locations_count": 1,
                            "average_duration_ms": 0.0,
                        })
                    if ping_runs > 0 or s_info["pings_ecu"] > 0:
                        runs_val = max(1, ping_runs)
                        day_monitors.append({
                            "monitor_id": "billing-derived-lightweight",
                            "monitor_name": "Lightweight Monitors (Billing SKU)",
                            "monitor_type": "http",
                            "monitor_namespace": "default",
                            "check_count": runs_val,
                            "up_count": runs_val,
                            "down_count": 0,
                            "locations_count": 1,
                            "average_duration_ms": 0.0,
                        })

            for sm in day_monitors:
                doc = {
                    "@timestamp": usage_day_ts,
                    "period_start": usage_day_ts,
                    "event": {"ingested": (now - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                    "composite_key": comp_key,
                    "doc_type": "synthetics_monitor",
                    "project_id": pid,
                    "project_name": pname,
                    "project_type": ptype,
                    "monitor_id": sm["monitor_id"],
                    "monitor_name": sm["monitor_name"],
                    "monitor_type": sm["monitor_type"],
                    "monitor_namespace": sm["monitor_namespace"],
                    "check_count": sm["check_count"],
                    "up_count": sm["up_count"],
                    "down_count": sm["down_count"],
                    "locations_count": sm["locations_count"],
                    "average_duration_ms": sm["average_duration_ms"],
                    "conf_chargeable_unit_rate": u_rate,
                    "conf_chargeable_unit_rate_unit": u_unit,
                    "conf_currency_symbol": u_sym
                }
                usage_docs.append(doc)

            # Emit project status record
            if harvest_status == "ok":
                if target_key and target_key == PROJECT_KEYS.get(pid):
                    key_type_val = "remote_key"
                elif target_key and target_key == CLOUD_API_KEY:
                    key_type_val = "cloud_key"
                else:
                    key_type_val = "hub_key" if is_hub else "remote_key"
            else:
                key_type_val = "missing_key"
            synth_stat_val = "ok" if harvest_status == "ok" else ("billing_derived" if (day_monitors or proj_day_synth) else "unauthorized")
            proj_mon_count = max(len(day_monitors), len(all_discovered_monitors))
            doc = {
                "@timestamp": usage_day_ts,
                "period_start": usage_day_ts,
                "event": {"ingested": (now - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")},
                "composite_key": comp_key,
                "doc_type": "project_status",
                "project_id": pid,
                "project_name": pname,
                "project_type": ptype,
                "optimized_for": p.get("optimized_for", "general_purpose"),
                "es_url": es_endpoint,
                "last_status": harvest_status,
                "api_key_type": key_type_val,
                "synthetics_status": synth_stat_val,
                "index_count": harvested_idx_count,
                "datastream_count": max(len(ds_map), harvested_ds_count),
                "ai_model_count": len(ml_models),
                "synthetics_monitor_count": proj_mon_count,
                "updated_at": ts_now,
                "conf_chargeable_unit_rate": u_rate,
                "conf_chargeable_unit_rate_unit": u_unit,
                "conf_currency_symbol": u_sym
            }
            usage_docs.append(doc)
            u_curr += timedelta(days=1)

        print(f"  • {pname} ({pid}): status={harvest_status}, datastreams={len(ds_map)}, ai_models={len(ml_models)}, monitors={len(all_discovered_monitors)}")

    # Preserve historical usage docs (datastream, ai_model, synthetics_monitor) for deleted/decommissioned projects
    ingested_str_inactive = (now - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen_preserved_usage_keys = set()
    for p_u_doc in preserved_deleted_usage:
        pid = p_u_doc.get("project_id")
        if not pid or pid in active_project_ids:
            continue
        ukey = (
            p_u_doc.get("composite_key"),
            p_u_doc.get("doc_type"),
            p_u_doc.get("datastream") or p_u_doc.get("index") or p_u_doc.get("model_id") or p_u_doc.get("monitor_id") or ""
        )
        if ukey in seen_preserved_usage_keys:
            continue
        seen_preserved_usage_keys.add(ukey)
        preserved_copy = dict(p_u_doc)
        preserved_copy["event"] = {"ingested": ingested_str_inactive}
        usage_docs.append(preserved_copy)

    # Emit project_status records for inactive / decommissioned projects seen in billing history
    ts_now_inactive = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for pid, pinfo in all_known_projects.items():
        if pid in active_project_ids:
            continue
        pname = pinfo.get("name") or pid
        ptype = pinfo.get("project_type") or "elasticsearch"
        u_curr = start_date
        while u_curr <= end_date:
            usage_day_str = u_curr.strftime("%Y-%m-%d")
            usage_day_ts = f"{usage_day_str}T00:00:00.000Z"
            usage_docs.append({
                "@timestamp": usage_day_ts,
                "period_start": usage_day_ts,
                "event": {"ingested": ingested_str_inactive},
                "composite_key": f"{usage_day_str}_{pid}",
                "doc_type": "project_status",
                "project_id": pid,
                "project_name": pname,
                "project_type": ptype,
                "optimized_for": "decommissioned",
                "es_url": "",
                "last_status": "inactive",
                "api_key_type": "cloud_billing_only",
                "synthetics_status": "inactive",
                "index_count": 0,
                "datastream_count": 0,
                "ai_model_count": 0,
                "synthetics_monitor_count": 0,
                "updated_at": ts_now_inactive,
                "conf_chargeable_unit_rate": u_rate,
                "conf_chargeable_unit_rate_unit": u_unit,
                "conf_currency_symbol": u_sym,
            })
            u_curr += timedelta(days=1)
        print(f"  • {pname} ({pid}): status=inactive (decommissioned / historical in window)")

    if usage_docs:
        min_u_ts = f"{start_date.strftime('%Y-%m-%d')}T00:00:00.000Z"
        max_u_ts = f"{end_date.strftime('%Y-%m-%d')}T00:00:00.000Z"
        delete_existing_window(ES_URL, ES_API_KEY, "metrics-chargeback_serverless.usage-default", min_u_ts, max_u_ts)
        for u_lk in [
            "serverless_datastream_contribution_lookup",
            "serverless_project_contribution_lookup",
            "serverless_ai_model_contribution_lookup",
            "serverless_synthetics_monitor_contribution_lookup",
            "serverless_project_status_lookup"
        ]:
            delete_existing_window(ES_URL, ES_API_KEY, u_lk, min_u_ts, max_u_ts)
        print(f"\n  Ingesting {len(usage_docs)} daily usage documents into metrics-chargeback_serverless.usage-default...")
        bulk_lines = []
        for d in usage_docs:
            bulk_lines.append(json.dumps({"create": {}}))
            bulk_lines.append(json.dumps(d))
        bulk_payload = "\n".join(bulk_lines) + "\n"

        target_es_bulk = f"{ES_URL.rstrip('/')}/metrics-chargeback_serverless.usage-default/_bulk"
        try:
            status, bulk_resp = http_post_bulk(target_es_bulk, es_headers, bulk_payload)
            errors = bulk_resp.get("errors", False)
            if errors:
                err_items = [item for item in bulk_resp.get("items", []) if "error" in item.get("create", {})]
                print(f"  ⚠ Bulk index reported {len(err_items)} errors: {err_items[:1]}")
            else:
                print(f"  ✓ Successfully indexed {len(usage_docs)} daily usage records.")
        except Exception as e:
            print(f"  ⚠ Failed to bulk index usage metrics: {e}")

        # Only direct-write project_status into serverless_project_status_lookup when the continuous Fleet transform is not installed
        status_docs = [d for d in usage_docs if d.get("doc_type") == "project_status"]
        if status_docs and not has_proj_status_tf:
            ensure_lookup_index(ES_URL, ES_API_KEY, "serverless_project_status_lookup")
            print(f"  • Ingesting {len(status_docs)} project status records directly into serverless_project_status_lookup (no transform detected)...")
            ps_bulk_lines = []
            for d in status_docs:
                doc_id = d["composite_key"]
                ps_bulk_lines.append(json.dumps({"index": {"_id": doc_id}}))
                ps_bulk_lines.append(json.dumps(d))
            ps_bulk_payload = "\n".join(ps_bulk_lines) + "\n"
            target_ps_bulk = f"{ES_URL.rstrip('/')}/serverless_project_status_lookup/_bulk"
            try:
                status, bulk_resp = http_post_bulk(target_ps_bulk, es_headers, ps_bulk_payload)
                errors = bulk_resp.get("errors", False)
                if errors:
                    err_items = [item for item in bulk_resp.get("items", []) if "error" in item.get("index", {})]
                    print(f"  ⚠ Bulk index to serverless_project_status_lookup reported {len(err_items)} errors: {err_items[:1]}")
                else:
                    print(f"  ✓ Successfully indexed {len(status_docs)} status records into serverless_project_status_lookup.")
            except Exception as e:
                print(f"  ⚠ Failed to direct-index into serverless_project_status_lookup: {e}")

    # Refresh data streams and lookups
    try:
        http_get(f"{ES_URL.rstrip('/')}/metrics-chargeback_serverless.*/_refresh", {"Authorization": f"ApiKey {ES_API_KEY}"})
        http_get(f"{ES_URL.rstrip('/')}/serverless_*_lookup/_refresh", {"Authorization": f"ApiKey {ES_API_KEY}"})
    except Exception:
        pass

    # Reset and schedule continuous transforms so lookup indices are cleanly populated without duplicate _ids
    if active_transforms:
        ensure_all_lookup_indices(ES_URL, ES_API_KEY)
        upstream_tfs = [t for t in active_transforms if "realized_pool" not in t and "proj_contrib" not in t]
        downstream_tfs = [t for t in active_transforms if "realized_pool" in t or "proj_contrib" in t]
        for tid in upstream_tfs:
            try:
                http_post_bulk(f"{ES_URL.rstrip('/')}/_transform/{tid}/_stop?force=true&wait_for_completion=true", es_headers, "{}")
                http_post_bulk(f"{ES_URL.rstrip('/')}/_transform/{tid}/_reset?force=true", es_headers, "{}")
            except Exception:
                pass
        ensure_all_lookup_indices(ES_URL, ES_API_KEY)
        for tid in upstream_tfs:
            try:
                http_post_bulk(f"{ES_URL.rstrip('/')}/_transform/{tid}/_start", es_headers, "{}")
                http_post_bulk(f"{ES_URL.rstrip('/')}/_transform/{tid}/_schedule_now", es_headers, "{}")
            except Exception:
                pass
        time.sleep(8)
        try:
            http_get(f"{ES_URL.rstrip('/')}/serverless_*_lookup/_refresh", {"Authorization": f"ApiKey {ES_API_KEY}"})
        except Exception:
            pass
        for tid in downstream_tfs:
            try:
                http_post_bulk(f"{ES_URL.rstrip('/')}/_transform/{tid}/_stop?force=true&wait_for_completion=true", es_headers, "{}")
                http_post_bulk(f"{ES_URL.rstrip('/')}/_transform/{tid}/_reset?force=true", es_headers, "{}")
            except Exception:
                pass
        ensure_all_lookup_indices(ES_URL, ES_API_KEY)
        for tid in downstream_tfs:
            try:
                http_post_bulk(f"{ES_URL.rstrip('/')}/_transform/{tid}/_start", es_headers, "{}")
                http_post_bulk(f"{ES_URL.rstrip('/')}/_transform/{tid}/_schedule_now", es_headers, "{}")
            except Exception:
                pass
        time.sleep(6)
        try:
            http_get(f"{ES_URL.rstrip('/')}/serverless_*_lookup/_refresh", {"Authorization": f"ApiKey {ES_API_KEY}"})
        except Exception:
            pass

    print("\n✓ Local collection pass finished successfully.")

if __name__ == "__main__":
    main()
