#!/usr/bin/env python3
"""Append REGIME_V2 / CORRELATION_GUARD shadow evidence from last_scan.

Usage (from schwab_skill/):
  python scripts/append_plugin_shadow_evidence.py
  python scripts/append_plugin_shadow_evidence.py --label post_rth
  python scripts/append_plugin_shadow_evidence.py --summarize-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="", help="Optional scan label stored on the row")
    parser.add_argument(
        "--summarize-only",
        action="store_true",
        help="Print ledger summary without appending",
    )
    args = parser.parse_args()

    from core.entry_timing_live_compare import load_last_scan_diagnostics
    from core.plugin_shadow_evidence import (
        append_plugin_shadow_record,
        build_plugin_shadow_record,
        ledger_path,
        load_plugin_shadow_records,
        summarize_plugin_shadow_records,
    )

    if args.summarize_only:
        rows = load_plugin_shadow_records(SKILL_DIR)
        summary = summarize_plugin_shadow_records(rows)
        print(json.dumps(summary, indent=2))
        print(f"ledger={ledger_path(SKILL_DIR)}")
        return 0

    diagnostics, meta = load_last_scan_diagnostics(sqlite_path=SKILL_DIR / "webapp" / "webapp.db")
    if diagnostics is None:
        print(f"FAIL: no last_scan ({meta.get('error')})")
        return 1

    record = build_plugin_shadow_record(
        diagnostics,
        scan_at=meta.get("scan_at"),
        signals_found=meta.get("signals_found"),
        scan_label=args.label or None,
    )
    if record is None:
        print("FAIL: regime_v2 and correlation_guard both off — nothing to record")
        return 1

    path = append_plugin_shadow_record(SKILL_DIR, record)
    rows = load_plugin_shadow_records(SKILL_DIR)
    summary = summarize_plugin_shadow_records(rows)
    print(f"APPENDED {path}")
    print(
        f"regime={record['regime_v2']['mode']} score={record['regime_v2']['score']} "
        f"bucket={record['regime_v2']['bucket']} | "
        f"corr={record['correlation_guard']['mode']} "
        f"would_demote={record['correlation_guard']['would_demote']}"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
