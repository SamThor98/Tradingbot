"""Shadow evidence ledger: overlap vs rank-v2, append, summarize."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.shadow_evidence import (  # noqa: E402
    append_shadow_evidence,
    build_shadow_evidence_record,
    ledger_path,
    load_shadow_evidence_records,
    record_shadow_evidence,
    summarize_shadow_evidence,
)


def test_build_record_computes_jaccard() -> None:
    signals = [
        {
            "ticker": "A",
            "expected_return_40d": 0.05,
            "rank_score_v2": 90.0,
            "prob_rank_selection": {"would_keep": True, "rank": 1},
            "prob_rank": {"model_id": "m1", "cross_section_rank": 1},
        },
        {
            "ticker": "B",
            "expected_return_40d": 0.04,
            "rank_score_v2": 10.0,
            "prob_rank_selection": {"would_keep": True, "rank": 2},
            "prob_rank": {"model_id": "m1", "cross_section_rank": 2},
        },
        {
            "ticker": "C",
            "expected_return_40d": 0.01,
            "rank_score_v2": 80.0,
            "prob_rank_selection": {"would_keep": False, "rank": 3},
            "prob_rank": {"model_id": "m1", "cross_section_rank": 3},
        },
    ]
    diagnostics = {
        "prob_rank_mode": "shadow",
        "prob_rank_top_n": 2,
        "prob_rank_scored": 3,
        "prob_rank_unscored": 0,
    }
    rec = build_shadow_evidence_record(signals, diagnostics)
    assert rec is not None
    assert rec["prob_would_keep_n"] == 2  # A,B
    assert rec["rank_v2_top_n_count"] == 2  # A,C by rank_v2
    assert rec["overlap_n"] == 1  # A
    assert rec["jaccard"] == pytest.approx(1 / 3, abs=1e-3)
    assert "B" in rec["only_prob_rank"]
    assert "C" in rec["only_rank_v2"]
    assert rec["model_id"] == "m1"


def test_off_mode_skips_record() -> None:
    assert build_shadow_evidence_record([], {"prob_rank_mode": "off"}) is None


def test_append_and_summarize(tmp_path: Path) -> None:
    signals = [
        {
            "ticker": "AAPL",
            "expected_return_40d": 0.02,
            "rank_score_v2": 70.0,
            "prob_rank_selection": {"would_keep": True},
            "prob_rank": {"model_id": "m2"},
        },
        {
            "ticker": "MSFT",
            "expected_return_40d": 0.01,
            "rank_score_v2": 60.0,
            "prob_rank_selection": {"would_keep": False},
            "prob_rank": {"model_id": "m2"},
        },
    ]
    diagnostics: dict = {
        "prob_rank_mode": "shadow",
        "prob_rank_top_n": 1,
        "prob_rank_scored": 2,
    }
    rec = record_shadow_evidence(signals, diagnostics, tmp_path, scan_label="unit")
    assert rec is not None
    assert ledger_path(tmp_path).is_file()
    assert diagnostics["prob_rank_shadow_evidence"]["written"] is True

    rows = load_shadow_evidence_records(tmp_path)
    assert len(rows) == 1
    summary = summarize_shadow_evidence(rows)
    assert summary["n_scans"] == 1
    assert summary["model_ids"] == ["m2"]
    assert summary["latest"]["overlap_n"] == 1


def test_append_is_jsonl(tmp_path: Path) -> None:
    r1 = {"ts_utc": "t1", "jaccard": 0.5, "overlap_n": 1}
    r2 = {"ts_utc": "t2", "jaccard": 1.0, "overlap_n": 2}
    append_shadow_evidence(tmp_path, r1)
    append_shadow_evidence(tmp_path, r2)
    text = ledger_path(tmp_path).read_text(encoding="utf-8").strip().splitlines()
    assert len(text) == 2
    assert json.loads(text[1])["overlap_n"] == 2


def test_seed_records_from_scored_trades() -> None:
    import pandas as pd

    from research.shadow_evidence import seed_records_from_scored_trades

    rows = []
    for i in range(10):
        rows.append(
            {
                "ticker": f"T{i}",
                "entry_date": "2020-06-01",
                "era": "volatility_chop",
                "expected_return_40d": 0.10 - i * 0.01,
                "rank_score_v2": float(i),  # inverse ranking vs prob
            }
        )
    merged = pd.DataFrame(rows)
    recs = seed_records_from_scored_trades(
        merged, top_n=3, model_id="m_seed", min_cohort=8, max_days=5
    )
    assert len(recs) == 1
    assert recs[0]["source"] == "cf_day_cohort"
    assert recs[0]["prob_would_keep_n"] == 3
    assert recs[0]["jaccard"] is not None
    assert recs[0]["jaccard"] < 1.0  # inverse ranks → disagreement
