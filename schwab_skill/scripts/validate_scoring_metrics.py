#!/usr/bin/env python3
"""Validate scoring components and composite rank offline.

Two data sources:
  * ``--source candidates`` (default): Stage2+VCP audit CSV from
    ``build_scoring_audit_dataset.py`` — tests metric validity without
    trade-simulation confounds. Labels: ``y_up_10d``, ``ret_10d_fwd``.
  * ``--source trades``: multi-era backtest chunks — tests rank on realized
    trades. Labels: ``net_return > 0``, ``net_return``.

Outputs:
  validation_artifacts/scoring_metrics_report.json
  validation_artifacts/scoring_metrics_report.md

Guardrails (``--strict``):
  * ``composite_score`` Spearman IC >= baseline ``signal_score`` IC
  * ``composite_score`` decile spread positive globally
  * Composite IC >= signal IC in >=3 eras (when enough era data)
  * At least one base component with AUC >= 0.52 globally
  * ``signal_score`` AUC >= 0.48 (raw signal diluted by harmful 52w pts)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
CHUNKS_DIR = ARTIFACT_DIR / "multi_era_chunks"
DEFAULT_CANDIDATE_CSV = ARTIFACT_DIR / "scoring_audit_dataset.csv"
FALLBACK_CSV = ARTIFACT_DIR / "advisory_dataset_latest.csv"

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.scoring_audit_builder import has_live_stack  # noqa: E402
from core.scoring_metrics import (  # noqa: E402
    CANDIDATE_HORIZONS,
    ERA_BOUNDS,
    assign_era,
    component_ablation,
    decile_table,
    enrich_candidate_scores,
    enrich_trade_frame_for_scoring,
    evaluate_score_column,
    pick_primary_horizon,
    rank_lift_table,
    reapply_composite_scores,
    score_columns_for_source,
    sma_multiplier_sensitivity,
)


def _resolve_candidate_csv(path: str) -> Path | None:
    if path:
        candidate = Path(path)
        return candidate if candidate.exists() else None
    for p in (DEFAULT_CANDIDATE_CSV, FALLBACK_CSV):
        if p.exists():
            return p
    return None


def _load_candidate_frame(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    df = df.dropna(subset=["entry_date", "signal_score"]).copy()
    if not has_live_stack(df):
        from config import get_stage2_52w_pct

        df = enrich_candidate_scores(df, stage2_floor=float(get_stage2_52w_pct(SKILL_DIR)))
        df["score_stack_source"] = "proxy"
    elif "score_stack_source" not in df.columns:
        df["score_stack_source"] = "live"
    df["era"] = assign_era(df["entry_date"])
    df = reapply_composite_scores(df, skill_dir=SKILL_DIR)
    from core.scoring_rank_v2 import enrich_dataframe_rank_v2

    df = enrich_dataframe_rank_v2(df, skill_dir=SKILL_DIR)
    return df


def _load_trade_frame(run_id: str) -> pd.DataFrame:
    chunk_base = CHUNKS_DIR / run_id
    if not chunk_base.exists():
        chunk_base = CHUNKS_DIR

    score_keys = [
        "signal_score",
        "close_vs_sma200_pct",
        "pts_52w",
        "pts_sma",
        "pts_volume",
        "pts_mirofish",
        "edge_score",
        "reliability_score",
        "execution_score",
        "composite_score",
        "rank_score",
        "rank_score_v2",
        "p_up_calibrated",
        "ev_10d",
        "advisory_confidence_bucket",
        "advisory_feature_coverage",
        "data_provider",
        "data_provider_primary",
        "used_fallback_data",
    ]
    rows: list[dict[str, Any]] = []
    for era in ERA_BOUNDS:
        era_dir = chunk_base / era if (chunk_base / era).exists() else CHUNKS_DIR / era
        if not era_dir.exists():
            continue
        for chunk_path in sorted(era_dir.glob("chunk_*.json")):
            if chunk_path.name.endswith("_tickers.json"):
                continue
            try:
                payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for raw in payload.get("trades", []):
                try:
                    entry = pd.Timestamp(raw.get("entry_date") or "")
                except Exception:
                    continue
                if pd.isna(entry):
                    continue
                net_ret = float(raw.get("net_return", 0.0) or 0.0)
                row: dict[str, Any] = {
                    "entry_date": entry.normalize(),
                    "era": era,
                    "ticker": str(raw.get("ticker") or "").upper(),
                    "net_return": net_ret,
                    "y_win": int(net_ret > 0),
                }
                for sk in score_keys:
                    if raw.get(sk) is not None:
                        row[sk] = raw.get(sk)
                rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        raise FileNotFoundError(f"no trades found for run_id={run_id}")
    df = df.dropna(subset=["entry_date", "net_return"]).copy()
    return enrich_trade_frame_for_scoring(df, skill_dir=SKILL_DIR)


def _evaluate_all_columns(
    df: pd.DataFrame,
    *,
    source: str,
    y_col: str,
    ret_col: str,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    deciles: dict[str, Any] = {}
    for col in score_columns_for_source(source, df):
        if col not in df.columns:
            continue
        prob_col = col if col == "p_up_calibrated" or col == "p_up_calibrated_proxy" else None
        pack = evaluate_score_column(df, col, y_col=y_col, ret_col=ret_col, prob_col=prob_col)
        if pack is None:
            continue
        metrics[col] = pack.to_dict()
        deciles[col] = decile_table(df, col, y_col=y_col, ret_col=ret_col)
    return {"metrics": metrics, "deciles": deciles}


def _era_metrics(
    df: pd.DataFrame,
    *,
    source: str,
    y_col: str,
    ret_col: str,
    min_rows: int = 40,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    cols = score_columns_for_source(source, df)
    if not cols:
        return out
    primary = "composite_score" if "composite_score" in cols else (
        "composite_score_proxy" if "composite_score_proxy" in cols else cols[0]
    )
    baseline = "signal_score" if "signal_score" in cols else cols[0]
    for era, group in df.groupby("era"):
        if len(group) < min_rows:
            continue
        comp_pack = evaluate_score_column(group, primary, y_col=y_col, ret_col=ret_col)
        base_pack = evaluate_score_column(group, baseline, y_col=y_col, ret_col=ret_col)
        if comp_pack is None or base_pack is None:
            continue
        out[str(era)] = {
            "n": int(len(group)),
            "composite_column": primary,
            "composite_ic": comp_pack.spearman_ic,
            "signal_ic": base_pack.spearman_ic,
            "composite_auc": comp_pack.auc,
            "signal_auc": base_pack.auc,
            "rank_ic": comp_pack.spearman_ic,
            "rank_auc": comp_pack.auc,
        }
    return out


def _era_ic_wins(
    df: pd.DataFrame,
    *,
    rank_col: str,
    baseline_col: str,
    y_col: str,
    ret_col: str,
    min_rows: int = 40,
) -> tuple[int, int]:
    wins = 0
    total = 0
    if rank_col not in df.columns or baseline_col not in df.columns:
        return wins, total
    for _, group in df.groupby("era"):
        if len(group) < min_rows:
            continue
        rank_pack = evaluate_score_column(group, rank_col, y_col=y_col, ret_col=ret_col)
        base_pack = evaluate_score_column(group, baseline_col, y_col=y_col, ret_col=ret_col)
        if rank_pack is None or base_pack is None:
            continue
        total += 1
        if float(rank_pack.spearman_ic) >= float(base_pack.spearman_ic):
            wins += 1
    return wins, total


def _apply_guardrails(
    report: dict[str, Any],
    *,
    strict: bool,
    source: str,
    min_era_rank_wins: int = 3,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    primary = str(report.get("primary_horizon") or "10d")
    horizon_block = (report.get("horizons") or {}).get(primary) or {}
    metrics = horizon_block.get("global", {}).get("metrics") or report.get("global", {}).get("metrics") or {}
    rank_lift = horizon_block.get("rank_lift") or report.get("rank_lift") or []
    era_metrics = horizon_block.get("era_metrics") or report.get("era_metrics") or {}

    if not metrics:
        if source == "trades":
            report["guardrail_note"] = (
                "Trade chunks lack score stack fields; regenerate chunks after backtest "
                "or run build_scoring_audit_dataset.py for component validation."
            )
            return True, []
        failures.append("no score metrics computed")
        return (not strict), failures

    using_proxy = report.get("score_stack_source") == "proxy"
    if using_proxy and strict:
        failures.append("dataset uses proxy score stack — rebuild with build_scoring_audit_dataset.py (no --skip-live-stack)")

    signal = metrics.get("signal_score") or {}
    composite_col = "composite_score" if "composite_score" in metrics else "composite_score_proxy"
    composite_row = metrics.get(composite_col) or next(
        (r for r in rank_lift if r.get("score_column") in {"composite_score", "composite_score_proxy"}),
        None,
    )
    rank_col = "rank_score" if "rank_score" in metrics else "rank_score_proxy"
    rank_row = metrics.get(rank_col) or next(
        (r for r in rank_lift if r.get("score_column") in {"rank_score", "rank_score_proxy"}),
        None,
    )
    if not signal:
        failures.append("missing signal_score metrics")
    elif float(signal.get("auc") or 0) < 0.48:
        failures.append(f"signal_score AUC below floor: {signal.get('auc')}")

    component_aucs = [
        float(metrics[c]["auc"])
        for c in ("pts_52w", "pts_sma", "pts_volume", "pts_mirofish")
        if c in metrics and metrics[c].get("auc") is not None and not math.isnan(float(metrics[c]["auc"]))
    ]
    if component_aucs and max(component_aucs) < 0.52:
        failures.append(f"no base component AUC >= 0.52 (max={max(component_aucs):.3f})")

    if composite_row and signal:
        composite_ic = float(composite_row.get("spearman_ic") or -999)
        signal_ic = float(signal.get("spearman_ic") or -999)
        report["composite_vs_signal"] = {
            "composite_ic": composite_ic,
            "signal_ic": signal_ic,
            "composite_column": composite_col,
        }
        if not math.isnan(composite_ic) and not math.isnan(signal_ic) and composite_ic + 1e-9 < signal_ic:
            failures.append(f"composite IC {composite_ic:.4f} below signal IC {signal_ic:.4f}")
        spread = composite_row.get("decile_spread")
        if spread is not None and float(spread) <= 0:
            failures.append(f"composite decile spread not positive: {spread}")

    rank_v2 = metrics.get("rank_score_v2") or {}
    if composite_row and rank_v2 and not math.isnan(float(rank_v2.get("spearman_ic") or float("nan"))):
        v2_ic = float(rank_v2.get("spearman_ic") or -999)
        composite_ic = float(composite_row.get("spearman_ic") or -999)
        report["composite_vs_v2"] = {
            "composite_ic": composite_ic,
            "rank_score_v2_ic": v2_ic,
        }
        if not math.isnan(composite_ic) and not math.isnan(v2_ic) and composite_ic + 1e-9 < v2_ic:
            report.setdefault("guardrail_notes", []).append(
                f"rank_score_v2 IC {v2_ic:.4f} beats composite IC {composite_ic:.4f} — v2 remains shadow/diagnostic"
            )

    if rank_row and composite_row:
        rank_ic = float(rank_row.get("spearman_ic") or -999)
        composite_ic = float(composite_row.get("spearman_ic") or -999)
        report["composite_vs_rank_v1"] = {"composite_ic": composite_ic, "rank_score_ic": rank_ic}

    era_composite_wins = 0
    for era_row in era_metrics.values():
        comp_ic = float(era_row.get("composite_ic") or era_row.get("rank_ic") or float("nan"))
        signal_ic = float(era_row.get("signal_ic") or float("nan"))
        if not math.isnan(comp_ic) and not math.isnan(signal_ic) and comp_ic >= signal_ic:
            era_composite_wins += 1
    min_wins = min(min_era_rank_wins, len(era_metrics)) if len(era_metrics) >= 2 else 0
    if min_wins > 0 and era_composite_wins < min_wins:
        failures.append(
            f"composite IC beat signal IC in only {era_composite_wins}/{len(era_metrics)} eras "
            f"(need {min_wins})",
        )

    if rank_v2 and rank_row and not math.isnan(float(rank_v2.get("spearman_ic") or float("nan"))):
        v2_ic = float(rank_v2.get("spearman_ic") or -999)
        v1_ic = float(rank_row.get("spearman_ic") or -999)
        signal_ic_v2 = float(signal.get("spearman_ic") or -999) if signal else -999
        report["rank_v2_vs_v1"] = {
            "rank_score_v2_ic": v2_ic,
            "rank_score_ic": v1_ic,
            "signal_ic": signal_ic_v2,
        }
        v2_spread = rank_v2.get("decile_spread")
        if v2_spread is not None and float(v2_spread) > 0:
            report["rank_v2_decile_spread_positive"] = True

    if failures and strict:
        return False, failures
    if failures:
        report["guardrail_warnings"] = failures
        return True, failures
    return True, []


def _evaluate_horizon(
    df: pd.DataFrame,
    *,
    source: str,
    y_col: str,
    ret_col: str,
    min_rows: int = 50,
) -> dict[str, Any]:
    work = df.dropna(subset=[y_col, ret_col]).copy()
    if len(work) < min_rows:
        return {"row_count": int(len(work)), "skipped": True}

    def _block(frame: pd.DataFrame) -> dict[str, Any]:
        return {
            "row_count": int(len(frame)),
            "global": _evaluate_all_columns(frame, source=source, y_col=y_col, ret_col=ret_col),
            "rank_lift": rank_lift_table(
                frame,
                y_col=y_col,
                ret_col=ret_col,
                rank_cols=score_columns_for_source(source, frame),
            ),
            "ablation": component_ablation(frame, y_col=y_col, ret_col=ret_col)
            if "pts_52w" in frame.columns
            else [],
            "sma_sensitivity": sma_multiplier_sensitivity(frame, y_col=y_col, ret_col=ret_col)
            if "pts_sma" in frame.columns and "signal_score" in frame.columns
            else [],
            "era_metrics": _era_metrics(frame, source=source, y_col=y_col, ret_col=ret_col),
        }

    out: dict[str, Any] = {
        "row_count": int(len(work)),
        "skipped": False,
        "label": {"y_col": y_col, "ret_col": ret_col},
        **_block(work),
    }
    if "mirofish_included" in work.columns:
        miro = work[work["mirofish_included"].astype(int) == 1].copy()
        if len(miro) >= max(20, min_rows // 2):
            out["mirofish_subset"] = {"row_count": int(len(miro)), **_block(miro)}
        elif len(miro) > 0:
            out["mirofish_subset"] = {"row_count": int(len(miro)), "skipped": True, "note": "insufficient mirofish rows"}
    return out


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Scoring Metrics Report",
        "",
        f"- Generated: {report.get('generated_at')}",
        f"- Source: {report.get('source')}",
        f"- Rows: {report.get('row_count')}",
        f"- Score stack: {report.get('score_stack_source')}",
        f"- Primary horizon: {report.get('primary_horizon')}",
        "",
    ]
    for horizon_key, block in (report.get("horizons") or {}).items():
        if block.get("skipped"):
            continue
        lines.extend([
            f"## Horizon {horizon_key}",
            "",
            "| Score | AUC | IC | Top10 ret | Monotonic |",
            "|---|---:|---:|---:|:---:|",
        ])
        for row in block.get("rank_lift") or []:
            lines.append(
                f"| {row.get('score_column')} | {float(row.get('auc') or 0):.3f} | "
                f"{float(row.get('spearman_ic') or 0):.4f} | "
                f"{float(row.get('top10_return_mean') or 0)*100:+.3f}% | "
                f"{'yes' if row.get('decile_monotonic') else 'no'} |"
            )
        lines.append("")
    primary = str(report.get("primary_horizon") or "")
    ablation = ((report.get("horizons") or {}).get(primary) or {}).get("ablation") or report.get("ablation") or []
    lines.extend(["## Component ablation (primary horizon)", ""])
    for row in ablation:
        lines.append(
            f"- **{row.get('removed_component')}**: AUC delta {float(row.get('auc_delta') or 0):+.4f}, "
            f"IC delta {float(row.get('ic_delta') or 0):+.4f}"
        )
    sma_sens = ((report.get("horizons") or {}).get(primary) or {}).get("sma_sensitivity") or report.get("sma_sensitivity") or []
    if sma_sens:
        lines.extend(["", "## SMA multiplier sensitivity (primary horizon)", ""])
        for row in sma_sens:
            lines.append(
                f"- mult={float(row.get('sma_multiplier', 0)):.1f}: "
                f"AUC {float(row.get('auc') or 0):.3f}, IC {float(row.get('spearman_ic') or 0):+.4f}"
            )
    miro_block = ((report.get("horizons") or {}).get(primary) or {}).get("mirofish_subset") or {}
    if miro_block and not miro_block.get("skipped"):
        lines.extend(["", "## MiroFish subset (primary horizon)", ""])
        lines.append(f"- Rows: {miro_block.get('row_count', 'n/a')}")
        miro_metrics = (miro_block.get("global") or {}).get("metrics") or {}
        for col in ("pts_mirofish", "signal_score", "rank_score"):
            row = miro_metrics.get(col) or {}
            if row:
                lines.append(
                    f"- {col}: AUC {float(row.get('auc', 0)):.3f}, "
                    f"IC {float(row.get('spearman_ic', 0)):+.4f}"
                )
    if report.get("guardrail_warnings"):
        lines.extend(["", "## Guardrail warnings", ""])
        for warn in report["guardrail_warnings"]:
            lines.append(f"- {warn}")
    if report.get("guardrail_note"):
        lines.extend(["", "## Note", "", str(report["guardrail_note"])])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate scoring components and composite rank.")
    parser.add_argument("--source", choices=("candidates", "trades"), default="candidates")
    parser.add_argument("--dataset", default="", help="Candidate CSV path")
    parser.add_argument("--run-id", default="control_legacy", help="Trade chunk run id when --source trades")
    parser.add_argument("--strict", action="store_true", help="Fail when guardrails not met")
    parser.add_argument("--skip-if-missing", action="store_true", help="Exit 0 when no dataset/chunks found")
    parser.add_argument(
        "--artifact-suffix",
        default="",
        help="Write scoring_metrics_report<SUFFIX>.json (e.g. _candidates).",
    )
    args = parser.parse_args()

    try:
        if args.source == "candidates":
            csv_path = _resolve_candidate_csv(args.dataset)
            if csv_path is None:
                if args.skip_if_missing:
                    print("PASS: scoring metrics skipped (no candidate dataset)")
                    return 0
                print(
                    "FAIL: no scoring audit dataset — run "
                    "python scripts/build_scoring_audit_dataset.py",
                )
                return 1
            df = _load_candidate_frame(csv_path)
            dataset_path = str(csv_path)
        else:
            df = _load_trade_frame(args.run_id)
            dataset_path = f"chunks:{args.run_id}"
    except FileNotFoundError as exc:
        if args.skip_if_missing:
            print(f"PASS: scoring metrics skipped ({exc})")
            return 0
        print(f"FAIL: {exc}")
        return 1

    if len(df) < 50:
        if args.skip_if_missing:
            print(f"PASS: scoring metrics skipped (only {len(df)} rows)")
            return 0
        print(f"FAIL: insufficient rows for scoring validation ({len(df)})")
        return 1

    if "score_stack_source" in df.columns and df["score_stack_source"].notna().any():
        stack_source = str(df["score_stack_source"].dropna().iloc[0])
    elif "score_stack_source" in df.columns and (df["score_stack_source"] == "live").any():
        stack_source = "live"
    elif has_live_stack(df):
        stack_source = "live"
    else:
        stack_source = "proxy"

    horizons_out: dict[str, Any] = {}
    if args.source == "candidates":
        for horizon_key, y_col, ret_col in CANDIDATE_HORIZONS:
            if y_col not in df.columns or ret_col not in df.columns:
                continue
            horizons_out[horizon_key] = _evaluate_horizon(
                df, source=args.source, y_col=y_col, ret_col=ret_col,
            )
        primary_horizon, y_col, ret_col = pick_primary_horizon(df, args.source)
    else:
        primary_horizon, y_col, ret_col = pick_primary_horizon(df, args.source)
        horizons_out[primary_horizon] = _evaluate_horizon(
            df, source=args.source, y_col=y_col, ret_col=ret_col,
        )

    primary_block = horizons_out.get(primary_horizon) or {}
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": args.source,
        "dataset": dataset_path,
        "row_count": int(len(df)),
        "score_stack_source": stack_source,
        "primary_horizon": primary_horizon,
        "horizons": horizons_out,
        "label": primary_block.get("label") or {"y_col": y_col, "ret_col": ret_col},
        "global": primary_block.get("global") or {},
        "era_metrics": primary_block.get("era_metrics") or {},
        "rank_lift": primary_block.get("rank_lift") or [],
        "ablation": primary_block.get("ablation") or [],
        "sma_sensitivity": primary_block.get("sma_sensitivity") or [],
    }
    from config import get_score_pts_sma_multiplier

    current_sma_mult = float(get_score_pts_sma_multiplier(SKILL_DIR))
    report["sma_multiplier_config"] = current_sma_mult
    if args.source == "candidates" and "rank_score_v2" in df.columns:
        v2_wins, v2_eras = _era_ic_wins(
            df.dropna(subset=[y_col, ret_col]),
            rank_col="rank_score_v2",
            baseline_col="signal_score",
            y_col=y_col,
            ret_col=ret_col,
        )
        report["rank_v2_era_ic_wins"] = {"wins": int(v2_wins), "eras": int(v2_eras)}
    ok, failures = _apply_guardrails(report, strict=args.strict, source=args.source)
    sma_sens = primary_block.get("sma_sensitivity") or []
    if sma_sens and args.source == "candidates":
        best = max(sma_sens, key=lambda r: float(r.get("auc") or 0))
        best_mult = float(best.get("sma_multiplier", 0))
        if abs(current_sma_mult - best_mult) > 1e-6:
            msg = (
                f"SCORE_PTS_SMA_MULTIPLIER={current_sma_mult} differs from offline best "
                f"mult={best_mult:.1f} (AUC {float(best.get('auc') or 0):.3f})"
            )
            if args.strict:
                failures.append(msg)
                ok = False
            else:
                report.setdefault("guardrail_warnings", []).append(msg)
    report["passes_guardrails"] = ok and not failures

    suffix = str(args.artifact_suffix or "")
    out_json = ARTIFACT_DIR / f"scoring_metrics_report{suffix}.json"
    out_md = ARTIFACT_DIR / f"scoring_metrics_report{suffix}.md"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    out_md.write_text(_render_markdown(report), encoding="utf-8")
    # Default artifact alias for downstream tools (prefer candidates).
    if args.source == "candidates" and not suffix:
        alias_json = ARTIFACT_DIR / "scoring_metrics_report.json"
        alias_md = ARTIFACT_DIR / "scoring_metrics_report.md"
        alias_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        alias_md.write_text(_render_markdown(report), encoding="utf-8")

    print(json.dumps({"ok": ok, "rows": len(df), "source": args.source, "out": str(out_json)}, indent=2))
    if failures:
        for msg in failures:
            print(f"{'FAIL' if args.strict else 'WARN'}: {msg}")
    if args.strict and not ok:
        return 1
    print("PASS: scoring metrics validation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
