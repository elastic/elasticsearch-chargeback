#!/usr/bin/env python3
"""Apply __chargeback_unfiltered__ sentinel to Chargeback Billing/Usage dashboard JSON.

Empty multi-select ES|QL params ([]) fail on Elasticsearch 9.4+. Shipping
selected_options: ["__chargeback_unfiltered__"] on controls plus CASE(...)
panel filters treats that sentinel as "no filter" without stack-specific values.

Controls are embedded in panelsJSON (below links nav) so vis panels receive variables.

See scripts/DESIGN_ESQL_EMPTY_PARAMS.md.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SENTINEL = "__chargeback_unfiltered__"
DASHBOARD_DIR = (
    Path(__file__).resolve().parents[2] / "integrations" / "packages/chargeback/kibana/dashboard"
)
DASHBOARDS = (
    "chargeback-b6cecac0-ebe7-43de-bfc6-1ff287ad860a",
    "chargeback-21588d0e-fb6a-4f76-ad2f-cd7b3d7a3d7c",
)

OLD_FILTER = re.compile(
    r"\(\?(?P<var>\w+) IS NULL OR MV_INTERSECTS\(\?(?P=var), (?P<field>[a-z_]+)\)\)"
)


def sentinel_clause(var: str, field: str) -> str:
    return (
        f'(CASE(?{var} IS NULL OR MV_INTERSECTS(?{var}, ["{SENTINEL}"]), '
        f"true, MV_INTERSECTS(?{var}, {field})))"
    )


ALL_SENTINEL = re.compile(
    r'MV_INTERSECTS\(\?(?P<var>\w+), \["All"\]\)'
)


def rewrite_esql(text: str) -> str:
    text = OLD_FILTER.sub(
        lambda m: sentinel_clause(m.group("var"), m.group("field")),
        text,
    )
    # Legacy "All" sentinel in CASE clauses → portable unfiltered token
    text = ALL_SENTINEL.sub(
        lambda m: f'MV_INTERSECTS(?{m.group("var")}, ["{SENTINEL}"])',
        text,
    )
    return text


def set_control_selected_options(obj: dict) -> None:
    attrs = obj["attributes"]

    for panel in attrs.get("panelsJSON", []):
        if panel.get("type") != "esql_control":
            continue
        panel["embeddableConfig"]["selected_options"] = [SENTINEL]

    pinned = attrs.get("pinned_panels")
    if isinstance(pinned, str):
        pinned = json.loads(pinned)
        attrs["pinned_panels"] = pinned
    if pinned and pinned.get("panels"):
        for panel in pinned["panels"].values():
            if panel.get("type") == "esql_control":
                panel["config"]["selected_options"] = [SENTINEL]


def apply_sentinel(obj: dict) -> dict:
    text = rewrite_esql(json.dumps(obj))
    obj = json.loads(text)
    set_control_selected_options(obj)
    return obj


def _normalize_attrs(attrs: dict) -> dict:
    out = dict(attrs)
    if isinstance(out.get("panelsJSON"), str):
        out["panelsJSON"] = json.loads(out["panelsJSON"])
    if isinstance(out.get("pinned_panels"), str):
        out["pinned_panels"] = json.loads(out["pinned_panels"])
    return out


def verify(path: Path, obj: dict | None = None) -> None:
    obj = obj if obj is not None else json.loads(path.read_text())
    attrs = _normalize_attrs(obj["attributes"])
    obj = {**obj, "attributes": attrs}
    text = json.dumps(obj)

    assert SENTINEL in text, f"{path.name}: missing sentinel"
    assert '"selected_options": []' not in text, f"{path.name}: empty selected_options"
    assert "MV_CONTAINS" not in text, f"{path.name}: MV_CONTAINS found"
    assert not OLD_FILTER.search(text), f"{path.name}: old filter pattern remains"
    assert "MV_APPEND" not in text, f"{path.name}: All-option MV_APPEND pattern must not be used"

    embedded = [p for p in attrs.get("panelsJSON", []) if p.get("type") == "esql_control"]
    pinned = attrs.get("pinned_panels")
    if isinstance(pinned, str):
        pinned = json.loads(pinned)
    pinned_controls = list((pinned or {}).get("panels", {}).values())

    assert embedded or pinned_controls, f"{path.name}: esql controls required (embedded or pinned)"
    assert not (embedded and pinned_controls), (
        f"{path.name}: use embedded esql_control OR pinned_panels, not both"
    )

    if embedded:
        for panel in embedded:
            cfg = panel["embeddableConfig"]
            assert cfg.get("selected_options") == [SENTINEL], (
                f"{path.name}: control {cfg.get('variable_name')} selected_options={cfg.get('selected_options')!r}"
            )
            assert cfg.get("esql_query", "").strip(), cfg.get("variable_name")
            assert "MV_CONTAINS" not in cfg["esql_query"], cfg.get("variable_name")
            assert "MV_INTERSECTS" not in cfg["esql_query"], cfg.get("variable_name")

    assert "CASE(?dg_selected" in text and SENTINEL in text, (
        f"{path.name}: panel ES|QL missing sentinel CASE filter"
    )
    assert any(p.get("type") == "links" for p in attrs.get("panelsJSON", [])), (
        f"{path.name}: missing links nav panel"
    )
    assert not any(p.get("type") == "markdown" and p.get("gridData", {}).get("y") == 0 for p in attrs.get("panelsJSON", [])), (
        f"{path.name}: markdown nav at y=0 must be removed; use links panel"
    )


def main() -> int:
    for dash_id in DASHBOARDS:
        path = DASHBOARD_DIR / f"{dash_id}.json"
        obj = apply_sentinel(json.loads(path.read_text()))
        verify(path, obj)
        path.write_text(json.dumps(obj, indent=4) + "\n")
        print(f"OK {path.name} ({json.dumps(obj).count(SENTINEL)} sentinel references)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
