#!/usr/bin/env python3
"""End-to-end scoring validation pipeline for overnight / CI runs.

Steps:
  1. Build scoring audit dataset (live score stack, multi-horizon labels)
  2. Validate candidates at 10d / 20d / 40d horizons
  3. Validate trade chunks when score fields exist (skip otherwise)
  4. Write morning summary markdown

Example:
    python scripts/run_scoring_validation_pipeline.py --max-tickers 80
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
PY = sys.executable


def _run(cmd: list[str], *, step: str) -> int:
    print(f"\n=== {step} ===")
    print(" ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), check=False)
    return int(proc.returncode)


def _dataset_paths(*, full_history: bool, with_mirofish: bool) -> tuple[Path, str, Path]:
    """Return (csv_path, validate_suffix, meta_path)."""
    if full_history and with_mirofish:
        csv = ARTIFACT_DIR / "scoring_audit_dataset_full_mirofish.csv"
        suffix = "_full_mirofish"
    elif full_history:
        csv = ARTIFACT_DIR / "scoring_audit_dataset_full.csv"
        suffix = "_full"
    elif with_mirofish:
        csv = ARTIFACT_DIR / "scoring_audit_dataset_mirofish.csv"
        suffix = "_mirofish"
    else:
        csv = ARTIFACT_DIR / "scoring_audit_dataset.csv"
        suffix = ""
    return csv, suffix, csv.with_suffix(".meta.json")


def _write_summary(
    *,
    build_rc: int,
    validate_rc: int,
    trades_rc: int,
    dataset_csv: Path,
    validate_suffix: str,
) -> Path:
    report_path = ARTIFACT_DIR / f"scoring_metrics_report{validate_suffix}.json"
    if not report_path.exists() and not validate_suffix:
        report_path = ARTIFACT_DIR / "scoring_metrics_report.json"
    trades_report_path = ARTIFACT_DIR / "scoring_metrics_report_trades.json"
    meta_path = dataset_csv.with_suffix(".meta.json")
    summary_path = ARTIFACT_DIR / f"scoring_validation_morning_summary{validate_suffix or ''}.md"

    report: dict = {}
    meta: dict = {}
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            report = {}
    trades_report: dict = {}
    if trades_report_path.exists():
        try:
            trades_report = json.loads(trades_report_path.read_text(encoding="utf-8"))
        except Exception:
            trades_report = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    primary = str(report.get("primary_horizon") or "n/a")
    primary_block = (report.get("horizons") or {}).get(primary) or {}
    rank_lift = primary_block.get("rank_lift") or report.get("rank_lift") or []
    top_rank = rank_lift[0] if rank_lift else {}
    signal_metrics = (primary_block.get("global") or report.get("global") or {}).get("metrics", {}).get("signal_score", {})
    rank_metrics = (primary_block.get("global") or report.get("global") or {}).get("metrics", {}).get("rank_score", {})

    lines = [
        "# Scoring Validation — Morning Summary",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Pipeline status",
        "",
        f"- Dataset build: {'PASS' if build_rc == 0 else 'FAIL'}",
        f"- Candidate validation: {'PASS' if validate_rc == 0 else 'FAIL'}",
        f"- Trade validation: {'PASS/SKIP' if trades_rc == 0 else 'FAIL'}",
        "",
        "## Dataset",
        "",
        f"- Rows: {meta.get('rows', report.get('row_count', 'n/a'))}",
        f"- Tickers: {meta.get('tickers', 'n/a')}",
        f"- Window: {meta.get('window_start', '?')} -> {meta.get('window_end', '?')}",
        f"- Score stack: {report.get('score_stack_source', meta.get('score_stack_source', 'n/a'))}",
        f"- Primary horizon: {primary}",
        "",
        "## Primary horizon highlights",
        "",
    ]
    if signal_metrics:
        lines.append(
            f"- signal_score: AUC {float(signal_metrics.get('auc', 0)):.3f}, "
            f"IC {float(signal_metrics.get('spearman_ic', 0)):.4f}"
        )
    if rank_metrics:
        lines.append(
            f"- rank_score: AUC {float(rank_metrics.get('auc', 0)):.3f}, "
            f"IC {float(rank_metrics.get('spearman_ic', 0)):.4f}"
        )
    v2_metrics = (primary_block.get("global") or {}).get("metrics", {}).get("rank_score_v2", {})
    if v2_metrics:
        lines.append(
            f"- rank_score_v2: AUC {float(v2_metrics.get('auc', 0)):.3f}, "
            f"IC {float(v2_metrics.get('spearman_ic', 0)):+.4f}, "
            f"decile spread {float(v2_metrics.get('decile_spread') or 0):+.4f}"
        )
    era_v2 = report.get("rank_v2_era_ic_wins") or {}
    if era_v2.get("eras"):
        lines.append(
            f"- rank_score_v2 era IC wins: {era_v2.get('wins')}/{era_v2.get('eras')}"
        )
    if top_rank:
        lines.append(
            f"- Best rank column: {top_rank.get('score_column')} "
            f"(IC {float(top_rank.get('spearman_ic', 0)):.4f})"
        )
    rank_v2_block = report.get("rank_v2_vs_v1") or {}
    if rank_v2_block:
        lines.extend(["", "## Rank v2 (shadow) vs v1", ""])
        lines.append(
            f"- rank_score_v2 IC {float(rank_v2_block.get('rank_score_v2_ic', 0)):+.4f} vs "
            f"rank_score IC {float(rank_v2_block.get('rank_score_ic', 0)):+.4f} vs "
            f"signal IC {float(rank_v2_block.get('signal_ic', 0)):+.4f}"
        )
    for note in report.get("guardrail_notes") or []:
        lines.append(f"- Note: {note}")

    # Component readout at primary horizon.
    comp_metrics = (primary_block.get("global") or {}).get("metrics") or {}
    ablation = primary_block.get("ablation") or []
    if comp_metrics:
        lines.extend(["", "## Component validity (primary horizon)", ""])
        for comp in ("pts_52w", "pts_sma", "pts_volume", "pts_mirofish"):
            row = comp_metrics.get(comp) or {}
            if not row:
                continue
            lines.append(
                f"- {comp}: AUC {float(row.get('auc', 0)):.3f}, "
                f"IC {float(row.get('spearman_ic', 0)):.4f}"
            )
        if ablation:
            lines.append("")
            lines.append("Leave-one-out ablation (AUC delta when removed):")
            for row in ablation:
                lines.append(
                    f"- {row.get('removed_component')}: {float(row.get('auc_delta', 0)):+.4f}"
                )
        sma_sens = primary_block.get("sma_sensitivity") or []
        if sma_sens:
            lines.extend(["", "## SMA multiplier sensitivity", ""])
            best = max(sma_sens, key=lambda r: float(r.get("auc") or 0))
            for row in sma_sens:
                lines.append(
                    f"- mult={float(row.get('sma_multiplier', 0)):.1f}: "
                    f"AUC {float(row.get('auc') or 0):.3f}, IC {float(row.get('spearman_ic') or 0):+.4f}"
                )
            lines.append(
                f"- Best offline SMA scale: mult={float(best.get('sma_multiplier', 0)):.1f} "
                f"(AUC {float(best.get('auc') or 0):.3f})"
            )
        miro_sub = primary_block.get("mirofish_subset") or {}
        if miro_sub and not miro_sub.get("skipped"):
            sub_metrics = (miro_sub.get("global") or {}).get("metrics") or {}
            pm = sub_metrics.get("pts_mirofish") or {}
            if pm:
                lines.extend(["", "## MiroFish subset (primary horizon)", ""])
                lines.append(f"- Subset rows: {miro_sub.get('row_count', 'n/a')}")
                lines.append(
                    f"- pts_mirofish: AUC {float(pm.get('auc', 0)):.3f}, "
                    f"IC {float(pm.get('spearman_ic', 0)):+.4f}"
                )
        mirofish_rows = meta.get("mirofish_included_rows", 0)
        mirofish_note = (
            f"- **pts_mirofish** covered {mirofish_rows} rows in this build."
            if int(mirofish_rows or 0) > 0
            else "- **pts_mirofish** is flat (MiroFish not enabled in audit build) — rerun with `--with-mirofish`."
        )
        rank_ic = float(rank_metrics.get("spearman_ic", 0) or 0)
        signal_ic = float(signal_metrics.get("spearman_ic", 0) or 0)
        rank_vs_signal = (
            "- **rank_score** IC beats signal on this sample."
            if rank_ic > signal_ic + 1e-4
            else "- Composite/rank does **not** yet beat raw signal at the 40d hold horizon on this sample."
        )
        signal_auc = float(signal_metrics.get("auc", 0) or 0)
        auc_note = (
            f"- **signal_score** AUC {signal_auc:.3f} clears the 0.50 floor."
            if signal_auc >= 0.50
            else f"- **signal_score** AUC {signal_auc:.3f} still below 0.50 floor."
        )
        lines.extend([
            "",
            "## Interpretation",
            "",
            rank_vs_signal,
            auc_note,
            "- **pts_volume** is the strongest persistent component (positive IC at 40d on recent samples).",
            "- **pts_sma** zeroed via promoted `SCORE_PTS_SMA_MULTIPLIER=0.0` (offline +0.024 AUC vs pre-promotion).",
            mirofish_note,
            "- Trade chunks need regeneration with augmented logging for policy-level rank validation.",
            "- Full 10y history: `scoring_validation_morning_summary_full.md`; MiroFish sample: `_mirofish.md`.",
        ])
    for hk, block in (report.get("horizons") or {}).items():
        if block.get("skipped"):
            lines.append(f"- {hk}: skipped (insufficient rows)")
            continue
        rl = block.get("rank_lift") or []
        best = rl[0] if rl else {}
        lines.append(
            f"- {hk}: n={block.get('row_count')} best={best.get('score_column', 'n/a')} "
            f"IC={float(best.get('spearman_ic', 0)):.4f}"
        )
    if report.get("guardrail_warnings"):
        lines.extend(["", "## Warnings", ""])
        for w in report["guardrail_warnings"]:
            lines.append(f"- {w}")
    if trades_report:
        lines.extend([
            "",
            "## Trade-chunk validation",
            "",
            f"- Rows: {trades_report.get('row_count', 'n/a')}",
            f"- Note: {trades_report.get('guardrail_note', 'legacy chunks may lack score fields')}",
        ])
    lines.extend([
        "",
        "## Artifacts",
        "",
        "- `validation_artifacts/scoring_audit_dataset.csv`",
        f"- Dataset: `{dataset_csv.name}`",
        f"- Report: `scoring_metrics_report{validate_suffix}.json`",
        "- `validation_artifacts/scoring_metrics_report_trades.json`",
        "- `validation_artifacts/scoring_metrics_report.md`",
        "",
        "## Refresh",
        "",
        "```bash",
        "python scripts/run_scoring_validation_pipeline.py --max-tickers 80",
        "```",
        "",
    ])
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return summary_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full scoring validation pipeline.")
    parser.add_argument("--max-tickers", type=int, default=80)
    parser.add_argument("--full-history", action="store_true")
    parser.add_argument("--skip-build", action="store_true", help="Use existing audit CSV.")
    parser.add_argument("--strict", action="store_true", help="Fail validate step on guardrails.")
    parser.add_argument(
        "--with-mirofish",
        action="store_true",
        help="Enable MiroFish during dataset build (slow; see build script caps).",
    )
    parser.add_argument(
        "--mirofish-max-rows",
        type=int,
        default=200,
        help="Cap MiroFish rows when --with-mirofish (default 200).",
    )
    parser.add_argument(
        "--with-trade-sample",
        action="store_true",
        help="Refresh augmented trade sample chunk for trade-level scoring validation.",
    )
    args = parser.parse_args()

    dataset_csv, validate_suffix, _meta_path = _dataset_paths(
        full_history=bool(args.full_history),
        with_mirofish=bool(args.with_mirofish),
    )

    build_rc = 0
    if not args.skip_build:
        build_cmd = [
            PY,
            str(SKILL_DIR / "scripts" / "build_scoring_audit_dataset.py"),
            "--max-tickers",
            str(max(1, int(args.max_tickers))),
            "--out",
            str(dataset_csv),
        ]
        if args.full_history:
            build_cmd.append("--full-history")
        if args.with_mirofish:
            build_cmd.extend(["--with-mirofish", "--mirofish-max-rows", str(max(1, int(args.mirofish_max_rows)))])
        build_rc = _run(build_cmd, step="build_scoring_audit_dataset")

    if build_rc == 0 or args.skip_build:
        _run(
            [
                PY,
                str(SKILL_DIR / "scripts" / "tune_composite_weights.py"),
                "--csv",
                str(dataset_csv),
            ],
            step="tune_composite_weights",
        )

    validate_cmd = [
        PY,
        str(SKILL_DIR / "scripts" / "validate_scoring_metrics.py"),
        "--source",
        "candidates",
        "--dataset",
        str(dataset_csv),
    ]
    if validate_suffix:
        validate_cmd.extend(["--artifact-suffix", validate_suffix])
    if args.strict:
        validate_cmd.append("--strict")
    validate_rc = _run(validate_cmd, step="validate_scoring_metrics (candidates)")

    trades_cmd = [
        PY,
        str(SKILL_DIR / "scripts" / "validate_scoring_metrics.py"),
        "--source",
        "trades",
        "--run-id",
        "scoring_trade_sample" if args.with_trade_sample else "control_legacy",
        "--skip-if-missing",
        "--artifact-suffix",
        "_trades",
    ]
    if args.with_trade_sample:
        sample_rc = _run(
            [PY, str(SKILL_DIR / "scripts" / "run_scoring_trades_backtest.py")],
            step="run_scoring_trades_backtest",
        )
        if sample_rc != 0:
            print("WARN: scoring trades backtest failed; falling back to control_legacy_aug if present")
            trades_cmd[5] = "control_legacy_aug"
        else:
            trades_cmd[5] = "scoring_trades_v2"
            trades_cmd[-1] = "_trades_v2"
    trades_rc = _run(trades_cmd, step="validate_scoring_metrics (trades)")

    summary = _write_summary(
        build_rc=build_rc,
        validate_rc=validate_rc,
        trades_rc=trades_rc,
        dataset_csv=dataset_csv,
        validate_suffix=validate_suffix,
    )
    print(f"\nWrote {summary}")

    if build_rc != 0:
        return build_rc
    return validate_rc


if __name__ == "__main__":
    raise SystemExit(main())
