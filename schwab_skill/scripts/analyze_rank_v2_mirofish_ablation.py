#!/usr/bin/env python3
"""Shadow/diagnostic-only rank-v2 MiroFish ablation (does not change LIVE rank).

Recomputes rank-v2 with current weights vs ``pts_mirofish=0`` and reports
Spearman, top-N enter/leave, mean |rank delta|, and mirofish coverage.

Usage (from schwab_skill/):
  python scripts/analyze_rank_v2_mirofish_ablation.py --signals-json path.json
  python scripts/analyze_rank_v2_mirofish_ablation.py --write-artifact
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.rank_v2_mirofish_ablation import (  # noqa: E402
    ablate_rank_v2_mirofish,
    parse_signals_payload,
)

LOG = logging.getLogger("analyze_rank_v2_mirofish_ablation")
ART = SKILL_DIR / "validation_artifacts"
DEFAULT_ARTIFACT = ART / "rank_v2_mirofish_ablation.json"
DEFAULT_SQLITE = SKILL_DIR / "webapp" / "webapp.db"


def _load_signals_json(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return parse_signals_payload(payload)


def _load_last_scan_signals(sqlite_path: Path) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    meta: dict[str, Any] = {"source": "sqlite", "path": str(sqlite_path)}
    if not sqlite_path.exists():
        meta["error"] = "sqlite_missing"
        return None, meta
    try:
        from core.pead_primary_shadow_compare import load_scan_bundle
    except Exception as exc:
        meta["error"] = str(exc)
        return None, meta
    _diag, signals, bundle_meta = load_scan_bundle(sqlite_path=sqlite_path)
    meta.update(bundle_meta or {})
    if not isinstance(signals, list):
        meta.setdefault("error", bundle_meta.get("error") if isinstance(bundle_meta, dict) else "no last_scan")
        return None, meta
    parsed = [row for row in signals if isinstance(row, dict)]
    return parsed, meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--signals-json",
        help="Fixture JSON: a signal list, {signals: [...]}, or {last_scan: {signals: [...]}}",
    )
    parser.add_argument(
        "--sqlite",
        default=str(DEFAULT_SQLITE),
        help="Local dashboard SQLite path (default: webapp/webapp.db)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Diagnostic top-N window (default: SIGNAL_TOP_N, or 10 if uncapped)",
    )
    parser.add_argument(
        "--write-artifact",
        action="store_true",
        help="Write validation_artifacts/rank_v2_mirofish_ablation.json",
    )
    parser.add_argument(
        "--require-scan",
        action="store_true",
        help="Fail when no last_scan / signals JSON is available (default: skip exit 0)",
    )
    args = parser.parse_args(argv)

    source = "unknown"
    source_meta: dict[str, Any] = {}
    signals: list[dict[str, Any]] | None = None

    if args.signals_json:
        path = Path(args.signals_json)
        if not path.exists():
            msg = f"signals JSON not found: {path}"
            print(f"FAIL: {msg}")
            LOG.error("Signals JSON missing: %s", path)
            return 1
        try:
            signals = _load_signals_json(path)
        except Exception as exc:
            print(f"FAIL: could not parse signals JSON: {exc}")
            LOG.exception("Failed to parse signals JSON")
            return 1
        source = "signals_json"
        source_meta = {"path": str(path)}
    else:
        sqlite_path = Path(args.sqlite)
        signals, source_meta = _load_last_scan_signals(sqlite_path)
        source = "last_scan"

    if signals is None:
        msg = source_meta.get("error") or "no last_scan signals"
        if args.require_scan:
            print(f"FAIL: {msg}")
            return 1
        print(f"SKIP: {msg}")
        LOG.info("Skip ablation: %s", msg)
        if args.write_artifact:
            report = {
                "ok": True,
                "mode": "shadow_diagnostic_only",
                "verdict": "skip",
                "changes_live_rank": False,
                "changes_fills": False,
                "source": source,
                "source_meta": source_meta,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "error": msg,
            }
            ART.mkdir(parents=True, exist_ok=True)
            DEFAULT_ARTIFACT.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Wrote {DEFAULT_ARTIFACT}")
        return 0

    report = ablate_rank_v2_mirofish(signals, top_n=args.top_n, skill_dir=SKILL_DIR)
    report["source"] = source
    report["source_meta"] = source_meta
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    if args.write_artifact:
        ART.mkdir(parents=True, exist_ok=True)
        DEFAULT_ARTIFACT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        report["artifact"] = str(DEFAULT_ARTIFACT)

    print(json.dumps(report, indent=2, default=str))
    overlap = report.get("top_n_overlap") or {}
    LOG.info(
        "MiroFish ablation n=%s spearman=%s enter=%s leave=%s mean_abs_rank_delta=%s missing_or_zero=%s",
        report.get("n_signals"),
        report.get("spearman"),
        overlap.get("enter_count"),
        overlap.get("leave_count"),
        report.get("mean_abs_rank_delta"),
        (report.get("mirofish_coverage") or {}).get("fraction_missing_or_zero"),
    )
    if args.write_artifact:
        print(f"Wrote {DEFAULT_ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
