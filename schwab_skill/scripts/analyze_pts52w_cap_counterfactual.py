#!/usr/bin/env python3
"""Offline pts_52w cap counterfactual on multi-era chunk trades (P0 bare-signal).

Does NOT re-run backtests. Filters existing ``stage2_only_aug`` (default) trades
by ``pts_52w <= cap`` and reports five-era PF mean / worst-era vs promotion gates.

Usage (from schwab_skill/):
  python scripts/analyze_pts52w_cap_counterfactual.py
  python scripts/analyze_pts52w_cap_counterfactual.py --run-id stage2_only_aug --cap 37
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.analyze_rank_filter_counterfactual import _safe_pf  # noqa: E402
from scripts.phase2_common import CHUNKS_DIR, ERA_BOUNDS  # noqa: E402

ART = SKILL_DIR / "validation_artifacts"
ALL_ERAS = list(ERA_BOUNDS.keys())
PF_MEAN_FLOOR = 1.20
WORST_ERA_FLOOR = 1.00
RETENTION_FLOOR_PCT = 50.0
MIN_MEAN_LIFT = 0.02


def _trades_frame(run_id: str) -> pd.DataFrame:
    base = CHUNKS_DIR / run_id
    rows: list[dict[str, Any]] = []
    for era in ALL_ERAS:
        era_dir = base / era
        if not era_dir.exists():
            continue
        for chunk_path in sorted(era_dir.glob("chunk_*.json")):
            if chunk_path.name.endswith("_tickers.json"):
                continue
            try:
                payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for raw in payload.get("trades") or []:
                pts = raw.get("pts_52w")
                try:
                    pts_f = float(pts) if pts is not None else None
                except (TypeError, ValueError):
                    pts_f = None
                net = raw.get("net_return")
                if net is None:
                    net = raw.get("return", 0.0)
                rows.append(
                    {
                        "era": era,
                        "ticker": str(raw.get("ticker") or "").upper(),
                        "net_return": float(net or 0.0),
                        "pts_52w": pts_f,
                    }
                )
    return pd.DataFrame(rows)


def _era_pf(df: pd.DataFrame) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for era in ALL_ERAS:
        sub = df[df["era"] == era]
        out[era] = _safe_pf(sub["net_return"]) if len(sub) else None
    return out


def _pf_mean(epf: dict[str, float | None]) -> float | None:
    vals = [v for e in ALL_ERAS if (v := epf.get(e)) is not None and math.isfinite(v)]
    return sum(vals) / len(vals) if vals else None


def _decide(
    baseline_mean: float,
    kept_mean: float,
    kept_worst: float,
    retention: float,
) -> dict[str, Any]:
    reasons: list[str] = []
    pass_gates = kept_mean >= PF_MEAN_FLOOR and kept_worst >= WORST_ERA_FLOOR
    lift = kept_mean - baseline_mean
    if kept_worst < WORST_ERA_FLOOR:
        reasons.append(f"worst-era PF {kept_worst:.4f} < {WORST_ERA_FLOOR}")
    if retention < RETENTION_FLOOR_PCT:
        reasons.append(f"retention {retention:.1f}% < {RETENTION_FLOOR_PCT}%")
    if lift < MIN_MEAN_LIFT:
        reasons.append(f"PF mean lift {lift:+.4f} < +{MIN_MEAN_LIFT}")
    if not pass_gates:
        reasons.append("promotion floors not cleared")
        action = "kill_or_revise"
    elif reasons:
        action = "keep_shadow_only"
    else:
        action = "pass_offline_cf_ready_for_multi_era_live"
    return {
        "action": action,
        "pass_promotion_floors": pass_gates,
        "pf_mean_lift": round(lift, 4),
        "reasons": reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="pts_52w cap counterfactual on chunk trades")
    parser.add_argument("--run-id", default="stage2_only_aug")
    parser.add_argument("--cap", type=float, default=37.0)
    args = parser.parse_args()

    df = _trades_frame(args.run_id)
    if df.empty:
        print(f"No trades for run_id={args.run_id}", file=sys.stderr)
        return 1

    base_epf = _era_pf(df)
    base_mean = _pf_mean(base_epf) or 0.0
    base_worst = min(v for v in base_epf.values() if v is not None)

    scored = df[df["pts_52w"].notna()]
    kept = scored[scored["pts_52w"] <= float(args.cap)]
    kept_epf = _era_pf(kept)
    kept_mean = _pf_mean(kept_epf) or 0.0
    kept_worst = min(v for v in kept_epf.values() if v is not None)
    retention = 100.0 * len(kept) / len(df)

    decision = _decide(base_mean, kept_mean, kept_worst, retention)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "cap": float(args.cap),
        "baseline": {
            "n": int(len(df)),
            "pf_mean": round(base_mean, 4),
            "worst_era_pf": round(float(base_worst), 4),
            "era_pf": {k: (round(v, 4) if v is not None else None) for k, v in base_epf.items()},
        },
        "kept": {
            "n": int(len(kept)),
            "retention_pct": round(retention, 2),
            "pf_mean": round(kept_mean, 4),
            "worst_era_pf": round(float(kept_worst), 4),
            "era_pf": {k: (round(v, 4) if v is not None else None) for k, v in kept_epf.items()},
        },
        "gates": {"pf_mean_floor": PF_MEAN_FLOOR, "worst_era_floor": WORST_ERA_FLOOR},
        "decision": decision,
        "note": (
            "Chunk trade CF only. Scanner default is PTS_52W_CAP_MODE=shadow. "
            "Set live only for a dedicated multi-era bare re-run before changing fills."
        ),
    }

    ART.mkdir(parents=True, exist_ok=True)
    out_json = ART / f"pts52w_cap_cf_{args.run_id}_cap{int(args.cap)}.json"
    out_md = ART / f"pts52w_cap_cf_{args.run_id}_cap{int(args.cap)}.md"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        f"# pts_52w cap CF — `{args.run_id}` @ {args.cap}",
        "",
        f"Generated: {report['generated_at']}",
        "",
        f"- Baseline: n={report['baseline']['n']} PF mean={report['baseline']['pf_mean']} "
        f"worst={report['baseline']['worst_era_pf']}",
        f"- Kept: n={report['kept']['n']} retention={report['kept']['retention_pct']}% "
        f"PF mean={report['kept']['pf_mean']} worst={report['kept']['worst_era_pf']}",
        f"- Decision: **{decision['action']}**",
        f"- Reasons: {decision['reasons'] or 'none'}",
        "",
        report["note"],
        "",
    ]
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    print(
        f"Decision: {decision['action']} | "
        f"kept PF mean={kept_mean:.4f} worst={kept_worst:.4f} retention={retention:.1f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
