"""Isolated param sweep (vectorbt optional). NOT promotion-eligible.

Must not be imported from signal_scanner.py or execution.py.
Any survivor must be re-run through run_multi_era_backtest_schwab_only.py
with live-parity code.
"""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np
import pandas as pd

NOT_PROMOTION_ELIGIBLE = (
    "NOT PROMOTION-ELIGIBLE. Any survivor must be re-run through "
    "run_multi_era_backtest_schwab_only.py with live-parity code."
)


def _pf(returns: np.ndarray) -> float:
    r = np.asarray(returns, dtype=np.float64)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return 0.0
    wins = float(r[r > 0].sum())
    losses = float(-r[r <= 0].sum())
    if losses <= 0:
        return 99.0 if wins > 0 else 0.0
    return wins / losses


def _simulate_path(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    *,
    entry_idx: int,
    atr_mult: float,
    breakout_buffer: float,
    hold_days: int,
    atr: float,
) -> float | None:
    """Cheap path PnL for one entry. Not live-parity."""
    if entry_idx < 1 or entry_idx >= len(close) - 1:
        return None
    entry = float(close[entry_idx])
    if entry <= 0:
        return None
    prior_high = float(np.max(high[max(0, entry_idx - 20) : entry_idx]))
    if prior_high > 0 and entry < prior_high * (1.0 + breakout_buffer):
        return None  # filtered — not a trade
    stop_pct = max(0.02, min(0.20, (atr / entry) * atr_mult if atr > 0 else 0.07))
    last = min(entry_idx + int(hold_days), len(close) - 1)
    highest = entry
    for j in range(entry_idx + 1, last + 1):
        px = float(close[j])
        lo = float(low[j])
        hi = float(high[j])
        highest = max(highest, hi, px)
        if min(px, lo) <= entry * (1.0 - stop_pct):
            return (px - entry) / entry
        if min(px, lo) <= highest * (1.0 - stop_pct) and (j - entry_idx) >= 15:
            return (px - entry) / entry
    return (float(close[last]) - entry) / entry


def sweep_frozen_trades(
    trades: pd.DataFrame,
    *,
    atr_multiples: Iterable[float] = (2.0, 2.5, 3.0),
    breakout_buffers: Iterable[float] = (0.0, 0.01),
    hold_days: Iterable[int] = (30, 40),
) -> pd.DataFrame:
    """
    Sweep using frozen trade net_return as the baseline path.

    When ``ohlc_path`` is absent, ATR/hold variants re-scale the frozen return
    as a diagnostic — still not promotion-eligible.
    """
    rows: list[dict[str, Any]] = []
    base_n = int(len(trades))
    ret = pd.to_numeric(trades.get("net_return"), errors="coerce").to_numpy(dtype=np.float64) if "net_return" in trades.columns else np.zeros(base_n)
    for am in atr_multiples:
        for buf in breakout_buffers:
            for hd in hold_days:
                # Frozen-list proxy: drop a slice as if buffer filtered, scale hold
                keep = np.ones(base_n, dtype=bool)
                if "breakout_distance_pct" in trades.columns:
                    dist = pd.to_numeric(trades["breakout_distance_pct"], errors="coerce").fillna(0.0).to_numpy()
                    keep = dist >= float(buf)
                sample = ret[keep]
                if float(hd) < 40 and len(sample):
                    sample = sample * (float(hd) / 40.0)
                n = int(len(sample))
                rows.append(
                    {
                        "atr_multiple": float(am),
                        "breakout_buffer": float(buf),
                        "hold_days": int(hd),
                        "n": n,
                        "retention": round(n / max(base_n, 1), 4),
                        "pf": round(_pf(sample), 4),
                        "promotion_eligible": False,
                    }
                )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["pf", "n"], ascending=[False, False]).reset_index(drop=True)
    return out


def sweep_ohlcv_panel(
    panel: dict[str, pd.DataFrame],
    *,
    atr_multiples: Iterable[float] = (2.0, 2.5),
    breakout_buffers: Iterable[float] = (0.0, 0.01),
    hold_days: Iterable[int] = (40,),
    entry_every: int = 20,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for am in atr_multiples:
        for buf in breakout_buffers:
            for hd in hold_days:
                rets: list[float] = []
                for _tkr, df in panel.items():
                    if df is None or len(df) < 40:
                        continue
                    close = df["close"].astype(float).to_numpy()
                    high = df["high"].astype(float).to_numpy() if "high" in df.columns else close
                    low = df["low"].astype(float).to_numpy() if "low" in df.columns else close
                    # crude ATR
                    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
                    atr_s = pd.Series(tr).rolling(14).mean().to_numpy()
                    for i in range(30, len(close) - int(hd) - 1, max(1, entry_every)):
                        atr = float(atr_s[i - 1]) if i - 1 < len(atr_s) and atr_s[i - 1] == atr_s[i - 1] else 0.0
                        sim = _simulate_path(
                            close, high, low, entry_idx=i, atr_mult=float(am),
                            breakout_buffer=float(buf), hold_days=int(hd), atr=atr,
                        )
                        if sim is not None:
                            rets.append(float(sim))
                arr = np.asarray(rets, dtype=np.float64)
                rows.append(
                    {
                        "atr_multiple": float(am),
                        "breakout_buffer": float(buf),
                        "hold_days": int(hd),
                        "n": int(len(arr)),
                        "retention": 1.0,
                        "pf": round(_pf(arr), 4),
                        "promotion_eligible": False,
                    }
                )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["pf", "n"], ascending=[False, False]).reset_index(drop=True)
    return out


def try_vectorbt_available() -> bool:
    try:
        import vectorbt  # noqa: F401
    except Exception:
        return False
    return True
