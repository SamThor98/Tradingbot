"""End-to-end probabilistic-ranking ops orchestration (materialize → promote)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.counterfactual import run_prob_rank_counterfactual
from research.dataset import build_rank_dataset, resolve_feature_columns
from research.infer import predict_frame
from research.paths import ensure_research_store_layout, research_store_dir
from research.portfolio import run_portfolio_research
from research.promotion import evaluate_prob_rank_promotion, metrics_from_portfolio_result
from research.report import write_experiment_report
from research.train import DEFAULT_TARGET, train_prob_rank_model

LOG = logging.getLogger(__name__)

# Dual-run: require both arms present and prob-rank not clearly worse on floors
MIN_DUAL_RUN_TRADES = 50


@dataclass
class OpsPipelineResult:
    ok: bool
    mode: str
    steps: list[str] = field(default_factory=list)
    dataset_path: str | None = None
    model_dir: str | None = None
    model_id: str | None = None
    report_dir: str | None = None
    counterfactual_path: str | None = None
    portfolio_path: str | None = None
    promotion_path: str | None = None
    dual_run: dict[str, Any] = field(default_factory=dict)
    promotion: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_synthetic_universe(
    *,
    n_tickers: int = 5,
    start: str = "2015-01-02",
    end: str = "2024-12-31",
    seed: int = 7,
) -> dict[str, pd.DataFrame]:
    """Generate Stage-2-friendly uptrend OHLCV for smoke ops runs."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, end)
    n = len(idx)
    out: dict[str, pd.DataFrame] = {}
    for t in range(n_tickers):
        ticker = f"SYN{t}"
        drift = 0.08 + 0.02 * t
        noise = rng.normal(0, 0.008, n).cumsum()
        close = 25.0 + t * 5.0 + np.linspace(0, drift * 100, n) + noise * 3.0
        close = np.maximum(close, 5.0)
        # Occasional pullbacks that still trend up over SMA stack
        high = close * (1.0 + rng.uniform(0.005, 0.02, n))
        low = close * (1.0 - rng.uniform(0.005, 0.02, n))
        open_ = close * (1.0 + rng.normal(0, 0.002, n))
        volume = rng.integers(800_000, 2_000_000, n).astype(float)
        # Volume dry-up pockets for VCP-ish structure
        for k in range(0, n, 90):
            volume[k : k + 15] *= 0.55
        out[ticker] = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=idx,
        )
    return out


def trades_from_labeled_dataset(
    ds: pd.DataFrame,
    *,
    sample_frac: float = 0.35,
    seed: int = 0,
) -> pd.DataFrame:
    """
    Build a frozen-trade frame from labeled candidates for smoke dual-run.

    Uses ``net_return`` when present, else ``ret_40d_fwd``; synthesizes a
    ``rank_score_v2`` correlated weakly with the label for a control arm.
    """
    if ds is None or ds.empty:
        return pd.DataFrame()
    work = ds.copy()
    if "net_return" not in work.columns:
        work["net_return"] = pd.to_numeric(work.get("ret_40d_fwd"), errors="coerce")
    work = work.dropna(subset=["net_return", "asof_date", "ticker"])
    if work.empty:
        return pd.DataFrame()
    rng = np.random.default_rng(seed)
    mask = rng.random(len(work)) < float(sample_frac)
    sample = work.loc[mask].copy()
    if sample.empty:
        sample = work.sample(n=min(200, len(work)), random_state=seed)
    ret = pd.to_numeric(sample["net_return"], errors="coerce").fillna(0.0)
    if "rank_score_v2" not in sample.columns or sample["rank_score_v2"].isna().all():
        # Weak label-correlated control score in [0, 100]
        sample["rank_score_v2"] = (50.0 + 400.0 * ret + rng.normal(0, 8, len(sample))).clip(0, 100)
    if "era" not in sample.columns:
        from research.dataset import ERA_BOUNDS

        eras = []
        for d in pd.to_datetime(sample["asof_date"]):
            assigned = "unknown"
            for name, (lo, hi) in ERA_BOUNDS.items():
                if d >= pd.Timestamp(lo) and (hi is None or d <= pd.Timestamp(hi)):
                    assigned = name
                    break
            eras.append(assigned)
        sample["era"] = eras
    trades = pd.DataFrame(
        {
            "ticker": sample["ticker"].astype(str).str.upper(),
            "entry_date": pd.to_datetime(sample["asof_date"]).dt.strftime("%Y-%m-%d"),
            "net_return": ret.astype(float),
            "era": sample["era"].astype(str),
            "rank_score_v2": pd.to_numeric(sample["rank_score_v2"], errors="coerce"),
            "sector_etf": sample["sector_etf"] if "sector_etf" in sample.columns else "XLK",
        }
    )
    return trades.reset_index(drop=True)


