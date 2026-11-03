#!/usr/bin/env python3
"""Build DuckDB views over research_store/ and validate against the registry schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402
from research.warehouse import open_duckdb, validate_frame  # noqa: E402

LOG = get_logger("build_research_warehouse")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)
    setup_logging()
    con = open_duckdb(SKILL_DIR)
    if con is None:
        LOG.error("duckdb unavailable")
        return 2
    try:
        n = con.execute("SELECT COUNT(*) FROM features").fetchone()[0]
        LOG.info("features view rows: %s", n)
        if n:
            import pandas as pd

            df = con.execute(f"SELECT * FROM features LIMIT {int(args.limit)}").df()
            errs = validate_frame(pd.DataFrame(df))
            if errs:
                LOG.warning("schema errors: %s", errs)
                return 3
    except Exception as exc:
        LOG.warning("warehouse query failed (empty store is ok): %s", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
