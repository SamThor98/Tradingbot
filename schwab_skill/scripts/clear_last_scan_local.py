"""Drop the persisted `last_scan` AppState row for the local dashboard user.

Use this after a code change that affects scan output (e.g. flagged_days
enrichment) but before you can run a fresh scan: it stops the dashboard from
rendering the *previous* scan's chips/rows on the next page load, so once you
hit Run Scan the screen reflects the new pipeline immediately.

Optionally pass --truncate-results to also delete the local ScanResult rows so
the next scan starts the days-flagged counter from zero.
"""

from __future__ import annotations

import argparse

from webapp.db import SessionLocal
from webapp.models import AppState, ScanResult


def _clear(*, truncate_results: bool) -> None:
    db = SessionLocal()
    try:
        deleted_state = (
            db.query(AppState)
            .filter(AppState.user_id == "local", AppState.key == "last_scan")
            .delete(synchronize_session=False)
        )
        deleted_results = 0
        if truncate_results:
            deleted_results = (
                db.query(ScanResult)
                .filter(ScanResult.user_id == "local")
                .delete(synchronize_session=False)
            )
        db.commit()
        print(f"Removed {deleted_state} AppState 'last_scan' row(s).")
        if truncate_results:
            print(f"Removed {deleted_results} ScanResult row(s).")
        print("Next page load will start blank; run a fresh scan to populate.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--truncate-results",
        action="store_true",
        help="Also delete local ScanResult rows so the days-flagged counter resets to zero.",
    )
    args = parser.parse_args()
    _clear(truncate_results=bool(args.truncate_results))


if __name__ == "__main__":
    main()
