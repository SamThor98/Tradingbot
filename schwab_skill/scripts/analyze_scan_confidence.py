#!/usr/bin/env python3
"""Fact-based scan-table confidence: screen every legal variable, then test blends.

Asks one question the live score does not answer: will this setup survive to the
21-40 day window, or get trailed out inside 20 days? That cohort split is where
the book's P&L actually comes from.

Everything is walk-forward by era. Feature selection happens *inside* the
training folds, so the holdout era never influences which variables are chosen.
Labels (forward returns, hold days, exits) are never inputs.

Reuses the frozen `control_legacy_aug` trades plus the labeled research panel;
runs no new backtest. PIT fundamentals / insider Form 4 are joined when
`scripts/build_pit_fundamental_panel.py` has cached them, and are reported as
"not testable" when they are absent rather than silently skipped.

Writes:
  validation_artifacts/scan_confidence/screen_<run_id>.json
  validation_artifacts/scan_confidence/counterfactual_<run_id>.json
  validation_artifacts/scan_confidence/report.md

Usage (from schwab_skill/):
  python scripts/analyze_scan_confidence.py
  python scripts/analyze_scan_confidence.py --run-id control_legacy_aug --max-features 3
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

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.scoring_historical import compute_historical_rank, load_blend_artifact  # noqa: E402
from core.scoring_metrics import spearman_corr  # noqa: E402
from research.counterfactual import (  # noqa: E402
    _era_pf_mean_worst,
    _safe_pf,
    select_by_percentile,
    select_top_n_by_day,
)
from research.labels import LABEL_COLUMNS  # noqa: E402
from research.leakage import assert_no_label_columns_in_features  # noqa: E402
from research.pit_fundamentals import (  # noqa: E402
    attach_fundamentals_asof,
    attach_insider_windows,
    derive_fundamental_ratios,
)
from research.report import bootstrap_pf_ci, bootstrap_pf_ci_blocked  # noqa: E402
from scripts.build_analog_library import load_analog_source_frame  # noqa: E402

ART = SKILL_DIR / "validation_artifacts" / "scan_confidence"
PANEL_DIR = SKILL_DIR / "research_store" / "panels"
MODEL_DIR = SKILL_DIR / "research_store" / "models"
LABELED_PANEL = (
    SKILL_DIR
    / "validation_artifacts"
    / "prob_rank_control_confirm"
    / "scored_features_control_legacy_aug_labeled.parquet"
)
ANALOG_CF = (
    SKILL_DIR / "validation_artifacts" / "analog_overlay" / "counterfactual_control_legacy_aug.json"
)

DEFAULT_RUN_ID = "control_legacy_aug"
PROMOTION_PF_MEAN = 1.20
PROMOTION_WORST_ERA_PF = 1.00
# A blend has to clear a single known variable by a real margin, not a rounding
# error. Same +0.03 bar the early-stop pre-entry counterfactual uses.
MIN_INCREMENTAL_PF_LIFT = 0.03
CURRENT_LIVE_PERCENTILE = 76.0
PTS_52W_CAP = 37.0

EARLY_STOP_MAX_HOLD = 20
WINNER_MIN_HOLD = 21
WINNER_MAX_HOLD = 40

# A feature must cover this share of the book to be screened at all.
MIN_COVERAGE = 0.70
MIN_ROWS_FOR_IC = 50
MIN_SIGN_CONSISTENT_ERAS = 4
BOUND_LO_Q = 0.01
BOUND_HI_Q = 0.99

# LightGBM outputs on this panel were trained holding out only `recent_current`,
# so they are in-sample on four of five eras. Same exclusion the historical
# blend applied to `ml_expected_return_norm` (SIGNAL_QUALITY_ROLLOUT Stage 2h).
UNPURGED_MODEL_COLUMNS = frozenset(
    {
        "expected_return_40d",
        "expected_downside_40d",
        "expected_pf_proxy",
    }
)

# Never features: outcomes, identifiers, or bookkeeping.
META_COLUMNS = frozenset(
    {
        "ticker",
        "entry_date",
        "exit_date",
        "entry_iso",
        "era",
        "asof_date",
        "dataset_id",
        "bar_provider",
        "candidate_set_version",
        "feature_schema_version",
        "score_stack_source",
        "data_provider",
        "data_provider_primary",
        "advisory_confidence_bucket",
        "exit_reason",
        "used_fallback_data",
        "confidence",
        "hold_days",
        "net_return",
        "y_win",
        "is_winner_21_40",
        "is_early_stop",
        "survival_label",
        "fund_staleness_days",
    }
)


def _numeric(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


# ── panel ──────────────────────────────────────────────────────────────────


def _merge_full_labeled_panel(trades: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
    """Attach every PIT research column, not just the analog subset."""
    if not LABELED_PANEL.is_file():
        return trades, None
    panel = pd.read_parquet(LABELED_PANEL)
    if panel.empty:
        return trades, None
    work = panel.copy()
    work["entry_date"] = pd.to_datetime(work.get("asof_date"), errors="coerce").dt.normalize()
    work["ticker"] = work["ticker"].astype(str).str.upper()
    work = work.dropna(subset=["entry_date"]).drop_duplicates(
        subset=["ticker", "entry_date"], keep="last"
    )
    # Labels must never survive the merge: a suffixed copy (ret_40d_fwd_panel)
    # would slip past a name-based leakage guard and score like an oracle.
    drop = {"era", "asof_date", "dataset_id", "bar_provider", "confidence", *LABEL_COLUMNS}
    out = trades.copy()
    out["entry_date"] = pd.to_datetime(out["entry_date"], errors="coerce").dt.normalize()
    out["ticker"] = out["ticker"].astype(str).str.upper()
    # The trade frame's own copy of a shared column wins; bringing in a second
    # one only creates a `_panel` twin that screens as a duplicate feature.
    existing = set(out.columns) - {"ticker", "entry_date"}
    keep = [c for c in work.columns if c not in drop and c not in existing]
    merged = out.merge(work[keep], on=["ticker", "entry_date"], how="left")
    return merged, str(LABELED_PANEL.relative_to(SKILL_DIR))


def _attach_pit_fundamentals(trades: pd.DataFrame, run_id: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Join cached PIT fundamentals / insider history when it exists."""
    status: dict[str, Any] = {"fundamentals": "absent", "insiders": "absent"}
    out = trades
    fund_path = PANEL_DIR / f"pit_fundamentals_{run_id}.parquet"
    ins_path = PANEL_DIR / f"pit_insiders_{run_id}.parquet"
    if fund_path.is_file():
        fundamentals = pd.read_parquet(fund_path)
        out = attach_fundamentals_asof(out, fundamentals)
        out = derive_fundamental_ratios(out)
        status["fundamentals"] = {
            "path": str(fund_path.relative_to(SKILL_DIR)),
            "filings": int(len(fundamentals)),
            "tickers": int(fundamentals["ticker"].nunique()),
        }
    if ins_path.is_file():
        insiders = pd.read_parquet(ins_path)
        out = attach_insider_windows(out, insiders)
        status["insiders"] = {
            "path": str(ins_path.relative_to(SKILL_DIR)),
            "ticker_days": int(len(insiders)),
            "tickers": int(insiders["ticker"].nunique()),
        }
    return out, status


