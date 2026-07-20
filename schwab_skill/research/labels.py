"""Forward-return and strategy labels for rank datasets."""

from __future__ import annotations

from typing import Any

import pandas as pd

from research.feature_engine import normalize_ohlcv


def forward_labels_at_index(df: pd.DataFrame, idx: int) -> dict[str, Any] | None:
    """
    Compute advisory-compatible forward labels at bar ``idx``.

    Requires ``idx + 40 < len(df)``. Features must be frozen at ``idx``;
    this function only reads future bars for labels.
    """
    if idx < 0 or idx + 40 >= len(df):
        return None
    close = df["close"].astype(float)
    close_t = float(close.iloc[idx])
    if close_t <= 0:
        return None
    ret_5 = (float(close.iloc[idx + 5]) - close_t) / close_t
    ret_10 = (float(close.iloc[idx + 10]) - close_t) / close_t
    ret_20 = (float(close.iloc[idx + 20]) - close_t) / close_t
    ret_40 = (float(close.iloc[idx + 40]) - close_t) / close_t
    lows_10 = close.iloc[idx + 1 : idx + 11]
    lows_40 = close.iloc[idx + 1 : idx + 41]
    dd_10 = float((float(lows_10.min()) - close_t) / close_t) if len(lows_10) else 0.0
    dd_40 = float((float(lows_40.min()) - close_t) / close_t) if len(lows_40) else 0.0
    return {
        "y_up_5d": int(ret_5 > 0),
        "y_up_10d": int(ret_10 > 0),
        "y_up_20d": int(ret_20 > 0),
        "y_up_40d": int(ret_40 > 0),
        "ret_5d_fwd": float(ret_5),
        "ret_10d_fwd": float(ret_10),
        "ret_20d_fwd": float(ret_20),
        "ret_40d_fwd": float(ret_40),
        "drawdown_10d": float(dd_10),
        "drawdown_40d": float(dd_40),
    }


def attach_forward_labels(
    feature_row: dict[str, Any],
    bars: pd.DataFrame,
) -> dict[str, Any] | None:
    """Join forward labels onto one feature row using full bar history."""
    norm = normalize_ohlcv(bars)
    asof = pd.Timestamp(feature_row["asof_date"]).normalize()
    idx_list = norm.index
    # Match asof to last bar on/before date
    eligible = norm.loc[idx_list <= asof]
    if eligible.empty:
        return None
    asof_ts = eligible.index[-1]
    loc = norm.index.get_loc(asof_ts)
    if isinstance(loc, slice):
        idx = loc.stop - 1
    elif isinstance(loc, (list, tuple)):
        idx = int(loc[-1])
    else:
        idx = int(loc)
    labels = forward_labels_at_index(norm, idx)
    if labels is None:
        return None
    out = dict(feature_row)
    out.update(labels)
    return out


def strategy_label_frame_from_trades(trades: list[dict[str, Any]] | pd.DataFrame) -> pd.DataFrame:
    """
    Normalize strategy trade outcomes for join on (ticker, asof_date≈entry_date).

    Expects dict rows or DataFrame with ticker, entry_date, net_return (and optional
    return, mfe, mae, exit_reason, era, rank_score_v2).
    """
    if isinstance(trades, pd.DataFrame):
        raw = trades.copy()
    else:
        raw = pd.DataFrame(list(trades))
    if raw.empty:
        return pd.DataFrame(
            columns=[
                "ticker",
                "asof_date",
                "net_return",
                "strategy_return",
                "mfe",
                "mae",
                "exit_reason",
                "era",
                "rank_score_v2",
                "r_multiple",
            ]
        )
    out = pd.DataFrame()
    out["ticker"] = raw.get("ticker", pd.Series(dtype=str)).astype(str).str.upper()
    out["asof_date"] = pd.to_datetime(raw.get("entry_date")).dt.strftime("%Y-%m-%d")
    out["net_return"] = pd.to_numeric(raw.get("net_return"), errors="coerce")
    out["strategy_return"] = pd.to_numeric(raw.get("return"), errors="coerce")
    out["mfe"] = pd.to_numeric(raw.get("mfe"), errors="coerce") if "mfe" in raw.columns else None
    out["mae"] = pd.to_numeric(raw.get("mae"), errors="coerce") if "mae" in raw.columns else None
    out["exit_reason"] = raw.get("exit_reason")
    out["era"] = raw.get("era")
    if "rank_score_v2" in raw.columns:
        out["rank_score_v2"] = pd.to_numeric(raw["rank_score_v2"], errors="coerce")
    # R-multiple proxy: net_return / |stop| when stop_pct present, else net/|mae|
    stop = pd.to_numeric(raw.get("stop_pct"), errors="coerce") if "stop_pct" in raw.columns else None
    if stop is not None:
        out["r_multiple"] = out["net_return"] / stop.replace(0, pd.NA).abs()
    elif out["mae"] is not None:
        out["r_multiple"] = out["net_return"] / out["mae"].abs().replace(0, pd.NA)
    else:
        out["r_multiple"] = pd.NA
    return out.dropna(subset=["ticker", "asof_date"])


def join_strategy_labels(features: pd.DataFrame, strategy: pd.DataFrame) -> pd.DataFrame:
    """Left-join strategy labels onto feature rows."""
    if features.empty:
        return features.copy()
    feat = features.copy()
    feat["ticker"] = feat["ticker"].astype(str).str.upper()
    feat["asof_date"] = pd.to_datetime(feat["asof_date"]).dt.strftime("%Y-%m-%d")
    if strategy.empty:
        feat["net_return"] = pd.NA
        feat["r_multiple"] = pd.NA
        return feat
    strat = strategy.copy()
    strat["ticker"] = strat["ticker"].astype(str).str.upper()
    strat["asof_date"] = pd.to_datetime(strat["asof_date"]).dt.strftime("%Y-%m-%d")
    # One strategy outcome per ticker/day (keep last)
    strat = strat.drop_duplicates(subset=["ticker", "asof_date"], keep="last")
    rename = {
        c: (c if c in ("ticker", "asof_date") else f"strategy_{c}" if c in feat.columns else c)
        for c in strat.columns
    }
    # Keep net_return / r_multiple / era without double-prefix when absent on features
    keep_plain = {"net_return", "r_multiple", "era", "mfe", "mae", "exit_reason", "strategy_return", "rank_score_v2"}
    for c in list(rename):
        if c in ("ticker", "asof_date"):
            continue
        if c in keep_plain and c not in feat.columns:
            rename[c] = c
    strat = strat.rename(columns=rename)
    return feat.merge(strat, on=["ticker", "asof_date"], how="left")


LABEL_COLUMNS = [
    "y_up_5d",
    "y_up_10d",
    "y_up_20d",
    "y_up_40d",
    "ret_5d_fwd",
    "ret_10d_fwd",
    "ret_20d_fwd",
    "ret_40d_fwd",
    "drawdown_10d",
    "drawdown_40d",
    "net_return",
    "r_multiple",
]
