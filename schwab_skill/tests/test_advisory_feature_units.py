"""Advisory features stay on the fraction scale the scanner emits."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from advisory_model import score_signal_advisory
from core.scoring_composite import apply_composite_risk_caps
from core.sec_risk_score import sec_risk_tag_from_score

SKILL_DIR = Path(__file__).resolve().parents[1]
ARTIFACT = json.loads((SKILL_DIR / "advisory_model_v1.json").read_text(encoding="utf-8"))


def _signal(**overrides: object) -> dict:
    row = {
        "signal_score": 62.0,
        "price": 100.0,
        "sma_50": 94.34,
        "sma_200": 84.75,
        "latest_volume": 1_200_000.0,
        "avg_vol_50": 1_000_000.0,
        "sec_risk_tag": "unknown",
        "score_components": {
            "pct_from_52w_high": 0.92,
            "avg_vcp_volume_ratio": 0.75,
        },
        "mirofish_result": {
            "continuation_probability": 0.55,
            "bull_trap_probability": 0.45,
        },
        "ret_5d_prev": 0.01,
        "ret_20d_prev": 0.03,
        "atr_pct": 0.025,
    }
    row.update(overrides)
    return row


def _use_repo_model(monkeypatch) -> None:
    monkeypatch.setattr("config.get_advisory_model_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr("advisory_model.load_model_artifact", lambda *_a, **_k: ARTIFACT)


def test_bootstrap_centers_match_fraction_units() -> None:
    cols = ARTIFACT["feature_columns"]
    mu = dict(zip(cols, ARTIFACT["feature_mu"], strict=True))
    sigma = dict(zip(cols, ARTIFACT["feature_sigma"], strict=True))
    assert mu["pct_from_52w_high"] == 0.8
    assert sigma["pct_from_52w_high"] == 0.1
    assert mu["close_vs_sma50_pct"] == 0.04
    assert mu["close_vs_sma200_pct"] == 0.1


def test_near_high_setup_is_not_forced_below_a_coin_flip(monkeypatch) -> None:
    _use_repo_model(monkeypatch)
    pred = score_signal_advisory(_signal(), skill_dir=SKILL_DIR)
    assert pred is not None
    assert pred.p_up_10d >= 0.50


def test_closer_to_the_high_raises_the_chance(monkeypatch) -> None:
    _use_repo_model(monkeypatch)
    far = _signal()
    far["score_components"] = {"pct_from_52w_high": 0.70, "avg_vcp_volume_ratio": 0.75}
    near = _signal()
    near["score_components"] = {"pct_from_52w_high": 1.0, "avg_vcp_volume_ratio": 0.75}
    far_pred = score_signal_advisory(far, skill_dir=SKILL_DIR)
    near_pred = score_signal_advisory(near, skill_dir=SKILL_DIR)
    assert far_pred is not None and near_pred is not None
    assert near_pred.p_up_10d - far_pred.p_up_10d >= 0.05


def test_price_history_changes_the_five_day_return(monkeypatch) -> None:
    _use_repo_model(monkeypatch)
    flat = pd.DataFrame(
        {
            "close": [100.0] * 25,
            "sma_50": [94.34] * 25,
            "sma_200": [84.75] * 25,
            "atr_14": [2.5] * 25,
            "volume": [1_200_000.0] * 25,
            "avg_vol_50": [1_000_000.0] * 25,
        }
    )
    rising = flat.copy()
    rising.loc[rising.index[-6] :, "close"] = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    base = _signal()
    base.pop("ret_5d_prev")
    base.pop("ret_20d_prev")
    flat_pred = score_signal_advisory(base, skill_dir=SKILL_DIR, window=flat)
    rising_pred = score_signal_advisory(base, skill_dir=SKILL_DIR, window=rising)
    assert flat_pred is not None and rising_pred is not None
    assert rising_pred.p_up_10d > flat_pred.p_up_10d


def test_sec_risk_score_sign_matches_the_advisory_encoder() -> None:
    assert sec_risk_tag_from_score(1.0) == "low"
    assert sec_risk_tag_from_score(-1.0) == "high"
    assert sec_risk_tag_from_score(0.0) == "unknown"
    low = apply_composite_risk_caps(
        80.0,
        reliability_score=80.0,
        execution_score=80.0,
        sec_risk_tag="unknown",
        forensic_flags=[],
        sec_risk_score=1.0,
    )
    high = apply_composite_risk_caps(
        80.0,
        reliability_score=80.0,
        execution_score=80.0,
        sec_risk_tag="unknown",
        forensic_flags=[],
        sec_risk_score=-1.0,
    )
    assert low == 80.0
    assert high == 45.0
