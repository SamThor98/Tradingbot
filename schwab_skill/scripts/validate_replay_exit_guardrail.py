#!/usr/bin/env python3
"""Validate Schwab replay exit-overlay evidence for exit grace promotion.

Reads ``validation_artifacts/replay_exit_overlay_<run_id>.json`` (produced by
``scripts/replay_exit_overlay.py``) and asserts that the exit grace profile
materially improves replay PF vs the baseline legacy exit path.

This script is offline — it does not call Schwab. Regenerate the artifact with
``replay_exit_overlay.py --data-provider schwab`` when trade chunks change.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"

DEFAULT_BASELINE = "baseline_legacy"
DEFAULT_GRACE = "exit_grace_t15_h40"


def _load_replay(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"missing replay artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _profile_summary(payload: dict[str, Any], profile_id: str) -> dict[str, Any] | None:
    profiles = payload.get("profiles") or {}
    row = profiles.get(profile_id)
    return row if isinstance(row, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate replay exit-overlay guardrails.")
    parser.add_argument("--run-id", default="control_legacy_aug", help="Augmented chunk run id.")
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument("--grace", default=DEFAULT_GRACE)
    parser.add_argument(
        "--min-pf-delta",
        type=float,
        default=0.40,
        help="Minimum grace minus baseline PF mean required to pass.",
    )
    parser.add_argument(
        "--min-grace-pf",
        type=float,
        default=1.0,
        help="Minimum grace profile PF mean required to pass.",
    )
    parser.add_argument(
        "--min-replayed",
        type=int,
        default=100,
        help="Minimum replayed trades required per profile.",
    )
    args = parser.parse_args()

    artifact = ARTIFACT_DIR / f"replay_exit_overlay_{args.run_id}.json"
    try:
        payload = _load_replay(artifact)
    except FileNotFoundError as exc:
        print(f"FAIL: {exc}")
        return 1

    baseline = _profile_summary(payload, args.baseline)
    grace = _profile_summary(payload, args.grace)
    if baseline is None or grace is None:
        print(
            f"FAIL: missing profiles in {artifact.name} "
            f"(baseline={args.baseline!r}, grace={args.grace!r})",
        )
        return 1

    base_pf = float(baseline.get("pf_mean") or 0.0)
    grace_pf = float(grace.get("pf_mean") or 0.0)
    pf_delta = grace_pf - base_pf
    base_n = int(baseline.get("n_replayed") or 0)
    grace_n = int(grace.get("n_replayed") or 0)

    checks = {
        "baseline_pf_mean": base_pf,
        "grace_pf_mean": grace_pf,
        "pf_delta": pf_delta,
        "baseline_n": base_n,
        "grace_n": grace_n,
        "min_pf_delta": args.min_pf_delta,
        "min_grace_pf": args.min_grace_pf,
        "passes": (
            base_n >= args.min_replayed
            and grace_n >= args.min_replayed
            and pf_delta >= args.min_pf_delta
            and grace_pf >= args.min_grace_pf
        ),
    }
    print(json.dumps(checks, indent=2))

    if not checks["passes"]:
        print(
            f"FAIL: replay guardrail not met for {artifact.name} "
            f"(delta {pf_delta:.3f}, grace PF {grace_pf:.3f})",
        )
        return 1

    print(
        f"PASS: replay exit overlay guardrail ({args.grace} vs {args.baseline}, "
        f"delta {pf_delta:+.3f})",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
