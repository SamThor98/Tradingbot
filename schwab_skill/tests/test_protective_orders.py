"""Entry brackets, stop replacement, and unknown-order locks."""

from __future__ import annotations

from pathlib import Path

import pytest

from config import clear_env_cache, get_entry_protection_mode
from core.order_ambiguity import (
    AMBIGUOUS_ORDER_ID,
    begin_intent,
    finish_known,
    get_lock,
    mark_unknown,
)
from core.protective_orders import (
    annotate_portfolio_protection,
    build_entry_bracket,
    child_stop_order_id,
    describe_protection,
    shift_trigger_limit,
    trailing_stop_order,
)
from execution import (
    _hard_stop_payload,
    _install_protective_stop,
    _prepare_entry_protection,
    resolve_order_ambiguity,
)


def test_entry_protection_mode_defaults_to_bracket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENTRY_PROTECTION_MODE", raising=False)
    clear_env_cache()
    assert get_entry_protection_mode(tmp_path) == "bracket"


def test_trailing_stop_uses_schwab_link_fields() -> None:
    payload = trailing_stop_order("aapl", 4, offset=1.5, link_type="VALUE", basis="LAST")
    assert payload["orderType"] == "TRAILING_STOP"
    assert payload["stopPriceLinkBasis"] == "LAST"
    assert payload["stopPriceLinkType"] == "VALUE"
    assert payload["stopPriceOffset"] == 1.5
    assert "stopPriceBasis" not in payload
    assert "stopPriceType" not in payload


def test_build_entry_bracket_keeps_the_stop_on_the_parent() -> None:
    entry = {
        "orderStrategyType": "SINGLE",
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "DAY",
        "price": 100.0,
        "orderLegCollection": [
            {"instruction": "BUY", "quantity": 10, "instrument": {"symbol": "AAPL", "assetType": "EQUITY"}}
        ],
    }
    stop = _hard_stop_payload("AAPL", 10, stop_price=93.0)
    bracket = build_entry_bracket(entry, stop)
    assert entry["orderStrategyType"] == "SINGLE"
    assert bracket["orderStrategyType"] == "TRIGGER"
    assert bracket["price"] == 100.0
    child = bracket["childOrderStrategies"][0]
    assert child["orderType"] == "STOP"
    assert child["stopPrice"] == 93.0
    assert child["orderLegCollection"][0]["instruction"] == "SELL"
    assert child["orderLegCollection"][0]["quantity"] == 10


def test_shift_trigger_limit_keeps_stop_distance() -> None:
    bracket = build_entry_bracket(
        {
            "orderStrategyType": "SINGLE",
            "orderType": "LIMIT",
            "price": 100.0,
            "orderLegCollection": [
                {"instruction": "BUY", "quantity": 2, "instrument": {"symbol": "MSFT", "assetType": "EQUITY"}}
            ],
        },
        _hard_stop_payload("MSFT", 2, stop_price=90.0),
    )
    shifted = shift_trigger_limit(bracket, 102.0)
    assert shifted is not None
    assert shifted["price"] == 102.0
    assert shifted["childOrderStrategies"][0]["stopPrice"] == 92.0
    assert bracket["price"] == 100.0
    tight = build_entry_bracket(
        {
            "orderStrategyType": "SINGLE",
            "orderType": "LIMIT",
            "price": 100.0,
            "orderLegCollection": [
                {"instruction": "BUY", "quantity": 2, "instrument": {"symbol": "MSFT", "assetType": "EQUITY"}}
            ],
        },
        _hard_stop_payload("MSFT", 2, stop_price=99.99),
    )
    assert shift_trigger_limit(tight, 101.0) is None


def test_child_stop_order_id_reads_the_nested_stop() -> None:
    tree = {
        "orderId": "PARENT",
        "orderStrategyType": "TRIGGER",
        "childOrderStrategies": [
            {
                "orderStrategyType": "SINGLE",
                "orderType": "STOP",
                "orderId": 445566,
                "status": "AWAITING_PARENT_ORDER",
            }
        ],
    }
    assert child_stop_order_id(tree) == "445566"


