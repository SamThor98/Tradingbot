"""Offline scoring validity metrics for component and composite rank evaluation."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ERA_BOUNDS: dict[str, tuple[str, str | None]] = {
    "late_bull": ("2015-01-01", "2017-12-31"),
    "volatility_chop": ("2018-01-01", "2019-12-31"),
    "crash_recovery": ("2020-01-01", "2021-12-31"),
    "bear_rates": ("2022-01-01", "2023-12-31"),
    "recent_current": ("2024-01-01", None),
}

# Candidate audit horizons — 40d aligns with promoted hold policy.
CANDIDATE_HORIZONS: list[tuple[str, str, str]] = [
    ("40d", "y_up_40d", "ret_40d_fwd"),
    ("20d", "y_up_20d", "ret_20d_fwd"),
    ("10d", "y_up_10d", "ret_10d_fwd"),
]


def pick_primary_horizon(df: pd.DataFrame, source: str) -> tuple[str, str, str]:
    if source == "trades":
        return ("trade", "y_win", "net_return")
    for spec in CANDIDATE_HORIZONS:
        _, y_col, ret_col = spec
        if y_col in df.columns and ret_col in df.columns:
            if int(df[[y_col, ret_col]].dropna().shape[0]) >= 50:
                return spec
    return ("10d", "y_up_10d", "ret_10d_fwd")


def assign_era(entry_dates: pd.Series) -> pd.Series:
    """Map entry dates to multi-era labels used elsewhere in validation."""
    dt = pd.to_datetime(entry_dates, errors="coerce")
    out = pd.Series(["unknown"] * len(dt), index=dt.index, dtype="object")
    for era, (start, end) in ERA_BOUNDS.items():
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end) if end else pd.Timestamp.max
        mask = (dt >= start_ts) & (dt <= end_ts)
        out.loc[mask] = era
    return out


def roc_auc_score_manual(y_true: np.ndarray, y_score: np.ndarray) -> float:
    y_true = y_true.astype(int)
    pos = y_true == 1
    neg = y_true == 0
    n_pos = int(pos.sum())
    n_neg = int(neg.sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = pd.Series(y_score).rank(method="average").to_numpy()
    sum_ranks_pos = float(ranks[pos].sum())
    return (sum_ranks_pos - (n_pos * (n_pos + 1) / 2.0)) / (n_pos * n_neg)


def average_precision_manual(y_true: np.ndarray, y_score: np.ndarray) -> float:
    order = np.argsort(-y_score)
    y = y_true[order].astype(int)
    tp = 0
    fp = 0
    precisions: list[float] = []
    recalls: list[float] = []
    total_pos = int(y.sum())
    if total_pos == 0:
        return float("nan")
    for val in y:
        if val == 1:
            tp += 1
        else:
            fp += 1
        precisions.append(tp / max(tp + fp, 1))
        recalls.append(tp / total_pos)
    ap = 0.0
    prev_recall = 0.0
    for p, r in zip(precisions, recalls):
        ap += p * max(0.0, r - prev_recall)
        prev_recall = r
    return ap


def ndcg_at_k(y_true: np.ndarray, y_score: np.ndarray, k: int) -> float:
    if k <= 0:
        return float("nan")
    order = np.argsort(-y_score)[:k]
    rel = y_true[order]
    gains = np.power(2.0, rel) - 1.0
    discounts = np.log2(np.arange(2, len(rel) + 2))
    dcg = float((gains / discounts).sum())
    ideal = np.sort(y_true)[::-1][:k]
    ideal_gains = np.power(2.0, ideal) - 1.0
    idcg = float((ideal_gains / discounts).sum())
    if idcg <= 0:
        return float("nan")
    return dcg / idcg


def spearman_corr(a: pd.Series | np.ndarray, b: pd.Series | np.ndarray) -> float:
    sa = pd.to_numeric(pd.Series(a), errors="coerce")
    sb = pd.to_numeric(pd.Series(b), errors="coerce")
    mask = ~(sa.isna() | sb.isna())
    sa = sa[mask]
    sb = sb[mask]
    if len(sa) < 3 or sa.nunique() <= 1 or sb.nunique() <= 1:
        return float("nan")
    ra = sa.rank(method="average")
    rb = sb.rank(method="average")
    val = ra.corr(rb, method="pearson")
    return float(val) if pd.notna(val) else float("nan")


@dataclass
class MetricPack:
    n: int
    auc: float
    pr_auc: float
    brier: float | None
    logloss: float | None
    spearman_ic: float
    top10_precision: float
    top5_precision: float
    top10_return_mean: float
    top5_return_mean: float
    ndcg10: float
    decile_monotonic: bool
    decile_spread: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decile_table(
    df: pd.DataFrame,
    score_col: str,
    *,
    y_col: str,
    ret_col: str,
) -> list[dict[str, Any]]:
    work = df[[score_col, y_col, ret_col]].dropna()
    if work.empty:
        return []
    try:
        bins = pd.qcut(work[score_col], q=10, duplicates="drop")
    except ValueError:
        return []
    out: list[dict[str, Any]] = []
    for i, (_, group) in enumerate(work.groupby(bins, observed=False), start=1):
        out.append(
            {
                "bin": i,
                "n": int(len(group)),
                "mean_score": float(group[score_col].mean()),
                "hit_rate": float(group[y_col].mean()),
                "avg_return": float(group[ret_col].mean()),
            }
        )
    return out


def decile_monotonicity(deciles: list[dict[str, Any]], *, field: str = "avg_return") -> tuple[bool, float | None]:
    if len(deciles) < 3:
        return False, None
    vals = [float(row[field]) for row in deciles if row.get(field) is not None]
    if len(vals) < 3:
        return False, None
    increases = sum(1 for i in range(1, len(vals)) if vals[i] >= vals[i - 1])
    monotonic = increases >= max(2, len(vals) - 2)
    spread = vals[-1] - vals[0]
    return monotonic, spread


def evaluate_score_column(
    df: pd.DataFrame,
    score_col: str,
    *,
    y_col: str,
    ret_col: str,
    prob_col: str | None = None,
) -> MetricPack | None:
    work = df[[score_col, y_col, ret_col]].copy()
    if prob_col and prob_col in df.columns:
        work[prob_col] = df[prob_col]
    work = work.dropna(subset=[score_col, y_col, ret_col])
    if len(work) < 30:
        return None

    y = work[y_col].astype(int).to_numpy()
    s = work[score_col].astype(float).to_numpy()
    ret = work[ret_col].astype(float).to_numpy()

    n = len(work)
    k10 = max(1, int(n * 0.10))
    k05 = max(1, int(n * 0.05))
    top10 = work.nlargest(k10, score_col)
    top05 = work.nlargest(k05, score_col)

    ret_norm = (ret - np.nanmin(ret)) / max(np.nanmax(ret) - np.nanmin(ret), 1e-9)
    deciles = decile_table(work, score_col, y_col=y_col, ret_col=ret_col)
    mono, spread = decile_monotonicity(deciles)

    brier = None
    logloss = None
    if prob_col and prob_col in work.columns:
        p = work[prob_col].astype(float).clip(1e-6, 1 - 1e-6).to_numpy()
        brier = float(np.mean((p - y) ** 2))
        logloss = float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))

    return MetricPack(
        n=n,
        auc=float(roc_auc_score_manual(y, s)),
        pr_auc=float(average_precision_manual(y, s)),
        brier=brier,
        logloss=logloss,
        spearman_ic=spearman_corr(work[score_col], work[ret_col]),
        top10_precision=float(top10[y_col].mean()),
        top5_precision=float(top05[y_col].mean()),
        top10_return_mean=float(top10[ret_col].mean()),
        top5_return_mean=float(top05[ret_col].mean()),
        ndcg10=float(ndcg_at_k(ret_norm, s, k10)),
        decile_monotonic=bool(mono),
        decile_spread=spread,
    )


def portfolio_curve_by_date(
    df: pd.DataFrame,
    score_col: str,
    *,
    ret_col: str,
    date_col: str = "entry_date",
    top_frac: float = 0.1,
) -> dict[str, float]:
    curves: list[tuple[Any, float]] = []
    for _, group in df.groupby(date_col):
        n = len(group)
        if n <= 0:
            continue
        k = max(1, int(n * top_frac))
        top = group.nlargest(k, score_col)
        curves.append((group[date_col].iloc[0], float(top[ret_col].mean())))
    if not curves:
        return {"avg_return": float("nan"), "max_drawdown": float("nan"), "profit_factor": float("nan"), "trades": 0}
    ordered = sorted(curves, key=lambda row: row[0])
    r = pd.Series([x[1] for x in ordered], dtype=float)
    eq = (1.0 + r).cumprod()
    peak = eq.cummax()
    dd = (eq / peak) - 1.0
    wins = float(r[r > 0].sum())
    losses = float(-r[r <= 0].sum())
    pf = wins / losses if losses > 0 else math.inf
    return {
        "avg_return": float(r.mean()),
        "max_drawdown": float(dd.min()),
        "profit_factor": float(pf if math.isfinite(pf) else 999.0),
        "trades": int(len(r)),
    }


def enrich_candidate_scores(
    df: pd.DataFrame,
    *,
    stage2_floor: float = 0.75,
    exclude_52w: bool = True,
    edge_signal_weight: float = 0.90,
    edge_pup_weight: float = 0.10,
    composite_edge_weight: float = 0.0,
    composite_reliability_weight: float = 0.0,
    composite_execution_weight: float = 0.0,
    direct_trend_weight: float = 0.70,
    direct_volume_weight: float = 0.20,
    direct_signal_weight: float = 0.05,
    direct_mirofish_weight: float = 0.05,
) -> pd.DataFrame:
    """Reconstruct base components and stack proxy scores on advisory-style rows."""
    out = df.copy()
    floor_span = max(0.01, 1.0 - stage2_floor)
    out["pts_52w"] = ((out["pct_from_52w_high"] - stage2_floor) / floor_span).clip(lower=0) * 40.0
    out["pts_sma"] = (out["close_vs_sma200_pct"] * 100.0).clip(lower=0, upper=25.0)
    out["pts_volume"] = (20.0 - (out["avg_vcp_volume_ratio"] * 20.0)).clip(lower=0.0)
    out["pts_mirofish"] = (
        out["signal_score"] - out["pts_52w"] - out["pts_sma"] - out["pts_volume"]
    ).clip(lower=0.0, upper=15.0)

    edge_signal = out["signal_score"].astype(float)
    if exclude_52w:
        edge_signal = (edge_signal - out["pts_52w"]).clip(lower=0.0, upper=100.0)

    p_raw = 0.5 + ((out["signal_score"] - 50.0) / 150.0)
    p_up = (0.5 + ((p_raw - 0.5) * 0.65)).clip(0.01, 0.99)
    out["p_up_calibrated_proxy"] = p_up
    out["edge_score_proxy"] = (
        (edge_signal_weight * edge_signal) + (edge_pup_weight * (p_up * 100.0))
    ).clip(0, 100)

    sec_values = pd.to_numeric(out["sec_risk_score"], errors="coerce")
    # Advisory encoding: high risk is -1, low risk is +1. Zero stays unknown.
    sec_tag = np.where(
        sec_values <= -0.5,
        "high",
        np.where(sec_values >= 0.5, "low", "unknown"),
    )
    if "advisory_confidence_bucket" in out.columns or "confidence_bucket" in out.columns:
        bucket_col = "advisory_confidence_bucket" if "advisory_confidence_bucket" in out.columns else "confidence_bucket"
        buckets = out[bucket_col].astype(str).str.lower()
        reliability = np.full(len(out), 82.0)
        reliability += np.where(buckets == "high", 12.0, 0.0)
        reliability += np.where(buckets == "medium", 4.0, 0.0)
        reliability -= np.where(buckets == "low", 12.0, 0.0)
        reliability -= np.where(buckets == "unknown", 8.0, 0.0)
        if "advisory_feature_coverage" in out.columns:
            coverage = pd.to_numeric(out["advisory_feature_coverage"], errors="coerce").fillna(0.55)
            reliability += ((coverage - 0.55) * 24.0).clip(-12.0, 12.0)
        reliability -= np.where(sec_tag == "high", 8.0, 0.0)
        reliability = np.clip(reliability, 0.0, 100.0)
    else:
        from core.scoring_reliability import reliability_series_from_frame

        proxy_rows = out.copy()
        if "advisory" not in proxy_rows.columns and "p_up_calibrated" in proxy_rows.columns:
            proxy_rows["advisory"] = [
                {"confidence_bucket": "medium", "p_up_10d": float(p) if pd.notna(p) else 0.5}
                for p in pd.to_numeric(proxy_rows["p_up_calibrated"], errors="coerce").fillna(0.5)
            ]
        reliability = reliability_series_from_frame(proxy_rows, context="backtest").to_numpy()
    out["reliability_score_proxy"] = reliability

    execution = np.full(len(out), 100.0)
    execution -= np.where(out["volume_ratio"] < 0.7, 20.0, np.where(out["volume_ratio"] < 0.9, 10.0, 0.0))
    execution -= np.where(sec_tag == "high", 15.0, np.where(sec_tag == "medium", 7.0, 0.0))
    execution -= np.where(out["breakout_confirmed"].astype(int) == 1, 0.0, 8.0)
    execution = np.clip(execution, 0.0, 100.0)
    out["execution_score_proxy"] = execution

    from core.scoring_composite import CompositeQualityWeights, compute_composite_quality_series

    proxy_weights = CompositeQualityWeights(
        direct_trend_weight=direct_trend_weight,
        direct_volume_weight=direct_volume_weight,
        direct_signal_weight=direct_signal_weight,
        direct_mirofish_weight=direct_mirofish_weight,
        edge_signal_weight=edge_signal_weight,
        edge_pup_weight=edge_pup_weight,
        composite_edge_weight=composite_edge_weight,
        composite_reliability_weight=composite_reliability_weight,
        composite_execution_weight=composite_execution_weight,
        exclude_52w=exclude_52w,
        safety_caps_only=True,
    )
    out["composite_score_proxy"] = compute_composite_quality_series(out, proxy_weights)

    composite = out["composite_score_proxy"]
    rank_base = (
        (composite * 0.75)
        + ((p_up * 100.0) * 0.15)
        + (execution * 0.05)
        + (reliability * 0.05)
    )
    rank = np.where(reliability < 40.0, np.minimum(rank_base, 55.0), rank_base)
    rank = np.where(execution < 45.0, np.minimum(rank, 58.0), rank)
    rank = np.where(sec_tag == "high", np.minimum(rank, 45.0), rank)
    out["rank_score_proxy"] = np.clip(rank, 0.0, 100.0)
    return out


def component_ablation(
    df: pd.DataFrame,
    *,
    y_col: str,
    ret_col: str,
    baseline_col: str = "signal_score",
) -> list[dict[str, Any]]:
    """Leave-one-out ablation on reconstructed signal_score components."""
    components = ["pts_52w", "pts_sma", "pts_volume", "pts_mirofish"]
    baseline = evaluate_score_column(df, baseline_col, y_col=y_col, ret_col=ret_col)
    rows: list[dict[str, Any]] = []
    for col in components:
        if col not in df.columns:
            continue
        ablated = df[baseline_col] - df[col]
        tmp = df.copy()
        tmp["ablated_signal_score"] = ablated.clip(lower=0.0, upper=100.0)
        pack = evaluate_score_column(tmp, "ablated_signal_score", y_col=y_col, ret_col=ret_col)
        if pack is None or baseline is None:
            continue
        rows.append(
            {
                "removed_component": col,
                "baseline_auc": baseline.auc,
                "ablated_auc": pack.auc,
                "auc_delta": pack.auc - baseline.auc,
                "baseline_ic": baseline.spearman_ic,
                "ablated_ic": pack.spearman_ic,
                "ic_delta": pack.spearman_ic - baseline.spearman_ic,
            }
        )
    return rows


def rank_lift_table(
    df: pd.DataFrame,
    *,
    y_col: str,
    ret_col: str,
    rank_cols: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for col in rank_cols:
        pack = evaluate_score_column(df, col, y_col=y_col, ret_col=ret_col)
        if pack is None:
            continue
        portfolio = portfolio_curve_by_date(df, col, ret_col=ret_col, top_frac=0.1)
        rows.append(
            {
                "score_column": col,
                **pack.to_dict(),
                "portfolio_top10pct": portfolio,
            }
        )
    rows.sort(key=lambda row: float(row.get("spearman_ic") or -999.0), reverse=True)
    return rows


def sma_multiplier_sensitivity(
    df: pd.DataFrame,
    *,
    y_col: str,
    ret_col: str,
    multipliers: list[float] | None = None,
    baseline_col: str = "signal_score",
) -> list[dict[str, Any]]:
    """Report signal_score AUC/IC when pts_sma is scaled (offline tuning only)."""
    if baseline_col not in df.columns or "pts_sma" not in df.columns:
        return []
    mults = multipliers if multipliers is not None else [0.0, 0.5, 0.7, 1.0]
    rows: list[dict[str, Any]] = []
    base_pts = pd.to_numeric(df["pts_sma"], errors="coerce").fillna(0.0)
    base_signal = pd.to_numeric(df[baseline_col], errors="coerce")
    for mult in mults:
        tmp = df.copy()
        adjusted = (base_signal - base_pts + (base_pts * float(mult))).clip(lower=0.0, upper=100.0)
        tmp["signal_score_sma_adj"] = adjusted
        pack = evaluate_score_column(tmp, "signal_score_sma_adj", y_col=y_col, ret_col=ret_col)
        if pack is None:
            continue
        rows.append(
            {
                "sma_multiplier": float(mult),
                **pack.to_dict(),
            }
        )
    return rows


_DEBATE_GRADE_COLUMNS = frozenset(
    {
        "qual_raw",
        "qual_raw_equal",
        "qual_raw_with_business",
        "qual_score",
        "qual_letter",
        "qual_trend",
        "qual_setup",
        "qual_asymmetry",
        "qual_asymmetry_distance",
        "qual_raw_with_asymmetry_distance",
        "qual_crowding",
        "qual_business",
        "debate_score",
        "qual_disagreement",
        "disagreement_source",
        "qual_abstain",
        "debate_grade_would_skip",
    }
)


def score_columns_for_source(source: str, df: pd.DataFrame | None = None) -> list[str]:
    base_components = ["pts_52w", "pts_sma", "pts_volume", "pts_mirofish"]
    live_stack = [
        "signal_score",
        *base_components,
        "edge_score",
        "reliability_score",
        "execution_score",
        "composite_score",
        "rank_score",
        "rank_score_v2",
        "p_up_calibrated",
    ]
    proxy_stack = [
        "signal_score",
        *base_components,
        "edge_score_proxy",
        "reliability_score_proxy",
        "execution_score_proxy",
        "composite_score_proxy",
        "rank_score_proxy",
        "p_up_calibrated_proxy",
    ]
    if source == "trades":
        cols = [
            "signal_score",
            "edge_score",
            "reliability_score",
            "execution_score",
            "composite_score",
            "rank_score",
            "p_up_calibrated",
        ]
        if df is not None:
            return [c for c in cols if c in df.columns and c not in _DEBATE_GRADE_COLUMNS]
        return [c for c in cols if c not in _DEBATE_GRADE_COLUMNS]
    if df is not None and "rank_score" in df.columns and df["rank_score"].notna().sum() >= 30:
        return [c for c in live_stack if c in df.columns and c not in _DEBATE_GRADE_COLUMNS]
    return [c for c in proxy_stack if c not in _DEBATE_GRADE_COLUMNS]


def composite_weight_pack_from_config(skill_dir: Path | None = None):
    from core.scoring_composite import composite_quality_weights_from_config

    return composite_quality_weights_from_config(skill_dir)


def _resolve_audit_csv_for_trades(skill_dir: Path | None = None) -> Path | None:
    root = skill_dir or Path(__file__).resolve().parent.parent
    for path in (
        root / "validation_artifacts" / "scoring_audit_dataset_full.csv",
        root / "validation_artifacts" / "scoring_audit_dataset.csv",
        root / "validation_artifacts" / "advisory_dataset_latest.csv",
    ):
        if path.exists():
            return path
    return None


def load_trade_chunks_frame(run_id: str, skill_dir: Path | None = None) -> pd.DataFrame:
    """Load raw trade rows from multi-era chunk JSON (no score enrichment)."""
    import json

    sd = skill_dir or Path(__file__).resolve().parent.parent
    chunks_dir = sd / "validation_artifacts" / "multi_era_chunks" / run_id
    if not chunks_dir.exists():
        raise FileNotFoundError(f"no trade chunks for run_id={run_id}")

    score_keys = (
        "signal_score",
        "close_vs_sma200_pct",
        "entry_sma_200",
        "entry_price_ref",
        "pts_52w",
        "pts_sma",
        "pts_volume",
        "pts_mirofish",
        "qual_raw",
        "qual_raw_equal",
        "qual_raw_with_business",
        "qual_score",
        "qual_letter",
        "qual_trend",
        "qual_setup",
        "qual_asymmetry",
        "qual_asymmetry_distance",
        "qual_raw_with_asymmetry_distance",
        "qual_crowding",
        "qual_business",
        "debate_score",
        "qual_coverage_trend",
        "qual_coverage_setup",
        "qual_coverage_asymmetry",
        "qual_coverage_crowding",
        "qual_disagreement",
        "disagreement_source",
        "qual_abstain",
        "debate_grade_would_skip",
        "edge_score",
        "reliability_score",
        "execution_score",
        "composite_score",
        "rank_score",
        "rank_score_v2",
        "p_up_calibrated",
        "sec_risk_score",
    )
    rows: list[dict[str, Any]] = []
    for era in ERA_BOUNDS:
        era_dir = chunks_dir / era
        if not era_dir.exists():
            continue
        for chunk_path in sorted(era_dir.glob("chunk_*.json")):
            if chunk_path.name.endswith("_tickers.json"):
                continue
            try:
                payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for raw in payload.get("trades") or []:
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
                for key in score_keys:
                    if raw.get(key) is not None:
                        row[key] = raw.get(key)
                rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        raise FileNotFoundError(f"no trades found for run_id={run_id}")
    return df.dropna(subset=["entry_date", "net_return"]).copy()


def prepare_trade_frame_for_tuning(df: pd.DataFrame, skill_dir: Path | None = None) -> pd.DataFrame:
    """Ensure component columns exist and recompute composite for weight search."""
    sd = skill_dir or Path(__file__).resolve().parent.parent
    out = df.copy()
    out["entry_date"] = pd.to_datetime(out["entry_date"], errors="coerce")
    if "era" not in out.columns:
        out["era"] = assign_era(out["entry_date"])

    if "signal_score" not in out.columns:
        raise ValueError("trade frame missing signal_score — run augmented backtest first")

    sig = pd.to_numeric(out["signal_score"], errors="coerce").fillna(0.0)
    out["pts_52w"] = pd.to_numeric(out.get("pts_52w"), errors="coerce").fillna(sig * 0.857)
    out["pts_volume"] = pd.to_numeric(out.get("pts_volume"), errors="coerce").fillna(sig * 0.143)
    out["pts_mirofish"] = pd.to_numeric(out.get("pts_mirofish"), errors="coerce").fillna(0.0)
    if "close_vs_sma200_pct" not in out.columns:
        out["close_vs_sma200_pct"] = float("nan")
    trend = pd.to_numeric(out["close_vs_sma200_pct"], errors="coerce")
    missing_trend = trend.isna() | (trend.fillna(0.0) <= 0.0)
    if missing_trend.any() and {"entry_price_ref", "entry_sma_200"}.issubset(out.columns):
        price = pd.to_numeric(out["entry_price_ref"], errors="coerce")
        sma = pd.to_numeric(out["entry_sma_200"], errors="coerce")
        derived = (price / sma) - 1.0
        out.loc[missing_trend, "close_vs_sma200_pct"] = derived.loc[missing_trend].clip(lower=0.0)
    still_missing = pd.to_numeric(out["close_vs_sma200_pct"], errors="coerce").isna()
    if still_missing.any():
        est = (0.0008 * sig + 0.028).clip(lower=0.02, upper=0.30)
        out.loc[still_missing, "close_vs_sma200_pct"] = est.loc[still_missing]

    if "reliability_score" not in out.columns:
        out["reliability_score"] = 82.0
    if "execution_score" not in out.columns:
        out["execution_score"] = 100.0
    if "sec_risk_score" not in out.columns:
        out["sec_risk_score"] = 0.0

    out["score_stack_source"] = "trade_tuning"
    out = reapply_composite_scores(out, sd)
    from core.scoring_rank_v2 import enrich_dataframe_rank_v2

    return enrich_dataframe_rank_v2(out, skill_dir=sd)


def enrich_trade_frame_for_scoring(df: pd.DataFrame, skill_dir: Path | None = None) -> pd.DataFrame:
    """Fill score-stack fields on trade rows for offline rank validation."""
    sd = skill_dir or Path(__file__).resolve().parent.parent
    out = df.copy()
    out["entry_date"] = pd.to_datetime(out["entry_date"], errors="coerce")

    def _has(col: str, min_rows: int = 30) -> bool:
        if col not in out.columns:
            return False
        return int(out[col].notna().sum()) >= min(min_rows, max(10, len(out) // 10))

    if _has("composite_score") and _has("pts_volume"):
        out["score_stack_source"] = "chunk"
        out = reapply_composite_scores(out, sd)
        from core.scoring_rank_v2 import enrich_dataframe_rank_v2

        out = enrich_dataframe_rank_v2(out, skill_dir=sd)
        return out

    audit_path = _resolve_audit_csv_for_trades(sd)
    if audit_path is not None and "ticker" in out.columns:
        audit_cols = [
            "ticker",
            "entry_date",
            "close_vs_sma200_pct",
            "close_vs_sma50_pct",
            "pct_from_52w_high",
            "avg_vcp_volume_ratio",
            "volume_ratio",
            "breakout_confirmed",
            "sec_risk_score",
            "pts_52w",
            "pts_sma",
            "pts_volume",
            "pts_mirofish",
        ]
        audit = pd.read_csv(audit_path, usecols=lambda c: c in audit_cols or c in {"ticker", "entry_date"})
        audit["entry_date"] = pd.to_datetime(audit["entry_date"], errors="coerce").dt.normalize()
        out["entry_date_norm"] = out["entry_date"].dt.normalize()
        merge_cols = [c for c in audit_cols if c not in {"ticker", "entry_date"} and c in audit.columns]
        exact = audit[["ticker", "entry_date", *merge_cols]].rename(columns={"entry_date": "entry_date_norm"})
        out = out.merge(exact, on=["ticker", "entry_date_norm"], how="left", suffixes=("", "_audit"))

    if "signal_score" in out.columns:
        sig = pd.to_numeric(out["signal_score"], errors="coerce").fillna(0.0)
        if "pts_52w" not in out.columns or out["pts_52w"].isna().all():
            out["pts_52w"] = sig * 0.857
        else:
            out["pts_52w"] = out["pts_52w"].fillna(sig * 0.857)
        if "pts_volume" not in out.columns or out["pts_volume"].isna().all():
            out["pts_volume"] = sig * 0.143
        else:
            out["pts_volume"] = out["pts_volume"].fillna(sig * 0.143)
        out["pts_mirofish"] = out.get("pts_mirofish", pd.Series(0.0, index=out.index)).fillna(0.0)
        if "close_vs_sma200_pct" not in out.columns:
            out["close_vs_sma200_pct"] = float("nan")
        missing_trend = out["close_vs_sma200_pct"].isna()
        if missing_trend.any():
            est = (0.0008 * sig + 0.028).clip(lower=0.02, upper=0.30)
            out.loc[missing_trend, "close_vs_sma200_pct"] = est.loc[missing_trend]
    elif "signal_score" not in out.columns:
        out["score_stack_source"] = "trade_unscored"
        return out

    if {"pct_from_52w_high", "avg_vcp_volume_ratio", "signal_score"}.issubset(out.columns):
        from config import get_stage2_52w_pct

        refine_mask = out["pts_52w"].isna() | out["close_vs_sma200_pct"].isna()
        if refine_mask.any():
            proxy_rows = enrich_candidate_scores(
                out.loc[refine_mask].copy(),
                stage2_floor=float(get_stage2_52w_pct(sd)),
            )
            for col in ("pts_52w", "pts_sma", "pts_volume", "pts_mirofish", "close_vs_sma200_pct"):
                if col in proxy_rows.columns:
                    out.loc[refine_mask, col] = proxy_rows[col].values

    out["score_stack_source"] = "trade_enriched"
    out = reapply_composite_scores(out, sd)
    from core.scoring_rank_v2 import enrich_dataframe_rank_v2

    out = enrich_dataframe_rank_v2(out, skill_dir=sd)
    if "entry_date_norm" in out.columns:
        out = out.drop(columns=["entry_date_norm"])
    return out


def _refresh_backtest_reliability_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Recompute reliability for trade rows using advisory/p_up dispersion."""
    out = df.copy()
    if "reliability_score" not in out.columns and "p_up_calibrated" not in out.columns:
        out["reliability_score"] = 82.0
        return out
    from core.scoring_reliability import reliability_series_from_frame

    recompute_rows = out.copy()
    if "advisory" not in recompute_rows.columns and "p_up_calibrated" in recompute_rows.columns:
        recompute_rows["advisory"] = [
            {
                "p_up_10d": float(p) if pd.notna(p) else 0.5,
                "feature_coverage": row.get("advisory_feature_coverage"),
                "confidence_bucket": row.get("advisory_confidence_bucket"),
            }
            for p, row in zip(
                pd.to_numeric(recompute_rows["p_up_calibrated"], errors="coerce").fillna(0.5),
                recompute_rows.to_dict(orient="records"),
            )
        ]
    out["reliability_score"] = reliability_series_from_frame(recompute_rows, context="backtest").values
    return out


