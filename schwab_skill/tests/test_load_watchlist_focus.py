"""
Regression tests for `_load_watchlist` honoring SIGNAL_UNIVERSE_MODE=focused.

When SIGNAL_UNIVERSE_MODE=focused + SIGNAL_UNIVERSE_TARGET_SIZE=N is set
(via .env or per-call API strategy_overrides), `_load_watchlist` must narrow
the SP1500 to N tickers via prefilter_watchlist. After commit `8ff00dc` the
wiring that honored those env vars was removed, so focused-mode runs
silently fell back to the full universe. This test pins the restored
behavior. Scan studio can also request universe_preset=focused explicitly.
"""

from __future__ import annotations

from pathlib import Path

import config
import signal_scanner


def _make_alpha_tickers(n: int) -> list[str]:
    """Generate `n` distinct A-Z-only tickers (length 1-5) so prefilter_watchlist
    keeps them all instead of filtering by `isalpha()`."""
    out: list[str] = []
    seen: set[str] = set()
    # 26 + 26*26 + 26*26*26 = 18278 unique 1-3 letter combos -> ample for tests.
    for a in range(26):
        for b in range(26):
            for c in range(26):
                t = f"{chr(65 + a)}{chr(65 + b)}{chr(65 + c)}"
                if t not in seen:
                    seen.add(t)
                    out.append(t)
                if len(out) >= n:
                    return out
    return out


_FAKE_SP1500 = _make_alpha_tickers(1500)


def _patch_load_full(monkeypatch) -> None:
    # `_load_watchlist` does a lazy `from watchlist_loader import ...` inside
    # the function body, so patching the module attribute is what gets picked
    # up at call time.
    import watchlist_loader

    monkeypatch.setattr(
        watchlist_loader,
        "load_full_watchlist",
        lambda *_, **__: list(_FAKE_SP1500),
        raising=True,
    )


def test_load_watchlist_default_returns_full_sp1500(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SIGNAL_UNIVERSE_MODE", raising=False)
    monkeypatch.delenv("SIGNAL_UNIVERSE_TARGET_SIZE", raising=False)
    config.clear_env_cache()
    _patch_load_full(monkeypatch)

    wl = signal_scanner._load_watchlist(tmp_path)
    assert len(wl) == len(_FAKE_SP1500)


def test_load_watchlist_focused_mode_narrows_to_target(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SIGNAL_UNIVERSE_MODE", "focused")
    monkeypatch.setenv("SIGNAL_UNIVERSE_TARGET_SIZE", "100")
    config.clear_env_cache()
    _patch_load_full(monkeypatch)

    wl = signal_scanner._load_watchlist(tmp_path)
    # prefilter_watchlist may add a small number of liquid ETF hints on top of
    # the requested target; assert "narrowed and not the full universe".
    assert len(wl) <= 110
    assert len(wl) < len(_FAKE_SP1500)


def test_load_watchlist_broad_mode_returns_full_universe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SIGNAL_UNIVERSE_MODE", "broad")
    monkeypatch.setenv("SIGNAL_UNIVERSE_TARGET_SIZE", "100")
    config.clear_env_cache()
    _patch_load_full(monkeypatch)

    wl = signal_scanner._load_watchlist(tmp_path)
    assert len(wl) == len(_FAKE_SP1500)


def test_load_watchlist_nasdaq_preset_uses_named_universe(tmp_path: Path, monkeypatch) -> None:
    import watchlist_loader

    monkeypatch.setattr(watchlist_loader, "load_universe", lambda preset, **__: ["AAPL", "MSFT", "NVDA"])
    wl = signal_scanner._load_watchlist(tmp_path, universe_preset="nasdaq100")
    assert wl == ["AAPL", "MSFT", "NVDA"]


def test_load_watchlist_focused_preset_narrows_without_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SIGNAL_UNIVERSE_MODE", raising=False)
    monkeypatch.setenv("SIGNAL_UNIVERSE_TARGET_SIZE", "50")
    config.clear_env_cache()
    _patch_load_full(monkeypatch)
    wl = signal_scanner._load_watchlist(tmp_path, universe_preset="focused")
    assert len(wl) <= 60
    assert len(wl) < len(_FAKE_SP1500)


def test_load_universe_sector_etfs() -> None:
    import watchlist_loader

    names = watchlist_loader.load_universe("sector_etfs")
    assert "SPY" in names
    assert "XLK" in names


def test_load_universe_unknown_raises() -> None:
    import pytest

    import watchlist_loader

    with pytest.raises(ValueError):
        watchlist_loader.load_universe("russell2000")

