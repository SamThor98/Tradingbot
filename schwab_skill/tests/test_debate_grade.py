"""Shadow debate grade: checklist, caps, and incremental correlation."""

from __future__ import annotations

import json
import math

import pandas as pd

from backtest import _score_fields_for_trade
from core.debate_grade import attach_debate_grade, grade_from_signal
from core.scoring_metrics import debate_grade_report, score_columns_for_source


def _passing_signal(**overrides: object) -> dict:
    signal: dict = {
        "ticker": "AAPL",
        "signal_score": 62.0,
        "price": 120.0,
        "sma_50": 100.0,
        "stop_pct": 0.07,
        "trend_stack_ok": True,
        "sma200_rising": True,
        "vcp_present": True,
        "score_components": {"pts_52w": 10.0, "pts_volume": 16.0},
    }
    signal.update(overrides)
    return signal


def test_pts_52w_moves_crowding_only() -> None:
    low = grade_from_signal(_passing_signal())
    high = grade_from_signal(_passing_signal(score_components={"pts_52w": 40.0, "pts_volume": 16.0}))
    assert low["qual_trend"] == high["qual_trend"] == 100.0
    assert high["qual_crowding"] == 100.0
    assert high["crowding_fail"] is True
    assert low["crowding_fail"] is False
    assert high["qual_trend"] == 100.0


def test_missing_votes_leave_disagreement_null() -> None:
    grade = grade_from_signal(_passing_signal())
    assert grade["qual_disagreement"] is None
    assert grade["disagreement_source"] == "unavailable"


def test_coverage_under_half_nulls_pillar_and_business_stays_null() -> None:
    grade = grade_from_signal(
        {
            "ticker": "MSFT",
            "signal_score": 50.0,
            "score_components": {"pts_52w": 8.0, "pts_volume": 4.0},
        }
    )
    assert grade["qual_trend"] is None
    assert grade["qual_coverage_trend"] == 0.0
    assert grade["qual_business"] is None
    assert grade["qual_crowding"] is not None


def test_stale_data_abstains_without_a_letter() -> None:
    grade = grade_from_signal(_passing_signal(_data_quality="stale"))
    assert grade["qual_abstain"] is True
    assert grade["qual_raw"] is None
    assert grade["qual_letter"] is None
    assert grade["qual_score"] is None


def test_caps_change_qual_score_and_leave_qual_raw() -> None:
    trend_fail = grade_from_signal(
        _passing_signal(trend_stack_ok=False, sma200_rising=False)
    )
    assert trend_fail["qual_trend"] == 0.0
    assert trend_fail["qual_raw"] > trend_fail["qual_score"]
    assert trend_fail["qual_letter"] in {"D", "F"}

    risk_fail = grade_from_signal(_passing_signal(price=102.0, sma_50=100.0, stop_pct=0.07))
    assert risk_fail["qual_asymmetry"] == 0.0
    assert risk_fail["qual_raw"] > 65.0
    assert risk_fail["qual_score"] <= 64.99
    assert risk_fail["qual_letter"] == "C"
    assert risk_fail["qual_raw"] != risk_fail["qual_score"]


def test_disagreement_cap_requires_votes() -> None:
    votes = [
        {"score": 100, "continuation_probability": 1.0},
        {"score": -100, "continuation_probability": 0.0},
    ]
    grade = grade_from_signal(_passing_signal(mirofish_result={"agent_votes": votes}))
    assert grade["disagreement_source"] == "votes"
    assert grade["qual_disagreement"] is not None
    assert grade["qual_disagreement"] > 0.65
    assert grade["qual_score"] <= 64.99
    assert grade["qual_raw"] > grade["qual_score"]


def test_attach_does_not_change_signal_score(monkeypatch) -> None:
    monkeypatch.setattr("config.get_debate_grade_mode", lambda skill_dir=None: "shadow")
    signal = _passing_signal()
    diagnostics: dict = {}
    out = attach_debate_grade(signal, diagnostics=diagnostics, skill_dir=None)
    assert out["signal_score"] == 62.0
    assert out["debate_grade"]["qual_letter"]
    assert diagnostics["debate_grade_evaluated"] == 1


