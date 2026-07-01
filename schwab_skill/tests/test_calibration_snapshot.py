from __future__ import annotations

import json
from pathlib import Path

from webapp.calibration_snapshot import build_calibration_snapshot


def test_build_calibration_snapshot_empty(tmp_path: Path) -> None:
    snap = build_calibration_snapshot(tmp_path)
    assert snap.get("self_study") is None
    assert snap.get("hypothesis_ledger") is None
    assert snap.get("empty") is True
    assert "hint" in snap


def test_build_calibration_snapshot_with_files(tmp_path: Path) -> None:
    (tmp_path / ".self_study.json").write_text(
        json.dumps(
            {
                "suggested_min_conviction": 40,
                "round_trips_count": 3,
                "win_rate": 58.3,
                "avg_return_pct": 2.41,
                "last_run": "2026-06-01T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".hypothesis_ledger.json").write_text(
        json.dumps([{"source": "signal_scanner", "ticker": "AAPL"}]),
        encoding="utf-8",
    )
    snap = build_calibration_snapshot(tmp_path)
    assert snap["self_study"]["suggested_min_conviction"] == 40
    assert snap["self_study"]["round_trips_count"] == 3
    assert snap["self_study"]["round_trips"] == 3
    assert snap["self_study"]["win_rate"] == 58.3
    assert snap.get("empty") is not True
    assert snap["hypothesis_ledger"]["row_count"] == 1


def test_build_calibration_snapshot_round_trips_legacy_key(tmp_path: Path) -> None:
    (tmp_path / ".self_study.json").write_text(
        json.dumps({"round_trips": 7}),
        encoding="utf-8",
    )
    snap = build_calibration_snapshot(tmp_path)
    assert snap["self_study"]["round_trips_count"] == 7
    assert snap["self_study"]["round_trips"] == 7


def test_build_calibration_snapshot_with_hypothesis_calibration(tmp_path: Path) -> None:
    (tmp_path / ".self_study.json").write_text(
        json.dumps(
            {
                "suggested_min_conviction": 40,
                "round_trips_count": 12,
                "win_rate": 58.3,
                "hypothesis_calibration": {
                    "by_source": {
                        "advisory": {
                            "scored_samples": 14,
                            "hit_rate": 0.62,
                            "mean_return_pct": 1.85,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    snap = build_calibration_snapshot(tmp_path)
    assert snap["hypothesis_calibration"]["by_source"]["advisory"]["hit_rate"] == 0.62
