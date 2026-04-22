from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from webapp.tasks import _collect_poisoned_chunks, _phase2_artifact_dir  # noqa: E402


def _write_chunk(path: Path, *, trades: list[dict], excluded_count: int) -> None:
    payload = {
        "era": "late_bull",
        "start": "2015-01-01",
        "end": "2017-12-31",
        "chunk_size": 120,
        "excluded_count": excluded_count,
        "trades": trades,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_collect_poisoned_chunks_flags_tiny_empty_zero_excluded(tmp_path: Path) -> None:
    run_root = tmp_path / "multi_era_chunks" / "control_legacy_aug" / "late_bull"
    bad = run_root / "chunk_0001.json"
    good_with_trades = run_root / "chunk_0002.json"
    good_with_excluded = run_root / "chunk_0003.json"
    _write_chunk(bad, trades=[], excluded_count=0)
    _write_chunk(good_with_trades, trades=[{"return": 0.1}], excluded_count=0)
    _write_chunk(good_with_excluded, trades=[], excluded_count=120)
    flagged = _collect_poisoned_chunks(tmp_path / "multi_era_chunks", ["control_legacy_aug"])
    assert bad in flagged
    assert good_with_trades not in flagged
    assert good_with_excluded not in flagged


def test_phase2_artifact_dir_uses_env_root_and_sanitizes_user() -> None:
    old = os.environ.get("SAAS_PHASE2_ARTIFACT_ROOT")
    try:
        os.environ["SAAS_PHASE2_ARTIFACT_ROOT"] = "C:/tmp/saas-phase2"
        out = _phase2_artifact_dir("user/with:bad*chars")
        assert str(out).replace("\\", "/").startswith("C:/tmp/saas-phase2/")
        assert "user_with_bad_chars" in str(out)
    finally:
        if old is None:
            os.environ.pop("SAAS_PHASE2_ARTIFACT_ROOT", None)
        else:
            os.environ["SAAS_PHASE2_ARTIFACT_ROOT"] = old
