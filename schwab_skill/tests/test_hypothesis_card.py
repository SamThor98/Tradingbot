from __future__ import annotations

import json
import sys
from pathlib import Path

from core.hypothesis_card import (
    augment_campaign_plan,
    executable_argv,
    load_cards,
    score_all_cards,
    score_card,
    settle_finished_queue_cards,
    validate_card_payload,
)
from core.weekend_backtest_campaign import fingerprints_from_runs, select_remaining_campaign_jobs

SKILL_DIR = Path(__file__).resolve().parents[1]
SHIPPED = {
    "cap37-capped-replay",
    "liquid-funding-z90",
    "sort-key-shadow",
    "weekly-vcp-score-46",
}


def _artifact_card(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "card_id": "sample-replay",
        "status": "proposed",
        "theory_name": "sample replay",
        "question": "Does the sample artifact clear the floors?",
        "single_change": {"field": "bare_run_id", "baseline": "control", "candidate": "bare"},
        "floors": {"pf_mean": 1.2, "worst_era_pf": 1.0, "min_trades": 10},
        "decision": "clears_floors",
        "kind": "artifact",
        "command": ["python", "scripts/phase2_edge_audit.py", "--out-prefix", "sample"],
        "artifact": "validation_artifacts/sample.json",
        "metrics": {
            "pf_mean": "bare.pf_mean",
            "worst_era_pf": "bare.worst_era_pf",
            "n_trades": "bare.n_total",
            "verdict": "verdict",
        },
    }
    payload.update(overrides)
    return payload


