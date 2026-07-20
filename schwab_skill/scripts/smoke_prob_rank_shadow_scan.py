#!/usr/bin/env python3
"""Focused live scan smoke for PROB_RANK_MODE=shadow.

Runs scan_service on a small liquid watchlist and asserts shadow diagnostics
are populated without dropping the shortlist via prob-rank live selection.

Example:
  python scripts/smoke_prob_rank_shadow_scan.py
  python scripts/smoke_prob_rank_shadow_scan.py --tickers AAPL,MSFT,NVDA,META,AMZN
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402
from scripts.refresh_prob_rank_dual_run_sample import SAMPLE_TICKERS  # noqa: E402

LOG = get_logger("smoke_prob_rank_shadow_scan")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tickers",
        type=str,
        default="",
        help="Comma-separated tickers (default: first 20 from dual-run sample)",
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--out",
        type=str,
        default=str(
            SKILL_DIR / "validation_artifacts" / "prob_rank_shadow_smoke" / "smoke_summary.json"
        ),
    )
    args = parser.parse_args(argv)
    setup_logging()

    if args.tickers.strip():
        watchlist = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        watchlist = [t.upper() for t in SAMPLE_TICKERS[: max(1, args.limit)]]

    from config import (  # noqa: E402
        bootstrap_dotenv_into_environ,
        clear_env_cache,
        get_prob_rank_mode,
        get_prob_rank_model_dir,
    )
    from core.scan_service import run_scan

    clear_env_cache()
    bootstrap_dotenv_into_environ(SKILL_DIR)
    mode = get_prob_rank_mode(SKILL_DIR)
    model_dir = get_prob_rank_model_dir(SKILL_DIR)
    LOG.info("PROB_RANK_MODE=%s MODEL_DIR=%s watchlist=%s", mode, model_dir, len(watchlist))
    if mode != "shadow":
        LOG.error("Expected PROB_RANK_MODE=shadow, got %s — set local .env first", mode)
        return 2

    result = run_scan(skill_dir=SKILL_DIR, watchlist_override=watchlist)
    diag = result.diagnostics or {}
    signals = result.signals or []

    scored_signals = sum(1 for s in signals if s.get("expected_return_40d") is not None)
    with_block = sum(1 for s in signals if isinstance(s.get("prob_rank"), dict))
    model_ids = sorted(
        {
            str((s.get("prob_rank") or {}).get("model_id") or s.get("prob_rank_model_id") or "")
            for s in signals
            if s.get("prob_rank_model_id") or (isinstance(s.get("prob_rank"), dict) and s["prob_rank"].get("model_id"))
        }
    )
    model_ids = [m for m in model_ids if m]

    summary = {
        "ok": False,
        "prob_rank_mode": diag.get("prob_rank_mode") or mode,
        "model_dir": model_dir,
        "watchlist_n": len(watchlist),
        "signals_n": len(signals),
        "signals_with_expected_return": scored_signals,
        "signals_with_prob_rank_block": with_block,
        "model_ids": model_ids,
        "diagnostics": {
            k: diag.get(k)
            for k in (
                "prob_rank_mode",
                "prob_rank_scored",
                "prob_rank_unscored",
                "prob_rank_would_keep",
                "prob_rank_would_drop",
                "prob_rank_top_n",
                "prob_rank_skipped",
                "prob_rank_dropped",
                "stage_a_candidates",
                "stage_b_enriched",
                "signals_emitted",
            )
            if k in diag or True
        },
        "sample_signal": None,
    }
    if signals:
        s0 = next((s for s in signals if s.get("expected_return_40d") is not None), signals[0])
        summary["sample_signal"] = {
            "ticker": s0.get("ticker"),
            "expected_return_40d": s0.get("expected_return_40d"),
            "prob_rank_model_id": s0.get("prob_rank_model_id"),
            "prob_rank_cross_section_rank": s0.get("prob_rank_cross_section_rank"),
            "prob_rank_selection": s0.get("prob_rank_selection"),
            "has_prob_rank_block": isinstance(s0.get("prob_rank"), dict),
        }

    # Pass criteria: shadow mode in diagnostics, no live drops, and either scores
    # on emitted signals or explicit scored counter from Stage B.
    mode_ok = str(summary["prob_rank_mode"]) == "shadow"
    no_live_drop = diag.get("prob_rank_dropped") in (None, 0)
    scored_ok = (
        int(diag.get("prob_rank_scored") or 0) > 0
        or scored_signals > 0
        or with_block > 0
    )
    # Empty signal lists can happen on regime/gate days — still OK if Stage B scored.
    stage_b = int(diag.get("stage_b_enriched") or diag.get("stage_a_candidates") or 0)
    if stage_b == 0 and not scored_ok:
        summary["note"] = "No Stage A/B activity on this smoke universe; widen tickers or retry in RTH"
        summary["ok"] = mode_ok and no_live_drop
    else:
        summary["ok"] = bool(mode_ok and no_live_drop and scored_ok)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if summary["ok"]:
        LOG.info("PASS shadow smoke -> %s", out_path)
        return 0
    LOG.error("FAIL shadow smoke -> %s", out_path)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