def reapply_composite_scores(df: pd.DataFrame, skill_dir: Path | None = None) -> pd.DataFrame:
    """Recompute edge/composite columns using current config weights (validation parity)."""
    from core.scoring_composite import composite_quality_weights_from_config, compute_composite_quality_series

    out = df.copy()
    weights = composite_quality_weights_from_config(skill_dir)
    out = _refresh_backtest_reliability_scores(out)
    edge_sig = _edge_signal_series(out, exclude_52w=weights.exclude_52w)
    p_up = _p_up_series(out)
    out["edge_score"] = (
        (weights.edge_signal_weight * edge_sig) + (weights.edge_pup_weight * (p_up * 100.0))
    ).clip(lower=0.0, upper=100.0)
    out["composite_score"] = compute_composite_quality_series(out, weights)
    return out


def _edge_signal_series(df: pd.DataFrame, *, exclude_52w: bool) -> pd.Series:
    sig = pd.to_numeric(df["signal_score"], errors="coerce").fillna(0.0)
    if exclude_52w and "pts_52w" in df.columns:
        pts = pd.to_numeric(df["pts_52w"], errors="coerce").fillna(0.0)
        sig = (sig - pts).clip(lower=0.0, upper=100.0)
    return sig.clip(lower=0.0, upper=100.0)


