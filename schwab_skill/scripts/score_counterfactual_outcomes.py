#!/usr/bin/env python3
"""Score historical counterfactual events.

Counterfactual events are written by `agent_intelligence.log_counterfactual_event`
when a layer suppressed, would-have-suppressed, or (after this loop closed)
emitted a signal. This script reads `.counterfactual_log.jsonl`, looks up the
forward return for each ticker over the configured horizon, and emits a
summary artifact under `validation_artifacts/counterfactual_scoring_<ts>.json`.

Closing this loop is the prerequisite for promoting `META_POLICY_MODE` and
`UNCERTAINTY_MODE` from shadow to live.

Usage:
    python scripts/score_counterfactual_outcomes.py
    python scripts/score_counterfactual_outcomes.py --horizon-days 5 --max-rows 500

Notes:
    * Forward returns come from yfinance (best effort) when the local market
      data layer is not configured for historical bars; the script degrades
      gracefully when prices are missing.
    * Only events whose timestamp is at least ``horizon_days`` in the past are
      scored; younger events are reported as ``status="pending"``.
    * ``quality_filtered`` and ``event_risk_blocked`` are suppressed (they did
      not trade). ``fired`` is the emitted control. ``meta_policy_would_downsize``
      is a third bucket so size clamps are not mixed with hard drops.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
LOG_FILE = SKILL_DIR / ".counterfactual_log.jsonl"

SUPPRESSED_REASONS = frozenset(
    {
        "quality_filtered",
        "event_risk_blocked",
        "meta_policy_suppressed",
        "meta_policy_would_suppress",
        "uncertainty_suppressed",
        "uncertainty_would_suppress",
    }
)
DOWNSIZED_REASONS = frozenset(
    {
        "meta_policy_would_downsize",
        "meta_policy_downsized",
        "uncertainty_would_downsize",
    }
)
FIRED_REASONS = frozenset({"fired", "emitted"})
WOULD_SKIP_REASONS = frozenset({"debate_grade_would_skip"})


def classify_counterfactual_bucket(reason: str) -> str:
    """Map a log reason to suppressed / downsized / fired / other."""
    key = str(reason or "").strip().lower()
    if key in SUPPRESSED_REASONS or key.startswith(("meta_policy_would_suppress", "meta_policy_suppress")):
        return "suppressed"
    if key in DOWNSIZED_REASONS or key.startswith("meta_policy_would_downsize"):
        return "downsized"
    if key in FIRED_REASONS:
        return "fired"
    if key in WOULD_SKIP_REASONS or key.startswith("debate_grade_would_skip"):
        return "would_skip"
    return "other"


def bucket_stats(name: str, values: list[float]) -> dict[str, Any]:
    if not values:
        return {"name": name, "count": 0}
    avg = sum(values) / len(values)
    wins = sum(1 for v in values if v > 0)
    return {
        "name": name,
        "count": len(values),
        "avg_return": round(avg, 5),
        "win_rate": round(wins / len(values), 4),
        "min": round(min(values), 5),
        "max": round(max(values), 5),
    }


def _load_events(path: Path, max_rows: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if max_rows and len(out) > max_rows:
        out = out[-max_rows:]
    return out


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _forward_return_from_closes(closes: list[tuple[datetime, float]], anchor: datetime, horizon_days: int) -> float | None:
    if len(closes) < 2:
        return None
    after = [(ts, px) for ts, px in closes if ts.date() >= anchor.date()]
    if len(after) < 2:
        return None
    anchor_close = float(after[0][1])
    target_idx = min(horizon_days, len(after) - 1)
    target_close = float(after[target_idx][1])
    if anchor_close <= 0:
        return None
    return (target_close - anchor_close) / anchor_close


class _YahooCloseCache:
    """One download per ticker covering the min/max event window."""

    def __init__(self) -> None:
        self._closes: dict[str, list[tuple[datetime, float]] | None] = {}
        self._ranges: dict[str, tuple[datetime, datetime]] = {}

    def prepare(self, ticker: str, start: datetime, end: datetime) -> None:
        key = ticker.upper()
        prev = self._ranges.get(key)
        if prev is None:
            self._ranges[key] = (start, end)
            return
        self._ranges[key] = (min(prev[0], start), max(prev[1], end))

    def closes(self, ticker: str) -> list[tuple[datetime, float]] | None:
        key = ticker.upper()
        if key in self._closes:
            return self._closes[key]
        window = self._ranges.get(key)
        if window is None:
            self._closes[key] = None
            return None
        start, end = window
        series = self._download(key, start, end)
        self._closes[key] = series
        return series

    @staticmethod
    def _closes_from_frame(df: Any) -> list[tuple[datetime, float]] | None:
        if df is None or getattr(df, "empty", True) or "Close" not in getattr(df, "columns", []):
            return None
        closes = df["Close"]
        if getattr(closes, "ndim", 1) > 1:
            closes = closes.iloc[:, 0]
        closes = closes.dropna()
        out: list[tuple[datetime, float]] = []
        for idx, value in closes.items():
            ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else datetime.fromisoformat(str(idx)[:10])
            if getattr(ts, "tzinfo", None) is not None:
                ts = ts.replace(tzinfo=None)
            out.append((ts, float(value)))
        return out or None

    @staticmethod
    def _download(ticker: str, start: datetime, end: datetime) -> list[tuple[datetime, float]] | None:
        try:
            import yfinance as yf  # type: ignore
        except Exception:
            return None
        start_s = start.date().isoformat()
        end_s = (end + timedelta(days=1)).date().isoformat()
        df = None
        try:
            df = yf.download(
                ticker,
                start=start_s,
                end=end_s,
                progress=False,
                auto_adjust=False,
            )
        except Exception:
            df = None
        parsed = _YahooCloseCache._closes_from_frame(df)
        if parsed:
            return parsed
        try:
            hist = yf.Ticker(ticker).history(start=start_s, end=end_s, auto_adjust=False)
        except Exception:
            return None
        return _YahooCloseCache._closes_from_frame(hist)


def score_events(
    events: list[dict[str, Any]],
    *,
    horizon_days: int,
    now: datetime | None = None,
    forward_fn: Callable[[str, datetime, int], float | None] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=horizon_days)
    cache = _YahooCloseCache() if forward_fn is None else None
    if cache is not None:
        for ev in events:
            ts = _parse_ts(str(ev.get("ts") or ""))
            ticker = str(ev.get("ticker") or "").upper()
            if not ts or not ticker or ts > cutoff:
                continue
            # Pad past weekends / holidays so T+1 from a Friday can still
            # resolve the next regular session (e.g. Labor Day weeks).
            cache.prepare(ticker, ts, ts + timedelta(days=max(horizon_days * 3, 10)))

    def _lookup(ticker: str, anchor: datetime) -> float | None:
        if forward_fn is not None:
            return forward_fn(ticker, anchor, horizon_days)
        assert cache is not None
        return _forward_return_from_closes(cache.closes(ticker) or [], anchor, horizon_days)

    scored: list[dict[str, Any]] = []
    bucket_returns: dict[str, list[float]] = defaultdict(list)
    reason_returns: dict[str, list[float]] = defaultdict(list)

    for ev in events:
        ts = _parse_ts(str(ev.get("ts") or ""))
        ticker = str(ev.get("ticker") or "").upper()
        reason = str(ev.get("reason") or "")
        if not ts or not ticker:
            continue
        if ts > cutoff:
            scored.append({**ev, "status": "pending"})
            continue
        ret = _lookup(ticker, ts)
        if ret is None:
            scored.append({**ev, "status": "missing_data"})
            continue
        bucket = classify_counterfactual_bucket(reason)
        scored.append({**ev, "status": "scored", "forward_return": ret, "bucket": bucket})
        bucket_returns[bucket].append(ret)
        reason_returns[reason or "unknown"].append(ret)

    suppressed = bucket_returns["suppressed"]
    fired = bucket_returns["fired"]
    summary = {
        "generated_at": now.isoformat(),
        "horizon_days": int(horizon_days),
        "totals": {
            "events": len(events),
            "scored": sum(1 for e in scored if e.get("status") == "scored"),
            "pending": sum(1 for e in scored if e.get("status") == "pending"),
            "missing_data": sum(1 for e in scored if e.get("status") == "missing_data"),
        },
        "buckets": {
            "suppressed": bucket_stats("suppressed", suppressed),
            "downsized": bucket_stats("downsized", bucket_returns["downsized"]),
            "fired": bucket_stats("fired", fired),
            "would_skip": bucket_stats("would_skip", bucket_returns["would_skip"]),
            "other": bucket_stats("other", bucket_returns["other"]),
        },
        "by_reason": {reason: bucket_stats(reason, vals) for reason, vals in sorted(reason_returns.items())},
        # Positive lift means fired beat suppressed — policy dropped worse names.
        "policy_lift_vs_fired": (
            round((sum(fired) / len(fired)) - (sum(suppressed) / len(suppressed)), 5)
            if fired and suppressed
            else None
        ),
        "events": scored,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--out-dir", type=Path, default=ARTIFACT_DIR)
    args = parser.parse_args()

    events = _load_events(LOG_FILE, args.max_rows)
    if not events:
        print(f"No counterfactual events found at {LOG_FILE}", file=sys.stderr)
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = args.out_dir / "counterfactual_scoring_empty.json"
        out_path.write_text(json.dumps({"events": 0, "scored": 0}, indent=2), encoding="utf-8")
        return 0

    summary = score_events(events, horizon_days=args.horizon_days)
    summary["log_file"] = str(LOG_FILE)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = args.out_dir / f"counterfactual_scoring_{stamp}.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(json.dumps({k: v for k, v in summary.items() if k != "events"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
