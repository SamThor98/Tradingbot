"""MarketContextProvider — regime + sector breadth -> MarketSnapshot."""

from __future__ import annotations

from typing import Any

from core.contracts.market import (
    MarketSnapshot,
    Movers,
    RegimeState,
    SectorStrength,
    VolatilityState,
)
from core.contracts.provenance import Provenance, utc_now


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _volatility_bucket(vix: float | None) -> VolatilityState:
    if vix is None:
        return "normal"
    if vix < 15:
        return "low"
    if vix < 22:
        return "normal"
    if vix < 32:
        return "elevated"
    return "extreme"


class MarketContextProvider:
    domain = "market"

    @staticmethod
    def normalize(
        *,
        regime_ctx: dict[str, Any] | None = None,
        regime_v2: dict[str, Any] | None = None,
        winning_sectors: list[dict[str, Any]] | None = None,
        vix_level: float | None = None,
        movers: dict[str, list[str]] | None = None,
        scan_blocked_by_regime: bool = False,
    ) -> MarketSnapshot:
        """Pure transform of the pieces the scanner already computes."""
        ctx = regime_ctx or {}
        v2 = regime_v2 or {}

        is_bullish = bool(ctx.get("bullish"))
        score = _f(v2.get("score"))
        bucket = v2.get("bucket")

        # Derive a tri-state regime label.
        state: RegimeState
        if not is_bullish:
            state = "bearish"
        elif bucket == "high" or (score is not None and score >= 66):
            state = "bullish"
        elif bucket == "low" or (score is not None and score < 40):
            state = "neutral"
        else:
            state = "bullish" if is_bullish else "neutral"

        breadth: list[SectorStrength] = []
        for i, row in enumerate(winning_sectors or []):
            if isinstance(row, dict):
                breadth.append(
                    SectorStrength(
                        etf=str(row.get("etf") or row.get("symbol") or ""),
                        name=row.get("name"),
                        rel_strength_pct=_f(row.get("rel_strength_pct") or row.get("rel_strength")),
                        is_winning=bool(row.get("is_winning", True)),
                        rank=i + 1,
                    )
                )
            elif isinstance(row, str):
                breadth.append(SectorStrength(etf=row, is_winning=True, rank=i + 1))

        mv = movers or {}
        return MarketSnapshot(
            regime_state=state,
            regime_score=score,
            regime_bucket=bucket,
            spy_price=_f(ctx.get("price") or ctx.get("spy_price")),
            spy_sma_200=_f(ctx.get("sma_200") or ctx.get("spy_sma_200")),
            is_regime_bullish=is_bullish,
            scan_blocked_by_regime=bool(scan_blocked_by_regime),
            sector_breadth=breadth,
            volatility_state=_volatility_bucket(vix_level),
            vix_level=vix_level,
            movers=Movers(
                gainers=list(mv.get("gainers") or []),
                losers=list(mv.get("losers") or []),
                most_active=list(mv.get("most_active") or []),
            ),
            provenance=Provenance(source="schwab", as_of=utc_now(), confidence="high"),
        )

    @staticmethod
    def normalize_movers(movers_json: dict[str, Any] | None) -> dict[str, list[str]]:
        """Parse Schwab /movers screener JSON into gainers/losers/most_active.

        Tolerant of both ``{"screeners": [...]}`` and a bare list payload.
        """
        data = movers_json or {}
        rows = data.get("screeners") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            rows = []

        scored: list[tuple[float, float, str]] = []  # (pct_change, volume, symbol)
        for r in rows:
            if not isinstance(r, dict):
                continue
            sym = str(r.get("symbol") or r.get("ticker") or "").upper()
            if not sym:
                continue
            pct = _f(r.get("netPercentChange") or r.get("netPercentChangeInDouble") or r.get("changePct")) or 0.0
            vol = _f(r.get("volume") or r.get("totalVolume")) or 0.0
            scored.append((pct, vol, sym))

        by_change_desc = sorted(scored, key=lambda x: x[0], reverse=True)
        by_change_asc = sorted(scored, key=lambda x: x[0])
        by_volume_desc = sorted(scored, key=lambda x: x[1], reverse=True)
        gainers = [sym for pct, _vol, sym in by_change_desc if pct > 0][:10]
        losers = [sym for pct, _vol, sym in by_change_asc if pct < 0][:10]
        most_active = [sym for _pct, vol, sym in by_volume_desc if vol > 0][:10]
        return {"gainers": gainers, "losers": losers, "most_active": most_active}

    @staticmethod
    def from_diagnostics(diagnostics: dict[str, Any] | None) -> MarketSnapshot:
        """Build a snapshot from a scan ``diagnostics`` dict (no extra fetch)."""
        diag = diagnostics or {}
        regime_ctx = {
            "bullish": diag.get("regime_bullish"),
            "price": diag.get("spy_price"),
            "sma_200": diag.get("spy_sma_200"),
        }
        return MarketContextProvider.normalize(
            regime_ctx=regime_ctx,
            regime_v2=diag.get("regime_v2") if isinstance(diag.get("regime_v2"), dict) else None,
            winning_sectors=diag.get("winning_sectors"),
            scan_blocked_by_regime=bool(diag.get("scan_blocked")),
        )