def _p_up_series(df: pd.DataFrame) -> pd.Series:
    for col in ("p_up_calibrated", "p_up_calibrated_proxy"):
        if col in df.columns:
            raw = pd.to_numeric(df[col], errors="coerce")
            if raw.notna().sum() >= 10:
                return raw.clip(lower=0.01, upper=0.99)
    sig = pd.to_numeric(df.get("signal_score"), errors="coerce").fillna(50.0)
    p_raw = 0.5 + ((sig - 50.0) / 150.0)
    return (0.5 + ((p_raw - 0.5) * 0.65)).clip(0.01, 0.99)


def compute_composite_series(
    df: pd.DataFrame,
    weights,
) -> pd.Series:
    """Recompute composite_score from row features and weight pack (offline tuning)."""
    from core.scoring_composite import CompositeQualityWeights, compute_composite_quality_series

    if isinstance(weights, CompositeQualityWeights):
        return compute_composite_quality_series(df, weights)
    pack = CompositeQualityWeights(
        direct_volume_weight=getattr(weights, "direct_volume_weight", 0.50),
        direct_signal_weight=getattr(weights, "direct_signal_weight", 0.35),
        direct_mirofish_weight=getattr(weights, "direct_mirofish_weight", 0.15),
        edge_signal_weight=getattr(weights, "edge_signal_weight", 0.90),
        edge_pup_weight=getattr(weights, "edge_pup_weight", 0.10),
        composite_edge_weight=getattr(weights, "composite_edge_weight", 0.0),
        composite_reliability_weight=getattr(weights, "composite_reliability_weight", 0.0),
        composite_execution_weight=getattr(weights, "composite_execution_weight", 0.0),
        exclude_52w=getattr(weights, "exclude_52w", True),
        safety_caps_only=True,
        use_direct_components=True,
    )
    return compute_composite_quality_series(df, pack)


