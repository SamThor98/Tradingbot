"""SEC companyfacts warehouse: extract, cache, recompute without network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.companyfacts.client import CompanyfactsFetchError, fetch_companyfacts
from core.companyfacts.extract import extract_statements
from core.companyfacts.model import implied_growth, price_model, statements_to_inputs
from core.companyfacts.service import ingest_ticker, pricing_payload, recompute_payload
from core.event_probability.valuation import dcf_per_share

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "companyfacts_aapl_subset.json"


@pytest.fixture
def facts() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def skill_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EDGAR_USER_AGENT", "TradingBot ciagent@gmail.com")
    monkeypatch.setenv("COMPANYFACTS_ENABLED", "true")
    monkeypatch.setenv("COMPANYFACTS_YAHOO_FALLBACK", "false")
    return tmp_path


def _fetcher(facts: dict):
    calls: list[str] = []

    def fetch(url: str, user_agent: str, timeout_sec: float) -> dict:
        calls.append(url)
        if "company_tickers" in url:
            return {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        if "companyfacts" in url:
            return dict(facts)
        raise AssertionError(f"unexpected URL {url}")

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_extract_derives_fcf_and_net_debt(facts: dict) -> None:
    statements = extract_statements(facts, ticker="AAPL")
    assert statements.errors == []
    assert [round(p.val / 1e9, 1) for p in statements.fcf] == [15.0, 17.0, 19.2]
    assert statements.net_debt() == pytest.approx(30_000_000_000)
    assert statements.shares is not None
    assert statements.shares.val == pytest.approx(15_000_000_000)
    assert statements.source_concepts["fcf"] == "operating_cf - capex"
    assert statements.cik == "0000320193"


def test_later_filed_restatement_wins(facts: dict) -> None:
    rows = facts["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"]
    rows.append(
        {
            "end": "2023-09-30",
            "val": 30000000000,
            "accn": "restated",
            "fy": 2023,
            "fp": "FY",
            "form": "10-K/A",
            "filed": "2024-02-01",
        }
    )
    statements = extract_statements(facts, ticker="AAPL")
    assert statements.operating_cf[-1].val == pytest.approx(30_000_000_000)
    assert statements.fcf[-1].val == pytest.approx(25_000_000_000)


def test_price_model_matches_engine(facts: dict) -> None:
    statements = extract_statements(facts, ticker="AAPL")
    inputs = statements_to_inputs(statements, price=10.0)
    priced = price_model(inputs)
    expected = dcf_per_share(
        last_fcf=inputs.last_fcf,
        growth=inputs.growth,
        wacc=inputs.wacc,
        terminal_growth=inputs.terminal_growth,
        net_debt=inputs.net_debt,
        shares=inputs.shares,
    )
    assert priced["intrinsic_value"] == pytest.approx(expected)
    assert priced["current_price"] == 10.0
    implied = implied_growth(inputs, target_price=expected)
    assert implied == pytest.approx(inputs.growth, abs=1e-3)


def test_fetch_uses_cache_on_second_call(facts: dict, skill_dir: Path) -> None:
    fetcher = _fetcher(facts)
    first = fetch_companyfacts("AAPL", skill_dir=skill_dir, fetcher=fetcher, cache_hours=72)
    assert first["_cache"]["from_cache"] is False
    second = fetch_companyfacts("AAPL", skill_dir=skill_dir, fetcher=fetcher, cache_hours=72)
    assert second["_cache"]["from_cache"] is True
    facts_calls = [u for u in fetcher.calls if "companyfacts" in u]
    assert len(facts_calls) == 1


def test_rate_limited_is_not_long_cached(skill_dir: Path) -> None:
    def boom(url: str, user_agent: str, timeout_sec: float) -> dict:
        if "company_tickers" in url:
            return {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        raise CompanyfactsFetchError("rate_limited", "429")

    with pytest.raises(CompanyfactsFetchError) as exc:
        fetch_companyfacts("AAPL", skill_dir=skill_dir, fetcher=boom, cache_hours=72)
    assert exc.value.code == "rate_limited"


def test_ingest_and_recompute_are_cpu_local_after_cache(
    facts: dict, skill_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.companyfacts.service._overlay_price", lambda ticker, skill_dir=None: (10.0, "test"))
    fetcher = _fetcher(facts)
    payload = ingest_ticker("AAPL", skill_dir=skill_dir, fetcher=fetcher, price=10.0)
    assert payload["status"] in {"ok", "degraded"}
    assert payload["dcf"]["intrinsic_value"] > 0
    assert payload["provenance"]["source"] == "sec.companyfacts"
    network_calls = len(fetcher.calls)
    recomputed = recompute_payload(
        "AAPL",
        skill_dir=skill_dir,
        growth=0.08,
        wacc=0.09,
        terminal_growth=0.02,
        price=10.0,
    )
    assert recomputed["status"] in {"ok", "degraded"}
    assert recomputed["recomputed"] is True
    assert recomputed["inputs"]["growth"] == pytest.approx(0.08)
    assert len(fetcher.calls) == network_calls


def test_placeholder_user_agent_refuses_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGAR_USER_AGENT", "SchwabTradingBot contact@example.com")
    fetcher = _fetcher(facts)
    with pytest.raises(CompanyfactsFetchError) as exc:
        fetch_companyfacts("AAPL", skill_dir=tmp_path, fetcher=fetcher)
    assert exc.value.code == "edgar_user_agent"
    assert fetcher.calls == []


def test_pricing_payload_not_ingested_when_cache_missing(skill_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.companyfacts.service._overlay_price", lambda ticker, skill_dir=None: (0.0, ""))
    payload = pricing_payload("MSFT", skill_dir=skill_dir, ingest_on_miss=False)
    assert payload["status"] == "not_ingested"
