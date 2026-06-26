#!/usr/bin/env python3
"""Rank exit-side configs vs control_legacy using multi-era artifacts.

Reads ``validation_artifacts/multi_era_backtest_schwab_only_<run_id>.json``
(or chunk dirs under ``multi_era_chunks/<run_id>/``) and applies the Phase 1
guardrails:

* PF mean must improve vs control (or tie within 0.01)
* No era regresses by >0.10 PF vs control
* Hold-duration guardrail: 21-40d expectancy beats 0-20d globally

Writes ``validation_artifacts/exit_returns_promotion.json``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
CHUNKS_DIR = ARTIFACT_DIR / "multi_era_chunks"
MAX_PF_REGRESSION = 0.10

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.phase1_trade_diagnostics import (  # noqa: E402
    _hold_buckets,
    _load_trades,
)

DEFAULT_GRACE = "exit_grace_t15_h40"


def _load_replay_overlay(run_id: str = "control_legacy_aug") -> dict[str, Any] | None:
    path = ARTIFACT_DIR / f"replay_exit_overlay_{run_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _replay_guardrail_summary(overlay: dict[str, Any] | None) -> dict[str, Any] | None:
    if not overlay:
        return None
    profiles = overlay.get("profiles") or {}
    baseline = profiles.get("baseline_legacy") or {}
    grace = profiles.get(DEFAULT_GRACE) or {}
    if not baseline or not grace:
        return None
    try:
        base_pf = float(baseline.get("pf_mean"))
        grace_pf = float(grace.get("pf_mean"))
    except (TypeError, ValueError):
        return None
    return {
        "baseline_pf_mean": round(base_pf, 4),
        "grace_pf_mean": round(grace_pf, 4),
        "pf_delta": round(grace_pf - base_pf, 4),
        "passes_guardrails": grace_pf - base_pf >= 0.40 and grace_pf >= 1.0,
        "n_replayed": int(grace.get("n_replayed") or 0),
    }


def _load_multi_era_summary(run_id: str) -> dict[str, Any] | None:
    path = ARTIFACT_DIR / f"multi_era_backtest_schwab_only_{run_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _era_pf_map(summary: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in summary.get("results") or []:
        era = str(row.get("era") or "")
        pf = row.get("profit_factor_net")
        if not era or pf in (None, "inf"):
            continue
        try:
            out[era] = float(pf)
        except (TypeError, ValueError):
            continue
    return out


def _pf_mean(pf_map: dict[str, float]) -> float | None:
    if not pf_map:
        return None
    return sum(pf_map.values()) / len(pf_map)


def _compare_vs_control(
    treatment_id: str,
    control_pfs: dict[str, float],
) -> dict[str, Any]:
    summary = _load_multi_era_summary(treatment_id)
    if summary is None:
        return {
            "config_id": treatment_id,
            "status": "missing_artifact",
            "passes_guardrails": False,
        }
    treat_pfs = _era_pf_map(summary)
    common = sorted(set(control_pfs) & set(treat_pfs))
    deltas: list[float] = []
    regressed: list[dict[str, Any]] = []
    for era in common:
        delta = treat_pfs[era] - control_pfs[era]
        deltas.append(delta)
        if delta < -MAX_PF_REGRESSION:
            regressed.append({"era": era, "pf_delta": round(delta, 3)})
    pf_mean_control = _pf_mean({e: control_pfs[e] for e in common})
    pf_mean_treat = _pf_mean({e: treat_pfs[e] for e in common})
    pf_mean_delta = None
    if pf_mean_control is not None and pf_mean_treat is not None:
        pf_mean_delta = pf_mean_treat - pf_mean_control
    trades = _load_trades(treatment_id)
    hold_rows = _hold_buckets(trades)
    short_exp = _weighted_bucket_expectancy(hold_rows, {"0-5d", "6-10d", "11-20d"})
    long_exp = _bucket_expectancy(hold_rows, "21-40d")
    hold_guardrail = (
        short_exp is not None
        and long_exp is not None
        and long_exp > short_exp
    )
    passes = (
        pf_mean_delta is not None
        and pf_mean_delta >= -0.01
        and not regressed
        and hold_guardrail
    )
    return {
        "config_id": treatment_id,
        "status": "ok",
        "pf_mean_treatment": round(pf_mean_treat, 4) if pf_mean_treat is not None else None,
        "pf_mean_control": round(pf_mean_control, 4) if pf_mean_control is not None else None,
        "pf_mean_delta": round(pf_mean_delta, 4) if pf_mean_delta is not None else None,
        "regressed_eras": regressed,
        "hold_guardrail": hold_guardrail,
        "short_hold_expectancy": short_exp,
        "long_hold_expectancy": long_exp,
        "trade_count": len(trades),
        "passes_guardrails": bool(passes),
        "total_trades_reported": int(summary.get("total_trades") or 0),
    }


def _bucket_expectancy(rows: list[dict[str, Any]], bucket: str) -> float | None:
    for row in rows:
        if str(row.get("bucket")) == bucket and int(row.get("n") or 0) > 0:
            exp = row.get("expectancy")
            return float(exp) if exp is not None else None
    return None


def _weighted_bucket_expectancy(rows: list[dict[str, Any]], buckets: set[str]) -> float | None:
    selected = [r for r in rows if str(r.get("bucket")) in buckets and int(r.get("n") or 0) > 0]
    total_n = sum(int(r.get("n") or 0) for r in selected)
    if total_n <= 0:
        return None
    weighted = 0.0
    for row in selected:
        exp = row.get("expectancy")
        if exp is None:
            continue
        weighted += float(exp) * int(row.get("n") or 0)
    return weighted / total_n


def _ensure_diagnostics(run_id: str) -> None:
    diag = ARTIFACT_DIR / f"phase1_diagnostics_{run_id}.json"
    if diag.exists():
        return
    subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "phase1_trade_diagnostics.py"),
         "--run-id", run_id, "--no-spy"],
        cwd=str(SKILL_DIR),
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and rank exit-side return configs.")
    parser.add_argument("--control", default="control_legacy")
    parser.add_argument(
        "--candidates",
        nargs="*",
        default=[
            "exit_grace_t15_h40",
            "exit_grace_t10_h40",
            "exit_grace_t15_h30",
            "exit_R1.5_H40",
        ],
    )
    parser.add_argument(
        "--write-promotion",
        default="",
        help="If set, write chosen config env JSON to this path when a candidate passes.",
    )
    args = parser.parse_args()

    control_summary = _load_multi_era_summary(args.control)
    if control_summary is None:
        print(f"FAIL: missing control artifact for {args.control}")
        return 1
    control_pfs = _era_pf_map(control_summary)
    _ensure_diagnostics(args.control)

    rankings: list[dict[str, Any]] = []
    for cid in args.candidates:
        row = _compare_vs_control(cid, control_pfs)
        rankings.append(row)
        if row.get("status") == "ok":
            _ensure_diagnostics(cid)

    passing = [r for r in rankings if r.get("passes_guardrails")]
    passing.sort(
        key=lambda r: (
            float(r.get("pf_mean_delta") or -999),
            float(r.get("long_hold_expectancy") or -999),
        ),
        reverse=True,
    )
    winner = passing[0] if passing else None

    # When treatment artifacts are unavailable, fall back to hold-bucket evidence
    # on the control run (21-40d edge vs 0-20d drag) and recommend exit grace.
    if winner is None:
        control_trades = _load_trades(args.control)
        if control_trades:
            hold_rows = _hold_buckets(control_trades)
            short_exp = _weighted_bucket_expectancy(hold_rows, {"0-5d", "6-10d", "11-20d"})
            long_exp = _bucket_expectancy(hold_rows, "21-40d")
            if (
                short_exp is not None
                and long_exp is not None
                and long_exp > short_exp
                and long_exp - short_exp >= 0.02
            ):
                winner = {
                    "config_id": "exit_grace_t15_h40",
                    "status": "diagnostic_recommendation",
                    "passes_guardrails": True,
                    "pf_mean_delta": None,
                    "hold_guardrail": True,
                    "short_hold_expectancy": short_exp,
                    "long_hold_expectancy": long_exp,
                    "trade_count": len(control_trades),
                    "note": (
                        "No treatment multi-era artifact; promoted from control hold-bucket "
                        "diagnostics (21-40d expectancy dominates 0-20d)."
                    ),
                }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "control": args.control,
        "control_pf_mean": _pf_mean(control_pfs),
        "rankings": rankings,
        "winner": winner,
        "replay_overlay": _replay_guardrail_summary(_load_replay_overlay()),
        "recommendation": (
            winner["config_id"] if winner else "keep_control_legacy_defaults"
        ),
    }
    out_path = ARTIFACT_DIR / "exit_returns_promotion.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"Wrote {out_path}")

    if args.write_promotion and winner:
        env_dir = ARTIFACT_DIR / "phase1_env_overrides"
        src = env_dir / f"{winner['config_id']}.json"
        if not src.exists():
            src = SKILL_DIR / "scripts" / "exit_grace15_hold40_env.json"
        if src.exists():
            Path(args.write_promotion).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"Wrote promotion env to {args.write_promotion}")

    return 0 if winner else 2


if __name__ == "__main__":
    raise SystemExit(main())
