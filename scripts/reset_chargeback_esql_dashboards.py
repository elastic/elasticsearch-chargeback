#!/usr/bin/env python3
"""Reset Chargeback Billing/Usage dashboards on Kibana from package source.

Thin wrapper around deploy_chargeback_dashboards_to_kibana.py for the two ES|QL dashboards.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def main() -> int:
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "deploy_chargeback_dashboards_to_kibana.py"),
        "--billing",
        "--usage",
    ]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
