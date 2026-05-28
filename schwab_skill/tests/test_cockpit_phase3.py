"""Phase 3: execution policies, post-fill risk, lifecycle + slippage attribution."""

from __future__ import annotations

from core import cockpit_service, execution_policies, post_fill_risk
from core.providers import ExecutionProvider


# --------------------------------------------------------------------------- #
# Execution policies
# --------------------------------------------------------------------------- #
def test_policy_prefers_limit_when_liquid() -> None:
    d = execution_policies.decide(
        side="BUY",
        base_order_type="MARKET",
        spread_bps=4.0,
        liquid=True,
        preferred_limit_price=100.0,
    )
    assert d["recommended_order_type"] == "LIMIT"
    assert d["reprice_strategy"] == "aggressive"  # spread <= tight
    assert "prefer_limit_liquid" in d["reasons"]
    assert d["policy_id"] == "exec_policy_v1"


def test_policy_patient_reprice_on_wide_spread() -> None:
    d = execution_policies.decide(
        side="BUY", base_order_type="MARKET", spread_bps=40.0, liquid=True, preferred_limit_price=100.0
    )
    assert d["reprice_strategy"] == "patient"


def test_policy_market_stays_market_when_illiquid() -> None:
    d = execution_policies.decide(side="BUY", base_order_type="MARKET", liquid=False)
    assert d["recommended_order_type"] == "MARKET"
    assert d["reprice_strategy"] == "none"


def test_policy_throttles_on_degraded_data() -> None:
    d = execution_policies.decide(side="BUY", base_order_type="MARKET", data_quality="stale", is_risk_increasing=True)
    assert d["throttle"] is True
    assert any("throttle_data_quality" in r for r in d["reasons"])


def test_policy_no_throttle_for_risk_reducing() -> None:
    d = execution_policies.decide(side="SELL", base_order_type="MARKET", data_quality="stale", is_risk_increasing=False)
    assert d["throttle"] is False


# --------------------------------------------------------------------------- #
# Post-fill risk
# --------------------------------------------------------------------------- #
def _portfolio(top1=10.0, gross=50.0, positions=None):
    return {
        "concentration": {"top1_pct": top1},
        "exposure": {"gross_pct": gross},
        "positions": positions or [],
    }


def test_post_fill_clean() -> None:
    flags = post_fill_risk.assess(_portfolio())
    assert flags == []


def test_post_fill_concentration_and_exposure_breach() -> None:
    flags = post_fill_risk.assess(_portfolio(top1=40.0, gross=200.0))
    assert any(f.startswith("concentration_breach") for f in flags)
    assert any(f.startswith("exposure_breach") for f in flags)


def test_post_fill_stop_integrity() -> None:
    portfolio = _portfolio(positions=[{"ticker": "AAPL", "qty": 100}, {"ticker": "MSFT", "qty": 50}])
    has_stop = {"AAPL": True, "MSFT": False}.get
    flags = post_fill_risk.assess(portfolio, stop_lookup=lambda t: bool(has_stop(t)))
    assert "stop_missing:MSFT" in flags
    assert "stop_missing:AAPL" not in flags


def test_build_portfolio_applies_risk_flags() -> None:
    account = {
        "accounts": [
            {
                "securitiesAccount": {
                    "currentBalances": {"liquidationValue": 1000.0},
                    "positions": [{"instrument": {"symbol": "AAPL"}, "longQuantity": 100, "marketValue": 600.0}],
                }
            }
        ]
    }
    out = cockpit_service.build_portfolio(account, stop_lookup=lambda t: False)
    # 600/1000 = 60% top1 > 25% default -> concentration breach; AAPL has no stop
    assert any(f.startswith("concentration_breach") for f in out["risk_flags"])
    assert "stop_missing:AAPL" in out["risk_flags"]


# --------------------------------------------------------------------------- #
# Lifecycle + slippage attribution
# --------------------------------------------------------------------------- #
def test_execution_provider_maps_policy_and_reprice() -> None:
    state = ExecutionProvider.from_order_result(
        {
            "order_id": "Z1",
            "ticker": "NVDA",
            "status": "filled",
            "fill_price": 120.5,
            "_execution_quality": {
                "expected_slippage_bps": 12.0,
                "spread_bps": 6.0,
                "reprice_attempts": 3,
                "policy": {"policy_id": "exec_policy_v1"},
            },
        }
    )
    assert state.intent.policy_id == "exec_policy_v1"
    assert state.quality.reprice_count == 3
    assert state.quality.realized_slippage_bps == 12.0  # falls back to expected when no realized


def test_execution_provider_preserves_realized_zero_slippage() -> None:
    # Realized 0.0 (perfect fill) must be kept, not replaced by the expected estimate.
    state = ExecutionProvider.from_order_result(
        {
            "ticker": "AAPL",
            "status": "filled",
            "_execution_quality": {"realized_slippage_bps": 0.0, "expected_slippage_bps": 18.0},
        }
    )
    assert state.quality.realized_slippage_bps == 0.0


def test_execution_provider_falls_back_to_expected_when_realized_missing() -> None:
    state = ExecutionProvider.from_order_result(
        {"ticker": "AAPL", "status": "filled", "_execution_quality": {"expected_slippage_bps": 18.0}}
    )
    assert state.quality.realized_slippage_bps == 18.0


def test_build_execution_quality_attribution() -> None:
    blotter = [
        {
            "state": "filled",
            "quality": {"realized_slippage_bps": 10.0, "spread_bps_at_submit": 5.0, "reprice_count": 1},
        },
        {
            "state": "filled",
            "quality": {"realized_slippage_bps": 20.0, "spread_bps_at_submit": 7.0, "reprice_count": 3},
        },
        {"state": "rejected", "quality": {}},
    ]
    summary = {"events": {"exec_quality_evaluated": 5, "exec_quality_live_blocked": 1}}
    out = cockpit_service.build_execution_quality(summary, blotter)
    assert out["lifecycle_counts"]["filled"] == 2
    assert out["lifecycle_counts"]["rejected"] == 1
    assert out["slippage"]["avg_realized_bps"] == 15.0
    assert out["slippage"]["max_realized_bps"] == 20.0
    assert out["policy_events"]["evaluated"] == 5
    assert out["policy_events"]["live_blocked"] == 1
