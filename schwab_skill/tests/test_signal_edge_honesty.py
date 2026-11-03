"""Empty-book rankings and score labels must not read as measured edge."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.phase1_overlay_sweep import (  # noqa: E402
    _summarise_per_era,
    ranking_sample_status,
)
from webapp.report_v2 import _pick_thesis_items  # noqa: E402

EMPTY_CONFIGS = {
    "breakout_vol_120",
    "confluence_both",
    "confluence_either",
    "signal_gate_combo",
    "vcp_pre_breakout",
}


def _era(trades: int, pf: float | None) -> dict:
    return {"total_trades": trades, "profit_factor_net": pf}


def test_thin_eras_are_not_a_profit_factor() -> None:
    treatment = {era: _era(0, 0.0) for era in ("late_bull", "bear_rates", "recent_current")}
    control = {era: _era(100, 1.1) for era in treatment}
    summary = _summarise_per_era(treatment, control)
    assert summary["sample_status"] == "insufficient_sample"
    assert summary["pf_mean_treatment"] is None
    assert summary["pf_mean_delta"] is None
    assert summary["regressed_eras"] == []
    assert summary["passes_guardrails"] is False
    assert ranking_sample_status(summary) == "insufficient_sample"


def test_measured_era_still_averages() -> None:
    treatment = {
        "late_bull": _era(80, 1.4),
        "bear_rates": _era(10, 0.0),
    }
    control = {
        "late_bull": _era(80, 1.0),
        "bear_rates": _era(80, 1.0),
    }
    summary = _summarise_per_era(treatment, control)
    assert summary["sample_status"] == "measured"
    assert summary["pf_mean_treatment"] == 1.4
    assert summary["pf_mean_delta"] == 0.4
    assert summary["thin_eras"] == ["bear_rates"]
    assert "bear_rates" not in {row["era"] for row in summary["regressed_eras"]}


def test_committed_gate_ranking_drops_empty_books() -> None:
    payload = json.loads(
        (SKILL_DIR / "tests" / "fixtures" / "phase1_progress_signal_gate.json").read_text(encoding="utf-8")
    )
    ranked = {row["config_id"] for row in payload["ranking"]}
    unranked = {row["config_id"] for row in payload["unranked_insufficient_sample"]}
    assert ranked.isdisjoint(EMPTY_CONFIGS)
    assert EMPTY_CONFIGS <= unranked
    assert "control_legacy" in ranked
    for row in payload["unranked_insufficient_sample"]:
        assert row["pf_mean_treatment"] is None
        assert row["sample_status"] == "insufficient_sample"


def test_signal_edge_ledger_blocks_plugin_live() -> None:
    payload = json.loads((SKILL_DIR / "signal_edge_ledger.json").read_text(encoding="utf-8"))
    assert payload["plugin_live"] == "regime_v2_only"
    assert payload["promotion_floors"]["pf_mean"] == 1.2
    assert payload["promotion_floors"]["worst_era_pf"] == 1.0
    roles = {book["role"]: book for book in payload["books"]}
    assert roles["bare_pre_cap"]["verdict"] == "iterate_with_caution"
    assert roles["bare_pre_cap"]["clears_floors"] is False
    assert roles["bare_pts52w_cap37"]["verdict"] == "proceed"
    assert roles["bare_pts52w_cap37"]["clears_floors"] is True
    assert roles["setup_confidence"]["promote"] is False
    replay = payload["capped_replay"]
    assert replay["verdict"] == "proceed"
    assert replay["equity_replay"]["max_gross_pct"] == 1.0
    assert replay["pf_mean"] == 1.2137
    shadow = payload["sort_shadow"]
    assert shadow["promote_sort"] is False
    assert shadow["verdict"] == "shadow_ahead_intervals_overlap"


def test_high_signal_points_are_not_called_conviction() -> None:
    thesis, risks, _, _ = _pick_thesis_items(
        {"technical": {"signal_score": 80, "stage_2": False, "vcp": False}}
    )
    text = " ".join(thesis + risks).lower()
    assert "conviction" not in text
    assert "not a probability" in text
