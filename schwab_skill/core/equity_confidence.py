"""File confidence beside the extension Rank.

Rank stays inverted 52-week extension. This module does not add a second
alpha weight. It reads the rest of the technical and fundamental file and
reports how completely that file was measured.

Each factor has a frozen role from the published books:

- ``moves_rank`` — inverted 52-week extension. The only point in the Rank.
- ``same_story`` — chart readings that describe the same stretch. Listed,
  not added again.
- ``no_signal`` — measured factors whose sign flips or is noise. Weight zero.
- ``eligible`` — stable enough to test later (free cash flow, PEAD surprise).
  Not in the Rank until a residual walk-forward promotes them.
- ``cap`` — reliability, execution, forensic, SEC. They can lower the Rank.
  They do not add points.

Confidence is ``coverage × agreement × freshness``, on 0–100.

Coverage is the share of the registry that was present on the row. Missing
fundamentals stay missing and lower coverage.

Agreement votes are the Rank itself, the 200-day stretch reading when it is
present (one vote for the whole same-story group), and the cap group when any
cap input was present. A cap that fires, or a 200-day reading that disagrees
with extension, lowers agreement. Factors with no signal do not vote.

Freshness is 1 when extension could be scored and no filing age is attached.
A filing age past 120 days fades, and past 400 days (the point-in-time
cutoff) freshness is 0.
"""

from __future__ import annotations

from typing import Any

from core.trust_score import trust_cap_reasons

REGISTRY_ID = "equity_ledger_v1"
STRETCH_PTS = 20.0
STRETCH_ABOVE_SMA200 = 0.20
FRESH_FILING_DAYS = 120.0
STALE_FILING_DAYS = 400.0

_ROLE_LABELS = (
    ("moves_rank", "moves rank"),
    ("same_story", "same story"),
    ("no_signal", "no signal"),
    ("eligible", "eligible"),
    ("cap", "cap inputs"),
)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _components(signal_row: dict[str, Any]) -> dict[str, Any]:
    comps = signal_row.get("score_components")
    return comps if isinstance(comps, dict) else {}


def _has(signal_row: dict[str, Any], *keys: str) -> bool:
    comps = _components(signal_row)
    for key in keys:
        if key in signal_row and signal_row.get(key) is not None:
            return True
        if key in comps and comps.get(key) is not None:
            return True
    return False


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _first_float(signal_row: dict[str, Any], *keys: str) -> float | None:
    comps = _components(signal_row)
    for key in keys:
        if key in signal_row:
            number = _optional_float(signal_row.get(key))
            if number is not None:
                return number
        if key in comps:
            number = _optional_float(comps.get(key))
            if number is not None:
                return number
    return None


def _stretch_pct(signal_row: dict[str, Any]) -> float | None:
    direct = _first_float(signal_row, "close_vs_sma200_pct")
    if direct is not None:
        return direct
    price = _first_float(signal_row, "price", "current_price")
    sma = _first_float(signal_row, "sma_200")
    if price is None or sma is None or sma <= 0:
        return None
    return (price / sma) - 1.0


def _sec_observed(signal_row: dict[str, Any]) -> bool:
    tag = str(signal_row.get("sec_risk_tag") or "").lower()
    return tag in {"low", "medium", "high"}


def _forensic_observed(signal_row: dict[str, Any]) -> bool:
    if signal_row.get("forensic_observed") is True:
        return True
    if _sec_observed(signal_row):
        return True
    flags = signal_row.get("forensic_flags")
    return isinstance(flags, list)


