"""Phase E: portfolio sizing + composite promotion."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from experiment_registry import append_registry_event, load_registry_events  # noqa: E402
from research.portfolio import (  # noqa: E402
    apply_sizing,
    equal_weight_by_day,
    run_portfolio_research,
    size_multiplier_for_signal,
)
from research.promotion import (  # noqa: E402
    PF_MEAN_FLOOR,
    evaluate_prob_rank_promotion,
    metrics_from_portfolio_result,
)


def _trades() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["A", "B", "C", "A", "B", "C"],
            "entry_date": ["2020-01-02"] * 3 + ["2020-01-03"] * 3,
            "net_return": [0.06, 0.02, -0.03, 0.04, -0.01, 0.03],
            "era": ["crash_recovery"] * 6,
            "rank_score_v2": [90, 70, 40, 85, 50, 60],
            "sector_etf": ["XLK", "XLK", "XLF", "XLK", "XLF", "XLF"],
            "atr_pct": [0.02, 0.03, 0.025, 0.02, 0.03, 0.02],
        }
    )


def _scored() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["A", "B", "C", "A", "B", "C"],
            "asof_date": ["2020-01-02", "2020-01-02", "2020-01-02", "2020-01-03", "2020-01-03", "2020-01-03"],
            "expected_return_40d": [0.05, 0.02, -0.01, 0.04, 0.01, 0.015],
            "confidence": [0.8, 0.6, 0.4, 0.7, 0.5, 0.55],
            "expected_downside_40d": [0.03, 0.04, 0.05, 0.03, 0.04, 0.035],
            "atr_pct": [0.02, 0.03, 0.025, 0.02, 0.03, 0.02],
        }
    )


def test_equal_weight_sums_to_one_per_day() -> None:
    df = _trades()
    sized = equal_weight_by_day(df)
    for _, grp in sized.groupby("entry_iso"):
        assert float(grp["position_weight"].sum()) == pytest.approx(1.0)


def test_edge_vol_respects_max_position() -> None:
    selected = _trades().assign(
        expected_return_40d=[0.1, 0.01, 0.01, 0.1, 0.01, 0.01],
        confidence=0.9,
        atr_pct=0.02,
    )
    sized = apply_sizing(selected, mode="edge_vol", max_position=0.5, max_sector=1.0, kelly_cap=0.5)
    for _, grp in sized.groupby("entry_iso"):
        assert float(grp["position_weight"].max()) <= 0.5 + 1e-6
        assert float(grp["position_weight"].sum()) == pytest.approx(1.0)


def test_portfolio_research_reports_ew_and_control() -> None:
    result = run_portfolio_research(_trades(), _scored(), top_n=2, sizing_mode="equal")
    assert result["n_selected"] == 4  # top-2 × 2 days
    assert result["equal_weight_top_n"]["n"] == 4
    assert result["rank_v2_control"] is not None
    assert result["portfolio"]["sizing_mode"] == "equal"


def test_size_multiplier_equal_is_one() -> None:
    assert size_multiplier_for_signal({"expected_return_40d": 0.1}, mode="equal") == 1.0


def test_promotion_rejects_below_floors() -> None:
    v = evaluate_prob_rank_promotion({"pf_mean": 0.90, "worst_era_pf": 0.70, "n_trades": 500})
    assert v.decision == "reject"
    assert v.floors_cleared is False


def test_promotion_hold_iterate_band() -> None:
    v = evaluate_prob_rank_promotion({"pf_mean": 1.10, "worst_era_pf": 0.90, "n_trades": 500})
    assert v.decision == "hold"
    assert v.floors_cleared is False


def test_promotion_shadow_when_floors_and_composite_ok() -> None:
    v = evaluate_prob_rank_promotion(
        {
            "pf_mean": 1.25,
            "worst_era_pf": 1.05,
            "n_trades": 1500,
            "retention": 0.30,
            "walk_forward_ic_mean": 0.02,
            "bootstrap": {"pf_mean": 1.24, "pf_lo": 1.15, "pf_hi": 1.35},
            "calibration_error": 0.02,
        },
        requested="shadow",
    )
    assert v.floors_cleared is True
    assert v.decision == "promote_shadow"
    assert v.composite_score is not None and v.composite_score >= 0.45


def test_promotion_live_requires_dual_run() -> None:
    metrics = {
        "pf_mean": 1.30,
        "worst_era_pf": 1.10,
        "n_trades": 2000,
        "retention": 0.28,
        "walk_forward_ic_mean": 0.03,
        "bootstrap": {"pf_mean": 1.28, "pf_lo": 1.20, "pf_hi": 1.36},
        "calibration_error": 0.01,
        "dual_run_ok": False,
    }
    v = evaluate_prob_rank_promotion(metrics, requested="live")
    assert v.decision == "promote_shadow"
    v2 = evaluate_prob_rank_promotion({**metrics, "dual_run_ok": True}, requested="live")
    assert v2.decision == "promote_live"


def test_metrics_from_portfolio_prefers_equal_weight() -> None:
    result = {
        "retention": 0.25,
        "equal_weight_top_n": {"pf_mean_eras": 1.22, "worst_era_pf": 1.01, "n": 100},
        "portfolio": {"pf_mean_eras": 1.50, "worst_era_pf": 1.20, "n": 100},
    }
    m = metrics_from_portfolio_result(result)
    assert m["pf_mean"] == 1.22
    assert m["worst_era_pf"] == 1.01


def test_registry_prob_rank_event(tmp_path: Path) -> None:
    append_registry_event(
        event_type="prob_rank_promotion_decision",
        target="PROB_RANK_MODE",
        decision="hold",
        rationale=["unit test"],
        gates={"pf_mean_floor": PF_MEAN_FLOOR},
        metadata={"test": True},
        skill_dir=tmp_path,
    )
    rows = load_registry_events(tmp_path)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "prob_rank_promotion_decision"


def test_decide_script_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    art = tmp_path / "portfolio.json"
    art.write_text(
        json.dumps(
            {
                "retention": 0.3,
                "equal_weight_top_n": {
                    "pf_mean_eras": 1.25,
                    "worst_era_pf": 1.05,
                    "n": 1200,
                },
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "decision.json"
    from scripts.decide_prob_rank_promotion import main

    monkeypatch.setattr(
        "scripts.decide_prob_rank_promotion.SKILL_DIR",
        tmp_path,
    )
    # Avoid writing into real validation_artifacts via default out
    rc = main(
        [
            "--artifact",
            str(art),
            "--requested",
            "shadow",
            "--out",
            str(out),
            "--ic",
            "0.02",
        ]
    )
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["decision"] == "promote_shadow"
