"""Split MiroFish evidence and shadow research-wave annotations."""

from __future__ import annotations

import pandas as pd

import engine_analysis as ea
from core.research_wave import annotate_research_wave


def _price_frame() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=30, freq="B")
    close = [100.0 + i for i in range(30)]
    return pd.DataFrame(
        {
            "close": close,
            "high": [value + 1.0 for value in close],
            "low": [value - 1.0 for value in close],
            "volume": [1_000_000.0] * 30,
        },
        index=index,
    )


def test_personas_read_separate_evidence(monkeypatch, tmp_path) -> None:
    prompts: list[tuple[str, str]] = []

    def _fake_llm(prompt: str, system: str, _env: dict) -> str:
        prompts.append((prompt, system))
        return (
            '{"continuation_probability":0.7,"bull_trap_probability":0.3,'
            '"vcp_alignment":0.2,"sma_alignment":0.2,"key_drivers":["a","b"],'
            '"horizon":"1-2 weeks"}'
        )

    monkeypatch.setattr(ea, "_call_llm", _fake_llm)
    monkeypatch.setattr(ea, "_get_news_seed", lambda *_a, **_k: "Recent news:\n- Upgrade beats estimates")
    monkeypatch.setenv("MIROFISH_CACHE_DIR", str(tmp_path / "cache"))
    ea.reset_mirofish_llm_budget()

    result = ea.MarketSimulation("AAA", seed_df=_price_frame(), skill_dir=tmp_path).run()

    assert len(prompts) == 3
    institutional, mean_reversion, retail = prompts
    assert "Recent news" not in institutional[0]
    assert "52-week proximity" not in institutional[0]
    assert "VCP" not in mean_reversion[0]
    assert "fade_score" in mean_reversion[0]
    assert "Headlines only" in retail[0]
    assert "VCP dry-up" not in retail[0]
    assert result["persona_prompt_version"] == ea.MIROFISH_PROMPT_VERSION
    assert {vote["evidence"] for vote in result["agent_votes"]} == {"trend", "extension", "headlines"}


def test_fade_schema_maps_to_negative_conviction() -> None:
    parsed = ea._parse_agent_response(
        '{"fade_score": 40, "exhaustion_probability": 0.7, "extension_alignment": 0.8, '
        '"key_drivers": ["extended", "near high"], "horizon": "1-2 weeks"}'
    )
    assert parsed["conviction_score"] == -40
    assert parsed["bull_trap_probability"] == 0.7
    assert parsed["continuation_probability"] == 0.3
    assert parsed["sma_alignment"] == -0.8


