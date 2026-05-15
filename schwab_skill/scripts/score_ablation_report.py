#!/usr/bin/env python3
"""
Score raw ablation runs into a promotion-ready leaderboard report.

Input artifact:
  - validation_artifacts/ablation_raw_<run_id>.json

Output artifacts:
  - validation_artifacts/ablation_report_<run_id>.json
  - validation_artifacts/ablation_report_<run_id>.md
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
DEFAULT_BOOTSTRAP_SAMPLES = 1000
DEFAULT_CONFIDENCE_LEVEL = 0.95

PRIMARY_METRIC_MAP = {
    "expectancy_per_trade": "avg_return_net_pct",
    "net_return": "total_return_net_pct",
    "hit_rate": "win_rate_net",
}

GUARDRAIL_METRIC_MAP = {
    "max_drawdown": "max_drawdown_net_pct",
    "trade_count": "total_trades",
    "hit_rate": "win_rate_net",
}


def _load_raw(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Raw artifact must be a JSON object: {path}")
    return data


def _mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_split_metric_map(variant: dict[str, Any], metric_key: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for split_row in variant.get("splits") or []:
        split = split_row.get("split") or {}
        split_name = str(split.get("name") or "")
        if not split_name:
            continue
        metrics = split_row.get("metrics") or {}
        v = _safe_float(metrics.get(metric_key))
        if v is None:
            continue
        out[split_name] = v
    return out


def _paired_arrays(
    baseline: dict[str, float],
    candidate: dict[str, float],
) -> tuple[list[float], list[float]]:
    shared = sorted(set(baseline.keys()) & set(candidate.keys()))
    return [baseline[s] for s in shared], [candidate[s] for s in shared]


def _quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    q = min(1.0, max(0.0, q))
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _bootstrap_relative_lift_ci(
    baseline_vals: list[float],
    variant_vals: list[float],
    *,
    samples: int,
    confidence_level: float,
    seed: int = 42,
) -> tuple[float | None, float | None]:
    if not baseline_vals or not variant_vals or len(baseline_vals) != len(variant_vals):
        return (None, None)
    n = len(baseline_vals)
    if n == 1:
        base = baseline_vals[0]
        var = variant_vals[0]
        denom = max(abs(base), 1e-9)
        rel = (var - base) / denom
        return (rel, rel)
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(max(100, int(samples))):
        idxs = [rng.randrange(n) for _ in range(n)]
        b = [baseline_vals[i] for i in idxs]
        v = [variant_vals[i] for i in idxs]
        b_mean = statistics.fmean(b)
        v_mean = statistics.fmean(v)
        denom = max(abs(b_mean), 1e-9)
        draws.append((v_mean - b_mean) / denom)
    draws.sort()
    alpha = max(0.0, min(1.0, 1.0 - confidence_level))
    lo_q = alpha / 2.0
    hi_q = 1.0 - lo_q
    return (_quantile(draws, lo_q), _quantile(draws, hi_q))


def _drawdown_magnitude(drawdown_val: float | None) -> float | None:
    if drawdown_val is None:
        return None
    return abs(min(0.0, drawdown_val))


def _score_variant(
    variant: dict[str, Any],
    *,
    baseline: dict[str, Any],
    primary_metric: str,
    guardrails: list[str],
    promotion_rules: dict[str, Any],
    bootstrap_samples: int,
    confidence_level: float,
) -> dict[str, Any]:
    primary_key = PRIMARY_METRIC_MAP.get(primary_metric, "avg_return_net_pct")
    baseline_primary = _extract_split_metric_map(baseline, primary_key)
    variant_primary = _extract_split_metric_map(variant, primary_key)
    b_vals, v_vals = _paired_arrays(baseline_primary, variant_primary)

    b_mean = _mean(b_vals)
    v_mean = _mean(v_vals)
    relative_lift = None
    if b_mean is not None and v_mean is not None:
        denom = max(abs(b_mean), 1e-9)
        relative_lift = (v_mean - b_mean) / denom

    ci_lo, ci_hi = _bootstrap_relative_lift_ci(
        b_vals,
        v_vals,
        samples=bootstrap_samples,
        confidence_level=confidence_level,
    )

    guardrail_snapshot: dict[str, Any] = {}
    for g in guardrails:
        metric_key = GUARDRAIL_METRIC_MAP.get(g)
        if metric_key is None:
            continue
        b_map = _extract_split_metric_map(baseline, metric_key)
        v_map = _extract_split_metric_map(variant, metric_key)
        gb, gv = _paired_arrays(b_map, v_map)
        guardrail_snapshot[g] = {
            "metric_key": metric_key,
            "baseline_mean": _mean(gb),
            "variant_mean": _mean(gv),
            "paired_count": len(gb),
        }

    flags: list[str] = []
    required_lift = float(promotion_rules.get("primary_min_relative_lift", 0.0) or 0.0)
    if relative_lift is None:
        flags.append("missing_primary_metric")
    elif relative_lift < required_lift:
        flags.append("primary_lift_below_threshold")

    dd_rule = promotion_rules.get("max_drawdown_max_relative_worsening")
    if dd_rule is not None:
        dd = guardrail_snapshot.get("max_drawdown") or {}
        b_dd = _drawdown_magnitude(_safe_float(dd.get("baseline_mean")))
        v_dd = _drawdown_magnitude(_safe_float(dd.get("variant_mean")))
        if b_dd is None or v_dd is None:
            flags.append("missing_drawdown_guardrail")
        else:
            dd_worsening = (v_dd - b_dd) / max(b_dd, 1e-9)
            dd["relative_worsening"] = dd_worsening
            if dd_worsening > float(dd_rule):
                flags.append("drawdown_worsening_exceeds_limit")

    tc_rule = promotion_rules.get("min_trade_count_ratio_vs_baseline")
    if tc_rule is not None:
        tc = guardrail_snapshot.get("trade_count") or {}
        b_tc = _safe_float(tc.get("baseline_mean"))
        v_tc = _safe_float(tc.get("variant_mean"))
        if b_tc is None or v_tc is None:
            flags.append("missing_trade_count_guardrail")
        else:
            ratio = v_tc / max(b_tc, 1e-9)
            tc["ratio_vs_baseline"] = ratio
            if ratio < float(tc_rule):
                flags.append("trade_count_ratio_below_minimum")

    summary = variant.get("summary") or {}
    if int(summary.get("failure_count", 0) or 0) > 0:
        flags.append("variant_contains_failed_splits")

    return {
        "variant_id": variant.get("variant_id"),
        "experiment_id": variant.get("experiment_id"),
        "variant_type": variant.get("variant_type"),
        "description": variant.get("description"),
        "env_overrides": variant.get("env_overrides") or {},
        "primary_metric_key": primary_key,
        "primary_baseline_mean": b_mean,
        "primary_variant_mean": v_mean,
        "relative_lift_vs_baseline": relative_lift,
        "ci_relative_lift_lower": ci_lo,
        "ci_relative_lift_upper": ci_hi,
        "paired_primary_count": len(b_vals),
        "guardrails": guardrail_snapshot,
        "regression_flags": sorted(set(flags)),
        "pass": len(flags) == 0,
    }


def _render_markdown(
    report: dict[str, Any],
    top_n: int,
) -> str:
    leaderboard = report.get("leaderboard") or []
    rows = leaderboard[: max(1, int(top_n))]
    lines = [
        "# Ablation Leaderboard",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- primary_metric: `{report.get('objective', {}).get('primary_metric')}`",
        "",
        "| Variant | Pass | Rel Lift | CI Low | CI High | Flags |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        lift = row.get("relative_lift_vs_baseline")
        lo = row.get("ci_relative_lift_lower")
        hi = row.get("ci_relative_lift_upper")
        flags = ", ".join(row.get("regression_flags") or []) or "-"
        lines.append(
            f"| {row.get('variant_id')} | {'yes' if row.get('pass') else 'no'} | "
            f"{(float(lift) if lift is not None else 0.0):.4f} | "
            f"{(float(lo) if lo is not None else 0.0):.4f} | "
            f"{(float(hi) if hi is not None else 0.0):.4f} | {flags} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-artifact", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    args = parser.parse_args()

    raw = _load_raw(args.raw_artifact)
    manifest = raw.get("manifest") or {}
    objective = manifest.get("objective") or {}
    execution = manifest.get("execution") or {}
    reporting = manifest.get("reporting") or {}
    results = raw.get("results") or []
    if not isinstance(results, list) or not results:
        raise ValueError("Raw artifact contains no results.")

    baseline = None
    for row in results:
        if isinstance(row, dict) and row.get("variant_id") == "baseline":
            baseline = row
            break
    if baseline is None:
        raise ValueError("Raw artifact is missing baseline variant.")

    primary_metric = str(objective.get("primary_metric") or "expectancy_per_trade")
    guardrails = [str(g) for g in (objective.get("guardrails") or [])]
    promotion_rules = objective.get("promotion_rules") or {}
    bootstrap_samples = int(execution.get("bootstrap_samples", DEFAULT_BOOTSTRAP_SAMPLES) or DEFAULT_BOOTSTRAP_SAMPLES)
    confidence_level = float(execution.get("confidence_level", DEFAULT_CONFIDENCE_LEVEL) or DEFAULT_CONFIDENCE_LEVEL)
    top_n = int(reporting.get("leaderboard_top_n", 10) or 10)

    scored: list[dict[str, Any]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        if row.get("variant_id") == "baseline":
            continue
        scored.append(
            _score_variant(
                row,
                baseline=baseline,
                primary_metric=primary_metric,
                guardrails=guardrails,
                promotion_rules=promotion_rules,
                bootstrap_samples=bootstrap_samples,
                confidence_level=confidence_level,
            )
        )

    scored.sort(
        key=lambda r: (
            bool(r.get("pass")),
            float(r.get("relative_lift_vs_baseline") or 0.0),
        ),
        reverse=True,
    )

    run_id = str(raw.get("run_id") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    report = {
        "schema_version": "1.0",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_artifact": str(args.raw_artifact),
        "objective": {
            "primary_metric": primary_metric,
            "guardrails": guardrails,
            "promotion_rules": promotion_rules,
            "bootstrap_samples": bootstrap_samples,
            "confidence_level": confidence_level,
        },
        "summary": {
            "variant_count": len(scored),
            "pass_count": sum(1 for r in scored if r.get("pass")),
            "fail_count": sum(1 for r in scored if not r.get("pass")),
            "best_variant": scored[0].get("variant_id") if scored else None,
        },
        "leaderboard": scored,
    }

    out_json = args.out_json or (ARTIFACT_DIR / f"ablation_report_{run_id}.json")
    out_md = args.out_md or (ARTIFACT_DIR / f"ablation_report_{run_id}.md")
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    out_md.write_text(_render_markdown(report, top_n=top_n), encoding="utf-8")

    print(f"Ablation report JSON: {out_json}")
    print(f"Ablation report Markdown: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
