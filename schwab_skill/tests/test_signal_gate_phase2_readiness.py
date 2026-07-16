from __future__ import annotations

from scripts.validate_signal_gate_phase2_readiness import (
    BARE_HALT_VERDICTS,
    BARE_OK_VERDICTS,
    _normalize_verdict,
)


def test_phase2_current_verdicts_classify_consistently() -> None:
    assert _normalize_verdict("PROCEED") in BARE_OK_VERDICTS
    assert _normalize_verdict("ITERATE") in BARE_OK_VERDICTS
    assert _normalize_verdict("iterate_with_caution") in BARE_OK_VERDICTS
    assert _normalize_verdict("halt_fix_signal_first") in BARE_HALT_VERDICTS
    assert _normalize_verdict("halt_insufficient_data") in BARE_HALT_VERDICTS


def test_phase2_unknown_verdict_is_not_actionable() -> None:
    verdict = _normalize_verdict("unexpected")
    assert verdict not in BARE_OK_VERDICTS
    assert verdict not in BARE_HALT_VERDICTS
