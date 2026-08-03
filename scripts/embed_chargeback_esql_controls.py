#!/usr/bin/env python3
"""Embed ES|QL controls in panelsJSON (required for vis/lens variable wiring on Kibana 9.4).

Pinned esql_control panels do not pass ?variables into `type: vis` dashboard panels,
which breaks Billing/Usage. Embedded controls below the links nav row work.

See scripts/DESIGN_ESQL_EMPTY_PARAMS.md for the __chargeback_unfiltered__ sentinel.
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
CONTROL_ROW_Y = 2
CONTROL_HEIGHT = 2
CONTROL_WIDTH = 12
NAV_HEIGHT = 2

BILLING_CONTROLS = [
    {
        "panel_id": "c42ec760-734f-46bb-8e0f-e52f3d4a1f4d",
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


def make_esql_control_panel(
    *,
    panel_id: str,
    x: int,
    y: int,
    variable_name: str,
    title: str,
    esql_query: str,
) -> dict[str, Any]:
    return {
        "embeddableConfig": {
            "control_type": "VALUES_FROM_QUERY",
            "esql_query": esql_query,
            "selected_options": [SENTINEL],
            "single_select": False,
            "title": title,
            "variable_name": variable_name,
            "variable_type": "multi_values",
        },
        "gridData": {
            "h": CONTROL_HEIGHT,
            "i": panel_id,
            "w": CONTROL_WIDTH,
            "x": x,
            "y": y,
        },
        "panelIndex": panel_id,
        "type": "esql_control",
    }


def shift_non_section_panels(panels: list[dict[str, Any]], delta_y: int) -> None:
    for panel in panels:
        grid = panel.get("gridData", {})
        if grid and not grid.get("sectionId") and panel.get("type") not in ("links", "esql_control"):
            grid["y"] = grid.get("y", 0) + delta_y


def shift_sections(sections: list[dict[str, Any]], delta_y: int, min_y: int) -> None:
    for section in sections:
        grid = section.get("gridData", {})
        if grid.get("y", 0) >= min_y:
            grid["y"] = grid["y"] + delta_y


def build_control_panels(specs: list[dict[str, str]], y: int) -> list[dict[str, Any]]:
    return [
        make_esql_control_panel(
            panel_id=spec["panel_id"],
            x=idx * CONTROL_WIDTH,
            y=y,
            variable_name=spec["variable_name"],
            title=spec["title"],
            esql_query=spec["esql_query"],
        )
        for idx, spec in enumerate(specs)
    ]


def embed_controls(obj: dict[str, Any], specs: list[dict[str, str]], *, shift_layout: bool) -> dict[str, Any]:
    obj = deepcopy(obj)
    attrs = obj["attributes"]
    attrs.pop("pinned_panels", None)

    panels = [p for p in attrs["panelsJSON"] if p.get("type") != "esql_control"]
    nav_idx = next((i for i, p in enumerate(panels) if p.get("type") == "links"), None)

    if shift_layout:
        shift_non_section_panels(panels, NAV_HEIGHT)
        shift_sections(attrs.get("sections", []), NAV_HEIGHT, min_y=CONTROL_ROW_Y)

    control_panels = build_control_panels(specs, CONTROL_ROW_Y)
    if nav_idx is not None:
        panels = panels[: nav_idx + 1] + control_panels + panels[nav_idx + 1 :]
    else:
        panels = control_panels + panels

    attrs["panelsJSON"] = panels
    return obj


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from apply_chargeback_esql_sentinel import apply_sentinel, verify

    billing_path = DASHBOARD_DIR / f"{BILLING_ID}.json"
    usage_path = DASHBOARD_DIR / f"{USAGE_ID}.json"

    billing = apply_sentinel(embed_controls(json.loads(billing_path.read_text()), BILLING_CONTROLS, shift_layout=False))
    usage = apply_sentinel(embed_controls(json.loads(usage_path.read_text()), USAGE_CONTROLS, shift_layout=True))

    billing_path.write_text(json.dumps(billing, indent=4) + "\n")
    usage_path.write_text(json.dumps(usage, indent=4) + "\n")

    verify(billing_path, billing)
    verify(usage_path, usage)

    print(f"Embedded controls in {billing_path.name}")
    print(f"Embedded controls in {usage_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
