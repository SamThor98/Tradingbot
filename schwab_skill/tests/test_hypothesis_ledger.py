from pathlib import Path

from hypothesis_ledger import (
    append_hypotheses_from_canary_sleeve,
    append_hypotheses_from_signals,
    append_hypothesis,
    backfill_s1_from_scan_artifact,
    fingerprint_from_mapping,
    load_ledger_data,
    summarize_scored_hypotheses,
)


def test_append_and_summarize_roundtrip(tmp_path: Path) -> None:
    rid = append_hypothesis(
        {
            "ticker": "ZZZ",
            "source": "advisory",
            "strategy_or_model_id": "test_model",
            "input_fingerprint": fingerprint_from_mapping({"x": 1}),
            "prediction": {"direction": "long", "entry_reference_px": 100.0},
            "outcomes": {"5": {"thesis_hit": True, "return_pct": 2.0}},
        },
        skill_dir=tmp_path,
    )
    assert rid
    s = summarize_scored_hypotheses(tmp_path)
    assert s["ledger_records"] >= 1
    assert "advisory" in s["by_source"]


def test_append_hypotheses_from_signals_dedupes_same_day(tmp_path: Path) -> None:
    signals = [
        {"ticker": "AAA", "price": 10.0, "signal_score": 40, "mirofish_conviction": 30},
        {"ticker": "BBB", "price": 12.0, "signal_score": 50, "mirofish_conviction": 40},
    ]
    first = append_hypotheses_from_signals(
        signals,
        tmp_path,
        et_date="2026-08-25",
        scan_at="2026-08-25T13:30:00+00:00",
    )
    second = append_hypotheses_from_signals(
        signals,
        tmp_path,
        et_date="2026-08-25",
        scan_at="2026-08-25T18:00:00+00:00",
    )
    assert first["appended"] == 2
    assert second["appended"] == 0
    assert second["skipped"] == 2
    data = load_ledger_data(tmp_path)
    assert len(data["records"]) == 2


def test_append_hypotheses_from_canary_sleeve_is_paper_s1(tmp_path: Path) -> None:
    diag = {
        "pead_primary_shadow_names": [
            {
                "ticker": "PEAD1",
                "edge_score": 88.0,
                "pead_beat": True,
                "pead_surprise_pct": 0.06,
                "capacity_top_n": True,
                "executable": False,
            },
            {
                "ticker": "SKIPME",
                "edge_score": 10.0,
                "pead_beat": True,
                "pead_surprise_pct": 0.01,
                "capacity_top_n": False,
                "executable": False,
            },
        ],
        "pead_primary_capacity_rank_arm": "top5_by_edge_score",
        "pead_primary_capacity_rank_top_n": 5,
        "data_quality": "ok",
    }
    first = append_hypotheses_from_canary_sleeve(
        diag,
        tmp_path,
        et_date="2026-09-03",
        scan_at="2026-09-03T14:30:00+00:00",
    )
    second = append_hypotheses_from_canary_sleeve(
        diag,
        tmp_path,
        et_date="2026-09-03",
        scan_at="2026-09-03T15:00:00+00:00",
    )
    assert first["appended"] == 1
    assert second["appended"] == 0
    records = load_ledger_data(tmp_path)["records"]
    assert len(records) == 1
    rec = records[0]
    assert rec["sleeve_id"] == "S1"
    assert rec["source"] == "pead_canary"
    assert rec["raw_context"]["executable"] is False
    assert rec["raw_context"]["allow_live"] is False
    assert rec["raw_context"]["pead_surprise_large"] is False
    assert rec["raw_context"]["pead_extension_drop_would_drop"] is None
    assert rec["ticker"] == "PEAD1"


def test_canary_harvest_refuses_allow_live_block(tmp_path: Path, monkeypatch) -> None:
    def _live_block(_diag, shadow_names=None):
        return {"executable": False, "allow_live": True, "names": [{"ticker": "X"}]}

    monkeypatch.setattr(
        "core.pead_primary_shadow_compare.build_pead_canary_sleeve_block",
        _live_block,
    )
    out = append_hypotheses_from_canary_sleeve({}, tmp_path, et_date="2026-09-03")
    assert out["appended"] == 0
    assert out["errors"] == 1
    assert load_ledger_data(tmp_path)["records"] == []


def test_backfill_s1_from_scan_artifact_skips_stale(tmp_path: Path) -> None:
    payload = {
        "summary": {"at": "2026-07-28T00:48:57+00:00", "data_quality": "stale"},
        "diagnostics_pead": {
            "data_quality": "stale",
            "pead_primary_shadow_names": [
                {
                    "ticker": "STALE1",
                    "edge_score": 9.0,
                    "pead_beat": True,
                    "pead_surprise_pct": 0.02,
                    "capacity_top_n": True,
                    "executable": False,
                }
            ],
        },
    }
    out = backfill_s1_from_scan_artifact(payload, tmp_path, require_dq_ok=True)
    assert out["appended"] == 0
    assert load_ledger_data(tmp_path)["records"] == []


def test_backfill_s1_from_scan_artifact_ok_session(tmp_path: Path) -> None:
    payload = {
        "summary": {"at": "2026-07-28T15:27:00+00:00", "data_quality": "ok"},
        "diagnostics_pead": {
            "data_quality": "ok",
            "pead_primary_capacity_rank_top_n": 5,
            "pead_primary_shadow_names": [
                {
                    "ticker": "ENSG",
                    "edge_score": 20.0,
                    "pead_beat": True,
                    "pead_surprise_pct": 0.04,
                    "capacity_top_n": True,
                    "executable": False,
                }
            ],
        },
    }
    out = backfill_s1_from_scan_artifact(payload, tmp_path, require_dq_ok=True)
    assert out["appended"] == 1
    rec = load_ledger_data(tmp_path)["records"][0]
    assert rec["sleeve_id"] == "S1"
    assert rec["ticker"] == "ENSG"
    assert rec["created_at"].startswith("2026-07-28T15:27:00")
