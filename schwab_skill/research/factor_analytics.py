"""Alphalens-style factor analytics over existing scores. Does not select trades.

Known negative-IC fact (wiki/signal-ranking, 2026-07-13 stage2_only_aug,
16,423 trades): signal_score -0.0268, composite_score -0.0403,
rank_score_v2 -0.0120. This module reports IC; it is not a PF promotion gate.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.scoring_metrics import spearman_corr

NOT_PROMOTION_GATE = (
    "Book diagnostics only — not a PF promotion gate. "
    "Do not overwrite BACKTEST_CATALOG.md without an explicit catalog regen."
)

DOCUMENTED_STAGE2_ONLY_AUG_IC: dict[str, Any] = {
    "run_id": "stage2_only_aug",
    "as_of": "2026-07-13",
    "n": 16423,
    "signal_score": -0.0268,
    "composite_score": -0.0403,
    "rank_score_v2": -0.0120,
    "source": "wiki/signal-ranking.md",
}

SCORE_COLS = ("signal_score", "rank_score_v2", "prob_rank", "expected_return_40d", "composite_score")
RET_CANDIDATES = ("net_return", "ret_40d_fwd")


def _ret_col(df: pd.DataFrame) -> str | None:
    for c in RET_CANDIDATES:
        if c in df.columns:
            return c
    return None


def ic_overall(df: pd.DataFrame, score_col: str, ret_col: str) -> float:
    return spearman_corr(df[score_col], df[ret_col])


def ic_by_era(df: pd.DataFrame, score_col: str, ret_col: str) -> list[dict[str, Any]]:
    if "era" not in df.columns:
        return [{"era": "all", "n": int(len(df)), "ic": ic_overall(df, score_col, ret_col)}]
    rows: list[dict[str, Any]] = []
    for era, grp in df.groupby("era"):
        rows.append({"era": str(era), "n": int(len(grp)), "ic": ic_overall(grp, score_col, ret_col)})
    return rows


def ic_decay(df: pd.DataFrame, score_col: str, horizons: tuple[str, ...] = ("ret_5d_fwd", "ret_10d_fwd", "ret_20d_fwd", "ret_40d_fwd")) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for col in horizons:
        if col not in df.columns:
            continue
        out.append({"horizon": col, "ic": ic_overall(df, score_col, col), "n": int(df[col].notna().sum())})
    return out


def quantile_forward_returns(
    df: pd.DataFrame,
    score_col: str,
    ret_col: str,
    *,
    n_quantiles: int = 5,
) -> list[dict[str, Any]]:
    work = df[[score_col, ret_col]].copy()
    work[score_col] = pd.to_numeric(work[score_col], errors="coerce")
    work[ret_col] = pd.to_numeric(work[ret_col], errors="coerce")
    work = work.dropna()
    if len(work) < n_quantiles * 3:
        return []
    work["q"] = pd.qcut(work[score_col], n_quantiles, labels=False, duplicates="drop")
    rows: list[dict[str, Any]] = []
    for q, grp in work.groupby("q"):
        rets = grp[ret_col]
        rows.append(
            {
                "quantile": int(q),
                "n": int(len(grp)),
                "mean_ret": float(rets.mean()),
                "median_ret": float(rets.median()),
                "mean_score": float(grp[score_col].mean()),
            }
        )
    return rows


def top_quantile_turnover(df: pd.DataFrame, score_col: str, *, date_col: str = "asof_date", ticker_col: str = "ticker", q: float = 0.8) -> float:
    if date_col not in df.columns or ticker_col not in df.columns or score_col not in df.columns:
        return float("nan")
    work = df[[date_col, ticker_col, score_col]].copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work = work.dropna()
    if work.empty:
        return float("nan")
    prev: set[str] | None = None
    turnovers: list[float] = []
    for _dt, grp in work.groupby(date_col):
        cutoff = grp[score_col].quantile(q)
        names = set(grp.loc[grp[score_col] >= cutoff, ticker_col].astype(str).str.upper())
        if prev is not None and (names or prev):
            union = names | prev
            turnovers.append(len(names ^ prev) / max(len(union), 1))
        prev = names
    if not turnovers:
        return float("nan")
    return float(np.mean(turnovers))


def equity_tearsheet(equity_curve: list[tuple[Any, float]] | pd.DataFrame) -> dict[str, Any]:
    """Quantstats-style book diagnostics from _simulate_portfolio_equity path. Not a PF gate."""
    if isinstance(equity_curve, pd.DataFrame):
        if "equity" in equity_curve.columns:
            eq = pd.to_numeric(equity_curve["equity"], errors="coerce").dropna().to_numpy(dtype=np.float64)
        else:
            eq = pd.to_numeric(equity_curve.iloc[:, -1], errors="coerce").dropna().to_numpy(dtype=np.float64)
        dates = None
        if "date" in equity_curve.columns:
            dates = pd.to_datetime(equity_curve["date"], errors="coerce")
    else:
        pairs = [(pd.Timestamp(d), float(v)) for d, v in equity_curve if v is not None]
        if not pairs:
            return {"label": "book_diagnostics_not_pf_gate", "n": 0}
        dates = pd.to_datetime([p[0] for p in pairs])
        eq = np.array([p[1] for p in pairs], dtype=np.float64)
    if len(eq) < 3 or eq[0] <= 0:
        return {"label": "book_diagnostics_not_pf_gate", "n": int(len(eq))}
    rets = np.diff(eq) / eq[:-1]
    rets = rets[np.isfinite(rets)]
    mu = float(np.mean(rets)) if len(rets) else 0.0
    sd = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    sharpe = (mu / sd) * math.sqrt(252.0) if sd > 1e-12 else float("nan")
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak > 0, peak, 1.0)
    max_dd = float(dd.min()) if len(dd) else 0.0
    years = 1.0
    if dates is not None:
        span = (pd.Timestamp(dates.iloc[-1]) - pd.Timestamp(dates.iloc[0])).days
        years = max(span / 365.25, 1.0 / 252.0)
    cagr = float((eq[-1] / eq[0]) ** (1.0 / years) - 1.0) if eq[0] > 0 else float("nan")
    return {
        "label": "book_diagnostics_not_pf_gate",
        "n": int(len(eq)),
        "sharpe": None if sharpe != sharpe else round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
        "cagr": None if cagr != cagr else round(cagr, 4),
        "ending_equity": float(eq[-1]),
        "starting_equity": float(eq[0]),
        "banner": NOT_PROMOTION_GATE,
    }


def analyze_scores(df: pd.DataFrame, *, score_cols: tuple[str, ...] | None = None) -> dict[str, Any]:
    ret_col = _ret_col(df)
    cols = [c for c in (score_cols or SCORE_COLS) if c in df.columns]
    payload: dict[str, Any] = {
        "n": int(len(df)),
        "ret_col": ret_col,
        "banner": NOT_PROMOTION_GATE,
        "documented_negative_ic": DOCUMENTED_STAGE2_ONLY_AUG_IC,
        "scores": {},
    }
    if ret_col is None:
        payload["error"] = "no_return_column"
        return payload
    for col in cols:
        ic = ic_overall(df, col, ret_col)
        payload["scores"][col] = {
            "ic": None if ic != ic else round(float(ic), 4),
            "ic_by_era": [
                {**row, "ic": None if row["ic"] != row["ic"] else round(float(row["ic"]), 4)}
                for row in ic_by_era(df, col, ret_col)
            ],
            "ic_decay": [
                {**row, "ic": None if row["ic"] != row["ic"] else round(float(row["ic"]), 4)}
                for row in ic_decay(df, col)
            ],
            "quantiles": quantile_forward_returns(df, col, ret_col),
            "turnover_top_quintile": (
                None
                if top_quantile_turnover(df, col) != top_quantile_turnover(df, col)
                else round(float(top_quantile_turnover(df, col)), 4)
            ),
            "negative_ic": bool(ic == ic and ic < 0),
        }
    return payload


def write_factor_report(
    payload: dict[str, Any],
    *,
    run_id: str,
    skill_dir: Path,
    tearsheet: dict[str, Any] | None = None,
) -> Path:
    out_dir = skill_dir / "validation_artifacts" / "factor_analytics" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["run_id"] = run_id
    payload["written_at"] = datetime.now(timezone.utc).isoformat()
    payload["catalog_overwrite"] = False
    (out_dir / "factor_analytics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if tearsheet:
        (out_dir / "book_tearsheet.json").write_text(json.dumps(tearsheet, indent=2), encoding="utf-8")
    return out_dir
