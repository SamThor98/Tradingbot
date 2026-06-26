#!/usr/bin/env python3
"""Replay alternate exit rules on augmented multi-era chunks.

Reads trades from ``multi_era_chunks/<run_id>/`` and re-simulates exits with
``backtest._evaluate_position_exit``. Bar data comes from:

* ``schwab`` (default): Schwab pricehistory from entry → hold window
* ``chunk``: stored ``ohlc_path`` only (no network)
* ``yfinance``: yfinance fallback when Schwab is unavailable

Writes ``validation_artifacts/replay_exit_overlay_<run_id>.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from backtest import _evaluate_position_exit, _fetch_history_schwab  # noqa: E402
from schwab_auth import DualSchwabAuth  # noqa: E402
from scripts.phase2_common import (  # noqa: E402
    ARTIFACT_DIR,
    ERA_BOUNDS,
    Trade,
    load_trades,
)
from stage_analysis import add_indicators  # noqa: E402

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExitProfile:
    name: str
    hold_days: int
    min_hold_before_trail: int
    defer_soft_exits: bool


PROFILES: dict[str, ExitProfile] = {
    "baseline_legacy": ExitProfile(
        name="baseline_legacy",
        hold_days=20,
        min_hold_before_trail=0,
        defer_soft_exits=False,
    ),
    "control_legacy_defaults": ExitProfile(
        name="control_legacy_defaults",
        hold_days=40,
        min_hold_before_trail=15,
        defer_soft_exits=True,
    ),
    "exit_grace_t15_h40": ExitProfile(
        name="exit_grace_t15_h40",
        hold_days=40,
        min_hold_before_trail=15,
        defer_soft_exits=True,
    ),
    "exit_grace_t10_h40": ExitProfile(
        name="exit_grace_t10_h40",
        hold_days=40,
        min_hold_before_trail=10,
        defer_soft_exits=True,
    ),
    "exit_grace_t15_h30": ExitProfile(
        name="exit_grace_t15_h30",
        hold_days=30,
        min_hold_before_trail=15,
        defer_soft_exits=True,
    ),
}

# Map replay profile names to phase1 sweep config_ids for reporting.
PROFILE_TO_CONFIG_ID: dict[str, str] = {
    "baseline_legacy": "control_legacy_exits",
    "control_legacy_defaults": "control_legacy",
    "exit_grace_t15_h40": "exit_grace_t15_h40",
    "exit_grace_t10_h40": "exit_grace_t10_h40",
    "exit_grace_t15_h30": "exit_grace_t15_h30",
}


def _path_to_df(path: list[dict[str, Any]]) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for bar in path:
        try:
            ts = pd.Timestamp(str(bar.get("date") or ""))
        except Exception:
            continue
        rows.append(
            {
                "date": ts,
                "open": float(bar.get("open", 0.0) or 0.0),
                "high": float(bar.get("high", 0.0) or 0.0),
                "low": float(bar.get("low", 0.0) or 0.0),
                "close": float(bar.get("close", 0.0) or 0.0),
                "volume": float(bar.get("volume", 0.0) or 0.0),
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("date").sort_index()
    return df[~df.index.duplicated(keep="last")]


def _fetch_bars_yfinance(ticker: str, start: str, end: str) -> pd.DataFrame:
    try:
        from backtest import _fetch_history

        return _fetch_history(ticker.upper(), start, end)
    except Exception as exc:
        LOG.debug("yfinance fetch failed for %s: %s", ticker, exc)
        return pd.DataFrame()


class BarLoader:
    """Cached bar fetcher shared across all replayed trades."""

    def __init__(self, provider: str, skill_dir: Path) -> None:
        self.provider = provider.strip().lower()
        self.skill_dir = skill_dir
        self._cache: dict[tuple[str, str, str], pd.DataFrame] = {}
        self._auth: DualSchwabAuth | None = None
        if self.provider == "schwab":
            os.environ["SCHWAB_ONLY_DATA"] = "true"
            self._auth = DualSchwabAuth(skill_dir=skill_dir, auto_refresh=True)

    def load(
        self,
        trade: Trade,
        *,
        hold_days: int,
    ) -> pd.DataFrame:
        if self.provider == "chunk":
            return _path_to_df(trade.ohlc_path)

        ticker = str(trade.ticker or "").upper().strip()
        if not ticker:
            return _path_to_df(trade.ohlc_path)

        start = pd.Timestamp(trade.entry_date).strftime("%Y-%m-%d")
        end_ts = pd.Timestamp(trade.entry_date) + pd.Timedelta(days=max(hold_days * 2, 90))
        end = end_ts.strftime("%Y-%m-%d")
        key = (ticker, start, end)
        if key not in self._cache:
            if self.provider == "schwab":
                df = _fetch_history_schwab(ticker, start, end, auth=self._auth)
            else:
                df = _fetch_bars_yfinance(ticker, start, end)
            self._cache[key] = df
        df = self._cache[key]
        if df is None or df.empty:
            return _path_to_df(trade.ohlc_path)
        entry_norm = pd.Timestamp(trade.entry_date).normalize()
        sliced = df[df.index >= entry_norm].copy()
        return sliced if not sliced.empty else _path_to_df(trade.ohlc_path)


def _apply_cost_model(
    *,
    entry_price: float,
    exit_price: float,
    original_ret: float,
    original_net: float,
) -> tuple[float, float]:
    gross = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
    if original_ret != 0:
        cost_drag = original_ret - original_net
        net = gross - cost_drag
    else:
        net = gross
    return gross, net


def _replay_exit_on_df(
    df: pd.DataFrame,
    *,
    profile: ExitProfile,
    stop_pct: float,
    skill_dir: Path,
) -> tuple[float, pd.Timestamp, str]:
    work = add_indicators(df.copy())
    if work.empty:
        raise ValueError("empty bar window")
    entry_idx = 0
    entry_price = float(work["close"].iloc[entry_idx])
    highest_close = entry_price
    last_idx = min(entry_idx + profile.hold_days, len(work) - 1)
    for j in range(entry_idx + 1, last_idx + 1):
        px = float(work["close"].iloc[j])
        highest_close = max(highest_close, px)
        reason = _evaluate_position_exit(
            px=px,
            entry_price=entry_price,
            entry_idx=entry_idx,
            idx=j,
            stop_pct=stop_pct,
            highest_close=highest_close,
            hold_days=profile.hold_days,
            min_hold_before_trail=profile.min_hold_before_trail,
            defer_soft_exits=profile.defer_soft_exits,
            window=work.iloc[: j + 1],
            skill_dir=skill_dir,
        )
        if reason:
            return px, work.index[j], reason
    return float(work["close"].iloc[last_idx]), work.index[last_idx], "time_exit"


def _replay_trade(
    trade: Trade,
    profile: ExitProfile,
    *,
    loader: BarLoader,
    max_hold_days: int,
    skill_dir: Path,
) -> dict[str, Any] | None:
    if trade.entry_price is None:
        return None
    if loader.provider == "chunk" and not trade.has_path():
        return None
    df = loader.load(trade, hold_days=max_hold_days)
    if df.empty or len(df) < 2:
        return None
    stop_pct = max(0.01, float(trade.stop_pct or 0.07))
    try:
        exit_px, exit_ts, reason = _replay_exit_on_df(
            df,
            profile=profile,
            stop_pct=stop_pct,
            skill_dir=skill_dir,
        )
    except Exception as exc:
        LOG.debug("replay failed %s: %s", trade.ticker, exc)
        return None
    entry_px = float(trade.entry_price)
    gross, net = _apply_cost_model(
        entry_price=entry_px,
        exit_price=float(exit_px),
        original_ret=float(trade.ret),
        original_net=float(trade.net_ret),
    )
    hold_days = max(0, int((pd.Timestamp(exit_ts) - pd.Timestamp(df.index[0])).days))
    return {
        "era": trade.era,
        "ticker": trade.ticker,
        "entry_date": str(trade.entry_date.date()),
        "exit_date": str(pd.Timestamp(exit_ts).date()),
        "exit_reason": reason,
        "hold_days": hold_days,
        "return": gross,
        "net_return": net,
        "original_net_return": float(trade.net_ret),
        "original_exit_reason": trade.exit_reason,
        "path_bars": len(df),
        "profile": profile.name,
    }


def profit_factor_from_nets(nets: list[float]) -> float | None:
    if not nets:
        return None
    wins = sum(x for x in nets if x > 0)
    losses = -sum(x for x in nets if x <= 0)
    if losses <= 0:
        return float("inf") if wins > 0 else None
    return wins / losses


def expectancy_from_nets(nets: list[float]) -> float | None:
    if not nets:
        return None
    return sum(nets) / len(nets)


def _mean_pf(era_stats: list[dict[str, Any]], key: str) -> float | None:
    vals: list[float] = []
    for row in era_stats:
        pf = row.get(key)
        if pf is None or pf == float("inf"):
            continue
        vals.append(float(pf))
    return sum(vals) / len(vals) if vals else None


def _summarise(replayed: list[dict[str, Any]]) -> dict[str, Any]:
    by_era: dict[str, list[dict[str, Any]]] = {}
    for row in replayed:
        by_era.setdefault(str(row["era"]), []).append(row)

    era_stats = []
    for era in ERA_BOUNDS:
        rows = by_era.get(era, [])
        if not rows:
            continue
        nets = [float(r["net_return"]) for r in rows]
        orig = [float(r["original_net_return"]) for r in rows]
        era_stats.append(
            {
                "era": era,
                "n": len(rows),
                "pf": profit_factor_from_nets(nets),
                "pf_original": profit_factor_from_nets(orig),
                "expectancy": expectancy_from_nets(nets),
                "expectancy_original": expectancy_from_nets(orig),
                "avg_hold": round(sum(int(r["hold_days"]) for r in rows) / len(rows), 1),
            }
        )
    all_nets = [float(r["net_return"]) for r in replayed]
    all_orig = [float(r["original_net_return"]) for r in replayed]
    return {
        "n_replayed": len(replayed),
        "pf_mean": _mean_pf(era_stats, "pf"),
        "pf_mean_original": _mean_pf(era_stats, "pf_original"),
        "expectancy": expectancy_from_nets(all_nets),
        "expectancy_original": expectancy_from_nets(all_orig),
        "eras": era_stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay exit overlays on augmented chunks.")
    parser.add_argument("--run-id", default="control_legacy_aug")
    parser.add_argument(
        "--profiles",
        nargs="*",
        default=["baseline_legacy", "exit_grace_t15_h40"],
    )
    parser.add_argument(
        "--data-provider",
        choices=("schwab", "chunk", "yfinance"),
        default="schwab",
        help="Bar source: Schwab pricehistory (default), stored ohlc_path, or yfinance.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max trades to replay (0=all).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    trades = load_trades(args.run_id)
    if args.data_provider == "chunk":
        trades = [t for t in trades if t.has_path()]
    if args.limit and args.limit > 0:
        trades = trades[: args.limit]

    max_hold = max(PROFILES[p].hold_days for p in args.profiles if p in PROFILES)
    loader = BarLoader(args.data_provider, SKILL_DIR)
    LOG.info(
        "Loaded %s trades from %s (provider=%s)",
        len(trades),
        args.run_id,
        args.data_provider,
    )

    results: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "data_provider": args.data_provider,
        "profiles": {},
    }

    for profile_name in args.profiles:
        profile = PROFILES.get(profile_name)
        if profile is None:
            LOG.warning("Unknown profile %s", profile_name)
            continue
        replayed: list[dict[str, Any]] = []
        skipped = 0
        for trade in trades:
            row = _replay_trade(
                trade,
                profile,
                loader=loader,
                max_hold_days=max_hold,
                skill_dir=SKILL_DIR,
            )
            if row is None:
                skipped += 1
                continue
            replayed.append(row)
        summary = _summarise(replayed)
        summary["skipped"] = skipped
        results["profiles"][profile_name] = summary
        LOG.info(
            "%s: n=%s pf_mean=%.3f (orig %.3f) expectancy=%.4f avg_hold=%.1f",
            profile_name,
            summary["n_replayed"],
            summary["pf_mean"] or 0.0,
            summary["pf_mean_original"] or 0.0,
            summary["expectancy"] or 0.0,
            (
                sum(r["avg_hold"] for r in summary["eras"]) / len(summary["eras"])
                if summary["eras"]
                else 0.0
            ),
        )

    base = results["profiles"].get("baseline_legacy")
    treat = results["profiles"].get("exit_grace_t15_h40")
    ctrl = results["profiles"].get("control_legacy_defaults")
    if base and treat:
        results["comparison"] = {
            "pf_mean_delta_vs_legacy_exits": (treat.get("pf_mean") or 0) - (base.get("pf_mean") or 0),
            "expectancy_delta_vs_legacy_exits": (treat.get("expectancy") or 0) - (base.get("expectancy") or 0),
        }
    if ctrl and treat:
        results["comparison_vs_control_defaults"] = {
            "pf_mean_delta": (treat.get("pf_mean") or 0) - (ctrl.get("pf_mean") or 0),
            "expectancy_delta": (treat.get("expectancy") or 0) - (ctrl.get("expectancy") or 0),
        }

    out_path = ARTIFACT_DIR / f"replay_exit_overlay_{args.run_id}.json"
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(json.dumps(results, indent=2, default=str))
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
