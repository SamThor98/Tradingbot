from __future__ import annotations

from webapp._shared import quote_health_hint


def test_hint_none_when_quote_ok() -> None:
    assert quote_health_hint({"reason": "http_error", "http_status": 401}, True) is None


def test_401_returns_entitlement_hint_not_reauth() -> None:
    hint = quote_health_hint(
        {"reason": "http_error", "http_status": 401, "error_detail": "401 Client Error: Unauthorized"},
        False,
    )
    assert hint is not None
    assert "Market Data API" in hint
    assert "NOT fix" in hint
    # Must not steer the operator back into a useless re-auth loop as the primary fix.
    assert "Run `python healthcheck.py`" not in hint


def test_401_detected_from_detail_without_status() -> None:
    hint = quote_health_hint(
        {"reason": "http_error", "error_detail": "401 Client Error: Unauthorized for url ..."},
        False,
    )
    assert hint is not None and "entitlement" in hint.lower()


def test_generic_http_error_still_suggests_healthcheck() -> None:
    hint = quote_health_hint({"reason": "http_error", "http_status": 500}, False)
    assert hint is not None
    assert "healthcheck.py" in hint


def test_last_price_not_parseable_hint_preserved() -> None:
    hint = quote_health_hint({"reason": "last_price_not_parseable"}, False)
    assert hint is not None and "last/mark/close" in hint
