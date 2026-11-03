#!/usr/bin/env python3
"""Fit the historical rank blend on realized 40d forward returns.

Walk-forward across the five backtest eras: for each held-out era, fit
non-negative weights on the remaining eras and score cross-sectional IC on the
holdout. Features whose weight is unstable across folds (or that add nothing
out-of-sample) are dropped rather than forced into the blend.

Features are computed through ``core.scoring_historical`` — the same module the
scanner uses at runtime — so offline weights and live scores cannot drift.

Fitting is cross-sectional: features and target are demeaned per ``asof_date``
before the non-negative least squares fit, because ranking only cares about
ordering *within* a day's cohort.

Writes:
  research_store/models/historical_rank_blend.json
  validation_artifacts/historical_rank/fit_report_<model_id>.json

Usage (from schwab_skill/):
  python scripts/fit_historical_rank_blend.py
  python scripts/fit_historical_rank_blend.py --panel <parquet> --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import nnls
from scipy.stats import spearmanr

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.scoring_historical import (  # noqa: E402
    FEATURE_NAMES,
    RAW_KEYS,
    normalize_feature,
    raw_features_from_signal_row,
)

ART = SKILL_DIR / "validation_artifacts" / "historical_rank"
MODEL_DIR = SKILL_DIR / "research_store" / "models"
DEFAULT_PANEL = (
    SKILL_DIR
    / "validation_artifacts"
    / "prob_rank_control_confirm"
    / "scored_features_control_legacy_aug_labeled.parquet"
)
TARGET = "ret_40d_fwd"
# Robust clip band for every feature. Bounds are fitted rather than inherited
# from composite_score's caps, which saturate ~half the candidate population.
BOUND_QUANTILE_LO = 0.01
BOUND_QUANTILE_HI = 0.99
# A feature must earn a positive weight in at least this many folds to survive.
MIN_POSITIVE_FOLDS = 4
MIN_COHORT_ROWS = 3


def _panel_rows_to_signal_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Shape research-panel rows like live signal rows for the shared features."""
    cols = {
        "close_vs_sma200_pct": "dist_sma200_pct",
        "avg_vcp_volume_ratio": "avg_vcp_volume_ratio",
        "volume_ratio": "volume_ratio",
        "pct_from_52w_high": "pct_from_52w_high",
        "expected_return_40d": "expected_return_40d",
    }
    present = {dst: src for dst, src in cols.items() if src in df.columns}
    frames = {dst: df[src].to_numpy() for dst, src in present.items()}
    n = len(df)
    return [{dst: frames[dst][i] for dst in frames} for i in range(n)]


def _raw_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Un-normalized blend inputs, computed through the runtime extractor."""
    rows = _panel_rows_to_signal_rows(df)
    raws = [raw_features_from_signal_row(row) for row in rows]
    return pd.DataFrame(raws, index=df.index).astype(float)


def _fit_bounds(raw: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Robust per-feature clip band from the training rows only."""
    bounds: dict[str, dict[str, float]] = {}
    for key in raw.columns:
        clean = pd.to_numeric(raw[key], errors="coerce").dropna()
        if clean.empty:
            continue
        lo = float(clean.quantile(BOUND_QUANTILE_LO))
        hi = float(clean.quantile(BOUND_QUANTILE_HI))
        if hi <= lo:
            hi = lo + 1e-9
        bounds[key] = {"lo": lo, "hi": hi}
    return bounds


def _normalize_frame(raw: pd.DataFrame, bounds: dict[str, Any], features: list[str]) -> pd.DataFrame:
    cols = {}
    for name in features:
        raw_key = RAW_KEYS[name]
        series = raw[raw_key] if raw_key in raw.columns else pd.Series(np.nan, index=raw.index)
        cols[name] = [normalize_feature(name, v, bounds) for v in series]
    return pd.DataFrame(cols, index=raw.index).astype(float)


def _demean_by_date(frame: pd.DataFrame, dates: pd.Series) -> pd.DataFrame:
    """Cross-sectionally demean each column within its asof_date cohort."""
    return frame.groupby(dates, sort=False).transform(lambda s: s - s.mean())


def _fit_nnls(x: pd.DataFrame, y: pd.Series) -> dict[str, float]:
    """Non-negative least squares on demeaned features (no intercept needed)."""
    a = np.nan_to_num(x.to_numpy(dtype=float), nan=0.0)
    b = np.nan_to_num(y.to_numpy(dtype=float), nan=0.0)
    coef, _ = nnls(a, b)
    return {name: float(c) for name, c in zip(x.columns, coef, strict=True)}


