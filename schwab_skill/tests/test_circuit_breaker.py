from __future__ import annotations

import socket

import pytest
import requests

import auth as auth_mod
import circuit_breaker as cb
import market_data


class _ReadTimeout(Exception):
    def __str__(self) -> str:
        return "Read timed out"


class _FakeAuth:
    def get_market_token(self) -> str:
        return "tok"

    class market_session:
        @staticmethod
        def force_refresh() -> bool:
            return False


@pytest.fixture(autouse=True)
def _reset_schwab_circuit():
    cb.schwab_circuit.reset()
    yield
    cb.schwab_circuit.reset()


def test_single_timeout_does_not_trip() -> None:
    breaker = cb.CircuitBreaker(name="t", unstable_for_seconds=60, failure_threshold=3)
    assert cb.maybe_trip_breaker(_ReadTimeout(), breaker) is False
    assert breaker.connection_stable is True
    assert breaker.consecutive_failures == 1


def test_consecutive_timeouts_trip_then_success_resets() -> None:
    breaker = cb.CircuitBreaker(name="t", unstable_for_seconds=60, failure_threshold=3)
    assert cb.maybe_trip_breaker(_ReadTimeout(), breaker) is False
    assert cb.maybe_trip_breaker(_ReadTimeout(), breaker) is False
    assert cb.maybe_trip_breaker(_ReadTimeout(), breaker) is True
    assert breaker.connection_stable is False
    breaker.record_success()
    assert breaker.connection_stable is True
    assert breaker.consecutive_failures == 0


def test_http_error_does_not_count_as_network_failure() -> None:
    breaker = cb.CircuitBreaker(name="t", failure_threshold=1)
    err = requests.HTTPError("401 Unauthorized")
    assert cb.maybe_trip_breaker(err, breaker) is False
    assert breaker.connection_stable is True


def test_dns_and_connect_timeout_are_transient() -> None:
    assert cb.is_transient_network_error(socket.gaierror("getaddrinfo failed"))
    connect = type("ConnectTimeout", (Exception,), {})("ConnectTimeout: timed out")
    assert cb.is_transient_network_error(connect)


def test_oauth_exchange_is_not_blocked_by_open_breaker(monkeypatch) -> None:
    cb.schwab_circuit.mark_unstable()
    assert cb.schwab_circuit.connection_stable is False

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"access_token": "a", "refresh_token": "r"}

    monkeypatch.setattr(auth_mod.requests, "post", lambda *args, **kwargs: _Resp())
    tokens = auth_mod.exchange_code_for_tokens("id", "secret", "code")
    assert tokens["access_token"] == "a"
    assert cb.schwab_circuit.connection_stable is True


def test_request_with_backoff_retries_timeout_then_succeeds(monkeypatch) -> None:
    calls = {"n": 0}

    class _Resp:
        status_code = 200

    def _request(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ReadTimeout("Read timed out")
        return _Resp()

    monkeypatch.setattr(market_data, "_MARKET_RATE_LIMITER", None)
    monkeypatch.setattr(market_data.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(market_data.requests, "request", _request)

    resp = market_data._request_with_backoff(_FakeAuth(), "GET", "https://api.schwabapi.com/marketdata/v1/quotes")
    assert resp.status_code == 200
    assert calls["n"] == 2
    assert cb.schwab_circuit.connection_stable is True
    assert cb.schwab_circuit.consecutive_failures == 0


def test_request_with_backoff_exhausted_timeout_counts_one_failure(monkeypatch) -> None:
    def _request(*args, **kwargs):
        raise requests.exceptions.ReadTimeout("Read timed out")

    monkeypatch.setattr(market_data, "_MARKET_RATE_LIMITER", None)
    monkeypatch.setattr(market_data.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(market_data.requests, "request", _request)
    cb.schwab_circuit.failure_threshold = 3

    with pytest.raises(requests.exceptions.ReadTimeout):
        market_data._request_with_backoff(_FakeAuth(), "GET", "https://api.schwabapi.com/marketdata/v1/quotes")

    assert cb.schwab_circuit.connection_stable is True
    assert cb.schwab_circuit.consecutive_failures == 1


def test_schwab_circuit_collapses_parallel_timeout_wave() -> None:
    breaker = cb.schwab_circuit
    breaker.failure_threshold = 8
    breaker.failure_collapse_seconds = 1.0
    for _ in range(4):
        assert cb.maybe_trip_breaker(_ReadTimeout(), breaker) is False
    assert breaker.consecutive_failures == 1
    assert breaker.connection_stable is True


def test_plain_breaker_still_trips_on_three_instant_failures() -> None:
    breaker = cb.CircuitBreaker(name="plain", failure_threshold=3, failure_collapse_seconds=0.0)
    assert cb.maybe_trip_breaker(_ReadTimeout(), breaker) is False
    assert cb.maybe_trip_breaker(_ReadTimeout(), breaker) is False
    assert cb.maybe_trip_breaker(_ReadTimeout(), breaker) is True
