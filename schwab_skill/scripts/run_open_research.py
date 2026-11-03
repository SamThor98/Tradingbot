#!/usr/bin/env python3
"""Run the open P0 research measurements. Does not change the live sort.

1. Walk-forward inverted ``pts_52w`` versus taking every trade and versus
   top-5 by ``signal_score``, on the frozen control book and the capped book.
2. Exit replay on control trades whose local price cache has a bar after the
   stored exit. Grace and a wider stop are scored only on that subset.
3. PEAD earnings-surprise coverage inside 20 days. When a provider is
   configured, missing tickers are warmed and coverage is scored again.

Usage (from schwab_skill/):
  python scripts/run_open_research.py
  python scripts/run_open_research.py --skip-earnings-warm
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

from research.counterfactual import select_by_percentile, select_top_n_by_day  # noqa: E402
from research.open_research import arm_stats, sort_key_verdict, walk_forward_percentile  # noqa: E402
from scripts.analyze_next_factor_tests import (  # noqa: E402
    PEAD_LOOKBACK_DAYS,
    _surprise_asof,
    extended_mask,
)
from scripts.replay_exit_overlay import (  # noqa: E402
    PROFILES,
    _replay_exit_on_df,
)

CHUNKS = SKILL_DIR / "validation_artifacts" / "multi_era_chunks"
PRICE_CACHE = SKILL_DIR / "validation_artifacts" / "fast_slope" / "price_cache"
EARNINGS_CACHE = SKILL_DIR / ".earnings_cache.json"
OUT_JSON = SKILL_DIR / "validation_artifacts" / "open_research_sort_exit.json"
OUT_MD = SKILL_DIR / "validation_artifacts" / "open_research_sort_exit.md"
CONTROL = "control_legacy_aug"
CAPPED = "stage2_pts52w_cap37"
PEAD = "pead_primary_aug_fixed_full_c40"
SCORE_KEYS = (
    "exit_date",
    "signal_score",
    "pts_52w",
    "rank_score_v2",
    "close_vs_sma200_pct",
    "stop_pct",
    "entry_price",
    "return",
    "net_return",
)
POST_EXIT_PAD = pd.Timedelta(days=90)
LOOKBACK = pd.Timedelta(days=280)


def _load_scores(run_id: str) -> pd.DataFrame:
    base = CHUNKS / run_id
    rows: list[dict[str, Any]] = []
    for era_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for chunk in sorted(era_dir.glob("chunk_*.json")):
            if chunk.name.endswith("_tickers.json"):
                continue
            try:
                payload = json.loads(chunk.read_text(encoding="utf-8"))
            except Exception:
                continue
            for raw in payload.get("trades") or []:
                try:
                    entry = pd.Timestamp(raw.get("entry_date") or "")
                except Exception:
                    continue
                if pd.isna(entry):
                    continue
                row: dict[str, Any] = {
                    "era": era_dir.name,
                    "ticker": str(raw.get("ticker") or "").upper(),
                    "entry_date": entry.normalize(),
                }
                for key in SCORE_KEYS:
                    if raw.get(key) is not None:
                        row[key] = raw.get(key)
                rows.append(row)
    if not rows:
        raise FileNotFoundError(run_id)
    df = pd.DataFrame(rows)
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce").dt.normalize()
    df["exit_date"] = pd.to_datetime(df.get("exit_date"), errors="coerce").dt.normalize()
    df["net_return"] = pd.to_numeric(df["net_return"], errors="coerce")
    return df.dropna(subset=["net_return"])


def _sort_book(run_id: str) -> dict[str, Any]:
    df = _load_scores(run_id)
    work = df.copy()
    work["inv_pts52w"] = -pd.to_numeric(work["pts_52w"], errors="coerce")
    work["signal_score"] = pd.to_numeric(work["signal_score"], errors="coerce")
    arms = {
        "take_every_trade": arm_stats(work),
        "top5_signal_score": arm_stats(select_top_n_by_day(work, score_col="signal_score", top_n=5)),
        "top5_inv_pts52w": arm_stats(select_top_n_by_day(work, score_col="inv_pts52w", top_n=5)),
        "insample_p76_inv_pts52w": arm_stats(
            select_by_percentile(work, score_col="inv_pts52w", min_percentile=76)
        ),
        "wf_p76_inv_pts52w": arm_stats(
            walk_forward_percentile(work, score_col="inv_pts52w", min_percentile=76)
        ),
        "wf_p76_signal_score": arm_stats(
            walk_forward_percentile(work, score_col="signal_score", min_percentile=76)
        ),
    }
    return {
        "run_id": run_id,
        "n": int(len(work)),
        "arms": arms,
        "verdict": sort_key_verdict(arms),
    }


def _cache_frame(ticker: str) -> pd.DataFrame | None:
    path = PRICE_CACHE / f"{ticker}.parquet"
    if not path.is_file():
        return None
    try:
        frame = pd.read_parquet(path)
    except Exception:
        return None
    if frame is None or frame.empty:
        return None
    if "date" in frame.columns:
        frame = frame.set_index("date")
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(frame.columns):
        return None
    return frame


def _replay_net(
    window: pd.DataFrame, profile_name: str, stop_pct: float, original_ret: float, original_net: float
) -> tuple[float, str] | None:
    if len(window) < 2:
        return None
    profile = PROFILES[profile_name]
    try:
        exit_px, _exit_ts, reason = _replay_exit_on_df(
            window, profile=profile, stop_pct=float(stop_pct), skill_dir=SKILL_DIR
        )
    except Exception:
        return None
    entry_px = float(window["close"].iloc[0])
    if entry_px <= 0:
        return None
    gross = (float(exit_px) - entry_px) / entry_px
    net = gross - (float(original_ret) - float(original_net)) if original_ret else gross
    return net, str(reason or "")


def _exit_replay(df: pd.DataFrame) -> dict[str, Any]:
    from stage_analysis import add_indicators

    prepared: dict[str, pd.DataFrame] = {}
    stored_rows: list[dict[str, Any]] = []
    grace_rows: list[dict[str, Any]] = []
    legacy_rows: list[dict[str, Any]] = []
    wide_rows: list[dict[str, Any]] = []
    n = 0
    n_cache = 0
    n_after = 0
    extended = extended_mask(df)
    df = df.copy()
    # itertuples renames columns that start with an underscore, so keep a plain name.
    df["is_extended"] = extended.to_numpy()
    for ticker, grp in df.groupby("ticker"):
        raw = _cache_frame(str(ticker))
        if raw is None:
            continue
        n_cache += int(len(grp))
        try:
            indicated = add_indicators(raw)
        except Exception:
            continue
        prepared[str(ticker)] = indicated
        for row in grp.itertuples(index=False):
            n += 1
            entry = pd.Timestamp(row.entry_date).normalize()
            exit_dt = pd.Timestamp(row.exit_date).normalize() if pd.notna(row.exit_date) else entry
            window_src = indicated.loc[(indicated.index >= entry - LOOKBACK) & (indicated.index <= entry + POST_EXIT_PAD)]
            if window_src.empty:
                continue
            entry_pos = window_src.index.searchsorted(entry)
            if entry_pos >= len(window_src):
                continue
            window = window_src.iloc[entry_pos:]
            if window.empty or window.index.max() <= exit_dt:
                continue
            stop = getattr(row, "stop_pct", None)
            try:
                stop_f = float(stop)
            except (TypeError, ValueError):
                continue
            if stop_f != stop_f or stop_f <= 0:
                continue
            original_ret = float(getattr(row, "return", 0.0) or 0.0)
            original_net = float(row.net_return)
            n_after += 1
            base = {"era": row.era, "net_return": original_net}
            stored_rows.append(base)
            grace = _replay_net(window, "exit_grace_t15_h40", stop_f, original_ret, original_net)
            legacy = _replay_net(window, "baseline_legacy", stop_f, original_ret, original_net)
            if grace is not None:
                grace_rows.append({"era": row.era, "net_return": grace[0], "reason": grace[1]})
            if legacy is not None:
                legacy_rows.append({"era": row.era, "net_return": legacy[0]})
            if bool(getattr(row, "is_extended", False)):
                wide = _replay_net(
                    window, "exit_grace_t15_h40", min(0.12, stop_f / 0.9), original_ret, original_net
                )
                if wide is not None:
                    wide_rows.append({"era": row.era, "net_return": wide[0]})
        if n and n % 2000 == 0:
            print(f"  exit replay scanned {n} control trades, {n_after} with a bar after exit", flush=True)
    share = (n_after / len(df)) if len(df) else 0.0
    reason_counts: dict[str, int] = {}
    for row in grace_rows:
        key = str(row.get("reason") or "unknown")
        reason_counts[key] = reason_counts.get(key, 0) + 1
    return {
        "run_id": CONTROL,
        "n_trades": int(len(df)),
        "n_with_price_cache": n_cache,
        "n_with_bar_after_exit": n_after,
        "share_with_bar_after_exit": round(share, 4),
        "interpretable": bool(n_after >= 500 and share >= 0.5),
        "grace_exit_reasons": reason_counts,
        "arms": {
            "stored_on_interpretable": arm_stats(pd.DataFrame(stored_rows)),
            "replay_legacy_hold20": arm_stats(pd.DataFrame(legacy_rows)),
            "replay_grace_15_40": arm_stats(pd.DataFrame(grace_rows)),
            "replay_grace_wider_stop_extended_only": arm_stats(pd.DataFrame(wide_rows)),
        },
        "note": (
            "Arms use only trades whose cached bars continue past the stored exit. "
            "The wider-stop arm is the walk-forward most-extended quintile only, so its "
            "profit factor is not comparable to the full-book arms. "
            "A share under 50% is not an exit promotion. SCAN_LIVE_SORT_KEY is unchanged."
        ),
    }


def _surprise_coverage(df: pd.DataFrame, cache: dict[str, Any]) -> dict[str, Any]:
    surprises = [
        _surprise_asof(cache, str(row.ticker), pd.Timestamp(row.entry_date))
        for row in df.itertuples(index=False)
    ]
    series = pd.to_numeric(pd.Series(surprises), errors="coerce")
    covered = series.notna()
    missing_tickers = sorted(
        {
            str(t).upper()
            for t in df.loc[~covered, "ticker"].astype(str)
            if str(t).upper() not in cache
        }
    )
    return {
        "n": int(len(df)),
        "coverage_20d": round(float(covered.mean()), 4) if len(df) else 0.0,
        "n_missing_ticker": len(missing_tickers),
        "missing_tickers": missing_tickers,
        "surprises": surprises,
    }


def _pead_surprise(*, warm: bool) -> dict[str, Any]:
    df = _load_scores(PEAD)
    extended = extended_mask(df)
    extension = {
        "rule": "drop walk-forward top quintile of close_vs_sma200_pct",
        "baseline": arm_stats(df),
        "kept": arm_stats(df.loc[~extended]),
        "dropped_extended": arm_stats(df.loc[extended]),
    }
    if not EARNINGS_CACHE.is_file():
        return {"run_id": PEAD, "extension": extension, "surprise": {"status": "cache_missing", "coverage_20d": 0.0}}
    cache = json.loads(EARNINGS_CACHE.read_text(encoding="utf-8"))
    if not isinstance(cache, dict):
        cache = {}
    first = _surprise_coverage(df, cache)
    warmed = None
    if warm and first["n_missing_ticker"] and first["coverage_20d"] < 0.70:
        from earnings_signal import _resolve_pead_provider, warm_earnings_for_tickers

        provider = _resolve_pead_provider(SKILL_DIR)
        if provider and provider != "off":
            print(
                f"  warming earnings for {first['n_missing_ticker']} PEAD tickers via {provider}",
                flush=True,
            )
            warmed = warm_earnings_for_tickers(first["missing_tickers"], SKILL_DIR, force=False, resume=True)
            cache = json.loads(EARNINGS_CACHE.read_text(encoding="utf-8"))
            first = _surprise_coverage(df, cache if isinstance(cache, dict) else {})
        else:
            warmed = {"status": "provider_off"}
    coverage = float(first["coverage_20d"])
    surprise: dict[str, Any] = {
        "status": "scored" if coverage >= 0.70 else "coverage_too_thin",
        "coverage_20d": coverage,
        "lookback_days": PEAD_LOOKBACK_DAYS,
        "n_missing_ticker": first["n_missing_ticker"],
        "warm": warmed,
    }
    if coverage >= 0.70:
        from research.open_research import surprise_vs_return

        scored = surprise_vs_return(df, first["surprises"])
        surprise["ic_net_return"] = scored["ic_net_return"]
        surprise["sign_consistent_eras"] = scored["sign_consistent_eras"]
        surprise["n_eras_with_ic"] = scored["n_eras_with_ic"]
    else:
        surprise["note"] = "Surprise was not scored. Coverage is still under 70% inside 20 days."
    surprise.pop("missing_tickers", None)
    return {"run_id": PEAD, "extension": extension, "surprise": surprise}


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _write_md(payload: dict[str, Any]) -> None:
    lines = [
        "# Open research: sort key, post-exit bars, PEAD surprise",
        "",
        f"Generated {payload['generated_at']}. Live sort key was not changed.",
        "",
        f"Sort-key verdict: `{payload['verdict']}`.",
        "",
        "## Walk-forward selection",
        "",
        "Cutoff for `wf_p76_*` is fit on the other eras. Top-5 is that day's cross-section only.",
        "",
    ]
    for book in payload["sort_walkforward"]:
        lines.append(f"### `{book['run_id']}`")
        lines.append("")
        lines.append("| Arm | n | PF mean | Worst era | Clears 1.20 / 1.00 |")
        lines.append("|---|---:|---:|---:|---|")
        for name, arm in book["arms"].items():
            lines.append(
                f"| {name} | {arm['n']} | {_fmt(arm['pf_mean'])} | {_fmt(arm['worst_era_pf'])} | {arm['clears_floors']} |"
            )
        lines.append("")
        lines.append(f"Book verdict: `{book['verdict']}`.")
        lines.append("")
    exit_block = payload["exit_replay"]
    lines.extend(
        [
            "## Exit replay with bars after the stored exit",
            "",
            f"Trades with a later bar: {exit_block['n_with_bar_after_exit']} / {exit_block['n_trades']} "
            f"({exit_block['share_with_bar_after_exit']}). Interpretable: {exit_block['interpretable']}.",
            "",
            "| Arm | n | PF mean | Worst era |",
            "|---|---:|---:|---:|",
        ]
    )
    for name, arm in exit_block["arms"].items():
        lines.append(f"| {name} | {arm['n']} | {_fmt(arm['pf_mean'])} | {_fmt(arm['worst_era_pf'])} |")
    lines.append("")
    lines.append(exit_block["note"])
    reasons = exit_block.get("grace_exit_reasons") or {}
    if reasons:
        rendered = ", ".join(f"{name} {count}" for name, count in sorted(reasons.items(), key=lambda item: -item[1]))
        lines.append("")
        lines.append(f"Grace exit reasons: {rendered}.")
    lines.append("")
    surprise = payload["pead"]["surprise"]
    ext = payload["pead"]["extension"]
    lines.extend(
        [
            "## PEAD",
            "",
            f"Extension drop kept PF {_fmt(ext['kept']['pf_mean'])} / worst {_fmt(ext['kept']['worst_era_pf'])} "
            f"versus baseline {_fmt(ext['baseline']['pf_mean'])} / {_fmt(ext['baseline']['worst_era_pf'])}.",
            "",
            f"Surprise status: `{surprise.get('status')}`. 20-day coverage: {_fmt(surprise.get('coverage_20d'))}. "
            f"Tickers still missing from the cache: {surprise.get('n_missing_ticker')}.",
            "",
        ]
    )
    if surprise.get("note"):
        lines.append(str(surprise["note"]))
        lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run open P0 research measurements.")
    parser.add_argument(
        "--skip-earnings-warm",
        action="store_true",
        help="Measure surprise coverage without fetching missing tickers.",
    )
    args = parser.parse_args()
    print("walk-forward sort on control and capped books", flush=True)
    books = [_sort_book(CONTROL), _sort_book(CAPPED)]
    print("exit replay from local price cache", flush=True)
    exit_block = _exit_replay(_load_scores(CONTROL))
    print("PEAD extension and surprise coverage", flush=True)
    pead = _pead_surprise(warm=not args.skip_earnings_warm)
    control_verdict = books[0]["verdict"]
    control_arms = books[0]["arms"]
    grace_arm = exit_block["arms"]["replay_grace_15_40"]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": control_verdict,
        "live_sort_unchanged": True,
        "control_take_every_pf": control_arms["take_every_trade"]["pf_mean"],
        "control_top5_signal_pf": control_arms["top5_signal_score"]["pf_mean"],
        "control_top5_inv_pts_pf": control_arms["top5_inv_pts52w"]["pf_mean"],
        "control_wf_inv_pts_pf": control_arms["wf_p76_inv_pts52w"]["pf_mean"],
        "control_wf_inv_pts_worst": control_arms["wf_p76_inv_pts52w"]["worst_era_pf"],
        "exit_share_after_exit": exit_block["share_with_bar_after_exit"],
        "exit_grace_pf": grace_arm["pf_mean"],
        "exit_grace_worst": grace_arm["worst_era_pf"],
        "pead_coverage_20d": pead["surprise"].get("coverage_20d"),
        "sort_walkforward": books,
        "exit_replay": exit_block,
        "pead": pead,
    }
    # Drop ticker lists from the public artifact.
    surprise = payload["pead"]["surprise"]
    surprise.pop("surprises", None)
    surprise.pop("missing_tickers", None)
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    _write_md(payload)
    print(f"wrote {OUT_JSON.name} verdict={control_verdict}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