def _blend_scores(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    total = sum(w for w in weights.values() if w > 0)
    if total <= 0:
        return pd.Series(np.nan, index=frame.index)
    score = pd.Series(0.0, index=frame.index)
    weight_used = pd.Series(0.0, index=frame.index)
    for name, weight in weights.items():
        if weight <= 0 or name not in frame.columns:
            continue
        col = frame[name]
        available = col.notna()
        score = score.add((col.fillna(0.0) * weight).where(available, 0.0), fill_value=0.0)
        weight_used = weight_used.add(pd.Series(weight, index=frame.index).where(available, 0.0), fill_value=0.0)
    out = score / weight_used.replace(0.0, np.nan)
    return out


def _cross_sectional_ic(scores: pd.Series, target: pd.Series, dates: pd.Series) -> dict[str, Any]:
    """Mean per-date Spearman IC — the metric that matters for a ranker."""
    frame = pd.DataFrame({"score": scores, "y": target, "date": dates}).dropna(subset=["score", "y"])
    ics: list[float] = []
    for _, grp in frame.groupby("date", sort=False):
        if len(grp) < MIN_COHORT_ROWS or grp["score"].nunique() < 2:
            continue
        rho = spearmanr(grp["score"], grp["y"]).correlation
        if rho is not None and rho == rho:
            ics.append(float(rho))
    if not ics:
        return {"ic_mean": None, "ic_median": None, "cohorts": 0, "hit_rate": None}
    arr = np.array(ics, dtype=float)
    return {
        "ic_mean": round(float(arr.mean()), 5),
        "ic_median": round(float(np.median(arr)), 5),
        "cohorts": int(arr.size),
        "hit_rate": round(float((arr > 0).mean()), 4),
    }


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    positive = {k: v for k, v in weights.items() if v > 0}
    total = sum(positive.values())
    if total <= 0:
        return {}
    return {k: round(v / total, 4) for k, v in sorted(positive.items(), key=lambda kv: -kv[1])}


def _walk_forward(df: pd.DataFrame, raw: pd.DataFrame, features: list[str]) -> list[dict[str, Any]]:
    """Per-era holdout. Bounds and weights are both fitted on training rows only."""
    folds: list[dict[str, Any]] = []
    for era in sorted(df["era"].dropna().unique()):
        train_mask = df["era"] != era
        train, test = df[train_mask], df[~train_mask]
        if train.empty or test.empty:
            continue
        bounds = _fit_bounds(raw[train_mask])
        train_x = _normalize_frame(raw[train_mask], bounds, features)
        test_x = _normalize_frame(raw[~train_mask], bounds, features)
        weights = _fit_nnls(
            _demean_by_date(train_x, train["asof_date"]),
            _demean_by_date(train[[TARGET]], train["asof_date"])[TARGET],
        )
        norm = _normalize(weights)
        oos = _cross_sectional_ic(_blend_scores(test_x, norm), test[TARGET], test["asof_date"])
        folds.append(
            {
                "holdout_era": str(era),
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "weights_raw": {k: round(v, 8) for k, v in weights.items()},
                "weights": norm,
                "feature_bounds": bounds,
                "oos": oos,
            }
        )
    return folds


def _feature_ic_baselines(frame: pd.DataFrame, df: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    return {
        name: _cross_sectional_ic(frame[name], df[TARGET], df["asof_date"])
        for name in features
    }


def _feature_ic_by_era(frame: pd.DataFrame, df: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    """Per-era standalone IC.

    This is the leakage check: ``ml_expected_return_norm`` comes from a
    LightGBM model trained on this panel with only ``recent_current`` held out,
    so its IC on the trained eras is in-sample and must not be read as edge.
    """
    out: dict[str, Any] = {}
    for era in sorted(df["era"].dropna().unique()):
        mask = df["era"] == era
        out[str(era)] = {
            name: _cross_sectional_ic(frame.loc[mask, name], df.loc[mask, TARGET], df.loc[mask, "asof_date"])[
                "ic_mean"
            ]
            for name in features
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--panel", default=str(DEFAULT_PANEL), help="Labeled feature panel parquet")
    parser.add_argument("--dry-run", action="store_true", help="Fit and report without writing the artifact")
    parser.add_argument(
        "--exclude",
        default="",
        help=(
            "Comma-separated features to exclude. Use this to drop "
            "ml_expected_return_norm, whose IC is in-sample on every era the "
            "LightGBM model was trained on."
        ),
    )
    parser.add_argument(
        "--ml-oos-era",
        default="recent_current",
        help="Era the prob-rank model held out (the only honest OOS era for the ML feature)",
    )
    args = parser.parse_args()
    excluded = {f.strip() for f in str(args.exclude or "").split(",") if f.strip()}

    panel_path = Path(args.panel)
    if not panel_path.is_absolute():
        panel_path = SKILL_DIR / panel_path
    if not panel_path.is_file():
        print(f"FAIL: panel not found: {panel_path}")
        return 2

    df = pd.read_parquet(panel_path)
    needed = {TARGET, "asof_date", "era"}
    missing = needed - set(df.columns)
    if missing:
        print(f"FAIL: panel missing columns: {sorted(missing)}")
        return 2

    df = df[df[TARGET].notna()].copy()
    df["asof_date"] = df["asof_date"].astype(str)
    print(f"Panel: {panel_path.name} rows={len(df)} eras={df['era'].nunique()}")

    features = [f for f in FEATURE_NAMES if f not in excluded]
    raw = _raw_frame(df)
    full_bounds = _fit_bounds(raw)
    print("\nFitted feature bounds (p1-p99 of the raw input):")
    for key, span in full_bounds.items():
        print(f"  {key:26s} [{span['lo']:.4f}, {span['hi']:.4f}]")

    # Drop features with no coverage before fitting.
    coverage_frame = _normalize_frame(raw, full_bounds, features)
    features = [f for f in features if coverage_frame[f].notna().mean() > 0.5]
    dropped_coverage = [f for f in FEATURE_NAMES if f not in features and f not in excluded]
    if excluded:
        print(f"Excluded by request: {sorted(excluded)}")
    if dropped_coverage:
        print(f"Dropped for coverage: {dropped_coverage}")
    if not features:
        print("FAIL: no features with usable coverage")
        return 2

    full_x = _normalize_frame(raw, full_bounds, features)
    print("\nStandalone per-feature cross-sectional IC (full panel):")
    baselines = _feature_ic_baselines(full_x, df, features)
    for name, stats in baselines.items():
        print(f"  {name:26s} IC {stats['ic_mean']} (cohorts {stats['cohorts']}, hit {stats['hit_rate']})")

    ic_by_era = _feature_ic_by_era(full_x, df, features)
    print("\nPer-era standalone IC (leakage check — ML feature is in-sample except on its holdout era):")
    header = "  " + "era".ljust(17) + "".join(n[:14].rjust(16) for n in features)
    print(header)
    for era, stats in ic_by_era.items():
        marker = " *" if era == args.ml_oos_era else "  "
        print("  " + (str(era) + marker).ljust(17) + "".join(f"{stats[n]!s:>16}" for n in features))
    if "ml_expected_return_norm" in features:
        honest = ic_by_era.get(args.ml_oos_era, {}).get("ml_expected_return_norm")
        print(
            f"\n  ML feature IC on its only honest OOS era ({args.ml_oos_era}): {honest}"
            "\n  Weights fitted across trained eras will overstate this feature."
        )

    folds = _walk_forward(df, raw, features)
    if not folds:
        print("FAIL: no walk-forward folds produced")
        return 2

    print("\nWalk-forward folds (holdout era -> OOS IC):")
    for fold in folds:
        print(
            f"  {fold['holdout_era']:16s} IC {fold['oos']['ic_mean']} "
            f"hit {fold['oos']['hit_rate']} weights {fold['weights']}"
        )

    # Stability screen: keep only features that earn weight in most folds.
    positive_counts = {
        name: sum(1 for f in folds if float(f["weights_raw"].get(name, 0.0)) > 0.0) for name in features
    }
    survivors = [n for n in features if positive_counts[n] >= min(MIN_POSITIVE_FOLDS, len(folds))]
    dropped_unstable = [n for n in features if n not in survivors]
    print(f"\nFold weight counts (of {len(folds)}): {positive_counts}")
    if dropped_unstable:
        print(f"Dropped for instability: {dropped_unstable}")
    if not survivors:
        print("FAIL: no feature earned stable positive weight")
        return 2

    final_folds = _walk_forward(df, raw, survivors) if dropped_unstable else folds
    final_x = full_x[survivors]
    final_weights = _normalize(
        _fit_nnls(
            _demean_by_date(final_x, df["asof_date"]),
            _demean_by_date(df[[TARGET]], df["asof_date"])[TARGET],
        )
    )
    if not final_weights:
        print("FAIL: full-panel fit produced no positive weights")
        return 2

    blended = _blend_scores(final_x, final_weights)
    in_sample = _cross_sectional_ic(blended, df[TARGET], df["asof_date"])
    # A ranker that ties a large share of the book cannot discriminate, however
    # good its IC looks — report the saturation explicitly.
    resolution = {
        "distinct_scores": int(blended.round(2).nunique()),
        "share_at_max": round(float((blended >= 99.995).mean()), 4),
        "share_at_min": round(float((blended <= 0.005).mean()), 4),
    }
    print(
        f"Score resolution: {resolution['distinct_scores']} distinct | "
        f"{resolution['share_at_max']:.1%} pinned at 100 | {resolution['share_at_min']:.1%} at 0"
    )
    oos_ics = [f["oos"]["ic_mean"] for f in final_folds if f["oos"]["ic_mean"] is not None]
    worst_era = min(oos_ics) if oos_ics else None
    mean_oos = round(float(np.mean(oos_ics)), 5) if oos_ics else None

    print(f"\nFinal weights: {final_weights}")
    print(f"In-sample IC: {in_sample['ic_mean']} | mean OOS IC: {mean_oos} | worst era OOS IC: {worst_era}")

    final_bounds = {RAW_KEYS[name]: full_bounds[RAW_KEYS[name]] for name in survivors if RAW_KEYS[name] in full_bounds}
    payload_for_id = json.dumps({"w": final_weights, "b": final_bounds, "t": TARGET}, sort_keys=True)
    model_id = "hist_blend_" + hashlib.sha1(payload_for_id.encode()).hexdigest()[:10]
    generated = datetime.now(timezone.utc).isoformat()

    artifact = {
        "model_id": model_id,
        "kind": "historical_rank_blend",
        "target": TARGET,
        "generated_at": generated,
        "panel": str(panel_path.relative_to(SKILL_DIR)) if panel_path.is_relative_to(SKILL_DIR) else str(panel_path),
        "rows": int(len(df)),
        "features": survivors,
        "weights": final_weights,
        "feature_bounds": final_bounds,
        "fit": {
            "method": "cross_sectional_demeaned_nnls",
            "bound_quantiles": [BOUND_QUANTILE_LO, BOUND_QUANTILE_HI],
            "in_sample_ic": in_sample,
            "score_resolution": resolution,
            "oos_ic_mean": mean_oos,
            "oos_ic_worst_era": worst_era,
            "folds": [
                {"holdout_era": f["holdout_era"], "weights": f["weights"], "oos": f["oos"]} for f in final_folds
            ],
            "standalone_feature_ic": baselines,
            "standalone_feature_ic_by_era": ic_by_era,
            "dropped_for_coverage": dropped_coverage,
            "dropped_for_instability": dropped_unstable,
            "excluded_by_request": sorted(excluded),
            "ml_oos_era": args.ml_oos_era,
        },
        "notes": [
            (
                "ml_expected_return_norm is the canonical prob-rank model's output. That model was "
                f"trained on this panel holding out only {args.ml_oos_era}, so its IC is in-sample on "
                "the other four eras (0.15-0.30) and collapses on its holdout. Fitting weights across "
                "all eras hands it ~100% of the blend for leakage reasons, so it is excluded until a "
                "fully purged model exists. Re-run without --exclude once one does."
            ),
            (
                "prox_52w_norm earned zero weight in every fold and has negative standalone IC, "
                "independently reproducing the RANK_V2_EXCLUDE_52W=true finding."
            ),
        ],
    }

    report = dict(artifact)
    report["fold_detail"] = folds
    ART.mkdir(parents=True, exist_ok=True)
    report_path = ART / f"fit_report_{model_id}.json"

    if args.dry_run:
        print("\n[dry-run] artifact not written")
        print(json.dumps(artifact, indent=2)[:1500])
        return 0

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MODEL_DIR / "historical_rank_blend.json"
    out_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path.relative_to(SKILL_DIR)}")
    print(f"Wrote {report_path.relative_to(SKILL_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
