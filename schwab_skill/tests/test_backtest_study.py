"""Adaptive study policy: vetoes, one factor, confirm, and budget."""

from __future__ import annotations

from pathlib import Path

from core.backtest_study import (
    TRIAL_BUDGET,
    propose_next,
    resolved_overrides,
    window_years,
)

ANCHOR = {
    "schema_version": 1,
    "universe_mode": "watchlist",
    "universe_preset": "nasdaq100",
    "start_date": "2024-01-01",
    "end_date": "2026-01-01",
    "strategy_ids": ["donchian_20"],
}


def _study(**extra: object) -> dict:
    payload = {"anchor_spec": ANCHOR, "budget": TRIAL_BUDGET, "trials": []}
    payload.update(extra)
    return payload


def _result(
    pf: float = 1.1,
    trades: int = 40,
    dd: float = 10.0,
    *,
    diagnostics: dict | None = None,
    data_integrity: dict | None = None,
) -> dict:
    return {
        "profit_factor_net": pf,
        "total_trades": trades,
        "max_drawdown_net_pct": dd,
        "diagnostics": diagnostics or {},
        "data_integrity": data_integrity or {},
    }


def _trial(role: str, spec: dict, result: dict, **extra: object) -> dict:
    row = {
        "run_id": extra.pop("run_id", role),
        "role": role,
        "status": "success",
        "spec": spec,
        "result": result,
    }
    row.update(extra)
    return row


def _changed_knobs(before: dict, after: dict) -> list[str]:
    left = resolved_overrides(before)
    right = resolved_overrides(after)
    return [knob for knob in left if left[knob] != right[knob]]


def test_data_quality_stops_without_a_child() -> None:
    result = _result(data_integrity={"history_fallback_used": 5, "history_fetch_total": 10})
    proposal = propose_next(_study(), [_trial("anchor", ANCHOR, result, run_id="a")])
    assert proposal["action"] == "stop"
    assert proposal["spec"] is None
    assert proposal["decision_kind"] == "abandon"


def test_thin_sample_widens_universe_without_tuning_knobs() -> None:
    result = _result(pf=1.8, trades=10)
    proposal = propose_next(_study(), [_trial("anchor", ANCHOR, result, run_id="a")])
    assert proposal["action"] == "queue"
    assert proposal["role"] == "widen"
    spec = proposal["spec"]
    assert spec["universe_preset"] == "sp1500"
    assert _changed_knobs(ANCHOR, spec) == []
    assert spec["start_date"] == ANCHOR["start_date"]
    assert spec["end_date"] == ANCHOR["end_date"]


def test_one_factor_step_changes_a_single_family() -> None:
    proposal = propose_next(_study(), [_trial("anchor", ANCHOR, _result(), run_id="a")])
    assert proposal["action"] == "queue"
    assert proposal["role"] == "screen"
    assert proposal["used_model"] is False
    changed = _changed_knobs(ANCHOR, proposal["spec"])
    assert changed == ["stage2_52w_pct"]
    assert proposal["family"] == "stage2"
    assert proposal["spec"]["universe_preset"] == "nasdaq100"
    assert proposal["spec"]["start_date"] == ANCHOR["start_date"]


def test_dead_direction_is_not_repeated() -> None:
    anchor = _trial("anchor", ANCHOR, _result(pf=1.2, trades=50), run_id="a")
    first = propose_next(_study(), [anchor])
    harmful = _trial(
        "screen",
        first["spec"],
        _result(pf=0.8, trades=36),
        run_id="b",
        family=first["family"],
        knob=first["knob"],
        direction=first["direction"],
        parent_run_id="a",
    )
    nxt = propose_next(_study(), [anchor, harmful])
    assert nxt["action"] == "queue"
    assert not (nxt["knob"] == first["knob"] and nxt["direction"] == first["direction"])
    assert nxt["used_model"] is False
    assert _changed_knobs(ANCHOR, nxt["spec"]) == [nxt["knob"]]


