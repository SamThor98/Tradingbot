#!/usr/bin/env python3
"""Replay one era's candidate membership: PIT vs Wikipedia-now.

Does NOT replace run_multi_era_backtest_schwab_only.py. Reports trade-count
and PF delta for review.

Example:
  python scripts/replay_pit_universe_era.py --asof 2016-06-01 --era late_bull --run-id stage2_only_aug
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402
from research.pit_universe import replay_membership_delta  # noqa: E402

LOG = get_logger("replay_pit_universe_era")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asof", required=True, help="YYYY-MM-DD membership date")
    parser.add_argument("--era", default="late_bull")
    parser.add_argument("--run-id", default=None, help="Optional trade chunk run_id for PF delta")
    parser.add_argument("--out", default=None, help="Write JSON report path")
    parser.add_argument(
        "--provider",
        default="auto",
        help="stub | sharadar | auto (sharadar when SHARADAR_API_KEY is set)",
    )
    args = parser.parse_args(argv)
    setup_logging()

    from config import clear_env_cache, get_sharadar_api_key
    from research.providers.sharadar_pit import SharadarPITAdapter
    from research.providers.stub_pit import StubPITProvider

    clear_env_cache()
    want = str(args.provider or "auto").strip().lower()
    if want == "auto":
        want = "sharadar" if get_sharadar_api_key(SKILL_DIR) else "stub"
    if want == "sharadar":
        universe = SharadarPITAdapter(skill_dir=SKILL_DIR)
        if not get_sharadar_api_key(SKILL_DIR):
            LOG.error("SHARADAR_API_KEY missing; fail-closed")
    else:
        universe = StubPITProvider()
        LOG.warning("Using stub PIT provider — not a real universe")

    trades = None
    if args.run_id:
        try:
            import pandas as pd

            from scripts.phase2_common import load_trades

            rows = load_trades(args.run_id, eras=[args.era])
            if rows:
                trades = pd.DataFrame(
                    {
                        "ticker": [t.ticker for t in rows],
                        "net_return": [t.net_ret for t in rows],
                        "era": [t.era for t in rows],
                    }
                )
        except Exception as exc:
            LOG.warning("Could not load trades for PF delta: %s", exc)

    report = replay_membership_delta(args.asof, universe, trades=trades)
    report["era"] = args.era
    if args.run_id:
        report["run_id"] = args.run_id
    report["catalog_overwrite"] = False
    LOG.info("PIT replay banner: %s", report.get("banner"))
    LOG.info(
        "asof=%s n_pit=%s n_wiki=%s trade_delta=%s pf_delta=%s",
        report.get("asof"),
        report.get("n_pit"),
        report.get("n_wikipedia_now"),
        report.get("trade_count_delta"),
        report.get("pf_delta"),
    )
    text = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    dq = report.get("data_quality") or {}
    return 0 if dq.get("status") == "ok" and int(report.get("n_pit") or 0) > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