def test_live_mode_is_coerced_and_off_omits_the_grade(monkeypatch) -> None:
    monkeypatch.setattr("config.get_debate_grade_mode", lambda skill_dir=None: "live")
    diagnostics: dict = {}
    live = attach_debate_grade(_passing_signal(), diagnostics=diagnostics, skill_dir=None)
    assert live["signal_score"] == 62.0
    assert live["debate_grade"]["mode"] == "shadow"
    assert diagnostics["debate_grade_live_coerced"] == 1

    monkeypatch.setattr("config.get_debate_grade_mode", lambda skill_dir=None: "off")
    off = attach_debate_grade(_passing_signal(), diagnostics={}, skill_dir=None)
    assert "debate_grade" not in off


def test_trade_fields_survive_and_stay_out_of_rank_stack(monkeypatch) -> None:
    monkeypatch.setattr("config.get_debate_grade_mode", lambda skill_dir=None: "shadow")
    signal = _passing_signal()
    attach_debate_grade(signal, diagnostics={}, skill_dir=None)
    # Mode may be off in a developer env; force the object for the flatten path.
    if "debate_grade" not in signal:
        signal["debate_grade"] = grade_from_signal(signal)
    fields = _score_fields_for_trade(signal)
    assert fields["signal_score"] == 62.0
    assert fields["qual_raw"] == signal["debate_grade"]["qual_raw"]
    assert fields["disagreement_source"] == "unavailable"
    assert fields["qual_abstain"] is False
    frame = pd.DataFrame(
        {
            "rank_score": [float(i) for i in range(40)],
            "signal_score": [50.0] * 40,
            "qual_raw": [float(i) for i in range(40)],
        }
    )
    columns = score_columns_for_source("candidates", frame)
    assert "qual_raw" not in columns
    assert "rank_score" in columns


def test_incremental_ic_and_disagreement_exclusion() -> None:
    n = 60
    signal_score = [float(i % 5) for i in range(n)]
    qual_raw = [float(i) for i in range(n)]
    returns = [0.001 * i for i in range(n)]
    frame = pd.DataFrame(
        {
            "signal_score": signal_score,
            "qual_raw": qual_raw,
            "qual_raw_equal": qual_raw,
            "qual_trend": [80.0] * n,
            "qual_setup": [80.0] * n,
            "qual_asymmetry": [80.0] * n,
            "qual_crowding": [20.0] * n,
            "qual_letter": ["B"] * n,
            "qual_abstain": [False] * n,
            "disagreement_source": ["unavailable"] * n,
            "qual_disagreement": [None] * n,
            "net_return": returns,
            "era": ["recent_current"] * n,
        }
    )
    block = debate_grade_report(frame, ret_col="net_return")
    assert block["skipped"] is False
    assert math.isfinite(block["incremental_ic"])
    assert block["disagreement"]["skipped"] is True
    assert all(row["insufficient"] for row in block["quintiles"])

    voted = frame.copy()
    voted["disagreement_source"] = "votes"
    voted["qual_disagreement"] = [0.1 if i < 30 else 0.8 for i in range(n)]
    voted_block = debate_grade_report(voted, ret_col="net_return")
    assert voted_block["disagreement"]["skipped"] is False
    assert voted_block["disagreement"]["row_count"] == n

    identical = frame.copy()
    identical["qual_raw"] = identical["signal_score"]
    identical_block = debate_grade_report(identical, ret_col="net_return")
    assert math.isnan(identical_block["incremental_ic"])
    assert block["asymmetry_spread"]["no_spread"] is True
    assert "excluded from the claim" in (block["claim_note"] or "")
    assert math.isfinite(block["incremental_ic"])