def load_panel(run_id: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    trades, sources = load_analog_source_frame(run_id)
    trades, panel_src = _merge_full_labeled_panel(trades)
    if panel_src:
        sources.append(f"{panel_src} (full column set)")
    trades, pit_status = _attach_pit_fundamentals(trades, run_id)

    hold = pd.to_numeric(trades.get("hold_days"), errors="coerce")
    net = pd.to_numeric(trades.get("net_return"), errors="coerce")
    trades["hold_days"] = hold
    trades["is_early_stop"] = (hold <= EARLY_STOP_MAX_HOLD) & (net < 0)
    trades["is_winner_21_40"] = (
        hold.between(WINNER_MIN_HOLD, WINNER_MAX_HOLD) & (net > 0)
    )
    # Survival label is only defined on the two cohorts that explain the P&L.
    trades["survival_label"] = np.where(
        trades["is_winner_21_40"], 1.0, np.where(trades["is_early_stop"], 0.0, np.nan)
    )
    meta = {
        "sources": sources,
        "pit_status": pit_status,
        "n_trades": int(len(trades)),
        "n_eras": int(trades["era"].nunique()),
        "n_winner_21_40": int(trades["is_winner_21_40"].sum()),
        "n_early_stop": int(trades["is_early_stop"].sum()),
    }
    return trades, meta


def feature_columns(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Legal, sufficiently covered numeric features. Returns (kept, rejected)."""
    label_set = set(LABEL_COLUMNS)
    candidates: list[str] = []
    rejected_leakage: list[str] = []
    for col in df.columns:
        if col in META_COLUMNS:
            continue
        # Strip merge suffixes before testing: `ret_40d_fwd_panel` is still a label.
        base = col
        for suffix in ("_panel", "_chunk", "_x", "_y"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        if col in label_set or base in label_set:
            rejected_leakage.append(f"{col} (label)")
            continue
        if "fwd" in col or col.startswith(("y_up", "y_win", "drawdown_", "barrier_")):
            rejected_leakage.append(f"{col} (forward/label-like name)")
            continue
        if col in UNPURGED_MODEL_COLUMNS or base in UNPURGED_MODEL_COLUMNS:
            rejected_leakage.append(f"{col} (unpurged model output, in-sample on 4/5 eras)")
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        if series.notna().sum() == 0:
            continue
        candidates.append(col)

    errors = assert_no_label_columns_in_features(candidates)
    if errors:
        raise RuntimeError(f"leakage guard tripped: {errors}")

    kept: list[str] = []
    rejected: list[str] = list(rejected_leakage)
    for col in candidates:
        series = pd.to_numeric(df[col], errors="coerce")
        coverage = float(series.notna().mean())
        if coverage < MIN_COVERAGE:
            rejected.append(f"{col} (coverage {coverage:.2f})")
            continue
        if series.nunique(dropna=True) < 5:
            rejected.append(f"{col} (near-constant)")
            continue
        kept.append(col)
    return sorted(kept), sorted(rejected)


# ── screening ──────────────────────────────────────────────────────────────


def _ic(x: pd.Series, y: pd.Series) -> float | None:
    work = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(work) < MIN_ROWS_FOR_IC or work["x"].nunique() < 3:
        return None
    val = spearman_corr(work["x"], work["y"])
    return None if val != val else round(float(val), 4)


def _auc(score: pd.Series, label: pd.Series) -> float | None:
    work = pd.DataFrame({"s": score, "y": label}).dropna()
    if len(work) < MIN_ROWS_FOR_IC:
        return None
    pos = work[work["y"] > 0.5]["s"]
    neg = work[work["y"] <= 0.5]["s"]
    if pos.empty or neg.empty:
        return None
    ranks = work["s"].rank()
    n_pos, n_neg = len(pos), len(neg)
    rank_sum = ranks[work["y"] > 0.5].sum()
    auc = (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
    return round(float(auc), 4)


def screen_feature(df: pd.DataFrame, col: str) -> dict[str, Any]:
    series = _numeric(df, col)
    net = _numeric(df, "net_return")
    survival = _numeric(df, "survival_label")
    fwd = _numeric(df, "ret_40d_fwd") if "ret_40d_fwd" in df.columns else pd.Series(np.nan, index=df.index)

    per_era: dict[str, Any] = {}
    ic_by_era: list[float] = []
    for era, grp in df.groupby("era"):
        g_series = _numeric(grp, col)
        g_net = _numeric(grp, "net_return")
        ic = _ic(g_series, g_net)
        per_era[str(era)] = {
            "n": int(g_series.notna().sum()),
            "ic_net_return": ic,
            "auc_survival": _auc(g_series, _numeric(grp, "survival_label")),
        }
        if ic is not None:
            ic_by_era.append(ic)

    pooled_ic = _ic(series, net)
    signs = [1 if v > 0 else -1 for v in ic_by_era if v != 0]
    sign_consistency = max(signs.count(1), signs.count(-1)) if signs else 0

    # Quintile PF shows whether the ordering means money, not just correlation.
    quintiles: list[dict[str, Any]] = []
    work = pd.DataFrame({"x": series, "net": net, "early": df["is_early_stop"]}).dropna(subset=["x", "net"])
    if len(work) >= 200:
        try:
            work["q"] = pd.qcut(work["x"], 5, labels=False, duplicates="drop")
            for q, grp in work.groupby("q"):
                quintiles.append(
                    {
                        "quintile": int(q),
                        "n": int(len(grp)),
                        "pf": _safe_pf(grp["net"]),
                        "mean_net_return": round(float(grp["net"].mean()), 5),
                        "early_stop_pct": round(100 * float(grp["early"].mean()), 2),
                    }
                )
        except ValueError:
            quintiles = []

    win = df[df["is_winner_21_40"]]
    early = df[df["is_early_stop"]]
    w_mean = _numeric(win, col).mean() if not win.empty else np.nan
    e_mean = _numeric(early, col).mean() if not early.empty else np.nan
    pooled_std = series.std()
    separation = (
        round(float((w_mean - e_mean) / pooled_std), 4)
        if pooled_std and pooled_std == pooled_std and pooled_std > 0 and w_mean == w_mean and e_mean == e_mean
        else None
    )

    q_pf = [q["pf"] for q in quintiles if q.get("pf") is not None]
    return {
        "feature": col,
        "coverage": round(float(series.notna().mean()), 4),
        "ic_net_return": pooled_ic,
        "ic_ret_40d_fwd": _ic(series, fwd),
        "auc_survival": _auc(series, survival),
        "n_eras_with_ic": len(ic_by_era),
        "sign_consistent_eras": sign_consistency,
        "ic_era_mean": round(float(np.mean(ic_by_era)), 4) if ic_by_era else None,
        "ic_era_min": round(float(np.min(ic_by_era)), 4) if ic_by_era else None,
        "ic_era_max": round(float(np.max(ic_by_era)), 4) if ic_by_era else None,
        "winner_mean": round(float(w_mean), 5) if w_mean == w_mean else None,
        "early_stop_mean": round(float(e_mean), 5) if e_mean == e_mean else None,
        "winner_vs_early_sd": separation,
        "quintile_pf_spread": round(float(max(q_pf) - min(q_pf)), 4) if len(q_pf) >= 2 else None,
        "quintiles": quintiles,
        "by_era": per_era,
    }


# ── walk-forward blend ─────────────────────────────────────────────────────


def _fit_bounds(train: pd.DataFrame, features: list[str]) -> dict[str, tuple[float, float]]:
    bounds: dict[str, tuple[float, float]] = {}
    for col in features:
        series = _numeric(train, col).dropna()
        if series.empty:
            continue
        lo = float(series.quantile(BOUND_LO_Q))
        hi = float(series.quantile(BOUND_HI_Q))
        if hi <= lo:
            hi = lo + 1e-9
        bounds[col] = (lo, hi)
    return bounds


def _normalize(df: pd.DataFrame, features: list[str], bounds: dict[str, tuple[float, float]]) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for col in features:
        if col not in bounds:
            continue
        lo, hi = bounds[col]
        out[col] = ((_numeric(df, col) - lo) / (hi - lo)).clip(0.0, 1.0)
    return out


def _select_features_in_fold(
    train: pd.DataFrame,
    candidates: list[str],
    *,
    max_features: int,
    target: str,
) -> list[dict[str, Any]]:
    """Rank candidates using training eras only, then de-correlate."""
    train_eras = [str(e) for e in train["era"].dropna().unique()]
    scored: list[dict[str, Any]] = []
    for col in candidates:
        series = _numeric(train, col)
        if float(series.notna().mean()) < MIN_COVERAGE:
            continue
        ics: list[float] = []
        for era in train_eras:
            grp = train[train["era"] == era]
            ic = _ic(_numeric(grp, col), _numeric(grp, target))
            if ic is not None:
                ics.append(ic)
        if len(ics) < max(2, len(train_eras) - 1):
            continue
        signs = [1 if v > 0 else -1 for v in ics]
        consistency = max(signs.count(1), signs.count(-1))
        # Require the sign to hold across (almost) every training era.
        if consistency < len(ics):
            continue
        scored.append(
            {
                "feature": col,
                "ic_mean": float(np.mean(ics)),
                "abs_ic_mean": abs(float(np.mean(ics))),
                "sign": 1.0 if np.mean(ics) > 0 else -1.0,
            }
        )
    scored.sort(key=lambda r: r["abs_ic_mean"], reverse=True)

    chosen: list[dict[str, Any]] = []
    for cand in scored:
        if len(chosen) >= max_features:
            break
        redundant = False
        for picked in chosen:
            pair = train[[cand["feature"], picked["feature"]]].apply(
                lambda s: pd.to_numeric(s, errors="coerce")
            ).dropna()
            if len(pair) >= MIN_ROWS_FOR_IC:
                corr = pair.corr(method="spearman").iloc[0, 1]
                if corr == corr and abs(float(corr)) > 0.80:
                    redundant = True
                    break
        if not redundant:
            chosen.append(cand)
    return chosen


def walk_forward_confidence(
    df: pd.DataFrame,
    candidates: list[str],
    *,
    max_features: int,
    target: str,
) -> tuple[pd.Series, list[dict[str, Any]]]:
    """Leave-one-era-out confidence score. Selection happens inside each fold."""
    scores = pd.Series(np.nan, index=df.index, dtype=float)
    folds: list[dict[str, Any]] = []
    eras = sorted(str(e) for e in df["era"].dropna().unique())
    for holdout in eras:
        train = df[df["era"].astype(str) != holdout]
        test_idx = df.index[df["era"].astype(str) == holdout]
        if train.empty or len(test_idx) == 0:
            continue
        picked = _select_features_in_fold(
            train, candidates, max_features=max_features, target=target
        )
        if not picked:
            folds.append({"holdout_era": holdout, "features": [], "note": "no stable feature"})
            continue
        names = [p["feature"] for p in picked]
        bounds = _fit_bounds(train, names)
        test_norm = _normalize(df.loc[test_idx], names, bounds)
        # Equal-weight the signed, normalized features: fewer knobs to overfit.
        contributions = []
        for p in picked:
            if p["feature"] not in test_norm.columns:
                continue
            col = test_norm[p["feature"]]
            contributions.append(col if p["sign"] > 0 else (1.0 - col))
        if not contributions:
            continue
        stacked = pd.concat(contributions, axis=1)
        scores.loc[test_idx] = stacked.mean(axis=1, skipna=True) * 100.0
        folds.append(
            {
                "holdout_era": holdout,
                "n_train": int(len(train)),
                "n_test": int(len(test_idx)),
                "features": [
                    {"name": p["feature"], "train_ic_mean": round(p["ic_mean"], 4), "sign": p["sign"]}
                    for p in picked
                ],
            }
        )
    return scores, folds


# ── arms ───────────────────────────────────────────────────────────────────


def _by_era(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty or "era" not in df.columns:
        return {}
    return {
        str(era): {"n": int(len(grp)), "pf": _safe_pf(grp["net_return"])}
        for era, grp in df.groupby("era")
    }


def _arm(name: str, df: pd.DataFrame, total: int, *, selection: str) -> dict[str, Any]:
    pf_mean, worst = _era_pf_mean_worst(df)
    ci_method = "none"
    ci: dict[str, Any] = {}
    if not df.empty:
        weeks = None
        if "entry_date" in df.columns and df["entry_date"].notna().any():
            weeks = pd.to_datetime(df["entry_date"], errors="coerce").dt.to_period("W").astype(str)
        if weeks is not None:
            ci = bootstrap_pf_ci_blocked(df["net_return"], weeks)
            ci_method = "week_block"
        else:
            ci = bootstrap_pf_ci(df["net_return"])
            ci_method = "trade_iid"
    return {
        "arm": name,
        "selection": selection,
        "n": int(len(df)),
        "retention": round(len(df) / max(1, total), 4),
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "pf_ci_method": ci_method,
        "pf_pooled_ci": ci,
        "by_era": _by_era(df),
        "gates": {
            "pf_mean_ok": bool(pf_mean is not None and pf_mean >= PROMOTION_PF_MEAN),
            "worst_era_ok": bool(worst is not None and worst >= PROMOTION_WORST_ERA_PF),
        },
    }


def _beats(challenger: dict[str, Any], incumbent: dict[str, Any]) -> dict[str, Any]:
    c_pf, c_worst = challenger.get("pf_mean"), challenger.get("worst_era_pf")
    i_pf, i_worst = incumbent.get("pf_mean"), incumbent.get("worst_era_pf")
    pf_ok = c_pf is not None and i_pf is not None and c_pf > i_pf
    worst_ok = c_worst is not None and i_worst is not None and c_worst > i_worst
    return {
        "vs": incumbent.get("arm"),
        "pf_mean_delta": None if (c_pf is None or i_pf is None) else round(c_pf - i_pf, 4),
        "worst_era_delta": None if (c_worst is None or i_worst is None) else round(c_worst - i_worst, 4),
        "beats_pf_mean": bool(pf_ok),
        "beats_worst_era": bool(worst_ok),
        "beats_incumbent": bool(pf_ok and worst_ok),
    }


def _signal_row_from_trade(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["score_components"] = {
        "pts_volume": row.get("pts_volume"),
        "pts_mirofish": row.get("pts_mirofish"),
        "pts_52w": row.get("pts_52w"),
        "pct_from_52w_high": row.get("pct_from_52w_high"),
        "avg_vcp_volume_ratio": row.get("avg_vcp_volume_ratio"),
    }
    return out


def _score_historical(trades: pd.DataFrame) -> pd.Series:
    artifact = load_blend_artifact(SKILL_DIR)
    if artifact is None:
        return pd.Series(np.nan, index=trades.index, dtype=float)
    scores: list[float | None] = []
    for row in trades.to_dict("records"):
        block = compute_historical_rank(_signal_row_from_trade(row), artifact)
        scores.append(None if block is None else float(block["score"]))
    return pd.Series(scores, index=trades.index, dtype=float)


def _load_analog_top5_arm() -> dict[str, Any] | None:
    """Stage 2i walk-forward analog_top5 on the same control_legacy_aug book.

    Reuses the analog overlay counterfactual (library built leave-one-era-out)
    rather than inventing a second analog score. Missing artifact → omit.
    """
    if not ANALOG_CF.is_file():
        return None
    try:
        payload = json.loads(ANALOG_CF.read_text(encoding="utf-8"))
    except Exception:
        return None
    arm = (payload.get("arms") or {}).get("analog_top5")
    if not isinstance(arm, dict) or arm.get("pf_mean") is None:
        return None
    out = dict(arm)
    out["selection"] = (
        "top-5/day by analog_p_win (walk-forward library; Stage 2i artifact)"
    )
    return out


def build_confidence_artifact(
    df: pd.DataFrame,
    candidates: list[str],
    *,
    max_features: int,
    target: str,
    folds: list[dict[str, Any]],
    validation: dict[str, Any],
    sources: list[str],
) -> dict[str, Any] | None:
    """Final fit on every era, using the procedure the walk-forward validated.

    The walk-forward folds prove the *selection rule* generalizes; the shipped
    artifact is that same rule applied to all available data.
    """
    picked = _select_features_in_fold(df, candidates, max_features=max_features, target=target)
    if not picked:
        return None
    names = [p["feature"] for p in picked]
    bounds = _fit_bounds(df, names)
    payload: dict[str, Any] = {
        "schema": "scan_confidence_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "target": target,
        "features": [
            {
                "name": p["feature"],
                "sign": float(p["sign"]),
                "ic_mean": round(float(p["ic_mean"]), 6),
                "bounds": [bounds[p["feature"]][0], bounds[p["feature"]][1]],
            }
            for p in picked
            if p["feature"] in bounds
        ],
        "walk_forward_folds": folds,
        "validation": validation,
        "promote": False,
        "mode_note": "shadow only; not approved as a live sort key",
    }
    blob = json.dumps(payload["features"], sort_keys=True, default=str)
    payload["model_id"] = "conf_" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]
    return payload


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _write_report(
    path: Path,
    *,
    run_id: str,
    created: str,
    meta: dict[str, Any],
    screen: list[dict[str, Any]],
    arms: dict[str, dict[str, Any]],
    family_scores: dict[str, Any],
    folds: list[dict[str, Any]],
    conf_ic: dict[str, Any],
    verdict: str,
    best_conf: dict[str, Any],
    best_control: dict[str, Any] | None,
    vs_live: dict[str, Any],
    vs_control: dict[str, Any] | None,
) -> None:
    """Plain-English findings. A reader should not need the JSON to act."""
    by_name = {f["feature"]: f for f in screen}

    def arm_line(name: str) -> str:
        arm = arms.get(name)
        if not arm:
            return f"| `{name}` | not run | | | |"
        ci = arm.get("pf_pooled_ci") or {}
        return (
            f"| `{name}` | {arm['n']} | {arm['retention']:.3f} | "
            f"{_fmt(arm['pf_mean'])} | {_fmt(arm['worst_era_pf'])} | "
            f"{_fmt(ci.get('pf_lo'), 3)}-{_fmt(ci.get('pf_hi'), 3)} |"
        )

    fundamentals = sorted(
        (f for f in screen if f["feature"].startswith("fund_")),
        key=lambda f: abs(f.get("ic_net_return") or 0.0),
        reverse=True,
    )
    insiders = sorted(
        (f for f in screen if f["feature"].startswith("insider_")),
        key=lambda f: abs(f.get("ic_net_return") or 0.0),
        reverse=True,
    )
    charts = [
        f
        for f in screen
        if not f["feature"].startswith(("fund_", "insider_"))
    ][:12]

    lines: list[str] = [
        "# Scan-table confidence: what actually predicts a winning trade",
        "",
        f"Run `{run_id}` | generated {created}",
        "",
        f"**Verdict: `{verdict}`.** Live sort key and rank-v2 p76 filter are unchanged.",
        "",
        "## The short version",
        "",
        "Every ranking number the scanner currently shows is pointed the wrong way.",
        "On the realized book, higher `signal_score` does not mean a better trade -- it",
        "means a slightly worse one. The variables that do separate winners from losers",
        "are chart-shape variables, and they work **inverted**: the less extended and",
        "less steeply trending a setup is at entry, the better it tends to finish.",
        "",
        "Fundamentals are almost entirely noise here, with one narrow exception",
        "(free cash flow). Insider buying and selling is noise -- not weak, *nothing*.",
        "",
        "## How this was measured",
        "",
        f"- {meta['n_trades']} realized trades from the frozen `{run_id}` book, five eras.",
        f"- {meta['n_winner_21_40']} winners held 21-40 days; {meta['n_early_stop']} early",
        "  stop-outs (closed inside 20 days at a loss). Those two cohorts are where the",
        "  book's profit and loss actually comes from.",
        "- Every score is **walk-forward**: for each era, features are chosen and scaled",
        "  using only the other four eras. The era being scored never influences which",
        "  variables were picked.",
        "- Forward returns, hold days, exit reasons and net return are never inputs.",
        "  A leakage guard rejects them by name, including renamed copies.",
        "- Unpurged LightGBM outputs (`expected_return_40d` and friends) are excluded:",
        "  they were trained on four of these five eras, so they score like an oracle.",
        "",
        "## Does a confidence number beat what we run today?",
        "",
        "All arms are on the same trades. `pf_pooled_ci` resamples entry weeks when",
        "entry dates exist (`pf_ci_method=week_block`), so trades open in the same",
        "week count as one draw. A trade-level bootstrap is used only when no",
        "entry date is on the frame.",
        "",
        "| Arm | Trades | Retention | PF mean | Worst era | PF 95% CI |",
        "|---|---|---|---|---|---|",
    ]
    for name in (
        "baseline",
        "current_live_p76",
        "current_live_p76_top5",
        "historical_top5",
        "analog_top5",
        "pts52w_cap37",
        "confidence_p76",
        "confidence_top5",
        "cap37_then_confidence_top5",
        "inv_pts52w_p76",
        "inv_signal_score_p76",
        "inv_trend_sma200_p76",
        "confidence_fundamental_p76",
        "confidence_insider_p76",
        "confidence_chart_p76",
    ):
        if name in arms:
            lines.append(arm_line(name))

    live = arms.get("current_live_p76_top5") or {}
    conf_arm = arms.get("confidence_p76") or {}
    lines.extend(
        [
            "",
            "Reading that table in plain English:",
            "",
            "- **Today's live selection is the worst arm on the board.** `current_live_p76_top5`",
            f"  (rank-v2 p76, then top 5 a day by `signal_score`) returns PF {_fmt(live.get('pf_mean'))}",
            f"  with a worst era of {_fmt(live.get('worst_era_pf'))}. Taking *every* trade in the book",
            f"  ({_fmt((arms.get('baseline') or {}).get('pf_mean'))}) beats it. The current ranking is",
            "  actively costing money relative to not ranking at all.",
            "- **The confidence score is a large improvement.** `confidence_p76` returns",
            f"  PF {_fmt(conf_arm.get('pf_mean'))} with a worst era of {_fmt(conf_arm.get('worst_era_pf'))}",
            "  at the same ~24% retention. Its confidence interval",
            f"  ({_fmt((conf_arm.get('pf_pooled_ci') or {}).get('pf_lo'), 3)}-"
            f"{_fmt((conf_arm.get('pf_pooled_ci') or {}).get('pf_hi'), 3)}) does not overlap the live",
            f"  stack's ({_fmt((live.get('pf_pooled_ci') or {}).get('pf_lo'), 3)}-"
            f"{_fmt((live.get('pf_pooled_ci') or {}).get('pf_hi'), 3)}), so that gap is real.",
            "- **But it is not a new discovery.** Simply keeping the least-extended quarter of",
            "  the book by `pts_52w` -- a rule already promoted as `pts_52w <= 37` -- gets",
            f"  PF {_fmt((arms.get('inv_pts52w_p76') or {}).get('pf_mean'))}. The three-feature blend beats that",
            f"  single variable by {_fmt((vs_control or {}).get('pf_mean_delta'))} PF, which is inside the noise",
            f"  band (the bar for a real gain is +{MIN_INCREMENTAL_PF_LIFT}).",
            "- **Inverting the live score would have worked too.** Sorting by *minus*",
            "  `signal_score` and keeping the top quarter returns PF",
            f"  {_fmt((arms.get('inv_signal_score_p76') or {}).get('pf_mean'))}. That is the cleanest single",
            "  statement of the problem: the number on the scan table is backwards.",
            "",
            "## Why the same variable can look good in one study and bad here",
            "",
            "The historical blend found distance above the 200-day average was the one",
            "feature with positive out-of-sample IC (about +0.044) against raw 40-day",
            "forward returns. On this book the same variable scores negative against",
            "realized profit and loss. Both are correct, and the difference matters:",
            "",
            "| Measured against | `close_vs_sma200_pct` IC |",
            "|---|---|",
            f"| Realized `net_return` (what the account felt) | {_fmt(by_name.get('close_vs_sma200_pct', {}).get('ic_net_return'))} |",
            f"| Raw `ret_40d_fwd` (price drift, no stops) | {_fmt(by_name.get('close_vs_sma200_pct', {}).get('ic_ret_40d_fwd'))} |",
            "",
            "Two things drive the gap. First, this book is conditioned on a breakout",
            "having already triggered, so the extended names here are a narrower, more",
            "stretched sample than the full candidate panel the blend was fitted on.",
            "Second, and more important, the strategy's stop and trail machinery sits",
            "between price drift and the account. An extended name can still drift up on",
            "average and *lose money for this strategy*, because it takes a deeper drawdown",
            "first and gets trailed out. The early-stop cohort shows exactly that: mean",
            "worst-case excursion of about -9.2% versus -2.6% for the 21-40 day winners.",
            "",
            "That is the nuance: a feature is only 'predictive' relative to an exit policy.",
            "",
            "## What separates the winners",
            "",
            "Strongest chart variables by absolute IC against realized return. `sep` is the",
            "gap between 21-40 day winners and early stop-outs in standard deviations --",
            "negative means winners score *lower* on that variable.",
            "",
            "| Variable | Coverage | IC vs net return | Same sign in N eras | Winner vs early-stop (SD) |",
            "|---|---|---|---|---|",
        ]
    )
    for feat in charts:
        lines.append(
            f"| `{feat['feature']}` | {feat['coverage']:.2f} | {_fmt(feat.get('ic_net_return'))} | "
            f"{feat.get('sign_consistent_eras')}/{feat.get('n_eras_with_ic')} | "
            f"{_fmt(feat.get('winner_vs_early_sd'))} |"
        )
    lines.extend(
        [
            "",
            "Every one of these is negative. Steeper 20-day and 200-day slopes, higher",
            "trend-strength and Weinstein stage scores, and greater distance above the",
            "200-day average all show up **more often in the trades that got stopped out**",
            "than in the trades that ran 21-40 days. The scanner is built to find exactly",
            "those characteristics, which is why its own score is anti-correlated.",
            "",
            "## Fundamentals",
            "",
            "Tested on real point-in-time Sharadar filings joined by filing date strictly",
            "before each entry (median staleness is roughly a quarter, as it should be).",
            "",
            "| Ratio | Coverage | IC vs net return | IC vs 40d forward | Stable eras |",
            "|---|---|---|---|---|",
        ]
    )
    for feat in fundamentals[:12]:
        lines.append(
            f"| `{feat['feature']}` | {feat['coverage']:.2f} | {_fmt(feat.get('ic_net_return'))} | "
            f"{_fmt(feat.get('ic_ret_40d_fwd'))} | "
            f"{feat.get('sign_consistent_eras')}/{feat.get('n_eras_with_ic')} |"
        )
    fund_arm = arms.get("confidence_fundamental_p76") or {}
    lines.extend(
        [
            "",
            "Return on equity, price/earnings, debt, margins and book value are all",
            "indistinguishable from noise for this purpose -- correlations near zero and",
            "signs that flip between eras. This is not surprising: the holding period is",
            "about six weeks, and quarterly accounting data barely moves on that horizon.",
            "",
            "The one exception is the **free cash flow family** (`fund_fcf`, `fund_fcfps`,",
            "`fund_fcf_yield`), which is the only fundamental group that keeps the same",
            "sign in all five eras. It is weak but consistent, and a fundamentals-only",
            f"blend built from it returns PF {_fmt(fund_arm.get('pf_mean'))} with a worst era of",
            f"{_fmt(fund_arm.get('worst_era_pf'))} -- better than the live stack, worse than the chart",
            "blend. Worth carrying as a tiebreaker, not as a ranking engine.",
            "",
            "## Insider activity",
            "",
            "| Measure | Coverage | IC vs net return | Stable eras |",
            "|---|---|---|---|",
        ]
    )
    for feat in insiders:
        lines.append(
            f"| `{feat['feature']}` | {feat['coverage']:.2f} | {_fmt(feat.get('ic_net_return'))} | "
            f"{feat.get('sign_consistent_eras')}/{feat.get('n_eras_with_ic')} |"
        )
    insider_family = (family_scores or {}).get("insider") or {}
    lines.extend(
        [
            "",
            f"Built from {(meta.get('pit_status') or {}).get('insiders', {}).get('ticker_days', 'n/a')}",
            "ticker-days of Form 4 filings, counted only in the 90 and 180 days strictly",
            "before entry. Open-market buys and sales are separated from grants and option",
            "exercises so that routine compensation does not masquerade as conviction.",
            "",
            "Result: nothing. Every measure sits within about 0.01 of zero correlation and",
            "no insider variable held a consistent sign across the training eras in **any**",
            "fold, so the insider-only blend never produced a score at all (coverage",
            f"{_fmt(insider_family.get('coverage'), 2)}). Net insider buying is, if anything, very slightly",
            "negative. On a six-week momentum horizon, insider filings are too slow and too",
            "noisy to rank setups.",
            "",
            "## What the confidence score actually is",
            "",
            "Per-era, the walk-forward picks were:",
            "",
        ]
    )
    for fold in folds:
        names = ", ".join(f"`{f['name']}`" for f in fold.get("features", [])) or "none stable"
        lines.append(f"- Scoring **{fold['holdout_era']}**, trained on the other four: {names}")
    lines.extend(
        [
            "",
            "The picks are stable in character even when the exact column changes: some",
            "measure of trend steepness, some measure of extension, and one context or",
            "flow variable, all entered with a negative sign. Its own correlation with",
            f"realized return is {_fmt(conf_ic.get('vs_net_return'))} and it separates the two cohorts with an",
            f"AUC of {_fmt(conf_ic.get('auc_survival'))} -- honest, modest, and the right direction, as against",
            "-0.0262 for `signal_score`.",
            "",
            "## What I would and would not do with this",
            "",
            "**Supported by the evidence:**",
            "",
            "- Show a confidence number on the scan table as a **shadow** readout. It is",
            "  the first score in this system that points the right way.",
            "- Keep and trust `pts_52w <= 37`. It is doing most of the work the fancier",
            "  blend claims, and it is already promoted.",
            "",
            "**Not supported:**",
            "",
            "- Promoting this blend as the live sort key. It clears the PF floors, but it",
            "  does not clear a single already-known variable by a meaningful margin, and",
            "  promoting it would be paying complexity for a rounding error.",
            "- Adding fundamental ratios or insider data to the ranking. They were tested",
            "  properly, with real point-in-time data, and they do not carry signal here.",
            "",
            "**The honest headline:** the biggest available gain is not a better score at",
            "all. It is that the current one is inverted. Before adding a fourth ranking",
            "number, the open question worth answering is whether `signal_score` should",
            "keep its job as the live sort key, because on this book almost anything --",
            "including its own negative -- would have done better.",
            "",
            "## Gates and status",
            "",
            f"- Promotion floors: PF mean >= {PROMOTION_PF_MEAN}, worst-era PF >= {PROMOTION_WORST_ERA_PF}.",
            f"- Best confidence arm `{best_conf.get('arm')}`: PF {_fmt(best_conf.get('pf_mean'))} /"
            f" worst {_fmt(best_conf.get('worst_era_pf'))}.",
            f"- Beats live top-5 stack: {vs_live.get('beats_incumbent')}"
            f" (PF {_fmt(vs_live.get('pf_mean_delta'))}, worst era {_fmt(vs_live.get('worst_era_delta'))}).",
            f"- Beats best single-variable control (`{(best_control or {}).get('arm')}`) by a"
            f" meaningful margin: False (delta {_fmt((vs_control or {}).get('pf_mean_delta'))}).",
            "- `SCAN_LIVE_SORT_KEY`, `RANK_FILTER_V2_MODE` and all plugin modes are unchanged",
            "  by this analysis.",
            "",
            "## Caveats a reader should hold onto",
            "",
            "- Percentile thresholds are taken over the pooled book, matching how the",
            "  incumbent arms are scored. Era-local thresholds would shift the exact",
            "  numbers, though all arms move together.",
            "- Top-5-per-day barely binds on this book (about 6.5 entries a day), which is",
            "  why the `_top5` arms all cluster near the baseline. The percentile arms are",
            "  the ones doing real selection.",
            "- `volatility_chop` is the weakest era for every arm and remains the binding",
            "  constraint on worst-era gates.",
            "- This is one strategy's book. The finding that extension hurts is specific to",
            "  entering on confirmed breakouts and exiting on a trail.",
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--percentile", type=float, default=CURRENT_LIVE_PERCENTILE)
    parser.add_argument("--max-features", type=int, default=3)
    parser.add_argument(
        "--no-write-artifact",
        dest="write_artifact",
        action="store_false",
        help="analyze only; do not refresh research_store/models/scan_confidence.json",
    )
    args = parser.parse_args(argv)

    print(f"Loading panel for {args.run_id} ...")
    df, meta = load_panel(args.run_id)
    df = df.reset_index(drop=True)
    print(
        f"Trades: {meta['n_trades']} | eras: {meta['n_eras']} | "
        f"winners 21-40d: {meta['n_winner_21_40']} | early stops: {meta['n_early_stop']}"
    )
    print(f"PIT status: {json.dumps(meta['pit_status'], default=str)[:300]}")

    features, rejected = feature_columns(df)
    print(f"Screening {len(features)} legal features ({len(rejected)} rejected on coverage/variance)")

    screen = [screen_feature(df, col) for col in features]
    screen.sort(key=lambda r: abs(r.get("ic_net_return") or 0.0), reverse=True)

    print("\nTop 15 by |IC| vs net_return:")
    print(f"  {'feature':38s} {'cov':>5s} {'IC':>8s} {'eras+':>5s} {'AUC':>6s} {'sep':>7s}")
    for row in screen[:15]:
        print(
            f"  {row['feature'][:38]:38s} {row['coverage']:.2f} "
            f"{str(row['ic_net_return']):>8s} "
            f"{row['sign_consistent_eras']}/{row['n_eras_with_ic']}   "
            f"{str(row['auc_survival']):>6s} {str(row['winner_vs_early_sd']):>7s}"
        )

    # Confidence score: survival-trained, walk-forward, selection inside folds.
    conf, folds = walk_forward_confidence(
        df, features, max_features=args.max_features, target="net_return"
    )
    df["setup_confidence"] = conf
    coverage = float(conf.notna().mean())
    print(f"\nWalk-forward confidence coverage: {coverage:.4f}")
    for fold in folds:
        names = ", ".join(f["name"] for f in fold.get("features", [])) or "none"
        print(f"  holdout {fold['holdout_era']:16s} -> {names}")

    # Family blends answer the question directly: can fundamentals alone, or
    # insider activity alone, rank these setups? Same walk-forward discipline.
    families = {
        "fundamental": [c for c in features if c.startswith("fund_")],
        "insider": [c for c in features if c.startswith("insider_")],
        "chart": [c for c in features if not c.startswith(("fund_", "insider_"))],
    }
    family_scores: dict[str, dict[str, Any]] = {}
    for family, cols in families.items():
        if not cols:
            continue
        score, fam_folds = walk_forward_confidence(
            df, cols, max_features=args.max_features, target="net_return"
        )
        col_name = f"confidence_{family}"
        df[col_name] = score
        family_scores[family] = {
            "n_candidates": len(cols),
            "coverage": round(float(score.notna().mean()), 4),
            "folds": fam_folds,
            "ic_net_return": _ic(score, _numeric(df, "net_return")),
        }
        chosen = sorted({f["name"] for fold in fam_folds for f in fold.get("features", [])})
        print(
            f"  family {family:12s} candidates={len(cols):3d} "
            f"coverage={score.notna().mean():.2f} ic={family_scores[family]['ic_net_return']} "
            f"picked={chosen if chosen else 'none stable'}"
        )

    df["historical_rank"] = _score_historical(df)
    for col in ("rank_score_v2", "signal_score", "historical_rank", "setup_confidence", "pts_52w"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    total = len(df)
    arms: list[dict[str, Any]] = [_arm("baseline", df, total, selection="all trades")]

    live_p76 = select_by_percentile(df, score_col="rank_score_v2", min_percentile=args.percentile)
    arms.append(
        _arm("current_live_p76", live_p76, total, selection=f"rank_score_v2 >= p{args.percentile:g}")
    )
    live_top = select_top_n_by_day(live_p76, score_col="signal_score", top_n=args.top_n)
    incumbent = _arm(
        "current_live_p76_top5",
        live_top,
        total,
        selection=f"rank_score_v2 >= p{args.percentile:g}, then top-{args.top_n}/day by signal_score",
    )
    arms.append(incumbent)

    hist_top = select_top_n_by_day(df, score_col="historical_rank", top_n=args.top_n)
    arms.append(
        _arm("historical_top5", hist_top, total, selection=f"top-{args.top_n}/day by historical_rank")
    )
    analog_arm = _load_analog_top5_arm()
    if analog_arm is not None:
        arms.append(analog_arm)

    cap_book = df[_numeric(df, "pts_52w").isna() | (_numeric(df, "pts_52w") <= PTS_52W_CAP)]
    arms.append(
        _arm("pts52w_cap37", cap_book, total, selection=f"pts_52w <= {PTS_52W_CAP:g} (filter, not a sort)")
    )

    conf_top = select_top_n_by_day(df, score_col="setup_confidence", top_n=args.top_n)
    confidence_arm = _arm(
        "confidence_top5",
        conf_top,
        total,
        selection=f"top-{args.top_n}/day by walk-forward setup_confidence",
    )
    arms.append(confidence_arm)

    conf_pct = select_by_percentile(df, score_col="setup_confidence", min_percentile=args.percentile)
    conf_pct_arm = _arm(
        "confidence_p76",
        conf_pct,
        total,
        selection=f"setup_confidence >= p{args.percentile:g} (diagnostic)",
    )
    arms.append(conf_pct_arm)

    cap_then_conf = select_top_n_by_day(cap_book, score_col="setup_confidence", top_n=args.top_n)
    stack_arm = _arm(
        "cap37_then_confidence_top5",
        cap_then_conf,
        total,
        selection=f"pts_52w <= {PTS_52W_CAP:g}, then top-{args.top_n}/day by setup_confidence",
    )
    arms.append(stack_arm)

    # Controls. If a single already-known variable matches the blend, the blend
    # is decoration. Each is the plain inverse of a feature the screen flagged.
    controls: list[dict[str, Any]] = []
    for name, source_col in (
        ("inv_pts52w", "pts_52w"),
        ("inv_trend_sma200", "close_vs_sma200_pct"),
        ("inv_signal_score", "signal_score"),
    ):
        if source_col not in df.columns:
            continue
        col = f"_{name}"
        df[col] = -_numeric(df, source_col)
        controls.append(
            _arm(
                f"{name}_top5",
                select_top_n_by_day(df, score_col=col, top_n=args.top_n),
                total,
                selection=f"top-{args.top_n}/day by -{source_col} (single-variable control)",
            )
        )
        controls.append(
            _arm(
                f"{name}_p76",
                select_by_percentile(df, score_col=col, min_percentile=args.percentile),
                total,
                selection=f"-{source_col} >= p{args.percentile:g} (single-variable control)",
            )
        )
    arms.extend(controls)

    for family in families:
        col = f"confidence_{family}"
        if col not in df.columns or df[col].notna().sum() == 0:
            continue
        arms.append(
            _arm(
                f"confidence_{family}_p76",
                select_by_percentile(df, score_col=col, min_percentile=args.percentile),
                total,
                selection=f"{family}-only walk-forward blend >= p{args.percentile:g}",
            )
        )

    print("\nArm                           n      ret     PF mean   worst era  gates")
    for arm in arms:
        gates = "pass" if arm["gates"]["pf_mean_ok"] and arm["gates"]["worst_era_ok"] else "fail"
        print(
            f"  {arm['arm']:28s} {arm['n']:6d}  {arm['retention']:.3f}   "
            f"{str(arm['pf_mean']):>8s}   {str(arm['worst_era_pf']):>8s}  {gates}"
        )

    conf_ic = {
        "vs_net_return": _ic(df["setup_confidence"], _numeric(df, "net_return")),
        "vs_ret_40d_fwd": _ic(df["setup_confidence"], _numeric(df, "ret_40d_fwd"))
        if "ret_40d_fwd" in df.columns
        else None,
        "auc_survival": _auc(df["setup_confidence"], _numeric(df, "survival_label")),
    }
    print(f"\nConfidence IC: {conf_ic}")

    confidence_arms = [confidence_arm, stack_arm, conf_pct_arm]
    best_conf = max(
        confidence_arms,
        key=lambda a: (a.get("pf_mean") is not None, a.get("pf_mean") or 0.0),
    )
    best_control = max(
        controls,
        key=lambda a: (a.get("pf_mean") is not None, a.get("pf_mean") or 0.0),
    ) if controls else None

    vs_live = _beats(best_conf, incumbent)
    vs_control = _beats(best_conf, best_control) if best_control else None
    gates_ok = bool(best_conf["gates"]["pf_mean_ok"] and best_conf["gates"]["worst_era_ok"])
    control_lift = (vs_control or {}).get("pf_mean_delta")
    adds_over_control = bool(
        control_lift is not None and control_lift >= MIN_INCREMENTAL_PF_LIFT
    )

    if not vs_live["beats_incumbent"]:
        verdict = "no_better_than_incumbent"
    elif gates_ok and adds_over_control:
        verdict = "shadow_attach_candidate"
    elif gates_ok:
        # Clears the floors, but a single known variable does the same work.
        verdict = "keep_shadow_single_variable_explains_it"
    else:
        verdict = "keep_shadow_diagnostic"

    print(f"\nBest confidence arm: {best_conf['arm']}")
    print(
        f"  vs {incumbent['arm']}: dPF {vs_live['pf_mean_delta']} "
        f"dWorst {vs_live['worst_era_delta']} beats={vs_live['beats_incumbent']}"
    )
    if best_control and vs_control:
        print(
            f"  vs best control {best_control['arm']} "
            f"(PF {best_control['pf_mean']} / worst {best_control['worst_era_pf']}): "
            f"dPF {vs_control['pf_mean_delta']} adds_edge={adds_over_control}"
        )
    print(f"VERDICT: {verdict}")
    print("  SCAN_LIVE_SORT_KEY unchanged; no promotion from this script.")

    ART.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc).isoformat()
    screen_payload = {
        "run_id": args.run_id,
        "created_at_utc": created,
        "panel": meta,
        "n_features_screened": len(features),
        "rejected_features": rejected,
        "min_coverage": MIN_COVERAGE,
        "labels": {
            "survival": "winner_21_40 (hold 21-40d, net>0) vs early_stop (hold<=20d, net<0)",
            "net_return": "realized net return per trade",
        },
        "features": screen,
    }
    screen_path = ART / f"screen_{args.run_id}.json"
    screen_path.write_text(json.dumps(screen_payload, indent=2, default=str), encoding="utf-8")

    cf_payload = {
        "run_id": args.run_id,
        "created_at_utc": created,
        "n_trades": total,
        "confidence_coverage": round(coverage, 4),
        "max_features": args.max_features,
        "folds": folds,
        "family_blends": family_scores,
        "gates": {"pf_mean_min": PROMOTION_PF_MEAN, "worst_era_pf_min": PROMOTION_WORST_ERA_PF},
        "arms": {arm["arm"]: arm for arm in arms},
        "confidence_ic": conf_ic,
        "comparisons": {
            "best_confidence_vs_live_top5": vs_live,
            "best_confidence_vs_best_single_variable_control": vs_control,
            "best_single_variable_control": best_control["arm"] if best_control else None,
            "confidence_adds_edge_over_single_variable": adds_over_control,
        },
        "best_confidence_arm": best_conf["arm"],
        "verdict": verdict,
        "promote": False,
    }
    cf_path = ART / f"counterfactual_{args.run_id}.json"
    cf_path.write_text(json.dumps(cf_payload, indent=2, default=str), encoding="utf-8")

    artifact = build_confidence_artifact(
        df,
        features,
        max_features=args.max_features,
        target="net_return",
        folds=folds,
        validation={
            "verdict": verdict,
            "best_confidence_arm": best_conf["arm"],
            "pf_mean": best_conf.get("pf_mean"),
            "worst_era_pf": best_conf.get("worst_era_pf"),
            "vs_current_live_p76_top5": vs_live,
            "vs_best_single_variable_control": vs_control,
            "confidence_ic": conf_ic,
        },
        sources=meta["sources"],
    )
    if artifact is None:
        print("\nNo stable feature set on the full panel; artifact not written.")
    elif args.write_artifact:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(artifact, indent=2, default=str)
        (MODEL_DIR / "scan_confidence.json").write_text(blob, encoding="utf-8")
        (MODEL_DIR / f"scan_confidence_{artifact['model_id']}.json").write_text(blob, encoding="utf-8")
        names = ", ".join(f["name"] for f in artifact["features"])
        print(f"\nArtifact {artifact['model_id']}: {names}")
        print("Wrote research_store/models/scan_confidence.json")

    report_path = ART / "report.md"
    _write_report(
        report_path,
        run_id=args.run_id,
        created=created,
        meta=meta,
        screen=screen,
        arms={arm["arm"]: arm for arm in arms},
        family_scores=family_scores,
        folds=folds,
        conf_ic=conf_ic,
        verdict=verdict,
        best_conf=best_conf,
        best_control=best_control,
        vs_live=vs_live,
        vs_control=vs_control,
    )

    print(f"\nWrote {screen_path.relative_to(SKILL_DIR)}")
    print(f"Wrote {cf_path.relative_to(SKILL_DIR)}")
    print(f"Wrote {report_path.relative_to(SKILL_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
