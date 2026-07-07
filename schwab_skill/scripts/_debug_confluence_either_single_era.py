"""One-off Task 2 verification: run the confluence_either gate config on a
small watchlist over the bear_rates era and dump gate diagnostics.

Distinguishes "gate legitimately filters trades" from "gate short-circuits
everything" (the June sweep symptom, where excluded_count == universe_size).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

os.environ.update(
    {
        "SCHWAB_ONLY_DATA": "true",
        "BACKTEST_SKIP_MIROFISH": "true",
        "SEC_FILING_LLM_SUMMARY_ENABLED": "false",
        # confluence_either overrides (validation_artifacts/phase1_env_overrides/confluence_either.json)
        "META_POLICY_MODE": "off",
        "UNCERTAINTY_MODE": "off",
        "EVENT_RISK_MODE": "off",
        "EXIT_MANAGER_MODE": "off",
        "EXEC_QUALITY_MODE": "off",
        "CONFLUENCE_GATE_MODE": "live",
        "CONFLUENCE_REQUIRE_COUNT": "1",
    }
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

from backtest import run_backtest  # noqa: E402
from backtest_intelligence import BacktestIntelligenceConfig  # noqa: E402

WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "AMD", "AVGO", "GOOGL", "AMZN", "META", "TSLA", "NFLX",
    "COST", "LLY", "UNH", "XOM", "CVX", "JPM", "GS", "CAT", "DE", "HON",
    "PANW", "ANET", "SMCI", "MPC", "VLO", "ELF", "DECK", "URI", "PHM", "FICO",
]


def main() -> int:
    overlay_cfg = BacktestIntelligenceConfig.from_env(SKILL_DIR)
    metrics = run_backtest(
        tickers=WATCHLIST,
        start_date="2022-01-01",
        end_date="2023-12-31",
        include_all_trades=True,
        intelligence_overlay=overlay_cfg,
        skill_dir=SKILL_DIR,
    )
    diag = metrics.get("diagnostics") or {}
    out = {
        "universe_size": metrics.get("universe_size"),
        "excluded_count": metrics.get("excluded_count"),
        "excluded_tickers": metrics.get("excluded_tickers"),
        "total_trades": metrics.get("total_trades"),
        "profit_factor_net": metrics.get("profit_factor_net"),
        "data_integrity": metrics.get("data_integrity"),
        "gate_diagnostics": {
            k: diag.get(k)
            for k in (
                "regime_blocked",
                "stage2_fail",
                "vcp_fail",
                "vcp_would_block",
                "breakout_not_confirmed",
                "quality_gates_filtered",
                "confluence_confirmed",
                "confluence_blocked",
                "confluence_would_block",
                "confluence_pead_unavailable",
            )
        },
    }
    print(json.dumps(out, indent=2, default=str))
    Path(SKILL_DIR / "validation_artifacts" / "_debug_confluence_either_bear_rates.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