def test_business_pillar_requires_a_date_on_or_before_entry(monkeypatch) -> None:
    monkeypatch.setattr("config.get_debate_grade_mode", lambda skill_dir=None: "shadow")
    undated = _passing_signal(
        forensic_flags=["beneish_manipulator"],
        forensic_observed=True,
        sec_risk_tag="high",
        entry_date="2020-06-01",
    )
    attach_debate_grade(undated, diagnostics={}, skill_dir=None)
    raw = undated["debate_grade"]["qual_raw"]
    assert undated["debate_grade"]["qual_business"] is None
    assert undated["debate_grade"]["qual_raw_with_business"] is None

    future = _passing_signal(
        sec_risk_tag="high",
        business_as_of="2026-01-01",
        entry_date="2020-06-01",
    )
    attach_debate_grade(future, diagnostics={}, skill_dir=None)
    assert future["debate_grade"]["qual_business"] is None
    assert future["debate_grade"]["qual_raw"] == raw

    dated = _passing_signal(
        sec_risk_tag="high",
        business_as_of="2020-06-01",
        entry_date="2020-06-01",
    )
    attach_debate_grade(dated, diagnostics={}, skill_dir=None)
    assert dated["debate_grade"]["qual_business"] == 0.0
    assert dated["debate_grade"]["qual_raw"] == raw
    assert dated["debate_grade"]["qual_raw_with_business"] != raw


def test_dossier_grade_line_uses_filing_date_only(monkeypatch) -> None:
    from core.debate_grade import attach_dossier_grade

    monkeypatch.setattr("config.get_debate_grade_mode", lambda skill_dir=None: "shadow")
    dossier = {
        "ticker": "AAPL",
        "sections": {
            "technical_valuation_fundamentals": {
                "raw_report": {
                    "technical": {
                        "current_price": 120.0,
                        "sma_50": 100.0,
                        "sma_150": 90.0,
                        "sma_200": 80.0,
                        "vcp": True,
                        "signal_score": 70.0,
                        "stage_2": False,
                    }
                }
            },
            "sec_narrative": {"analyze": {"filing_date": "2024-02-01", "guidance_signal": "negative"}},
            "forensic": {"ok": True, "forensic_flags": ["beneish_manipulator"]},
        },
    }
    attach_dossier_grade(dossier)
    grade = dossier["debate_grade"]
    assert grade["qual_trend"] == 100.0
    assert grade["qual_business"] == 0.0
    assert grade["qual_raw"] == 100.0
    assert grade["qual_raw_with_business"] < grade["qual_raw"]
    from webapp.routes.research import _dossier_grade_lines

    text = " ".join(_dossier_grade_lines(dossier))
    assert "Shadow grade:" in text
    assert "Does not change rank." in text


def test_research_grade_uses_sma_stack_and_filing_date() -> None:
    from core.debate_grade import research_grade_signal

    signal = research_grade_signal(
        technical={
            "current_price": 90.0,
            "sma_50": 100.0,
            "sma_150": 80.0,
            "sma_200": 70.0,
            "vcp": True,
            "stage_2": True,
        },
        sec_analyze={"filing_date": "2024-03-01", "guidance_signal": "negative"},
    )
    assert signal["trend_stack_ok"] is False
    assert signal["vcp_present"] is True
    assert signal["business_as_of"] == "2024-03-01"
    assert "stage_2" not in signal


