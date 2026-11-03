"""Factor analytics: reproduce negative-IC sign and quantile tables."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.factor_analytics import (  # noqa: E402
    DOCUMENTED_STAGE2_ONLY_AUG_IC,
    analyze_scores,
    equity_tearsheet,
    write_factor_report,
)


def _neg_ic_frame(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    ret = rng.normal(0.0, 0.04, n)
    # Anti-rank: higher score → worse forward return (stage2_only_aug pattern)
    score = -ret + rng.normal(0, 0.005, n)
    eras = np.array(["late_bull", "volatility_chop", "crash_recovery", "bear_rates"] * (n // 4 + 1))[:n]
    dates = pd.bdate_range("2016-01-04", periods=n)
    return pd.DataFrame(
        {
            "asof_date": dates,
            "ticker": [f"T{i % 40:02d}" for i in range(n)],
            "era": eras,
            "net_return": ret,
            "ret_40d_fwd": ret,
            "ret_20d_fwd": ret * 0.6,
            "ret_10d_fwd": ret * 0.4,
            "ret_5d_fwd": ret * 0.2,
            "signal_score": 50 + score * 100,
            "rank_score_v2": 50 + score * 80,
            "composite_score": 50 + score * 90,
            "expected_return_40d": score,
            "prob_rank": score,
        }
    )


def test_analyze_scores_reproduces_negative_ic_and_quantiles(tmp_path: Path) -> None:
    df = _neg_ic_frame()
    payload = analyze_scores(df)
    assert payload["n"] == 400
    sig = payload["scores"]["signal_score"]
    assert sig["negative_ic"] is True
    assert sig["ic"] < 0
    assert sig["quantiles"], "quantile table required"
    assert sig["ic_by_era"]
    assert DOCUMENTED_STAGE2_ONLY_AUG_IC["signal_score"] < 0
    out = write_factor_report(payload, run_id="fixture_neg_ic", skill_dir=tmp_path)
    assert (out / "factor_analytics.json").is_file()
    # Must not touch catalog
    assert payload.get("catalog_overwrite") is None or payload.get("catalog_overwrite") is False


def test_tearsheet_is_book_diagnostics_not_pf_gate() -> None:
    eq = pd.DataFrame(
        {
            "date": pd.bdate_range("2020-01-02", periods=80),
            "equity": np.linspace(100_000, 108_000, 80),
        }
    )
    sheet = equity_tearsheet(eq)
    assert sheet["label"] == "book_diagnostics_not_pf_gate"
    assert "not a PF" in sheet["banner"].lower() or "not a PF" in sheet["banner"]
    assert sheet["sharpe"] == sheet["sharpe"]
    assert sheet["cagr"] is not None
