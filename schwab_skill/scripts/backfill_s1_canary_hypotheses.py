#!/usr/bin/env python3
"""Backfill S1 paper hypotheses from saved PEAD shadow-scan artifacts.

Only dq=ok artifacts. Never sets ALLOW_LIVE. Dedupes by ticker+fingerprint+ET date.

Usage (from schwab_skill):
  python scripts/backfill_s1_canary_hypotheses.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def main() -> int:
    from core.r7_promotion import r7_may_promote
    from hypothesis_ledger import backfill_s1_from_scan_artifact, load_ledger_data

    art_dir = SKILL_DIR / "validation_artifacts"
    paths = sorted(art_dir.glob("pead_primary_shadow_scan_*.json"))
    total = {"appended": 0, "skipped": 0, "errors": 0, "files": 0}
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"SKIP {path.name}: {exc}")
            continue
        if not isinstance(payload, dict):
            continue
        out = backfill_s1_from_scan_artifact(payload, SKILL_DIR, require_dq_ok=True)
        if out.get("appended") or out.get("skipped") or out.get("errors"):
            print(
                f"{path.name} appended={out.get('appended')} "
                f"skipped={out.get('skipped')} errors={out.get('errors')}"
            )
        total["appended"] += int(out.get("appended") or 0)
        total["skipped"] += int(out.get("skipped") or 0)
        total["errors"] += int(out.get("errors") or 0)
        total["files"] += 1

    records = [r for r in (load_ledger_data(SKILL_DIR).get("records") or []) if isinstance(r, dict)]
    gate = r7_may_promote(records, sleeve_id="S1", offline_floors_ok=True)
    summ = gate.get("summary") or {}
    print(
        f"backfill files={total['files']} appended={total['appended']} "
        f"skipped={total['skipped']} errors={total['errors']}"
    )
    print(
        f"r7_s1 n={summ.get('n')}/40 expectancy={summ.get('expectancy')} "
        f"ok={gate.get('ok')} reasons={gate.get('reasons')} ALLOW_LIVE=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
