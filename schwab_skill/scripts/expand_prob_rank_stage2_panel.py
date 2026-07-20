#!/usr/bin/env python3
"""Expand stage2_pass feature panel for top control_legacy tickers (non-trade dates).

Materializes monthly Stage-2 candidate rows for tickers outside the 50-name sample,
attaches ret_40d_fwd labels, and merges into a train panel suitable for purged retrain.

Example:
  python scripts/expand_prob_rank_stage2_panel.py --top-n 100 --start 2015-01-01 --end 2026-07-01
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
from research.labels import attach_forward_labels  # noqa: E402
from research.materialize import materialize_ticker  # noqa: E402
from research.regime_context import attach_regime_features, fetch_spy_bars  # noqa: E402
from scripts.refresh_prob_rank_dual_run_sample import SAMPLE_TICKERS  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

LOG = get_logger("expand_prob_rank_stage2_panel")


def _month_end_dates(start: str, end: str) -> list[str]:
    idx = pd.date_range(start=start, end=end, freq="ME")
    return [d.strftime("%Y-%m-%d") for d in idx]


def _fetch_bars(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    from datetime import datetime, timezone

    from market_data import get_daily_history

    # Trailing window from today must reach ``start`` (+ warmup), not start→end span.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    end_ts: datetime | None
    try:
        start_ts = datetime.strptime(start[:10], "%Y-%m-%d")
        end_ts = datetime.strptime(end[:10], "%Y-%m-%d")
        days = max(400, min(5000, (now - start_ts).days + 400))
    except ValueError:
        days = 2000
        end_ts = None
    df = get_daily_history(ticker, days=days, skill_dir=SKILL_DIR)
    if df is None or getattr(df, "empty", True):
        return None
    if end_ts is not None:
        df = df.loc[df.index <= (pd.Timestamp(end_ts) + pd.Timedelta(days=80))]
    return df


def _top_control_tickers(run_id: str, top_n: int, exclude: set[str]) -> list[str]:
    trades = _load_trade_frame(run_id)
    counts = (
        trades.assign(ticker=trades["ticker"].astype(str).str.upper())
        .groupby("ticker")
        .size()
        .sort_values(ascending=False)
    )
    out: list[str] = []
    for t in counts.index.astype(str):
        if t in exclude:
            continue
        out.append(t)
        if len(out) >= top_n:
            break
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=str, default="control_legacy_aug")
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--start", type=str, default="2015-01-01")
    parser.add_argument("--end", type=str, default="2026-07-01")
    parser.add_argument(
        "--base-panel",
        type=str,
        default=str(
            SKILL_DIR
            / "validation_artifacts"
            / "prob_rank_control_confirm"
            / "scored_features_control_legacy_aug_labeled.parquet"
        ),
    )
    parser.add_argument(
        "--out",
        type=str,
        default=str(
            SKILL_DIR
            / "validation_artifacts"
            / "prob_rank_control_confirm"
            / "scored_features_control_legacy_aug_expanded.parquet"
        ),
    )
    parser.add_argument(
        "--progress",
        type=str,
        default=str(
            SKILL_DIR
            / "validation_artifacts"
            / "prob_rank_control_confirm"
            / "expand_stage2_progress.json"
        ),
    )
    parser.add_argument("--ticker-limit", type=int, default=0, help="0=all selected")
    args = parser.parse_args(argv)
    setup_logging()

    exclude = {t.upper() for t in SAMPLE_TICKERS}
    tickers = _top_control_tickers(args.run_id, args.top_n, exclude)
    if args.ticker_limit > 0:
        tickers = tickers[: args.ticker_limit]
    dates = _month_end_dates(args.start, args.end)
    LOG.info("Expand tickers=%s month_ends=%s exclude_sample=%s", len(tickers), len(dates), len(exclude))

    spy = fetch_spy_bars(skill_dir=SKILL_DIR, days=4000)
    if spy is None or getattr(spy, "empty", True):
        LOG.error("SPY bars unavailable")
        return 2

    progress_path = Path(args.progress)
    out_path = Path(args.out)
    done: set[str] = set()
    if progress_path.is_file():
        try:
            done = set(json.loads(progress_path.read_text(encoding="utf-8")).get("done_tickers") or [])
        except Exception:
            done = set()

    chunks: list[pd.DataFrame] = []
    # Resume: reload checkpointed new-only rows if present
    new_only_path = out_path.with_name(out_path.stem + "_new_only.parquet")
    if new_only_path.is_file() and done:
        try:
            chunks.append(pd.read_parquet(new_only_path))
            LOG.info("Resumed %s rows from %s", len(chunks[0]), new_only_path)
        except Exception as exc:
            LOG.warning("Could not resume checkpoint: %s", exc)

    t0 = time.time()
    n_rows = 0
    for i, ticker in enumerate(tickers, start=1):
        if ticker in done:
            continue
        bars = _fetch_bars(ticker, args.start, args.end)
        if bars is None or bars.empty:
            LOG.warning("No bars for %s", ticker)
            done.add(ticker)
            continue
        # Snap month-ends to available bar dates (on/before)
        asofs: list[str] = []
        for d in dates:
            eligible = bars.loc[bars.index <= pd.Timestamp(d)]
            if eligible.empty:
                continue
            asofs.append(str(eligible.index[-1].date()))
        asofs = sorted(set(asofs))
        frame = materialize_ticker(
            ticker=ticker,
            bars=bars,
            asof_dates=asofs,
            candidate_set_version="stage2_pass_v1",
            skill_dir=SKILL_DIR,
            require_stage2=True,
            write=True,
        )
        if frame is None or frame.empty:
            done.add(ticker)
            continue
        labeled_rows: list[dict] = []
        for _, row in frame.iterrows():
            attached = attach_forward_labels(row.to_dict(), bars)
            if attached is not None:
                labeled_rows.append(attached)
        if not labeled_rows:
            done.add(ticker)
            continue
        part = pd.DataFrame(labeled_rows)
        part = attach_regime_features(part, spy, assign_eras=True)
        part = add_chop_helper_features(part)
        chunks.append(part)
        n_rows += len(part)
        done.add(ticker)
        if i % 10 == 0 or i == len(tickers):
            elapsed = time.time() - t0
            LOG.info(
                "Progress %s/%s tickers rows=%s elapsed=%ss",
                i,
                len(tickers),
                n_rows,
                f"{elapsed:.0f}",
            )
            progress_path.write_text(
                json.dumps(
                    {
                        "done_tickers": sorted(done),
                        "n_done": len(done),
                        "n_target": len(tickers),
                        "n_rows": n_rows,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            if chunks:
                # checkpoint expanded-only
                exp = pd.concat(chunks, ignore_index=True)
                exp.to_parquet(out_path.with_name(out_path.stem + "_new_only.parquet"), index=False)

    base = pd.read_parquet(args.base_panel) if Path(args.base_panel).is_file() else pd.DataFrame()
    new = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    if new.empty and out_path.with_name(out_path.stem + "_new_only.parquet").is_file():
        new = pd.read_parquet(out_path.with_name(out_path.stem + "_new_only.parquet"))

    # Drop score cols from base if present; keep features+labels
    drop_scores = [
        c
        for c in (
            "expected_return_40d",
            "expected_downside_40d",
            "confidence",
            "expected_pf_proxy",
        )
        if c in base.columns
    ]
    if drop_scores:
        base = base.drop(columns=drop_scores)
    if not new.empty and drop_scores:
        new = new.drop(columns=[c for c in drop_scores if c in new.columns], errors="ignore")

    if base.empty and new.empty:
        LOG.error("No rows to write")
        return 2
    if base.empty:
        merged = new
    elif new.empty:
        merged = base
    else:
        merged = pd.concat([base, new], ignore_index=True, sort=False)
        merged["ticker"] = merged["ticker"].astype(str).str.upper()
        merged["asof_date"] = pd.to_datetime(merged["asof_date"]).dt.strftime("%Y-%m-%d")
        merged = merged.drop_duplicates(
            subset=["ticker", "asof_date", "candidate_set_version"],
            keep="last",
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(out_path, index=False)
    summary = {
        "n_tickers_target": len(tickers),
        "n_tickers_done": len(done),
        "n_new_rows": int(len(new)),
        "n_merged_rows": int(len(merged)),
        "label_rate": round(float(merged["ret_40d_fwd"].notna().mean()), 4)
        if "ret_40d_fwd" in merged.columns
        else None,
        "out": str(out_path),
    }
    (out_path.parent / "expand_stage2_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    LOG.info("Wrote %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
