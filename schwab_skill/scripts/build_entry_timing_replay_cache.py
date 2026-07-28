#!/usr/bin/env python3
"""Build ``entry_timing_replay_cache_<run_id>.json`` from multi-era chunks.

Fetches daily history **once per ticker** (Schwab preferred, yfinance fallback),
computes entry-timing metrics at each trade entry, and merges chunk score fields
so peer-generator stack CFs can evaluate the 1% breakout-buffer (± rank) arms.

Usage (from schwab_skill/):
  python scripts/build_entry_timing_replay_cache.py \\
    --run-id pead_primary_aug_fixed_full_c40
  python scripts/build_entry_timing_replay_cache.py \\
    --run-id pead_primary_aug_fixed_full_c40 --provider yfinance --max-tickers 20
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.analyze_entry_timing_shadow_counterfactual import (  # noqa: E402
    _apply_shadow_flags,
    _label_cohort,
    _load_replay_cache,
    _save_replay_cache,
    _trade_replay_key,
)
from scripts.analyze_rank_filter_counterfactual import (  # noqa: E402
    _hold_days_map,
    _merge_hold_days,
)
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402
from stage_analysis import add_indicators, compute_entry_timing_metrics  # noqa: E402

LOG = logging.getLogger(__name__)
LOOKBACK_CALENDAR_DAYS = 420


def _fetch_history(
    ticker: str,
    start: str,
    end: str,
    *,
    provider: str,
    auth: Any | None,
) -> pd.DataFrame:
    """Return daily OHLCV indexed by date (naive), empty on failure."""
    ticker = str(ticker).upper().strip()
    if not ticker:
        return pd.DataFrame()
    if provider == "schwab":
        try:
            from backtest import _fetch_history_schwab

            df = _fetch_history_schwab(ticker, start, end, auth=auth)
            if df is not None and not df.empty:
                return df
        except Exception as exc:
            LOG.debug("Schwab fetch failed %s: %s", ticker, exc)
        # Fall through to yfinance.
    try:
        from backtest import _fetch_history

        df = _fetch_history(ticker, start, end)
        return df if df is not None else pd.DataFrame()
    except Exception as exc:
        LOG.debug("yfinance fetch failed %s: %s", ticker, exc)
        return pd.DataFrame()


def _metrics_through_entry(hist: pd.DataFrame, entry_date: pd.Timestamp) -> dict[str, Any] | None:
    if hist is None or hist.empty:
        return None
    entry_norm = pd.Timestamp(entry_date).normalize()
    eligible = hist[hist.index <= entry_norm]
    if len(eligible) < 2:
        return None
    # Prefer ≥200 bars for SMA50/52w metrics; still emit buffer with short history.
    work = add_indicators(eligible) if len(eligible) >= 50 else eligible.copy()
    metrics = compute_entry_timing_metrics(work, SKILL_DIR)
    if metrics.get("breakout_buffer_pct") is None and len(eligible) >= 2:
        price = float(eligible["close"].iloc[-1])
        prior_high = float(eligible["high"].iloc[-2])
        if prior_high > 0:
            metrics["breakout_buffer_pct"] = round((price - prior_high) / prior_high, 4)
    return metrics


def build_replay_rows(
    run_id: str,
    *,
    provider: str = "schwab",
    max_tickers: int | None = None,
    checkpoint_every: int = 25,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build or resume entry-timing replay rows for ``run_id``."""
    df = _load_trade_frame(run_id)
    hold_map = _hold_days_map(run_id)
    df = _merge_hold_days(df, hold_map)
    df["entry_iso"] = pd.to_datetime(df["entry_date"]).dt.strftime("%Y-%m-%d")
    df["ticker"] = df["ticker"].astype(str).str.upper()
    df["cohort"] = df.apply(_label_cohort, axis=1)

    existing = _load_replay_cache(run_id)
    done_keys: set[tuple[str, str, str]] = set()
    rows: list[dict[str, Any]] = []
    if not existing.empty:
        for _, cached in existing.iterrows():
            key = _trade_replay_key(
                str(cached.get("era") or ""),
                str(cached.get("ticker") or ""),
                cached.get("entry_date"),
            )
            done_keys.add(key)
            rows.append(cached.to_dict())
        LOG.info("Resuming: %s / %s trades already cached", len(done_keys), len(df))

    pending = df[
        ~df.apply(
            lambda r: _trade_replay_key(str(r.era), r.ticker, r.entry_date) in done_keys,
            axis=1,
        )
    ].copy()
    by_ticker = list(pending.groupby("ticker", sort=True))
    if max_tickers is not None:
        by_ticker = by_ticker[: max(0, int(max_tickers))]

    auth = None
    if provider == "schwab":
        os.environ.setdefault("SCHWAB_ONLY_DATA", "true")
        from schwab_auth import DualSchwabAuth

        auth = DualSchwabAuth(skill_dir=SKILL_DIR, auto_refresh=True)

    stats = {
        "tickers_total": len(by_ticker),
        "tickers_fetched": 0,
        "tickers_empty": 0,
        "trades_added": 0,
        "trades_skipped_no_bars": 0,
        "provider": provider,
    }
    t0 = time.time()
    for idx, (ticker, group) in enumerate(by_ticker, start=1):
        entry_min = pd.to_datetime(group["entry_date"]).min()
        entry_max = pd.to_datetime(group["entry_date"]).max()
        start = (entry_min - timedelta(days=LOOKBACK_CALENDAR_DAYS)).date().isoformat()
        end = (entry_max + timedelta(days=5)).date().isoformat()
        hist = _fetch_history(str(ticker), start, end, provider=provider, auth=auth)
        stats["tickers_fetched"] += 1
        if hist is None or hist.empty:
            stats["tickers_empty"] += 1
            LOG.warning("Empty history for %s (%s pending trades)", ticker, len(group))
            continue
        for _, trade_row in group.iterrows():
            metrics = _metrics_through_entry(hist, trade_row["entry_date"])
            if metrics is None:
                stats["trades_skipped_no_bars"] += 1
                continue
            out = trade_row.to_dict()
            out.update(metrics)
            out["entry_shadow_would_filter"] = False
            out["entry_shadow_reasons"] = []
            rows.append(out)
            stats["trades_added"] += 1
        if idx % checkpoint_every == 0 or idx == len(by_ticker):
            replay_df = pd.DataFrame(rows)
            if not replay_df.empty:
                _save_replay_cache(replay_df, run_id)
            elapsed = time.time() - t0
            rate = idx / max(elapsed, 1e-6)
            eta = (len(by_ticker) - idx) / max(rate, 1e-6)
            LOG.info(
                "Progress %s/%s tickers (%.1f/min, eta %.0fs) cache_rows=%s added=%s",
                idx,
                len(by_ticker),
                rate * 60,
                eta,
                len(rows),
                stats["trades_added"],
            )

    replay_df = pd.DataFrame(rows)
    if not replay_df.empty:
        replay_df = _apply_shadow_flags(replay_df, skill_dir=SKILL_DIR)
        _save_replay_cache(replay_df, run_id)
    stats["cache_rows"] = int(len(replay_df))
    stats["elapsed_sec"] = round(time.time() - t0, 1)
    return replay_df, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--provider",
        choices=("schwab", "yfinance"),
        default="schwab",
        help="Primary history provider (schwab falls back to yfinance).",
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=0,
        help="Limit tickers processed this run (0 = all pending).",
    )
    parser.add_argument("--checkpoint-every", type=int, default=25)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    max_tickers = None if int(args.max_tickers) <= 0 else int(args.max_tickers)
    replay_df, stats = build_replay_rows(
        args.run_id,
        provider=str(args.provider),
        max_tickers=max_tickers,
        checkpoint_every=max(1, int(args.checkpoint_every)),
    )
    print(f"run_id={args.run_id}")
    print(f"cache_rows={stats.get('cache_rows')} added={stats.get('trades_added')}")
    print(
        f"tickers_fetched={stats.get('tickers_fetched')} "
        f"empty={stats.get('tickers_empty')} "
        f"skipped_no_bars={stats.get('trades_skipped_no_bars')} "
        f"elapsed_s={stats.get('elapsed_sec')}"
    )
    if replay_df.empty:
        return 1
    # Require coverage of most trades for full-stack CF readiness.
    try:
        target_n = len(_load_trade_frame(args.run_id))
    except Exception:
        target_n = 0
    coverage = len(replay_df) / max(1, target_n)
    print(f"coverage={coverage:.1%} ({len(replay_df)}/{target_n})")
    return 0 if coverage >= 0.90 or max_tickers is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
