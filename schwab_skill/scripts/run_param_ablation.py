#!/usr/bin/env python3
"""
Run manifest-driven parameter ablations and emit raw split metrics.

This runner intentionally keeps scoring separate. Use
`scripts/score_ablation_report.py` to convert the raw artifact into a
leaderboard with confidence intervals and regression flags.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "ablation_manifest_v1.json"

DEFAULT_METRIC_KEYS = (
    "total_trades",
    "win_rate",
    "win_rate_net",
    "avg_return_pct",
    "avg_return_net_pct",
    "total_return_pct",
    "total_return_net_pct",
    "max_drawdown_net_pct",
    "profit_factor_net",
)


def _load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Manifest must be a JSON object: {path}")
    return data


def _split_name(start_date: str, category: str, idx: int) -> str:
    return f"{category}_{idx}_{start_date}"


def _extract_splits(manifest: dict[str, Any]) -> list[dict[str, str]]:
    data_splits = manifest.get("data_splits") or {}
    out: list[dict[str, str]] = []
    for category in ("train_windows", "test_windows"):
        values = data_splits.get(category) or []
        if not isinstance(values, list):
            continue
        for idx, item in enumerate(values):
            if isinstance(item, list) and item:
                start = str(item[0])
                end = str(item[1]) if len(item) > 1 else ""
            elif isinstance(item, dict):
                start = str(item.get("start") or "")
                end = str(item.get("end") or "")
            else:
                start = str(item)
                end = ""
            if not start:
                continue
            out.append(
                {
                    "name": _split_name(start, category.replace("_windows", ""), idx),
                    "category": category.replace("_windows", ""),
                    "start_date": start,
                    "end_date": end,
                }
            )
    if not out:
        raise ValueError("Manifest contains no usable data_splits entries.")
    return out


def _safe_variant_token(value: Any) -> str:
    txt = str(value).strip()
    token = txt.replace(" ", "_").replace("/", "_").replace("\\", "_")
    token = token.replace("=", "-").replace(":", "-")
    return token


def _build_variants(manifest: dict[str, Any], include_interactions: bool) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = [
        {
            "variant_id": "baseline",
            "experiment_id": "baseline",
            "variant_type": "baseline",
            "env_overrides": {},
            "description": "No env override.",
        }
    ]
    experiments = manifest.get("experiments") or []
    for exp in experiments:
        if not isinstance(exp, dict):
            continue
        exp_id = str(exp.get("id") or "experiment")
        param = str(exp.get("param") or "").strip()
        values = exp.get("values") or []
        if not param or not isinstance(values, list):
            continue
        for value in values:
            token = _safe_variant_token(value)
            variants.append(
                {
                    "variant_id": f"{exp_id}__{param}_{token}",
                    "experiment_id": exp_id,
                    "variant_type": "single_param",
                    "env_overrides": {param: str(value)},
                    "description": f"{param}={value}",
                }
            )
    if include_interactions:
        interactions = manifest.get("interaction_followups") or []
        for exp in interactions:
            if not isinstance(exp, dict):
                continue
            exp_id = str(exp.get("id") or "interaction")
            grid = exp.get("grid") or {}
            if not isinstance(grid, dict) or not grid:
                continue
            keys = [str(k) for k in grid.keys()]
            value_lists = [grid[k] if isinstance(grid[k], list) else [] for k in keys]
            if not all(value_lists):
                continue
            for combo in itertools.product(*value_lists):
                overrides = {keys[i]: str(combo[i]) for i in range(len(keys))}
                combo_token = "__".join(f"{k}_{_safe_variant_token(v)}" for k, v in overrides.items())
                variants.append(
                    {
                        "variant_id": f"{exp_id}__{combo_token}",
                        "experiment_id": exp_id,
                        "variant_type": "interaction",
                        "env_overrides": overrides,
                        "description": ", ".join(f"{k}={v}" for k, v in overrides.items()),
                    }
                )
    return variants


def _render_command(template: str, *, split: dict[str, str], variant_id: str) -> list[str]:
    rendered = template.format(
        python=sys.executable,
        start_date=split["start_date"],
        end_date=split["end_date"],
        split_name=split["name"],
        variant_id=variant_id,
    )
    return shlex.split(rendered)


def _read_metrics(metrics_file: Path) -> dict[str, Any] | None:
    if not metrics_file.exists():
        return None
    try:
        data = json.loads(metrics_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {k: data.get(k) for k in DEFAULT_METRIC_KEYS}


def _run_command(
    cmd: list[str],
    *,
    env_overrides: dict[str, str],
    metrics_file: Path,
) -> dict[str, Any]:
    env = dict(os.environ)
    env.update(env_overrides)
    started = datetime.now(timezone.utc)
    proc = subprocess.run(
        cmd,
        cwd=str(SKILL_DIR),
        capture_output=True,
        text=True,
        env=env,
    )
    ended = datetime.now(timezone.utc)
    metrics = _read_metrics(metrics_file)
    return {
        "command": cmd,
        "returncode": int(proc.returncode),
        "ok": proc.returncode == 0,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
        "metrics": metrics,
    }


def _summarize_variant(variant_runs: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "split_count": len(variant_runs),
        "success_count": sum(1 for r in variant_runs if r.get("ok")),
        "failure_count": sum(1 for r in variant_runs if not r.get("ok")),
    }
    for key in DEFAULT_METRIC_KEYS:
        vals: list[float] = []
        for run in variant_runs:
            metrics = run.get("metrics") or {}
            raw = metrics.get(key)
            try:
                if raw is not None:
                    vals.append(float(raw))
            except (TypeError, ValueError):
                continue
        summary[f"{key}_mean"] = round(sum(vals) / len(vals), 6) if vals else None
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--run-baseline-command",
        action="store_true",
        help="Run manifest.execution.baseline_command before ablation splits.",
    )
    parser.add_argument(
        "--include-interactions",
        action="store_true",
        help="Include interaction_followups grid runs in addition to single-param sweeps.",
    )
    parser.add_argument(
        "--max-variants",
        type=int,
        default=0,
        help="Optional cap to run only the first N variants (0 = no cap).",
    )
    args = parser.parse_args()

    manifest = _load_manifest(args.manifest)
    splits = _extract_splits(manifest)
    execution = manifest.get("execution") or {}
    run_template = str(
        execution.get("per_variant_backtest_command")
        or "{python} scripts/validate_backtest.py --tickers 20 --start {start_date}"
    )
    baseline_template = str(execution.get("baseline_command") or "").strip()
    metrics_file = SKILL_DIR / str(execution.get("metrics_file") or ".backtest_results.json")
    variants = _build_variants(manifest, include_interactions=args.include_interactions)
    if args.max_variants > 0:
        variants = variants[: args.max_variants]

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    preflight: dict[str, Any] | None = None
    if args.run_baseline_command and baseline_template:
        cmd = _render_command(
            baseline_template,
            split={"start_date": splits[0]["start_date"], "end_date": splits[0]["end_date"], "name": "baseline_preflight"},
            variant_id="baseline_preflight",
        )
        preflight = _run_command(cmd, env_overrides={}, metrics_file=metrics_file)

    results: list[dict[str, Any]] = []
    for variant in variants:
        variant_runs: list[dict[str, Any]] = []
        env_overrides = dict(variant.get("env_overrides") or {})
        print(f"[ablation] running variant={variant['variant_id']} splits={len(splits)}")
        for split in splits:
            cmd = _render_command(run_template, split=split, variant_id=str(variant["variant_id"]))
            run_result = _run_command(cmd, env_overrides=env_overrides, metrics_file=metrics_file)
            run_result["split"] = split
            variant_runs.append(run_result)
            status = "PASS" if run_result.get("ok") else "FAIL"
            print(
                f"  - {status} split={split['name']} start={split['start_date']} "
                f"rc={run_result.get('returncode')}"
            )
        results.append(
            {
                "variant_id": variant["variant_id"],
                "experiment_id": variant["experiment_id"],
                "variant_type": variant["variant_type"],
                "description": variant["description"],
                "env_overrides": env_overrides,
                "splits": variant_runs,
                "summary": _summarize_variant(variant_runs),
            }
        )

    out = {
        "schema_version": "1.0",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(args.manifest),
        "manifest": manifest,
        "execution": {
            "per_variant_backtest_command": run_template,
            "metrics_file": str(metrics_file),
            "include_interactions": bool(args.include_interactions),
            "max_variants": int(args.max_variants),
        },
        "preflight": preflight,
        "results": results,
    }

    out_path = ARTIFACT_DIR / f"ablation_raw_{run_id}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nAblation raw artifact: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
