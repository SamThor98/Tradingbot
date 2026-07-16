#!/usr/bin/env python3
"""Run one full-universe scan on the live entry + exit-grace + rank-v2 stack.

Accepts RANK_FILTER_V2_MODE of shadow or live (post Stage 2d promote). No orders.
Persists last_scan and a validation artifact with entry/rank-v2 metrics.
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


def _build_summary(
    *,
    watchlist_size: int,
    signals: list[dict[str, Any]],
    shortlist: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    readiness: dict[str, Any],
    process_modes: dict[str, Any],
) -> dict[str, Any]:
    from core.entry_timing_live_compare import extract_live_entry_shadow_metrics

    live = extract_live_entry_shadow_metrics(diagnostics)
    evaluated = int(diagnostics.get("rank_filter_v2_evaluated") or 0)
    would_drop = int(diagnostics.get("rank_filter_v2_would_drop") or 0)
    retained = max(0, evaluated - would_drop)
    retention_pct = (100.0 * retained / evaluated) if evaluated else None
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "watchlist_size": watchlist_size,
        "signals_found": len(signals),
        "shortlist_signals": len(shortlist),
        "stack_readiness": readiness,
        "process_modes": process_modes,
        "entry_timing": live,
        "provider": {
            "scan_primary_provider_mode": diagnostics.get("scan_primary_provider_mode"),
            "data_provider_primary_count": diagnostics.get("data_provider_primary_count"),
            "data_provider_fallback_count": diagnostics.get("data_provider_fallback_count"),
            "data_provider_unknown_count": diagnostics.get("data_provider_unknown_count"),
            "silent_fallback_count": diagnostics.get("silent_fallback_count"),
            "primary_provider_filtered": diagnostics.get("primary_provider_filtered"),
            "regime_history_provider": diagnostics.get("regime_history_provider"),
        },
        "rank_filter_v2": {
            "mode": diagnostics.get("rank_filter_v2_mode"),
            "min_percentile": diagnostics.get("rank_filter_v2_min_percentile"),
            "evaluated": evaluated,
            "threshold": diagnostics.get("rank_filter_v2_threshold"),
            "would_drop": would_drop,
            "dropped": diagnostics.get("rank_filter_v2_dropped"),
            "retained": retained,
            "retention_pct": retention_pct,
            "skipped": diagnostics.get("rank_filter_v2_skipped"),
        },
        "data_quality": diagnostics.get("data_quality"),
        "data_quality_reasons": diagnostics.get("data_quality_reasons"),
        "stage_a_candidates": diagnostics.get("stage_a_candidates"),
        "stage_a_shortlisted": diagnostics.get("stage_a_shortlisted"),
        "entry_timing_blocked": diagnostics.get("entry_timing_blocked"),
        "entry_timing_live_enforced": diagnostics.get("entry_timing_live_enforced"),
        "entry_timing_shadow_profile": diagnostics.get("entry_timing_shadow_profile"),
        "entry_timing_shadow_mode": diagnostics.get("entry_timing_shadow_mode"),
        "quality_gates_filtered": diagnostics.get("quality_gates_filtered"),
        "self_study_filtered": diagnostics.get("self_study_filtered"),
        "diagnostics_subset": {
            k: diagnostics.get(k)
            for k in (
                "watchlist_size",
                "stage2_fail",
                "vcp_fail",
                "scan_stage_a_ms",
                "scan_stage_b_ms",
                "exceptions",
                "event_risk_blocked",
                "regime_v2_blocked",
            )
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="", help="Optional label suffix for artifact filename")
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    from config import (
        get_entry_timing_shadow_mode,
        get_exit_manager_mode,
        get_rank_filter_v2_mode,
        get_schwab_only_data,
    )
    from core.env_local import (
        SIGNAL_STACK_ENFORCED_ENV,
        reload_env_file_into_process,
        restore_process_env,
        signal_stack_enforced_file_readiness,
    )
    from core.scan_service import run_scan
    from signal_scanner import _load_watchlist

    env_path = SKILL_DIR / ".env"
    readiness = signal_stack_enforced_file_readiness(env_path)
    if not readiness.get("ready"):
        print("FAIL: signal stack enforced env not ready")
        for item in readiness.get("missing_env") or []:
            print(f"- {item}")
        return 1
    rank_mode = str(readiness.get("rank_filter_v2_mode") or "").lower()
    if rank_mode not in {"shadow", "live"}:
        print(f"FAIL: RANK_FILTER_V2_MODE must be shadow or live (got {rank_mode!r})")
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = f"_{args.label}" if args.label else ""
    out_path = SKILL_DIR / "validation_artifacts" / f"full_universe_stack_scan_{stamp}{label}.json"

    saved = reload_env_file_into_process(env_path, keys=list(SIGNAL_STACK_ENFORCED_ENV.keys()))
    try:
        process_modes = {
            "entry_timing": get_entry_timing_shadow_mode(SKILL_DIR),
            "exit_manager": get_exit_manager_mode(SKILL_DIR),
            "rank_filter_v2": get_rank_filter_v2_mode(SKILL_DIR),
            "schwab_only_data": get_schwab_only_data(SKILL_DIR),
        }
        if process_modes["rank_filter_v2"] not in {"shadow", "live"}:
            print(
                "FAIL: process RANK_FILTER_V2_MODE must be shadow or live "
                f"(got {process_modes['rank_filter_v2']!r})"
            )
            return 1

        watchlist = list(_load_watchlist(SKILL_DIR))
        print(
            f"Running full-universe scan on {len(watchlist)} tickers "
            f"(entry={process_modes['entry_timing']} exit={process_modes['exit_manager']} "
            f"rank_v2={process_modes['rank_filter_v2']}; no orders)…",
            flush=True,
        )
        scan_out = run_scan(skill_dir=SKILL_DIR, watchlist_override=watchlist)
        diagnostics = dict(scan_out.diagnostics or {})
        summary = _build_summary(
            watchlist_size=len(watchlist),
            signals=scan_out.signals,
            shortlist=scan_out.shortlist_signals,
            diagnostics=diagnostics,
            readiness=readiness,
            process_modes=process_modes,
        )

        if not args.no_persist:
            from scripts.run_entry_timing_experiment_scan import _persist_last_scan

            _persist_last_scan(
                sqlite_path=SKILL_DIR / "webapp" / "webapp.db",
                last_scan={
                    "at": summary["at"],
                    "signals_found": len(scan_out.signals),
                    "signals": scan_out.signals[:200],
                    "shortlist_signals": scan_out.shortlist_signals[:200],
                    "diagnostics": diagnostics,
                    "diagnostics_summary": None,
                    "strategy_summary": None,
                },
            )
            summary["persisted_last_scan"] = True

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print("ARTIFACT", out_path)
        entry = summary["entry_timing"]
        rank = summary["rank_filter_v2"]
        print(
            f"entry_filter_pct={entry.get('would_filter_pct')} "
            f"stage_a={entry.get('stage_a_candidates')} blocked={entry.get('entry_timing_blocked')} "
            f"signals={summary['signals_found']} dq={summary.get('data_quality')}"
        )
        print(
            f"rank_v2 evaluated={rank.get('evaluated')} threshold={rank.get('threshold')} "
            f"would_drop={rank.get('would_drop')} retention_pct={rank.get('retention_pct')}"
        )
        return 0
    finally:
        restore_process_env(saved)


if __name__ == "__main__":
    raise SystemExit(main())
