#!/usr/bin/env python3
"""Entry-timing shadow counterfactual on realized trades (P0).

Replays live shadow rules (SMA50 cushion, breakout buffer, extension cap) at
historical entry dates using yfinance bars, and reports oracle bounds from
stored ``ohlc_path`` (first-5-day drawdown, MAE).

Usage (from schwab_skill/):
  python scripts/analyze_entry_timing_shadow_counterfactual.py
  python scripts/analyze_entry_timing_shadow_counterfactual.py --max-trades 200
  python scripts/analyze_entry_timing_shadow_counterfactual.py --all
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.analyze_early_stopout_cohorts import _merge_trade_metadata  # noqa: E402
from scripts.analyze_rank_filter_counterfactual import (  # noqa: E402
    DEFAULT_RUN_ID,
    OVERLAP_ERAS,
    _cohort_stats,
    _era_pf_from_df,
    _hold_days_map,
    _merge_hold_days,
)
from scripts.phase2_common import load_trades  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402
from stage_analysis import add_indicators, evaluate_entry_timing_shadow  # noqa: E402

LOG = logging.getLogger(__name__)
ART = SKILL_DIR / "validation_artifacts"
REPLAY_CACHE_NAME = "entry_timing_replay_cache_{run_id}.json"


def _label_cohort(row: pd.Series) -> str:
    hold = int(row.get("hold_days") or 0)
    net = float(row.get("net_return") or 0.0)
    if hold <= 20 and net < 0:
        return "early_stop"
    if 21 <= hold <= 40 and net > 0:
        return "winner_21_40"
    return "other"


def _first_n_drawdown(path: list[dict[str, Any]], n: int = 5) -> float | None:
    if len(path) < 2:
        return None
    entry = float(path[0]["close"])
    if entry <= 0:
        return None
    lows = [float(bar["low"]) for bar in path[:n]]
    return (min(lows) - entry) / entry


def _oracle_scenarios(df: pd.DataFrame) -> list[dict[str, Any]]:
    baseline = _cohort_stats(df)
    baseline_overlap, _, _ = _era_pf_from_df(df, OVERLAP_ERAS)
    rows: list[dict[str, Any]] = []
    dd_vals = df["dd5"].dropna() if "dd5" in df.columns else pd.Series(dtype=float)
    for thresh in (-0.035, -0.04, -0.045):
        if dd_vals.empty:
            continue
        drop = df["dd5"].notna() & (df["dd5"] <= thresh)
        kept = df[~drop]
        if len(kept) < 30:
            continue
        cohort = _cohort_stats(kept)
        overlap_mean, _, _ = _era_pf_from_df(kept, OVERLAP_ERAS)
        early = kept[kept["cohort"] == "early_stop"]
        winners = kept[kept["cohort"] == "winner_21_40"]
        rows.append(
            {
                "scenario": f"oracle_dd5_lte_{abs(thresh):.3f}",
                "kind": "oracle",
                "retention_pct": round(100 * len(kept) / len(df), 1),
                "early_stop_retention_pct": round(100 * len(early) / max(1, len(df[df["cohort"] == "early_stop"])), 1),
                "winner_retention_pct": round(100 * len(winners) / max(1, len(df[df["cohort"] == "winner_21_40"])), 1),
                "pf_all": cohort["pf"],
                "overlap_pf_mean": overlap_mean,
                "delta_overlap_pf_mean": round(overlap_mean - baseline_overlap, 4),
                "delta_early_stopout_pp": round(
                    cohort["early_stopout_pct"] - baseline["early_stopout_pct"], 2
                ),
            }
        )
    return rows


def _fetch_df_through_entry(ticker: str, entry_date: pd.Timestamp) -> pd.DataFrame | None:
    try:
        import yfinance as yf  # type: ignore
    except Exception:
        return None
    try:
        start = (entry_date - timedelta(days=420)).date().isoformat()
        end = (entry_date + timedelta(days=2)).date().isoformat()
        raw = yf.download(
            ticker,
            start=start,
            end=end,
            progress=False,
            auto_adjust=False,
        )
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [str(c[0]).lower() for c in raw.columns]
        else:
            raw.columns = [str(c).lower() for c in raw.columns]
        raw = raw.rename(columns={"close": "close", "high": "high", "low": "low", "open": "open", "volume": "volume"})
        raw.index = pd.to_datetime(raw.index).tz_localize(None)
        entry_norm = entry_date.normalize()
        eligible = raw[raw.index <= entry_norm]
        if len(eligible) < 200:
            return None
        return add_indicators(eligible)
    except Exception:
        return None


def _replay_shadow_rules(
    df: pd.DataFrame,
    *,
    skill_dir: Path,
    max_trades: int | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    trades = load_trades(df.attrs.get("run_id", DEFAULT_RUN_ID))
    if max_trades is not None:
        trades = trades[: max_trades]
    replay_rows: list[dict[str, Any]] = []
    metrics_rows: list[dict[str, Any]] = []
    for idx, trade in enumerate(trades, start=1):
        if not trade.ticker:
            continue
        if idx % 50 == 0:
            LOG.info("Replay progress: %s / %s", idx, len(trades))
        hist = _fetch_df_through_entry(str(trade.ticker).upper(), trade.entry_date)
        if hist is None or hist.empty:
            continue
        shadow = evaluate_entry_timing_shadow(hist, skill_dir)
        key = (trade.era, str(trade.ticker).upper(), trade.entry_date.strftime("%Y-%m-%d"))
        base = df[
            (df["era"] == trade.era)
            & (df["ticker"] == str(trade.ticker).upper())
            & (df["entry_iso"] == trade.entry_date.strftime("%Y-%m-%d"))
        ]
        if base.empty:
            continue
        row = base.iloc[0].to_dict()
        row["entry_shadow_would_filter"] = bool(shadow.get("would_filter"))
        row["entry_shadow_reasons"] = list(shadow.get("would_filter_reasons") or [])
        row["pct_above_sma50"] = shadow.get("pct_above_sma50")
        row["breakout_buffer_pct"] = shadow.get("breakout_buffer_pct")
        row["pct_from_52w_high"] = shadow.get("pct_from_52w_high")
        metrics_rows.append(row)
        replay_rows.append({"key": key, **shadow})
    replay_df = pd.DataFrame(metrics_rows)
    meta = {
        "replayed_trades": len(replay_df),
        "would_filter_any": int(replay_df["entry_shadow_would_filter"].sum()) if not replay_df.empty else 0,
        "reason_counts": {},
    }
    if not replay_df.empty:
        reason_counts: dict[str, int] = {}
        for reasons in replay_df["entry_shadow_reasons"]:
            for reason in reasons or []:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        meta["reason_counts"] = reason_counts
    return replay_df, meta


def _apply_shadow_flags(
    replay_df: pd.DataFrame,
    *,
    skill_dir: Path,
) -> pd.DataFrame:
    """Recompute would-filter flags from stored metrics using current config thresholds."""
    if replay_df.empty:
        return replay_df
    from config import (
        get_entry_shadow_disable_sma50_filters,
        get_entry_shadow_max_pct_above_sma50,
        get_entry_shadow_min_breakout_buffer_pct,
        get_entry_shadow_min_pct_above_sma50,
    )

    min_sma = float(get_entry_shadow_min_pct_above_sma50(skill_dir))
    max_sma = float(get_entry_shadow_max_pct_above_sma50(skill_dir))
    min_buf = float(get_entry_shadow_min_breakout_buffer_pct(skill_dir))
    disable_sma50 = get_entry_shadow_disable_sma50_filters(skill_dir)
    out = replay_df.copy()
    reasons: list[list[str]] = []
    flags: list[bool] = []
    for _, row in out.iterrows():
        row_reasons: list[str] = []
        if not disable_sma50:
            pct_above = row.get("pct_above_sma50")
            if pct_above is not None and pd.notna(pct_above):
                if float(pct_above) < min_sma:
                    row_reasons.append("sma50_cushion_low")
                elif float(pct_above) > max_sma:
                    row_reasons.append("sma50_extension_high")
        buffer_pct = row.get("breakout_buffer_pct")
        if buffer_pct is not None and pd.notna(buffer_pct) and float(buffer_pct) < min_buf:
            row_reasons.append("breakout_buffer_low")
        reasons.append(row_reasons)
        flags.append(bool(row_reasons))
    out["entry_shadow_reasons"] = reasons
    out["entry_shadow_would_filter"] = flags
    return out


def _replay_cache_path(run_id: str) -> Path:
    return ART / REPLAY_CACHE_NAME.format(run_id=run_id)


def _save_replay_cache(replay_df: pd.DataFrame, run_id: str) -> None:
    if replay_df.empty:
        return
    payload = replay_df.to_dict(orient="records")
    _replay_cache_path(run_id).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _load_replay_cache(run_id: str) -> pd.DataFrame:
    path = _replay_cache_path(run_id)
    if not path.exists():
        return pd.DataFrame()
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return pd.DataFrame()
    if not isinstance(rows, list):
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _would_filter_metrics(
    row: pd.Series,
    *,
    min_pct_above_sma50: float,
    max_pct_above_sma50: float,
    min_breakout_buffer_pct: float,
) -> bool:
    pct_above = row.get("pct_above_sma50")
    if pct_above is not None and pd.notna(pct_above):
        if float(pct_above) < min_pct_above_sma50 or float(pct_above) > max_pct_above_sma50:
            return True
    buffer_pct = row.get("breakout_buffer_pct")
    if buffer_pct is not None and pd.notna(buffer_pct):
        if float(buffer_pct) < min_breakout_buffer_pct:
            return True
    return False


def _sweep_shadow_thresholds(replay_df: pd.DataFrame) -> list[dict[str, Any]]:
    if replay_df.empty or len(replay_df) < 30:
        return []
    baseline = _cohort_stats(replay_df)
    baseline_overlap, _, _ = _era_pf_from_df(replay_df, OVERLAP_ERAS)
    rows: list[dict[str, Any]] = []
    min_buffers = (0.001, 0.002, 0.003, 0.005, 0.008)
    min_sma50_vals = (0.005, 0.01, 0.015, 0.02)
    max_sma50_vals = (0.10, 0.12, 0.15)
    for min_buf in min_buffers:
        for min_sma in min_sma50_vals:
            for max_sma in max_sma50_vals:
                drop = replay_df.apply(
                    lambda r: _would_filter_metrics(
                        r,
                        min_pct_above_sma50=min_sma,
                        max_pct_above_sma50=max_sma,
                        min_breakout_buffer_pct=min_buf,
                    ),
                    axis=1,
                )
                kept = replay_df[~drop]
                if len(kept) < 30:
                    continue
                cohort = _cohort_stats(kept)
                overlap_mean, _, _ = _era_pf_from_df(kept, OVERLAP_ERAS)
                rows.append(
                    {
                        "min_breakout_buffer_pct": min_buf,
                        "min_pct_above_sma50": min_sma,
                        "max_pct_above_sma50": max_sma,
                        "retention_pct": round(100 * len(kept) / len(replay_df), 1),
                        "would_drop": int(drop.sum()),
                        "pf_all": cohort["pf"],
                        "overlap_pf_mean": overlap_mean,
                        "delta_overlap_pf_mean": round(overlap_mean - baseline_overlap, 4),
                        "delta_early_stopout_pp": round(
                            cohort["early_stopout_pct"] - baseline["early_stopout_pct"], 2
                        ),
                    }
                )
    rows.sort(
        key=lambda r: (
            float(r.get("delta_early_stopout_pp") or 999),
            -float(r.get("delta_overlap_pf_mean") or -999),
        ),
    )
    return rows[:25]


def _sweep_breakout_buffer_only(replay_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Sweep breakout buffer alone (no SMA50 cushion/extension filters)."""
    if replay_df.empty or len(replay_df) < 30:
        return []
    baseline = _cohort_stats(replay_df)
    baseline_overlap, _, _ = _era_pf_from_df(replay_df, OVERLAP_ERAS)
    rows: list[dict[str, Any]] = []
    for min_buf in (0.0, 0.001, 0.002, 0.003, 0.005, 0.008, 0.01):
        drop = replay_df["breakout_buffer_pct"].apply(
            lambda v: pd.notna(v) and float(v) < min_buf
        )
        kept = replay_df[~drop.fillna(False)]
        if len(kept) < 30:
            continue
        cohort = _cohort_stats(kept)
        overlap_mean, _, _ = _era_pf_from_df(kept, OVERLAP_ERAS)
        rows.append(
            {
                "scenario": f"breakout_buffer_only_gte_{min_buf:.3f}",
                "min_breakout_buffer_pct": min_buf,
                "retention_pct": round(100 * len(kept) / len(replay_df), 1),
                "would_drop": int(drop.sum()),
                "pf_all": cohort["pf"],
                "overlap_pf_mean": overlap_mean,
                "delta_overlap_pf_mean": round(overlap_mean - baseline_overlap, 4),
                "delta_early_stopout_pp": round(
                    cohort["early_stopout_pct"] - baseline["early_stopout_pct"], 2
                ),
            }
        )
    rows.sort(
        key=lambda r: (
            float(r.get("delta_early_stopout_pp") or 999),
            -float(r.get("delta_overlap_pf_mean") or -999),
        ),
    )
    return rows


