"""Quick diagnostic: dump the persisted last_scan (incl. per-signal flagged_days) from local SQLite."""

from __future__ import annotations

import json

from webapp.db import SessionLocal
from webapp.models import AppState, ScanResult


def main() -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(AppState)
            .filter(AppState.user_id == "local", AppState.key == "last_scan")
            .first()
        )
        if not row:
            print("No persisted last_scan found for user 'local'.")
        else:
            value = row.value_json
            if isinstance(value, str):
                value = json.loads(value)
            diag = value.get("diagnostics") or {}
            print("Persisted last_scan diagnostics:")
            print(f"  at:                 {value.get('at')}")
            print(f"  signals_found:      {value.get('signals_found')}")
            print(f"  watchlist_size:     {diag.get('watchlist_size')}")
            print(f"  watchlist_source:   {diag.get('watchlist_source')}")
            print(f"  stage2_fail:        {diag.get('stage2_fail')}")
            print(f"  data_quality:       {diag.get('data_quality')}")

            sigs = value.get("signals") or []
            print()
            print(f"Persisted signals ({len(sigs)}):")
            for i, sig in enumerate(sigs[:8]):
                ticker = sig.get("ticker") or sig.get("symbol")
                flagged = sig.get("flagged_days")
                conviction = sig.get("mirofish_conviction")
                signal_score = sig.get("signal_score")
                print(
                    f"  [{i}] {ticker}: flagged_days={flagged!r}  signal_score={signal_score!r}  conviction={conviction!r}"
                )

        print()
        scan_count = db.query(ScanResult).filter(ScanResult.user_id == "local").count()
        print(f"Local ScanResult rows: {scan_count}")
        if scan_count:
            recent = (
                db.query(ScanResult)
                .filter(ScanResult.user_id == "local")
                .order_by(ScanResult.created_at.desc())
                .limit(5)
                .all()
            )
            print("Most recent ScanResult rows:")
            for r in recent:
                print(f"  - id={r.id} ticker={r.ticker} created_at={r.created_at} job={r.job_id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