def component_ic_table(
    df: pd.DataFrame,
    *,
    y_col: str,
    ret_col: str,
) -> list[dict[str, Any]]:
    """Spearman IC for score columns and base components."""
    candidates = [
        "pts_52w",
        "pts_sma",
        "pts_volume",
        "pts_mirofish",
        "signal_score",
        "edge_score",
        "edge_score_proxy",
        "reliability_score",
        "reliability_score_proxy",
        "execution_score",
        "execution_score_proxy",
        "composite_score",
        "composite_score_proxy",
        "rank_score",
        "rank_score_proxy",
        "rank_score_v2",
        "p_up_calibrated",
        "p_up_calibrated_proxy",
    ]
    rows: list[dict[str, Any]] = []
    for col in candidates:
        if col not in df.columns:
            continue
        pack = evaluate_score_column(df, col, y_col=y_col, ret_col=ret_col)
        if pack is None:
            continue
        rows.append({"column": col, "spearman_ic": pack.spearman_ic, "auc": pack.auc, "n": pack.n})
    rows.sort(key=lambda row: float(row.get("spearman_ic") or -999.0), reverse=True)
    return rows


def _era_ic_wins_for_column(
    df: pd.DataFrame,
    *,
    score_col: str,
    baseline_col: str,
    y_col: str,
    ret_col: str,
    min_rows: int = 40,
) -> tuple[int, int]:
    wins = 0
    total = 0
    if "era" not in df.columns:
        return 0, 0
    for _, group in df.groupby("era"):
        if len(group) < min_rows:
            continue
        score_pack = evaluate_score_column(group, score_col, y_col=y_col, ret_col=ret_col)
        base_pack = evaluate_score_column(group, baseline_col, y_col=y_col, ret_col=ret_col)
        if score_pack is None or base_pack is None:
            continue
        total += 1
        if float(score_pack.spearman_ic) >= float(base_pack.spearman_ic):
            wins += 1
    return wins, total


