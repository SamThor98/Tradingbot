from __future__ import annotations

from core.mirofish_model_log import (
    append_mirofish_model_log,
    compare_mirofish_model_log,
    load_mirofish_model_log,
)


def test_compare_pairs_same_seed_different_models(tmp_path) -> None:
    append_mirofish_model_log(
        skill_dir=tmp_path,
        ticker="AAA",
        result={
            "simulation_id": "sim_a",
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
            "conviction_score": 10,
            "mirofish_disagreement": 0.1,
            "continuation_probability": 0.6,
            "bull_trap_probability": 0.4,
            "agent_weighting": {"mode": "shadow", "applied": False},
            "seed_fingerprint": "seed1",
        },
    )
    append_mirofish_model_log(
        skill_dir=tmp_path,
        ticker="AAA",
        result={
            "simulation_id": "sim_b",
            "llm_provider": "xai",
            "llm_model": "grok-4-fast",
            "conviction_score": 30,
            "mirofish_disagreement": 0.2,
            "continuation_probability": 0.7,
            "bull_trap_probability": 0.3,
            "agent_weighting": {"mode": "shadow", "applied": False},
            "seed_fingerprint": "seed1",
        },
    )
    rows = load_mirofish_model_log(tmp_path)
    report = compare_mirofish_model_log(rows)
    assert report["paired_rows"] == 1
    pair = report["pairs"][0]
    assert pair["ticker"] == "AAA"
    assert pair["conviction_delta"] == 20.0
    assert report["mean_abs_conviction_delta"] == 20.0


def test_compare_skips_unpaired_models(tmp_path) -> None:
    append_mirofish_model_log(
        skill_dir=tmp_path,
        ticker="BBB",
        result={
            "llm_model": "grok-4-fast",
            "conviction_score": 12,
            "seed_fingerprint": "only-grok",
        },
    )
    report = compare_mirofish_model_log(load_mirofish_model_log(tmp_path))
    assert report["paired_rows"] == 0
    assert report["pairs"] == []
