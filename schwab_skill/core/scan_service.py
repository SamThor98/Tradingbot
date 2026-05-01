from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signal_scanner import scan_for_signals_detailed


@dataclass(frozen=True)
class ScanRunResult:
    signals: list[dict[str, Any]]
    diagnostics: dict[str, Any]


def run_scan(
    *,
    skill_dir: Path,
    env_overrides: dict[str, str] | None = None,
    watchlist_override: list[str] | None = None,
) -> ScanRunResult:
    signals, diagnostics = scan_for_signals_detailed(
        skill_dir=skill_dir,
        env_overrides=env_overrides,
        watchlist_override=watchlist_override,
    )
    return ScanRunResult(signals=signals, diagnostics=diagnostics)


def summarize_live_strategy(signals: list[dict[str, Any]] | None) -> dict[str, Any]:
    rows = signals or []
    counts: dict[str, int] = {}
    for sig in rows:
        attr = sig.get("strategy_attribution") if isinstance(sig, dict) else None
        name = str((attr or {}).get("top_live") or "unknown")
        counts[name] = int(counts.get(name, 0) or 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    dominant = ranked[0][0] if ranked else None
    dominant_count = ranked[0][1] if ranked else 0
    return {
        "dominant_live_strategy": dominant,
        "dominant_count": dominant_count,
        "total_ranked": len(rows),
        "counts": {k: v for k, v in ranked},
    }
