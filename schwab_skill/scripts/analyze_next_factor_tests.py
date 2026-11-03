#!/usr/bin/env python3
"""Three tests the concrete books still had not run.

1. Exit replay on frozen ``control_legacy_aug`` paths: grace only on the most
   extended quintile, a wider initial stop only on that quintile, and removal
   of the 0.9 trail tightening that fires when the prior 20-day return exceeds
   6 percent.
2. ``distribution_score`` as one p76 arm against inverting ``pts_52w``.
3. PEAD on its own: walk-forward drop of the most extended quintile, plus
   earnings-surprise IC from the PIT earnings cache when coverage allows.

No live sort change. No new multi-era backtest.

Usage (from schwab_skill/):
  python scripts/analyze_next_factor_tests.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.counterfactual import _era_pf_mean_worst, select_by_percentile  # noqa: E402
from scripts.analyze_scan_confidence import screen_feature  # noqa: E402
from scripts.compare_confidence_across_books import _load_book  # noqa: E402
from scripts.replay_exit_overlay import (  # noqa: E402
    ExitProfile,
    _path_to_df,
    _replay_exit_on_df,
)
from stage_analysis import add_indicators  # noqa: E402

ART = SKILL_DIR / "validation_artifacts" / "scan_confidence"
CHUNKS = SKILL_DIR / "validation_artifacts" / "multi_era_chunks"
LABELED = (
    SKILL_DIR
    / "validation_artifacts"
    / "prob_rank_control_confirm"
    / "scored_features_control_legacy_aug_labeled.parquet"
)
EARNINGS_CACHE = SKILL_DIR / ".earnings_cache.json"
CONTROL = "control_legacy_aug"
PEAD = "pead_primary_aug_fixed_full_c40"
PF_MEAN_FLOOR = 1.20
WORST_FLOOR = 1.00
MIN_LIFT = 0.03
TIGHTEN_TRIGGER = 0.06
TIGHTEN_FACTOR = 0.9
STOP_MAX = 0.12
EXTENSION_QUANTILE = 0.80
PEAD_LOOKBACK_DAYS = 20

LEGACY = ExitProfile("baseline_legacy", hold_days=20, min_hold_before_trail=0, defer_soft_exits=False)
GRACE = ExitProfile("exit_grace_t15_h40", hold_days=40, min_hold_before_trail=15, defer_soft_exits=True)


def undo_trend_tightening(stop_pct: float, ret_20d_prev: float | None) -> float:
    """Reverse the 0.9 multiplier applied when the prior 20-day return exceeds 6%.

    Names that did not trip the rule keep their stored stop. The result stays
    inside the adaptive-stop max (12%).
    """
    stop = float(stop_pct)
    if ret_20d_prev is None or ret_20d_prev != ret_20d_prev or float(ret_20d_prev) <= TIGHTEN_TRIGGER:
        return stop
    return min(STOP_MAX, stop / TIGHTEN_FACTOR)


def widen_initial_stop(stop_pct: float) -> float:
    """Widen an extended-quintile stop by reversing a 0.9 tighten, capped at 12%."""
    return min(STOP_MAX, float(stop_pct) / TIGHTEN_FACTOR)


def extended_mask(df: pd.DataFrame, col: str = "close_vs_sma200_pct") -> pd.Series:
    """Walk-forward top quintile. The holdout era does not set its own cut."""
    values = pd.to_numeric(df[col], errors="coerce")
    out = pd.Series(False, index=df.index)
    for era, _grp in df.groupby("era"):
        train = values[df["era"].astype(str) != str(era)].dropna()
        if train.empty:
            continue
        cut = float(train.quantile(EXTENSION_QUANTILE))
        holdout = df["era"].astype(str) == str(era)
        out.loc[holdout] = values.loc[holdout] >= cut
    return out


def _path_censor_stats(frame: pd.DataFrame) -> dict[str, Any]:
    """Share of paths whose last bar is the original exit. Those cannot test a longer hold."""
    ends = 0
    n = 0
    for row in frame.itertuples(index=False):
        path = row.ohlc_path or []
        if not path:
            continue
        n += 1
        try:
            last = pd.Timestamp(path[-1].get("date")).normalize()
            exit_dt = pd.Timestamp(row.exit_date).normalize()
        except Exception:
            continue
        if last == exit_dt:
            ends += 1
    return {
        "n": n,
        "n_path_ends_on_exit": ends,
        "pct_path_ends_on_exit": (ends / n) if n else None,
    }


def _early_stop_pct(df: pd.DataFrame) -> float | None:
    if df.empty or "hold_days" not in df.columns:
        return None
    hold = pd.to_numeric(df["hold_days"], errors="coerce")
    net = pd.to_numeric(df["net_return"], errors="coerce")
    return round(100 * float(((hold <= 20) & (net < 0)).mean()), 2)


def _arm_stats(df: pd.DataFrame) -> dict[str, Any]:
    pf_mean, worst = _era_pf_mean_worst(df)
    return {
        "n": int(len(df)),
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "early_stop_pct": _early_stop_pct(df),
        "gates": {
            "pf_mean_ok": bool(pf_mean is not None and pf_mean >= PF_MEAN_FLOOR),
            "worst_era_ok": bool(worst is not None and worst >= WORST_FLOOR),
        },
    }


def _load_paths(run_id: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    base = CHUNKS / run_id
    for era_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for chunk in sorted(era_dir.glob("chunk_*.json")):
            if chunk.name.endswith("_tickers.json"):
                continue
            try:
                payload = json.loads(chunk.read_text(encoding="utf-8"))
            except Exception:
                continue
            for raw in payload.get("trades") or []:
                path = raw.get("ohlc_path") or []
                if len(path) < 2 or raw.get("entry_price") is None:
                    continue
                try:
                    entry = pd.Timestamp(raw.get("entry_date") or "")
                except Exception:
                    continue
                if pd.isna(entry):
                    continue
                rows.append(
                    {
                        "era": era_dir.name,
                        "ticker": str(raw.get("ticker") or "").upper(),
                        "entry_date": entry.normalize(),
                        "close_vs_sma200_pct": raw.get("close_vs_sma200_pct"),
                        "stop_pct": raw.get("stop_pct"),
                        "entry_price": raw.get("entry_price"),
                        "exit_date": raw.get("exit_date"),
                        "original_net": raw.get("net_return"),
                        "original_ret": raw.get("return"),
                        "ohlc_path": path,
                    }
                )
    if not rows:
        raise FileNotFoundError(run_id)
    return pd.DataFrame(rows)


def _attach_ret20(df: pd.DataFrame) -> pd.DataFrame:
    if not LABELED.is_file():
        df["ret_20d_prev"] = pd.NA
        return df
    panel = pd.read_parquet(LABELED, columns=["asof_date", "ticker", "ret_20d_prev"])
    panel["entry_date"] = pd.to_datetime(panel["asof_date"], errors="coerce").dt.normalize()
    panel["ticker"] = panel["ticker"].astype(str).str.upper()
    slim = panel[["ticker", "entry_date", "ret_20d_prev"]].drop_duplicates(
        subset=["ticker", "entry_date"], keep="last"
    )
    out = df.copy()
    out["entry_date"] = pd.to_datetime(out["entry_date"], errors="coerce").dt.normalize()
    return out.merge(slim, on=["ticker", "entry_date"], how="left")


def _replay_one(
    work: pd.DataFrame,
    profile: ExitProfile,
    stop_pct: float,
    original_ret: float,
    original_net: float,
) -> dict[str, Any] | None:
    if len(work) < 2:
        return None
    try:
        exit_px, exit_ts, reason = _replay_exit_on_df(
            work, profile=profile, stop_pct=stop_pct, skill_dir=SKILL_DIR
        )
    except Exception:
        return None
    entry_px = float(work["close"].iloc[0])
    gross = (float(exit_px) - entry_px) / entry_px if entry_px > 0 else 0.0
    if original_ret:
        net = gross - (float(original_ret) - float(original_net))
    else:
        net = gross
    hold = max(0, int((pd.Timestamp(exit_ts).normalize() - pd.Timestamp(work.index[0]).normalize()).days))
    return {"net_return": net, "hold_days": hold, "exit_reason": reason, "path_bars": len(work)}


def run_exit_replay(limit: int = 0) -> dict[str, Any]:
    frame = _attach_ret20(_load_paths(CONTROL))
    if limit > 0:
        frame = frame.head(limit).copy()
    frame = frame.reset_index(drop=True)
    extended = extended_mask(frame)
    ret20 = pd.to_numeric(frame["ret_20d_prev"], errors="coerce")
    stored = pd.to_numeric(frame["stop_pct"], errors="coerce").fillna(0.07).clip(lower=0.01)
    undone = [
        undo_trend_tightening(float(stop), None if pd.isna(ret) else float(ret))
        for stop, ret in zip(stored, ret20, strict=True)
    ]
    frame["stop_stored"] = stored
    frame["stop_undone"] = undone
    frame["extended"] = extended.to_numpy()

    specs = {
        "legacy_stored_stop": lambda row: (LEGACY, float(row.stop_stored)),
        "grace_all": lambda row: (GRACE, float(row.stop_stored)),
        "grace_extended_only": lambda row: (GRACE if row.extended else LEGACY, float(row.stop_stored)),
        "wider_stop_extended_only": lambda row: (
            LEGACY,
            widen_initial_stop(float(row.stop_stored)) if row.extended else float(row.stop_stored),
        ),
        "undo_tighten_when_20d_up_6pct": lambda row: (LEGACY, float(row.stop_undone)),
    }
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in specs}
    path_bars: list[int] = []
    for i, row in enumerate(frame.itertuples(index=False), start=1):
        raw = _path_to_df(row.ohlc_path)
        if len(raw) < 2:
            continue
        try:
            work = add_indicators(raw)
        except Exception:
            continue
        for name, spec in specs.items():
            profile, stop = spec(row)
            played = _replay_one(
                work,
                profile,
                stop,
                float(row.original_ret or 0.0),
                float(row.original_net or 0.0),
            )
            if played is None:
                continue
            if name == "legacy_stored_stop":
                path_bars.append(int(played["path_bars"]))
            buckets[name].append(
                {
                    "era": row.era,
                    "net_return": played["net_return"],
                    "hold_days": played["hold_days"],
                    "exit_reason": played["exit_reason"],
                }
            )
        if i % 2000 == 0:
            print(f"  replayed {i}/{len(frame)}", flush=True)
    arms = {name: _arm_stats(pd.DataFrame(rows)) for name, rows in buckets.items()}
    tightened = int((ret20 > TIGHTEN_TRIGGER).sum())
    censor = _path_censor_stats(frame)
    return {
        "run_id": CONTROL,
        "n_with_path": int(len(frame)),
        "ret_20d_coverage": round(float(ret20.notna().mean()), 4),
        "n_tightened_by_rule": tightened,
        "n_extended": int(extended.sum()),
        "median_path_bars": float(pd.Series(path_bars).median()) if path_bars else None,
        "path_censor": censor,
        "interpretable": False,
        "arms": arms,
        "note": (
            "Not an exit result. Stored paths end on the original exit date "
            f"({censor['pct_path_ends_on_exit']:.1%} of trades), median "
            f"{float(pd.Series(path_bars).median()) if path_bars else 0:.0f} bars, "
            "with no bars after that exit and no pre-entry history. A wider stop "
            "or a longer hold cannot be seen. grace_all landing near the stored "
            "book is that censoring: deferred soft exits ride to the last stored "
            "bar. The legacy replay exits earlier because SMA50 and VCP have no "
            "history before entry. Do not promote any arm in this table."
        ),
    }


def run_distribution_arm() -> dict[str, Any]:
    df = _load_book(CONTROL)
    if not LABELED.is_file():
        return {"status": "labeled_panel_missing"}
    panel = pd.read_parquet(LABELED, columns=["asof_date", "ticker", "distribution_score"])
    panel["entry_date"] = pd.to_datetime(panel["asof_date"], errors="coerce").dt.normalize()
    panel["ticker"] = panel["ticker"].astype(str).str.upper()
    slim = panel[["ticker", "entry_date", "distribution_score"]].drop_duplicates(
        subset=["ticker", "entry_date"], keep="last"
    )
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce").dt.normalize()
    df["ticker"] = df["ticker"].astype(str).str.upper()
    merged = df.merge(slim, on=["ticker", "entry_date"], how="left")
    hold = pd.to_numeric(merged["hold_days"], errors="coerce")
    net = pd.to_numeric(merged["net_return"], errors="coerce")
    merged["is_early_stop"] = (hold <= 20) & (net < 0)
    merged["is_winner_21_40"] = hold.between(21, 40) & (net > 0)
    merged["survival_label"] = pd.Series(
        [1.0 if w else (0.0 if e else float("nan")) for w, e in zip(merged["is_winner_21_40"], merged["is_early_stop"], strict=True)],
        index=merged.index,
    )
    screened = screen_feature(merged, "distribution_score")
    work = merged.copy()
    work["_dist"] = pd.to_numeric(work["distribution_score"], errors="coerce")
    work["_inv_pts"] = -pd.to_numeric(work["pts_52w"], errors="coerce")
    dist_arm = _arm_stats(select_by_percentile(work, score_col="_dist", min_percentile=76))
    pts_arm = _arm_stats(select_by_percentile(work, score_col="_inv_pts", min_percentile=76))
    lift = None
    if dist_arm["pf_mean"] is not None and pts_arm["pf_mean"] is not None:
        lift = round(dist_arm["pf_mean"] - pts_arm["pf_mean"], 4)
    return {
        "status": "scored",
        "coverage": screened["coverage"],
        "ic_net_return": screened["ic_net_return"],
        "sign_consistent_eras": screened["sign_consistent_eras"],
        "n_eras_with_ic": screened["n_eras_with_ic"],
        "auc_survival": screened["auc_survival"],
        "distribution_p76": dist_arm,
        "inv_pts52w_p76": pts_arm,
        "pf_mean_lift_vs_inv_pts52w": lift,
        "beats_single_variable_bar": bool(lift is not None and lift >= MIN_LIFT),
    }


def _surprise_asof(cache: dict[str, Any], ticker: str, entry: pd.Timestamp) -> float | None:
    blob = cache.get(ticker) or cache.get(ticker.upper())
    if not isinstance(blob, dict):
        return None
    rows = blob.get("rows") or []
    start = entry.normalize() - pd.Timedelta(days=PEAD_LOOKBACK_DAYS)
    best_date: pd.Timestamp | None = None
    best: float | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            day = pd.Timestamp(str(row.get("date") or ""))
        except Exception:
            continue
        if pd.isna(day) or day.normalize() > entry.normalize() or day.normalize() < start:
            continue
        actual = row.get("actual_eps")
        estimate = row.get("estimate_eps")
        try:
            actual_f = float(actual)
            est_f = float(estimate)
        except (TypeError, ValueError):
            continue
        if est_f == 0 or actual_f != actual_f or est_f != est_f:
            continue
        surprise = (actual_f - est_f) / max(abs(est_f), 0.01)
        if best_date is None or day > best_date:
            best_date = day
            best = surprise
    return best


def run_pead_tests() -> dict[str, Any]:
    df = _load_book(PEAD).reset_index(drop=True)
    extended = extended_mask(df)
    kept = df.loc[~extended].copy()
    dropped = df.loc[extended].copy()
    extension_arm = {
        "rule": "drop walk-forward top quintile of close_vs_sma200_pct",
        "kept": _arm_stats(kept),
        "dropped_extended": _arm_stats(dropped),
        "baseline": _arm_stats(df),
    }
    surprise_block: dict[str, Any]
    if not EARNINGS_CACHE.is_file():
        surprise_block = {"status": "cache_missing", "coverage": 0.0}
    else:
        cache = json.loads(EARNINGS_CACHE.read_text(encoding="utf-8"))
        surprises = [
            _surprise_asof(cache, str(row.ticker), pd.Timestamp(row.entry_date))
            for row in df.itertuples(index=False)
        ]
        df["pead_surprise_pct"] = surprises
        coverage = float(pd.to_numeric(df["pead_surprise_pct"], errors="coerce").notna().mean())
        if coverage < 0.70:
            surprise_block = {
                "status": "coverage_too_thin",
                "coverage": round(coverage, 4),
                "lookback_days": PEAD_LOOKBACK_DAYS,
                "note": "Earnings cache does not cover enough PEAD entries as-of. Surprise was not scored.",
            }
        else:
            screened = screen_feature(df, "pead_surprise_pct")
            surprise_block = {
                "status": "scored",
                "coverage": round(coverage, 4),
                "lookback_days": PEAD_LOOKBACK_DAYS,
                "ic_net_return": screened["ic_net_return"],
                "sign_consistent_eras": screened["sign_consistent_eras"],
                "n_eras_with_ic": screened["n_eras_with_ic"],
                "auc_survival": screened["auc_survival"],
            }
    return {"run_id": PEAD, "extension": extension_arm, "surprise": surprise_block}


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    exits = payload["exit_replay"]["arms"]
    dist = payload["distribution"]
    pead = payload["pead"]
    lines = [
        "# Next factor tests",
        "",
        f"Generated {payload['created_at_utc']}",
        "",
        "Entries stay frozen. Nothing here changes `SCAN_LIVE_SORT_KEY`.",
        "",
        "## 1. Trail on extended Stage 2 names",
        "",
        f"Paths replayed: {payload['exit_replay']['n_with_path']}. "
        f"Median bars on the stored path: {payload['exit_replay']['median_path_bars']}. "
        f"Prior-20-day return coverage: {payload['exit_replay']['ret_20d_coverage']}. "
        f"Names with a 20-day return above 6%: {payload['exit_replay']['n_tightened_by_rule']}.",
        "",
        "| Arm | n | PF mean | Worst era | Early-stop % | Clears 1.20 / 1.00 |",
        "|---|---|---|---|---|---|",
    ]
    for name, arm in exits.items():
        gates = arm.get("gates") or {}
        ok = "yes" if gates.get("pf_mean_ok") and gates.get("worst_era_ok") else "no"
        lines.append(
            f"| `{name}` | {arm.get('n')} | {_fmt(arm.get('pf_mean'))} | "
            f"{_fmt(arm.get('worst_era_pf'))} | {arm.get('early_stop_pct')} | {ok} |"
        )
    lines.extend(
        [
            "",
            payload["exit_replay"]["note"],
            "",
            "The table is what those censored paths produce. It is not a tradable exit change.",
            "",
            "## 2. Down-day volume as a rank",
            "",
            f"Status: {dist.get('status')}. Coverage {dist.get('coverage')}. "
            f"IC {dist.get('ic_net_return')} "
            f"({dist.get('sign_consistent_eras')}/{dist.get('n_eras_with_ic')} eras).",
            "",
            f"- `distribution_score` p76: PF {_fmt((dist.get('distribution_p76') or {}).get('pf_mean'))} "
            f"/ worst {_fmt((dist.get('distribution_p76') or {}).get('worst_era_pf'))}",
            f"- invert `pts_52w` p76: PF {_fmt((dist.get('inv_pts52w_p76') or {}).get('pf_mean'))} "
            f"/ worst {_fmt((dist.get('inv_pts52w_p76') or {}).get('worst_era_pf'))}",
            f"- Lift vs invert pts_52w: {_fmt(dist.get('pf_mean_lift_vs_inv_pts52w'))}. "
            f"Clears the +{MIN_LIFT} bar: {dist.get('beats_single_variable_bar')}.",
            "A keep also needs worst-era PF at least 1.00. This arm misses that floor.",
            "",
            "## 3. PEAD on its own",
            "",
        ]
    )
    ext = pead["extension"]
    lines.append(
        f"Dropping the walk-forward most-extended quintile keeps "
        f"{ext['kept']['n']} trades at PF {_fmt(ext['kept']['pf_mean'])} / "
        f"worst {_fmt(ext['kept']['worst_era_pf'])}. "
        f"Baseline is {_fmt(ext['baseline']['pf_mean'])} / {_fmt(ext['baseline']['worst_era_pf'])}. "
        f"The dropped tail is {_fmt(ext['dropped_extended']['pf_mean'])} / "
        f"{_fmt(ext['dropped_extended']['worst_era_pf'])}. "
        "That is a PEAD-only shadow filter on frozen trades. It does not change the Stage 2 live sort."
    )
    surprise = pead["surprise"]
    lines.extend(["", f"Surprise: {surprise.get('status')}. Coverage {surprise.get('coverage')}."])
    if surprise.get("status") == "scored":
        lines.append(
            f"Surprise IC {surprise.get('ic_net_return')} "
            f"({surprise.get('sign_consistent_eras')}/{surprise.get('n_eras_with_ic')} eras), "
            f"lookback {surprise.get('lookback_days')} days."
        )
    elif surprise.get("note"):
        lines.append(str(surprise["note"]))
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Replay at most this many Stage 2 paths (0=all).")
    args = parser.parse_args(argv)
    print("Exit replay ...")
    exits = run_exit_replay(limit=args.limit)
    for name, arm in exits["arms"].items():
        print(f"  {name:32s} pf={arm['pf_mean']} worst={arm['worst_era_pf']} early={arm['early_stop_pct']}")
    print("Distribution arm ...")
    dist = run_distribution_arm()
    print(
        f"  lift={dist.get('pf_mean_lift_vs_inv_pts52w')} "
        f"beats={dist.get('beats_single_variable_bar')} ic={dist.get('ic_net_return')}"
    )
    print("PEAD ...")
    pead = run_pead_tests()
    print(
        "  extension kept",
        pead["extension"]["kept"]["pf_mean"],
        pead["extension"]["kept"]["worst_era_pf"],
        "surprise",
        pead["surprise"].get("status"),
        pead["surprise"].get("coverage"),
    )
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "exit_replay": exits,
        "distribution": dist,
        "pead": pead,
        "promote": False,
    }
    ART.mkdir(parents=True, exist_ok=True)
    json_path = ART / "next_factor_tests.json"
    md_path = ART / "next_factor_tests.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    _write_report(md_path, payload)
    print(f"Wrote {md_path.relative_to(SKILL_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