def test_cache_misses_other_model_and_prompt_version(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MIROFISH_CACHE_DIR", str(tmp_path))
    ea.cache_conviction(
        "AAA",
        {
            "conviction_score": 40,
            "summary": "Neutral / Mixed",
            "agent_votes": [],
            "simulation_id": "sim_test",
            "seed_fingerprint": "abc",
            "llm_model": "grok-4-fast",
            "persona_prompt_version": "split-v1",
        },
        skill_dir=tmp_path,
    )
    hit = ea.get_cached_conviction(
        "AAA",
        skill_dir=tmp_path,
        llm_model="grok-4-fast",
        prompt_version="split-v1",
    )
    assert hit is not None
    assert ea.get_cached_conviction("AAA", skill_dir=tmp_path, llm_model="gpt-4o-mini") is None
    assert ea.get_cached_conviction("AAA", skill_dir=tmp_path, prompt_version="shared-v0") is None


def _signal(**overrides: object) -> dict:
    base = {
        "ticker": "AAA",
        "price": 100.0,
        "sma_50": 95.0,
        "sma_200": 80.0,
        "signal_score": 70.0,
        "score_components": {"score": 70.0, "pts_mirofish": 5.0},
        "breakout_confirmed": True,
        "sector_etf": "XLK",
        "sector_winning": True,
        "entry_family": "stage2",
        "_data_quality": "ok",
        "forensic_flags": [],
        "avg_vol_50": 2_000_000.0,
        "mirofish_conviction": 35,
        "mirofish_result": {
            "conviction_score": 35,
            "summary": "Moderate Continuation",
            "agent_votes": [
                {"name": "institutional_trend", "score": 40, "continuation_probability": 0.6},
                {"name": "mean_reversion", "score": 30, "continuation_probability": 0.55},
            ],
        },
    }
    base.update(overrides)
    return base


def test_clean_signal_keeps_score_and_skips_skeptic(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MIROFISH_ABSTAIN_MODE", "shadow")
    monkeypatch.setenv("R3_CAPACITY_SHADOW", "false")
    signal = _signal()
    notes = annotate_research_wave(signal, skill_dir=tmp_path, df=_price_frame())
    assert signal["signal_score"] == 70.0
    assert signal["skeptic"]["voted"] is False
    assert notes["skeptic_votes"] == 0
    assert signal["mirofish_abstain_would"] is False
    assert signal["r5_sector_rs_eligible"] is True
    assert signal["r1_entry_state"] in {"setup", "pierce", "armed"}
    assert "signal_score_r3" not in signal


def test_skeptic_and_shadow_abstain_do_not_change_score(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MIROFISH_ABSTAIN_MODE", "shadow")
    monkeypatch.setenv("R3_CAPACITY_SHADOW", "true")
    signal = _signal(
        price=120.0,
        sma_50=100.0,
        avg_vol_50=1_000.0,
        mirofish_result={
            "conviction_score": 10,
            "summary": "Neutral / Mixed",
            "agent_votes": [
                {"name": "institutional_trend", "score": 100, "continuation_probability": 0.9},
                {"name": "mean_reversion", "score": -100, "continuation_probability": 0.1},
            ],
        },
    )
    annotate_research_wave(signal, skill_dir=tmp_path)
    assert signal["signal_score"] == 70.0
    assert signal["skeptic"]["voted"] is True
    assert signal["skeptic"]["score"] < 0
    assert "extended_gt_12pct_sma50" in signal["r2_flags"]
    assert signal["mirofish_abstain_would"] is True
    assert signal["mirofish_conviction"] == 35
    assert signal["signal_score_r3"] < 70.0
    annotate_research_wave(signal, skill_dir=tmp_path)
    names = [vote["name"] for vote in signal["mirofish_result"]["agent_votes"]]
    assert names.count("skeptic") == 1


def test_live_abstain_removes_mirofish_points(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MIROFISH_ABSTAIN_MODE", "live")
    monkeypatch.delenv("R3_CAPACITY_SHADOW", raising=False)
    signal = _signal(
        mirofish_result={
            "conviction_score": 10,
            "summary": "Neutral / Mixed",
            "agent_votes": [
                {"name": "institutional_trend", "score": 100, "continuation_probability": 0.9},
                {"name": "mean_reversion", "score": -100, "continuation_probability": 0.1},
            ],
        }
    )
    annotate_research_wave(signal, skill_dir=tmp_path)
    assert signal["mirofish_abstain_would"] is True
    assert signal["signal_score"] == 65.0
    assert signal["score_components"]["pts_mirofish"] == 0.0
    assert signal["mirofish_conviction"] is None
    assert signal["mirofish_result"]["summary"] == "Abstain (disagreement)"


def test_r4_scores_pead_rows_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MIROFISH_ABSTAIN_MODE", "off")
    monkeypatch.setenv("R3_CAPACITY_SHADOW", "false")
    stage2 = _signal()
    annotate_research_wave(stage2, skill_dir=tmp_path)
    assert "r4_score" not in stage2
    pead = _signal(entry_family="pead_primary", pead_surprise_pct=4.0, edge_score=10.0)
    annotate_research_wave(pead, skill_dir=tmp_path)
    assert pead["r4_score"] > 0
