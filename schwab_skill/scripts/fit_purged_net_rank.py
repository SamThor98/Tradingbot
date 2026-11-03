#!/usr/bin/env python3
"""Fit a purged ridge on realized net return and compare it to inverted pts_52w.

Does not set PROB_RANK_MODE, HISTORICAL_RANK_MODE, or SCAN_LIVE_SORT_KEY.
The artifact is a research report, not a scanner model.

Usage (from schwab_skill/):
  python scripts/fit_purged_net_rank.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.labels import LABEL_COLUMNS  # noqa: E402
from research.open_research import arm_stats, walk_forward_percentile  # noqa: E402
from research.purged_net_rank import (  # noqa: E402
    TARGET,
    fit_purged_oos_scores,
    legal_feature_columns,
    purged_rank_verdict,
)
from scripts.run_open_research import CONTROL, _load_scores  # noqa: E402

DEFAULT_PANEL = (
    SKILL_DIR
    / "validation_artifacts"
    / "prob_rank_control_confirm"
    / "scored_features_control_legacy_aug_labeled.parquet"
)
OUT_DIR = SKILL_DIR / "validation_artifacts" / "purged_net_rank"
# Chart features already measured on this book. Keep the fit small and legal.
CANDIDATE_FEATURES = (
    "pts_52w",
    "signal_score",
    "rank_score_v2",
    "close_vs_sma200_pct",
    "dist_sma200_pct",
    "dist_sma50_pct",
    "pct_from_52w_high",
    "volume_ratio",
    "avg_vcp_volume_ratio",
    "ret_5d_prev",
    "ret_20d_prev",
    "ret_60d_prev",
    "atr_pct",
    "sma_200_slope",
    "trend_efficiency_20d",
    "breakout_distance_pct",
)


def _load_panel(path: Path) -> pd.DataFrame:
    """Entered trades carry net_return. The labeled panel only adds PIT features.

    Forward-return columns on the panel are dropped before the fit so a
    ``_panel`` twin cannot become an oracle feature.
    """
    trades = _load_scores(CONTROL)
    if not path.is_file():
        raise FileNotFoundError(path)
    panel = pd.read_parquet(path)
    work = panel.copy()
    work["entry_date"] = pd.to_datetime(work.get("asof_date"), errors="coerce").dt.normalize()
    work["ticker"] = work["ticker"].astype(str).str.upper()
    work = work.dropna(subset=["entry_date"]).drop_duplicates(subset=["ticker", "entry_date"], keep="last")
    drop = {"era", "asof_date", "dataset_id", "bar_provider", "confidence", *LABEL_COLUMNS}
    out = trades.copy()
    out["entry_date"] = pd.to_datetime(out["entry_date"], errors="coerce").dt.normalize()
    out["ticker"] = out["ticker"].astype(str).str.upper()
    existing = set(out.columns) - {"ticker", "entry_date"}
    keep = [col for col in work.columns if col not in drop and col not in existing]
    merged = out.merge(work[keep], on=["ticker", "entry_date"], how="left")
    merged["asof_date"] = merged["entry_date"]
    if TARGET not in merged.columns:
        raise ValueError(f"joined book has no {TARGET} column")
    return merged


def _present_features(df: pd.DataFrame) -> list[str]:
    available = [name for name in CANDIDATE_FEATURES if name in df.columns]
    kept, _rejected = legal_feature_columns(available)
    numeric: list[str] = []
    for col in kept:
        series = pd.to_numeric(df[col], errors="coerce")
        if float(series.notna().mean()) < 0.5:
            continue
        if int(series.nunique(dropna=True)) < 5:
            continue
        numeric.append(col)
    return numeric


def run_fit(df: pd.DataFrame) -> dict[str, Any]:
    features = _present_features(df)
    if not features:
        raise ValueError("panel has none of the candidate features")
    fitted = fit_purged_oos_scores(df, features)
    scored = fitted["scored"]
    scored = scored.loc[scored["purged_net_score"].notna()].copy()
    if "pts_52w" in scored.columns:
        scored["inv_pts52w"] = -pd.to_numeric(scored["pts_52w"], errors="coerce")
    elif "pct_from_52w_high" in scored.columns:
        scored["inv_pts52w"] = -pd.to_numeric(scored["pct_from_52w_high"], errors="coerce")
    else:
        raise ValueError("panel has no pts_52w or pct_from_52w_high control")
    if "era" not in scored.columns:
        raise ValueError("panel has no era column for the profit-factor floors")
    arms = {
        "take_every_scored_trade": arm_stats(scored),
        "wf_p76_purged_net": arm_stats(walk_forward_percentile(scored, score_col="purged_net_score", min_percentile=76)),
        "wf_p76_inv_pts52w": arm_stats(walk_forward_percentile(scored, score_col="inv_pts52w", min_percentile=76)),
    }
    verdict = purged_rank_verdict(arms["wf_p76_purged_net"], arms["wf_p76_inv_pts52w"])
    return {
        "target": TARGET,
        "features": fitted["features"],
        "rejected_features": fitted["rejected_features"],
        "n_rows": fitted["n_rows"],
        "n_scored": int(len(scored)),
        "folds": fitted["folds"],
        "arms": arms,
        "verdict": verdict,
        "prob_rank_mode_unchanged": True,
        "scan_live_sort_key_unchanged": True,
        "note": (
            "OOS ridge on net_return under purged splits with a 40-day embargo. "
            "Promotion requires beating inverted pts_52w by 0.03 PF mean and clearing "
            "1.20 / 1.00. A shadow-ready verdict does not load a scanner model."
        ),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def write_reports(payload: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = OUT_DIR / "counterfactual.json"
    out_md = OUT_DIR / "counterfactual.md"
    slim = {k: v for k, v in payload.items() if k != "folds"}
    slim["n_folds"] = len(payload.get("folds") or [])
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# Purged net-return rank",
        "",
        f"Generated {payload['generated_at']}. PROB_RANK_MODE was not changed.",
        "",
        f"Verdict: `{payload['verdict']}`.",
        "",
        f"Target `{payload['target']}` on {payload['n_scored']} scored rows. Features: {', '.join(payload['features'])}.",
        "",
        "| Arm | n | PF mean | Worst era | Clears floors |",
        "|---|---:|---:|---:|---|",
    ]
    for name, arm in payload["arms"].items():
        lines.append(
            f"| {name} | {arm['n']} | {_fmt(arm['pf_mean'])} | {_fmt(arm['worst_era_pf'])} | {arm['clears_floors']} |"
        )
    lines.extend(["", payload["note"], ""])
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_json}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit purged net-return rank. Does not go live.")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    args = parser.parse_args()
    df = _load_panel(args.panel)
    block = run_fit(df)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "panel": str(args.panel), **block}
    write_reports(payload)
    print(f"Verdict: {payload['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
