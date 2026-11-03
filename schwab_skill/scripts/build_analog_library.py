#!/usr/bin/env python3
"""Build the analog winner/loser library from existing backtest artifacts.

Does not run a new multi-era backtest. Re-reads ``control_legacy_aug`` chunks
(via ``_load_trade_frame``) and, when present, the labeled research parquet.

Writes:
  research_store/models/analog_library.json
  research_store/models/analog_library_<short_id>.json

Usage (from schwab_skill/):
  python scripts/build_analog_library.py
  python scripts/build_analog_library.py --run-id control_legacy_aug
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.scoring_analog import (  # noqa: E402
    DEFAULT_ARTIFACT_PATH,
    DEFAULT_K,
    DEFAULT_MIN_N,
    FEATURE_NAMES,
    MIN_ERA_LABELED_ROWS,
    analog_model_id,
    compact_library_row,
    era_priors,
    fit_feature_bounds,
    label_outcome_rows,
    raw_features_from_signal_row,
)
from research.counterfactual import _safe_pf  # noqa: E402
from research.report import calibration_deciles  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

LOG = logging.getLogger("build_analog_library")
MODEL_DIR = SKILL_DIR / "research_store" / "models"
DEFAULT_RUN_ID = "control_legacy_aug"
DEFAULT_PANEL = (
    SKILL_DIR
    / "validation_artifacts"
    / "prob_rank_control_confirm"
    / "scored_features_control_legacy_aug_labeled.parquet"
)
EXTRA_CHUNK_KEYS = (
    "exit_date",
    "exit_reason",
    "volume_ratio",
    "pct_from_52w_high",
    "avg_vcp_volume_ratio",
    "regime_above_200",
    "regime_bucket",
    "hold_days",
)


def _chunk_dirs(run_id: str) -> list[Path]:
    root = SKILL_DIR / "validation_artifacts" / "multi_era_chunks"
    preferred = root / run_id
    if preferred.exists():
        return [preferred]
    return [root]


def _merge_chunk_extras(trades: pd.DataFrame, run_id: str) -> pd.DataFrame:
    """Attach hold/exit/regime fields that ``_load_trade_frame`` does not keep."""
    extras: list[dict[str, Any]] = []
    for base in _chunk_dirs(run_id):
        for era_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            for chunk_path in sorted(era_dir.glob("chunk_*.json")):
                if chunk_path.name.endswith("_tickers.json"):
                    continue
                try:
                    payload = json.loads(chunk_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                for raw in payload.get("trades", []):
                    ticker = str(raw.get("ticker") or "").upper()
                    try:
                        entry = pd.Timestamp(raw.get("entry_date") or "")
                    except Exception:
                        continue
                    if not ticker or pd.isna(entry):
                        continue
                    row: dict[str, Any] = {
                        "ticker": ticker,
                        "entry_date": entry.normalize(),
                    }
                    for key in EXTRA_CHUNK_KEYS:
                        if raw.get(key) is not None:
                            row[key] = raw.get(key)
                    if row.get("hold_days") is None and raw.get("exit_date"):
                        try:
                            exit_ = pd.Timestamp(raw.get("exit_date"))
                            if not pd.isna(exit_):
                                row["hold_days"] = int((exit_.normalize() - entry.normalize()).days)
                                row["exit_date"] = exit_.normalize()
                        except Exception:
                            pass
                    extras.append(row)
    if not extras:
        return trades
    extra_df = pd.DataFrame(extras).drop_duplicates(subset=["ticker", "entry_date"], keep="last")
    work = trades.copy()
    work["entry_date"] = pd.to_datetime(work["entry_date"], errors="coerce").dt.normalize()
    extra_df["entry_date"] = pd.to_datetime(extra_df["entry_date"], errors="coerce").dt.normalize()
    return work.merge(extra_df, on=["ticker", "entry_date"], how="left", suffixes=("", "_chunk"))


def _merge_labeled_panel(trades: pd.DataFrame, panel_path: Path) -> tuple[pd.DataFrame, str | None]:
    if not panel_path.is_file():
        return trades, None
    try:
        panel = pd.read_parquet(panel_path)
    except Exception as exc:
        LOG.warning("Labeled parquet unreadable at %s: %s", panel_path, exc)
        return trades, None
    if panel.empty:
        return trades, None
    work = panel.copy()
    if "asof_date" in work.columns:
        work["entry_date"] = pd.to_datetime(work["asof_date"], errors="coerce").dt.normalize()
    elif "entry_date" in work.columns:
        work["entry_date"] = pd.to_datetime(work["entry_date"], errors="coerce").dt.normalize()
    else:
        return trades, None
    if "ticker" not in work.columns:
        return trades, None
    work["ticker"] = work["ticker"].astype(str).str.upper()
    keep = [
        c
        for c in (
            "ticker",
            "entry_date",
            "pct_from_52w_high",
            "volume_ratio",
            "avg_vcp_volume_ratio",
            "dist_sma200_pct",
            "ret_40d_fwd",
            "advisory_p_up_10d",
            "pts_mirofish",
        )
        if c in work.columns
    ]
    slim = work[keep].drop_duplicates(subset=["ticker", "entry_date"], keep="last")
    merged = trades.merge(slim, on=["ticker", "entry_date"], how="left", suffixes=("", "_panel"))
    if "close_vs_sma200_pct" in merged.columns and "dist_sma200_pct" in merged.columns:
        merged["close_vs_sma200_pct"] = merged["close_vs_sma200_pct"].fillna(merged["dist_sma200_pct"])
    if "p_up_calibrated" in merged.columns and "advisory_p_up_10d" in merged.columns:
        merged["p_up_calibrated"] = merged["p_up_calibrated"].fillna(merged["advisory_p_up_10d"])
    return merged, str(panel_path)


def load_analog_source_frame(
    run_id: str,
    *,
    panel_path: Path | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Trade frame plus optional parquet features. Never invents rows."""
    sources: list[str] = []
    trades = _load_trade_frame(run_id)
    sources.append(f"multi_era_chunks/{run_id}")
    trades = _merge_chunk_extras(trades, run_id)
    panel = panel_path if panel_path is not None else DEFAULT_PANEL
    trades, panel_used = _merge_labeled_panel(trades, panel)
    if panel_used:
        sources.append(panel_used)
    return trades, sources


