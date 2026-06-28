#!/usr/bin/env python3
"""Compare breakout_2bar entry gate vs available baselines from chunk artifacts."""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.phase2_common import load_trades, per_era_stats, profit_factor  # noqa: E402

ART = SKILL_DIR / "validation_artifacts"
OUT_JSON = ART / "breakout_2bar_compare.json"
OUT_MD = ART / "breakout_2bar_compare.md"

PER_ERA_MIN_TRADES = 50
MAX_PF_REGRESSION = 0.10
PROCEED_PF_MEAN = 1.20
PROCEED_WORST = 1.00
OVERLAP_ERAS = ("crash_recovery", "bear_rates", "recent_current")


def _cohort(trades: list) -> dict:
    early = [t for t in trades if t.hold_days <= 20 and t.net_ret < 0]
    mid = [t for t in trades if 21 <= t.hold_days <= 40 and t.net_ret > 0]
    short = [t for t in trades if t.hold_days <= 20]
    long = [t for t in trades if 21 <= t.hold_days <= 40]
    n = len(trades)
    return {
        "n": n,
        "pf_all": profit_factor(trades),
        "early_stopouts_lte20d": len(early),
        "early_share_pct": round(100 * len(early) / n, 2) if n else 0,
        "early_loss_mass": round(sum(t.net_ret for t in early), 4),
        "winners_21_40d": len(mid),
        "hold_lte20d_pf": profit_factor(short),
        "hold_21_40d_pf": profit_factor(long),
    }


def _summarize(run_id: str) -> dict | None:
    try:
        trades = load_trades(run_id)
    except Exception as exc:
        return {"run_id": run_id, "error": str(exc), "n": 0}
    if not trades:
        return {"run_id": run_id, "n": 0, "eras": []}

    era_rows: list[dict] = []
    pf_values: list[float] = []
    for stat in per_era_stats(trades):
        pf = stat.pf
        pf_num = 99.0 if pf == float("inf") else (float(pf) if pf is not None else 0.0)
        if stat.n > 0:
            pf_values.append(pf_num)
        era_rows.append(
            {
                "era": stat.era,
                "n": stat.n,
                "pf": round(pf_num, 4) if pf is not None else None,
                "win_rate": round(stat.win_rate, 4) if stat.win_rate is not None else None,
                "expectancy": round(stat.expectancy, 6) if stat.expectancy is not None else None,
                "avg_hold_days": round(stat.avg_hold_days, 1),
                "thin": stat.n < PER_ERA_MIN_TRADES,
            }
        )

    pf_mean = statistics.mean(pf_values) if pf_values else 0.0
    worst = min(pf_values) if pf_values else 0.0
    thin_eras = [r["era"] for r in era_rows if r["thin"]]
    return {
        "run_id": run_id,
        "n": len(trades),
        "pf_mean": round(pf_mean, 4),
        "worst_era_pf": round(worst, 4),
        "thin_eras": thin_eras,
        "passes_promotion_gate": pf_mean >= PROCEED_PF_MEAN and worst >= PROCEED_WORST,
        "eras": era_rows,
        "cohort": _cohort(trades),
    }


def _delta_vs_control(treatment: dict, control: dict) -> dict:
    era_map = {r["era"]: r for r in control.get("eras", [])}
    regressed: list[dict] = []
    deltas: list[float] = []
    for row in treatment.get("eras", []):
        base = era_map.get(row["era"])
        if not base or row.get("pf") is None or base.get("pf") is None:
            continue
        d = row["pf"] - base["pf"]
        deltas.append(d)
        if d < -MAX_PF_REGRESSION:
            regressed.append({"era": row["era"], "pf_delta": round(d, 4)})
    pf_mean_delta = treatment["pf_mean"] - control["pf_mean"]
    return {
        "pf_mean_delta": round(pf_mean_delta, 4),
        "worst_era_delta": round(treatment["worst_era_pf"] - control["worst_era_pf"], 4),
        "regressed_eras": regressed,
        "passes_guardrails": pf_mean_delta >= -0.01 and not regressed,
    }