def _observed_map(signal_row: dict[str, Any], *, pts_52w: float) -> dict[str, bool]:
    del pts_52w  # presence of the caller argument is the extension observation
    return {
        "pts_52w": True,
        "chart_stretch": _stretch_pct(signal_row) is not None,
        "chart_strength": _has(
            signal_row,
            "trend_stack_ok",
            "sma200_rising",
            "trend_strength_score",
            "weinstein_score",
            "sma_20_slope",
            "sma_200_slope",
        ),
        "volume": _has(signal_row, "pts_volume", "pts_volume_rank", "volume_ratio"),
        "reliability": _has(signal_row, "reliability_score"),
        "execution": _has(signal_row, "execution_score"),
        "forensic": _forensic_observed(signal_row),
        "free_cash_flow": _has(signal_row, "fund_fcf", "fund_fcf_yield", "fund_fcfps"),
        "classic_fundamentals": _has(
            signal_row,
            "fund_roe",
            "fund_pe",
            "fund_debt",
            "fund_netmargin",
            "fund_pb",
            "roe",
            "pe_ratio",
        ),
        "insider": _has(signal_row, "insider_net_shares", "insider_buy_count", "fund_insider_net"),
        "earnings_surprise": _has(signal_row, "pead_surprise_pct"),
    }


def _ledger(observed: dict[str, bool]) -> list[dict[str, Any]]:
    catalog = (
        ("pts_52w", "moves_rank"),
        ("chart_stretch", "same_story"),
        ("chart_strength", "same_story"),
        ("volume", "no_signal"),
        ("reliability", "cap"),
        ("execution", "cap"),
        ("forensic", "cap"),
        ("free_cash_flow", "eligible"),
        ("classic_fundamentals", "no_signal"),
        ("insider", "no_signal"),
        ("earnings_surprise", "eligible"),
    )
    return [
        {"id": factor_id, "role": role, "observed": bool(observed[factor_id])}
        for factor_id, role in catalog
    ]


def _summary(ledger: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for role, label in _ROLE_LABELS:
        group = [item for item in ledger if item["role"] == role]
        seen = sum(1 for item in group if item["observed"])
        parts.append(f"{label} {seen}/{len(group)}")
    return " · ".join(parts)


def _agreement(signal_row: dict[str, Any], *, pts_52w: float, observed: dict[str, bool]) -> float:
    votes: list[bool] = [True]
    stretch = _stretch_pct(signal_row)
    if stretch is not None:
        extension_stretched = float(pts_52w) >= STRETCH_PTS
        sma_stretched = float(stretch) >= STRETCH_ABOVE_SMA200
        votes.append(extension_stretched == sma_stretched)
    caps_seen = observed["reliability"] or observed["execution"] or observed["forensic"]
    if caps_seen:
        votes.append(not trust_cap_reasons(signal_row))
    return _clamp(sum(1.0 for vote in votes if vote) / float(len(votes)))


def _freshness(signal_row: dict[str, Any]) -> float:
    age = _first_float(signal_row, "filing_recency_days")
    if age is None:
        return 1.0
    if age <= FRESH_FILING_DAYS:
        return 1.0
    if age >= STALE_FILING_DAYS:
        return 0.0
    span = STALE_FILING_DAYS - FRESH_FILING_DAYS
    return _clamp(1.0 - ((age - FRESH_FILING_DAYS) / span))


def equity_confidence_block(signal_row: dict[str, Any], *, pts_52w: float) -> dict[str, Any]:
    """Stamp file confidence for a row that already has extension points."""
    observed = _observed_map(signal_row, pts_52w=pts_52w)
    ledger = _ledger(observed)
    coverage = _clamp(sum(1.0 for item in ledger if item["observed"]) / float(len(ledger)))
    agreement = _agreement(signal_row, pts_52w=pts_52w, observed=observed)
    freshness = _freshness(signal_row)
    score = round(100.0 * coverage * agreement * freshness)
    return {
        "equity_confidence": int(max(0, min(100, score))),
        "equity_confidence_coverage": round(coverage, 4),
        "equity_confidence_agreement": round(agreement, 4),
        "equity_confidence_freshness": round(freshness, 4),
        "equity_confidence_registry": REGISTRY_ID,
        "equity_confidence_summary": _summary(ledger),
        "factor_ledger": ledger,
    }
