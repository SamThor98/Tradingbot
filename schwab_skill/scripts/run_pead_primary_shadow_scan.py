#!/usr/bin/env python3
"""Run a PEAD-primary shadow dual-admit scan (no PEAD-only execution).

Upserts are expected already in .env (STRATEGY_PEAD_PRIMARY_MODE=shadow).
Persists last_scan, appends plugin shadow evidence, writes a validation artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

PEAD_ENV_KEYS = [
    "STRATEGY_PEAD_PRIMARY_MODE",
    "STRATEGY_PEAD_PRIMARY_ALLOW_LIVE",
    "PEAD_PRIMARY_SHADOW_MAX_NAMES",
    "PEAD_PRIMARY_SHADOW_RANK_TOP_N",
    "PEAD_PRIMARY_LOOKBACK_DAYS",
    "PEAD_ENABLED",
    "PEAD_PRESCAN_WARM_ENABLED",
    "PEAD_LOOKBACK_DAYS",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-tickers", type=int, default=400)
    parser.add_argument("--label", default="mid400")
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    from config import (
        clear_env_cache,
        get_entry_timing_shadow_mode,
        get_pead_enabled,
        get_pead_prescan_warm_enabled,
        get_strategy_pead_primary_effective_mode,
        get_strategy_pead_primary_mode,
    )
    from core.env_local import (
        SIGNAL_STACK_ENFORCED_ENV,
        reload_env_file_into_process,
        restore_process_env,
    )
    from core.plugin_shadow_evidence import (
        append_plugin_shadow_record,
        build_plugin_shadow_record,
    )
    from core.scan_service import run_scan
    from signal_scanner import _load_watchlist

    env_path = SKILL_DIR / ".env"
    keys = list(SIGNAL_STACK_ENFORCED_ENV.keys()) + PEAD_ENV_KEYS
    saved = reload_env_file_into_process(env_path, keys=keys)
    clear_env_cache()
    try:
        mode = get_strategy_pead_primary_mode(SKILL_DIR)
        eff = get_strategy_pead_primary_effective_mode(SKILL_DIR)
        print(f"pead_mode={mode} effective={eff}")
        print(
            f"pead_enabled={get_pead_enabled(SKILL_DIR)} "
            f"prescan_warm={get_pead_prescan_warm_enabled(SKILL_DIR)} "
            f"entry_timing={get_entry_timing_shadow_mode(SKILL_DIR)}"
        )
        if eff == "off":
            print("FAIL: STRATEGY_PEAD_PRIMARY_MODE effective is off — set shadow in .env")
            return 1

        watchlist = list(_load_watchlist(SKILL_DIR))
        if int(args.max_tickers) > 0:
            watchlist = watchlist[: int(args.max_tickers)]
        print(
            f"Running PEAD shadow dual-admit scan on {len(watchlist)} tickers (no orders)...",
            flush=True,
        )
        scan_out = run_scan(skill_dir=SKILL_DIR, watchlist_override=watchlist)
        diag = dict(scan_out.diagnostics or {})
        finished = datetime.now(timezone.utc).isoformat()

        pead_keys = [
            "strategy_pead_primary_mode",
            "strategy_pead_primary_effective_mode",
            "pead_primary_evaluated",
            "pead_primary_admitted",
            "overlap_with_stage2",
            "pead_primary_would_rank_top_n",
            "pead_primary_would_rank_capacity_top_n",
            "pead_primary_capacity_rank_arm",
            "pead_primary_capacity_rank_top_n",
            "pead_primary_shadow_truncated",
            "pead_primary_lookback_days",
            "pead_primary_live_coerced_to_shadow",
            "pead_primary_shadow_sort_key",
            "data_quality",
            "stage_a_candidates",
            "stage_a_shortlisted",
            "watchlist_size",
        ]
        summary = {k: diag.get(k) for k in pead_keys}
        summary.update(
            {
                "at": finished,
                "label": args.label,
                "signals_found": len(scan_out.signals),
                "shadow_names_sample": list(diag.get("pead_primary_shadow_names") or [])[:20],
                "pead_cache": diag.get("pead_cache"),
            }
        )
        print("PEAD SUMMARY", json.dumps(summary, default=str), flush=True)

        leaked = [
            s.get("ticker")
            for s in scan_out.signals
            if str(s.get("entry_family") or "") == "pead_primary"
        ]
        print(f"leaked_pead_only_count={len(leaked)} sample={leaked[:10]}", flush=True)

        if not args.no_persist:
            from scripts.run_entry_timing_experiment_scan import _persist_last_scan

            _persist_last_scan(
                sqlite_path=SKILL_DIR / "webapp" / "webapp.db",
                last_scan={
                    "at": finished,
                    "signals_found": len(scan_out.signals),
                    "signals": scan_out.signals[:200],
                    "shortlist_signals": scan_out.shortlist_signals[:200],
                    "diagnostics": diag,
                    "diagnostics_summary": None,
                    "strategy_summary": None,
                },
            )
            print("Persisted last_scan", flush=True)
            rec = build_plugin_shadow_record(
                diag,
                scan_at=finished,
                signals_found=len(scan_out.signals),
                scan_label=f"pead_primary_shadow_{args.label}",
            )
            if rec is not None:
                path = append_plugin_shadow_record(SKILL_DIR, rec)
                print(f"Appended shadow evidence -> {path}", flush=True)
            else:
                print("WARNING: no shadow evidence record built", flush=True)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        art = (
            SKILL_DIR
            / "validation_artifacts"
            / f"pead_primary_shadow_scan_{stamp}_{args.label}.json"
        )
        art.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "summary": summary,
            "leaked_pead_only": leaked,
            "diagnostics_pead": {
                **{k: diag.get(k) for k in pead_keys},
                "pead_primary_shadow_names": diag.get("pead_primary_shadow_names"),
            },
        }
        art.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"ARTIFACT {art}", flush=True)

        if leaked:
            print("FAIL: PEAD-only names leaked into executable signals")
            return 1
        if int(diag.get("pead_primary_evaluated") or 0) <= 0:
            print("WARN: pead_primary_evaluated=0 — check earnings warm / mode")
            return 0
        print("PASS: PEAD shadow scan complete (non-exec invariant held)")
        return 0
    finally:
        restore_process_env(saved)


if __name__ == "__main__":
    raise SystemExit(main())
