#!/usr/bin/env python3
"""Factor analytics (Alphalens-style) + optional book tearsheet.

Writes validation_artifacts/factor_analytics/<run_id>/. Never overwrites
BACKTEST_CATALOG.md.

Example:
  python scripts/run_factor_analytics.py --run-id stage2_only_aug --cap 37
  python scripts/run_factor_analytics.py --fixture tests/fixtures/factor_analytics_negative_ic.parquet --run-id fixture_neg_ic
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402
from research.factor_analytics import (  # noqa: E402
    DOCUMENTED_STAGE2_ONLY_AUG_IC,
    NOT_PROMOTION_GATE,
    analyze_scores,
    equity_tearsheet,
    write_factor_report,
)

LOG = get_logger("run_factor_analytics")


def _load_trades(run_id: str, cap: float | None) -> pd.DataFrame:
    from scripts.phase2_common import load_trades

    rows = load_trades(run_id)
    if not rows:
        raise FileNotFoundError(f"no trade chunks for run_id={run_id}")
    recs = []
    for t in rows:
        recs.append(
            {
                "ticker": t.ticker,
                "asof_date": t.entry_date,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "net_return": t.net_ret,
                "era": t.era,
                "signal_score": t.signal_score,
            }
        )
    df = pd.DataFrame(recs)
    if cap is not None and "pts_52w" in df.columns:
        df = df[pd.to_numeric(df["pts_52w"], errors="coerce") <= float(cap)].copy()
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="stage2_only_aug")
    parser.add_argument("--cap", type=float, default=37.0, help="pts_52w cap filter when column present")
    parser.add_argument("--fixture", default=None, help="Parquet/CSV with scores + returns")
    parser.add_argument("--equity-json", default=None, help="Optional equity_curve JSON from _simulate_portfolio_equity")
    args = parser.parse_args(argv)
    setup_logging()
    LOG.info("%s", NOT_PROMOTION_GATE)

    if args.fixture:
        path = Path(args.fixture)
        if path.suffix.lower() == ".csv":
            df = pd.read_csv(path)
        else:
            df = pd.read_parquet(path)
    else:
        try:
            df = _load_trades(args.run_id, args.cap)
        except Exception as exc:
            LOG.error("Failed loading trades (fail-closed, not fabricating): %s", exc)
            return 2

    payload = analyze_scores(df)
    payload["input_run_id"] = args.run_id
    payload["pts_52w_cap"] = args.cap
    tearsheet = None
    if args.equity_json:
        raw = pd.read_json(args.equity_json)
        tearsheet = equity_tearsheet(raw)

    out = write_factor_report(payload, run_id=args.run_id, skill_dir=SKILL_DIR, tearsheet=tearsheet)
    LOG.info("Wrote factor analytics %s n=%s", out, payload.get("n"))
    for name, block in (payload.get("scores") or {}).items():
        LOG.info("IC %s=%s negative=%s", name, block.get("ic"), block.get("negative_ic"))

    # Reproduce documented negative-IC sign on stage2_only_aug / fixture.
    doc = DOCUMENTED_STAGE2_ONLY_AUG_IC
    sig = (payload.get("scores") or {}).get("signal_score") or {}
    if sig.get("ic") is not None and sig.get("negative_ic") is not True:
        LOG.warning(
            "signal_score IC is not negative (documented %s on %s)",
            doc.get("signal_score"),
            doc.get("run_id"),
        )
        if args.run_id == "stage2_only_aug":
            return 3
    print(json_dumps_safe(payload))
    return 0


def json_dumps_safe(payload: dict) -> str:
    import json

    return json.dumps(payload, indent=2, default=str)


if __name__ == "__main__":
    raise SystemExit(main())
