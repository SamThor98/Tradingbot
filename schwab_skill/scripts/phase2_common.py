"""
Phase 2 — shared utilities.

Loader and statistics helpers reused by every Phase 2 script (edge audit,
replay engine, Optuna search, CPCV validator, report).

Design notes
------------
* The on-disk chunk schema is intentionally permissive. The minimum-viable
  ``Trade`` only requires ``return``, ``net_return``, ``entry_date``,
  ``exit_date``, ``stop_pct`` (matches every chunk written by
  ``run_multi_era_backtest_schwab_only.py`` to date). Augmented fields
  (ticker, prices, MFE/MAE, OHLC path) are optional and only populated by
  re-runs that use the Stage-2 instrumentation. Code that needs an
  augmented field must guard with ``trade.has_augmentation()`` and skip
  trades that lack it rather than crash.
* PF / win-rate / expectancy math lives here so every Phase 2 script
  reports the *same* numbers from the *same* code path. No duplication of
  the phase1_trade_diagnostics loader.
* Era bounds are duplicated from phase1_trade_diagnostics on purpose:
  we want Phase 2 to be self-contained and not import from a sibling
  script (which would make pytest discovery fragile and create a
  reverse dependency from common -> trade_diagnostics).
"""

from __future__ import annotations

import json
import logging
import math
import os
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

LOG = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).resolve().parent.parent
_art_root = (os.getenv("BACKTEST_ARTIFACT_DIR") or "").strip()
ARTIFACT_DIR = Path(_art_root) if _art_root else (SKILL_DIR / "validation_artifacts")
CHUNKS_DIR = ARTIFACT_DIR / "multi_era_chunks"

ERA_BOUNDS: dict[str, tuple[str, str | None]] = {
    "late_bull": ("2015-01-01", "2017-12-31"),
    "volatility_chop": ("2018-01-01", "2019-12-31"),
    "crash_recovery": ("2020-01-01", "2021-12-31"),
    "bear_rates": ("2022-01-01", "2023-12-31"),
    "recent_current": ("2024-01-01", None),
}


@dataclass
class Trade:
    """One closed position. Optional fields require Stage-2 instrumentation."""

    era: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    ret: float
    net_ret: float
    stop_pct: float
    # Optional augmented fields. None when chunk was written by the
    # pre-instrumentation backtest.
    ticker: str | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    mfe: float | None = None
    mae: float | None = None
    exit_reason: str | None = None
    signal_score: float | None = None
    # Optional daily OHLC slice between entry and exit, indexed by date.
    # Stored as a list of dicts so it round-trips through JSON cleanly.
    ohlc_path: list[dict[str, Any]] = field(default_factory=list)

    @property
    def hold_days(self) -> int:
        try:
            return max(int((self.exit_date - self.entry_date).days), 0)
        except Exception:
            return 0

    def has_augmentation(self) -> bool:
        return self.entry_price is not None and self.exit_price is not None

    def has_path(self) -> bool:
        return bool(self.ohlc_path)


def load_trades(
    run_id: str,
    *,
    eras: Iterable[str] | None = None,
    chunks_root: Path | None = None,
) -> list[Trade]:
    """
    Load every chunk under ``multi_era_chunks/<run_id>/<era>/chunk_*.json``.

    Falls back to the legacy flat layout (``multi_era_chunks/<era>/chunk_*.json``)
    only when the per-run-id directory does not exist *and* the run_id is
    ``control_legacy`` (the historical default).
    """
    root = chunks_root or CHUNKS_DIR
    base = root / run_id
    if not base.exists() and run_id == "control_legacy":
        # Legacy layout: chunks were written directly under multi_era_chunks/<era>/.
        base = root
        LOG.info("load_trades: %s missing, falling back to flat layout %s", run_id, base)
    selected = list(eras) if eras is not None else list(ERA_BOUNDS.keys())
    trades: list[Trade] = []
    for era in selected:
        era_dir = base / era
        if not era_dir.exists():
            continue
        for chunk_path in sorted(era_dir.glob("chunk_*.json")):
            if chunk_path.name.endswith("_tickers.json"):
                continue
            try:
                payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            except Exception as exc:
                LOG.warning("load_trades: failed to read %s: %s", chunk_path, exc)
                continue
            for raw in payload.get("trades", []):
                try:
                    entry = pd.Timestamp(raw.get("entry_date") or "")
                    exit_ = pd.Timestamp(raw.get("exit_date") or "")
                except Exception:
                    continue
                if pd.isna(entry) or pd.isna(exit_):
                    continue
                trades.append(
                    Trade(
                        era=era,
                        entry_date=entry.normalize(),
                        exit_date=exit_.normalize(),
                        ret=float(raw.get("return", 0.0) or 0.0),
                        net_ret=float(raw.get("net_return", 0.0) or 0.0),
                        stop_pct=float(raw.get("stop_pct", 0.0) or 0.0),
                        ticker=raw.get("ticker"),
                        entry_price=raw.get("entry_price"),
                        exit_price=raw.get("exit_price"),
                        mfe=raw.get("mfe"),
                        mae=raw.get("mae"),
                        exit_reason=raw.get("exit_reason"),
                        signal_score=raw.get("signal_score"),
                        ohlc_path=raw.get("ohlc_path") or [],
                    )
                )
    return trades


