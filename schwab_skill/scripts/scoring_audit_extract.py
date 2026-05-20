from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATASET = ROOT / "validation_artifacts" / "advisory_dataset_latest.csv"
OUT_JSON = ROOT / "validation_artifacts" / "scoring_audit_extract_20260518.json"


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


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
    sa = pd.Series(a).rank(method="average")
    sb = pd.Series(b).rank(method="average")
    return float(sa.corr(sb, method="pearson"))


@dataclass
class MetricPack:
    auc: float
    pr_auc: float
    brier: float | None
    logloss: float | None
    spearman_ic: float
    top10_precision: float
    top5_precision: float
    top10_ret10d_mean: float
    top5_ret10d_mean: float
    ndcg10: float


def evaluate(df: pd.DataFrame, score_col: str, prob_col: str | None = None) -> MetricPack:
    y = df["y_up_10d"].astype(int).to_numpy()
    s = df[score_col].astype(float).to_numpy()

    auc = roc_auc_score_manual(y, s)
    pr_auc = average_precision_manual(y, s)
    spearman_ic = spearman_corr(df[score_col], df["ret_10d_fwd"])

    n = len(df)
    k10 = max(1, int(n * 0.10))
    k05 = max(1, int(n * 0.05))
    top10 = df.nlargest(k10, score_col)
    top05 = df.nlargest(k05, score_col)
    top10_precision = float(top10["y_up_10d"].mean())
    top05_precision = float(top05["y_up_10d"].mean())
    top10_ret = float(top10["ret_10d_fwd"].mean())
    top05_ret = float(top05["ret_10d_fwd"].mean())

    ret = df["ret_10d_fwd"].astype(float).to_numpy()
    ret_norm = (ret - np.nanmin(ret)) / max(np.nanmax(ret) - np.nanmin(ret), 1e-9)
    ndcg10 = ndcg_at_k(ret_norm, s, k10)

    brier = None
    logloss = None
    if prob_col is not None:
        p = df[prob_col].astype(float).clip(1e-6, 1 - 1e-6).to_numpy()
        brier = float(np.mean((p - y) ** 2))
        logloss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

    return MetricPack(
        auc=float(auc),
        pr_auc=float(pr_auc),
        brier=brier,
        logloss=logloss,
        spearman_ic=spearman_ic,
        top10_precision=top10_precision,
        top5_precision=top05_precision,
        top10_ret10d_mean=top10_ret,
        top5_ret10d_mean=top05_ret,
        ndcg10=float(ndcg10),
    )


def portfolio_curve_by_date(df: pd.DataFrame, score_col: str, top_frac: float = 0.1) -> dict[str, float]:
    curves = []
    for date, g in df.groupby("entry_date"):
        n = len(g)
        k = max(1, int(n * top_frac))
        top = g.nlargest(k, score_col)
        curves.append((date, float(top["ret_10d_fwd"].mean())))
    if not curves:
        return {"avg_return": float("nan"), "max_drawdown": float("nan"), "profit_factor": float("nan"), "trades": 0}
    r = pd.Series([x[1] for x in sorted(curves, key=lambda x: x[0])], dtype=float)
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


