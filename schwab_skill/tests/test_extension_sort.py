"""Extension shadow sort annotates pts_52w and never retargets the live sort."""

from __future__ import annotations

from core.extension_sort import apply_extension_sort_shadow, extension_sort_block


def test_block_inverts_pts_52w() -> None:
    block = extension_sort_block({"score_components": {"pts_52w": 30.0}, "sort_score": 80.0})
    assert block is not None
    assert block["pts_52w"] == 30.0
    assert block["extension_sort_score"] == 70.0
    assert block["extension_sort_basis"] == "inv_pts_52w"
    assert "sort_score" not in block


def test_missing_pts_is_unscored() -> None:
    assert extension_sort_block({"signal_score": 60.0}) is None
    assert extension_sort_block({"pts_52w": "nope"}) is None


def test_shadow_stamps_without_changing_sort(monkeypatch) -> None:
    monkeypatch.setenv("EXTENSION_SORT_MODE", "shadow")
    monkeypatch.setenv("SCAN_LIVE_SORT_KEY", "signal_score")
    signals = [
        {"ticker": "A", "pts_52w": 10.0, "sort_score": 55.0},
        {"ticker": "B", "signal_score": 90.0, "sort_score": 90.0},
    ]
    diag: dict = {}
    apply_extension_sort_shadow(signals, diag, skill_dir=None)
    assert signals[0]["extension_sort_score"] == 90.0
    assert signals[0]["trust_score"] == 75.0
    assert signals[0]["trust_score_basis"] == "inv_pts_52w"
    assert signals[0]["trust_score_mode"] == "shadow"
    assert signals[0]["equity_confidence"] == 9
    assert signals[0]["equity_confidence_registry"] == "equity_ledger_v1"
    assert "factor_ledger" in signals[0]
    assert signals[0]["sort_score"] == 55.0
    assert "extension_sort_score" not in signals[1]
    assert diag["extension_sort_mode"] == "shadow"
    assert diag["extension_sort_live_sort_key"] == "signal_score"
    assert diag["extension_sort_scored"] == 1
    assert diag["extension_sort_unscored"] == 1


def test_live_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("EXTENSION_SORT_MODE", "live")
    signals = [{"ticker": "A", "pts_52w": 12.0, "sort_score": 40.0}]
    diag: dict = {}
    apply_extension_sort_shadow(signals, diag, skill_dir=None)
    assert "extension_sort_score" not in signals[0]
    assert signals[0]["sort_score"] == 40.0
    assert diag["extension_sort_skipped"] == "live_not_approved"
    assert diag["extension_sort_scored"] == 0


def test_off_is_a_noop(monkeypatch) -> None:
    monkeypatch.setenv("EXTENSION_SORT_MODE", "off")
    signals = [{"ticker": "A", "pts_52w": 12.0, "sort_score": 40.0}]
    diag: dict = {}
    apply_extension_sort_shadow(signals, diag, skill_dir=None)
    assert signals[0] == {"ticker": "A", "pts_52w": 12.0, "sort_score": 40.0}
    assert diag["extension_sort_mode"] == "off"
    assert diag["extension_sort_scored"] == 0
