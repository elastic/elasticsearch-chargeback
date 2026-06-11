#!/usr/bin/env python3
"""Add shared [Chargeback] Navigation links saved object to all Chargeback dashboards.

Replaces inline or savedObjectRef links panels at y=0 with a savedObjectRef to the
packaged links asset. Each dashboard gets its own panelIndex (do not reuse across dashboards).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

INTEGRATIONS = Path(__file__).resolve().parents[2] / "integrations"
DASHBOARD_DIR = INTEGRATIONS / "packages/chargeback/kibana/dashboard"
LINKS_PATH = INTEGRATIONS / "packages/chargeback/kibana/links/chargeback-e97bc218-1acc-474f-b47c-58d09ce83daf.json"

NAV_LINKS_ID = "chargeback-e97bc218-1acc-474f-b47c-58d09ce83daf"

# Stable per-dashboard panel IDs (must not be shared across dashboards).
NAV_PANEL_IDS = {
    "chargeback-b6cecac0-ebe7-43de-bfc6-1ff287ad860a": "c39e2f18-f301-40b0-8f59-2a8547ddffbb",
    "chargeback-21588d0e-fb6a-4f76-ad2f-cd7b3d7a3d7c": "13cd2a6a-ec89-4553-ae64-cbc347770aa5",
    "chargeback-7ec9743a-ccfa-43d2-8311-2f25893dadae": "98c09b2b-c68f-ac41-8625-0fc04714f44f",
}

DASHBOARDS = tuple(NAV_PANEL_IDS.keys())


def nav_panel(panel_id: str) -> dict:
    return {
        "type": "links",
        "embeddableConfig": {"hide_title": True},
        "panelIndex": panel_id,
        "gridData": {
            "h": 2,
            "i": panel_id,
            "w": 48,
            "x": 0,
            "y": 0,
        },
    }


def nav_reference(panel_id: str) -> dict:
    return {
        "id": NAV_LINKS_ID,
        "name": f"{panel_id}:savedObjectRef",
        "type": "links",
    }


def is_top_nav_panel(panel: dict) -> bool:
    grid = panel.get("gridData") or {}
    if grid.get("y") != 0 or grid.get("sectionId"):
        return False
    if panel.get("type") == "markdown":
        return True
    return panel.get("type") == "links"


def strip_nav_references(references: list[dict], panel_id: str) -> list[dict]:
    nav_name = f"{panel_id}:savedObjectRef"
    kept = []
    for ref in references:
        name = ref.get("name", "")
        if ref.get("type") == "dashboard" and ":link_" in name:
            continue
        if ref.get("type") == "links" and (name.endswith(":savedObjectRef") or ref.get("id") == NAV_LINKS_ID):
            continue
        kept.append(ref)
    return kept


def apply_dashboard(path: Path, dash_id: str) -> None:
    panel_id = NAV_PANEL_IDS[dash_id]
    obj = json.loads(path.read_text())
    panels = obj["attributes"]["panelsJSON"]
    panels = [p for p in panels if not is_top_nav_panel(p)]
    panels.insert(0, nav_panel(panel_id))
    obj["attributes"]["panelsJSON"] = panels

    refs = strip_nav_references(obj.get("references", []), panel_id)
    refs.insert(0, nav_reference(panel_id))
    obj["references"] = refs

    path.write_text(json.dumps(obj, indent=4) + "\n")
    print(f"OK {path.name}: nav panel {panel_id[:8]}… at y=0")


def main() -> int:
    if not LINKS_PATH.is_file():
        raise SystemExit(f"Missing links asset: {LINKS_PATH}")

    for dash_id in DASHBOARDS:
        apply_dashboard(DASHBOARD_DIR / f"{dash_id}.json", dash_id)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from apply_chargeback_esql_sentinel import verify

    for dash_id in DASHBOARDS[:2]:
        path = DASHBOARD_DIR / f"{dash_id}.json"
        verify(path)
        print(f"OK {path.name}: sentinel intact")

    for dash_id in DASHBOARDS:
        path = DASHBOARD_DIR / f"{dash_id}.json"
        obj = json.loads(path.read_text())
        panel_id = NAV_PANEL_IDS[dash_id]
        p0 = obj["attributes"]["panelsJSON"][0]
        assert p0["type"] == "links" and p0["panelIndex"] == panel_id, dash_id
        assert nav_reference(panel_id) in obj["references"], dash_id
    print("OK all dashboards have unique nav panel refs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
