"""Replay the Alex strategy (labeled Swing Edge v10) on historical daily bars.

Research sleeve only — does not change the Stage 2 live scanner.

Usage (from schwab_skill/)::

    python scripts/run_alex_strategy_backtest.py --tickers 25 --start-date 2020-01-01
    python scripts/run_alex_strategy_backtest.py --symbols DIS JPM XOM WMT --start-date 2024-01-01
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from backtest import _fetch_history_with_meta  # noqa: E402
from core.alex_strategy import (  # noqa: E402
    DEFAULT_UNIVERSE_PATH,
    AlexParams,
    load_alex_params,
    load_alex_universe,
    run_alex_backtest,
)
from logger_setup import get_logger, setup_logging  # noqa: E402

LOG = logging.getLogger("run_alex_strategy_backtest")


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Alex strategy (Swing Edge v10) backtest")
    p.add_argument("--start-date", default="2020-01-01", help="YYYY-MM-DD")
    p.add_argument("--end-date", default="", help="YYYY-MM-DD; default today")
    p.add_argument("--tickers", type=int, default=0, help="Cap universe size (0 = all loaded)")
    p.add_argument("--symbols", nargs="*", default=None, help="Explicit ticker list (overrides cap)")
    p.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH), help="Universe JSON path")
    p.add_argument(
        "--entry-mode",
        choices=["next_open", "close"],
        default="",
        help="Override ALEX_ENTRY_MODE (default next_open)",
    )
    p.add_argument(
        "--output",
        default="",
        help="JSON artifact path (default validation_artifacts/alex_strategy_backtest.json)",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def _pad_start(start_date: str, params: AlexParams) -> str:
    extra = max(params.shy_roc_days, params.atr_period, 20) + 10
    dt = datetime.fromisoformat(start_date) - timedelta(days=int(extra * 1.7))
    return dt.strftime("%Y-%m-%d")


def _fetch_many(symbols: list[str], start_date: str, end_date: str) -> dict[str, Any]:
    from pandas import DataFrame

    out: dict[str, Any] = {}
    empty = 0
    for i, symbol in enumerate(symbols, start=1):
        df, meta = _fetch_history_with_meta(symbol, start_date, end_date)
        if df is None or df.empty:
            empty += 1
            LOG.warning("Empty history for %s (%s)", symbol, meta.get("reason"))
            continue
        out[symbol] = df
        if i % 10 == 0 or i == len(symbols):
            LOG.info("Fetched %s/%s symbols (%s empty so far)", i, len(symbols), empty)
    if "SPY" not in out:
        out["SPY"] = DataFrame()
    if "SHY" not in out:
        out["SHY"] = DataFrame()
    return out


def main() -> int:
    args = _parse()
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)
    get_logger(__name__)

    universe = load_alex_universe(Path(args.universe))
    if args.symbols:
        wanted = [str(s).strip().upper() for s in args.symbols if str(s).strip()]
        universe = {t: universe.get(t, "Unknown") for t in wanted}
    elif args.tickers and args.tickers > 0:
        kept = list(universe.items())[: int(args.tickers)]
        universe = dict(kept)

    params = load_alex_params(SKILL_DIR)
    if args.entry_mode:
        params = replace(params, entry_mode=args.entry_mode)

    end_date = args.end_date.strip() or datetime.now().strftime("%Y-%m-%d")
    fetch_start = _pad_start(args.start_date, params)
    symbols = list(universe.keys()) + ["SPY", "SHY"]
    LOG.info(
        "Alex backtest: %s symbols, %s to %s, entry_mode=%s",
        len(universe),
        args.start_date,
        end_date,
        params.entry_mode,
    )
    frames = _fetch_many(symbols, fetch_start, end_date)
    spy = frames.pop("SPY", None)
    shy = frames.pop("SHY", None)
    price_data = {t: frames[t] for t in universe if t in frames}

    result = run_alex_backtest(
        price_data,
        universe=universe,
        spy=spy,
        shy=shy,
        params=params,
        start_date=args.start_date,
    )
    result["window"] = {"start": args.start_date, "end": end_date, "fetch_start": fetch_start}
    result["symbols_requested"] = len(universe)
    result["symbols_loaded"] = len(price_data)

    out_path = Path(args.output) if args.output else SKILL_DIR / "validation_artifacts" / "alex_strategy_backtest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    slim = dict(result)
    slim["trades"] = result.get("trades") or []
    out_path.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")

    LOG.info(
        "Alex result: trades=%s win_rate=%s pf=%s return_pct=%s cagr_pct=%s max_dd_pct=%s ending=%s",
        result.get("total_trades"),
        result.get("win_rate"),
        result.get("profit_factor"),
        result.get("total_return_pct"),
        result.get("cagr_pct"),
        result.get("max_drawdown_pct"),
        result.get("ending_equity"),
    )
    LOG.info("Wrote %s", out_path)
    print(json.dumps({k: result[k] for k in result if k not in {"trades", "equity_curve"}}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
