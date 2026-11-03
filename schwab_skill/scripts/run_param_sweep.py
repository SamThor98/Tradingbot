#!/usr/bin/env python3
"""Cheap ATR / breakout-buffer / hold-days grid. NOT PROMOTION-ELIGIBLE.

Any survivor must be re-run through run_multi_era_backtest_schwab_only.py
with live-parity code. This script must stay isolated from signal_scanner
and execution.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.param_sweep import (  # noqa: E402
    NOT_PROMOTION_ELIGIBLE,
    sweep_frozen_trades,
    sweep_ohlcv_panel,
    try_vectorbt_available,
)

BANNER = "*** NOT PROMOTION-ELIGIBLE ***\n" + NOT_PROMOTION_ELIGIBLE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades", default=None, help="Parquet/CSV frozen trade list")
    parser.add_argument("--panel-dir", default=None, help="Directory of {TICKER}.parquet OHLCV")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    print(BANNER)
    print("vectorbt_available=", try_vectorbt_available())

    if args.trades:
        path = Path(args.trades)
        trades = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_parquet(path)
        table = sweep_frozen_trades(trades)
    elif args.panel_dir:
        panel: dict[str, pd.DataFrame] = {}
        root = Path(args.panel_dir)
        for p in list(root.glob("*.parquet"))[:8]:
            panel[p.stem.upper()] = pd.read_parquet(p)
        table = sweep_ohlcv_panel(panel)
    else:
        print("Provide --trades or --panel-dir")
        return 2

    table["promotion_eligible"] = False
    print(table.to_string(index=False))
    if args.out:
        Path(args.out).write_text(table.to_json(orient="records", indent=2), encoding="utf-8")
    print(BANNER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
