"""Release gate: cockpit contracts, providers, and endpoint catalog integrity.

Validates that the Phase 0 scaffolding is importable and internally consistent:
- every contract round-trips through pydantic
- each provider produces its DTO from a representative raw payload
- the endpoint catalog has unique keys and a documented status/phase per row
- the frozen observability metric-name constants are present

Exit code 0 = pass, 1 = fail. Safe to run with no Schwab credentials.

Run:
    cd schwab_skill && python scripts/validate_contracts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from repo root or schwab_skill/.
_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent
if str(_SKILL) not in sys.path:
    sys.path.insert(0, str(_SKILL))

FAILURES: list[str] = []


def _check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [ok] {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"  [FAIL] {name} -- {detail}")


def validate_contracts() -> None:
    print("contracts:")
    from core.contracts import (
        DecisionPacket,
        ExecutionState,
        MarketSnapshot,
        PortfolioRiskState,
        Provenance,
        SymbolDecisionCard,
    )

    for model, kwargs in (
        (Provenance, {}),
        (MarketSnapshot, {}),
        (SymbolDecisionCard, {"ticker": "AAPL"}),
        (ExecutionState, {"ticker": "AAPL"}),
        (PortfolioRiskState, {}),
        (DecisionPacket, {"packet_id": "abc", "ticker": "AAPL"}),
    ):
        try:
            obj = model(**kwargs)
            restored = model.model_validate(obj.model_dump())
            _check(f"{model.__name__} round-trips", restored.model_dump() == obj.model_dump())
        except Exception as exc:  # noqa: BLE001
            _check(f"{model.__name__} round-trips", False, str(exc))


def validate_providers() -> None:
    print("providers:")
    from core.providers import (
        ExecutionProvider,
        MarketContextProvider,
        PortfolioProvider,
        SymbolIntelProvider,
    )

    card = SymbolIntelProvider.normalize_signal(
        {"ticker": "aapl", "price": 100.0, "rank_score": 80.0, "_filter_status": "kept"}
    )
    _check("SymbolIntelProvider builds card", card.ticker == "AAPL" and card.rank.rank_score == 80.0)

    state = PortfolioProvider.normalize_account(
        {"accounts": [{"securitiesAccount": {"currentBalances": {"liquidationValue": 1000.0}, "positions": []}}]}
    )
    _check("PortfolioProvider builds state", state.equity == 1000.0)

    snap = MarketContextProvider.normalize(regime_ctx={"bullish": True}, regime_v2={"score": 70, "bucket": "high"})
    _check("MarketContextProvider builds snapshot", snap.regime_state == "bullish")

    es = ExecutionProvider.from_order_result({"ticker": "X", "status": "filled", "fill_price": 1.0})
    _check("ExecutionProvider builds state", es.state == "filled" and es.is_terminal)


def validate_catalog() -> None:
    print("endpoint catalog:")
    from core import endpoint_catalog as cat

    endpoints = cat.all_endpoints()
    keys = [e.key for e in endpoints]
    _check("catalog non-empty", len(endpoints) > 0)
    _check("catalog keys unique", len(keys) == len(set(keys)), f"{len(keys)} keys, {len(set(keys))} unique")
    bad_status = [e.key for e in endpoints if e.status not in {"live", "gap"}]
    _check("catalog statuses valid", not bad_status, str(bad_status))
    missing_phase = [e.key for e in endpoints if not e.phase]
    _check("catalog phases present", not missing_phase, str(missing_phase))
    _check("known live endpoint present", "marketdata.quotes" in cat.live_keys())


def validate_observability() -> None:
    print("observability:")
    from core import observability as obs

    for const in (
        "M_REQUEST_LATENCY_MS",
        "M_REQUEST_ERRORS",
        "M_DATA_FALLBACK",
        "M_DATA_STALE_RATIO",
        "M_PROVIDER_CONFIDENCE",
        "M_CIRCUIT_BREAKER_STATE",
    ):
        _check(f"metric constant {const}", hasattr(obs, const))


def main() -> int:
    print("== validate_contracts (cockpit Phase 0) ==")
    validate_contracts()
    validate_providers()
    validate_catalog()
    validate_observability()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s)")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("PASSED: all cockpit Phase 0 checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