def test_prepare_entry_refuses_a_buy_without_a_price(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENTRY_PROTECTION_MODE", raising=False)
    clear_env_cache()
    monkeypatch.setattr("execution._get_quote_quality_snapshot", lambda *_args, **_kwargs: {})
    entry = {
        "orderStrategyType": "SINGLE",
        "orderType": "MARKET",
        "orderLegCollection": [
            {"instruction": "BUY", "quantity": 1, "instrument": {"symbol": "AAPL", "assetType": "EQUITY"}}
        ],
    }
    prepared = _prepare_entry_protection(
        entry,
        ticker="AAPL",
        qty=1,
        order_type="MARKET",
        limit_price=None,
        price_hint=None,
        auth=None,  # type: ignore[arg-type]
        skill_dir=tmp_path,
    )
    assert isinstance(prepared, str)
    assert "No price to attach a protective stop" in prepared


def test_prepare_entry_wraps_a_limit_buy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENTRY_PROTECTION_MODE", raising=False)
    monkeypatch.setenv("EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL", "15")
    clear_env_cache()
    monkeypatch.setattr("execution._get_quote_quality_snapshot", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("execution._compute_adaptive_stop_pct", lambda *_args, **_kwargs: 0.07)
    entry = {
        "orderStrategyType": "SINGLE",
        "orderType": "LIMIT",
        "price": 50.0,
        "orderLegCollection": [
            {"instruction": "BUY", "quantity": 4, "instrument": {"symbol": "AAPL", "assetType": "EQUITY"}}
        ],
    }
    prepared = _prepare_entry_protection(
        entry,
        ticker="AAPL",
        qty=4,
        order_type="LIMIT",
        limit_price=50.0,
        price_hint=None,
        auth=None,  # type: ignore[arg-type]
        skill_dir=tmp_path,
    )
    assert isinstance(prepared, tuple)
    bracket, info = prepared
    assert info["mode"] == "bracket"
    assert info["stop_grace_active"] is True
    assert bracket["orderStrategyType"] == "TRIGGER"
    assert bracket["childOrderStrategies"][0]["orderType"] == "STOP"
    assert float(bracket["childOrderStrategies"][0]["stopPrice"]) < 50.0


def test_unknown_order_locks_until_absent(tmp_path: Path) -> None:
    from core.order_ambiguity import AmbiguityLocked

    intent = begin_intent(tmp_path, "aapl", "entry_bracket")
    mark_unknown(tmp_path, "AAPL", intent, "entry_bracket: timed out")
    locked = get_lock(tmp_path, "AAPL")
    assert locked is not None
    assert locked["status"] == "unknown"
    with pytest.raises(AmbiguityLocked):
        begin_intent(tmp_path, "AAPL", "entry_bracket")
    resolved = resolve_order_ambiguity(tmp_path, "AAPL", "absent")
    assert resolved["ok"] is True
    assert get_lock(tmp_path, "AAPL") is None


def test_known_refusal_clears_the_lock(tmp_path: Path) -> None:
    intent = begin_intent(tmp_path, "MSFT", "entry")
    finish_known(tmp_path, "MSFT", intent)
    assert get_lock(tmp_path, "MSFT") is None


def test_stop_replace_does_not_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from execution import _BrokerSubmit

    seen: dict[str, object] = {}

    def _fake_submit(**kwargs):
        seen.update(kwargs)
        return _BrokerSubmit(None, None, False, "NEWSTOP")

    monkeypatch.setattr("execution._submit_broker_order", _fake_submit)
    cancelled: list[str] = []
    monkeypatch.setattr(
        "execution._cancel_order_with_refresh",
        lambda *args, **kwargs: cancelled.append("cancel"),
    )
    posted: list[str] = []
    monkeypatch.setattr(
        "execution._post_order_with_refresh",
        lambda *args, **kwargs: posted.append("post"),
    )
    result = _install_protective_stop(
        orders_url="https://api.example/orders",
        auth=None,  # type: ignore[arg-type]
        skill_dir=tmp_path,
        ticker="AAPL",
        pos={"stop_order_id": "OLDSTOP", "stop_attached": True},
        payload={"orderType": "STOP"},
        action="breakeven_stop",
        position_key="AAPL:ENTRY",
    )
    assert result.order_id == "NEWSTOP"
    assert seen["method"] == "put"
    assert str(seen["url"]).endswith("/OLDSTOP")
    assert cancelled == []
    assert posted == []


def test_ambiguous_stop_id_does_not_send_a_second_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr("execution._submit_broker_order", lambda **kwargs: called.append(kwargs["method"]))
    result = _install_protective_stop(
        orders_url="https://api.example/orders",
        auth=None,  # type: ignore[arg-type]
        skill_dir=tmp_path,
        ticker="AAPL",
        pos={"stop_order_id": AMBIGUOUS_ORDER_ID},
        payload={"orderType": "TRAILING_STOP"},
        action="trailing_stop",
        position_key="AAPL:ENTRY",
    )
    assert result.error
    assert called == []


def test_landed_trailing_stop_does_not_retry(tmp_path: Path) -> None:
    from execution_persistence import _load_exit_manager_state, _save_exit_manager_state

    _save_exit_manager_state(
        tmp_path,
        {
            "positions": {
                "AAPL:E1": {
                    "ticker": "AAPL",
                    "status": "active",
                    "stop_order_id": "OLD",
                    "stop_grace_active": True,
                    "trailing_stop_armed": False,
                }
            }
        },
    )
    intent = begin_intent(tmp_path, "AAPL", "trailing_stop", position_key="AAPL:E1")
    mark_unknown(tmp_path, "AAPL", intent, "trailing_stop: timed out")
    resolved = resolve_order_ambiguity(tmp_path, "AAPL", "landed")
    assert resolved["ok"] is True
    pos = _load_exit_manager_state(tmp_path)["positions"]["AAPL:E1"]
    assert pos["trailing_stop_armed"] is True
    assert pos["stop_order_id"] == AMBIGUOUS_ORDER_ID
    described = describe_protection(
        qty=10,
        asset_type="EQUITY",
        managed=pos,
        lock=None,
        ambiguous_order_id=AMBIGUOUS_ORDER_ID,
    )
    assert described["status"] == "FULL"


def test_shadow_buy_previews_the_bracket_and_does_not_send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from execution import place_order

    for key, value in {
        "EXECUTION_SHADOW_MODE": "true",
        "EVENT_RISK_MODE": "off",
        "EXEC_QUALITY_MODE": "off",
        "REGIME_V2_MODE": "off",
        "EXIT_MANAGER_MODE": "off",
        "SECTOR_FILTER_ENABLED": "false",
        "ENTRY_PROTECTION_MODE": "bracket",
        "EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL": "15",
    }.items():
        monkeypatch.setenv(key, value)
    clear_env_cache()
    monkeypatch.setattr("execution._get_account_hash_for_orders", lambda *args, **kwargs: "HASH")
    monkeypatch.setattr("execution.GuardrailWrapper._check_guardrails", lambda *args, **kwargs: None)
    monkeypatch.setattr("execution._get_quote_quality_snapshot", lambda *args, **kwargs: {"ask": 80.0, "bid": 79.5, "last": 80.0})
    monkeypatch.setattr("execution._compute_adaptive_stop_pct", lambda *args, **kwargs: 0.08)
    monkeypatch.setattr("execution.send_alert", lambda *args, **kwargs: True)
    monkeypatch.setattr("sector_strength.is_market_regime_bullish", lambda *args, **kwargs: (True, {}))
    monkeypatch.setattr("data_health.assess_symbol_data_health", lambda *args, **kwargs: {"data_quality": "ok"})

    def _boom(*args, **kwargs):
        raise AssertionError("shadow mode sent an order")

    monkeypatch.setattr("execution._post_order_with_refresh", _boom)

    class _Auth:
        def get_account_token(self) -> str:
            return "token"

        class account_session:
            @staticmethod
            def force_refresh() -> bool:
                return False

    result = place_order(
        "AAPL",
        3,
        "BUY",
        "LIMIT",
        limit_price=80.0,
        auth=_Auth(),
        skill_dir=tmp_path,
    )
    assert isinstance(result, dict)
    assert result["shadow_mode"] is True
    preview = result["order_payload_preview"]
    assert preview["orderStrategyType"] == "TRIGGER"
    assert preview["childOrderStrategies"][0]["orderType"] == "STOP"
    assert result["_stop_protection"]["status"] == "shadow_bracket"
    assert get_lock(tmp_path, "AAPL") is None


def test_portfolio_row_shows_no_stop_when_unregistered(tmp_path: Path) -> None:
    summary = annotate_portfolio_protection(
        {
            "positions": [
                {"symbol": "AAPL", "qty": 10, "asset_type": "EQUITY", "market_value": 1000},
            ]
        },
        tmp_path,
    )
    assert summary["positions"][0]["protection"]["status"] == "NO STOP SEEN"
    assert summary["protection_counts"]["NO STOP SEEN"] == 1
