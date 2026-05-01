#!/usr/bin/env python3
"""Validate the hybrid alpha policy configuration is coherent.

The hybrid alpha policy combines:

1. **Quality floor** — ``QUALITY_MIN_SIGNAL_SCORE`` enforces a hard minimum
   signal score so weak signals never consume capacity.
2. **Dynamic sizing multipliers** — ``REGIME_V2_SIZE_MULT_{LOW,MED,HIGH}``
   adapt position size to the prevailing regime confidence bucket. Must be
   monotonically non-decreasing across LOW → MED → HIGH so weak regimes
   genuinely size down rather than up.
3. **Slot allocation** — ``SIGNAL_TOP_N`` caps how many signals can be
   acted on per cycle, which (combined with the quality floor) prevents
   weak signals from consuming disproportionate capacity.

This validator does not run a backtest. It performs a fast static check of
the active config and surfaces any inconsistency that would silently
defeat the hybrid policy. Wire it into ``validate_all.py`` so unintended
.env edits get caught at the same time as other plugin-mode validators.

Exit codes:
- 0: PASS, policy is coherent.
- 1: FAIL, one or more invariants are broken (each printed as a reason).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
sys.path.insert(0, str(SKILL_DIR))

from config import (  # noqa: E402
    get_quality_min_signal_score,
    get_regime_v2_size_mult_high,
    get_regime_v2_size_mult_low,
    get_regime_v2_size_mult_med,
    get_signal_top_n,
)


def evaluate_hybrid_alpha_policy(
    *,
    min_quality_floor: int,
    max_signal_top_n: int,
    skill_dir: Path = SKILL_DIR,
) -> tuple[bool, list[str], dict[str, float | int]]:
    """Pure helper, importable from tests.

    Returns ``(passed, reasons, snapshot)`` where ``snapshot`` is the
    config values that were checked. Reasons are human-readable; first
    token is a stable identifier suitable for dashboards.
    """
    quality_floor = int(get_quality_min_signal_score(skill_dir))
    size_low = float(get_regime_v2_size_mult_low(skill_dir))
    size_med = float(get_regime_v2_size_mult_med(skill_dir))
    size_high = float(get_regime_v2_size_mult_high(skill_dir))
    top_n = int(get_signal_top_n(skill_dir))

    reasons: list[str] = []

    if quality_floor < int(min_quality_floor):
        reasons.append(f"quality_floor_too_low:QUALITY_MIN_SIGNAL_SCORE={quality_floor}<{int(min_quality_floor)}")

    if not (0.0 <= size_low <= size_med <= size_high <= 2.0):
        reasons.append(f"size_multipliers_not_monotonic:low={size_low:.3f},med={size_med:.3f},high={size_high:.3f}")

    if size_low <= 0.0:
        # A zero/negative LOW multiplier is full-shutdown in weak regimes,
        # which violates the adaptive (size-down, not off) policy.
        reasons.append(f"low_regime_size_multiplier_disables_participation:REGIME_V2_SIZE_MULT_LOW={size_low:.3f}<=0")

    if top_n <= 0:
        reasons.append(f"signal_top_n_must_be_positive:SIGNAL_TOP_N={top_n}")
    elif top_n > int(max_signal_top_n):
        reasons.append(f"signal_top_n_too_high:SIGNAL_TOP_N={top_n}>{int(max_signal_top_n)}")

    snapshot: dict[str, float | int] = {
        "QUALITY_MIN_SIGNAL_SCORE": quality_floor,
        "REGIME_V2_SIZE_MULT_LOW": size_low,
        "REGIME_V2_SIZE_MULT_MED": size_med,
        "REGIME_V2_SIZE_MULT_HIGH": size_high,
        "SIGNAL_TOP_N": top_n,
    }
    if not reasons:
        reasons.append("hybrid_alpha_policy_coherent")
    return (len(reasons) == 1 and reasons[0] == "hybrid_alpha_policy_coherent"), reasons, snapshot


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate hybrid alpha policy config (quality floor + sizing + slots)."
    )
    parser.add_argument(
        "--min-quality-floor",
        type=int,
        default=40,
        help=(
            "Minimum acceptable QUALITY_MIN_SIGNAL_SCORE. Default 40 keeps a "
            "non-trivial quality bar regardless of upstream tuning."
        ),
    )
    parser.add_argument(
        "--max-signal-top-n",
        type=int,
        default=15,
        help=(
            "Maximum acceptable SIGNAL_TOP_N. Defaults to 15 to bound capital "
            "per cycle and keep weak signals from absorbing disproportionate "
            "capacity."
        ),
    )
    args = parser.parse_args()

    passed, reasons, snapshot = evaluate_hybrid_alpha_policy(
        min_quality_floor=int(args.min_quality_floor),
        max_signal_top_n=int(args.max_signal_top_n),
    )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out = ARTIFACT_DIR / f"hybrid_alpha_policy_{run_id}.json"
    out.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "passed": passed,
                "reasons": reasons,
                "snapshot": snapshot,
                "thresholds": {
                    "min_quality_floor": int(args.min_quality_floor),
                    "max_signal_top_n": int(args.max_signal_top_n),
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(("PASS" if passed else "FAIL") + ": hybrid alpha policy")
    for r in reasons:
        print(f"  - {r}")
    print(f"Artifact: {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