def _rows_from_frame(df: pd.DataFrame) -> list[dict[str, Any]]:
    records = df.to_dict("records")
    out: list[dict[str, Any]] = []
    for raw in records:
        row = dict(raw)
        row["score_components"] = {
            "pts_volume": raw.get("pts_volume"),
            "pts_mirofish": raw.get("pts_mirofish"),
            "pts_52w": raw.get("pts_52w"),
            "pct_from_52w_high": raw.get("pct_from_52w_high"),
            "avg_vcp_volume_ratio": raw.get("avg_vcp_volume_ratio"),
        }
        if row.get("hold_days") is None and raw.get("entry_date") is not None and raw.get("exit_date") is not None:
            try:
                hold = (pd.Timestamp(raw["exit_date"]) - pd.Timestamp(raw["entry_date"])).days
                row["hold_days"] = int(hold)
            except Exception:
                pass
        out.append(row)
    return out


def _era_label_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        era = str(row.get("era") or "unknown")
        bucket = counts.setdefault(era, {"n": 0, "winners": 0, "losers": 0, "labeled": 0})
        bucket["n"] += 1
        if row.get("is_winner"):
            bucket["winners"] += 1
            bucket["labeled"] += 1
        if row.get("is_loser"):
            bucket["losers"] += 1
            bucket["labeled"] += 1
    return counts


