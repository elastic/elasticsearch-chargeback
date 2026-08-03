#!/usr/bin/env python3
"""Add ds_type / ds_namespace ES|QL controls and panel filters to Usage dashboard (0.5.1).

- Fixes legacy CASE filters that still use ["All"] to use __chargeback_unfiltered__
- Embeds six Usage controls at width 8 (one row)
- Injects ds_type_selected / ds_namespace_selected WHERE clauses after datastream_selected
"""
from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from apply_chargeback_esql_sentinel import SENTINEL, apply_sentinel, verify  # noqa: E402
from embed_chargeback_esql_controls import (  # noqa: E402
    CONTROL_HEIGHT,
    CONTROL_ROW_Y,
    DASHBOARD_DIR,
    USAGE_CONTROLS,
    USAGE_ID,
    make_esql_control_panel,
)

USAGE_CONTROL_WIDTH = 8  # 6 controls × 8 = 48 grid units

DATASTREAM_WHERE = re.compile(
    r"\| WHERE \(CASE\(\?datastream_selected IS NULL OR MV_INTERSECTS\(\?datastream_selected, "
    r'\["(?:All|__chargeback_unfiltered__)"\]\), true, MV_INTERSECTS\(\?datastream_selected, datastream\)\)\)'
)

DS_TYPE_CLAUSE = (
    f'| WHERE (CASE(?ds_type_selected IS NULL OR MV_INTERSECTS(?ds_type_selected, ["{SENTINEL}"]), '
    f"true, MV_INTERSECTS(?ds_type_selected, ds_type)))"
)
DS_NAMESPACE_CLAUSE = (
    f'| WHERE (CASE(?ds_namespace_selected IS NULL OR MV_INTERSECTS(?ds_namespace_selected, ["{SENTINEL}"]), '
    f"true, MV_INTERSECTS(?ds_namespace_selected, ds_namespace)))"
)


def inject_ds_filters(esql: str) -> str:
    if "?ds_type_selected" in esql:
        return esql

    def _repl(match: re.Match[str]) -> str:
        return f"{match.group(0)}\n{DS_TYPE_CLAUSE}\n{DS_NAMESPACE_CLAUSE}"

    updated, n = DATASTREAM_WHERE.subn(_repl, esql)
    if n == 0 and "datastream_selected" in esql:
        raise RuntimeError("datastream_selected present but WHERE pattern did not match")
    return updated


def walk_inject(obj: object) -> object:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "esql" and isinstance(v, str) and "datastream_selected" in v:
                out[k] = inject_ds_filters(v)
            else:
                out[k] = walk_inject(v)
        return out
    if isinstance(obj, list):
        return [walk_inject(v) for v in obj]
    return obj


def rebuild_usage_controls(obj: dict) -> dict:
    obj = deepcopy(obj)
    attrs = obj["attributes"]
    panels = [p for p in attrs["panelsJSON"] if p.get("type") != "esql_control"]
    nav_idx = next((i for i, p in enumerate(panels) if p.get("type") == "links"), None)

    control_panels = [
        make_esql_control_panel(
            panel_id=spec["panel_id"],
            x=idx * USAGE_CONTROL_WIDTH,
            y=CONTROL_ROW_Y,
            variable_name=spec["variable_name"],
            title=spec["title"],
            esql_query=spec["esql_query"],
        )
        for idx, spec in enumerate(USAGE_CONTROLS)
    ]
    # Override width for six-across layout
    for panel in control_panels:
        panel["gridData"]["w"] = USAGE_CONTROL_WIDTH
        panel["gridData"]["h"] = CONTROL_HEIGHT

    if nav_idx is not None:
        panels = panels[: nav_idx + 1] + control_panels + panels[nav_idx + 1 :]
    else:
        panels = control_panels + panels
    attrs["panelsJSON"] = panels
    attrs.pop("pinned_panels", None)
    return obj


def main() -> int:
    usage_path = DASHBOARD_DIR / f"{USAGE_ID}.json"
    if not usage_path.exists():
        # Sibling layout fallback
        alt = (
            Path(__file__).resolve().parents[1].parent
            / "integrations"
            / "packages/chargeback/kibana/dashboard"
            / f"{USAGE_ID}.json"
        )
        if alt.exists():
            usage_path = alt
        else:
            raise SystemExit(f"Usage dashboard not found: {usage_path}")

    obj = json.loads(usage_path.read_text())
    obj = apply_sentinel(obj)  # All → sentinel + selected_options
    obj = walk_inject(obj)
    obj = rebuild_usage_controls(obj)
    obj = apply_sentinel(obj)

    usage_path.write_text(json.dumps(obj, indent=4) + "\n")
    verify(usage_path, obj)

    text = json.dumps(obj)
    assert text.count("ds_type_selected") > 10, "expected ds_type_selected in panels"
    assert text.count("ds_namespace_selected") > 10, "expected ds_namespace_selected in panels"
    assert '["All"]' not in text, "legacy All sentinel remains in Usage dashboard"
    controls = [p for p in obj["attributes"]["panelsJSON"] if p.get("type") == "esql_control"]
    assert len(controls) == 6, len(controls)
    print(f"OK {usage_path} — {len(controls)} controls, ds_* filters wired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
