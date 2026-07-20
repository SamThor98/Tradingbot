"""Sector × regime trade attribution and sector-gate counterfactuals (P0).

Pure helpers for tagging backtest trades and summarizing PF / expectancy by
(sector_etf, regime_bucket). Used by ``backtest.py`` and
``scripts/analyze_sector_regime.py``.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

REGIME_BUCKETS = ("bull", "chop", "bear", "unknown")
MIN_TRADES_DEFAULT = 30


def classify_entry_regime_bucket(
    *,
    above_sma_200: bool | None,
    ret_63: float | None,
) -> str:
    """Map entry-day SPY state to bull / chop / bear.

    Rules (aligned with ablation regime slices):
    - bull: SPY above 200 SMA and 63d return > 0
    - bear: SPY below 200 SMA and 63d return < 0
    - chop: otherwise (including missing return)
    """
    if above_sma_200 is None:
        return "unknown"
    if ret_63 is None:
        return "chop" if above_sma_200 else "bear"
    if bool(above_sma_200) and float(ret_63) > 0.0:
        return "bull"
    if (not bool(above_sma_200)) and float(ret_63) < 0.0:
        return "bear"
    return "chop"


def build_spy_regime_series(spy_df: pd.DataFrame | None) -> tuple[pd.Series | None, pd.Series | None]:
    """Return (above_200, regime_bucket) series indexed like ``spy_df``."""
    if spy_df is None or spy_df.empty or len(spy_df) < 200:
        return None, None
    if "close" not in spy_df.columns:
        return None, None

    from stage_analysis import add_indicators

    spy = add_indicators(spy_df.copy())
    if "sma_200" not in spy.columns:
        return None, None

    above = spy["close"] > spy["sma_200"]
    ret_63 = spy["close"].pct_change(63)
    buckets: list[str] = []
    for idx in spy.index:
        a = above.loc[idx]
        r = ret_63.loc[idx]
        above_val: bool | None
        if pd.isna(a):
            above_val = None
        else:
            above_val = bool(a)
        ret_val: float | None
        if pd.isna(r):
            ret_val = None
        else:
            ret_val = float(r)
        buckets.append(classify_entry_regime_bucket(above_sma_200=above_val, ret_63=ret_val))
    return above.astype(bool), pd.Series(buckets, index=spy.index, dtype="object")


def lookup_regime_at(
    ts: pd.Timestamp,
    above_200: pd.Series | None,
    buckets: pd.Series | None,
) -> dict[str, Any]:
    """Point-in-time regime tags for an entry timestamp (pad to prior bar)."""
    out: dict[str, Any] = {
        "regime_above_200": None,
        "regime_bucket": "unknown",
    }
    day = pd.Timestamp(ts)
    if above_200 is not None and not above_200.empty:
        try:
            i = int(above_200.index.get_indexer([day], method="pad")[0])
            if i >= 0:
                out["regime_above_200"] = bool(above_200.iloc[i])
        except Exception:
            pass
    if buckets is not None and not buckets.empty:
        try:
            i = int(buckets.index.get_indexer([day], method="pad")[0])
            if i >= 0:
                out["regime_bucket"] = str(buckets.iloc[i] or "unknown")
        except Exception:
            pass
    return out


def cohort_metrics(returns: pd.Series, *, min_trades: int = 0) -> dict[str, Any]:
    """PF / expectancy / hit-rate for a return series (net preferred upstream)."""
    s = pd.to_numeric(returns, errors="coerce").dropna()
    n = int(len(s))
    if n == 0:
        return {
            "n": 0,
            "profit_factor": None,
            "expectancy": None,
            "hit_rate": None,
            "avg_win": None,
            "avg_loss": None,
            "sparse": True,
        }
    wins = s[s > 0]
    losses = s[s <= 0]
    gross_profit = float(wins.sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses.sum())) if not losses.empty else 0.0
    if gross_loss > 0:
        pf: float | None = round(gross_profit / gross_loss, 4)
    elif gross_profit > 0:
        pf = 99.0
    else:
        pf = None
    return {
        "n": n,
        "profit_factor": pf,
        "expectancy": round(float(s.mean()), 6),
        "hit_rate": round(float((s > 0).mean()), 4),
        "avg_win": round(float(wins.mean()), 6) if not wins.empty else None,
        "avg_loss": round(float(losses.mean()), 6) if not losses.empty else None,
        "sparse": n < int(min_trades),
    }


def _trade_frame(trades: list[dict[str, Any]]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    df = pd.DataFrame(trades)
    if "net_return" not in df.columns and "return" in df.columns:
        df["net_return"] = df["return"]
    if "sector_etf" not in df.columns:
        df["sector_etf"] = "unknown"
    else:
        df["sector_etf"] = df["sector_etf"].fillna("unknown").astype(str)
        df.loc[df["sector_etf"].isin(["", "None", "nan"]), "sector_etf"] = "unknown"
    if "regime_bucket" not in df.columns:
        df["regime_bucket"] = "unknown"
    else:
        df["regime_bucket"] = df["regime_bucket"].fillna("unknown").astype(str)
    if "sector_filter" not in df.columns:
        df["sector_filter"] = ""
    return df


def summarize_sector_regime(
    trades: list[dict[str, Any]],
    *,
    min_trades: int = MIN_TRADES_DEFAULT,
) -> dict[str, Any]:
    """Build overall / by-regime / by-sector / sector×regime pivots."""
    df = _trade_frame(trades)
    if df.empty:
        return {
            "overall": cohort_metrics(pd.Series(dtype=float), min_trades=min_trades),
            "by_regime": {},
            "by_sector": {},
            "by_sector_regime": {},
            "min_trades": int(min_trades),
            "trade_count": 0,
        }

    by_regime: dict[str, Any] = {}
    for bucket, group in df.groupby("regime_bucket", dropna=False):
        by_regime[str(bucket)] = cohort_metrics(group["net_return"], min_trades=min_trades)

    by_sector: dict[str, Any] = {}
    for sector, group in df.groupby("sector_etf", dropna=False):
        by_sector[str(sector)] = cohort_metrics(group["net_return"], min_trades=min_trades)

    by_sector_regime: dict[str, Any] = {}
    for (sector, bucket), group in df.groupby(["sector_etf", "regime_bucket"], dropna=False):
        key = f"{sector}|{bucket}"
        by_sector_regime[key] = {
            "sector_etf": str(sector),
            "regime_bucket": str(bucket),
            **cohort_metrics(group["net_return"], min_trades=min_trades),
        }

    return {
        "overall": cohort_metrics(df["net_return"], min_trades=min_trades),
        "by_regime": by_regime,
        "by_sector": by_sector,
        "by_sector_regime": by_sector_regime,
        "min_trades": int(min_trades),
        "trade_count": int(len(df)),
    }


def sector_gate_counterfactual(
    trades: list[dict[str, Any]],
    *,
    min_trades: int = MIN_TRADES_DEFAULT,
    winning_reasons: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Compare all trades vs hard sector-gate keep-set (sector_winning).

    Intended for a shadow-gate backtest run where losing-sector trades are kept
    and tagged via ``sector_filter``.
    """
    keep = winning_reasons or frozenset({"sector_winning"})
    df = _trade_frame(trades)
    if df.empty:
        empty = summarize_sector_regime([], min_trades=min_trades)
        return {
            "baseline_shadow": empty,
            "hard_gate": empty,
            "lift": {"profit_factor_delta": None, "expectancy_delta": None, "trade_count_ratio": None},
            "by_regime_lift": {},
            "min_trades": int(min_trades),
        }

    hard_df = df[df["sector_filter"].astype(str).isin(keep)]
    baseline = summarize_sector_regime(df.to_dict(orient="records"), min_trades=min_trades)
    hard = summarize_sector_regime(hard_df.to_dict(orient="records"), min_trades=min_trades)

    b_pf = baseline["overall"].get("profit_factor")
    h_pf = hard["overall"].get("profit_factor")
    b_exp = baseline["overall"].get("expectancy")
    h_exp = hard["overall"].get("expectancy")
    b_n = int(baseline["overall"].get("n") or 0)
    h_n = int(hard["overall"].get("n") or 0)

    by_regime_lift: dict[str, Any] = {}
    for bucket in sorted(set(baseline["by_regime"]) | set(hard["by_regime"])):
        bm = baseline["by_regime"].get(bucket) or {}
        hm = hard["by_regime"].get(bucket) or {}
        bp = bm.get("profit_factor")
        hp = hm.get("profit_factor")
        be = bm.get("expectancy")
        he = hm.get("expectancy")
        by_regime_lift[bucket] = {
            "baseline_n": bm.get("n", 0),
            "hard_n": hm.get("n", 0),
            "profit_factor_delta": (
                round(float(hp) - float(bp), 4) if bp is not None and hp is not None else None
            ),
            "expectancy_delta": (
                round(float(he) - float(be), 6) if be is not None and he is not None else None
            ),
            "baseline_sparse": bool(bm.get("sparse")),
            "hard_sparse": bool(hm.get("sparse")),
        }

    positive_regimes = sum(
        1
        for bucket, row in by_regime_lift.items()
        if bucket in {"bull", "chop", "bear"}
        and not row.get("hard_sparse")
        and row.get("profit_factor_delta") is not None
        and float(row["profit_factor_delta"]) > 0
    )

    return {
        "baseline_shadow": baseline,
        "hard_gate": hard,
        "lift": {
            "profit_factor_delta": (
                round(float(h_pf) - float(b_pf), 4) if b_pf is not None and h_pf is not None else None
            ),
            "expectancy_delta": (
                round(float(h_exp) - float(b_exp), 6) if b_exp is not None and h_exp is not None else None
            ),
            "trade_count_ratio": round(h_n / b_n, 4) if b_n > 0 else None,
            "positive_regime_lift_count": positive_regimes,
        },
        "by_regime_lift": by_regime_lift,
        "min_trades": int(min_trades),
        "recommendation": _recommend_gate(positive_regimes, b_n=b_n, h_n=h_n, pf_delta=(
            None if b_pf is None or h_pf is None else float(h_pf) - float(b_pf)
        )),
    }


