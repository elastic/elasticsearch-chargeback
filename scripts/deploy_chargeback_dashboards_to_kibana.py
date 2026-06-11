#!/usr/bin/env python3
"""Push Chargeback dashboard JSON from integrations package to a running Kibana stack.

Editing packages/chargeback/kibana/dashboard/*.json does NOT update localhost until you
run this script or reinstall the package zip (Fleet upload / elastic-package install).

Kibana saved-object field types matter:
- panelsJSON, optionsJSON: JSON strings
- sections, controlGroupInput: objects (not strings)
- pinned_panels: omit or null — NEVER write {"panels": {}} (breaks /api/dashboards)

Usage:
  KIBANA_HOST=https://localhost:5601 KIBANA_PASSWORD=changeme \\
    python3 scripts/deploy_chargeback_dashboards_to_kibana.py
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

INTEGRATIONS_ROOT = Path(
    os.environ.get(
        "INTEGRATIONS_REPO",
        Path(__file__).resolve().parents[2] / "integrations",
    )
)
DASHBOARD_DIR = INTEGRATIONS_ROOT / "packages/chargeback/kibana/dashboard"
LINKS_DIR = INTEGRATIONS_ROOT / "packages/chargeback/kibana/links"
NAV_LINKS_ID = "chargeback-e97bc218-1acc-474f-b47c-58d09ce83daf"

KIBANA_HOST = os.environ.get("KIBANA_HOST", "https://127.0.0.1:5601").rstrip("/")
KIBANA_USER = os.environ.get("KIBANA_USER", "elastic")
KIBANA_PASSWORD = os.environ.get("KIBANA_PASSWORD", "changeme")

ALL_DASHBOARDS = (
    "chargeback-b6cecac0-ebe7-43de-bfc6-1ff287ad860a",
    "chargeback-21588d0e-fb6a-4f76-ad2f-cd7b3d7a3d7c",
    "chargeback-7ec9743a-ccfa-43d2-8311-2f25893dadae",
)


def kibana_request(method: str, path: str, body: dict | None = None) -> dict:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {
        "kbn-xsrf": "true",
        "Content-Type": "application/json",
        "Authorization": "Basic "
        + base64.b64encode(f"{KIBANA_USER}:{KIBANA_PASSWORD}".encode()).decode(),
    }
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{KIBANA_HOST}{path}", data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, context=ctx) as resp:
        return json.loads(resp.read())


def prepare_kibana_attributes(attrs: dict) -> dict:
    out = dict(attrs)

    for key in ("panelsJSON", "optionsJSON"):
        if key in out and not isinstance(out[key], str):
            out[key] = json.dumps(out[key])

    pinned = out.get("pinned_panels")
    if pinned is None:
        out["pinned_panels"] = None
    elif isinstance(pinned, dict):
        if pinned.get("panels"):
            out["pinned_panels"] = json.dumps(pinned)
        else:
            out["pinned_panels"] = None
    elif isinstance(pinned, str) and pinned.strip() in ("", "{}", '{"panels": {}}', '{"panels":{}}'):
        out["pinned_panels"] = None

    if out.get("controlGroupInput") is None:
        out["controlGroupInput"] = {}
    elif isinstance(out.get("controlGroupInput"), str):
        out["controlGroupInput"] = json.loads(out["controlGroupInput"])

    kmeta = out.get("kibanaSavedObjectMeta", {})
    if isinstance(kmeta.get("searchSourceJSON"), dict):
        out["kibanaSavedObjectMeta"] = {
            "searchSourceJSON": json.dumps(kmeta["searchSourceJSON"])
        }

    return out


def verify_dashboard_api(dashboard_id: str) -> None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {
        "kbn-xsrf": "true",
        "Authorization": "Basic "
        + base64.b64encode(f"{KIBANA_USER}:{KIBANA_PASSWORD}".encode()).decode(),
    }
    req = urllib.request.Request(
        f"{KIBANA_HOST}/api/dashboards/{dashboard_id}",
        headers=headers,
        method="GET",
    )
    with urllib.request.urlopen(req, context=ctx) as resp:
        if resp.status != 200:
            raise SystemExit(f"{dashboard_id}: /api/dashboards returned {resp.status}")


def deploy_links_asset() -> None:
    path = LINKS_DIR / f"{NAV_LINKS_ID}.json"
    if not path.is_file():
        raise SystemExit(f"Missing navigation links asset: {path}")
    pkg = json.loads(path.read_text())
    body = {
        "attributes": pkg["attributes"],
        "references": pkg.get("references", []),
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {
        "kbn-xsrf": "true",
        "Content-Type": "application/json",
        "Authorization": "Basic "
        + base64.b64encode(f"{KIBANA_USER}:{KIBANA_PASSWORD}".encode()).decode(),
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{KIBANA_HOST}/api/saved_objects/links/{NAV_LINKS_ID}?overwrite=true",
        data=data,
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(req, context=ctx) as resp:
        if resp.status not in (200, 201):
            raise SystemExit(f"{NAV_LINKS_ID}: links asset deploy returned {resp.status}")
    print(f"  {NAV_LINKS_ID}: [Chargeback] Navigation links asset deployed")


def deploy_dashboard(dashboard_id: str) -> None:
    path = DASHBOARD_DIR / f"{dashboard_id}.json"
    if not path.is_file():
        raise SystemExit(f"Missing package dashboard: {path}")

    pkg = json.loads(path.read_text())
    attrs = prepare_kibana_attributes(pkg["attributes"])

    kibana_request(
        "PUT",
        f"/api/saved_objects/dashboard/{dashboard_id}",
        {
            "attributes": attrs,
            "references": pkg.get("references", []),
        },
    )
    verify_dashboard_api(dashboard_id)

    after = kibana_request("GET", f"/api/saved_objects/dashboard/{dashboard_id}")
    panels = json.loads(after["attributes"]["panelsJSON"])
    pp = after["attributes"].get("pinned_panels")
    pinned_count = 0
    if isinstance(pp, str) and pp:
        pp = json.loads(pp)
        pinned_count = len(pp.get("panels", {}))
    print(
        f"  {dashboard_id}: links={sum(1 for p in panels if p.get('type')=='links')} "
        f"embedded_esql={sum(1 for p in panels if p.get('type')=='esql_control')} "
        f"pinned={pinned_count} dashboard_api=OK"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--billing", action="store_true")
    parser.add_argument("--usage", action="store_true")
    parser.add_argument("--config", action="store_true")
    args = parser.parse_args()

    if args.billing or args.usage or args.config:
        ids = []
        if args.billing:
            ids.append(ALL_DASHBOARDS[0])
        if args.usage:
            ids.append(ALL_DASHBOARDS[1])
        if args.config:
            ids.append(ALL_DASHBOARDS[2])
    else:
        ids = list(ALL_DASHBOARDS)

    print(f"Deploying navigation links + {len(ids)} dashboard(s) to {KIBANA_HOST}")
    try:
        deploy_links_asset()
        for dashboard_id in ids:
            deploy_dashboard(dashboard_id)
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode()}", file=sys.stderr)
        return 1

    print("Done. Hard-refresh Kibana (Cmd+Shift+R).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
