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
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

def get_env(key, default=""):
    return os.environ.get(key, default).strip()

ORG_ID = get_env("ORGANIZATION_ID")
CLOUD_API_KEY = get_env("CLOUD_API_KEY") or get_env("BILLING_API_KEY")
ES_URL = get_env("ES_URL") or get_env("sl_url") or get_env("ES_HOST")
ES_API_KEY = get_env("ES_API_KEY") or get_env("HUB_ES_API_KEY") or get_env("HUB_KIBANA_API_KEY")
HUB_ES_API_KEY = get_env("HUB_ES_API_KEY") or ES_API_KEY

BILLING_API_BASE = get_env("BILLING_API_BASE", "https://billing.elastic-cloud.com").rstrip("/")
CLOUD_API_BASE = get_env("CLOUD_API_BASE", "https://api.elastic-cloud.com").rstrip("/")

LOOKBEHIND = int(get_env("LOOKBEHIND", "30") or 30)
CONF_RATE = float(get_env("CONF_RATE", "1.0") or 1.0)
CONF_RATE_UNIT = get_env("CONF_RATE_UNIT", "USD") or "USD"
CONF_CURRENCY_SYMBOL = get_env("CONF_CURRENCY_SYMBOL", "$") or "$"

SYMBOL_MAP = {
    "EUR": "€", "GBP": "£", "ZAR": "R", "JPY": "¥", "CNY": "¥", "INR": "₹",
    "CAD": "CA$", "AUD": "A$", "CHF": "CHF", "BRL": "R$", "SEK": "kr",
    "NOK": "kr", "DKK": "kr", "PLN": "zł", "NZD": "NZ$", "SGD": "S$",
    "HKD": "HK$", "ILS": "₪", "KRW": "₩", "USD": "$"
}
if not get_env("CONF_CURRENCY_SYMBOL") and CONF_RATE_UNIT.upper() in SYMBOL_MAP:
    CONF_CURRENCY_SYMBOL = SYMBOL_MAP[CONF_RATE_UNIT.upper()]

# Project keys mapping: project_id -> api_key
PROJECT_KEYS = {}
project_keys_env = get_env("PROJECT_API_KEYS")
if project_keys_env:
    try:
        parsed = json.loads(project_keys_env)
        if isinstance(parsed, list):
            for item in parsed:
                if ":" in item:
                    pid, pkey = item.split(":", 1)
                    PROJECT_KEYS[pid.strip()] = pkey.strip()
    except Exception:
        pass

single_pid = get_env("PROJECT_ID")
single_pkey = get_env("SL_API_KEY")
if single_pid and single_pkey:
    PROJECT_KEYS[single_pid] = single_pkey

CURL_INSECURE = get_env("CURL_INSECURE", "0") == "1"

def create_ssl_context(target_url=""):
    if CURL_INSECURE or "localhost" in target_url or "127.0.0.1" in target_url:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None

def http_get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {})
    ctx = create_ssl_context(url)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.getcode(), json.loads(resp.read().decode("utf-8"))

def http_post_bulk(url, headers, bulk_body_str, timeout=60):
    req = urllib.request.Request(url, data=bulk_body_str.encode("utf-8"), headers=headers)
    ctx = create_ssl_context(url)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.getcode(), json.loads(resp.read().decode("utf-8"))

