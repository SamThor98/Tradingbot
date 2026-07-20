#!/usr/bin/env python3
"""Sector × regime attribution and sector-gate on/off comparison (P0).

Default mode runs one shadow-gate backtest (losing-sector trades kept), tags
each trade with sector_etf + regime_bucket, then counterfactually applies a
hard sector gate. Optional ``--paired`` runs full shadow vs hard backtests.

Usage (from schwab_skill/):
  python scripts/analyze_sector_regime.py --run
  python scripts/analyze_sector_regime.py --run --paired
  python scripts/analyze_sector_regime.py --from-trades path/to/trades.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from env_overrides import temporary_env  # noqa: E402
from sector_regime_analysis import (  # noqa: E402
    MIN_TRADES_DEFAULT,
    paired_run_lift,
    sector_gate_counterfactual,
    summarize_sector_regime,
)

ART = SKILL_DIR / "validation_artifacts"

DEFAULT_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "V", "UNH",
    "HD", "PG", "MA", "DIS", "BAC", "XOM", "CVX", "KO", "PEP", "WMT",
    "IBM", "ORCL", "CRM", "ADBE", "NFLX", "INTC", "AMD", "QCOM", "TXN", "AVGO",
    "CSCO", "ACN", "NOW", "INTU", "AMAT", "LRCX", "KLAC", "MU", "SBUX", "NKE",
]


def _load_trades(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [t for t in payload if isinstance(t, dict)]
    if isinstance(payload, dict):
        trades = payload.get("trades") or payload.get("trades_sample") or []
        if isinstance(trades, list):
            return [t for t in trades if isinstance(t, dict)]
    raise ValueError(f"Expected trades list or backtest payload with trades in {path}")


def _run_backtest(
    *,
    tickers: list[str],
    start: str,
    end: str | None,
    sector_gate_mode: str,
    allow_bear: bool,
) -> dict[str, Any]:
    from backtest import run_backtest

    overrides: dict[str, object] = {
        "SCAN_SECTOR_GATE_MODE": sector_gate_mode,
        "SCAN_ALLOW_BEAR_REGIME": "true" if allow_bear else "false",
    }
    kwargs: dict[str, Any] = {
        "tickers": tickers,
        "start_date": start,
        "include_all_trades": True,
        "slippage_bps_per_side": 15.0,
        "fee_per_share": 0.005,
        "min_fee_per_order": 1.0,
        "max_adv_participation": 0.02,
    }
    if end:
        kwargs["end_date"] = end
    with temporary_env(overrides):
        return run_backtest(**kwargs)


def _print_summary(report: dict[str, Any]) -> None:
    cf = report.get("counterfactual") or {}
    lift = cf.get("lift") or {}
    print("\n--- SECTOR x REGIME ---")
    print(f"  trades: {report.get('trade_count', 0)}")
    print(f"  recommendation: {cf.get('recommendation')}")
    print(
        "  hard vs shadow lift: "
        f"PF_delta={lift.get('profit_factor_delta')} "
        f"exp_delta={lift.get('expectancy_delta')} "
        f"n_ratio={lift.get('trade_count_ratio')} "
        f"positive_regimes={lift.get('positive_regime_lift_count')}"
    )
    by_regime = (cf.get("baseline_shadow") or {}).get("by_regime") or {}
    if by_regime:
        print("  by regime (shadow):")
        for bucket, m in sorted(by_regime.items()):
            print(
                f"    {bucket}: n={m.get('n')} PF={m.get('profit_factor')} "
                f"exp={m.get('expectancy')} sparse={m.get('sparse')}"
            )
    regime_lift = cf.get("by_regime_lift") or {}
    if regime_lift:
        print("  by regime (hard - shadow PF):")
        for bucket, m in sorted(regime_lift.items()):
            print(
                f"    {bucket}: PF_delta={m.get('profit_factor_delta')} "
                f"n {m.get('baseline_n')}->{m.get('hard_n')}"
            )
    paired = report.get("paired")
    if paired:
        print(f"  paired full-run lift: {paired.get('lift')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sector × regime backtest attribution")
    parser.add_argument("--run", action="store_true", help="Run a fresh shadow-gate backtest")
    parser.add_argument(
        "--from-trades",
        type=Path,
        help="Load trades from JSON (list or backtest payload with trades)",
    )
    parser.add_argument(
        "--paired",
        action="store_true",
        help="Also run a hard-gate backtest and compare portfolio metrics",
    )
    parser.add_argument("--tickers", type=int, default=40, help="Universe size when --run")
    parser.add_argument("--start", default="2015-01-01", help="Backtest start date")
    parser.add_argument("--end", default=None, help="Optional backtest end date")
    parser.add_argument(
        "--allow-bear-regime",
        dest="allow_bear",
        action="store_true",
        default=True,
        help="Allow entries when SPY < 200 SMA (default on for regime coverage)",
    )
    parser.add_argument(
        "--no-allow-bear-regime",
        dest="allow_bear",
        action="store_false",
        help="Disable bear-regime entries (live default parity)",
    )
    parser.add_argument("--min-trades", type=int, default=MIN_TRADES_DEFAULT)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output artifact path (default validation_artifacts/sector_regime_*.json)",
    )
    args = parser.parse_args()

    if not args.run and args.from_trades is None:
        parser.error("Provide --run and/or --from-trades")

    allow_bear = bool(args.allow_bear)
    tickers = DEFAULT_TICKERS[: max(1, int(args.tickers))]
    trades: list[dict[str, Any]] = []
    shadow_result: dict[str, Any] | None = None
    hard_result: dict[str, Any] | None = None

    if args.from_trades is not None:
        trades = _load_trades(args.from_trades)
        print(f"Loaded {len(trades)} trades from {args.from_trades}")

    if args.run:
        print(
            f"Running shadow-gate backtest "
            f"(tickers={len(tickers)}, start={args.start}, allow_bear={allow_bear})..."
        )
        shadow_result = _run_backtest(
            tickers=tickers,
            start=args.start,
            end=args.end,
            sector_gate_mode="shadow",
            allow_bear=allow_bear,
        )
        trades = list(shadow_result.get("trades") or [])
        print(
            f"  shadow trades={shadow_result.get('total_trades')} "
            f"PF_net={shadow_result.get('profit_factor_net')} "
            f"ret_net={shadow_result.get('total_return_net_pct')}%"
        )
        if args.paired:
            print("Running hard-gate backtest...")
            hard_result = _run_backtest(
                tickers=tickers,
                start=args.start,
                end=args.end,
                sector_gate_mode="hard",
                allow_bear=allow_bear,
            )
            print(
                f"  hard trades={hard_result.get('total_trades')} "
                f"PF_net={hard_result.get('profit_factor_net')} "
                f"ret_net={hard_result.get('total_return_net_pct')}%"
            )

    attribution = summarize_sector_regime(trades, min_trades=int(args.min_trades))
    counterfactual = sector_gate_counterfactual(trades, min_trades=int(args.min_trades))

    report: dict[str, Any] = {
        "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "run" if args.run else "from_trades",
        "config": {
            "tickers": len(tickers) if args.run else None,
            "start": args.start if args.run else None,
            "end": args.end,
            "allow_bear_regime": allow_bear if args.run else None,
            "min_trades": int(args.min_trades),
            "paired": bool(args.paired and args.run),
        },
        "trade_count": len(trades),
        "attribution": attribution,
        "counterfactual": counterfactual,
    }
    if shadow_result is not None:
        report["shadow_backtest"] = {
            k: shadow_result.get(k)
            for k in (
                "total_trades",
                "profit_factor_net",
                "total_return_net_pct",
                "max_drawdown_net_pct",
                "win_rate_net",
                "diagnostics",
                "sector_regime_summary",
            )
        }
    if hard_result is not None:
        report["hard_backtest"] = {
            k: hard_result.get(k)
            for k in (
                "total_trades",
                "profit_factor_net",
                "total_return_net_pct",
                "max_drawdown_net_pct",
                "win_rate_net",
                "diagnostics",
                "sector_regime_summary",
            )
        }
        report["paired"] = {
            "lift": paired_run_lift(shadow_result or {}, hard_result),
            "hard_attribution": summarize_sector_regime(
                list(hard_result.get("trades") or []),
                min_trades=int(args.min_trades),
            ),
        }

    ART.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = args.out or (ART / f"sector_regime_{stamp}.json")
    # Keep artifact lean: drop full trade list unless explicitly from small file analysis
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    trades_path = out_path.with_name(out_path.stem + "_trades.json")
    trades_path.write_text(json.dumps(trades, indent=2, default=str), encoding="utf-8")

    _print_summary(report)
    print(f"\nWrote {out_path}")
    print(f"Wrote {trades_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