def build_library_from_rows(
    source_rows: list[dict[str, Any]],
    *,
    sources: list[str],
    k: int = DEFAULT_K,
    min_n: int = DEFAULT_MIN_N,
    min_era_labeled: int = MIN_ERA_LABELED_ROWS,
    fail_closed: bool = True,
) -> dict[str, Any]:
    labeled = label_outcome_rows(source_rows, outcome_key="net_return")
    counts = _era_label_counts(labeled)
    thin = {era: c for era, c in counts.items() if c["labeled"] < min_era_labeled}
    if thin and fail_closed:
        detail = ", ".join(f"{era} labeled={c['labeled']} n={c['n']}" for era, c in sorted(thin.items()))
        raise ValueError(
            f"era labeled-row floor is {min_era_labeled}; refusing to mix thin eras: {detail}"
        )

    raws = [raw_features_from_signal_row(row) for row in labeled]
    bounds = fit_feature_bounds(raws)
    compact: list[dict[str, Any]] = []
    for row in labeled:
        packed = compact_library_row(row, bounds=bounds)
        if packed is not None:
            compact.append(packed)
    if not compact:
        raise ValueError("no analog-scorable rows after feature extract")

    priors = era_priors(labeled)
    score_rows = [
        {
            # Seed only. Walk-forward analog_p_win vs realized return is the
            # counterfactual script; this in-sample flag is not a promotion input.
            "analog_p_win": 1.0 if packed.get("is_winner") else (0.0 if packed.get("is_loser") else 0.5),
            "net_return": packed.get("net_return"),
        }
        for packed in compact
    ]
    cal_df = pd.DataFrame(score_rows)
    deciles: list[dict[str, Any]] = []
    if len(cal_df) >= 20:
        deciles = calibration_deciles(cal_df, score_col="analog_p_win", label_col="net_return")
        for bin_row, grp in cal_df.assign(
            bin=pd.qcut(cal_df["analog_p_win"], 10, labels=False, duplicates="drop")
        ).groupby("bin"):
            pf = _safe_pf(grp["net_return"])
            for item in deciles:
                if int(item.get("bin", -1)) == int(bin_row):
                    item["pf"] = pf

    label_rule = "quintile_net_return"
    model_id = analog_model_id(
        features=FEATURE_NAMES,
        bounds=bounds,
        label_rule=label_rule,
        sources=sources,
        k=k,
        min_n=min_n,
    )
    return {
        "kind": "analog_library",
        "model_id": model_id,
        "distance": "euclidean",
        "k": int(k),
        "min_n": int(min_n),
        "min_era_labeled": int(min_era_labeled),
        "features": list(FEATURE_NAMES),
        "feature_bounds": bounds,
        "label_rule": label_rule,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "row_counts": {
            "n_source": len(source_rows),
            "n_library": len(compact),
            "winners": sum(1 for r in compact if r.get("is_winner")),
            "losers": sum(1 for r in compact if r.get("is_loser")),
            "by_era": counts,
        },
        "era_priors": priors,
        "calibration_deciles": deciles,
        "rows": compact,
    }


def write_library(artifact: dict[str, Any], *, out_dir: Path | None = None) -> list[Path]:
    dest = out_dir or MODEL_DIR
    dest.mkdir(parents=True, exist_ok=True)
    canonical = dest / Path(DEFAULT_ARTIFACT_PATH).name
    hashed = dest / f"analog_library_{str(artifact.get('model_id') or 'unknown').replace('analog_', '')}.json"
    text = json.dumps(artifact, indent=2, default=str)
    written: list[Path] = []
    for path in (canonical, hashed):
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--panel", default=str(DEFAULT_PANEL))
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--min-n", type=int, default=DEFAULT_MIN_N)
    parser.add_argument("--min-era-labeled", type=int, default=MIN_ERA_LABELED_ROWS)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)

    try:
        frame, sources = load_analog_source_frame(args.run_id, panel_path=Path(args.panel))
    except Exception as exc:
        print(f"FAIL: could not load analog source for {args.run_id}: {exc}")
        return 2

    print(f"Source rows: {len(frame)} | sources: {sources}")
    try:
        artifact = build_library_from_rows(
            _rows_from_frame(frame),
            sources=sources,
            k=args.k,
            min_n=args.min_n,
            min_era_labeled=args.min_era_labeled,
        )
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 2

    counts = artifact["row_counts"]
    print(
        f"Library rows: {counts['n_library']} | winners={counts['winners']} "
        f"losers={counts['losers']} | model {artifact['model_id']}"
    )
    for era, bucket in sorted((counts.get("by_era") or {}).items()):
        print(
            f"  {era:20s} n={bucket['n']:5d}  labeled={bucket['labeled']:5d}  "
            f"W={bucket['winners']:4d} L={bucket['losers']:4d}"
        )

    out_dir = Path(args.out_dir) if args.out_dir else MODEL_DIR
    if not out_dir.is_absolute():
        out_dir = SKILL_DIR / out_dir
    written = write_library(artifact, out_dir=out_dir)
    for path in written:
        try:
            rel = path.relative_to(SKILL_DIR)
        except ValueError:
            rel = path
        print(f"Wrote {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