def _weights_to_env(weights: dict[str, Any]) -> dict[str, Any]:
    return {
        "SCORE_EDGE_EXCLUDE_52W": "true" if weights.get("exclude_52w", True) else "false",
        "SCORE_EDGE_SIGNAL_WEIGHT": weights.get("edge_signal_weight"),
        "SCORE_EDGE_PUP_WEIGHT": weights.get("edge_pup_weight"),
        "SCORE_COMPOSITE_USE_DIRECT_COMPONENTS": "true"
        if weights.get("use_direct_components", True)
        else "false",
        "SCORE_COMPOSITE_DIRECT_TREND_WEIGHT": weights.get("direct_trend_weight"),
        "SCORE_COMPOSITE_DIRECT_VOLUME_WEIGHT": weights.get("direct_volume_weight"),
        "SCORE_COMPOSITE_DIRECT_SIGNAL_WEIGHT": weights.get("direct_signal_weight"),
        "SCORE_COMPOSITE_DIRECT_MIROFISH_WEIGHT": weights.get("direct_mirofish_weight"),
        "SCORE_COMPOSITE_STACK_BLEND_WEIGHT": weights.get("stack_blend_weight", 0.0),
        "SCORE_COMPOSITE_SAFETY_CAPS_ONLY": "true"
        if weights.get("safety_caps_only", True)
        else "false",
        "SCORE_COMPOSITE_EDGE_WEIGHT": weights.get("composite_edge_weight", 0.0),
        "SCORE_COMPOSITE_RELIABILITY_WEIGHT": weights.get("composite_reliability_weight", 0.0),
        "SCORE_COMPOSITE_EXECUTION_WEIGHT": weights.get("composite_execution_weight", 0.0),
    }


def _evaluate_weight_candidate(
    df: pd.DataFrame,
    weights,
    *,
    y_col: str,
    ret_col: str,
    baseline_col: str,
    baseline_ic: float,
    min_era_wins: int,
    min_era_rows: int = 40,
    min_ic_lift: float = 0.01,
) -> dict[str, Any] | None:
    from core.scoring_composite import compute_composite_quality_series

    tmp = df.copy()
    tmp["composite_tuned"] = compute_composite_quality_series(tmp, weights)
    pack = evaluate_score_column(tmp, "composite_tuned", y_col=y_col, ret_col=ret_col)
    if pack is None:
        return None
    era_wins, era_total = _era_ic_wins_for_column(
        tmp,
        score_col="composite_tuned",
        baseline_col=baseline_col,
        y_col=y_col,
        ret_col=ret_col,
        min_rows=min_era_rows,
    )
    v2_ic = None
    if "rank_score_v2" in tmp.columns and tmp["rank_score_v2"].notna().sum() >= 30:
        v2_pack = evaluate_score_column(tmp, "rank_score_v2", y_col=y_col, ret_col=ret_col)
        if v2_pack is not None:
            v2_ic = float(v2_pack.spearman_ic)
    ic_lift = pack.spearman_ic - baseline_ic if not math.isnan(baseline_ic) else None
    return {
        "weights": weights.to_dict(),
        "spearman_ic": pack.spearman_ic,
        "decile_spread": pack.decile_spread,
        "auc": pack.auc,
        "era_wins": era_wins,
        "era_total": era_total,
        "rank_score_v2_ic": v2_ic,
        "beats_v2": v2_ic is None or float(pack.spearman_ic) >= v2_ic - 1e-6,
        "ic_lift_vs_signal": ic_lift,
        "promote_ok": (
            ic_lift is not None
            and float(ic_lift) >= float(min_ic_lift)
            and era_wins >= min(min_era_wins, era_total)
            and pack.decile_spread is not None
            and float(pack.decile_spread) > 0
        ),
    }


_CANDIDATE_WEIGHT_QUADS: list[tuple[float, float, float, float]] = [
    (0.55, 0.30, 0.10, 0.05),
    (0.50, 0.35, 0.10, 0.05),
    (0.65, 0.25, 0.05, 0.05),
    (0.70, 0.20, 0.05, 0.05),
    (0.45, 0.40, 0.10, 0.05),
    (0.60, 0.30, 0.05, 0.05),
    (0.55, 0.35, 0.05, 0.05),
    (0.50, 0.30, 0.15, 0.05),
]

_TRADE_WEIGHT_QUADS: list[tuple[float, float, float, float]] = [
    (0.00, 0.20, 0.75, 0.05),
    (0.00, 0.25, 0.70, 0.05),
    (0.00, 0.30, 0.65, 0.05),
    (0.10, 0.20, 0.65, 0.05),
    (0.20, 0.20, 0.55, 0.05),
    (0.30, 0.30, 0.35, 0.05),
    (0.50, 0.30, 0.15, 0.05),
    (0.70, 0.20, 0.05, 0.05),
    (0.55, 0.30, 0.10, 0.05),
    (0.00, 0.15, 0.80, 0.05),
    (0.00, 0.10, 0.85, 0.05),
    (0.00, 0.00, 1.00, 0.00),
]


