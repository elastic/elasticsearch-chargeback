#!/usr/bin/env python3
"""Remove markdown nav hack; restore native links panels on Billing/Usage.

Do NOT use add_chargeback_markdown_nav.py — links nav belongs in panelsJSON like Configuration.
"""
from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

INTEGRATIONS = Path(__file__).resolve().parents[2] / "integrations"
DASHBOARD_DIR = INTEGRATIONS / "packages/chargeback/kibana/dashboard"
REPO = INTEGRATIONS

DASHBOARDS = (
    "chargeback-b6cecac0-ebe7-43de-bfc6-1ff287ad860a",
    "chargeback-21588d0e-fb6a-4f76-ad2f-cd7b3d7a3d7c",
)

# Simple control queries (no All / MV_APPEND — sentinel lives in selected_options only).
CONTROL_QUERIES = {
    "dg_selected": (
        "FROM {index}\n"
        '| WHERE deployment_group != ""\n'
        "| STATS COUNT(*) BY deployment_group\n"
        "| KEEP deployment_group\n"
        "| SORT deployment_group"
    ),
    "deployment_selected": (
        "FROM {index}\n"
        '| WHERE deployment_group != ""\n'
        "| STATS COUNT(*) BY deployment_name\n"
        "| KEEP deployment_name\n"
        "| SORT deployment_name"
    ),
    "cost_category_selected": (
        "FROM billing_cluster_cost_lookup\n"
        "| WHERE cost_category IS NOT NULL\n"
        "| STATS COUNT(*) BY cost_category\n"
        "| KEEP cost_category\n"
        "| SORT cost_category"
    ),
    "cost_type_selected": (
        "FROM billing_cluster_cost_lookup\n"
        "| WHERE cost_type IS NOT NULL\n"
        "| STATS COUNT(*) BY cost_type\n"
        "| KEEP cost_type\n"
        "| SORT cost_type"
    ),
    "tier_selected": (
        "FROM cluster_tier_contribution_lookup\n"
        '| WHERE tier IS NOT NULL AND tier != "unknown"\n'
        "| STATS COUNT(*) BY tier\n"
        "| KEEP tier\n"
        "| SORT tier"
    ),
    "datastream_selected": (
        "FROM cluster_tier_and_datastream_contribution_lookup\n"
        "| WHERE datastream IS NOT NULL\n"
        "| STATS COUNT(*) BY datastream\n"
        "| KEEP datastream\n"
        "| SORT datastream"
    ),
}

BILLING_INDEX = "billing_cluster_cost_lookup"
USAGE_INDEX = "billing_realized_pool_lookup"


def git_head_dashboard(dash_id: str) -> dict:
    raw = subprocess.check_output(
        ["git", "-C", str(REPO), "show", f"HEAD:packages/chargeback/kibana/dashboard/{dash_id}.json"],
        text=True,
    )
    return json.loads(raw)


def links_nav_from_git(dash_id: str) -> tuple[dict, list]:
    obj = git_head_dashboard(dash_id)
    panels = obj["attributes"]["panelsJSON"]
    links = next(p for p in panels if p.get("type") == "links")
    refs = [r for r in obj.get("references", []) if ":link_" in r.get("name", "")]
    return deepcopy(links), deepcopy(refs)


def restore_dashboard(path: Path, dash_id: str, index: str) -> None:
    obj = json.loads(path.read_text())
    panels = obj["attributes"]["panelsJSON"]
    links_panel, link_refs = links_nav_from_git(dash_id)

    panels = [p for p in panels if not (p.get("type") == "markdown" and p.get("gridData", {}).get("y") == 0)]
    if not any(p.get("type") == "links" for p in panels):
        panels.insert(0, links_panel)

    for panel in panels:
        if panel.get("type") != "esql_control":
            continue
        cfg = panel["embeddableConfig"]
        var = cfg.get("variable_name", "")
        if var not in CONTROL_QUERIES:
            continue
        q = CONTROL_QUERIES[var]
        if "{index}" in q:
            q = q.format(index=index)
        cfg["esql_query"] = q

    obj["attributes"]["panelsJSON"] = panels
    other_refs = [r for r in obj.get("references", []) if ":link_" not in r.get("name", "")]
    obj["references"] = link_refs + other_refs

    path.write_text(json.dumps(obj, indent=4) + "\n")
    print(f"OK {path.name}: links nav restored, markdown removed")


def main() -> int:
    restore_dashboard(DASHBOARD_DIR / f"{DASHBOARDS[0]}.json", DASHBOARDS[0], BILLING_INDEX)
    restore_dashboard(DASHBOARD_DIR / f"{DASHBOARDS[1]}.json", DASHBOARDS[1], USAGE_INDEX)

    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from apply_chargeback_esql_sentinel import apply_sentinel, verify

    for dash_id in DASHBOARDS:
        path = DASHBOARD_DIR / f"{dash_id}.json"
        obj = apply_sentinel(json.loads(path.read_text()))
        verify(path, obj)
        path.write_text(json.dumps(obj, indent=4) + "\n")
        print(f"OK {path.name}: sentinel verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