def _recommend_gate(
    positive_regimes: int,
    *,
    b_n: int,
    h_n: int,
    pf_delta: float | None,
) -> str:
    if b_n < MIN_TRADES_DEFAULT or h_n < MIN_TRADES_DEFAULT:
        return "insufficient_sample_keep_shadow"
    if pf_delta is None:
        return "inconclusive_keep_shadow"
    if positive_regimes >= 2 and pf_delta > 0:
        return "promote_hard_candidate"
    if pf_delta <= 0:
        return "keep_shadow_no_lift"
    return "keep_shadow_unstable_across_regimes"


def paired_run_lift(baseline: dict[str, Any], treatment: dict[str, Any]) -> dict[str, Any]:
    """Compare two full backtest summary payloads (shadow vs hard runs)."""
    b = baseline.get("overall") or baseline
    t = treatment.get("overall") or treatment
    # Accept either summarize_sector_regime overall or flat backtest metrics
    def _pf(payload: dict[str, Any]) -> float | None:
        if "profit_factor" in payload and payload["profit_factor"] != "inf":
            try:
                return float(payload["profit_factor"])
            except (TypeError, ValueError):
                return None
        if "profit_factor_net" in payload and payload["profit_factor_net"] != "inf":
            try:
                return float(payload["profit_factor_net"])
            except (TypeError, ValueError):
                return None
        return None

    def _n(payload: dict[str, Any]) -> int:
        if "n" in payload:
            return int(payload.get("n") or 0)
        return int(payload.get("total_trades") or 0)

    def _exp(payload: dict[str, Any]) -> float | None:
        if payload.get("expectancy") is not None:
            return float(payload["expectancy"])
        if payload.get("avg_return_net_pct") is not None:
            return float(payload["avg_return_net_pct"]) / 100.0
        return None

    b_pf, t_pf = _pf(b), _pf(t)
    b_n, t_n = _n(b), _n(t)
    b_exp, t_exp = _exp(b), _exp(t)
    return {
        "profit_factor_delta": (
            round(float(t_pf) - float(b_pf), 4) if b_pf is not None and t_pf is not None else None
        ),
        "expectancy_delta": (
            round(float(t_exp) - float(b_exp), 6) if b_exp is not None and t_exp is not None else None
        ),
        "trade_count_ratio": round(t_n / b_n, 4) if b_n > 0 else None,
        "baseline_n": b_n,
        "treatment_n": t_n,
    }