def optimize_composite_weights_by_era(
    df: pd.DataFrame,
    *,
    y_col: str,
    ret_col: str,
    baseline_col: str = "signal_score",
    min_rows: int = 40,
) -> dict[str, Any]:
    """Per-era IC and best direct weights (regime sensitivity)."""
    from core.scoring_composite import CompositeQualityWeights, compute_composite_quality_series

    if "era" not in df.columns:
        return {}
    quick_quads = [
        (0.55, 0.30, 0.10, 0.05),
        (0.50, 0.35, 0.10, 0.05),
        (0.65, 0.25, 0.05, 0.05),
        (0.70, 0.20, 0.05, 0.05),
        (0.45, 0.40, 0.10, 0.05),
    ]
    out: dict[str, Any] = {}
    default_w = CompositeQualityWeights()
    for era, group in df.groupby("era"):
        if len(group) < min_rows:
            continue
        base_pack = evaluate_score_column(group, baseline_col, y_col=y_col, ret_col=ret_col)
        tuned = group.copy()
        tuned["composite_tuned"] = compute_composite_quality_series(tuned, default_w)
        comp_pack = evaluate_score_column(tuned, "composite_tuned", y_col=y_col, ret_col=ret_col)
        if base_pack is None or comp_pack is None:
            continue
        best_ic = float(comp_pack.spearman_ic)
        best_env = _weights_to_env(default_w.to_dict())
        for trend_w, vol_w, sig_w, miro_w in quick_quads:
            weights = CompositeQualityWeights(
                direct_trend_weight=trend_w,
                direct_volume_weight=vol_w,
                direct_signal_weight=sig_w,
                direct_mirofish_weight=miro_w,
            )
            tmp = group.copy()
            tmp["composite_tuned"] = compute_composite_quality_series(tmp, weights)
            pack = evaluate_score_column(tmp, "composite_tuned", y_col=y_col, ret_col=ret_col)
            if pack is None:
                continue
            if float(pack.spearman_ic) > best_ic + 1e-6:
                best_ic = float(pack.spearman_ic)
                best_env = _weights_to_env(weights.to_dict())
        out[str(era)] = {
            "n": int(len(group)),
            "signal_ic": base_pack.spearman_ic,
            "default_composite_ic": comp_pack.spearman_ic,
            "best_composite_ic": best_ic,
            "recommended_env": best_env,
        }
    return out


def optimize_composite_weights(
    df: pd.DataFrame,
    *,
    y_col: str,
    ret_col: str,
    baseline_col: str = "signal_score",
    min_era_wins: int = 3,
    min_era_rows: int = 40,
    min_ic_lift: float = 0.01,
    profile: str = "candidates",
) -> dict[str, Any]:
    """Grid search direct component weights (+ optional stack blend) for max IC."""
    from core.scoring_composite import CompositeQualityWeights

    direct_quads = _TRADE_WEIGHT_QUADS if profile == "trades" else _CANDIDATE_WEIGHT_QUADS
    stack_blends = (0.0,)
    edge_pairs = ((1.0, 0.0),)

    baseline_pack = evaluate_score_column(df, baseline_col, y_col=y_col, ret_col=ret_col)
    baseline_ic = float(baseline_pack.spearman_ic) if baseline_pack else float("nan")

    best: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = []

    for trend_w, vol_w, sig_w, miro_w in direct_quads:
        for stack_blend in stack_blends:
            for edge_sig_w, edge_pup_w in edge_pairs:
                weights = CompositeQualityWeights(
                    direct_trend_weight=trend_w,
                    direct_volume_weight=vol_w,
                    direct_signal_weight=sig_w,
                    direct_mirofish_weight=miro_w,
                    use_direct_components=True,
                    stack_blend_weight=stack_blend,
                    edge_signal_weight=edge_sig_w,
                    edge_pup_weight=edge_pup_w,
                    safety_caps_only=True,
                )
                row = _evaluate_weight_candidate(
                    df,
                    weights,
                    y_col=y_col,
                    ret_col=ret_col,
                    baseline_col=baseline_col,
                    baseline_ic=baseline_ic,
                    min_era_wins=min_era_wins,
                    min_era_rows=min_era_rows,
                    min_ic_lift=min_ic_lift,
                )
                if row is None:
                    continue
                candidates.append(row)
                if best is None:
                    best = row
                    continue
                best_ic = float(best.get("spearman_ic") or -999)
                cand_ic = float(row.get("spearman_ic") or -999)
                if cand_ic > best_ic + 1e-6:
                    best = row
                elif abs(cand_ic - best_ic) <= 1e-6:
                    if bool(row.get("beats_v2")) and not bool(best.get("beats_v2")):
                        best = row
                    else:
                        best_spread = float(best.get("decile_spread") or -999)
                        cand_spread = float(row.get("decile_spread") or -999)
                        if cand_spread > best_spread:
                            best = row

    recommended = best or {}
    weights_dict = recommended.get("weights") or CompositeQualityWeights().to_dict()
    return {
        "baseline_signal_ic": baseline_ic,
        "recommended": recommended,
        "promote_recommended_defaults": bool(recommended.get("promote_ok")),
        "candidates_evaluated": len(candidates),
        "top_candidates": sorted(
            candidates,
            key=lambda row: float(row.get("spearman_ic") or -999.0),
            reverse=True,
        )[:8],
        "recommended_env": _weights_to_env(weights_dict),
        "by_era": optimize_composite_weights_by_era(
            df,
            y_col=y_col,
            ret_col=ret_col,
            baseline_col=baseline_col,
        ),
    }


def incremental_spearman_ic(
    feature: pd.Series,
    control: pd.Series,
    target: pd.Series,
    *,
    min_rows: int = 50,
) -> float:
    """Spearman of feature ranks after a linear residual on control ranks.

    This is the pre-registered claim for the debate grade: what ``qual_raw``
    still ranks after ``signal_score`` is removed. Raw IC of ``qual_raw`` is
    reported separately and is not the claim.
    """
    frame = pd.DataFrame({"feature": feature, "control": control, "target": target})
    frame = frame.apply(pd.to_numeric, errors="coerce").dropna()
    if len(frame) < min_rows:
        return float("nan")
    feature_rank = frame["feature"].rank(method="average")
    control_rank = frame["control"].rank(method="average")
    if int(feature_rank.nunique()) <= 1 or int(control_rank.nunique()) <= 1:
        return float("nan")
    slope, intercept = np.polyfit(control_rank.to_numpy(dtype=float), feature_rank.to_numpy(dtype=float), 1)
    residual = feature_rank.to_numpy(dtype=float) - (intercept + slope * control_rank.to_numpy(dtype=float))
    if float(np.nanstd(residual)) < 1e-8:
        return float("nan")
    return spearman_corr(residual, frame["target"])


def _bucket_profit_factor(returns: pd.Series) -> float | None:
    vals = pd.to_numeric(returns, errors="coerce").dropna()
    if vals.empty:
        return None
    wins = float(vals[vals > 0].sum())
    losses = float(-vals[vals <= 0].sum())
    if losses <= 0:
        return None if wins <= 0 else float("inf")
    return wins / losses


def _return_bucket(returns: pd.Series, *, min_trades: int) -> dict[str, Any]:
    vals = pd.to_numeric(returns, errors="coerce").dropna()
    n = int(len(vals))
    if n < min_trades:
        return {"n": n, "insufficient": True}
    pf = _bucket_profit_factor(vals)
    return {
        "n": n,
        "insufficient": False,
        "mean_return": float(vals.mean()),
        "win_rate": float((vals > 0).mean()),
        "profit_factor": None if pf is None or not math.isfinite(pf) else float(pf),
    }


def _scored_debate_frame(df: pd.DataFrame, ret_col: str) -> pd.DataFrame:
    work = df.copy()
    if "qual_abstain" in work.columns:
        abstain = work["qual_abstain"].fillna(False).astype(bool)
        work = work.loc[~abstain].copy()
    work["qual_raw"] = pd.to_numeric(work.get("qual_raw"), errors="coerce")
    work[ret_col] = pd.to_numeric(work.get(ret_col), errors="coerce")
    return work.dropna(subset=["qual_raw", ret_col])


def _skip_vs_kept(df: pd.DataFrame, ret_col: str, *, min_trades: int) -> dict[str, Any]:
    if "debate_grade_would_skip" not in df.columns or ret_col not in df.columns:
        return {"skipped": True, "reason": "debate_grade_would_skip missing"}
    work = df.copy()
    work[ret_col] = pd.to_numeric(work[ret_col], errors="coerce")
    work = work.dropna(subset=[ret_col])
    flag = work["debate_grade_would_skip"].fillna(False).astype(bool)
    return {
        "skipped": False,
        "would_skip": _return_bucket(work.loc[flag, ret_col], min_trades=min_trades),
        "kept": _return_bucket(work.loc[~flag, ret_col], min_trades=min_trades),
    }


