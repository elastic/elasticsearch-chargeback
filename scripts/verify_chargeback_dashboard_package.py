#!/usr/bin/env python3
"""Verify Chargeback integration dashboard JSON (and optional built zip) ship sentinel + nav.

Exit 0 only when Billing/Usage dashboards have:
- links nav in panelsJSON at y=0 (no markdown nav)
- embedded esql_control panels with selected_options: ["__chargeback_unfiltered__"]
- CASE/MV_INTERSECTS sentinel filters in panel ES|QL
- no empty selected_options or MV_CONTAINS

Usage:
  python3 scripts/verify_chargeback_dashboard_package.py
  python3 scripts/verify_chargeback_dashboard_package.py --zip path/to/chargeback-0.5.0.zip
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from apply_chargeback_esql_sentinel import DASHBOARDS, SENTINEL, verify  # noqa: E402

INTEGRATIONS_ROOT = Path(__file__).resolve().parents[2] / "integrations"
DASHBOARD_DIR = INTEGRATIONS_ROOT / "packages/chargeback/kibana/dashboard"
CONFIG_ID = "chargeback-7ec9743a-ccfa-43d2-8311-2f25893dadae"


def verify_config_obj(obj: dict, label: str) -> None:
    panels = obj["attributes"]["panelsJSON"]
    if isinstance(panels, str):
        panels = json.loads(panels)
    assert any(p.get("type") == "links" for p in panels), f"{label}: missing links nav"
    assert not obj["attributes"].get("pinned_panels"), f"{label}: unexpected pinned_panels"


def verify_config(path: Path) -> None:
    verify_config_obj(json.loads(path.read_text()), path.name)


def verify_source_tree() -> None:
    # package-spec does not allow kibana/links/; nav must be inline on each dashboard
    links_dir = DASHBOARD_DIR.parent / "links"
    assert not links_dir.exists() or not any(links_dir.glob("*.json")), (
        f"Unsupported packaged links asset present under {links_dir}"
    )

    for dash_id in DASHBOARDS:
        path = DASHBOARD_DIR / f"{dash_id}.json"
        if not path.is_file():
            raise SystemExit(f"Missing {path}")
        verify(path)
        print(f"OK source {path.name}")

    config_path = DASHBOARD_DIR / f"{CONFIG_ID}.json"
    verify_config(config_path)
    print(f"OK source {config_path.name}")


def zip_dashboard_member(zf: zipfile.ZipFile, dash_id: str) -> str:
    suffix = f"kibana/dashboard/{dash_id}.json"
    matches = [name for name in zf.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise SystemExit(f"{zf.filename}: expected one {suffix}, found {matches}")
    return matches[0]


def verify_zip(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        for dash_id in DASHBOARDS:
            member = zip_dashboard_member(zf, dash_id)
            raw = zf.read(member)
            obj = json.loads(raw)
            verify(Path(dash_id + ".json"), obj)
            text = raw.decode()
            assert SENTINEL in text, f"{zip_path}:{member} missing sentinel"
            assert '"selected_options": []' not in text, f"{zip_path}:{member} empty selected_options"
            print(f"OK zip {member} ({text.count(SENTINEL)} sentinel refs)")

        config_member = zip_dashboard_member(zf, CONFIG_ID)
        verify_config_obj(json.loads(zf.read(config_member)), f"{zip_path}:{config_member}")
        print(f"OK zip {config_member}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", type=Path, help="Also verify built chargeback zip")
    args = parser.parse_args()

    verify_source_tree()
    if args.zip:
        if not args.zip.is_file():
            print(f"Zip not found: {args.zip}", file=sys.stderr)
            return 1
        verify_zip(args.zip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