def assess_dual_run(
    result: dict[str, Any],
    *,
    min_trades: int = MIN_DUAL_RUN_TRADES,
) -> dict[str, Any]:
    """
    Heuristic dual-run gate for promotion extras.

    ``dual_run_ok`` when both prob-rank and rank_v2 control arms have enough
    trades and prob-rank worst-era PF is not below control by more than 0.10.
    """
    pr = result.get("prob_rank") or result.get("equal_weight_top_n") or result.get("portfolio") or {}
    ctrl = result.get("rank_v2_control") or {}
    n_pr = int(pr.get("n") or result.get("n_selected") or 0)
    n_ctrl = int(ctrl.get("n") or 0)
    pr_worst = pr.get("worst_era_pf")
    ctrl_worst = ctrl.get("worst_era_pf")
    pr_pf = pr.get("pf_mean_eras") if pr.get("pf_mean_eras") is not None else pr.get("pf_mean")
    ctrl_pf = ctrl.get("pf_mean")

    reasons: list[str] = []
    ok = True
    if n_pr < min_trades:
        ok = False
        reasons.append(f"prob_rank n={n_pr} < {min_trades}")
    if n_ctrl < min_trades:
        ok = False
        reasons.append(f"rank_v2_control n={n_ctrl} < {min_trades}")
    if pr_worst is not None and ctrl_worst is not None:
        if float(pr_worst) + 0.10 < float(ctrl_worst):
            ok = False
            reasons.append(
                f"prob_rank worst_era_pf={pr_worst} materially below control={ctrl_worst}"
            )
    if not reasons and ok:
        reasons.append("Both arms present with adequate trades; worst-era within tolerance")

    return {
        "dual_run_ok": bool(ok),
        "n_prob_rank": n_pr,
        "n_control": n_ctrl,
        "prob_rank_pf_mean": pr_pf,
        "prob_rank_worst_era_pf": pr_worst,
        "control_pf_mean": ctrl_pf,
        "control_worst_era_pf": ctrl_worst,
        "reasons": reasons,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def run_ops_pipeline(
    *,
    skill_dir: Path,
    mode: str = "smoke",
    ticker_bars: dict[str, pd.DataFrame] | None = None,
    trades: pd.DataFrame | None = None,
    date_start: str | None = "2016-01-01",
    date_end: str | None = "2024-06-01",
    label_set: str = "fwd40",
    top_n: int = 5,
    sizing_mode: str = "equal",
    num_boost_round: int = 80,
    requested_promotion: str = "shadow",
    artifact_dir: Path | None = None,
    write_panels: bool = True,
    apply_registry: bool = False,
) -> OpsPipelineResult:
    """
    Run materialize → dataset → train → CF → portfolio → promotion decision.

    ``mode``:
      - ``smoke``: synthetic universe + synthetic trades (no market/chunks)
      - ``bars``: caller-supplied ``ticker_bars``; trades optional
    """
    skill_dir = Path(skill_dir)
    art = Path(artifact_dir) if artifact_dir is not None else skill_dir / "validation_artifacts" / "prob_rank_ops"
    art.mkdir(parents=True, exist_ok=True)
    ensure_research_store_layout(skill_dir)

    result = OpsPipelineResult(ok=False, mode=mode, meta={"created_at_utc": datetime.now(timezone.utc).isoformat()})

    if mode == "smoke" and not ticker_bars:
        ticker_bars = make_synthetic_universe()
        result.steps.append("synthetic_universe")
    if not ticker_bars:
        result.errors.append("No ticker_bars provided")
        return result

    try:
        ds, ds_path, manifest = build_rank_dataset(
            ticker_bars=ticker_bars,
            date_start=date_start,
            date_end=date_end,
            label_set=label_set,
            skill_dir=skill_dir,
            write=write_panels,
        )
    except Exception as exc:
        LOG.exception("Dataset build failed: %s", exc)
        result.errors.append(f"dataset: {exc}")
        return result

    if ds is None or ds.empty:
        result.errors.append("Dataset empty — widen dates or relax Stage-2 filters")
        return result

    result.dataset_path = str(ds_path) if ds_path else None
    result.steps.append("dataset")
    result.meta["dataset_rows"] = int(len(ds))
    result.meta["leakage_ok"] = bool((manifest or {}).get("leakage", {}).get("ok"))

    feature_cols = resolve_feature_columns(ds)
    if not feature_cols:
        result.errors.append("No feature columns resolved")
        return result

    try:
        artifact = train_prob_rank_model(
            ds,
            feature_cols,
            target_col=DEFAULT_TARGET,
            skill_dir=skill_dir,
            num_boost_round=num_boost_round,
            early_stopping_rounds=max(10, num_boost_round // 5),
            dataset_id=str(ds["dataset_id"].iloc[0]) if "dataset_id" in ds.columns else "ops",
            write=True,
        )
    except Exception as exc:
        LOG.exception("Training failed: %s", exc)
        result.errors.append(f"train: {exc}")
        return result

    model_id = str(artifact.get("model_id"))
    model_dir = research_store_dir(skill_dir) / "models" / model_id
    result.model_id = model_id
    result.model_dir = str(model_dir)
    result.steps.append("train")

    scored = predict_frame(artifact, ds)
    report_out = write_experiment_report(
        run_id=f"ops_{mode}_{model_id}",
        artifact=artifact,
        scored_df=scored,
        skill_dir=skill_dir,
    )
    result.report_dir = str(report_out)
    result.steps.append("report")

    # Trades for dual-run
    trade_frame = trades
    if trade_frame is None or trade_frame.empty:
        if mode == "smoke":
            trade_frame = trades_from_labeled_dataset(ds)
            result.steps.append("synthetic_trades")
        else:
            result.meta["skipped"] = ["counterfactual", "portfolio", "promotion"]
            result.ok = True
            result.warnings.append(
                "No trades provided — trained model only; "
                "re-run with --run-id once multi_era_chunks exist for dual-run/promotion"
            )
            _write_json(art / "ops_pipeline_result.json", result.to_dict())
            return result

    if trade_frame is None or trade_frame.empty:
        result.errors.append("Trade frame empty after synthesis")
        return result

    cf = run_prob_rank_counterfactual(trade_frame, scored, top_n=top_n, control_percentile=75.0)
    cf_path = _write_json(art / f"prob_rank_counterfactual_{mode}.json", cf)
    result.counterfactual_path = str(cf_path)
    result.steps.append("counterfactual")

    port = run_portfolio_research(
        trade_frame,
        scored,
        top_n=top_n,
        sizing_mode=sizing_mode,
        control_percentile=75.0,
    )
    port_path = _write_json(art / f"prob_rank_portfolio_{mode}_{sizing_mode}.json", port)
    result.portfolio_path = str(port_path)
    result.steps.append("portfolio")

    dual = assess_dual_run(port)
    # Also accept CF-shaped keys
    if not dual.get("dual_run_ok"):
        dual_cf = assess_dual_run(cf)
        if dual_cf.get("dual_run_ok"):
            dual = dual_cf
    result.dual_run = dual
    _write_json(art / f"prob_rank_dual_run_{mode}.json", dual)
    result.steps.append("dual_run")

    extras: dict[str, Any] = {"dual_run_ok": bool(dual.get("dual_run_ok"))}
    metrics_path = Path(report_out) / "metrics.json"
    if metrics_path.is_file():
        m = json.loads(metrics_path.read_text(encoding="utf-8"))
        if m.get("walk_forward_ic_mean") is not None:
            extras["walk_forward_ic_mean"] = m.get("walk_forward_ic_mean")
    metrics = metrics_from_portfolio_result(port, extras=extras)
    verdict = evaluate_prob_rank_promotion(metrics, requested=requested_promotion)
    promo_payload = {
        "decision": verdict.decision,
        "floors_cleared": verdict.floors_cleared,
        "composite_score": verdict.composite_score,
        "rationale": verdict.rationale,
        "gates": verdict.gates,
        "dimension_scores": verdict.dimension_scores,
        "metrics": metrics,
        "artifact": str(port_path),
        "mode": mode,
    }
    promo_path = _write_json(art / f"prob_rank_promotion_decision_{mode}.json", promo_payload)
    result.promotion_path = str(promo_path)
    result.promotion = {
        "decision": verdict.decision,
        "floors_cleared": verdict.floors_cleared,
        "composite_score": verdict.composite_score,
    }
    result.steps.append("promotion")

    if apply_registry:
        from experiment_registry import append_registry_event

        append_registry_event(
            event_type="prob_rank_promotion_decision",
            target="PROB_RANK_MODE",
            decision=verdict.decision,
            rationale=verdict.rationale + [f"ops_pipeline mode={mode}"],
            gates=verdict.gates,
            metadata={
                "artifact": str(port_path),
                "decision_path": str(promo_path),
                "model_id": model_id,
                "mode": mode,
                "dual_run": dual,
            },
            skill_dir=skill_dir,
        )
        result.steps.append("registry")

    result.ok = True
    _write_json(art / "ops_pipeline_result.json", result.to_dict())
    LOG.info(
        "Ops pipeline ok mode=%s model=%s decision=%s dual_run_ok=%s",
        mode,
        model_id,
        verdict.decision,
        dual.get("dual_run_ok"),
    )
    return result