def classify_sku(sku, kind, item_type):
    s = (sku or "")
    if not s:
        return ("other", "other", False, "none")

    if s.startswith("observability.") or s.startswith("security."):
        if any(x in s for x in ["retained", "storage", "retention"]):
            return ("retained", "data_tier", True, "storage")
        if any(x in s for x in ["ingest", "logs", "metrics", "traces", "apm", "siem"]):
            return ("ingest", "data_tier", True, "indexing")

    if s.startswith("elasticsearch.") or s.startswith("vector.") or s.startswith("elasticsearch-vector."):
        if "-ingest-vcu_" in s or "ingest-vcu" in s:
            return ("ingest-vcu", "data_tier", True, "indexing")
        if "-search-vcu_" in s or "search-vcu" in s:
            return ("search-vcu", "data_tier", True, "query")
        if s.startswith("elasticsearch.retained_") or "retained_" in s or "storage" in s:
            return ("retained", "data_tier", True, "storage")
        if "-ml-vcu_" in s or "ml-vcu" in s:
            return ("ml-vcu", "platform", False, "none")

    if s.startswith("serverless.support-"):
        return ("serverless_premium_support", "support", False, "none")
    if any(x in s for x in ["data-transfer", "data-out", "egress"]):
        return ("data-transfer", "transfer", False, "none")
    if "snapshot" in s:
        return ("snapshots", "snapshot", False, "none")
    if any(x in s for x in ["inference", "llm"]):
        return ("inference", "platform", False, "none")
    if "synthetics" in s:
        return ("synthetics", "platform", False, "none")
    if "workflow" in s:
        return ("workflows", "platform", False, "none")
    if "agent-builder" in s:
        return ("agent-builder", "platform", False, "none")
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
    project_types = ["elasticsearch", "observability", "security"]
    for ptype in project_types:
        url = f"{CLOUD_API_BASE}/api/v1/serverless/projects/{ptype}"
        try:
            status, data = http_get(url, cloud_headers)
            items = data.get("items", [])
            for item in items:
                item["project_type"] = ptype
                projects.append(item)
            print(f"  • Discovered {len(items)} {ptype} project(s)")
        except Exception as e:
            print(f"  ⚠ Failed to query {ptype} projects: {e}")

    project_map = {p["id"]: p for p in projects}
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

    billing_docs = []
    latest_billing_instances = []

    current_date = start_date
    while current_date <= end_date:
        day_start = datetime(current_date.year, current_date.month, current_date.day, 0, 0, 0, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        day_from_str = day_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        day_to_str = day_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        day_timestamp = day_start.strftime("%Y-%m-%dT00:00:00.000Z")
        day_str = current_date.strftime("%Y-%m-%d")

        billing_url = f"{BILLING_API_BASE}/api/v2/billing/organizations/{ORG_ID}/costs/instances?from={urllib.parse.quote(day_from_str)}&to={urllib.parse.quote(day_to_str)}"
        try:
            status, bdata = http_get(billing_url, cloud_headers)
            raw_instances = bdata.get("instances", [])
            day_instances = [
                inst for inst in raw_instances
                if inst.get("type") in ["elasticsearch", "observability", "security"]
            ]
            if day_instances:
                latest_billing_instances = day_instances

            day_line_count = 0
            for inst in day_instances:
                inst_id = inst.get("id", "")
                inst_name = inst.get("name", inst_id)
                inst_type = inst.get("type", "elasticsearch")

                proj = project_map.get(inst_id, {})
                pname = proj.get("name", inst_name) or inst_name or inst_id
                ptype = proj.get("project_type", inst_type) or inst_type

                line_items = inst.get("product_line_items", [])
                for li in line_items:
                    sku = li.get("sku", "")
                    kind = li.get("kind", "")
                    itype = li.get("type", "")
                    total_ecu = float(li.get("total_ecu") or 0.0)
                    qty = li.get("quantity") or {}
                    qty_val = float(qty.get("value") or 0.0) if isinstance(qty, dict) else 0.0

                    cost_type, cost_cat, is_alloc, driver = classify_sku(sku, kind, itype)

                    doc = {
                        "@timestamp": day_timestamp,
                        "period_start": day_timestamp,
                        "composite_key": f"{day_str}_{inst_id}",
                        "project_id": inst_id,
                        "project_name": pname,
                        "project_type": ptype,
                        "sku": sku,
                        "total_ecu": total_ecu,
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
                    day_line_count += 1
            if day_instances:
                print(f"  • {day_str}: {len(day_instances)} project instance(s), {day_line_count} line items")
        except Exception as e:
            print(f"  ⚠ Failed to fetch billing instances for {current_date}: {e}")

        current_date += timedelta(days=1)

    # Purge any records of today or future from target indices
    delete_existing_window(ES_URL, ES_API_KEY, "metrics-chargeback_serverless.billing-default", today_start_iso, "now+1d")
    delete_existing_window(ES_URL, ES_API_KEY, "metrics-chargeback_serverless.usage-default", today_start_iso, "now+1d")
    for lk_idx in [
        "serverless_billing_cost_lookup",
        "serverless_billing_realized_pool_lookup",
        "serverless_datastream_contribution_lookup",
        "serverless_project_contribution_lookup",
        "serverless_ai_model_contribution_lookup",
        "serverless_project_status_lookup"
    ]:
        delete_existing_window(ES_URL, ES_API_KEY, lk_idx, today_start_iso, "now+1d")

    if billing_docs:
        min_ts = billing_docs[0]["@timestamp"]
        max_ts = billing_docs[-1]["@timestamp"]
        delete_existing_window(ES_URL, ES_API_KEY, "metrics-chargeback_serverless.billing-default", min_ts, max_ts)
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
                print(f"  ✓ Successfully indexed {len(billing_docs)} daily billing records.")
        except Exception as e:
            print(f"  ⚠ Failed to bulk index billing metrics: {e}")
    else:
        print("  Notice: No Serverless billing line items found for the given window.")

    # 3. Harvest Project Usage & AI Metrics
    print("\n>>> Step 3: Harvesting granular usage metrics across Serverless projects...")

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
            elif "ml" in sku or "inference" in sku:
                ai_e += ecu
        pools[pid] = {
            "indexing_ecu": idx_e,
            "query_ecu": qry_e,
            "storage_ecu": stg_e,
            "ai_ecu": ai_e,
            "total_ecu": idx_e + qry_e + stg_e + ai_e
        }

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

        # Determine target key
        target_key = PROJECT_KEYS.get(pid)
        is_hub = False
        if not target_key and ES_URL:
            # Check if this project is the hub
            if pid in ES_URL or (es_endpoint and es_endpoint in ES_URL):
                target_key = HUB_ES_API_KEY
                is_hub = True

        if not target_key:
            # Fallback to general cloud api key or report unharvested
            target_key = CLOUD_API_KEY

        headers = {
            "Authorization": f"ApiKey {target_key}",
            "Content-Type": "application/json"
        }

        # Query _cat/indices
        cat_url = f"{es_endpoint}/_cat/indices?h=index,dataset.size,indexing.index_time,search.query_time&format=json&bytes=b&time=ms&expand_wildcards=open,hidden"
        indices = []
        harvest_status = "ok"
        try:
            status, cat_data = http_get(cat_url, headers, timeout=15)
            indices = cat_data if isinstance(cat_data, list) else []
        except Exception:
            harvest_status = "empty_or_unauthorized"

        # Query _ml/trained_models/_stats
        ml_models = []
        if harvest_status == "ok":
            ml_url = f"{es_endpoint}/_ml/trained_models/_stats"
            try:
                status, ml_data = http_get(ml_url, headers, timeout=15)
                ml_models = ml_data.get("trained_model_stats", [])
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

            ds_parts = ds_name.split("-")
            ds_type = ds_parts[0] if len(ds_parts) > 1 else "other"
            ds_namespace = ds_parts[-1] if len(ds_parts) > 1 else "other"

            size_b = int(row.get("dataset.size") or 0)
            idx_time = int(row.get("indexing.index_time") or 0)
            qry_time = int(row.get("search.query_time") or 0)

            if ds_name not in ds_map:
                ds_map[ds_name] = {
                    "ds": ds_name,
                    "ds_type": ds_type,
                    "ds_namespace": ds_namespace,
                    "size": 0,
                    "idx_time": 0,
                    "qry_time": 0
                }
            ds_map[ds_name]["size"] += size_b
            ds_map[ds_name]["idx_time"] += idx_time
            ds_map[ds_name]["qry_time"] += qry_time

        proj_total_size = sum(v["size"] for v in ds_map.values()) or 1
        proj_total_idx = sum(v["idx_time"] for v in ds_map.values()) or 1
        proj_total_qry = sum(v["qry_time"] for v in ds_map.values()) or 1

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
                    "composite_key": comp_key,
                    "doc_type": "datastream",
                    "project_id": pid,
                    "project_name": pname,
                    "project_type": ptype,
                    "datastream": ds_name,
                    "ds_type": v["ds_type"],
                    "ds_namespace": v["ds_namespace"],
                    "datastream_sum_indexing_time": v["idx_time"],
                    "datastream_sum_query_time": v["qry_time"],
                    "datastream_sum_data_set_store_size": v["size"],
                    "conf_chargeable_unit_rate": CONF_RATE,
                    "conf_chargeable_unit_rate_unit": CONF_RATE_UNIT,
                    "conf_currency_symbol": CONF_CURRENCY_SYMBOL
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
                task_type = "ingest_inference" if int(m.get("pipeline_count") or 0) > 0 else "query_inference"
                doc = {
                    "@timestamp": usage_day_ts,
                    "period_start": usage_day_ts,
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
                    "conf_chargeable_unit_rate": CONF_RATE,
                    "conf_chargeable_unit_rate_unit": CONF_RATE_UNIT,
                    "conf_currency_symbol": CONF_CURRENCY_SYMBOL
                }
                usage_docs.append(doc)

            # Emit project status record
            doc = {
                "@timestamp": usage_day_ts,
                "period_start": usage_day_ts,
                "composite_key": comp_key,
                "doc_type": "project_status",
                "project_id": pid,
                "project_name": pname,
                "project_type": ptype,
                "optimized_for": p.get("optimized_for", "general_purpose"),
                "es_url": es_endpoint,
                "last_status": harvest_status,
                "index_count": len(indices),
                "datastream_count": len(ds_map),
                "total_size_bytes": proj_total_size,
                "updated_at": ts_now,
                "conf_chargeable_unit_rate": CONF_RATE,
                "conf_chargeable_unit_rate_unit": CONF_RATE_UNIT,
                "conf_currency_symbol": CONF_CURRENCY_SYMBOL
            }
            usage_docs.append(doc)
            u_curr += timedelta(days=1)

        print(f"  • {pname} ({pid}): status={harvest_status}, datastreams={len(ds_map)}, ai_models={len(ml_models)}")

    if usage_docs:
        min_u_ts = f"{start_date.strftime('%Y-%m-%d')}T00:00:00.000Z"
        max_u_ts = f"{end_date.strftime('%Y-%m-%d')}T00:00:00.000Z"
        delete_existing_window(ES_URL, ES_API_KEY, "metrics-chargeback_serverless.usage-default", min_u_ts, max_u_ts)
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

    # Refresh data streams
    try:
        http_get(f"{ES_URL.rstrip('/')}/metrics-chargeback_serverless.*/_refresh", {"Authorization": f"ApiKey {ES_API_KEY}"})
    except Exception:
        pass

    print("\n✓ Local collection pass finished successfully.")

if __name__ == "__main__":
    main()