def test_forest_not_used_before_four_family_trials() -> None:
    anchor = _trial("anchor", ANCHOR, _result(), run_id="a")
    proposal = propose_next(_study(), [anchor])
    assert proposal["used_model"] is False
    screens = [anchor]
    spec = ANCHOR
    for idx in range(3):
        spec = {
            **ANCHOR,
            "overrides": {"stage2_sma_upward_days": 15 + idx},
        }
        screens.append(
            _trial(
                "screen",
                spec,
                _result(pf=1.05, trades=40),
                run_id=f"s{idx}",
                family="stage2",
                knob="stage2_sma_upward_days",
                direction="down",
                parent_run_id="a",
            )
        )
    nxt = propose_next(_study(), screens)
    assert nxt["used_model"] is False
    assert nxt["action"] == "queue"
    assert nxt["role"] == "screen"


def test_forest_ranks_one_neighbor_after_four_family_screens() -> None:
    anchor = _trial("anchor", ANCHOR, _result(pf=1.1, trades=40), run_id="a")
    screens = [anchor]
    overrides = (
        {"stage2_sma_upward_days": 10},
        {"stage2_sma_upward_days": 30},
        {"stage2_52w_pct": 0.75},
        {"stage2_52w_pct": 0.95},
    )
    for idx, ov in enumerate(overrides):
        screens.append(
            _trial(
                "screen",
                {**ANCHOR, "overrides": ov},
                _result(pf=1.05, trades=40),
                run_id=f"s{idx}",
                family="stage2",
                knob=next(iter(ov)),
                direction="down",
                parent_run_id="a",
            )
        )
    proposal = propose_next(_study(), screens)
    assert proposal["used_model"] is True
    assert proposal["family"] == "stage2"
    assert proposal["role"] == "screen"
    assert len(_changed_knobs(ANCHOR, proposal["spec"])) == 1


def test_confirm_horizon_instead_of_another_knob() -> None:
    anchor = _trial("anchor", ANCHOR, _result(pf=1.1, trades=40), run_id="a")
    winner = _trial("screen", ANCHOR, _result(pf=1.4, trades=40), run_id="b", family="stage2")
    proposal = propose_next(_study(), [anchor, winner])
    assert proposal["action"] == "queue"
    assert proposal["role"] == "confirm"
    assert proposal["family"] is None
    assert window_years(proposal["spec"]) == 5
    assert _changed_knobs(ANCHOR, proposal["spec"]) == []
    assert proposal["spec"]["universe_preset"] == "nasdaq100"


def test_budget_stops() -> None:
    trials = [_trial("anchor", ANCHOR, _result(), run_id="a")]
    for idx in range(TRIAL_BUDGET - 1):
        trials.append(
            _trial(
                "screen",
                ANCHOR,
                _result(pf=1.0, trades=40),
                run_id=f"s{idx}",
                family="stage2",
                knob="stage2_52w_pct",
                direction="down",
                parent_run_id="a",
            )
        )
    proposal = propose_next(_study(), trials)
    assert len(trials) == TRIAL_BUDGET
    assert proposal["action"] == "stop"
    assert proposal["spec"] is None
    assert proposal["decision_kind"] == "abandon"


def test_capacity_moves_positions_only() -> None:
    result = _result(
        trades=40,
        diagnostics={"capital_filtered": 10, "entries": 4},
    )
    proposal = propose_next(_study(), [_trial("anchor", ANCHOR, result, run_id="a")])
    assert proposal["role"] == "capacity"
    assert proposal["family"] == "capacity"
    assert _changed_knobs(ANCHOR, proposal["spec"]) == ["backtest_portfolio_max_positions"]
    assert resolved_overrides(proposal["spec"])["backtest_portfolio_max_positions"] == 14


def test_abandon_records_hold_not_promote(tmp_path: Path) -> None:
    from experiment_registry import load_registry_events
    from webapp.backtest_study_store import new_study, refresh_proposal

    study = new_study(ANCHOR, budget=1)
    study["trials"] = [_trial("anchor", ANCHOR, _result(), run_id="a")]
    proposal = refresh_proposal(
        study,
        {"a": {"status": "success", "result": _result()}},
        skill_dir=tmp_path,
    )
    assert proposal["action"] == "stop"
    events = load_registry_events(tmp_path)
    assert len(events) == 1
    assert events[0]["event_type"] == "backtest_study_decision"
    assert events[0]["decision"] == "hold"
    assert events[0]["decision"] != "promote"