# ---------------------------------------------------------------------------
# Aggregate statistics
# ---------------------------------------------------------------------------


def profit_factor(trades: list[Trade]) -> float | None:
    """Sum(net positive returns) / |Sum(net negative returns)|. None if empty."""
    if not trades:
        return None
    wins = sum(t.net_ret for t in trades if t.net_ret > 0)
    losses = -sum(t.net_ret for t in trades if t.net_ret < 0)
    if losses <= 0:
        return float("inf") if wins > 0 else None
    return wins / losses


def win_rate(trades: list[Trade]) -> float | None:
    if not trades:
        return None
    return sum(1 for t in trades if t.net_ret > 0) / len(trades)


def expectancy(trades: list[Trade]) -> float | None:
    if not trades:
        return None
    return statistics.fmean(t.net_ret for t in trades)


def equity_curve(
    trades: list[Trade],
    *,
    starting_equity: float = 100_000.0,
    position_pct: float = 0.10,
) -> list[dict[str, Any]]:
    """
    Replay trades sequentially in (entry_date, exit_date) order. Each trade
    is sized at ``position_pct`` of *current* equity. Returns one row per
    trade containing equity and running drawdown.
    """
    sorted_t = sorted(trades, key=lambda t: (t.entry_date, t.exit_date))
    eq = starting_equity
    peak = eq
    curve: list[dict[str, Any]] = []
    for t in sorted_t:
        size = eq * position_pct
        eq += size * t.net_ret
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        curve.append(
            {
                "exit_date": t.exit_date.isoformat()[:10],
                "equity": round(eq, 2),
                "drawdown_pct": round(dd * 100, 4),
            }
        )
    return curve


def max_drawdown_pct(trades: list[Trade], **kwargs: Any) -> float:
    curve = equity_curve(trades, **kwargs)
    if not curve:
        return 0.0
    return max(point["drawdown_pct"] for point in curve)


def total_return_pct(trades: list[Trade], **kwargs: Any) -> float:
    curve = equity_curve(trades, **kwargs)
    if not curve:
        return 0.0
    starting = float(kwargs.get("starting_equity", 100_000.0))
    final = float(curve[-1]["equity"])
    if starting <= 0:
        return 0.0
    return (final / starting - 1.0) * 100.0


@dataclass
class EraStats:
    era: str
    n: int
    pf: float | None
    win_rate: float | None
    expectancy: float | None
    avg_hold_days: float
    median_stop_pct: float
    max_dd_pct: float
    total_return_pct: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "era": self.era,
            "n": self.n,
            "pf": _jsonify_pf(self.pf),
            "win_rate": self.win_rate,
            "expectancy": self.expectancy,
            "avg_hold_days": self.avg_hold_days,
            "median_stop_pct": self.median_stop_pct,
            "max_dd_pct": self.max_dd_pct,
            "total_return_pct": self.total_return_pct,
        }


def _jsonify_pf(value: float | None) -> float | str | None:
    if value is None:
        return None
    if math.isinf(value):
        return "inf"
    return value


def per_era_stats(trades: list[Trade], eras: Iterable[str] | None = None) -> list[EraStats]:
    selected = list(eras) if eras is not None else list(ERA_BOUNDS.keys())
    out: list[EraStats] = []
    for era in selected:
        et = [t for t in trades if t.era == era]
        if not et:
            continue
        out.append(
            EraStats(
                era=era,
                n=len(et),
                pf=profit_factor(et),
                win_rate=win_rate(et),
                expectancy=expectancy(et),
                avg_hold_days=statistics.fmean(t.hold_days for t in et),
                median_stop_pct=statistics.median(t.stop_pct for t in et),
                max_dd_pct=max_drawdown_pct(et),
                total_return_pct=total_return_pct(et),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Formatting helpers (markdown-friendly)
# ---------------------------------------------------------------------------


def fmt_pf(value: float | None) -> str:
    if value is None:
        return "n/a"
    if math.isinf(value):
        return "inf"
    return f"{value:.3f}"


def fmt_pct_signed(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.3f}%"


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def fmt_pct_unit(value: float | None) -> str:
    """Format a 0-1 value as a 0-100% percentage with no sign."""
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"
