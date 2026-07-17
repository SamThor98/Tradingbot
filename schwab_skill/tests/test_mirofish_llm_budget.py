from __future__ import annotations

import engine_analysis as ea


def test_mirofish_llm_budget_caps_fresh_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("MIROFISH_MAX_LLM_TICKERS_PER_SCAN", "1")
    monkeypatch.setenv("MIROFISH_CACHE_DIR", str(tmp_path / "cache"))
    ea.reset_mirofish_llm_budget()

    calls = {"n": 0, "seed": 0}

    def _fake_llm(*_args, **_kwargs):
        calls["n"] += 1
        return (
            '{"continuation_probability":0.7,"bull_trap_probability":0.3,'
            '"vcp_alignment":0.5,"sma_alignment":0.5,"key_drivers":["x"],'
            '"reason":"ok","horizon":"1-2 weeks"}'
        )

    def _fake_seed(self):
        calls["seed"] += 1
        return ("price seed\n\nnews", None)

    monkeypatch.setattr(ea, "_call_llm", _fake_llm)
    monkeypatch.setattr(ea.MarketSimulation, "_fetch_seed_data", _fake_seed)

    first = ea.MarketSimulation("AAA").run()
    second = ea.MarketSimulation("BBB").run()

    assert first.get("conviction_score") is not None
    assert second.get("conviction_score") is None
    assert second.get("unavailable_reason") == "llm_budget_exhausted"
    # Three agents for the first ticker only; second ticker must not seed-fetch.
    assert calls["n"] == 3
    assert calls["seed"] == 1


def test_mirofish_shared_cache_dir(monkeypatch, tmp_path):
    cache_dir = tmp_path / "shared"
    monkeypatch.setenv("MIROFISH_CACHE_DIR", str(cache_dir))
    path = ea._get_cache_path(tmp_path / "tenant")
    assert path == cache_dir / ea.MIROFISH_CACHE_FILE
    assert cache_dir.is_dir()
