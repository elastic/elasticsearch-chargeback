#!/usr/bin/env python3
"""Export Chargeback dashboards from Kibana into integrations package JSON.

Round-trips Billing, Usage, and Configuration dashboards from a running Kibana
stack into packages/chargeback/kibana/dashboard/*.json, then applies the
__chargeback_unfiltered__ sentinel on Billing/Usage pinned controls and panel
filters so the integration zip ships a deployable default.

Usage:
  KIBANA_HOST=https://localhost:5601 KIBANA_PASSWORD=changeme \\
    python3 scripts/export_chargeback_dashboards_to_integration.py

Requires INTEGRATIONS_REPO (default: ../integrations sibling clone).
"""
from __future__ import annotations

import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from apply_chargeback_esql_sentinel import DASHBOARDS as SENTINEL_DASHBOARDS  # noqa: E402
from apply_chargeback_esql_sentinel import apply_sentinel, verify  # noqa: E402

INTEGRATIONS_ROOT = Path(
    os.environ.get(
        "INTEGRATIONS_REPO",
        Path(__file__).resolve().parents[2] / "integrations",
    )
)
DASHBOARD_DIR = INTEGRATIONS_ROOT / "packages/chargeback/kibana/dashboard"

KIBANA_HOST = os.environ.get("KIBANA_HOST", "https://127.0.0.1:5601").rstrip("/")
KIBANA_USER = os.environ.get("KIBANA_USER", "elastic")
KIBANA_PASSWORD = os.environ.get("KIBANA_PASSWORD", "changeme")

DASHBOARDS: tuple[tuple[str, str], ...] = (
    ("chargeback-b6cecac0-ebe7-43de-bfc6-1ff287ad860a", "[Chargeback] Billing Components Overview"),
    ("chargeback-21588d0e-fb6a-4f76-ad2f-cd7b3d7a3d7c", "[Chargeback] Usage & Cost Allocation"),
    ("chargeback-7ec9743a-ccfa-43d2-8311-2f25893dadae", "[Chargeback] Configuration"),
)

STRIP_KEYS = (
    "accessControl",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
    "version",
    "namespaces",
    "managed",
    "migrationVersion",
    "originId",
)


def kibana_request(method: str, path: str) -> dict:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {
        "kbn-xsrf": "true",
        "Authorization": "Basic "
        + base64.b64encode(f"{KIBANA_USER}:{KIBANA_PASSWORD}".encode()).decode(),
    }
    req = urllib.request.Request(f"{KIBANA_HOST}{path}", headers=headers, method=method)
    with urllib.request.urlopen(req, context=ctx) as resp:
        return json.loads(resp.read())


def parse_json_field(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def normalize_attributes(attrs: dict) -> dict:
    out = dict(attrs)
    out["panelsJSON"] = parse_json_field(out["panelsJSON"])
    if "optionsJSON" in out:
        out["optionsJSON"] = parse_json_field(out["optionsJSON"])
    if "pinned_panels" in out:
        out["pinned_panels"] = parse_json_field(out["pinned_panels"])
    if "controlGroupInput" in out and out["controlGroupInput"]:
        out["controlGroupInput"] = parse_json_field(out["controlGroupInput"])
    kmeta = out.get("kibanaSavedObjectMeta", {})
    if isinstance(kmeta.get("searchSourceJSON"), str):
        out["kibanaSavedObjectMeta"] = {
            "searchSourceJSON": json.loads(kmeta["searchSourceJSON"])
        }
    return out


def to_package_object(live: dict, *, dashboard_id: str, title: str) -> dict:
    attrs = normalize_attributes(live["attributes"])
    attrs["title"] = title
    return {
        "attributes": attrs,
        "coreMigrationVersion": live.get("coreMigrationVersion", "8.8.0"),
        "id": dashboard_id,
        "references": live.get("references", []),
        "type": "dashboard",
        "typeMigrationVersion": live.get("typeMigrationVersion", "10.3.0"),
    }


def export_dashboard(dashboard_id: str, title: str) -> Path:
    live = kibana_request("GET", f"/api/saved_objects/dashboard/{dashboard_id}")
    obj = to_package_object(live, dashboard_id=dashboard_id, title=title)
    if dashboard_id in SENTINEL_DASHBOARDS:
        obj = apply_sentinel(obj)
    path = DASHBOARD_DIR / f"{dashboard_id}.json"
    if dashboard_id in SENTINEL_DASHBOARDS:
        verify(path, obj)
    path.write_text(json.dumps(obj, indent=4) + "\n")
    return path


def main() -> int:
    if not DASHBOARD_DIR.is_dir():
        print(f"Dashboard dir missing: {DASHBOARD_DIR}", file=sys.stderr)
        return 1

    print(f"Exporting Chargeback dashboards from {KIBANA_HOST}")
    print(f"Writing to {DASHBOARD_DIR}")

    try:
        for dashboard_id, title in DASHBOARDS:
            path = export_dashboard(dashboard_id, title)
            obj = json.loads(path.read_text())
            pinned = obj["attributes"].get("pinned_panels", {})
            pinned_count = len(pinned.get("panels", {})) if pinned else 0
            links = sum(1 for p in obj["attributes"]["panelsJSON"] if p.get("type") == "links")
            print(f"  {path.name}: links={links} pinned_controls={pinned_count}")
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode()}", file=sys.stderr)
        return 1

    print("Done. Run apply_chargeback_esql_sentinel.py and elastic-package build before release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
