#!/usr/bin/env python3
"""Replace packaged kibana/links savedObjectRef nav with inline links panels.

package-spec does not allow kibana/links/ (format_version 3.x). Inline links
panels on each dashboard are valid and match the pre-shared-asset shape.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

INTEGRATIONS = Path(__file__).resolve().parents[2] / "integrations"
DASHBOARD_DIR = INTEGRATIONS / "packages/chargeback/kibana/dashboard"
LINKS_DIR = INTEGRATIONS / "packages/chargeback/kibana/links"
NAV_LINKS_ID = "chargeback-e97bc218-1acc-474f-b47c-58d09ce83daf"

DASHBOARD_IDS = {
    "billing": "chargeback-b6cecac0-ebe7-43de-bfc6-1ff287ad860a",
    "usage": "chargeback-21588d0e-fb6a-4f76-ad2f-cd7b3d7a3d7c",
    "config": "chargeback-7ec9743a-ccfa-43d2-8311-2f25893dadae",
}

# Stable per-dashboard panel + link entry IDs (from prior inline export).
NAV_SPECS = {
    DASHBOARD_IDS["billing"]: {
        "panel_id": "c39e2f18-f301-40b0-8f59-2a8547ddffbb",
        "link_ids": [
            "cba353e8-3f35-e498-55ec-72e282a480ce",
            "4b7dbb50-c024-8666-14ba-f6b4487eced7",
            "3a5f90b9-a885-f9a9-6669-49f360b72b47",
        ],
    },
    DASHBOARD_IDS["usage"]: {
        "panel_id": "13cd2a6a-ec89-4553-ae64-cbc347770aa5",
        "link_ids": [
            "a11b22c3-d44e-455f-8660-778899001122",
            "b22c33d4-e55f-4660-9771-889900112233",
            "c33d44e5-f660-4771-a882-990011223344",
        ],
    },
    DASHBOARD_IDS["config"]: {
        "panel_id": "98c09b2b-c68f-ac41-8625-0fc04714f44f",
        "link_ids": [
            "cba353e8-3f35-e498-55ec-72e282a480ce",
            "4b7dbb50-c024-8666-14ba-f6b4487eced7",
            "3a5f90b9-a885-f9a9-6669-49f360b72b47",
        ],
    },
}

LINK_TARGETS = [
    (DASHBOARD_IDS["billing"], "[Chargeback] Billing Components Overview"),
    (DASHBOARD_IDS["usage"], "[Chargeback] Usage & Cost Allocation"),
    (DASHBOARD_IDS["config"], "[Chargeback] Configuration"),
]


def make_inline_nav(panel_id: str, link_ids: list[str]) -> tuple[dict, list[dict]]:
    links = []
    refs = []
    for order, (link_id, (dash_id, label)) in enumerate(zip(link_ids, LINK_TARGETS)):
        dest = f"link_{link_id}_dashboard"
        links.append(
            {
                "destinationRefName": dest,
                "id": link_id,
                "label": label,
                "options": {
                    "openInNewTab": False,
                    "useCurrentDateRange": True,
                    "useCurrentFilters": True,
                },
                "order": order,
                "type": "dashboardLink",
            }
        )
        refs.append(
            {
                "id": dash_id,
                "name": f"{panel_id}:{dest}",
                "type": "dashboard",
            }
        )

    panel = {
        "embeddableConfig": {
            "attributes": {
                "layout": "horizontal",
                "links": links,
            },
            "enhancements": {},
            "hide_title": True,
        },
        "gridData": {"h": 2, "i": panel_id, "w": 48, "x": 0, "y": 0},
        "panelIndex": panel_id,
        "type": "links",
    }
    return panel, refs


def strip_old_nav_refs(references: list[dict], panel_id: str) -> list[dict]:
    kept = []
    for ref in references:
        name = ref.get("name", "")
        if ref.get("type") == "links":
            continue
        if ref.get("type") == "dashboard" and (
            name.startswith(f"{panel_id}:link_") or ":link_" in name and name.endswith("_dashboard")
        ):
            # drop old per-panel link refs; re-added below
            if name.startswith(f"{panel_id}:"):
                continue
        if ref.get("id") == NAV_LINKS_ID:
            continue
        kept.append(ref)
    return kept


def is_top_nav_panel(panel: dict) -> bool:
    grid = panel.get("gridData") or {}
    if grid.get("y") != 0 or grid.get("sectionId"):
        return False
    return panel.get("type") in ("links", "markdown")


def apply_dashboard(path: Path, dash_id: str) -> None:
    spec = NAV_SPECS[dash_id]
    panel_id = spec["panel_id"]
    panel, link_refs = make_inline_nav(panel_id, spec["link_ids"])

    obj = json.loads(path.read_text())
    panels = [p for p in obj["attributes"]["panelsJSON"] if not is_top_nav_panel(p)]
    panels.insert(0, panel)
    obj["attributes"]["panelsJSON"] = panels

    refs = strip_old_nav_refs(obj.get("references", []), panel_id)
    # Also strip any remaining *:savedObjectRef links refs
    refs = [r for r in refs if not (r.get("type") == "links" or r.get("name", "").endswith(":savedObjectRef"))]
    obj["references"] = link_refs + refs

    path.write_text(json.dumps(obj, indent=4) + "\n")
    print(f"OK {path.name}: inline links nav ({panel_id[:8]}…)")


def main() -> int:
    for dash_id in NAV_SPECS:
        apply_dashboard(DASHBOARD_DIR / f"{dash_id}.json", dash_id)

    if LINKS_DIR.is_dir():
        for f in LINKS_DIR.glob("*.json"):
            f.unlink()
            print(f"Removed {f.relative_to(INTEGRATIONS)}")
        try:
            LINKS_DIR.rmdir()
            print(f"Removed {LINKS_DIR.relative_to(INTEGRATIONS)}/")
        except OSError:
            pass

    # Verify no packaged links asset and no savedObjectRef to it
    assert not LINKS_DIR.exists() or not any(LINKS_DIR.glob("*.json")), LINKS_DIR
    for dash_id in NAV_SPECS:
        obj = json.loads((DASHBOARD_DIR / f"{dash_id}.json").read_text())
        p0 = obj["attributes"]["panelsJSON"][0]
        assert p0["type"] == "links"
        assert "attributes" in p0["embeddableConfig"]
        assert not any(r.get("type") == "links" for r in obj.get("references", []))
        assert any(
            r.get("type") == "dashboard" and ":link_" in r.get("name", "")
            for r in obj.get("references", [])
        ), dash_id

    print("OK all dashboards use inline links; kibana/links removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
