"""Tests for PEAD earnings provider routing and surprise logic."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from config import clear_env_cache
from earnings_signal import (
    EARNINGS_CACHE_FILE,
    _calc_surprise,
    _evaluate_earnings_window,
    _resolve_pead_provider,
    check_earnings_at_date,
    check_recent_earnings,
    earnings_cache_summary,
    maybe_warm_earnings_for_scan,
    warm_earnings_for_ticker,
    warm_earnings_for_tickers,
)


@pytest.fixture(autouse=True)
def _clear_config_cache() -> Iterator[None]:
    clear_env_cache()
    yield
    clear_env_cache()


def test_calc_surprise_floors_near_zero_estimate() -> None:
    assert _calc_surprise(0.13, 0.10) == pytest.approx(0.3)
    assert _calc_surprise(0.13, 0.03) == pytest.approx(1.0)


def test_calc_surprise_clamps_extremes() -> None:
    assert _calc_surprise(1.0, 0.01) == 3.0
    assert _calc_surprise(-1.0, 0.01) == -3.0


def test_resolve_pead_provider_defaults_to_finnhub_with_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("FINNHUB_API_KEY=test-key\n", encoding="utf-8")
    assert _resolve_pead_provider(tmp_path) == "finnhub"


def test_resolve_pead_provider_off_without_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("PEAD_DATA_PROVIDER", raising=False)
    assert _resolve_pead_provider(tmp_path) == "off"


def test_resolve_pead_provider_blocks_yfinance_under_schwab_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "PEAD_DATA_PROVIDER=yfinance\nSCHWAB_ONLY_DATA=true\n",
        encoding="utf-8",
    )
    assert _resolve_pead_provider(tmp_path) == "off"


def test_check_earnings_at_date_uses_finnhub_cache(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "PEAD_DATA_PROVIDER=finnhub\nFINNHUB_API_KEY=test-key\nSCHWAB_ONLY_DATA=true\n",
        encoding="utf-8",
    )

    cache_path = tmp_path / EARNINGS_CACHE_FILE
    cache_path.write_text(
        json.dumps(
            {
                "AAPL": {
                    "stored_at": time.time(),
                    "provider": "finnhub",
                    "rows": [
                        {
                            "date": "2024-01-25",
                            "actual_eps": 2.18,
                            "estimate_eps": 2.10,
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    out = check_earnings_at_date("AAPL", "2024-01-30", lookback_days=10, skill_dir=tmp_path)
    assert out is not None
    assert out["had_recent_earnings"] is True
    assert out["earnings_provider"] == "finnhub"
    assert out["beat"] is True
    assert out["surprise_pct"] == pytest.approx(0.0381, rel=1e-3)


def test_check_earnings_at_date_off_when_provider_off(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PEAD_DATA_PROVIDER=off\n", encoding="utf-8")
    assert check_earnings_at_date("AAPL", "2024-01-30", skill_dir=tmp_path) is None


def test_evaluate_earnings_window_no_recent_event() -> None:
    frame = pd.DataFrame(
        [{"actual_eps": 1.0, "estimate_eps": 0.9}],
        index=pd.to_datetime(["2024-01-01"]),
    )
    out = _evaluate_earnings_window(
        frame,
        pd.Timestamp("2024-03-01"),
        10,
        earnings_provider="finnhub",
    )
    assert out is not None
    assert out["had_recent_earnings"] is False


def test_warm_earnings_for_ticker_uses_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("PEAD_DATA_PROVIDER", raising=False)
    (tmp_path / ".env").write_text(
        "PEAD_DATA_PROVIDER=finnhub\nFINNHUB_API_KEY=test-key\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / EARNINGS_CACHE_FILE
    cache_path.write_text(
        json.dumps(
            {
                "MSFT": {
                    "stored_at": time.time(),
                    "provider": "finnhub",
                    "rows": [{"date": "2024-04-25", "actual_eps": 2.94, "estimate_eps": 2.82}],
                }
            }
        ),
        encoding="utf-8",
    )
    out = warm_earnings_for_ticker("MSFT", skill_dir=tmp_path, force=False)
    assert out["skipped"] is True
    assert out["reason"] == "cache_fresh"
    assert out["row_count"] == 1


def test_warm_earnings_for_ticker_fetches_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("PEAD_DATA_PROVIDER", raising=False)
    (tmp_path / ".env").write_text(
        "PEAD_DATA_PROVIDER=finnhub\nFINNHUB_API_KEY=test-key\n",
        encoding="utf-8",
    )

    def _fake_history(ticker: str, *, skill_dir: Path | None = None, history_years: int = 12):
        return {
            "ok": True,
            "ticker": ticker,
            "rows": [{"date": "2024-02-01", "actual_eps": 1.2, "estimate_eps": 1.0}],
            "errors": [],
        }

    monkeypatch.setattr("finnhub_data.get_finnhub_earnings_history", _fake_history)
    out = warm_earnings_for_ticker("NVDA", skill_dir=tmp_path, force=False)
    assert out["skipped"] is False
    assert out["ok"] is True
    assert out["row_count"] == 1
    cached = json.loads((tmp_path / EARNINGS_CACHE_FILE).read_text(encoding="utf-8"))
    assert "NVDA" in cached


def test_warm_earnings_batch_resumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("PEAD_DATA_PROVIDER", raising=False)
    (tmp_path / ".env").write_text(
        "PEAD_DATA_PROVIDER=finnhub\nFINNHUB_API_KEY=test-key\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def _fake_history(ticker: str, *, skill_dir: Path | None = None, history_years: int = 12):
        calls.append(ticker)
        return {
            "ok": True,
            "ticker": ticker,
            "rows": [{"date": "2024-02-01", "actual_eps": 1.0, "estimate_eps": 0.9}],
            "errors": [],
        }

    monkeypatch.setattr("finnhub_data.get_finnhub_earnings_history", _fake_history)
    warm_earnings_for_tickers(["AAA", "BBB"], skill_dir=tmp_path, force=False, resume=True)
    warm_earnings_for_tickers(["AAA", "BBB"], skill_dir=tmp_path, force=False, resume=True)
    assert calls == ["AAA", "BBB"]


def test_earnings_cache_summary_counts_fresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("PEAD_DATA_PROVIDER", raising=False)
    cache_path = tmp_path / EARNINGS_CACHE_FILE
    cache_path.write_text(
        json.dumps(
            {
                "AAA": {
                    "stored_at": time.time(),
                    "provider": "finnhub",
                    "rows": [{"date": "2024-01-01", "actual_eps": 1.0, "estimate_eps": 0.9}],
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "PEAD_DATA_PROVIDER=finnhub\nFINNHUB_API_KEY=test-key\n",
        encoding="utf-8",
    )
    summary = earnings_cache_summary(["AAA", "BBB"], skill_dir=tmp_path)
    assert summary["fresh"] == 1
    assert summary["missing"] == 1


def test_maybe_warm_earnings_for_scan_skips_when_warm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("PEAD_DATA_PROVIDER", raising=False)
    (tmp_path / ".env").write_text(
        "PEAD_DATA_PROVIDER=finnhub\nFINNHUB_API_KEY=test-key\nPEAD_ENABLED=true\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / EARNINGS_CACHE_FILE
    cache_path.write_text(
        json.dumps(
            {
                "AAA": {
                    "stored_at": time.time(),
                    "provider": "finnhub",
                    "rows": [{"date": "2024-01-01", "actual_eps": 1.0, "estimate_eps": 0.9}],
                }
            }
        ),
        encoding="utf-8",
    )
    out = maybe_warm_earnings_for_scan(["AAA"], skill_dir=tmp_path)
    assert out.get("reason") == "cache_warm"


def test_maybe_warm_earnings_for_scan_defers_large_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("PEAD_DATA_PROVIDER", raising=False)
    (tmp_path / ".env").write_text(
        "PEAD_DATA_PROVIDER=finnhub\nFINNHUB_API_KEY=test-key\n"
        "PEAD_PRESCAN_WARM_MAX_MISSING=2\n",
        encoding="utf-8",
    )
    out = maybe_warm_earnings_for_scan(["AAA", "BBB", "CCC"], skill_dir=tmp_path)
    assert out.get("reason") == "too_many_missing_run_warm_script"


def test_check_recent_earnings_returns_none_when_off(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PEAD_DATA_PROVIDER=off\n", encoding="utf-8")
    assert check_recent_earnings("AAPL", skill_dir=tmp_path) is None