def _qual_raw_without_pillar(scored: pd.DataFrame, drop_name: str) -> pd.Series:
    from core.debate_grade import WEIGHTS_PRIMARY

    pillar_cols = {
        "trend": "qual_trend",
        "setup": "qual_setup",
        "asymmetry": "qual_asymmetry",
        "crowding": "qual_crowding",
    }
    numerator = pd.Series(0.0, index=scored.index)
    denominator = pd.Series(0.0, index=scored.index)
    for name, weight in WEIGHTS_PRIMARY.items():
        if name == drop_name or pillar_cols[name] not in scored.columns:
            continue
        column = pd.to_numeric(scored[pillar_cols[name]], errors="coerce")
        present = column.notna()
        contribution = (100.0 - column) if name == "crowding" else column
        numerator = numerator + contribution.fillna(0.0) * float(weight)
        denominator = denominator + present.astype(float) * float(weight)
    return numerator / denominator.replace(0.0, np.nan)


def _side_incremental(
    scored: pd.DataFrame,
    column: str,
    ret_col: str,
    *,
    min_rows: int,
    note: str,
) -> dict[str, Any]:
    if column not in scored.columns or "signal_score" not in scored.columns:
        return {"skipped": True, "reason": f"{column} missing", "note": note}
    feature = pd.to_numeric(scored[column], errors="coerce")
    if int(feature.notna().sum()) < min_rows:
        return {"skipped": True, "reason": "insufficient rows", "row_count": int(feature.notna().sum()), "note": note}
    signal = pd.to_numeric(scored["signal_score"], errors="coerce")
    return {
        "skipped": False,
        "note": note,
        "row_count": int(feature.notna().sum()),
        "incremental_ic": incremental_spearman_ic(feature, signal, scored[ret_col], min_rows=min_rows),
    }


