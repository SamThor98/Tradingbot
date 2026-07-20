"""Experiment report package writer for prob-rank runs."""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.explain import global_importance, shap_summary


def _safe_pf(series: pd.Series) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return None
    wins = float(s[s > 0].sum())
    losses = float(-s[s <= 0].sum())
    if losses <= 0:
        return None if wins <= 0 else 99.0
    return round(wins / losses, 4)


def bootstrap_pf_ci(returns: pd.Series, *, n_boot: int = 200, seed: int = 42) -> dict[str, float | None]:
    rng = np.random.default_rng(seed)
    s = pd.to_numeric(returns, errors="coerce").dropna().to_numpy(dtype=np.float64)
    if len(s) < 10:
        return {"pf_mean": _safe_pf(pd.Series(s)), "pf_lo": None, "pf_hi": None}
    pfs: list[float] = []
    for _ in range(n_boot):
        sample = rng.choice(s, size=len(s), replace=True)
        pf = _safe_pf(pd.Series(sample))
        if pf is not None:
            pfs.append(float(pf))
    if not pfs:
        return {"pf_mean": None, "pf_lo": None, "pf_hi": None}
    return {
        "pf_mean": round(float(statistics.mean(pfs)), 4),
        "pf_lo": round(float(np.quantile(pfs, 0.025)), 4),
        "pf_hi": round(float(np.quantile(pfs, 0.975)), 4),
    }


def per_era_table(df: pd.DataFrame, *, score_col: str = "expected_return_40d") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "era" not in df.columns:
        return rows
    for era, grp in df.groupby("era"):
        ret_col = "net_return" if "net_return" in grp.columns else "ret_40d_fwd"
        if ret_col not in grp.columns:
            continue
        rows.append(
            {
                "era": str(era),
                "n": int(len(grp)),
                "pf": _safe_pf(grp[ret_col]),
                "mean_ret": float(pd.to_numeric(grp[ret_col], errors="coerce").mean()),
                "mean_score": float(pd.to_numeric(grp[score_col], errors="coerce").mean())
                if score_col in grp.columns
                else None,
            }
        )
    return rows


def calibration_deciles(df: pd.DataFrame, *, score_col: str, label_col: str) -> list[dict[str, Any]]:
    if score_col not in df.columns or label_col not in df.columns:
        return []
    work = df[[score_col, label_col]].copy()
    work[score_col] = pd.to_numeric(work[score_col], errors="coerce")
    work[label_col] = pd.to_numeric(work[label_col], errors="coerce")
    work = work.dropna()
    if len(work) < 20:
        return []
    work["bin"] = pd.qcut(work[score_col], 10, labels=False, duplicates="drop")
    out: list[dict[str, Any]] = []
    for b, grp in work.groupby("bin"):
        out.append(
            {
                "bin": int(b),
                "n": int(len(grp)),
                "mean_score": float(grp[score_col].mean()),
                "mean_label": float(grp[label_col].mean()),
            }
        )
    return out


def write_experiment_report(
    *,
    run_id: str,
    artifact: dict[str, Any],
    scored_df: pd.DataFrame,
    skill_dir: Path,
    retention: float | None = None,
    extra_metrics: dict[str, Any] | None = None,
) -> Path:
    """
    Write validation_artifacts/prob_rank/<run_id>/ report package.
    """
    out_dir = skill_dir / "validation_artifacts" / "prob_rank" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    ret_col = "net_return" if "net_return" in scored_df.columns else "ret_40d_fwd"
    metrics: dict[str, Any] = {
        "n": int(len(scored_df)),
        "pf": _safe_pf(scored_df[ret_col]) if ret_col in scored_df.columns else None,
        "mean_ret": float(pd.to_numeric(scored_df[ret_col], errors="coerce").mean())
        if ret_col in scored_df.columns
        else None,
        "retention": retention,
        "model_id": artifact.get("model_id"),
        "dataset_id": artifact.get("dataset_id"),
        "target_col": artifact.get("target_col"),
    }
    if extra_metrics:
        metrics.update(extra_metrics)

    # Walk-forward summary
    folds = (artifact.get("walk_forward") or {}).get("folds") or []
    if folds:
        ics = [float(f["metrics"]["ic"]) for f in folds if f.get("metrics", {}).get("ic") == f.get("metrics", {}).get("ic")]
        metrics["walk_forward_ic_mean"] = round(float(statistics.mean(ics)), 4) if ics else None
        metrics["walk_forward_fold_count"] = len(folds)

    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (out_dir / "per_era.json").write_text(json.dumps(per_era_table(scored_df), indent=2), encoding="utf-8")
    (out_dir / "feature_importance.json").write_text(
        json.dumps(global_importance(artifact), indent=2), encoding="utf-8"
    )
    shap_payload = shap_summary(artifact, scored_df)
    (out_dir / "shap_summary.json").write_text(json.dumps(shap_payload, indent=2), encoding="utf-8")
    (out_dir / "calibration.json").write_text(
        json.dumps(
            {
                "deciles_ret40": calibration_deciles(
                    scored_df, score_col="expected_return_40d", label_col="ret_40d_fwd"
                ),
                "score_pf_bins": artifact.get("score_pf_calibration") or [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if ret_col in scored_df.columns:
        (out_dir / "bootstrap.json").write_text(
            json.dumps(bootstrap_pf_ci(scored_df[ret_col]), indent=2), encoding="utf-8"
        )
    else:
        (out_dir / "bootstrap.json").write_text(json.dumps({"pf_mean": None}, indent=2), encoding="utf-8")

    # Classification head optional diagnostics
    pr_roc: dict[str, Any] = {}
    if "y_up_40d" in scored_df.columns and "expected_return_40d" in scored_df.columns:
        y = pd.to_numeric(scored_df["y_up_40d"], errors="coerce")
        s = pd.to_numeric(scored_df["expected_return_40d"], errors="coerce")
        mask = y.notna() & s.notna()
        if mask.sum() >= 20:
            # Simple AUC via sampled pairwise ranking
            yy = y[mask].to_numpy()
            ss = s[mask].to_numpy()
            pos = np.where(yy == 1)[0]
            neg = np.where(yy == 0)[0]
            if len(pos) and len(neg):
                correct = 0
                total = 0
                # sample pairs if huge
                rng = np.random.default_rng(42)
                pos_s = rng.choice(pos, size=min(500, len(pos)), replace=False)
                neg_s = rng.choice(neg, size=min(500, len(neg)), replace=False)
                for i in pos_s:
                    for j in neg_s:
                        total += 1
                        if ss[i] > ss[j]:
                            correct += 1
                        elif ss[i] == ss[j]:
                            correct += 0.5
                pr_roc["auc_approx"] = round(correct / total, 4) if total else None
                pr_roc["positive_rate"] = round(float(yy.mean()), 4)
    (out_dir / "pr_roc.json").write_text(json.dumps(pr_roc, indent=2), encoding="utf-8")

    # Regime attribution stub (era already covers primary buckets)
    (out_dir / "regime_attribution.json").write_text(
        json.dumps({"by_era": per_era_table(scored_df)}, indent=2), encoding="utf-8"
    )

    # Strip non-serializable from artifact copy
    art_safe = {k: v for k, v in artifact.items() if not k.startswith("_")}
    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": artifact.get("model_id"),
        "dataset_id": artifact.get("dataset_id"),
        "schema_version": 1,
        "artifact": art_safe,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out_dir
