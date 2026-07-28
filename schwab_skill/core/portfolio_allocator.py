"""Portfolio allocator (constitution model E): caps, DD staircase, crash, regime.

ALLOCATOR_MODE=off|shadow|live — shadow computes decisions without blocking;
live blocks new risk-increasing orders when constitution says so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.multi_sleeve_constitution import (
    CAP_CLUSTER,
    CAP_GROSS,
    CAP_NAME,
    CAP_S0,
    CAP_S1,
    CAP_S1_PROMOTED,
    CRASH_CLEAR_NO_NEW_LOW_DAYS,
    CRASH_CLEAR_SMA,
    CRASH_LOOKBACK_DAYS,
    CRASH_SPY_DROP,
    DD_FLOOR,
    DD_HARD,
    DD_SOFT,
    SLEEVE_S0,
    SLEEVE_S1,
)

LOG = logging.getLogger(__name__)


@dataclass
class AllocatorDecision:
    """Result of evaluating new-risk against the multi-sleeve constitution."""

    allow_new_risk: bool
    crash_active: bool = False
    dd_tier: str = "ok"  # ok | soft | hard | floor
    regime_bear: bool = False
    data_quality_ok: bool = True
    s0_cap: float = CAP_S0
    s1_cap: float = CAP_S1
    s1_new_risk_mult: float = 1.0  # 1.0 normal, 0.5 bear half-cap, 0.0 crash/block
    s0_new_risk_mult: float = 1.0
    cash_weight: float = 0.0
    reasons: list[str] = field(default_factory=list)
    mode: str = "off"

    def as_dict(self) -> dict[str, Any]:
        return {
            "allow_new_risk": self.allow_new_risk,
            "crash_active": self.crash_active,
            "dd_tier": self.dd_tier,
            "regime_bear": self.regime_bear,
            "data_quality_ok": self.data_quality_ok,
            "s0_cap": self.s0_cap,
            "s1_cap": self.s1_cap,
            "s0_new_risk_mult": self.s0_new_risk_mult,
            "s1_new_risk_mult": self.s1_new_risk_mult,
            "cash_weight": self.cash_weight,
            "reasons": list(self.reasons),
            "mode": self.mode,
            "caps": {
                "gross": CAP_GROSS,
                "name": CAP_NAME,
                "cluster": CAP_CLUSTER,
            },
        }


def detect_crash_mode(
    spy_closes: list[float],
    *,
    drop: float = CRASH_SPY_DROP,
    lookback: int = CRASH_LOOKBACK_DAYS,
) -> bool:
    """True when SPY peak-to-trough within lookback is <= -drop."""
    if not spy_closes or len(spy_closes) < 2:
        return False
    window = spy_closes[-max(lookback, 2) :]
    peak = max(window)
    trough = min(window)
    if peak <= 0:
        return False
    return (peak - trough) / peak >= drop


def crash_clear(
    spy_closes: list[float],
    *,
    sma_period: int = CRASH_CLEAR_SMA,
    no_new_low_days: int = CRASH_CLEAR_NO_NEW_LOW_DAYS,
) -> bool:
    """Clear when last close > SMA(sma_period) and no new lookback low in last N sessions."""
    need = max(sma_period, no_new_low_days) + 1
    if len(spy_closes) < need:
        return False
    closes = [float(x) for x in spy_closes]
    last = closes[-1]
    sma = sum(closes[-sma_period:]) / sma_period
    if last <= sma:
        return False
    lookback = closes[-(CRASH_LOOKBACK_DAYS + no_new_low_days) :]
    if len(lookback) < no_new_low_days + 1:
        return False
    recent = lookback[-no_new_low_days:]
    prior = lookback[:-no_new_low_days]
    if not prior:
        return False
    prior_low = min(prior)
    return min(recent) > prior_low


def drawdown_tier(drawdown_from_peak: float | None) -> str:
    """Map |DD| fraction to ok/soft/hard/floor."""
    if drawdown_from_peak is None:
        return "ok"
    try:
        dd = abs(float(drawdown_from_peak))
    except (TypeError, ValueError):
        return "ok"
    if dd >= DD_FLOOR:
        return "floor"
    if dd >= DD_HARD:
        return "hard"
    if dd >= DD_SOFT:
        return "soft"
    return "ok"


def evaluate_allocator(
    *,
    mode: str = "off",
    data_quality: str = "ok",
    regime_bear: bool = False,
    crash_active: bool = False,
    drawdown_from_peak: float | None = None,
    s1_promoted_cap: bool = False,
    sleeve_killed: set[str] | frozenset[str] | None = None,
) -> AllocatorDecision:
    """Evaluate whether new risk is allowed and per-sleeve multipliers."""
    mode_n = str(mode or "off").strip().lower()
    decision = AllocatorDecision(allow_new_risk=True, mode=mode_n)
    if mode_n == "off":
        decision.reasons.append("allocator_off")
        return decision

    killed = {str(s).upper() for s in (sleeve_killed or set())}
    decision.s1_cap = CAP_S1_PROMOTED if s1_promoted_cap else CAP_S1
    decision.s0_cap = CAP_S0

    dq = str(data_quality or "").strip().lower()
    decision.data_quality_ok = dq in ("", "ok")
    if not decision.data_quality_ok:
        decision.allow_new_risk = False
        decision.s0_new_risk_mult = 0.0
        decision.s1_new_risk_mult = 0.0
        decision.reasons.append(f"data_quality_block:{dq or 'unknown'}")

    decision.dd_tier = drawdown_tier(drawdown_from_peak)
    if decision.dd_tier in ("hard", "floor"):
        decision.allow_new_risk = False
        decision.s0_new_risk_mult = 0.0
        decision.s1_new_risk_mult = 0.0
        decision.reasons.append(f"dd_block:{decision.dd_tier}")
    elif decision.dd_tier == "soft":
        decision.s0_new_risk_mult = min(decision.s0_new_risk_mult, 0.5)
        decision.s1_new_risk_mult = min(decision.s1_new_risk_mult, 0.5)
        decision.reasons.append("dd_soft_halve")

    decision.crash_active = bool(crash_active)
    if decision.crash_active:
        decision.allow_new_risk = False
        decision.s0_new_risk_mult = 0.0
        decision.s1_new_risk_mult = 0.0
        decision.reasons.append("crash_mode")

    decision.regime_bear = bool(regime_bear)
    if decision.regime_bear and not decision.crash_active:
        # Asymmetric: S0 blocked; S1 half-cap until crash
        decision.s0_new_risk_mult = 0.0
        decision.s1_new_risk_mult = min(decision.s1_new_risk_mult, 0.5)
        decision.reasons.append("regime_bear_s0_block_s1_half")

    if SLEEVE_S0 in killed:
        decision.s0_new_risk_mult = 0.0
        decision.reasons.append("sleeve_killed:S0")
    if SLEEVE_S1 in killed:
        decision.s1_new_risk_mult = 0.0
        decision.reasons.append("sleeve_killed:S1")

    # Effective invested room → residual cash weight hint
    s0_eff = decision.s0_cap * decision.s0_new_risk_mult
    s1_eff = decision.s1_cap * decision.s1_new_risk_mult
    deployed = min(CAP_GROSS, s0_eff + s1_eff)
    decision.cash_weight = max(0.0, CAP_GROSS - deployed)
    if not decision.allow_new_risk:
        decision.cash_weight = CAP_GROSS

    return decision


def apply_name_and_cluster_caps(
    weights: dict[str, float],
    *,
    sector_by_ticker: dict[str, str] | None = None,
    name_cap: float = CAP_NAME,
    cluster_cap: float = CAP_CLUSTER,
) -> dict[str, float]:
    """Clip single-name and sector cluster weights; renormalize if needed."""
    if not weights:
        return {}
    out = {str(t).upper(): float(w) for t, w in weights.items() if float(w) > 0}
    for tkr in list(out):
        if out[tkr] > name_cap:
            out[tkr] = name_cap
    if sector_by_ticker:
        sectors: dict[str, list[str]] = {}
        for tkr, w in out.items():
            sec = str(sector_by_ticker.get(tkr) or "UNKNOWN").upper()
            sectors.setdefault(sec, []).append(tkr)
        for _sec, tickers in sectors.items():
            total = sum(out[t] for t in tickers)
            if total > cluster_cap and total > 0:
                scale = cluster_cap / total
                for t in tickers:
                    out[t] *= scale
    return out


def scale_sleeve_bundle(
    sleeve_weights: dict[str, float],
    *,
    sleeve_cap: float,
    risk_mult: float,
) -> dict[str, float]:
    """Scale a normalized sleeve bundle to sleeve_cap * risk_mult."""
    if not sleeve_weights or sleeve_cap <= 0 or risk_mult <= 0:
        return {}
    total = sum(abs(float(v)) for v in sleeve_weights.values())
    if total <= 0:
        return {}
    target = sleeve_cap * risk_mult
    return {str(t).upper(): (float(w) / total) * target for t, w in sleeve_weights.items()}


def idle_s1_to_cash(s1_cap: float, s1_deployed: float) -> float:
    """Unused S1 budget stays in S3 (cash), not auto-filled by S0."""
    return max(0.0, float(s1_cap) - max(0.0, float(s1_deployed)))