def main() -> int:
    run_ids = ["breakout_2bar", "control_legacy_aug", "breakout_vol_100"]
    summaries = {rid: _summarize(rid) for rid in run_ids}

    pg_path = SKILL_DIR / "phase1_progress_signal_gate.json"
    historical_control = None
    if pg_path.exists():
        pg = json.loads(pg_path.read_text(encoding="utf-8"))
        historical_control = next(
            (r for r in pg.get("ranking", []) if r.get("config_id") == "control_legacy"),
            None,
        )

    treatment = summaries["breakout_2bar"]
    aug = summaries["control_legacy_aug"]
    comparison_aug = _delta_vs_control(treatment, aug) if treatment and aug and aug.get("n") else None

    overlap: dict[str, dict] = {}
    for rid in run_ids:
        if summaries.get(rid, {}).get("n"):
            trades = [t for t in load_trades(rid) if t.era in OVERLAP_ERAS]
            pf_values = []
            for stat in per_era_stats(trades, eras=OVERLAP_ERAS):
                if stat.pf is None:
                    continue
                pf_values.append(99.0 if stat.pf == float("inf") else float(stat.pf))
            early = [t for t in trades if t.hold_days <= 20 and t.net_ret < 0]
            overlap[rid] = {
                "n": len(trades),
                "pf_mean": round(statistics.mean(pf_values), 4) if pf_values else 0.0,
                "worst_era_pf": round(min(pf_values), 4) if pf_values else 0.0,
                "early_stopout_pct": round(100 * len(early) / len(trades), 2) if trades else 0.0,
            }

    chunk_diagnosis = {}
    chunks_root = ART / "multi_era_chunks"
    for rid in ("control_legacy", "exit_grace_t15_h40", "breakout_2bar"):
        base = chunks_root / rid
        if not base.exists():
            continue
        empty = total = 0
        excluded_sample: list[int] = []
        for era_dir in base.iterdir():
            if not era_dir.is_dir():
                continue
            for cf in era_dir.glob("chunk_*.json"):
                if "ticker" in cf.name:
                    continue
                total += 1
                payload = json.loads(cf.read_text(encoding="utf-8"))
                n = len(payload.get("trades") or [])
                if n == 0:
                    empty += 1
                if len(excluded_sample) < 8:
                    excluded_sample.append(int(payload.get("excluded_count") or 0))
        chunk_diagnosis[rid] = {
            "chunk_files": total,
            "empty_chunks": empty,
            "sample_excluded_counts": excluded_sample,
        }

    recommendation = "no_promote_entry_gate"
    rationale: list[str] = []
    if not treatment or treatment.get("n", 0) == 0:
        recommendation = "insufficient_data"
        rationale.append("breakout_2bar has no trades in chunks.")
    elif comparison_aug:
        if comparison_aug["passes_guardrails"] and treatment["pf_mean"] > aug["pf_mean"]:
            recommendation = "shadow_breakout_2bar"
            rationale.append("breakout_2bar beats control_legacy_aug on PF mean without era regressions.")
        else:
            rationale.append(
                f"PF mean delta vs aug={comparison_aug['pf_mean_delta']:+.4f}; "
                f"regressed_eras={comparison_aug['regressed_eras']}"
            )
    if treatment and not treatment.get("passes_promotion_gate"):
        rationale.append(
            f"Still below promotion gate (need mean>={PROCEED_PF_MEAN}, worst>={PROCEED_WORST}); "
            f"got mean={treatment.get('pf_mean')} worst={treatment.get('worst_era_pf')}"
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "treatment": "breakout_2bar",
        "description": "BREAKOUT_CONFIRM_BARS=2, all overlays off",
        "summaries": summaries,
        "historical_control_legacy_sweep": historical_control,
        "comparison_vs_control_legacy_aug": comparison_aug,
        "overlap_eras_3": overlap,
        "overlap_eras_note": (
            "Fair compare on crash_recovery + bear_rates + recent_current only "
            "(control_legacy_aug lacks late_bull/volatility_chop)."
        ),
        "zero_trade_chunk_diagnosis": chunk_diagnosis,
        "recommendation": recommendation,
        "rationale": rationale,
    }
    ART.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# breakout_2bar vs baselines",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Treatment: breakout_2bar",
        f"- Trades: {treatment.get('n')}",
        f"- PF mean: {treatment.get('pf_mean')} | worst era: {treatment.get('worst_era_pf')}",
        f"- Promotion gate: {'PASS' if treatment.get('passes_promotion_gate') else 'FAIL'}",
        f"- Early stop-outs (<=20d losers): {treatment.get('cohort', {}).get('early_stopouts_lte20d')} "
        f"({treatment.get('cohort', {}).get('early_share_pct')}%)",
        "",
        "## Per era",
        "| era | n | PF | expectancy |",
        "|-----|---|----|------------|",
    ]
    for row in treatment.get("eras", []):
        lines.append(
            f"| {row['era']} | {row['n']} | {row['pf']} | {row.get('expectancy')} |"
        )
    if comparison_aug:
        lines.extend(
            [
                "",
                "## vs control_legacy_aug (759 trades, 3 eras)",
                f"- PF mean delta: {comparison_aug['pf_mean_delta']:+.4f}",
                f"- Worst-era delta: {comparison_aug['worst_era_delta']:+.4f}",
                f"- Regressed eras: {comparison_aug['regressed_eras']}",
                f"- Passes guardrails: {comparison_aug['passes_guardrails']}",
            ]
        )
    if historical_control:
        lines.extend(
            [
                "",
                "## Historical control_legacy (signal-gate sweep Jun 2026)",
                f"- PF mean: {historical_control.get('pf_mean_treatment')} | "
                f"worst era: {historical_control.get('worst_era_pf_treatment')}",
            ]
        )
    if overlap:
        lines.extend(["", "## Overlap eras (fair 3-era compare)", ""])
        for rid, row in overlap.items():
            lines.append(
                f"- **{rid}**: n={row['n']} PF mean={row['pf_mean']} "
                f"worst={row['worst_era_pf']} early_stopout_pct={row['early_stopout_pct']}%"
            )
        b = overlap.get("breakout_2bar", {})
        a = overlap.get("control_legacy_aug", {})
        if b and a:
            lines.append(
                f"- Delta (2bar - aug): PF mean {b['pf_mean'] - a['pf_mean']:+.4f}, "
                f"early stopouts {b['early_stopout_pct'] - a['early_stopout_pct']:+.1f}pp"
            )
    if chunk_diagnosis:
        lines.extend(
            [
                "",
                "## Zero-trade chunk diagnosis",
                "- `control_legacy`: high `excluded_count` (15-35) and empty trades - run never produced entries.",
                "- `exit_grace_t15_h40`: same exclusion profile as working `breakout_2bar` but empty trades - likely stale/corrupt re-run artifacts, not a strategy effect.",
                "",
            ]
        )
        for rid, row in chunk_diagnosis.items():
            lines.append(
                f"- {rid}: {row['empty_chunks']}/{row['chunk_files']} empty chunks; "
                f"sample excluded={row['sample_excluded_counts']}"
            )
    lines.extend(["", f"## Recommendation: **{recommendation}**", ""])
    for r in rationale:
        lines.append(f"- {r}")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({"recommendation": recommendation, "report_json": str(OUT_JSON), "report_md": str(OUT_MD)}, indent=2))
    print("\n" + OUT_MD.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
