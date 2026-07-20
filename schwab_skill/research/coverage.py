"""Close feature/score coverage gaps on frozen trade entry dates."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from research.materialize import materialize_ticker
from research.registry import FEATURE_SCHEMA_VERSION

LOG = logging.getLogger(__name__)


def missing_trade_keys(
    trades: pd.DataFrame,
    feature_or_scored: pd.DataFrame,
    *,
    score_col: str | None = "expected_return_40d",
) -> pd.DataFrame:
    """
    Return trade rows with no join to features/scores on (ticker, entry_date).

    If ``score_col`` is present on ``feature_or_scored``, treat non-null score as covered;
    otherwise treat any matching (ticker, asof_date) feature row as covered.
    """
    tr = trades.copy()
    tr["ticker"] = tr["ticker"].astype(str).str.upper()
    tr["entry_iso"] = pd.to_datetime(tr["entry_date"]).dt.strftime("%Y-%m-%d")
    sc = feature_or_scored.copy()
    sc["ticker"] = sc["ticker"].astype(str).str.upper()
    date_col = "asof_date" if "asof_date" in sc.columns else "entry_iso"
    sc["_join_date"] = pd.to_datetime(sc[date_col]).dt.strftime("%Y-%m-%d")
    if score_col and score_col in sc.columns:
        covered = sc.loc[sc[score_col].notna(), ["ticker", "_join_date"]].drop_duplicates()
    else:
        covered = sc[["ticker", "_join_date"]].drop_duplicates()
    covered = covered.rename(columns={"_join_date": "entry_iso"})
    covered["_covered"] = True
    merged = tr.merge(covered, on=["ticker", "entry_iso"], how="left")
    return merged[merged["_covered"].isna()].drop(columns=["_covered"], errors="ignore")


def materialize_trade_entry_features(
    trades: pd.DataFrame,
    ticker_bars: dict[str, pd.DataFrame],
    *,
    skill_dir: Any = None,
    candidate_set_version: str = "trade_entry_score_v1",
    feature_schema_version: int = FEATURE_SCHEMA_VERSION,
    write: bool = True,
) -> pd.DataFrame:
    """
    Materialize OHLCV features at exact trade entry dates (no Stage-2 re-filter).

    Trades already passed Stage-2 in the backtest; forcing Stage-2 again drops
    valid entry days and creates dual-run coverage holes.
    """
    work = trades.copy()
    work["ticker"] = work["ticker"].astype(str).str.upper()
    work["entry_iso"] = pd.to_datetime(work["entry_date"]).dt.strftime("%Y-%m-%d")
    frames: list[pd.DataFrame] = []
    for ticker, grp in work.groupby("ticker"):
        bars = ticker_bars.get(str(ticker))
        if bars is None or getattr(bars, "empty", True):
            LOG.warning("No bars for %s — skip %s entry dates", ticker, len(grp))
            continue
        dates = sorted(set(grp["entry_iso"].tolist()))
        frame = materialize_ticker(
            ticker=str(ticker),
            bars=bars,
            asof_dates=dates,
            candidate_set_version=candidate_set_version,
            feature_schema_version=feature_schema_version,
            skill_dir=skill_dir,
            require_stage2=False,
            write=write,
        )
        if frame is not None and not frame.empty:
            frames.append(frame)
            LOG.info("Trade-entry features %s: %s/%s dates", ticker, len(frame), len(dates))
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates(
        subset=["asof_date", "ticker", "candidate_set_version", "feature_schema_version"],
        keep="last",
    )


def coverage_report(
    trades: pd.DataFrame,
    scored: pd.DataFrame,
) -> dict[str, Any]:
    """Summarize score join coverage overall and by era."""
    from research.infer import attach_scores_to_trades

    merged = attach_scores_to_trades(trades, scored)
    has = merged["expected_return_40d"].notna() if "expected_return_40d" in merged.columns else pd.Series(False, index=merged.index)
    by_era: dict[str, Any] = {}
    if "era" in merged.columns:
        for era, g in merged.groupby("era"):
            h = g["expected_return_40d"].notna()
            by_era[str(era)] = {
                "n": int(len(g)),
                "n_scored": int(h.sum()),
                "coverage": round(float(h.mean()), 4) if len(g) else 0.0,
            }
    return {
        "n_trades": int(len(merged)),
        "n_scored": int(has.sum()),
        "coverage": round(float(has.mean()), 4) if len(merged) else 0.0,
        "by_era": by_era,
    }