def test_committee_writes_debate_score_without_changing_qual_raw(monkeypatch) -> None:
    from core.debate_committee import maybe_attach_committee

    monkeypatch.setattr("config.get_debate_committee_mode", lambda skill_dir=None: "shadow")
    monkeypatch.setattr("config.get_debate_committee_max_per_scan", lambda skill_dir=None: 1)
    monkeypatch.setattr("config.get_tab_agent_debate_enabled", lambda skill_dir=None: False)
    monkeypatch.setattr("config.get_debate_grade_mode", lambda skill_dir=None: "shadow")

    def _payload(scores: dict[str, float | None]) -> str:
        return json.dumps(
            {
                "seats": [{"name": name, "score": score, "claim": name} for name, score in scores.items()],
                "chair": "Round complete.",
            }
        )

    calls = {"n": 0}

    def complete(_prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return _payload({"trend": 100, "setup": 0, "risk": 10, "skeptic": 0, "business": 95})
        return _payload({"trend": 100, "setup": 0, "risk": 80, "skeptic": 0, "business": 95})

    signal = _passing_signal()
    attach_debate_grade(signal, diagnostics={}, skill_dir=None)
    raw = signal["debate_grade"]["qual_raw"]
    assert raw > 65
    diagnostics: dict = {}
    maybe_attach_committee(signal, diagnostics=diagnostics, skill_dir=None, complete=complete, force=True)
    grade = signal["debate_grade"]
    assert grade["qual_raw"] == raw
    assert signal["signal_score"] == 62.0
    assert grade["debate_score"] == 12.5
    assert grade["disagreement_source"] == "votes"
    assert grade["qual_business"] is None
    business = next(seat for seat in grade["committee"]["seats"] if seat["seat"] == "business")
    assert business["score"] is None
    risk = next(seat for seat in grade["committee"]["seats"] if seat["seat"] == "risk")
    assert risk["score"] == 25.0
    assert grade["qual_score"] <= 64.99
    assert grade["qual_letter"] in {"C", "D", "F"}
    assert diagnostics["debate_committee_ran"] == 1

    second = _passing_signal(ticker="MSFT")
    attach_debate_grade(second, diagnostics={}, skill_dir=None)
    maybe_attach_committee(second, diagnostics=diagnostics, skill_dir=None, complete=complete, force=True)
    assert second["debate_grade"].get("debate_score") is None

    off_diag: dict = {}
    monkeypatch.setattr("config.get_debate_committee_mode", lambda skill_dir=None: "off")
    maybe_attach_committee(signal, diagnostics=off_diag, skill_dir=None, complete=complete, force=True)
    assert off_diag.get("debate_committee_ran", 0) == 0


def test_skip_vs_kept_profit_factor() -> None:
    from core.scoring_metrics import render_debate_grade_markdown

    rows = []
    for i in range(35):
        rows.append(
            {
                "signal_score": float(i % 5),
                "qual_raw": float(i),
                "qual_asymmetry": float(20 + (i % 3) * 30),
                "debate_grade_would_skip": True,
                "net_return": -0.04 if i < 20 else 0.01,
                "qual_abstain": False,
            }
        )
    for i in range(35):
        rows.append(
            {
                "signal_score": float(i % 5),
                "qual_raw": float(40 + i),
                "qual_asymmetry": float(20 + (i % 3) * 30),
                "debate_grade_would_skip": False,
                "net_return": 0.05 if i < 25 else -0.01,
                "qual_abstain": False,
            }
        )
    block = debate_grade_report(pd.DataFrame(rows), ret_col="net_return", min_rows=50, min_bucket=30)
    assert block["skipped"] is False
    assert block["asymmetry_spread"]["no_spread"] is False
    skip = block["skip_vs_kept"]["would_skip"]
    kept = block["skip_vs_kept"]["kept"]
    assert skip["insufficient"] is False
    assert kept["insufficient"] is False
    assert kept["profit_factor"] > skip["profit_factor"]
    text = "\n".join(render_debate_grade_markdown(block))
    assert "Would-skip versus kept" in text
    assert "not an entry filter" in text


def test_asymmetry_distance_varies_without_changing_qual_raw(monkeypatch) -> None:
    monkeypatch.setattr("config.get_debate_grade_mode", lambda skill_dir=None: "shadow")
    tight = _passing_signal(price=108.0, sma_50=100.0, stop_pct=0.07)
    wide = _passing_signal(price=140.0, sma_50=100.0, stop_pct=0.07)
    attach_debate_grade(tight, diagnostics={}, skill_dir=None)
    attach_debate_grade(wide, diagnostics={}, skill_dir=None)
    assert tight["debate_grade"]["qual_asymmetry"] == wide["debate_grade"]["qual_asymmetry"] == 100.0
    assert tight["debate_grade"]["qual_raw"] == wide["debate_grade"]["qual_raw"]
    assert tight["debate_grade"]["qual_asymmetry_distance"] < wide["debate_grade"]["qual_asymmetry_distance"]
    assert tight["debate_grade"]["qual_raw_with_asymmetry_distance"] != tight["debate_grade"]["qual_raw"]


def test_pit_filings_ignore_dates_after_entry() -> None:
    from core.debate_grade import stamp_pit_business

    filings = [
        {"form": "10-K", "date": "2024-02-01"},
        {"form": "8-K", "date": "2024-08-01", "description": "restatement of revenue"},
    ]
    early = _passing_signal(entry_date="2024-06-01")
    stamp_pit_business(early, filings)
    assert early["business_as_of"] == "2024-02-01"
    assert early["sec_risk_tag"] == "low"

    late = _passing_signal(entry_date="2024-09-01")
    stamp_pit_business(late, filings)
    assert late["business_as_of"] == "2024-08-01"
    assert late["sec_risk_tag"] == "high"

    before = _passing_signal(entry_date="2020-01-01", forensic_flags=["beneish_manipulator"])
    stamp_pit_business(before, filings)
    assert "business_as_of" not in before


def test_committee_sample_joins_only_on_or_before_entry(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("config.get_debate_grade_mode", lambda skill_dir=None: "shadow")
    log = tmp_path / ".debate_committee_log.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ticker": "AAPL",
                        "as_of": "2026-01-10",
                        "debate_score": 40.0,
                        "qual_disagreement": 0.2,
                    }
                ),
                json.dumps(
                    {
                        "ticker": "AAPL",
                        "as_of": "2026-03-01",
                        "debate_score": 99.0,
                        "qual_disagreement": 0.9,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    signal = _passing_signal(entry_date="2026-02-01")
    attach_debate_grade(signal, diagnostics={}, skill_dir=None)
    raw = signal["debate_grade"]["qual_raw"]
    fields = _score_fields_for_trade(signal, skill_dir=tmp_path)
    assert fields["debate_score"] == 40.0
    assert fields["qual_raw"] == raw
    assert fields["disagreement_source"] == "votes"

    earlier = _passing_signal(entry_date="2025-12-01")
    attach_debate_grade(earlier, diagnostics={}, skill_dir=None)
    assert _score_fields_for_trade(earlier, skill_dir=tmp_path)["debate_score"] is None


def test_would_skip_counterfactual_has_its_own_bucket() -> None:
    from scripts.score_counterfactual_outcomes import classify_counterfactual_bucket, score_events

    assert classify_counterfactual_bucket("debate_grade_would_skip") == "would_skip"
    assert classify_counterfactual_bucket("quality_filtered") == "suppressed"
    summary = score_events(
        [
            {
                "ts": "2020-01-02T15:00:00+00:00",
                "ticker": "AAPL",
                "reason": "debate_grade_would_skip",
            }
        ],
        horizon_days=5,
        now=pd.Timestamp("2020-01-20", tz="UTC").to_pydatetime(),
        forward_fn=lambda *_args: 0.01,
    )
    assert summary["buckets"]["would_skip"]["count"] == 1
    assert summary["buckets"]["other"]["count"] == 0


def test_horizon_sleeve_grade_is_not_the_daily_vcp_checklist() -> None:
    grade = grade_from_signal(
        _passing_signal(
            entry_family="horizon",
            vcp_present=False,
            trend_stack_ok=False,
            sma200_rising=False,
            strategy_plugins=[
                {"name": "trend_breakout", "triggered": False, "raw_score": 0.0},
                {"name": "weekly_swing", "triggered": True, "raw_score": 100.0},
            ],
        )
    )
    assert grade["grade_basis"] == "sleeve"
    assert grade["qual_letter"] == "A"
    assert grade["qual_score"] == 100.0
    assert "weekly swing" in grade["chair"]


def test_stage2_grade_stays_on_the_daily_checklist() -> None:
    grade = grade_from_signal(
        _passing_signal(
            entry_family="stage2",
            strategy_plugins=[{"name": "weekly_swing", "triggered": True, "raw_score": 100.0}],
        )
    )
    assert grade.get("grade_basis") != "sleeve"
    assert grade["qual_trend"] == 100.0
