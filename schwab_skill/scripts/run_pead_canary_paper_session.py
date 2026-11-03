#!/usr/bin/env python3
"""One PEAD paper dual-admit session + compare + S1 R7 status.

Qualifying session: weekday RTH (09:30–16:00 ET) and data_quality=ok.
Never sets STRATEGY_PEAD_PRIMARY_ALLOW_LIVE=true.

Usage (from schwab_skill):
  python scripts/run_pead_canary_paper_session.py
  python scripts/run_pead_canary_paper_session.py --allow-non-rth   # pipeline smoke only
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

TZ_NY = ZoneInfo("America/New_York")


def _is_rth(now: datetime | None = None) -> bool:
    from core.plugin_shadow_evidence import evidence_session_is_rth

    clock = now or datetime.now(TZ_NY)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=TZ_NY)
    return evidence_session_is_rth({"scan_at": clock.astimezone(TZ_NY).isoformat()})


def _run(args: list[str]) -> int:
    print("+", " ".join(args), flush=True)
    proc = subprocess.run(args, cwd=str(SKILL_DIR))
    return int(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=0,
        help="0 = full watchlist (default). Use a small N for smoke.",
    )
    parser.add_argument(
        "--label",
        default="",
        help="Scan label. Default full_sp1500_rth_YYYYMMDD",
    )
    parser.add_argument(
        "--allow-non-rth",
        action="store_true",
        help="Run after hours for pipeline smoke. Session will not qualify.",
    )
    parser.add_argument(
        "--skip-scan",
        action="store_true",
        help="Compare + R7 status on last_scan only.",
    )
    args = parser.parse_args()

    from core.env_local import (
        pead_canary_paper_file_readiness,
        signal_stack_enforced_file_readiness,
    )

    env_path = SKILL_DIR / ".env"
    pead = pead_canary_paper_file_readiness(env_path)
    if pead.get("allow_live"):
        print("FAIL: STRATEGY_PEAD_PRIMARY_ALLOW_LIVE=true — refuse this cycle")
        return 1
    if not pead.get("ready"):
        print("FAIL: PEAD paper env not ready. Run apply_pead_canary_paper_env.py")
        for item in pead.get("missing_env") or []:
            print(f"- {item}")
        return 1

    stack = signal_stack_enforced_file_readiness(env_path)
    print(
        f"pead_paper ready allow_live=false top_n={pead.get('rank_top_n')} "
        f"stack_exit={stack.get('exit_min_hold_days_before_trail')}/"
        f"{stack.get('exit_max_hold_days')}"
    )

    now = datetime.now(TZ_NY)
    rth = _is_rth(now)
    if not rth and not args.allow_non_rth and not args.skip_scan:
        print(
            f"SKIP: not RTH (now {now.isoformat()} ET). "
            "Re-run between 09:30–16:00 ET, or pass --allow-non-rth for smoke."
        )
        return 2

    day = now.strftime("%Y%m%d")
    label = args.label or f"full_sp1500_rth_{day}"
    if not rth:
        label = args.label or f"full_sp1500_nonrth_{day}"
        print("WARN: non-RTH session will not count as the enablement-bar scan")

    if not args.skip_scan:
        rc = _run(
            [
                sys.executable,
                "scripts/run_pead_primary_shadow_scan.py",
                "--max-tickers",
                str(int(args.max_tickers)),
                "--label",
                label,
            ]
        )
        if rc != 0:
            return rc

    rc = _run(
        [
            sys.executable,
            "scripts/compare_pead_primary_shadow_to_offline.py",
            "--write-artifact",
        ]
    )
    if rc != 0:
        return rc

    _run([sys.executable, "scripts/summarize_pead_dual_admit_sessions.py", "--limit", "8"])

    from core.pead_canary_splits import format_s1_split_lines, summarize_s1_splits
    from core.r7_promotion import r7_may_promote
    from hypothesis_ledger import load_ledger_data

    records = [r for r in (load_ledger_data(SKILL_DIR).get("records") or []) if isinstance(r, dict)]
    gate = r7_may_promote(records, sleeve_id="S1", offline_floors_ok=True)
    summ = gate.get("summary") or {}
    n = int(summ.get("n") or 0)
    print(
        f"r7_s1 n={n}/40 expectancy={summ.get('expectancy')} "
        f"ok={gate.get('ok')} reasons={gate.get('reasons')} "
        "ALLOW_LIVE=false"
    )
    for line in format_s1_split_lines(summarize_s1_splits(records, horizon="5", skill_dir=SKILL_DIR)):
        print(line)
    if n < 40:
        print("HOLD: collect more dq=ok canary names before any executable talk")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
