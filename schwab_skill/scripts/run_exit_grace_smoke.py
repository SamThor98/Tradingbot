"""Single-era smoke: baseline vs delayed-trail exit experiment."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

os.environ["SCHWAB_ONLY_DATA"] = "true"
os.environ["BACKTEST_SKIP_MIROFISH"] = "true"
os.environ["SEC_FILING_LLM_SUMMARY_ENABLED"] = "false"

from backtest import run_backtest  # noqa: E402
from backtest_intelligence import BacktestIntelligenceConfig  # noqa: E402
from scripts.run_multi_era_backtest_schwab_only import _load_universe_tickers  # noqa: E402


def _hold_days(trade: dict) -> int:
    ed = datetime.fromisoformat(str(trade["entry_date"]).replace("Z", "+00:00"))
    xd = datetime.fromisoformat(str(trade["exit_date"]).replace("Z", "+00:00"))
    return max((xd - ed).days, 0)


def _pf(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    gp = sum(float(r["net_return"]) for r in rows if float(r["net_return"]) > 0)
    gl = abs(sum(float(r["net_return"]) for r in rows if float(r["net_return"]) <= 0))
    return gp / gl if gl > 0 else float("inf")


def cohort_stats(trades: list[dict]) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0}
    early = [t for t in trades if _hold_days(t) <= 20]
    mid = [t for t in trades if 21 <= _hold_days(t) <= 40]
    return {
        "n": n,
        "pf_net": round(_pf(trades), 3),
        "early_n": len(early),
        "early_share_pct": round(100 * len(early) / n, 1),
        "early_pf": round(_pf(early), 3),
        "hold21_40_n": len(mid),
        "hold21_40_pf": round(_pf(mid), 3),
        "avg_hold": round(sum(_hold_days(t) for t in trades) / n, 1),
        "exits_trailing_stop": sum(1 for t in trades if t.get("exit_reason") == "trailing_stop"),
    }


def run(label: str, overrides: dict[str, str], tickers: list[str]) -> dict:
    print(f"Running {label} on {len(tickers)} tickers (recent_current)...", flush=True)
    out = run_backtest(
        tickers=tickers,
        start_date="2024-01-01",
        end_date=None,
        include_all_trades=True,
        intelligence_overlay=overrides,
        skill_dir=SKILL_DIR,
    )
    trades = list(out.get("trades") or [])
    stats = cohort_stats(trades)
    stats["total_return_net_pct"] = out.get("total_return_net_pct")
    stats["profit_factor_net"] = out.get("profit_factor_net")
    stats["max_drawdown_net_pct"] = out.get("max_drawdown_net_pct")
    return stats


def main() -> int:
    tickers = _load_universe_tickers()[:60]
    overlay = BacktestIntelligenceConfig.all_off().as_dict()
    baseline = run(
        "baseline",
        {**overlay, "BACKTEST_HOLD_DAYS": "20", "BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL": "0"},
        tickers,
    )
    experiment = run(
        "exit_grace15_hold40",
        {
            **overlay,
            "BACKTEST_HOLD_DAYS": "40",
            "BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL": "15",
            "BACKTEST_MIN_HOLD_DEFER_SOFT_EXITS": "true",
        },
        tickers,
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "era": "recent_current",
        "tickers": len(tickers),
        "baseline": baseline,
        "experiment": experiment,
    }
    out_path = SKILL_DIR / "scripts" / "exit_grace_smoke_recent_current.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