def _pick_threshold_recommendation(sweep_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not sweep_rows:
        return {"action": "insufficient_replay_data", "reason": "No threshold sweep rows."}
    for row in sweep_rows:
        if (
            (row.get("delta_overlap_pf_mean") or 0) >= 0.05
            and row.get("retention_pct", 0) >= 50
            and (row.get("delta_early_stopout_pp") or 0) <= -3
        ):
            return {
                "action": "tune_shadow_thresholds",
                "thresholds": {
                    "ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT": row["min_breakout_buffer_pct"],
                    "ENTRY_SHADOW_MIN_PCT_ABOVE_SMA50": row["min_pct_above_sma50"],
                    "ENTRY_SHADOW_MAX_PCT_ABOVE_SMA50": row["max_pct_above_sma50"],
                },
                "expected_delta_early_stopout_pp": row.get("delta_early_stopout_pp"),
                "expected_delta_overlap_pf_mean": row.get("delta_overlap_pf_mean"),
                "retention_pct": row.get("retention_pct"),
                "reason": "Threshold combo improves overlap PF and early stops on replay sample.",
            }
    best = sweep_rows[0]
    return {
        "action": "keep_default_shadow_thresholds",
        "best_seen": best,
        "reason": (
            "No threshold combo met promotion criteria on replay sample "
            "(overlap PF +0.05, retention >=50%, early stops -3pp)."
        ),
    }


def _scenario_from_replay(replay_df: pd.DataFrame) -> list[dict[str, Any]]:
    if replay_df.empty:
        return []
    baseline = _cohort_stats(replay_df)
    baseline_overlap, _, _ = _era_pf_from_df(replay_df, OVERLAP_ERAS)
    kept = replay_df[~replay_df["entry_shadow_would_filter"].fillna(False)]
    if len(kept) < 30:
        return []
    cohort = _cohort_stats(kept)
    overlap_mean, _, _ = _era_pf_from_df(kept, OVERLAP_ERAS)
    early_base = replay_df[replay_df["cohort"] == "early_stop"]
    winner_base = replay_df[replay_df["cohort"] == "winner_21_40"]
    early_kept = kept[kept["cohort"] == "early_stop"]
    winner_kept = kept[kept["cohort"] == "winner_21_40"]
    return [
        {
            "scenario": "live_shadow_any_rule",
            "kind": "live_shadow",
            "retention_pct": round(100 * len(kept) / len(replay_df), 1),
            "early_stop_retention_pct": round(100 * len(early_kept) / max(1, len(early_base)), 1),
            "winner_retention_pct": round(100 * len(winner_kept) / max(1, len(winner_base)), 1),
            "pf_all": cohort["pf"],
            "overlap_pf_mean": overlap_mean,
            "delta_overlap_pf_mean": round(overlap_mean - baseline_overlap, 4),
            "delta_early_stopout_pp": round(
                cohort["early_stopout_pct"] - baseline["early_stopout_pct"], 2
            ),
        }
    ]


def _pick_recommendation(
    oracle_rows: list[dict[str, Any]],
    live_rows: list[dict[str, Any]],
    threshold_rec: dict[str, Any],
    buffer_only_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    for row in buffer_only_rows:
        if (
            (row.get("delta_overlap_pf_mean") or 0) >= 0.05
            and row.get("retention_pct", 0) >= 50
            and (row.get("delta_early_stopout_pp") or 0) <= -3
        ):
            return {
                "action": "experiment_breakout_buffer_only",
                "reason": (
                    "Breakout-buffer-only shadow at "
                    f"ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT={row.get('min_breakout_buffer_pct')} "
                    "meets offline promotion shape on full replay (retention >=50%, early stops -3pp, overlap PF +0.05). "
                    "Disable SMA50 extension/cushion counters or set MAX very high; keep shadow-only."
                ),
                "breakout_buffer_only": row,
            }
    if threshold_rec.get("action") == "tune_shadow_thresholds":
        return {
            "action": "tune_shadow_thresholds",
            "reason": threshold_rec.get("reason"),
            "thresholds": threshold_rec.get("thresholds"),
            "threshold_sweep": threshold_rec,
        }
    live = live_rows[0] if live_rows else None
    if live and (live.get("delta_overlap_pf_mean") or 0) < -0.05:
        best_buffer = buffer_only_rows[0] if buffer_only_rows else None
        return {
            "action": "revise_shadow_thresholds",
            "reason": (
                "Current shadow defaults hurt overlap-era PF on full replay "
                f"({live.get('delta_overlap_pf_mean'):+.4f}) and do not reduce early stops "
                f"({live.get('delta_early_stopout_pp'):+.2f}pp). "
                "sma50_extension_high dominates would-filter; prefer breakout-buffer-only experiments."
            ),
            "live_shadow": live,
            "best_breakout_buffer_only": best_buffer,
        }
    if live:
        if (
            (live.get("delta_overlap_pf_mean") or 0) >= 0.05
            and live.get("retention_pct", 0) >= 50
            and (live.get("delta_early_stopout_pp") or 0) <= -3
        ):
            return {
                "action": "shadow_entry_timing_promising",
                "reason": "Live entry-timing shadow improves overlap PF with acceptable retention and early-stop reduction.",
                "live_shadow": live,
            }
        return {
            "action": "keep_entry_timing_shadow_only",
            "reason": (
                "Live entry-timing shadow does not yet meet promotion criteria "
                "(overlap PF +0.05, retention >=50%, early stops -3pp). Monitor live scan counters."
            ),
            "live_shadow": live,
        }
    best_oracle = oracle_rows[0] if oracle_rows else None
    return {
        "action": "fix_entry_timing_not_rank_filter",
        "reason": (
            "Oracle first-5d drawdown filters show headroom, but live scan-time shadow replay "
            "was insufficient — prioritize entry cushion/breakout-buffer experiments in shadow."
        ),
        "best_oracle": best_oracle,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Entry-timing shadow counterfactual")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--max-trades", type=int, default=150, help="yfinance replay cap (0 = all)")
    parser.add_argument("--all", action="store_true", help="Replay all trades via yfinance")
    parser.add_argument(
        "--reuse-cache",
        action="store_true",
        help="Skip yfinance replay; load validation_artifacts/entry_timing_replay_cache_<run_id>.json",
    )
    parser.add_argument(
        "--sweep-only",
        action="store_true",
        help="Only run threshold sweep on cached replay metrics",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.sweep_only:
        replay_df = _load_replay_cache(args.run_id)
        if replay_df.empty:
            print("Replay cache missing; run full replay first.")
            return 1
        replay_df = _apply_shadow_flags(replay_df, skill_dir=SKILL_DIR)
        reason_counts: dict[str, int] = {}
        for reasons in replay_df.get("entry_shadow_reasons", []):
            for reason in reasons or []:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        sweep_rows = _sweep_shadow_thresholds(replay_df)
        buffer_only_rows = _sweep_breakout_buffer_only(replay_df)
        threshold_rec = _pick_threshold_recommendation(sweep_rows)
        live_rows = _scenario_from_replay(replay_df)
        recommendation = _pick_recommendation([], live_rows, threshold_rec, buffer_only_rows)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": args.run_id,
            "live_shadow_replay": {
                "replayed_trades": len(replay_df),
                "source": "cache",
                "would_filter_any": int(replay_df["entry_shadow_would_filter"].sum()),
                "reason_counts": reason_counts,
            },
            "live_shadow_scenarios": live_rows,
            "threshold_sweep_top": sweep_rows[:10],
            "breakout_buffer_only_sweep": buffer_only_rows[:8],
            "threshold_recommendation": threshold_rec,
            "recommendation": recommendation,
        }
        out_json = ART / f"entry_timing_shadow_counterfactual_{args.run_id}.json"
        out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote {out_json}")
        print(f"Recommendation: {recommendation.get('action')} — {recommendation.get('reason')}")
        return 0

    df = _load_trade_frame(args.run_id)
    df.attrs["run_id"] = args.run_id
    hold_map = _hold_days_map(args.run_id)
    df = _merge_hold_days(df, hold_map)
    df = _merge_trade_metadata(df, args.run_id)
    df["entry_iso"] = pd.to_datetime(df["entry_date"]).dt.strftime("%Y-%m-%d")
    df["cohort"] = df.apply(_label_cohort, axis=1)
    trade_by_key = {
        (t.era, str(t.ticker or "").upper(), t.entry_date.strftime("%Y-%m-%d")): t for t in load_trades(args.run_id)
    }
    df["dd5"] = [
        _first_n_drawdown(trade_by_key.get((str(r.era), r.ticker, r.entry_iso)).ohlc_path or [], 5)
        if trade_by_key.get((str(r.era), r.ticker, r.entry_iso))
        else None
        for r in df.itertuples(index=False)
    ]

    baseline = _cohort_stats(df)
    oracle_rows = _oracle_scenarios(df)

    if args.reuse_cache:
        replay_df = _load_replay_cache(args.run_id)
        replay_meta = {"replayed_trades": len(replay_df), "source": "cache"}
        if replay_df.empty:
            print("Replay cache missing or empty; run without --reuse-cache first.")
            return 1
        replay_df = _apply_shadow_flags(replay_df, skill_dir=SKILL_DIR)
    else:
        max_trades = None if args.all else (None if args.max_trades <= 0 else args.max_trades)
        replay_df, replay_meta = _replay_shadow_rules(df, skill_dir=SKILL_DIR, max_trades=max_trades)
        replay_meta["source"] = "yfinance"
        _save_replay_cache(replay_df, args.run_id)
        replay_df = _apply_shadow_flags(replay_df, skill_dir=SKILL_DIR)

    reason_counts: dict[str, int] = {}
    if not replay_df.empty:
        for reasons in replay_df.get("entry_shadow_reasons", []):
            for reason in reasons or []:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
    replay_meta["would_filter_any"] = int(replay_df["entry_shadow_would_filter"].sum()) if not replay_df.empty else 0
    replay_meta["reason_counts"] = reason_counts

    sweep_rows = _sweep_shadow_thresholds(replay_df)
    buffer_only_rows = _sweep_breakout_buffer_only(replay_df)
    threshold_rec = _pick_threshold_recommendation(sweep_rows)
    live_rows = _scenario_from_replay(replay_df) if not replay_df.empty else []
    recommendation = _pick_recommendation(oracle_rows, live_rows, threshold_rec, buffer_only_rows)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "baseline": baseline,
        "oracle_scenarios": oracle_rows,
        "live_shadow_replay": replay_meta,
        "live_shadow_scenarios": live_rows,
        "threshold_sweep_top": sweep_rows[:10],
        "breakout_buffer_only_sweep": buffer_only_rows[:8],
        "threshold_recommendation": threshold_rec,
        "recommendation": recommendation,
    }
    out_json = ART / f"entry_timing_shadow_counterfactual_{args.run_id}.json"
    out_md = ART / f"entry_timing_shadow_counterfactual_{args.run_id}.md"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        f"# Entry-timing shadow counterfactual — `{args.run_id}`",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Baseline",
        f"- Trades: {baseline.get('n')} | early stop-outs: {baseline.get('early_stopout_pct')}%",
        "",
        "## Live shadow replay",
        f"- Replayed: {replay_meta.get('replayed_trades')} | would-filter: {replay_meta.get('would_filter_any')}",
        f"- Reasons: {replay_meta.get('reason_counts')}",
        "",
        "## Threshold sweep (top 5 by early-stop reduction)",
    ]
    for row in sweep_rows[:5]:
        lines.append(
            f"- buf>={row['min_breakout_buffer_pct']} sma50 [{row['min_pct_above_sma50']},{row['max_pct_above_sma50']}] "
            f"retain={row['retention_pct']}% d_early={row.get('delta_early_stopout_pp')} d_pf={row.get('delta_overlap_pf_mean')}"
        )
    lines.extend(
        [
        "",
        "## Recommendation",
        f"- **{recommendation.get('action')}** — {recommendation.get('reason')}",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    print(f"Recommendation: {recommendation.get('action')} — {recommendation.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