def _write_card(directory: Path, payload: dict[str, object]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    card_id = str(payload["card_id"])
    (directory / f"{card_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_shipped_cards_validate() -> None:
    cards, issues = load_cards(SKILL_DIR)
    assert issues == []
    assert {card.card_id for card in cards} == SHIPPED
    assert {card.card_id for card in cards if card.status == "ready"} == set()
    assert {card.card_id for card in cards if card.status == "proposed"} == SHIPPED


def test_weaker_floor_and_disallowed_command_are_rejected() -> None:
    weak = _artifact_card(floors={"pf_mean": 1.1, "worst_era_pf": 1.0})
    assert any(
        "weaker than the ledger" in item
        for item in validate_card_payload(weak, filename="sample-replay.json", pf_floor=1.2, worst_floor=1.0)
    )
    unsafe = _artifact_card(command=["python", "scripts/promotion_ledger.py"])
    assert any(
        "not an allowed backtest command" in item
        for item in validate_card_payload(unsafe, filename="sample-replay.json", pf_floor=1.2, worst_floor=1.0)
    )
    escaped = _artifact_card(artifact="validation_artifacts/../../.env")
    assert any(
        "relative path" in item
        for item in validate_card_payload(escaped, filename="sample-replay.json", pf_floor=1.2, worst_floor=1.0)
    )


def test_score_copies_artifact_numbers_and_never_promotes(tmp_path: Path) -> None:
    artifact = {
        "generated_at": "2026-10-06T00:00:00+00:00",
        "verdict": "proceed",
        "bare": {"pf_mean": 1.25, "worst_era_pf": 1.05, "n_total": 40},
    }
    (tmp_path / "validation_artifacts").mkdir()
    (tmp_path / "validation_artifacts" / "sample.json").write_text(json.dumps(artifact), encoding="utf-8")
    _write_card(tmp_path / "research" / "hypothesis_cards", _artifact_card())
    summary = score_all_cards(tmp_path)
    assert summary["errors"] == []
    assert summary["scored"] == 1
    result = json.loads((tmp_path / "artifacts" / "hypothesis_cards" / "sample-replay.json").read_text())
    assert result["metrics"] == {
        "pf_mean": 1.25,
        "worst_era_pf": 1.05,
        "n_trades": 40,
        "verdict": "proceed",
    }
    assert result["gate"] == "clears_floors"
    assert result["promote"] is False
    assert result["source_generated_at"] == "2026-10-06T00:00:00+00:00"


def test_score_quote_only_and_missing_artifact(tmp_path: Path) -> None:
    payload = _artifact_card(
        card_id="quote-me",
        decision="quote_only",
        floors={"pf_mean": 1.2, "worst_era_pf": 1.0},
        metrics={
            "pf_mean": "arms.book.pf_mean",
            "worst_era_pf": "arms.book.worst_era_pf",
            "n_trades": "arms.book.n",
            "verdict": "verdict",
        },
        also_quote={"promote_sort": "promote_sort"},
    )
    artifact = {
        "verdict": "shadow_ahead_intervals_overlap",
        "promote_sort": False,
        "arms": {"book": {"pf_mean": 1.01, "worst_era_pf": 0.9, "n": 12}},
    }
    (tmp_path / "validation_artifacts").mkdir()
    (tmp_path / "validation_artifacts" / "sample.json").write_text(json.dumps(artifact), encoding="utf-8")
    cards_dir = tmp_path / "research" / "hypothesis_cards"
    _write_card(cards_dir, payload)
    cards, issues = load_cards(tmp_path)
    assert issues == []
    quoted = score_card(cards[0], tmp_path)
    assert quoted["gate"] == "quoted"
    assert quoted["clears_numeric_floors"] is False
    assert quoted["quoted"]["promote_sort"] is False
    assert quoted["promote"] is False

    missing = _artifact_card(card_id="absent-replay", artifact="validation_artifacts/missing.json")
    _write_card(cards_dir, missing)
    summary = score_all_cards(tmp_path)
    assert summary["artifact_missing"] == 1
    absent = json.loads((tmp_path / "artifacts" / "hypothesis_cards" / "absent-replay.json").read_text())
    assert absent["gate"] == "artifact_missing"
    assert absent["metrics"] is None
    assert absent["promote"] is False


def test_bool_and_non_finite_metrics_do_not_clear(tmp_path: Path) -> None:
    artifact = {"verdict": "proceed", "bare": {"pf_mean": True, "worst_era_pf": 1.2, "n_total": 10}}
    (tmp_path / "validation_artifacts").mkdir()
    (tmp_path / "validation_artifacts" / "sample.json").write_text(json.dumps(artifact), encoding="utf-8")
    _write_card(tmp_path / "research" / "hypothesis_cards", _artifact_card())
    cards, issues = load_cards(tmp_path)
    assert issues == []
    result = score_card(cards[0], tmp_path)
    assert result["gate"] == "metric_missing"
    assert result["promote"] is False


def test_ready_queue_card_is_prepended_and_not_duplicated(tmp_path: Path) -> None:
    payload = json.loads((SKILL_DIR / "research" / "hypothesis_cards" / "weekly-vcp-score-46.json").read_text())
    payload["status"] = "ready"
    payload["card_id"] = "weekly-vcp-score-47"
    payload["single_change"]["candidate"] = 47
    payload["spec"]["overrides"]["quality_min_signal_score"] = 47
    payload["spec"]["theory_name"] = "card weekly_vcp sp1500 quality_min_signal_score=47 5y"
    payload["theory_name"] = payload["spec"]["theory_name"]
    _write_card(tmp_path / "research" / "hypothesis_cards", payload)
    plan, notes = augment_campaign_plan([], tmp_path)
    assert plan[0]["source"] == "hypothesis_card"
    assert plan[0]["card_id"] == "weekly-vcp-score-47"
    assert plan[0]["spec"]["overrides"]["skip_mirofish"] is True
    assert any("queued ahead" in note for note in notes)

    again, notes_again = augment_campaign_plan(plan, tmp_path)
    assert len(again) == len(plan)
    assert any("already in the plan" in note for note in notes_again)

    remaining = select_remaining_campaign_jobs(
        [
            {"fingerprint": "short", "window_years": 2, "spec": {"universe_preset": "nasdaq100"}},
            plan[0],
        ],
        set(),
        horizon_only=True,
    )
    assert remaining[0]["card_id"] == "weekly-vcp-score-47"


def test_dashboard_run_matches_ready_card_fingerprint(tmp_path: Path) -> None:
    payload = json.loads((SKILL_DIR / "research" / "hypothesis_cards" / "weekly-vcp-score-46.json").read_text())
    payload["status"] = "ready"
    payload["card_id"] = "weekly-vcp-score-47"
    payload["single_change"]["candidate"] = 47
    payload["spec"]["overrides"]["quality_min_signal_score"] = 47
    payload["spec"]["theory_name"] = "card weekly_vcp sp1500 quality_min_signal_score=47 5y"
    payload["theory_name"] = payload["spec"]["theory_name"]
    _write_card(tmp_path / "research" / "hypothesis_cards", payload)
    plan, _notes = augment_campaign_plan([], tmp_path)
    card = next(row for row in plan if row["card_id"] == "weekly-vcp-score-47")
    runs = [
        {"spec": card["spec"], "status": "queued"},
        {
            "spec": {"theory_name": "manual idea", "start_date": "2024-01-01", "end_date": "2024-06-01"},
            "status": "done",
        },
    ]
    found = fingerprints_from_runs(runs, plan_fingerprints={card["fingerprint"]})
    assert found == {card["fingerprint"]}


def _queue_card(card_id: str, *, status: str, candidate: int) -> dict[str, object]:
    payload = json.loads((SKILL_DIR / "research" / "hypothesis_cards" / "weekly-vcp-score-46.json").read_text())
    payload["status"] = status
    payload["card_id"] = card_id
    payload["single_change"]["candidate"] = candidate
    payload["spec"]["overrides"]["quality_min_signal_score"] = candidate
    theory = f"card weekly_vcp sp1500 quality_min_signal_score={candidate} 5y"
    payload["spec"]["theory_name"] = theory
    payload["theory_name"] = theory
    return payload


def test_network_status_counts_clear_missing_and_waiting(tmp_path: Path) -> None:
    artifact = {
        "generated_at": "2026-10-06T00:00:00+00:00",
        "verdict": "proceed",
        "bare": {"pf_mean": 1.25, "worst_era_pf": 1.05, "n_total": 40},
    }
    (tmp_path / "validation_artifacts").mkdir()
    (tmp_path / "validation_artifacts" / "sample.json").write_text(json.dumps(artifact), encoding="utf-8")
    cards_dir = tmp_path / "research" / "hypothesis_cards"
    _write_card(cards_dir, _artifact_card())
    _write_card(cards_dir, _artifact_card(card_id="absent-replay", artifact="validation_artifacts/missing.json"))
    _write_card(cards_dir, _queue_card("weekly-vcp-score-47", status="ready", candidate=47))
    summary = score_all_cards(tmp_path)
    status = summary["network_status"]
    assert status["filed"] == {"proposed": 2, "ready": 1, "settled": 0, "discarded": 0}
    assert status["scored_with_artifact"] == 1
    assert status["floors_cleared"] == 1
    assert status["floors_missed"] == 0
    assert status["queue_waiting"] == 1
    assert status["promote_not_false"] == 0
    document = json.loads((tmp_path / "artifacts" / "hypothesis_cards" / "network_status.json").read_text())
    assert document["promote_not_false"] == 0
    assert {row["card_id"] for row in document["rows"]} == {
        "sample-replay",
        "absent-replay",
        "weekly-vcp-score-47",
    }


def test_settled_card_is_not_prepended(tmp_path: Path) -> None:
    _write_card(
        tmp_path / "research" / "hypothesis_cards",
        _queue_card("weekly-vcp-score-47", status="settled", candidate=47),
    )
    plan, notes = augment_campaign_plan([], tmp_path)
    assert plan == []
    assert any("none ready" in note for note in notes)


def test_finished_run_settles_ready_card_and_blocks_requeue(tmp_path: Path) -> None:
    payload = _queue_card("weekly-vcp-score-47", status="ready", candidate=47)
    _write_card(tmp_path / "research" / "hypothesis_cards", payload)
    run = {
        "id": "run-1",
        "status": "success",
        "created_at": "2026-10-16T12:00:00+00:00",
        "spec": payload["spec"],
        "result_summary": {"profit_factor_net": 1.4, "total_trades": 12},
    }
    settled = settle_finished_queue_cards(tmp_path, [run])
    assert settled == ["weekly-vcp-score-47"]
    stored = json.loads((tmp_path / "research" / "hypothesis_cards" / "weekly-vcp-score-47.json").read_text())
    assert stored["status"] == "settled"
    result = json.loads((tmp_path / "artifacts" / "hypothesis_cards" / "weekly-vcp-score-47.json").read_text())
    assert result["promote"] is False
    assert result["gate"] == "settled_success"
    assert result["clears_numeric_floors"] is None
    assert result["metrics"] is None
    assert result["quoted"]["profit_factor_net"] == 1.4
    plan, _notes = augment_campaign_plan([], tmp_path)
    assert plan == []
    assert settle_finished_queue_cards(tmp_path, [run]) == []
    summary = score_all_cards(tmp_path)
    assert summary["network_status"]["filed"]["settled"] == 1
    assert summary["network_status"]["floors_cleared"] == 0
    assert summary["network_status"]["promote_not_false"] == 0
    kept = json.loads((tmp_path / "artifacts" / "hypothesis_cards" / "weekly-vcp-score-47.json").read_text())
    assert kept["gate"] == "settled_success"
    assert kept["quoted"]["total_trades"] == 12


def test_queued_or_failed_run_does_not_clear_floors(tmp_path: Path) -> None:
    payload = _queue_card("weekly-vcp-score-47", status="ready", candidate=47)
    _write_card(tmp_path / "research" / "hypothesis_cards", payload)
    waiting = {"id": "run-q", "status": "queued", "spec": payload["spec"]}
    assert settle_finished_queue_cards(tmp_path, [waiting]) == []
    stored = json.loads((tmp_path / "research" / "hypothesis_cards" / "weekly-vcp-score-47.json").read_text())
    assert stored["status"] == "ready"

    failed = {"id": "run-f", "status": "failed", "spec": payload["spec"], "created_at": "2026-10-16T13:00:00+00:00"}
    assert settle_finished_queue_cards(tmp_path, [waiting, failed]) == ["weekly-vcp-score-47"]
    result = json.loads((tmp_path / "artifacts" / "hypothesis_cards" / "weekly-vcp-score-47.json").read_text())
    assert result["gate"] == "settled_failed"
    assert result["promote"] is False
    assert result["clears_numeric_floors"] is None


def test_executable_argv_uses_this_interpreter() -> None:
    argv = executable_argv(["python", "scripts/phase2_edge_audit.py", "--out-prefix", "sample"])
    assert argv[0] == sys.executable
    assert argv[1:] == ["scripts/phase2_edge_audit.py", "--out-prefix", "sample"]
