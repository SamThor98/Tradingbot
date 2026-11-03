"""Append-only MiroFish model comparison log (shadow; no secrets)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

LOG = logging.getLogger(__name__)
MODEL_LOG_FILE = ".mirofish_model_log.jsonl"


def model_log_path(skill_dir: Path) -> Path:
    return skill_dir / MODEL_LOG_FILE


def append_mirofish_model_log(
    *,
    skill_dir: Path,
    ticker: str,
    result: Mapping[str, Any],
) -> None:
    """Record one simulation for later Grok vs baseline compare. Best-effort."""
    conviction = result.get("conviction_score")
    weighting = result.get("agent_weighting") if isinstance(result.get("agent_weighting"), dict) else {}
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ticker": str(ticker or "").upper(),
        "simulation_id": result.get("simulation_id"),
        "llm_provider": result.get("llm_provider"),
        "llm_model": result.get("llm_model"),
        "conviction_score": conviction,
        "mirofish_disagreement": result.get("mirofish_disagreement"),
        "continuation_probability": result.get("continuation_probability"),
        "bull_trap_probability": result.get("bull_trap_probability"),
        "weighting_mode": weighting.get("mode"),
        "weighting_applied": weighting.get("applied"),
        "seed_fingerprint": result.get("seed_fingerprint"),
        "unavailable_reason": result.get("unavailable_reason"),
    }
    path = model_log_path(skill_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    except Exception as exc:
        LOG.debug("MiroFish model log write skipped: %s", exc)


def load_mirofish_model_log(skill_dir: Path) -> list[dict[str, Any]]:
    path = model_log_path(skill_dir)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    except OSError:
        return []
    return rows


def compare_mirofish_model_log(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pair rows that share ticker + seed_fingerprint but differ by llm_model."""
    by_key: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        fingerprint = str(row.get("seed_fingerprint") or "")
        model = str(row.get("llm_model") or "")
        if not ticker or not fingerprint or not model:
            continue
        if row.get("conviction_score") is None:
            continue
        bucket = by_key.setdefault((ticker, fingerprint), {})
        bucket.setdefault(model, []).append(row)

    pairs: list[dict[str, Any]] = []
    for (ticker, fingerprint), models in by_key.items():
        names = sorted(models)
        if len(names) < 2:
            continue
        baseline = names[0]
        for other in names[1:]:
            a = models[baseline][-1]
            b = models[other][-1]
            try:
                delta = float(b.get("conviction_score")) - float(a.get("conviction_score"))
            except (TypeError, ValueError):
                continue
            pairs.append(
                {
                    "ticker": ticker,
                    "seed_fingerprint": fingerprint,
                    "baseline_model": baseline,
                    "challenger_model": other,
                    "baseline_conviction": a.get("conviction_score"),
                    "challenger_conviction": b.get("conviction_score"),
                    "conviction_delta": round(delta, 4),
                    "baseline_provider": a.get("llm_provider"),
                    "challenger_provider": b.get("llm_provider"),
                }
            )

    n = len(pairs)
    mean_abs = round(sum(abs(float(p["conviction_delta"])) for p in pairs) / n, 4) if n else 0.0
    return {
        "paired_rows": n,
        "mean_abs_conviction_delta": mean_abs,
        "pairs": pairs,
    }
