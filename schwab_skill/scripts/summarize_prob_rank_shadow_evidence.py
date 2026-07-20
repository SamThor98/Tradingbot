#!/usr/bin/env python3
"""Summarize (or seed) the prob-rank shadow evidence ledger.

Append-only JSONL lives at:
  validation_artifacts/prob_rank_shadow_evidence/shadow_scans.jsonl

Written automatically on each Stage-B scan when PROB_RANK_MODE is shadow/live.

Examples:
  python scripts/summarize_prob_rank_shadow_evidence.py
  python scripts/summarize_prob_rank_shadow_evidence.py --seed-smoke
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
from research.shadow_evidence import (  # noqa: E402
    ledger_path,
    load_shadow_evidence_records,
    summarize_shadow_evidence,
)

LOG = get_logger("summarize_prob_rank_shadow_evidence")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=str,
        default=str(
            SKILL_DIR
            / "validation_artifacts"
            / "prob_rank_shadow_evidence"
            / "summary.json"
        ),
    )
    parser.add_argument(
        "--seed-smoke",
        action="store_true",
        help="Run smoke_prob_rank_shadow_scan first to append one ledger row",
    )
    args = parser.parse_args(argv)
    setup_logging()

    if args.seed_smoke:
        from scripts.smoke_prob_rank_shadow_scan import main as smoke_main

        rc = smoke_main(["--limit", "20"])
        if rc != 0:
            LOG.error("Smoke seed failed rc=%s", rc)
            return rc

    records = load_shadow_evidence_records(SKILL_DIR)
    summary = summarize_shadow_evidence(records)
    summary["ledger"] = str(ledger_path(SKILL_DIR))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    LOG.info("Wrote %s (n_scans=%s)", out, summary.get("n_scans"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
