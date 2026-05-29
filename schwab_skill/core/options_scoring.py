"""Options-chain intelligence -> scan scoring overlay (OFF -> SHADOW -> LIVE).

Computes a small, bounded score delta from options intelligence (ATM IV,
put/call skew, expected move) and, in ``shadow`` (default), attaches it to the
top survivors for measurement WITHOUT changing ranking. In ``live`` the delta is
applied to ``rank_score`` / ``composite_score`` and the survivors are re-sorted.

The scoring math (:func:`compute_options_score_delta`) is pure and offline-
testable; the overlay injects the chain fetcher so it can be tested without
network access.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger(__name__)

# Hard bound on the per-signal delta so the overlay can never dominate the
# primary score stack.
_MAX_ABS_DELTA = 5.0


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float = -_MAX_ABS_DELTA, hi: float = _MAX_ABS_DELTA) -> float:
    return max(lo, min(hi, x))


def compute_options_score_delta(intel: dict[str, Any] | None, signal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return ``{"delta": float, "reasons": [...]}`` from options intel.

    Heuristics (all bounded, conservative):
    - Rich ATM IV (> 80%) → premium/event risk → mild penalty.
    - Positive put/call skew (puts richer than calls = defensive hedging) →
      penalty; negative skew (calls richer) → small bonus.
    - Expected move much larger than the advisory's expected move → event-risk
      mismatch → penalty.
    """
    intel = intel or {}
    sig = signal or {}
    reasons: list[str] = []
    delta = 0.0

    atm_iv = _f(intel.get("atm_iv"))
    if atm_iv is not None:
        if atm_iv > 0.80:
            delta -= 2.0
            reasons.append(f"rich_atm_iv:{round(atm_iv * 100, 1)}%")
        elif atm_iv < 0.25:
            delta += 1.0
            reasons.append("calm_atm_iv")

    skew = _f(intel.get("put_call_skew"))
    if skew is not None:
        if skew > 0.03:
            delta -= 1.5
            reasons.append("defensive_put_skew")
        elif skew < -0.03:
            delta += 1.0
            reasons.append("call_skew_bullish")

    opt_move = _f(intel.get("expected_move_pct"))
    advisory = sig.get("advisory") if isinstance(sig.get("advisory"), dict) else {}
    adv_move = _f(advisory.get("expected_move_10d"))
    if opt_move is not None and adv_move is not None and adv_move > 0:
        # advisory expected_move_10d is a fraction (e.g. 0.05); options pct is %.
        adv_move_pct = adv_move * 100.0
        if opt_move > adv_move_pct * 2.0:
            delta -= 1.5
            reasons.append("options_imply_larger_move")

    return {"delta": round(_clamp(delta), 2), "reasons": reasons}


def _mode(skill_dir: Path | None) -> str:
    try:
        from config import get_options_scoring_mode

        return get_options_scoring_mode(skill_dir)
    except Exception:
        return "off"


def _intel_enabled(skill_dir: Path | None) -> bool:
    try:
        from config import get_options_intel_mode

        return get_options_intel_mode(skill_dir) != "off"
    except Exception:
        return False


def _max_symbols(skill_dir: Path | None) -> int:
    try:
        from config import get_options_scoring_max_symbols

        return get_options_scoring_max_symbols(skill_dir)
    except Exception:
        return 15


def _default_chain_fetcher(skill_dir: Path | None) -> Callable[[str], dict[str, Any] | None]:
    from market_data import get_options_chain_with_status

    def _fetch(ticker: str) -> dict[str, Any] | None:
        chain, _meta = get_options_chain_with_status(ticker, skill_dir=skill_dir)
        return chain

    return _fetch


def apply_options_scoring(
    signals: list[dict[str, Any]],
    diagnostics: dict[str, Any] | None = None,
    *,
    skill_dir: Path | None = None,
    chain_fetcher: Callable[[str], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    """Attach options score deltas to top survivors; apply only in live mode.

    Returns the (possibly re-sorted) signals list. Safe no-op when the overlay
    or the options data source is disabled.
    """
    diag = diagnostics if isinstance(diagnostics, dict) else {}
    mode = _mode(skill_dir)
    summary = {"mode": mode, "evaluated": 0, "applied": 0, "errors": 0}
    if mode == "off" or not _intel_enabled(skill_dir) or not signals:
        diag["options_scoring"] = summary
        return signals

    from core.providers import OptionsProvider

    fetch = chain_fetcher or _default_chain_fetcher(skill_dir)
    cap = _max_symbols(skill_dir)

    for sig in signals[:cap]:
        if not isinstance(sig, dict):
            continue
        ticker = str(sig.get("ticker") or "").upper()
        if not ticker:
            continue
        try:
            chain = fetch(ticker)
            if not chain:
                continue
            intel = OptionsProvider.normalize_chain(chain).model_dump(mode="json")
            scored = compute_options_score_delta(intel, sig)
            sig["options_intel"] = intel
            sig["options_score_delta"] = scored["delta"]
            sig["options_score_reasons"] = scored["reasons"]
            summary["evaluated"] += 1
            if mode == "live" and scored["delta"]:
                base_rank = _f(sig.get("rank_score"))
                if base_rank is not None:
                    sig["rank_score"] = round(base_rank + scored["delta"], 2)
                base_comp = _f(sig.get("composite_score"))
                if base_comp is not None:
                    sig["composite_score"] = round(base_comp + scored["delta"], 2)
                sig["options_score_applied"] = True
                summary["applied"] += 1
        except Exception as exc:
            summary["errors"] += 1
            LOG.debug("options scoring failed for %s: %s", ticker, exc)

    if mode == "live" and summary["applied"]:
        signals.sort(key=lambda s: s.get("rank_score") or 0.0, reverse=True)

    diag["options_scoring"] = summary
    return signals
