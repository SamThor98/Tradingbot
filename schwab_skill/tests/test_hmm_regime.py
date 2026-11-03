"""Advisory SPY HMM: causal filter, latch rules, and the cockpit card contract."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from core import cockpit_service, hmm_regime
from core.contracts.market import HmmRegimeView

ROOT = Path(__file__).resolve().parents[1]


def _sticky(k: int = 3) -> np.ndarray:
    trans = np.full((k, k), 0.02 / max(k - 1, 1))
    np.fill_diagonal(trans, 0.98)
    return trans / trans.sum(axis=1, keepdims=True)


def _bars(n_each: int = 140, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    price = 100.0
    closes: list[float] = []
    for drift, vol in ((0.0012, 0.004), (0.0, 0.006), (-0.004, 0.03)):
        for shock in rng.normal(drift, vol, n_each):
            price *= float(np.exp(shock))
            closes.append(price)
    close: np.ndarray = np.asarray(closes, dtype=float)
    index = pd.bdate_range("2016-01-04", periods=len(close))
    span = 0.01 + 0.01 * (np.arange(len(close)) % 7) / 6.0
    volume = np.full(len(close), 1_000_000.0)
    volume[n_each : 2 * n_each] = 1_500_000.0
    volume[2 * n_each :] = 3_000_000.0
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * (1.0 + span),
            "low": close * (1.0 - span * 0.8),
            "close": close,
            "volume": volume,
        },
        index=index,
    )


def _view_for(
    label: str | None,
    *,
    action: str = "hold",
    size: float = 0.8,
    uncertain: bool = False,
    data_quality: str | None = None,
) -> dict:
    labels = ["DRIFT_DOWN", "CHOP", "CALM_UP", "CRASH"]
    active = labels.index(label) if label in labels else None
    trans = _sticky(4)
    model = {
        "labels": labels,
        "stress": [True, False, False, True],
        "A": trans,
        "mean_log_return": [-0.002, 0.0, 0.001, -0.004],
        "realized_vol": [0.2, 0.1, 0.1, 0.4],
        "n_states": 4,
        "bic_by_states": {2: 10.0, 3: 12.0},
        "fit_through": "2020-01-02",
        "freeze_reasons": [],
    }
    index = pd.bdate_range("2020-01-02", periods=5)
    rules = {
        "switches": [],
        "actions": [action],
        "last": {
            "action": action,
            "action_reason": "test",
            "model_size_multiplier": size,
            "active_index": active,
            "current_label": label,
            "current_probability": 0.8 if label else None,
            "leading_label": label or "CHOP",
            "leading_probability": 0.8,
            "uncertain": uncertain,
            "top_gap": 0.05 if uncertain else 0.7,
            "stress_next_probability": 0.1,
            "next_probabilities": [0.25, 0.25, 0.25, 0.25],
            "confirms": 2,
            "bars_in_state": 4,
            "hold_bars_remaining": 1,
            "probabilities": [0.05, 0.05, 0.8, 0.1],
        },
    }
    return hmm_regime._assemble_view(
        model=model,
        params={
            "labels": labels,
            "stress": model["stress"],
            "A": trans,
            "pi": np.full(4, 0.25),
            "mu": np.zeros((4, 5)),
            "var": np.ones((4, 5)),
        },
        rules=rules,
        filtered=np.tile([0.05, 0.05, 0.8, 0.1], (5, 1)),
        pred_ll=np.zeros(5),
        index=index,
        fit_through=pd.Timestamp("2020-01-02"),
        reused=False,
        today=index[-1].date(),
        diagnostics={"data_quality": data_quality} if data_quality else {},
        bars_used=5,
    )


def test_choose_states_prefers_the_simpler_model_when_scores_are_close() -> None:
    assert hmm_regime.choose_states({2: 100.0, 3: 105.0, 4: 130.0}) == 2
    assert hmm_regime.choose_states({2: 200.0, 3: 100.0, 4: 109.0}) == 3


def test_latch_needs_two_confirming_bars_and_ignores_later_rows() -> None:
    labels = ["CALM_UP", "CHOP", "CRASH"]
    stress = [False, False, True]
    trans = _sticky()
    calm = np.tile([0.9, 0.05, 0.05], (4, 1))
    early = hmm_regime.apply_decision_rules(calm[:1], labels=labels, stress=stress, transition=trans)
    assert early["actions"] == ["size_zero"]
    held = hmm_regime.apply_decision_rules(calm, labels=labels, stress=stress, transition=trans)
    assert held["actions"][0] == "size_zero"
    assert held["actions"][1] == "switch"
    assert held["last"]["current_label"] == "CALM_UP"
    assert held["last"]["confirms"] == 2
    longer = calm.copy()
    longer = np.vstack([longer, np.tile([0.05, 0.05, 0.9], (3, 1))])
    full = hmm_regime.apply_decision_rules(longer, labels=labels, stress=stress, transition=trans)
    assert full["actions"][:4] == held["actions"]


def test_close_states_size_to_zero_and_stress_next_cuts_half() -> None:
    labels = ["CALM_UP", "CHOP", "CRASH"]
    stress = [False, False, True]
    unsure = np.array([[0.45, 0.40, 0.15], [0.46, 0.42, 0.12]])
    flat = hmm_regime.apply_decision_rules(unsure, labels=labels, stress=stress, transition=_sticky())
    assert flat["actions"] == ["size_zero", "size_zero"]
    assert flat["last"]["model_size_multiplier"] == 0.0
    assert flat["last"]["uncertain"] is True

    leak = np.array(
        [
            [0.50, 0.05, 0.45],
            [0.05, 0.90, 0.05],
            [0.05, 0.05, 0.90],
        ]
    )
    calm = np.tile([0.92, 0.05, 0.03], (3, 1))
    cut = hmm_regime.apply_decision_rules(calm, labels=labels, stress=stress, transition=leak)
    assert cut["actions"][-1] == "cut_half"
    assert abs(cut["last"]["model_size_multiplier"] - 0.46) < 1e-9


def test_zscores_and_refit_do_not_use_future_bars(tmp_path: Path) -> None:
    bars = _bars()
    early_features = hmm_regime.build_feature_frame(bars.iloc[:220])
    full_features = hmm_regime.build_feature_frame(bars)
    early_z = hmm_regime.causal_zscores(early_features, min_periods=20).dropna(how="any")
    full_z = hmm_regime.causal_zscores(full_features, min_periods=20)
    pd.testing.assert_frame_equal(early_z, full_z.loc[early_z.index])

    prefix = bars.iloc[:280].copy()
    kw = {
        "min_bars": 80,
        "min_periods": 20,
        "k_min": 2,
        "k_max": 3,
        "max_iter": 8,
        "include_trace": True,
    }
    cache = tmp_path / ".hmm_regime_spy.json"
    first = hmm_regime.evaluate_hmm(prefix, cache_path=cache, today=prefix.index[-1].date(), **kw)
    assert first["orders_placed"] == 0
    assert first["advisory_only"] is True
    assert first["probability_kind"] == "forward_filtered"
    frozen = hmm_regime.evaluate_hmm(prefix, cache_path=cache, today=prefix.index[-1].date(), **kw)
    extended = bars.iloc[:300].copy()
    extended.iloc[-1, extended.columns.get_loc("close")] = float(extended.iloc[-1]["close"]) * 1.5
    second = hmm_regime.evaluate_hmm(extended, cache_path=cache, today=extended.index[-1].date(), **kw)
    assert frozen["refit_reused"] is True
    assert second["refit_reused"] is True
    n = len(frozen["_trace_probs"])
    np.testing.assert_allclose(second["_trace_probs"][:n], frozen["_trace_probs"], atol=1e-8)
    assert second["_trace_actions"][:n] == frozen["_trace_actions"]


def test_short_or_stale_history_goes_flat() -> None:
    short = hmm_regime.evaluate_hmm(_bars(), min_bars=500, min_periods=20, k_max=2, max_iter=2)
    assert short["status"] == "unavailable"
    assert short["reason"] == "need_3y_history"
    assert short["advisory_size_multiplier"] == 0.0

    fresh = _bars()
    stale = hmm_regime.evaluate_hmm(
        fresh,
        min_bars=80,
        min_periods=20,
        k_min=2,
        k_max=2,
        max_iter=6,
        today=fresh.index[-1].date() + pd.Timedelta(days=10),
    )
    assert stale["status"] == "flat"
    assert stale["action"] == "flat"
    assert stale["advisory_size_multiplier"] == 0.0
    assert stale["orders_placed"] == 0


def test_playbook_policy_and_bad_data_quality() -> None:
    calm = _view_for("CALM_UP")
    assert calm["status"] == "ok"
    assert calm["advisory_size_multiplier"] == 0.8
    assert calm["active_playbook"]["book_allows_entry"] is True
    assert calm["orders_placed"] == 0

    crash = _view_for("CRASH")
    assert crash["action"] == "stand_aside"
    assert crash["advisory_size_multiplier"] == 0.0
    assert crash["active_playbook"]["book_allows_entry"] is False

    unsure = _view_for("CALM_UP", uncertain=True)
    assert unsure["status"] == "flat"
    assert unsure["advisory_size_multiplier"] == 0.0

    bad = _view_for("CALM_UP", data_quality="degraded")
    assert bad["action"] == "flat"
    assert bad["advisory_size_multiplier"] == 0.0
    assert bad["entries_frozen"] is True
    HmmRegimeView.model_validate(calm)
    HmmRegimeView.model_validate(bad)


def test_build_market_keeps_regime_v2_and_drops_a_bad_hmm_card() -> None:
    out = cockpit_service.build_market(
        {"regime_bullish": True, "spy_price": 500.0, "spy_sma_200": 460.0, "scan_blocked": 0},
        hmm_regime=_view_for("CALM_UP"),
    )
    assert out["is_regime_bullish"] is True
    assert out["hmm_regime"]["advisory_only"] is True
    assert out["hmm_regime"]["live_gate"] == "regime_v2"
    assert out["scan_blocked_by_regime"] is False

    broken = cockpit_service.build_market({}, hmm_regime={"states": "nope"})
    assert broken["hmm_regime"]["status"] == "unavailable"
    assert broken["hmm_regime"]["orders_placed"] == 0


def test_cockpit_card_is_advisory_only() -> None:
    text = (ROOT / "webapp" / "static" / "panels" / "cockpit.js").read_text(encoding="utf-8")
    assert "renderHmmRegime" in text
    assert "This card places no orders" in text
    assert "Regime v2 remains the live gate" in text
