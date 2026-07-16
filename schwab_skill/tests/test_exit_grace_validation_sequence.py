from __future__ import annotations

from scripts.run_exit_grace_validation_sequence import _audit_inputs_ready


def test_edge_audit_preflight_requires_trade_and_era_coverage() -> None:
    assert _audit_inputs_ready(
        control_trades=16_433,
        control_eras=5,
        bare_trades=16_423,
        bare_eras=5,
    )
    assert not _audit_inputs_ready(
        control_trades=16_433,
        control_eras=5,
        bare_trades=86,
        bare_eras=1,
    )


def test_edge_audit_preflight_rejects_too_few_trades() -> None:
    assert not _audit_inputs_ready(
        control_trades=49,
        control_eras=5,
        bare_trades=16_423,
        bare_eras=5,
    )
