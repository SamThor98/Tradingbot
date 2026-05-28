"""SymbolIntelProvider — scanner signal dict -> SymbolDecisionCard.

``normalize_signal`` is a pure transform of the existing scanner signal dict
(see ``signal_scanner._apply_score_stack`` output) so it is fully testable
without network access. ``build_card`` layers the ``/api/decision-card``
trade-plan synthesis on top when available.
"""

from __future__ import annotations

from typing import Any

from core.contracts.provenance import Provenance
from core.contracts.symbol import (
    GATE_DISPOSITIONS,
    ConfidenceInfo,
    GateStatus,
    QualityFlags,
    RankScores,
    SetupInfo,
    SymbolDecisionCard,
    TradePlan,
)


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    f = _f(value)
    return int(f) if f is not None else None


class SymbolIntelProvider:
    domain = "symbol"

    @staticmethod
    def normalize_signal(signal: dict[str, Any]) -> SymbolDecisionCard:
        """Pure transform: scanner signal dict -> typed decision card."""
        sig = signal or {}
        components = sig.get("score_components") or {}
        advisory = sig.get("advisory") or {}
        attribution = sig.get("strategy_attribution") or {}

        rank = RankScores(
            rank_score=_f(sig.get("rank_score")),
            composite_score=_f(sig.get("composite_score")),
            signal_score=_f(sig.get("signal_score")),
            edge_score=_f(sig.get("edge_score")),
            reliability_score=_f(sig.get("reliability_score")),
            execution_score=_f(sig.get("execution_score")),
            p_up_calibrated=_f(sig.get("p_up_calibrated")),
            ev_10d=_f(sig.get("ev_10d")),
            rank_basis=sig.get("rank_basis"),
        )

        setup = SetupInfo(
            stage2=components.get("stage2") if isinstance(components, dict) else None,
            vcp=sig.get("vcp"),
            breakout_confirmed=bool(sig.get("breakout_confirmed")),
            sector_etf=sig.get("sector_etf"),
            strategy_top_live=attribution.get("top_live") if isinstance(attribution, dict) else None,
            sma_50=_f(sig.get("sma_50")),
            sma_200=_f(sig.get("sma_200")),
        )

        confidence = ConfidenceInfo(
            bucket=(advisory.get("confidence_bucket") if isinstance(advisory, dict) else None),
            mirofish_conviction=_f(sig.get("mirofish_conviction")),
            expected_move_10d=_f(advisory.get("expected_move_10d") if isinstance(advisory, dict) else None),
        )

        quality = QualityFlags(
            sec_risk_tag=sig.get("sec_risk_tag"),
            sec_risk_reasons=list(sig.get("sec_risk_reasons") or []),
            forensic_flags=list(sig.get("forensic_flags") or []),
            pead_beat=sig.get("pead_beat"),
            pead_surprise_pct=_f(sig.get("pead_surprise_pct")),
            guidance_signal=sig.get("guidance_signal"),
        )

        disposition = str(sig.get("_filter_status") or "unknown")
        if disposition not in GATE_DISPOSITIONS:
            disposition = "unknown"
        gate = GateStatus(
            disposition=disposition,  # type: ignore[arg-type]
            reasons=list(sig.get("_filter_reasons") or []),
        )

        prov = Provenance.from_lineage(
            {
                "provider": sig.get("data_provider"),
                "used_fallback_data": sig.get("used_fallback_data"),
                "fallback_reason": sig.get("fallback_reason"),
                "data_quality": sig.get("_data_quality"),
            }
        )

        return SymbolDecisionCard(
            ticker=str(sig.get("ticker") or "").upper(),
            price=_f(sig.get("price")),
            rank=rank,
            setup=setup,
            confidence=confidence,
            quality_flags=quality,
            gate_status=gate,
            key_reasons=list(sig.get("reliability_reasons") or [])[:6],
            provenance=prov,
        )

    @staticmethod
    def apply_decision_card(card: SymbolDecisionCard, decision_card: dict[str, Any]) -> SymbolDecisionCard:
        """Merge the ``/api/decision-card/{ticker}`` payload (trade plan, reasons)."""
        dc = decision_card or {}
        size = dc.get("size") or {}
        raw_zone = dc.get("entry_zone")
        entry_zone: list[float] | None
        if isinstance(raw_zone, dict):
            lo, hi = _f(raw_zone.get("low")), _f(raw_zone.get("high"))
            entry_zone = [lo, hi] if (lo is not None or hi is not None) else None
        elif isinstance(raw_zone, (list, tuple)):
            entry_zone = [v for v in (_f(x) for x in raw_zone) if v is not None] or None
        else:
            entry_zone = None
        card.trade_plan = TradePlan(
            entry_zone=entry_zone,
            stop_invalidation=_f(dc.get("stop_invalidation")),
            size_qty=_i(size.get("qty")),
            size_usd=_f(size.get("usd")),
        )
        if dc.get("key_reasons"):
            card.key_reasons = list(dc.get("key_reasons"))[:6]
        card.block_reason = dc.get("block_reason")
        return card

    @classmethod
    def normalize_many(cls, signals: list[dict[str, Any]] | None) -> list[SymbolDecisionCard]:
        return [cls.normalize_signal(s) for s in (signals or []) if isinstance(s, dict)]
