#!/usr/bin/env python3
"""Phase 0: freeze multi-sleeve baseline artifact (no behavior change)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def _safe_get(name: str, default: object = None):
    try:
        import config

        fn = getattr(config, name, None)
        if callable(fn):
            return fn(SKILL_DIR)
    except Exception as exc:
        return {"error": str(exc), "default": default}
    return default


def build_baseline() -> dict:
    now = datetime.now(timezone.utc).strftime("%Y%m%d")
    stack = {
        "ENTRY_TIMING_SHADOW_MODE": _safe_get("get_entry_timing_shadow_mode"),
        "ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT": _safe_get("get_entry_shadow_min_breakout_buffer_pct"),
        "EXIT_MANAGER_MODE": _safe_get("get_exit_manager_mode"),
        "EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL": _safe_get("get_exit_min_hold_days_before_trail"),
        "EXIT_MAX_HOLD_DAYS": _safe_get("get_exit_max_hold_days"),
        "RANK_FILTER_V2_MODE": _safe_get("get_rank_filter_v2_mode"),
        "RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2": _safe_get(
            "get_rank_filter_shadow_min_percentile_rank_v2"
        ),
        "PTS_52W_CAP_MODE": _safe_get("get_pts_52w_cap_mode"),
        "PTS_52W_CAP_MAX": _safe_get("get_pts_52w_cap_max"),
        "COUNTERFACTUAL_LOGGING_ENABLED": _safe_get("get_counterfactual_logging_enabled"),
        "HYPOTHESIS_LEDGER_ENABLED": _safe_get("get_hypothesis_ledger_enabled"),
        "ALLOCATOR_MODE": _safe_get("get_allocator_mode", "off"),
        "STRATEGY_PEAD_PRIMARY_MODE": _safe_get("get_strategy_pead_primary_mode"),
    }
    # Verify ledger append path with a dry fixture record (tmp under artifacts).
    ledger_ok = False
    ledger_error = None
    try:
        from hypothesis_ledger import append_hypothesis, record_from_signal

        fixture = {
            "ticker": "BASELINE",
            "price": 1.0,
            "signal_score": 1.0,
            "sleeve_id": "S0",
        }
        tmp = SKILL_DIR / "validation_artifacts" / f"_baseline_ledger_probe_{now}"
        tmp.mkdir(parents=True, exist_ok=True)
        rid = append_hypothesis(record_from_signal(fixture, skill_dir=tmp), skill_dir=tmp)
        ledger_ok = bool(rid)
    except Exception as exc:
        ledger_error = str(exc)

    return {
        "schema": 1,
        "phase": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "do_not_regress": [
            "Keep S0 live stack: 1% breakout buffer + exit grace 15/40 + rank-v2 p76 + pts_52w<=37",
            "See wiki/signal-quality-rollout and SIGNAL_QUALITY_ROLLOUT.md",
            "Do not transfer Stage2 buffer/pts/rank defaults onto PEAD S1",
            "ALLOCATOR_MODE stays off until Phase 2 shadow exit criteria pass",
        ],
        "live_stack_snapshot": stack,
        "ledger_append_probe_ok": ledger_ok,
        "ledger_append_probe_error": ledger_error,
        "constitution": "wiki/multi-sleeve-trading-system-constitution.md",
        "build_plan": "wiki/multi-sleeve-phased-build-plan.md",
    }


def main() -> int:
    out_dir = SKILL_DIR / "validation_artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_baseline()
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = out_dir / f"multi_sleeve_baseline_{day}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # Also write a stable pointer for wiki links
    (out_dir / "multi_sleeve_baseline_latest.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {path}")
    if not payload.get("ledger_append_probe_ok"):
        print("WARN: ledger probe failed:", payload.get("ledger_append_probe_error"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
