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
    s = (sku or "").lower()
    k = (kind or "").lower()
    t = (item_type or "").lower()

    if any(x in s for x in ["ingest", "indexing"]) or "index" in k:
        return ("Indexing", "Indexing", True, "indexing_time")
    if any(x in s for x in ["search", "query"]) or "query" in k:
        return ("Query", "Search", True, "query_time")
    if any(x in s for x in ["retained", "storage", "store"]) or "storage" in k:
        return ("Storage", "Storage", True, "storage_size")
    if any(x in s for x in ["ml-vcu", "inference", "ai", "trained"]) or "ml" in k:
        return ("AI & ML", "Machine Learning", True, "inference_count")
    if any(x in s for x in ["data-transfer", "egress", "network"]):
        return ("Data Transfer", "Networking", False, "unallocated")
    return ("Other Platform", "Platform", False, "unallocated")

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

    # 2. Collect Historical Billing Metrics
    now = datetime.now(timezone.utc)
    from_date = now - timedelta(days=LOOKBEHIND)
    print(f"\n>>> Step 2: Collecting Billing metrics (last {LOOKBEHIND} days)...")
    from_str = from_date.strftime("%Y-%m-%dT00:00:00Z")
    to_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    billing_url = f"{BILLING_API_BASE}/api/v2/billing/organizations/{ORG_ID}/costs/instances?from={urllib.parse.quote(from_str)}&to={urllib.parse.quote(to_str)}"
    billing_instances = []
    try:
        status, bdata = http_get(billing_url, cloud_headers)
        raw_instances = bdata.get("instances", [])
        billing_instances = [
            inst for inst in raw_instances
            if inst.get("type") in ["elasticsearch", "observability", "security"]
        ]
        print(f"  • Retrieved billing instances for {len(billing_instances)} Serverless project instance(s)")
    except Exception as e:
        print(f"  ⚠ Failed to fetch billing instances: {e}")

    # Format billing events
    billing_docs = []
    for inst in billing_instances:
        inst_id = inst.get("id", "")
        inst_name = inst.get("name", inst_id)
        inst_type = inst.get("type", "elasticsearch")

        # Resolve project details if matched
        proj = project_map.get(inst_id, {})
        pname = proj.get("name", inst_name)
        ptype = proj.get("project_type", inst_type)

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
                "@timestamp": from_str,
                "deployment_id": inst_id,
                "deployment_name": pname,
                "deployment_type": ptype,
                "project_id": inst_id,
                "project_name": pname,
                "project_type": ptype,
                "cluster_name": inst_id,
                "sku": sku,
                "kind": kind,
                "type": itype,
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

    if billing_docs:
        print(f"  • Ingesting {len(billing_docs)} billing metric documents into metrics-chargeback_serverless.billing-default...")
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
                print(f"  ✓ Successfully indexed {len(billing_docs)} billing records.")
        except Exception as e:
            print(f"  ⚠ Failed to bulk index billing metrics: {e}")
    else:
        print("  Notice: No Serverless billing line items found for the given window.")

    # 3. Harvest Project Usage & AI Metrics
    print("\n>>> Step 3: Harvesting granular usage metrics across Serverless projects...")

    # Calculate latest daily realized pool per project
    pools = {}
    for inst in billing_instances:
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

        # Aggregate datastreams
        ds_map = {}
        for row in indices:
            idx_name = row.get("index", "")
            if idx_name.startswith(".") and not idx_name.startswith(".ds-"):
                continue

            if idx_name.startswith(".ds-"):
                parts = idx_name.split("-")
                ds_name = parts[1] if len(parts) > 1 else idx_name
            else:
                ds_name = idx_name

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

        # Emit datastream records
        ts_now = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        for ds_name, v in ds_map.items():
            doc = {
                "@timestamp": ts_now,
                "doc_type": "datastream",
                "project_id": pid,
                "project_name": pname,
                "project_type": ptype,
                "cluster_name": pid,
                "datastream": ds_name,
                "ds_type": v["ds_type"],
                "ds_namespace": v["ds_namespace"],
                "datastream_sum_indexing_time": v["idx_time"],
                "datastream_sum_query_time": v["qry_time"],
                "datastream_sum_data_set_store_size": v["size"],
                "deployment_sum_indexing_time": proj_total_idx,
                "deployment_sum_query_time": proj_total_qry,
                "deployment_sum_data_set_store_size": proj_total_size,
                "indexing_ecu": pool["indexing_ecu"],
                "query_ecu": pool["query_ecu"],
                "storage_ecu": pool["storage_ecu"],
                "total_ecu": pool["total_ecu"],
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
            inf_time = int(counts.get("inference_time_ms") or 0)
            doc = {
                "@timestamp": ts_now,
                "doc_type": "ai_model",
                "project_id": pid,
                "project_name": pname,
                "project_type": ptype,
                "cluster_name": pid,
                "model_id": mid,
                "inference_count": inf_count,
                "inference_time_ms": inf_time,
                "ai_ecu": pool["ai_ecu"],
                "query_ecu": pool["query_ecu"],
                "conf_chargeable_unit_rate": CONF_RATE,
                "conf_chargeable_unit_rate_unit": CONF_RATE_UNIT,
                "conf_currency_symbol": CONF_CURRENCY_SYMBOL
            }
            usage_docs.append(doc)

        # Emit project status record
        doc = {
            "@timestamp": ts_now,
            "doc_type": "project_status",
            "project_id": pid,
            "project_name": pname,
            "project_type": ptype,
            "cluster_name": pid,
            "last_status": harvest_status,
            "index_count": len(indices),
            "datastream_count": len(ds_map),
            "total_size_bytes": proj_total_size,
            "es_endpoint": es_endpoint,
            "indexing_ecu": pool["indexing_ecu"],
            "query_ecu": pool["query_ecu"],
            "storage_ecu": pool["storage_ecu"],
            "ai_ecu": pool["ai_ecu"],
            "total_ecu": pool["total_ecu"]
        }
        usage_docs.append(doc)
        print(f"  • {pname} ({pid}): status={harvest_status}, datastreams={len(ds_map)}, ai_models={len(ml_models)}")

    if usage_docs:
        print(f"\n  Ingesting {len(usage_docs)} usage documents into metrics-chargeback_serverless.usage-default...")
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
                print(f"  ✓ Successfully indexed {len(usage_docs)} usage records.")
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