def main() -> None:
    from config import get_stage2_52w_pct

    df = pd.read_csv(DATASET)
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    df = df.dropna(subset=["entry_date", "signal_score", "y_up_10d", "ret_10d_fwd"]).copy()

    # Reconstruct Stage A base components (using recorded feature columns).
    stage2_floor = float(get_stage2_52w_pct(ROOT))
    floor_span = max(0.01, 1.0 - stage2_floor)
    df["pts_52w_recon"] = ((df["pct_from_52w_high"] - stage2_floor) / floor_span).clip(lower=0) * 40.0
    df["pts_sma_recon"] = (df["close_vs_sma200_pct"] * 100.0).clip(lower=0, upper=25.0)
    df["pts_volume_recon"] = (20.0 - (df["avg_vcp_volume_ratio"] * 20.0)).clip(lower=0.0)
    df["pts_mirofish_implied"] = (
        df["signal_score"] - df["pts_52w_recon"] - df["pts_sma_recon"] - df["pts_volume_recon"]
    ).clip(lower=0.0, upper=15.0)

    # Stage B proxy reconstruction from current formula with conservative assumptions
    # (fallback/primary unknown in dataset -> assume primary and no fallback).
    p_raw = 0.5 + ((df["signal_score"] - 50.0) / 150.0)
    p_up = 0.5 + ((p_raw - 0.5) * 0.65)  # unknown confidence bucket shrink
    p_up = p_up.clip(0.01, 0.99)
    df["p_up_calibrated_proxy"] = p_up
    df["edge_score_proxy"] = (0.65 * df["signal_score"] + 0.35 * (p_up * 100.0)).clip(0, 100)

    sec_tag = np.where(df["sec_risk_score"] >= 0.67, "high", np.where(df["sec_risk_score"] >= 0.33, "medium", "unknown"))
    reliability = np.full(len(df), 82.0)
    reliability -= 18.0  # advisory unknown
    reliability -= 8.0   # advisory unavailable
    reliability -= 10.0  # conviction missing from dataset
    reliability -= np.where(sec_tag == "high", 8.0, 0.0)
    reliability = np.clip(reliability, 0.0, 100.0)
    df["reliability_score_proxy"] = reliability

    execution = np.full(len(df), 100.0)
    execution -= np.where(df["volume_ratio"] < 0.7, 20.0, np.where(df["volume_ratio"] < 0.9, 10.0, 0.0))
    execution -= np.where(sec_tag == "high", 15.0, np.where(sec_tag == "medium", 7.0, 0.0))
    execution -= np.where(df["breakout_confirmed"].astype(int) == 1, 0.0, 8.0)
    execution = np.clip(execution, 0.0, 100.0)
    df["execution_score_proxy"] = execution

    avg_win = 0.01 + (np.maximum(0.0, df["edge_score_proxy"] - 50.0) / 100.0) * 0.06
    avg_loss = 0.008 + (np.maximum(0.0, 50.0 - df["edge_score_proxy"]) / 100.0) * 0.05
    friction = 0.002 + ((100.0 - execution) / 100.0) * 0.01
    ev_10d = (p_up * avg_win) - ((1.0 - p_up) * avg_loss) - friction
    df["ev_10d_proxy"] = ev_10d

    composite = (df["edge_score_proxy"] * 0.5) + (reliability * 0.3) + (execution * 0.2)
    composite = np.where(reliability < 40.0, np.minimum(composite, 55.0), composite)
    composite = np.where(sec_tag == "high", np.minimum(composite, 45.0), composite)
    composite = np.clip(composite, 0.0, 100.0)
    df["composite_score_proxy"] = composite

    rank_base = (
        (composite * 0.55)
        + (df["edge_score_proxy"] * 0.15)
        + (reliability * 0.15)
        + (execution * 0.10)
        + ((p_up * 100.0) * 0.05)
    )
    rank_nudge = np.clip(ev_10d * 1000.0, -8.0, 8.0)
    rank = rank_base + rank_nudge
    rank = np.where(reliability < 40.0, np.minimum(rank, 55.0), rank)
    rank = np.where(execution < 45.0, np.minimum(rank, 58.0), rank)
    rank = np.where(sec_tag == "high", np.minimum(rank, 45.0), rank)
    rank = np.clip(rank, 0.0, 100.0)
    df["rank_score_proxy"] = rank

    metrics = {
        "signal_score": asdict(evaluate(df, "signal_score")),
        "composite_score_proxy": asdict(evaluate(df, "composite_score_proxy")),
        "rank_score_proxy": asdict(evaluate(df, "rank_score_proxy")),
        "p_up_calibrated_proxy": asdict(evaluate(df, "p_up_calibrated_proxy", prob_col="p_up_calibrated_proxy")),
    }

    portfolio = {
        "signal_score_top10pct": portfolio_curve_by_date(df, "signal_score", 0.10),
        "composite_proxy_top10pct": portfolio_curve_by_date(df, "composite_score_proxy", 0.10),
        "rank_proxy_top10pct": portfolio_curve_by_date(df, "rank_score_proxy", 0.10),
    }

    # Sensitivity grid around current rank formula.
    sensitivity_rows: list[dict[str, Any]] = []
    for mult in [0.7, 0.85, 1.0, 1.15, 1.3]:
        test_rank = (
            (composite * (0.55 * mult))
            + (df["edge_score_proxy"] * 0.15)
            + (reliability * 0.15)
            + (execution * 0.10)
            + ((p_up * 100.0) * 0.05)
            + rank_nudge
        )
        test_rank = np.clip(test_rank, 0.0, 100.0)
        rho = spearman_corr(pd.Series(test_rank), df["rank_score_proxy"])
        auc = roc_auc_score_manual(df["y_up_10d"].to_numpy(), test_rank.to_numpy())
        sensitivity_rows.append({"composite_weight_multiplier": mult, "spearman_vs_base_rank": rho, "auc": float(auc)})

    # Decile monotonicity for key variables.
    def decile_table(col: str) -> list[dict[str, Any]]:
        q = pd.qcut(df[col], q=10, duplicates="drop")
        out = []
        for i, (_, g) in enumerate(df.groupby(q), start=1):
            out.append(
                {
                    "bin": i,
                    "n": int(len(g)),
                    "mean_score": float(g[col].mean()),
                    "hit_rate_10d": float(g["y_up_10d"].mean()),
                    "avg_ret_10d": float(g["ret_10d_fwd"].mean()),
                    "avg_dd_10d": float(g["drawdown_10d"].mean()),
                }
            )
        return out

    deciles = {
        "signal_score": decile_table("signal_score"),
        "composite_score_proxy": decile_table("composite_score_proxy"),
        "rank_score_proxy": decile_table("rank_score_proxy"),
        "pts_52w_recon": decile_table("pts_52w_recon"),
        "pts_sma_recon": decile_table("pts_sma_recon"),
        "pts_volume_recon": decile_table("pts_volume_recon"),
    }

    # Variable stats inventory.
    inventory = {}
    for c in [
        "signal_score",
        "pts_52w_recon",
        "pts_sma_recon",
        "pts_volume_recon",
        "pts_mirofish_implied",
        "edge_score_proxy",
        "reliability_score_proxy",
        "execution_score_proxy",
        "composite_score_proxy",
        "rank_score_proxy",
        "p_up_calibrated_proxy",
        "ev_10d_proxy",
    ]:
        s = pd.to_numeric(df[c], errors="coerce")
        inventory[c] = {
            "dtype": str(df[c].dtype),
            "missing_pct": float(s.isna().mean() * 100.0),
            "min": float(s.min()),
            "p5": float(s.quantile(0.05)),
            "p50": float(s.quantile(0.50)),
            "p95": float(s.quantile(0.95)),
            "max": float(s.max()),
        }

    # Regime splits using date-level proxies.
    day = (
        df.groupby("entry_date", as_index=False)
        .agg(median_ret20=("ret_20d_prev", "median"), median_atr=("atr_pct", "median"))
        .sort_values("entry_date")
    )
    atr_cut = float(day["median_atr"].median())
    day["regime"] = np.where(day["median_ret20"] >= 0, "bull_proxy", "bear_proxy")
    day["vol_regime"] = np.where(day["median_atr"] >= atr_cut, "high_vol", "low_vol")
    df = df.merge(day[["entry_date", "regime", "vol_regime"]], on="entry_date", how="left")

    regime_metrics: dict[str, dict[str, float]] = {}
    for key in ["bull_proxy", "bear_proxy", "high_vol", "low_vol"]:
        if key in {"bull_proxy", "bear_proxy"}:
            g = df[df["regime"] == key]
        else:
            g = df[df["vol_regime"] == key]
        if len(g) < 30:
            continue
        regime_metrics[key] = {
            "n": int(len(g)),
            "signal_auc": float(roc_auc_score_manual(g["y_up_10d"].to_numpy(), g["signal_score"].to_numpy())),
            "rank_proxy_auc": float(roc_auc_score_manual(g["y_up_10d"].to_numpy(), g["rank_score_proxy"].to_numpy())),
            "signal_ic": spearman_corr(g["signal_score"], g["ret_10d_fwd"]),
            "rank_proxy_ic": spearman_corr(g["rank_score_proxy"], g["ret_10d_fwd"]),
        }

    out = {
        "dataset": {
            "path": str(DATASET),
            "rows": int(len(df)),
            "date_min": str(df["entry_date"].min().date()),
            "date_max": str(df["entry_date"].max().date()),
            "tickers": int(df["ticker"].nunique()),
        },
        "metrics": metrics,
        "portfolio": portfolio,
        "sensitivity": sensitivity_rows,
        "deciles": deciles,
        "inventory": inventory,
        "regime_metrics": regime_metrics,
    }
    OUT_JSON.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(OUT_JSON), "rows": len(df), "tickers": int(df["ticker"].nunique())}, indent=2))


if __name__ == "__main__":
    main()
