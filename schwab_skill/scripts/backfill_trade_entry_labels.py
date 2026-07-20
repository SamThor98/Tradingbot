#!/usr/bin/env python3
"""Backfill ret_40d_fwd labels on trade_entry_score_v1 feature rows.

Confirm materialize wrote trade-entry features without forward labels (0% labeled),
so retrains only saw the old stage2_pass_v1 sample. This script attaches labels
from OHLCV history and writes an updated panel parquet.

Example:
  python scripts/backfill_trade_entry_labels.py
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
from research.labels import LABEL_COLUMNS, attach_forward_labels  # noqa: E402

LOG = get_logger("backfill_trade_entry_labels")


def _fetch_bars(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    """Trailing history from today must reach ``start``; keep ~120d past ``end`` for labels."""
    from datetime import datetime, timezone

    from market_data import get_daily_history

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    end_ts: datetime | None
    try:
        start_ts = datetime.strptime(start[:10], "%Y-%m-%d")
        end_ts = datetime.strptime(end[:10], "%Y-%m-%d")
        # Reach start with warmup; also keep room after end for ret_40d_fwd.
        days = max(400, min(5000, (now - start_ts).days + 400))
    except ValueError:
        days = 1200
        end_ts = None
    df = get_daily_history(ticker, days=days, skill_dir=SKILL_DIR)
    if df is None or getattr(df, "empty", True):
        return None
    if end_ts is not None:
        # Need ~40 bars after last entry for labels
        df = df.loc[df.index <= (pd.Timestamp(end_ts) + pd.Timedelta(days=120))]
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--panel",
        type=str,
        default=str(
            SKILL_DIR
            / "validation_artifacts"
            / "prob_rank_control_confirm"
            / "scored_features_control_legacy_aug.parquet"
        ),
    )
    parser.add_argument(
        "--out",
        type=str,
        default=str(
            SKILL_DIR
            / "validation_artifacts"
            / "prob_rank_control_confirm"
            / "scored_features_control_legacy_aug_labeled.parquet"
        ),
    )
    parser.add_argument(
        "--progress",
        type=str,
        default=str(
            SKILL_DIR
            / "validation_artifacts"
            / "prob_rank_control_confirm"
            / "label_backfill_progress.json"
        ),
    )
    args = parser.parse_args(argv)
    setup_logging()

    panel_path = Path(args.panel)
    out_path = Path(args.out)
    progress_path = Path(args.progress)
    panel = pd.read_parquet(panel_path)
    te_mask = panel["candidate_set_version"].astype(str) == "trade_entry_score_v1"
    need = panel.loc[te_mask & panel["ret_40d_fwd"].isna()].copy()
    LOG.info(
        "Panel rows=%s trade_entry_unlabeled=%s tickers=%s",
        len(panel),
        len(need),
        need["ticker"].nunique() if not need.empty else 0,
    )
    if need.empty:
        panel.to_parquet(out_path, index=False)
        LOG.info("Nothing to backfill; wrote %s", out_path)
        return 0

    done: set[str] = set()
    if progress_path.is_file():
        try:
            done = set(json.loads(progress_path.read_text(encoding="utf-8")).get("done_tickers") or [])
        except Exception:
            done = set()

    labeled_chunks: list[pd.DataFrame] = []
    # Resume: load prior out if present
    if out_path.is_file() and done:
        prior = pd.read_parquet(out_path)
        labeled_chunks.append(prior.loc[prior["candidate_set_version"].astype(str) == "trade_entry_score_v1"])
        LOG.info("Resuming with %s prior trade_entry rows, done_tickers=%s", len(labeled_chunks[0]), len(done))

    tickers = sorted(need["ticker"].astype(str).str.upper().unique())
    t0 = time.time()
    n_labeled = 0
    n_fail = 0
    for i, ticker in enumerate(tickers, start=1):
        if ticker in done:
            continue
        sub = need[need["ticker"].astype(str).str.upper() == ticker]
        dates = sorted(pd.to_datetime(sub["asof_date"]).dt.strftime("%Y-%m-%d").unique())
        bars = _fetch_bars(ticker, dates[0], dates[-1])
        if bars is None or bars.empty:
            LOG.warning("No bars for %s", ticker)
            done.add(ticker)
            n_fail += 1
            continue
        rows: list[dict] = []
        for _, row in sub.iterrows():
            attached = attach_forward_labels(row.to_dict(), bars)
            if attached is None:
                continue
            rows.append(attached)
        if rows:
            labeled_chunks.append(pd.DataFrame(rows))
            n_labeled += len(rows)
        done.add(ticker)
        if i % 25 == 0 or i == len(tickers):
            elapsed = time.time() - t0
            LOG.info(
                "Progress %s/%s tickers labeled_rows=%s elapsed=%ss",
                i,
                len(tickers),
                n_labeled,
                f"{elapsed:.0f}",
            )
            progress_path.write_text(
                json.dumps({"done_tickers": sorted(done), "n_done": len(done), "n_target": len(tickers)}, indent=2),
                encoding="utf-8",
            )

    # Rebuild panel: keep stage2 rows + newly labeled trade_entry (+ unlabeled leftovers)
    base = panel.loc[~te_mask].copy()
    if labeled_chunks:
        te_labeled = pd.concat(labeled_chunks, ignore_index=True)
        te_labeled = te_labeled.drop_duplicates(subset=["ticker", "asof_date"], keep="last")
    else:
        te_labeled = panel.loc[te_mask].iloc[0:0].copy()

    # Unlabeled trade_entry still kept for scoring coverage (no train use)
    te_keys = te_labeled[["ticker", "asof_date"]].copy()
    te_keys["ticker"] = te_keys["ticker"].astype(str).str.upper()
    te_keys["asof_date"] = pd.to_datetime(te_keys["asof_date"]).dt.strftime("%Y-%m-%d")
    te_keys["_lab"] = 1
    te_all = panel.loc[te_mask].copy()
    te_all["ticker"] = te_all["ticker"].astype(str).str.upper()
    te_all["asof_date"] = pd.to_datetime(te_all["asof_date"]).dt.strftime("%Y-%m-%d")
    te_all = te_all.merge(te_keys, on=["ticker", "asof_date"], how="left")
    te_unlab = te_all[te_all["_lab"].isna()].drop(columns=["_lab"], errors="ignore")
    # Prefer labeled versions
    te_out = pd.concat([te_labeled, te_unlab], ignore_index=True, sort=False)
    te_out = te_out.drop_duplicates(subset=["ticker", "asof_date"], keep="first")

    out = pd.concat([base, te_out], ignore_index=True, sort=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False)
    label_rate = float(out["ret_40d_fwd"].notna().mean()) if "ret_40d_fwd" in out.columns else 0.0
    te_rate = float(te_out["ret_40d_fwd"].notna().mean()) if len(te_out) else 0.0
    summary = {
        "panel_rows": int(len(out)),
        "label_rate": round(label_rate, 4),
        "trade_entry_rows": int(len(te_out)),
        "trade_entry_label_rate": round(te_rate, 4),
        "n_labeled_this_run": int(n_labeled),
        "n_fail_tickers": int(n_fail),
        "label_columns": list(LABEL_COLUMNS),
        "out": str(out_path),
    }
    (out_path.parent / "label_backfill_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    LOG.info("Wrote %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
