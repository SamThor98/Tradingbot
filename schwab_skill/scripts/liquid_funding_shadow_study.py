"""Shadow study: do Liquid funding features separate Stage 2 backtest trades?

Runs the standard backtest over only the Liquid-covered tickers for the
window where funding history exists, then buckets closed trades by their
point-in-time funding z90 (attached by backtest._attach_liquid_shadow).

This is a measurement, not a promotion. The sample is a handful of
mega-caps over <= ~11 months — buckets under the floor are reported as
insufficient_sample, never as a profit factor of 0.

    python scripts/liquid_funding_shadow_study.py
    python scripts/liquid_funding_shadow_study.py --start 2026-02-01 --end 2026-10-21
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SKILL_DIR = Path(__file__).resolve().parent.parent
MIN_BUCKET_TRADES = 30


def _pf(net_returns: list[float]) -> float | None:
    gains = sum(r for r in net_returns if r > 0)
    losses = -sum(r for r in net_returns if r < 0)
    if losses <= 0:
        return None  # undefined, not infinity and not zero
    return round(gains / losses, 4)


def _bucket(name: str, trades: list[dict[str, Any]]) -> dict[str, Any]:
    rets = [float(t.get("net_return") or 0.0) for t in trades]
    return {
        "bucket": name,
        "n": len(trades),
        "pf_net": _pf(rets),
        "avg_net_return": round(sum(rets) / len(rets), 5) if rets else None,
        "status": "measured" if len(trades) >= MIN_BUCKET_TRADES else "insufficient_sample",
    }


def analyze_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    covered = [t for t in trades if t.get("liquid_funding_days")]
    with_z = [t for t in covered if t.get("liquid_funding_z90") is not None]
    buckets = {
        "covered_all": _bucket("covered_all", covered),
        "z_low": _bucket("z_low", [t for t in with_z if t["liquid_funding_z90"] <= -0.5]),
        "z_mid": _bucket("z_mid", [t for t in with_z if -0.5 < t["liquid_funding_z90"] < 0.5]),
        "z_high": _bucket("z_high", [t for t in with_z if t["liquid_funding_z90"] >= 0.5]),
        "crowded_long": _bucket(
            "crowded_long", [t for t in with_z if t["liquid_funding_z90"] >= 1.5]
        ),
    }
    # Shadow would-filter: drop crowded-long extremes, keep everything else.
    kept = [t for t in covered if (t.get("liquid_funding_z90") or 0.0) < 1.5]
    buckets["shadow_kept"] = _bucket("shadow_kept", kept)
    measured = [b for b in buckets.values() if b["status"] == "measured"]
    return {
        "total_trades": len(trades),
        "covered_trades": len(covered),
        "with_z_trades": len(with_z),
        "coverage_pct": round(100.0 * len(covered) / len(trades), 1) if trades else 0.0,
        "buckets": buckets,
        "verdict": "measured" if measured else "insufficient_sample",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Price warmup: the backtest needs >= 260 bars, so the window starts
    # months before funding history begins (2025-11). Trades before a
    # ticker's funding listing simply land in the uncovered bucket.
    parser.add_argument("--start", default="2025-06-02")
    parser.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--tickers", help="Comma-separated override (default: funding-covered tickers)")
    args = parser.parse_args()

    from core.liquid_positioning.store import get_funding_daily

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        # Covered = has enough funding history for a z90 (20+ days minimum).
        from core.liquid_positioning.store import get_latest_snapshots

        tickers = []
        for snap in get_latest_snapshots(SKILL_DIR):
            daily = get_funding_daily(snap["ticker"], days=3650, skill_dir=SKILL_DIR)
            if len(daily) >= 60:
                tickers.append(snap["ticker"])
        tickers.sort()

    from backtest import run_backtest

    result = run_backtest(
        tickers=tickers,
        start_date=args.start,
        end_date=args.end,
        include_all_trades=True,
        skill_dir=SKILL_DIR,
    )
    trades = result.get("trades") or []
    study = analyze_trades(trades)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start": args.start, "end": args.end},
        "tickers": tickers,
        "backtest_total_trades": result.get("total_trades"),
        "backtest_profit_factor_net": result.get("profit_factor_net"),
        "diagnostics_liquid": {
            k: (result.get("diagnostics") or {}).get(k)
            for k in ("liquid_evaluated", "liquid_covered", "liquid_uncovered", "liquid_errors")
        },
        **study,
        # Single recent window: there are no eras to take a worst-of.
        # Null (not 0, not the window PF) so the card scorer cannot clear floors.
        "worst_era_pf": None,
        "note": (
            "Shadow measurement only. Buckets under 30 trades are insufficient_sample. "
            "Sample is Liquid-covered mega-caps over the funding-history window; "
            "not a multi-era verdict and not comparable to the capped-replay ledger floors."
        ),
    }
    dest = SKILL_DIR / "validation_artifacts" / "liquid_funding_study.json"
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print("WROTE", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
