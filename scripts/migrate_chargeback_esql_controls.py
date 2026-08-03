#!/usr/bin/env python3
"""Migrate Chargeback Billing/Usage dashboards to pinned ES|QL controls.

Kibana 9.4 does not render links navigation when esql_control panels are embedded in
panelsJSON on the same dashboard. Controls belong in pinned_panels (below nav links).

- links panel stays in panelsJSON at y=0
- esql_control panels live in pinned_panels
- control queries are simple option lists (no chained MV_* in control queries)
- selected_options defaults to [__chargeback_unfiltered__] (portable "no filter" sentinel)
- panel ES|QL uses CASE(... sentinel ...) MV_INTERSECTS filter clauses
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

INTEGRATIONS = Path(__file__).resolve().parents[2] / "integrations"
DASHBOARD_DIR = INTEGRATIONS / "packages/chargeback/kibana/dashboard"

BILLING_ID = "chargeback-b6cecac0-ebe7-43de-bfc6-1ff287ad860a"
USAGE_ID = "chargeback-21588d0e-fb6a-4f76-ad2f-cd7b3d7a3d7c"

SENTINEL = "__chargeback_unfiltered__"

BILLING_CONTROLS = [
    {
        "panel_id": "c42ec760-734f-46bb-8e0f-e52f3d4a1f4d",
        "order": 0,
        "variable_name": "dg_selected",
        "title": "Deployment group",
        "esql_query": (
            "FROM billing_cluster_cost_lookup\n"
            '| WHERE deployment_group != ""\n'
            "| STATS COUNT(*) BY deployment_group\n"
            "| KEEP deployment_group\n"
            "| SORT deployment_group"
        ),
    },
    {
        "panel_id": "5d11a79f-3957-4c0c-a42a-35616191a6fa",
        "order": 1,
        "variable_name": "deployment_selected",
        "title": "Deployment name",
        "esql_query": (
            "FROM billing_cluster_cost_lookup\n"
            '| WHERE deployment_group != ""\n'
            "| STATS COUNT(*) BY deployment_name\n"
            "| KEEP deployment_name\n"
            "| SORT deployment_name"
        ),
    },
    {
        "panel_id": "2cc4273b-85a3-46d8-aebd-019a383c4b64",
        "order": 2,
        "variable_name": "cost_category_selected",
        "title": "Cost category",
        "esql_query": (
            "FROM billing_cluster_cost_lookup\n"
            "| WHERE cost_category IS NOT NULL\n"
            "| STATS COUNT(*) BY cost_category\n"
            "| KEEP cost_category\n"
            "| SORT cost_category"
        ),
    },
    {
        "panel_id": "18543502-9e06-4644-9a39-34e39d2eecd4",
        "order": 3,
        "variable_name": "cost_type_selected",
        "title": "Cost type",
        "esql_query": (
            "FROM billing_cluster_cost_lookup\n"
            "| WHERE cost_type IS NOT NULL\n"
            "| STATS COUNT(*) BY cost_type\n"
            "| KEEP cost_type\n"
            "| SORT cost_type"
        ),
    },
]

USAGE_CONTROLS = [
    {
        "panel_id": "b36355dd-4157-4047-9f99-c6979674fd5a",
        "order": 0,
        "variable_name": "dg_selected",
        "title": "Deployment group",
        "esql_query": (
            "FROM billing_realized_pool_lookup\n"
            '| WHERE deployment_group != ""\n'
            "| STATS COUNT(*) BY deployment_group\n"
            "| KEEP deployment_group\n"
            "| SORT deployment_group"
        ),
    },
    {
        "panel_id": "2900feae-7837-4c04-bb45-5c78ccc46b95",
        "order": 1,
        "variable_name": "deployment_selected",
        "title": "Deployment name",
        "esql_query": (
            "FROM billing_realized_pool_lookup\n"
            '| WHERE deployment_group != ""\n'
            "| STATS COUNT(*) BY deployment_name\n"
            "| KEEP deployment_name\n"
            "| SORT deployment_name"
        ),
    },
    {
        "panel_id": "ff353537-6404-47e4-8b77-f12580668ae9",
        "order": 2,
        "variable_name": "tier_selected",
        "title": "Data tier",
        "esql_query": (
            "FROM cluster_tier_contribution_lookup\n"
            '| WHERE tier IS NOT NULL AND tier != "unknown"\n'
            "| STATS COUNT(*) BY tier\n"
            "| KEEP tier\n"
            "| SORT tier"
        ),
    },
    {
        "panel_id": "2e6a43c7-b43b-46dd-b8cd-2da45eced9c7",
        "order": 3,
        "variable_name": "datastream_selected",
        "title": "Data stream",
        "esql_query": (
            "FROM cluster_tier_and_datastream_contribution_lookup\n"
            "| WHERE datastream IS NOT NULL\n"
            "| STATS COUNT(*) BY datastream\n"
            "| KEEP datastream\n"
            "| SORT datastream"
        ),
    },
    {
        "panel_id": "a1c8e2f4-6b3d-4e9a-9c5f-1d2e3f4a5b6c",
        "order": 4,
        "variable_name": "ds_type_selected",
        "title": "Data stream type",
        "esql_query": (
            "FROM cluster_tier_and_datastream_contribution_lookup\n"
            "| WHERE ds_type IS NOT NULL\n"
            "| STATS COUNT(*) BY ds_type\n"
            "| KEEP ds_type\n"
            "| SORT ds_type"
        ),
    },
    {
        "panel_id": "b2d9f3e5-7c4e-5f0b-0d6a-2e3f4a5b6c7d",
        "order": 5,
        "variable_name": "ds_namespace_selected",
        "title": "Data stream namespace",
        "esql_query": (
            "FROM cluster_tier_and_datastream_contribution_lookup\n"
            "| WHERE ds_namespace IS NOT NULL\n"
            "| STATS COUNT(*) BY ds_namespace\n"
            "| KEEP ds_namespace\n"
            "| SORT ds_namespace"
        ),
    },
]


def make_pinned_panels(specs: list[dict[str, Any]]) -> dict[str, Any]:
    panels: dict[str, Any] = {}
    for spec in specs:
        panels[spec["panel_id"]] = {
            "config": {
                "control_type": "VALUES_FROM_QUERY",
                "esql_query": spec["esql_query"],
                "selected_options": [SENTINEL],
                "single_select": False,
                "title": spec["title"],
                "variable_name": spec["variable_name"],
                "variable_type": "multi_values",
            },
            "grow": False,
            "order": spec["order"],
            "type": "esql_control",
            "width": "medium",
        }
    return {"panels": panels}


def strip_embedded_controls(obj: dict[str, Any], shift_up: int = 2) -> dict[str, Any]:
    attrs = obj["attributes"]
    had_embedded = any(p.get("type") == "esql_control" for p in attrs["panelsJSON"])
    if not had_embedded:
        return obj

    panels = [p for p in attrs["panelsJSON"] if p.get("type") != "esql_control"]
    for panel in panels:
        grid = panel.get("gridData", {})
        if grid and not grid.get("sectionId") and panel.get("type") != "links":
            y = grid.get("y", 0)
            if y >= shift_up:
                grid["y"] = y - shift_up
    for section in attrs.get("sections", []):
        grid = section.get("gridData", {})
        y = grid.get("y", 0)
        if y >= shift_up:
            grid["y"] = y - shift_up
    attrs["panelsJSON"] = panels
    return obj


def migrate_dashboard(obj: dict[str, Any], control_specs: list[dict[str, Any]]) -> dict[str, Any]:
    obj = deepcopy(obj)
    obj = strip_embedded_controls(obj)
    obj["attributes"]["pinned_panels"] = make_pinned_panels(control_specs)
    return obj


def verify_dashboard(path: Path, control_vars: list[str]) -> None:
    obj = json.loads(path.read_text())
    attrs = obj["attributes"]
    pinned = attrs.get("pinned_panels", {}).get("panels", {})
    assert pinned, f"{path.name}: pinned_panels required"

    embedded = [p for p in attrs["panelsJSON"] if p.get("type") == "esql_control"]
    assert not embedded, f"{path.name}: embedded esql_control breaks nav links"

    seen = []
    for panel in pinned.values():
        cfg = panel["config"]
        seen.append(cfg["variable_name"])
        assert cfg["selected_options"] == [SENTINEL], cfg["variable_name"]
        assert cfg["esql_query"].strip(), cfg["variable_name"]
        assert "MV_CONTAINS" not in cfg["esql_query"], cfg["variable_name"]
        assert "MV_INTERSECTS" not in cfg["esql_query"], cfg["variable_name"]

    assert sorted(seen) == sorted(control_vars), (path.name, seen, control_vars)

    text = path.read_text()
    assert SENTINEL in text, f"{path.name}: missing sentinel"
    assert "MV_CONTAINS" not in text, f"{path.name}: MV_CONTAINS in panel queries"
    assert text.count("MV_INTERSECTS") > 0, f"{path.name}: expected MV_INTERSECTS in panel queries"
    assert "CASE(?dg_selected" in text and SENTINEL in text, f"{path.name}: sentinel CASE filters"


def main() -> int:
    billing_path = DASHBOARD_DIR / f"{BILLING_ID}.json"
    usage_path = DASHBOARD_DIR / f"{USAGE_ID}.json"

    billing = migrate_dashboard(json.loads(billing_path.read_text()), BILLING_CONTROLS)
    usage = migrate_dashboard(json.loads(usage_path.read_text()), USAGE_CONTROLS)

    billing_path.write_text(json.dumps(billing, indent=4) + "\n")
    usage_path.write_text(json.dumps(usage, indent=4) + "\n")

    verify_dashboard(billing_path, [c["variable_name"] for c in BILLING_CONTROLS])
    verify_dashboard(usage_path, [c["variable_name"] for c in USAGE_CONTROLS])

    print(f"Updated {billing_path.name}")
    print(f"Updated {usage_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