def debate_grade_report(
    df: pd.DataFrame,
    *,
    ret_col: str,
    min_rows: int = 50,
    min_bucket: int = 30,
) -> dict[str, Any]:
    """Pre-registered debate-grade block. Letters are secondary and uncalibrated."""
    skip_vs_kept = _skip_vs_kept(df, ret_col, min_trades=min_bucket)
    if "qual_raw" not in df.columns:
        return {"skipped": True, "reason": "qual_raw missing", "skip_vs_kept": skip_vs_kept}
    total = int(len(df))
    abstain_n = 0
    if "qual_abstain" in df.columns:
        abstain_n = int(df["qual_abstain"].fillna(False).astype(bool).sum())
    scored = _scored_debate_frame(df, ret_col)
    if len(scored) < min_rows:
        return {
            "skipped": True,
            "reason": "insufficient non-abstain rows",
            "row_count": int(len(scored)),
            "abstain_n": abstain_n,
            "abstain_rate": (abstain_n / total) if total else None,
            "skip_vs_kept": skip_vs_kept,
        }

    signal = pd.to_numeric(scored.get("signal_score"), errors="coerce")
    raw_ic = spearman_corr(scored["qual_raw"], scored[ret_col])
    signal_ic = spearman_corr(signal, scored[ret_col]) if "signal_score" in scored.columns else float("nan")
    incremental = (
        incremental_spearman_ic(scored["qual_raw"], signal, scored[ret_col], min_rows=min_rows)
        if "signal_score" in scored.columns
        else float("nan")
    )
    equal_ic = float("nan")
    if "qual_raw_equal" in scored.columns:
        equal_ic = spearman_corr(pd.to_numeric(scored["qual_raw_equal"], errors="coerce"), scored[ret_col])

    try:
        quintiles = pd.qcut(scored["qual_raw"], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
    except ValueError:
        quintiles = pd.Series(index=scored.index, dtype=object)
    quintile_rows: list[dict[str, Any]] = []
    if quintiles.notna().any():
        for label, group in scored.groupby(quintiles, observed=True):
            bucket = _return_bucket(group[ret_col], min_trades=min_bucket)
            bucket["quintile"] = str(label)
            quintile_rows.append(bucket)

    era_rows: list[dict[str, Any]] = []
    if "era" in df.columns:
        for era, group in df.groupby("era"):
            era_abstain = 0
            if "qual_abstain" in group.columns:
                era_abstain = int(group["qual_abstain"].fillna(False).astype(bool).sum())
            era_scored = _scored_debate_frame(group, ret_col)
            era_signal = pd.to_numeric(era_scored.get("signal_score"), errors="coerce")
            era_rows.append(
                {
                    "era": str(era),
                    "n": int(len(group)),
                    "abstain_n": era_abstain,
                    "abstain_rate": (era_abstain / len(group)) if len(group) else None,
                    "incremental_ic": incremental_spearman_ic(
                        era_scored["qual_raw"], era_signal, era_scored[ret_col], min_rows=min_rows
                    )
                    if "signal_score" in era_scored.columns
                    else float("nan"),
                    "skipped": len(era_scored) < min_rows,
                }
            )

    disagreement: dict[str, Any] = {"skipped": True, "reason": "disagreement_source missing"}
    if "disagreement_source" in scored.columns:
        voted = scored.loc[scored["disagreement_source"].astype(str) == "votes"].copy()
        if len(voted) < min_rows:
            disagreement = {
                "skipped": True,
                "reason": "insufficient rows with committee votes",
                "row_count": int(len(voted)),
            }
        else:
            voted["qual_disagreement"] = pd.to_numeric(voted.get("qual_disagreement"), errors="coerce")
            low = voted.loc[voted["qual_disagreement"] <= 0.35]
            high = voted.loc[voted["qual_disagreement"] > 0.35]
            disagreement = {
                "skipped": False,
                "row_count": int(len(voted)),
                "threshold": 0.35,
                "low": _return_bucket(low[ret_col], min_trades=min_bucket),
                "high": _return_bucket(high[ret_col], min_trades=min_bucket),
            }

    from core.debate_grade import WEIGHTS_PRIMARY

    ablation: list[dict[str, Any]] = []
    pillar_cols = {
        "trend": "qual_trend",
        "setup": "qual_setup",
        "asymmetry": "qual_asymmetry",
        "crowding": "qual_crowding",
    }
    if all(col in scored.columns for col in pillar_cols.values()):
        base_ic = spearman_corr(scored["qual_raw"], scored[ret_col])
        for drop_name in WEIGHTS_PRIMARY:
            numerator = pd.Series(0.0, index=scored.index)
            denominator = pd.Series(0.0, index=scored.index)
            for name, weight in WEIGHTS_PRIMARY.items():
                if name == drop_name:
                    continue
                column = pd.to_numeric(scored[pillar_cols[name]], errors="coerce")
                present = column.notna()
                contribution = (100.0 - column) if name == "crowding" else column
                numerator = numerator + contribution.fillna(0.0) * float(weight)
                denominator = denominator + present.astype(float) * float(weight)
            rebuilt_s = numerator / denominator.replace(0.0, np.nan)
            ablated_ic = spearman_corr(rebuilt_s, scored[ret_col])
            ablation.append(
                {
                    "removed_pillar": drop_name,
                    "ic": ablated_ic,
                    "ic_delta": ablated_ic - base_ic
                    if math.isfinite(ablated_ic) and math.isfinite(base_ic)
                    else float("nan"),
                }
            )

    letters: list[dict[str, Any]] = []
    if "qual_letter" in scored.columns:
        for letter, group in scored.groupby(scored["qual_letter"].astype(str)):
            bucket = _return_bucket(group[ret_col], min_trades=min_bucket)
            bucket["letter"] = str(letter)
            letters.append(bucket)

    asymmetry_spread: dict[str, Any] = {"nunique": 0, "no_spread": True}
    incremental_ex_asymmetry = float("nan")
    claim_note = None
    if "qual_asymmetry" in scored.columns:
        asymmetry_values = pd.to_numeric(scored["qual_asymmetry"], errors="coerce").dropna()
        nunique = int(asymmetry_values.nunique())
        asymmetry_spread = {"nunique": nunique, "no_spread": nunique <= 1}
        if nunique <= 1 and "signal_score" in scored.columns:
            rebuilt = _qual_raw_without_pillar(scored, "asymmetry")
            incremental_ex_asymmetry = incremental_spearman_ic(
                rebuilt, signal, scored[ret_col], min_rows=min_rows
            )
            claim_note = (
                "Asymmetry has no spread on this sample, so it is excluded from the claim. "
                "Stored qual_raw is unchanged. The claim figure is incremental_ic_ex_asymmetry."
            )

    return {
        "skipped": False,
        "claim": "incremental_spearman_ic of qual_raw after signal_score",
        "claim_note": claim_note,
        "row_count": int(len(scored)),
        "abstain_n": abstain_n,
        "abstain_rate": (abstain_n / total) if total else None,
        "qual_raw_ic": raw_ic,
        "signal_score_ic": signal_ic,
        "incremental_ic": incremental,
        "incremental_ic_ex_asymmetry": incremental_ex_asymmetry,
        "asymmetry_spread": asymmetry_spread,
        "qual_raw_equal_ic": equal_ic,
        "equal_weight_note": "Printed beside the frozen primary weights. Not used to pick a winner.",
        "skip_vs_kept": skip_vs_kept,
        "qual_raw_with_business": _side_incremental(
            scored,
            "qual_raw_with_business",
            ret_col,
            min_rows=min_rows,
            note="Not the claim. Business stays out of qual_raw so live and historical samples stay comparable.",
        ),
        "debate_score": _side_incremental(
            scored,
            "debate_score",
            ret_col,
            min_rows=min_rows,
            note="Not the claim. Committee scores exist only on sampled shadow scans joined at exit.",
        ),
        "qual_raw_with_asymmetry_distance": _side_incremental(
            scored,
            "qual_raw_with_asymmetry_distance",
            ret_col,
            min_rows=min_rows,
            note="Not the claim. Continuous stop-cushion replaces the binary asymmetry pillar only in this column.",
        ),
        "quintiles": quintile_rows,
        "eras": era_rows,
        "disagreement": disagreement,
        "ablation": ablation,
        "letters_uncalibrated": letters,
    }


def _skip_vs_kept_lines(block: dict[str, Any]) -> list[str]:
    lines = ["", "### Would-skip versus kept", ""]
    if not block or block.get("skipped"):
        lines.append(f"- Skipped: {(block or {}).get('reason', 'missing')}")
        return lines
    lines.append("- Would skip: " + _bucket_inline(block.get("would_skip") or {}))
    lines.append("- Kept: " + _bucket_inline(block.get("kept") or {}))
    lines.append("- This compares realized trades. It is not an entry filter.")
    return lines


def render_debate_grade_markdown(block: dict[str, Any]) -> list[str]:
    lines = ["", "## Debate grade (shadow)", ""]
    lines.extend(_skip_vs_kept_lines(block.get("skip_vs_kept") or {}))
    if block.get("skipped"):
        lines.append(f"- Skipped: {block.get('reason')} (rows={block.get('row_count', 0)})")
        if block.get("abstain_rate") is not None:
            lines.append(f"- Abstain rate: {float(block['abstain_rate']):.1%}")
        return lines
    lines.extend(
        [
            "- Claim: incremental Spearman IC of `qual_raw` after `signal_score`.",
            "- Letters are uncalibrated display cuts. They are not the claim.",
            f"- Non-abstain rows: {block.get('row_count')}",
            f"- Abstain rate: {float(block.get('abstain_rate') or 0):.1%}",
            f"- `qual_raw` IC: {_fmt_ic(block.get('qual_raw_ic'))}",
            f"- `signal_score` IC: {_fmt_ic(block.get('signal_score_ic'))}",
            f"- Incremental IC: {_fmt_ic(block.get('incremental_ic'))}",
            f"- Equal-weight `qual_raw` IC (not selected): {_fmt_ic(block.get('qual_raw_equal_ic'))}",
            "",
            "### Quintiles of qual_raw",
            "",
            "| Quintile | n | Mean return | Win rate | Profit factor |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in block.get("quintiles") or []:
        lines.append(_bucket_markdown_row(str(row.get("quintile")), row))
    lines.extend(["", "### Era incremental IC", ""])
    for row in block.get("eras") or []:
        status = "skipped" if row.get("skipped") else _fmt_ic(row.get("incremental_ic"))
        rate = row.get("abstain_rate")
        rate_s = "n/a" if rate is None else f"{float(rate):.1%}"
        lines.append(f"- {row.get('era')}: incremental IC {status}, abstain {rate_s} (n={row.get('n')})")
    disagree = block.get("disagreement") or {}
    lines.extend(["", "### Disagreement split (votes only)", ""])
    if disagree.get("skipped"):
        lines.append(f"- Skipped: {disagree.get('reason')} (rows={disagree.get('row_count', 0)})")
    else:
        lines.append(f"- Rows with votes: {disagree.get('row_count')}. Threshold {disagree.get('threshold')}.")
        lines.append("- Low: " + _bucket_inline(disagree.get("low") or {}))
        lines.append("- High: " + _bucket_inline(disagree.get("high") or {}))
    spread = block.get("asymmetry_spread") or {}
    lines.extend(["", "### Asymmetry spread", ""])
    if spread.get("no_spread"):
        lines.append(f"- No spread (unique values={spread.get('nunique', 0)}).")
        lines.append(f"- {block.get('claim_note')}")
        lines.append(
            f"- Incremental IC with asymmetry removed: {_fmt_ic(block.get('incremental_ic_ex_asymmetry'))}"
        )
    else:
        lines.append(f"- Asymmetry takes {spread.get('nunique')} values. The claim stays on stored `qual_raw`.")
    for label, key in (
        ("Business side column", "qual_raw_with_business"),
        ("Committee debate_score", "debate_score"),
        ("Asymmetry distance side column", "qual_raw_with_asymmetry_distance"),
    ):
        side = block.get(key) or {}
        lines.extend(["", f"### {label}", ""])
        if side.get("skipped"):
            lines.append(f"- Skipped: {side.get('reason')} (rows={side.get('row_count', 0)}). {side.get('note')}")
        else:
            lines.append(f"- {side.get('note')}")
            lines.append(f"- Incremental IC: {_fmt_ic(side.get('incremental_ic'))} (n={side.get('row_count')})")
    lines.extend(["", "### Pillar ablation on qual_raw", ""])
    for row in block.get("ablation") or []:
        lines.append(
            f"- Drop {row.get('removed_pillar')}: IC {_fmt_ic(row.get('ic'))}, "
            f"delta {_fmt_ic(row.get('ic_delta'))}"
        )
    lines.extend(["", "### Letters (uncalibrated)", ""])
    for row in block.get("letters_uncalibrated") or []:
        lines.append(f"- {row.get('letter')}: {_bucket_inline(row)}")
    return lines


def _fmt_ic(value: Any) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(num):
        return "n/a"
    return f"{num:+.4f}"


def _bucket_inline(row: dict[str, Any]) -> str:
    if row.get("insufficient"):
        return f"insufficient (n={row.get('n', 0)})"
    pf = row.get("profit_factor")
    pf_s = "n/a" if pf is None else f"{float(pf):.2f}"
    mean = row.get("mean_return")
    mean_s = "n/a" if mean is None else f"{float(mean) * 100:+.2f}%"
    win = row.get("win_rate")
    win_s = "n/a" if win is None else f"{float(win):.1%}"
    return f"n={row.get('n')} mean {mean_s} win {win_s} PF {pf_s}"


def _bucket_markdown_row(label: str, row: dict[str, Any]) -> str:
    if row.get("insufficient"):
        return f"| {label} | {row.get('n', 0)} | insufficient | | |"
    pf = row.get("profit_factor")
    pf_s = "n/a" if pf is None else f"{float(pf):.2f}"
    mean = float(row.get("mean_return") or 0.0) * 100
    win = float(row.get("win_rate") or 0.0)
    return f"| {label} | {row.get('n')} | {mean:+.2f}% | {win:.1%} | {pf_s} |"
