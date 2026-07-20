"""Disagreement attribution: only_prob vs only_v2 PF buckets."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.shadow_disagreement import analyze_topn_disagreement  # noqa: E402


def test_disagreement_favors_prob_when_only_prob_wins() -> None:
    rows = []
    # Day with 10 names: top-3 by expected_return are T0,T1,T2 (positive returns)
    # top-3 by rank_v2 are T7,T8,T9 (negative returns) → only_prob should win
    for i in range(10):
        rows.append(
            {
                "ticker": f"T{i}",
                "entry_date": "2020-06-01",
                "era": "volatility_chop",
                "expected_return_40d": 0.10 - i * 0.01,
                "rank_score_v2": float(i),
                "net_return": 0.05 if i < 3 else (-0.04 if i >= 7 else 0.01),
            }
        )
    # Second day for era mean
    for i in range(10):
        rows.append(
            {
                "ticker": f"U{i}",
                "entry_date": "2020-06-02",
                "era": "volatility_chop",
                "expected_return_40d": 0.10 - i * 0.01,
                "rank_score_v2": float(i),
                "net_return": 0.06 if i < 3 else (-0.05 if i >= 7 else 0.0),
            }
        )
    report = analyze_topn_disagreement(pd.DataFrame(rows), top_n=3, min_cohort=8)
    assert report["ok"] is True
    assert report["n_days"] == 2
    assert report["buckets"]["only_prob"]["n"] > 0
    assert report["buckets"]["only_v2"]["n"] > 0
    assert report["buckets"]["only_prob"]["pf"] is not None
    assert report["buckets"]["only_prob"]["pf"] > report["buckets"]["only_v2"]["pf"]
    assert report["verdict"] in {
        "disagreement_favors_prob",
        "slight_edge_prob",
        "near_tie",
    }
