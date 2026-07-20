#!/usr/bin/env python3
"""Confirm chop/regime prob-rank model on full control_legacy_aug dual-run.

Materializes trade-entry features for missing joins, attaches SPY regime + chop
helpers, scores with the trained model, then runs p75 CF vs rank_v2.

Resumable: skips tickers already present in the scored output parquet.

Example:
  python scripts/confirm_prob_rank_control_legacy.py \\
      --model-dir research_store/models/lgbm_ret_40d_fwd_a0663ea485 \\
      --run-id control_legacy_aug
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402
from research.calibrate import add_chop_helper_features  # noqa: E402
from research.counterfactual import run_prob_rank_counterfactual  # noqa: E402
from research.coverage import coverage_report, missing_trade_keys  # noqa: E402
from research.infer import predict_frame  # noqa: E402
from research.materialize import materialize_ticker  # noqa: E402
from research.promotion import evaluate_prob_rank_promotion  # noqa: E402
from research.regime_context import attach_regime_features, fetch_spy_bars  # noqa: E402
from research.train import load_model_artifact  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

LOG = get_logger("confirm_prob_rank_control_legacy")


def _fetch_bars(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    """Fetch trailing daily bars that reach ``start`` (with warmup), then clip to ``end``.

    ``get_daily_history`` returns the last N days ending near today — so ``days``
    must be measured from *now* back through ``start``, not from ``start``→``end``.
    Otherwise late_bull (2015–2017) requests ~400 days of *recent* bars and the
    end-date filter empties the frame.
    """
    from datetime import datetime, timezone

    from market_data import get_daily_history

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    end_ts: datetime | None
    try:
        start_ts = datetime.strptime(start[:10], "%Y-%m-%d")
        end_ts = datetime.strptime(end[:10], "%Y-%m-%d")
        days = max(400, min(5000, (now - start_ts).days + 400))
    except ValueError:
        days = 1200
        end_ts = None
    df = get_daily_history(ticker, days=days, skill_dir=SKILL_DIR)
    if df is None or getattr(df, "empty", True):
        return None
    if end_ts is not None:
        df = df.loc[df.index <= pd.Timestamp(end_ts)]
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=str, default="control_legacy_aug")
    parser.add_argument(
        "--model-dir",
        type=str,
        default=str(SKILL_DIR / "research_store" / "models" / "lgbm_ret_40d_fwd_a0663ea485"),
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(SKILL_DIR / "validation_artifacts" / "prob_rank_control_confirm"),
    )
    parser.add_argument("--ticker-limit", type=int, default=0, help="0=all missing tickers")
    parser.add_argument("--min-percentile", type=float, default=75.0)
    parser.add_argument(
        "--seed-features",
        type=str,
        default=str(
            SKILL_DIR
            / "validation_artifacts"
            / "prob_rank_ops_sample_train"
            / "dataset_with_chop_regime_features.parquet"
        ),
    )
    args = parser.parse_args(argv)
    setup_logging()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scored_path = out_dir / f"scored_features_{args.run_id}.parquet"
    progress_path = out_dir / "progress.json"

    trades = _load_trade_frame(args.run_id)
    artifact = load_model_artifact(Path(args.model_dir))
    spy = fetch_spy_bars(skill_dir=SKILL_DIR, days=4000)
    if spy is None or getattr(spy, "empty", True):
        LOG.error("SPY bars unavailable")
        return 2

    # Seed from prior enriched dataset if present
    frames: list[pd.DataFrame] = []
    if scored_path.is_file():
        frames.append(pd.read_parquet(scored_path))
        LOG.info("Resuming from %s rows=%s", scored_path, len(frames[0]))
    seed = Path(args.seed_features)
    if seed.is_file():
        seed_df = pd.read_parquet(seed)
        seed_df = attach_regime_features(seed_df, spy, assign_eras=True)
        seed_df = add_chop_helper_features(seed_df)
        frames.append(predict_frame(artifact, seed_df))

    scored = (
        pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ticker", "asof_date"], keep="last")
        if frames
        else pd.DataFrame()
    )
    if not scored.empty:
        scored["ticker"] = scored["ticker"].astype(str).str.upper()
        scored["asof_date"] = pd.to_datetime(scored["asof_date"]).dt.strftime("%Y-%m-%d")

    before = coverage_report(trades, scored) if not scored.empty else {"coverage": 0.0, "n_scored": 0}
    LOG.info("Coverage before materialize: %s", before)
    miss = missing_trade_keys(trades, scored) if not scored.empty else trades.copy()
    if "entry_iso" not in miss.columns:
        miss = miss.copy()
        miss["ticker"] = miss["ticker"].astype(str).str.upper()
        miss["entry_iso"] = pd.to_datetime(miss["entry_date"]).dt.strftime("%Y-%m-%d")

    tickers = sorted(miss["ticker"].unique())
    if args.ticker_limit and args.ticker_limit > 0:
        tickers = tickers[: int(args.ticker_limit)]
    LOG.info("Missing trade rows=%s tickers_to_process=%s", len(miss), len(tickers))

    done: set[str] = set()
    if progress_path.is_file():
        try:
            done = set(json.loads(progress_path.read_text(encoding="utf-8")).get("done_tickers") or [])
        except Exception:
            done = set()

    new_scored_chunks: list[pd.DataFrame] = []
    t0 = time.time()
    for i, ticker in enumerate(tickers, start=1):
        if ticker in done:
            continue
        sub = miss[miss["ticker"] == ticker]
        dates = sorted(set(sub["entry_iso"].tolist()))
        if not dates:
            done.add(ticker)
            continue
        bars = _fetch_bars(ticker, dates[0], dates[-1])
        if bars is None or bars.empty:
            LOG.warning("No bars for %s (%s dates)", ticker, len(dates))
            done.add(ticker)
            continue
        frame = materialize_ticker(
            ticker=ticker,
            bars=bars,
            asof_dates=dates,
            candidate_set_version="trade_entry_score_v1",
            skill_dir=SKILL_DIR,
            require_stage2=False,
            write=True,
        )
        if frame is None or frame.empty:
            LOG.warning("No features for %s", ticker)
            done.add(ticker)
            continue
        frame = attach_regime_features(frame, spy, assign_eras=True)
        frame = add_chop_helper_features(frame)
        chunk = predict_frame(artifact, frame)
        new_scored_chunks.append(chunk)
        done.add(ticker)

        if i % 25 == 0 or i == len(tickers):
            elapsed = float(time.time() - t0)
            rate = float(i) / max(elapsed, 1e-6)
            LOG.info(
                "Progress %s/%s tickers elapsed=%ss rate=%s t/s",
                int(i),
                int(len(tickers)),
                f"{elapsed:.0f}",
                f"{rate:.2f}",
            )
            # checkpoint
            if new_scored_chunks:
                part = pd.concat(new_scored_chunks, ignore_index=True)
                scored = (
                    pd.concat([scored, part], ignore_index=True)
                    if not scored.empty
                    else part
                )
                scored = scored.drop_duplicates(subset=["ticker", "asof_date"], keep="last")
                scored.to_parquet(scored_path, index=False)
                new_scored_chunks = []
            progress_path.write_text(
                json.dumps(
                    {
                        "done_tickers": sorted(done),
                        "n_done": len(done),
                        "n_target": len(tickers),
                        "scored_rows": int(len(scored)),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

    if new_scored_chunks:
        part = pd.concat(new_scored_chunks, ignore_index=True)
        scored = pd.concat([scored, part], ignore_index=True) if not scored.empty else part
        scored = scored.drop_duplicates(subset=["ticker", "asof_date"], keep="last")
        scored.to_parquet(scored_path, index=False)

    after = coverage_report(trades, scored)
    LOG.info("Coverage after: %s", after)

    cf = run_prob_rank_counterfactual(
        trades,
        scored,
        min_percentile=args.min_percentile,
        control_percentile=75.0,
    )
    cf["coverage_before"] = before
    cf["coverage_after"] = after
    cf["run_id"] = args.run_id
    cf["model_dir"] = str(args.model_dir)
    cf_path = out_dir / f"prob_rank_counterfactual_p75_{args.run_id}.json"
    cf_path.write_text(json.dumps(cf, indent=2), encoding="utf-8")

    metrics = {
        "pf_mean": cf["prob_rank"]["pf_mean"],
        "worst_era_pf": cf["prob_rank"]["worst_era_pf"],
        "n_trades": cf["prob_rank"]["n"],
        "retention": cf["prob_rank"]["retention"],
        "dual_run_ok": True,
        "walk_forward_ic_mean": None,
    }
    verdict = evaluate_prob_rank_promotion(metrics, requested="shadow")
    promo = {
        "decision": verdict.decision,
        "floors_cleared": verdict.floors_cleared,
        "composite_score": verdict.composite_score,
        "rationale": verdict.rationale,
        "gates": verdict.gates,
        "metrics": metrics,
        "counterfactual": str(cf_path),
        "model_dir": str(args.model_dir),
        "run_id": args.run_id,
        "note": "Broader confirmation on control_legacy_aug; do not enable live without review",
    }
    promo_path = out_dir / f"prob_rank_promotion_decision_{args.run_id}.json"
    promo_path.write_text(json.dumps(promo, indent=2), encoding="utf-8")

    summary = {
        "coverage_before": before,
        "coverage_after": after,
        "prob_rank": cf["prob_rank"],
        "rank_v2_control": cf["rank_v2_control"],
        "baseline": cf["baseline"],
        "promotion": {
            "decision": verdict.decision,
            "floors_cleared": verdict.floors_cleared,
            "composite_score": verdict.composite_score,
        },
        "scored_path": str(scored_path),
        "cf_path": str(cf_path),
        "promo_path": str(promo_path),
    }
    (out_dir / f"confirm_summary_{args.run_id}.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
