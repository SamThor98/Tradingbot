"""Pure helpers for the extended-exit and PEAD factor tests."""

from __future__ import annotations

import pandas as pd

from scripts.analyze_next_factor_tests import extended_mask, undo_trend_tightening


def test_undo_tightening_only_when_20d_return_exceeds_6pct() -> None:
    assert abs(undo_trend_tightening(0.09, 0.07) - 0.1) < 1e-9
    assert undo_trend_tightening(0.09, 0.06) == 0.09
    assert undo_trend_tightening(0.09, None) == 0.09
    assert undo_trend_tightening(0.11, 0.20) == 0.12


def test_extended_mask_does_not_use_the_holdout_era() -> None:
    df = pd.DataFrame(
        {
            "era": ["a", "a", "b", "b"],
            "close_vs_sma200_pct": [0.10, 0.50, 0.10, 0.90],
        }
    )
    mask = extended_mask(df)
    # Era b's cut comes from era a (p80 of 0.10 and 0.50 = 0.42), so 0.90 is extended
    # and 0.10 is not. Era a's cut comes from era b.
    assert bool(mask.iloc[3]) is True
    assert bool(mask.iloc[2]) is False
