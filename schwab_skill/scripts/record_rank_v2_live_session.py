#!/usr/bin/env python3
"""Record a Stage-2d rank-v2 live session from last_scan (p76 post-retune).

No market calls — reads persisted diagnostics and appends an evidence row.

Usage (from schwab_skill/):
  python scripts/record_rank_v2_live_session.py --label post_p76_rth1
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

ART = SKILL_DIR / "validation_artifacts"
LOG_NAME = "rank_filter_v2_live_evidence_log.jsonl"
GUIDANCE_RETENTION_LO = 22.0
GUIDANCE_RETENTION_HI = 35.0


def _retention(evaluated: int, dropped: int) -> float | None:
    if evaluated <= 0:
        return None
    return round(100.0 * max(0, evaluated - dropped) / evaluated, 1)


def build_session_row(
    diagnostics: dict[str, Any],
    *,
    label: str,
    scan_at: str | None,
    signals_found: int | None,
    min_percentile_expected: int = 76,
) -> dict[str, Any]:
    evaluated = int(diagnostics.get("rank_filter_v2_evaluated") or 0)
    dropped = int(
        diagnostics.get("rank_filter_v2_dropped")
        if diagnostics.get("rank_filter_v2_dropped") is not None
        else diagnostics.get("rank_filter_v2_would_drop")
        or 0
    )
    retention = _retention(evaluated, dropped)
    mode = str(diagnostics.get("rank_filter_v2_mode") or "").strip().lower()
    pct_raw = diagnostics.get("rank_filter_v2_min_percentile")
    try:
        pct = int(pct_raw) if pct_raw is not None else None
    except (TypeError, ValueError):
        pct = None
    dq = str(diagnostics.get("data_quality") or "").strip().lower() or None
    in_band = (
        retention is not None
        and GUIDANCE_RETENTION_LO <= float(retention) <= GUIDANCE_RETENTION_HI
    )
    ok = (
        mode == "live"
        and pct == int(min_percentile_expected)
        and evaluated >= 10
        and dq == "ok"
        and bool(in_band)
    )
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "scan_at": scan_at,
        "signals_found": signals_found,
        "data_quality": dq,
        "rank_filter_v2": {
            "mode": mode,
            "min_percentile": pct,
            "threshold": diagnostics.get("rank_filter_v2_threshold"),
            "evaluated": evaluated,
            "dropped": dropped,
            "would_drop": diagnostics.get("rank_filter_v2_would_drop"),
            "retention_pct": retention,
            "guidance_band": [GUIDANCE_RETENTION_LO, GUIDANCE_RETENTION_HI],
            "retention_in_guidance_band": in_band,
        },
        "entry_timing_shadow_mode": diagnostics.get("entry_timing_shadow_mode"),
        "pts_52w_cap_mode": diagnostics.get("pts_52w_cap_mode"),
        "watchlist_size": diagnostics.get("watchlist_size"),
        "stage_a_candidates": diagnostics.get("stage_a_candidates"),
        "stage_a_shortlisted": diagnostics.get("stage_a_shortlisted"),
        "qualifies_post_p76_session": ok,
        "expected_min_percentile": int(min_percentile_expected),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="post_p76_session")
    parser.add_argument("--min-percentile", type=int, default=76)
    parser.add_argument(
        "--min-evaluated",
        type=int,
        default=10,
        help="Warn when evaluated < this (default 10).",
    )
    args = parser.parse_args()

    from core.pead_primary_shadow_compare import load_scan_bundle

    diag, signals, meta = load_scan_bundle(sqlite_path=SKILL_DIR / "webapp" / "webapp.db")
    if not diag:
        print("FAIL: no last_scan diagnostics")
        return 1

    row = build_session_row(
        diag,
        label=str(args.label),
        scan_at=(meta or {}).get("scan_at"),
        signals_found=len(signals or []) if signals is not None else (meta or {}).get("signals_found"),
        min_percentile_expected=int(args.min_percentile),
    )
    ART.mkdir(parents=True, exist_ok=True)
    log_path = ART / LOG_NAME
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap = ART / f"rank_filter_v2_live_session_{stamp}_{args.label}.json"
    snap.write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")

    rf = row["rank_filter_v2"]
    print(f"Wrote {log_path}")
    print(f"Wrote {snap}")
    print(
        f"mode={rf.get('mode')} p{rf.get('min_percentile')} "
        f"eval={rf.get('evaluated')} drop={rf.get('dropped')} "
        f"retention={rf.get('retention_pct')} dq={row.get('data_quality')} "
        f"qualifies={row.get('qualifies_post_p76_session')}"
    )
    if int(rf.get("evaluated") or 0) < int(args.min_evaluated):
        print(
            f"WARN: evaluated<{args.min_evaluated} — too thin for a qualifying RTH session"
        )
        return 2
    if not row.get("qualifies_post_p76_session"):
        print("WARN: session did not meet post-p76 qualify bar (live@p76, dq=ok, band, n>=10)")
        return 3
    print("PASS: qualifying post-p76 session")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
