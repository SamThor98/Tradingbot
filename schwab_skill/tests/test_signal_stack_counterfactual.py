from __future__ import annotations

import pandas as pd

from scripts.analyze_signal_stack_counterfactual import _rank_v2_percentile_filter


def test_rank_v2_percentile_filter_keeps_top_thirty_percent() -> None:
    frame = pd.DataFrame(
        {
            "ticker": [f"T{value}" for value in range(1, 11)],
            "rank_score_v2": [float(value) for value in range(1, 11)],
        }
    )

    kept, threshold = _rank_v2_percentile_filter(frame, 70)

    assert threshold == 7.3
    assert kept["ticker"].tolist() == ["T8", "T9", "T10"]


def test_rank_v2_percentile_filter_fails_closed_without_scores() -> None:
    kept, threshold = _rank_v2_percentile_filter(pd.DataFrame({"ticker": ["A", "B", "C"]}), 70)

    assert kept.empty
    assert threshold is None
