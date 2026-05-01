"""Tests for the adaptive-regime participation floor and hybrid alpha policy.

Two invariants:

1. The regime counterfactual guardrail rejects configs that completely
   suppress trade flow in weak regimes (preferring size-down + stricter
   quality over full shutdown).
2. The hybrid alpha policy validator catches incoherent config (e.g.
   non-monotonic sizing multipliers, zero LOW multiplier).

We intentionally test the pure helper exposed by the validator script,
not by spawning subprocesses, so the tests stay hermetic.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _make_skill_dir(tmp_path: Path, env_lines: list[str]) -> Path:
    (tmp_path / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    # Ensure config cache picks up the new file.
    import config

    config.clear_env_cache()
    return tmp_path


def test_hybrid_alpha_policy_passes_with_default_compliant_config(tmp_path: Path) -> None:
    skill_dir = _make_skill_dir(
        tmp_path,
        [
            "QUALITY_MIN_SIGNAL_SCORE=55",
            "REGIME_V2_SIZE_MULT_LOW=0.4",
            "REGIME_V2_SIZE_MULT_MED=0.7",
            "REGIME_V2_SIZE_MULT_HIGH=1.0",
            "SIGNAL_TOP_N=8",
        ],
    )
    from validate_hybrid_alpha_policy import evaluate_hybrid_alpha_policy

    passed, reasons, snapshot = evaluate_hybrid_alpha_policy(
        min_quality_floor=40,
        max_signal_top_n=15,
        skill_dir=skill_dir,
    )
    assert passed is True
    assert "hybrid_alpha_policy_coherent" in reasons
    assert snapshot["QUALITY_MIN_SIGNAL_SCORE"] == 55


def test_hybrid_alpha_policy_rejects_zero_low_multiplier(tmp_path: Path) -> None:
    """A 0.0 LOW multiplier means full shutdown in weak regimes — that is
    explicitly the *non-adaptive* behaviour we forbid."""
    skill_dir = _make_skill_dir(
        tmp_path,
        [
            "QUALITY_MIN_SIGNAL_SCORE=55",
            "REGIME_V2_SIZE_MULT_LOW=0.0",
            "REGIME_V2_SIZE_MULT_MED=0.7",
            "REGIME_V2_SIZE_MULT_HIGH=1.0",
            "SIGNAL_TOP_N=8",
        ],
    )
    from validate_hybrid_alpha_policy import evaluate_hybrid_alpha_policy

    passed, reasons, _ = evaluate_hybrid_alpha_policy(
        min_quality_floor=40,
        max_signal_top_n=15,
        skill_dir=skill_dir,
    )
    assert passed is False
    assert any(r.startswith("low_regime_size_multiplier_disables_participation") for r in reasons)


def test_hybrid_alpha_policy_rejects_non_monotonic_sizing(tmp_path: Path) -> None:
    skill_dir = _make_skill_dir(
        tmp_path,
        [
            "QUALITY_MIN_SIGNAL_SCORE=55",
            "REGIME_V2_SIZE_MULT_LOW=0.9",
            "REGIME_V2_SIZE_MULT_MED=0.5",
            "REGIME_V2_SIZE_MULT_HIGH=1.0",
            "SIGNAL_TOP_N=8",
        ],
    )
    from validate_hybrid_alpha_policy import evaluate_hybrid_alpha_policy

    passed, reasons, _ = evaluate_hybrid_alpha_policy(
        min_quality_floor=40,
        max_signal_top_n=15,
        skill_dir=skill_dir,
    )
    assert passed is False
    assert any(r.startswith("size_multipliers_not_monotonic") for r in reasons)


def test_hybrid_alpha_policy_rejects_quality_floor_too_low(tmp_path: Path) -> None:
    skill_dir = _make_skill_dir(
        tmp_path,
        [
            "QUALITY_MIN_SIGNAL_SCORE=20",
            "REGIME_V2_SIZE_MULT_LOW=0.4",
            "REGIME_V2_SIZE_MULT_MED=0.7",
            "REGIME_V2_SIZE_MULT_HIGH=1.0",
            "SIGNAL_TOP_N=8",
        ],
    )
    from validate_hybrid_alpha_policy import evaluate_hybrid_alpha_policy

    passed, reasons, _ = evaluate_hybrid_alpha_policy(
        min_quality_floor=40,
        max_signal_top_n=15,
        skill_dir=skill_dir,
    )
    assert passed is False
    assert any(r.startswith("quality_floor_too_low") for r in reasons)


def test_hybrid_alpha_policy_caps_signal_top_n_to_avoid_dilution(tmp_path: Path) -> None:
    skill_dir = _make_skill_dir(
        tmp_path,
        [
            "QUALITY_MIN_SIGNAL_SCORE=55",
            "REGIME_V2_SIZE_MULT_LOW=0.4",
            "REGIME_V2_SIZE_MULT_MED=0.7",
            "REGIME_V2_SIZE_MULT_HIGH=1.0",
            "SIGNAL_TOP_N=20",
        ],
    )
    from validate_hybrid_alpha_policy import evaluate_hybrid_alpha_policy

    passed, reasons, _ = evaluate_hybrid_alpha_policy(
        min_quality_floor=40,
        max_signal_top_n=15,
        skill_dir=skill_dir,
    )
    assert passed is False
    assert any(r.startswith("signal_top_n_too_high") for r in reasons)
