"""Phase 3 post-fill risk controls.

Computes ``risk_flags`` for a :class:`PortfolioRiskState`:
- **stop integrity** — every open long should have a registered stop (exit manager)
- **concentration drift** — single-position weight exceeds the cap
- **exposure drift** — gross exposure exceeds the cap

Pure assessment over an already-built portfolio dict plus an optional
``stop_lookup`` (ticker -> bool: does a stop exist). Offline-testable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _thresholds(skill_dir: Path | None) -> tuple[float, float]:
    try:
        from config import get_risk_max_concentration_pct, get_risk_max_gross_exposure_pct

        return get_risk_max_concentration_pct(skill_dir), get_risk_max_gross_exposure_pct(skill_dir)
    except Exception:
        return 25.0, 150.0


def assess(
    portfolio: dict[str, Any] | None,
    *,
    stop_lookup: Callable[[str], bool] | None = None,
    skill_dir: Path | None = None,
) -> list[str]:
    """Return a list of risk-flag strings for the given portfolio state dict."""
    state = portfolio or {}
    max_conc, max_gross = _thresholds(skill_dir)
    flags: list[str] = []

    # Stop integrity: open longs without a registered stop.
    if stop_lookup is not None:
        for pos in state.get("positions") or []:
            if not isinstance(pos, dict):
                continue
            qty = _f(pos.get("qty")) or 0.0
            ticker = str(pos.get("ticker") or "").upper()
            if qty > 0 and ticker:
                try:
                    has_stop = bool(stop_lookup(ticker))
                except Exception:
                    has_stop = True  # fail open: don't false-alarm on lookup error
                if not has_stop:
                    flags.append(f"stop_missing:{ticker}")

    # Concentration drift.
    conc = state.get("concentration") or {}
    top1 = _f(conc.get("top1_pct"))
    if top1 is not None and top1 > max_conc:
        flags.append(f"concentration_breach:top1={round(top1, 1)}%>{round(max_conc, 1)}%")

    # Exposure drift.
    exposure = state.get("exposure") or {}
    gross = _f(exposure.get("gross_pct"))
    if gross is not None and gross > max_gross:
        flags.append(f"exposure_breach:gross={round(gross, 1)}%>{round(max_gross, 1)}%")

    return flags
