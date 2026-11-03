"""Walk-forward cutoff must not use the holdout era."""

from __future__ import annotations

import pandas as pd

from research.open_research import sort_key_verdict, surprise_vs_return, walk_forward_percentile


def test_surprise_ic_uses_net_return_only() -> None:
    rows = []
    for era in ("a", "b"):
        for i in range(60):
            rows.append({"era": era, "net_return": i / 100.0})
    df = pd.DataFrame(rows)
    surprises = [row["net_return"] for row in rows]
    scored = surprise_vs_return(df, surprises)
    assert scored["ic_net_return"] == 1.0
    assert scored["n_eras_with_ic"] == 2
    assert scored["sign_consistent_eras"] == 2


def test_walk_forward_threshold_ignores_holdout() -> None:
    rows = []
    for era, scores in {"train": [1, 2, 3, 4], "hold": [100, 100, 100, 0]}.items():
        for score in scores:
            rows.append({"era": era, "score": score, "net_return": 0.01})
    df = pd.DataFrame(rows)
    kept = walk_forward_percentile(df, score_col="score", min_percentile=75)
    hold = kept.loc[kept["era"] == "hold", "score"].tolist()
    # Train p75 of [1, 2, 3, 4] is 3.25, so the holdout 0 is dropped and 100s stay.
    assert 0 not in hold
    assert hold == [100, 100, 100]


def test_sort_key_verdict_stays_unpromoted_when_floors_miss() -> None:
    assert (
        sort_key_verdict(
            {
                "wf_p76_inv_pts52w": {"clears_floors": False, "pf_mean": 1.1},
                "take_every_trade": {"pf_mean": 1.0},
                "top5_signal_score": {"pf_mean": 0.9},
            }
        )
        == "do_not_promote_sort_key"
    )
