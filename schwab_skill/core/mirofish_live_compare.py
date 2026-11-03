"""Live-vs-backtest MiroFish score-gap report (P0 backtest fidelity).

Live scans often fill ``pts_mirofish`` from LLM conviction; multi-era books
typically skip MiroFish via ``BACKTEST_SKIP_MIROFISH``. Rank-v2 still blends
mirofish at weight 0.15, so offline PF and live rank can diverge silently.

This module is read-only: it does not change live ranking or enable MiroFish
inside backtests.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.scoring_reliability import is_backtest_skip_mirofish

ARTIFACT_NAME = "live_mirofish_backtest_compare.json"
RANK_V2_MIROFISH_WEIGHT = 0.15

_SKIP_ACTIONS = {
    "skip": "No live scan signals to compare",
    "live_llm_backtest_skipped": "Do not compare live rank_v2 to BACKTEST_SKIP_MIROFISH books",
    "both_absent": "MiroFish is absent on both sides; rank_v2 mirofish weight does not create a live/backtest gap",
    "both_present": "Live and backtest both include MiroFish; confirm llm_model matches before comparing PF",
    "none": "Live scan has no MiroFish convictions while backtest would score them",
}


def default_backtest_skip_mirofish() -> bool:
    """Assume skip-mirofish books unless the env explicitly disables the skip."""
    raw = os.environ.get("BACKTEST_SKIP_MIROFISH")
    if raw is None or not str(raw).strip():
        return True
    return is_backtest_skip_mirofish()


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _signals_from_last_scan(last_scan: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if not isinstance(last_scan, dict):
        return None
    raw = last_scan.get("signals")
    if raw is None:
        nested = last_scan.get("last_scan")
        if isinstance(nested, dict):
            raw = nested.get("signals")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    return [row for row in raw if isinstance(row, dict)]


def _diagnostics_from_last_scan(last_scan: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(last_scan, dict):
        return {}
    diag = last_scan.get("diagnostics")
    if isinstance(diag, dict):
        return diag
    nested = last_scan.get("last_scan")
    if isinstance(nested, dict) and isinstance(nested.get("diagnostics"), dict):
        return nested["diagnostics"]
    return {}


def _pts_mirofish(signal: dict[str, Any]) -> float:
    comps = signal.get("score_components")
    if isinstance(comps, dict) and comps.get("pts_mirofish") is not None:
        pts = _safe_float(comps.get("pts_mirofish"))
        return pts if pts is not None else 0.0
    pts = _safe_float(signal.get("pts_mirofish"))
    return pts if pts is not None else 0.0


def signal_has_live_mirofish(signal: dict[str, Any]) -> bool:
    """True when the live row carries LLM conviction or a non-zero mirofish component."""
    if signal.get("mirofish_conviction") is not None:
        return _safe_float(signal.get("mirofish_conviction")) is not None
    result = signal.get("mirofish_result")
    if isinstance(result, dict) and result.get("conviction_score") is not None:
        return _safe_float(result.get("conviction_score")) is not None
    return _pts_mirofish(signal) > 0.0


def _extract_llm_meta(
    last_scan: dict[str, Any] | None,
    signals: list[dict[str, Any]],
    diagnostics: dict[str, Any],
) -> tuple[str | None, str | None]:
    sources: list[dict[str, Any]] = []
    if isinstance(diagnostics, dict):
        sources.append(diagnostics)
        nested_llm = diagnostics.get("mirofish_llm")
        if isinstance(nested_llm, dict):
            sources.append(nested_llm)
    if isinstance(last_scan, dict):
        sources.append(last_scan)
    for signal in signals:
        sources.append(signal)
        result = signal.get("mirofish_result")
        if isinstance(result, dict):
            sources.append(result)

    model: str | None = None
    provider: str | None = None
    for src in sources:
        if model is None:
            model = _str_or_none(
                src.get("llm_model") or src.get("mirofish_llm_model") or src.get("model")
            )
        if provider is None:
            provider = _str_or_none(
                src.get("llm_provider") or src.get("mirofish_llm_provider") or src.get("provider")
            )
        if model and provider:
            break
    return model, provider


def _fidelity_gap(*, live_scored: int, backtest_skip_mirofish: bool) -> str:
    live_present = live_scored > 0
    backtest_present = not backtest_skip_mirofish
    if live_present and not backtest_present:
        return "live_llm_backtest_skipped"
    if live_present and backtest_present:
        return "both_present"
    if not live_present and not backtest_present:
        return "both_absent"
    return "none"


def _recommended_action(gap: str, *, llm_model: str | None) -> str:
    if gap == "live_llm_backtest_skipped":
        model = (llm_model or "").lower()
        if model.startswith("grok"):
            return "Do not compare live rank_v2 (Grok MiroFish) to BACKTEST_SKIP_MIROFISH books"
        return _SKIP_ACTIONS["live_llm_backtest_skipped"]
    return _SKIP_ACTIONS.get(gap, _SKIP_ACTIONS["none"])


def _verdict(
    *,
    n_signals: int | None,
    malformed: bool,
    gap: str,
) -> str:
    if malformed:
        return "fail"
    if n_signals is None or n_signals <= 0:
        return "skip"
    if gap == "live_llm_backtest_skipped":
        return "warn"
    if gap == "none":
        return "warn"
    return "pass"


def build_mirofish_live_compare_report(
    last_scan: dict[str, Any] | None,
    *,
    backtest_skip_mirofish: bool,
) -> dict[str, Any]:
    """Build a pure live-vs-backtest MiroFish fidelity report.

    Verdict rules:
    - skip: no signals (empty or missing last_scan)
    - warn: live has convictions while backtest skips MiroFish (or the inverse)
    - pass: both sides absent, or both sides present
    - fail: last_scan has a signals key that is not a list
    """
    malformed = isinstance(last_scan, dict) and "signals" in last_scan and not isinstance(
        last_scan.get("signals"), list
    )
    if (
        not malformed
        and isinstance(last_scan, dict)
        and isinstance(last_scan.get("last_scan"), dict)
        and "signals" in last_scan["last_scan"]
        and not isinstance(last_scan["last_scan"].get("signals"), list)
    ):
        malformed = True

    signals = _signals_from_last_scan(last_scan)
    diagnostics = _diagnostics_from_last_scan(last_scan)
    n_signals = None if signals is None else len(signals)
    if malformed:
        n_signals = None
        scored = 0
        missing = 0
        usable: list[dict[str, Any]] = []
    else:
        usable = signals or []
        scored = sum(1 for row in usable if signal_has_live_mirofish(row))
        missing = max(0, len(usable) - scored)

    gap = "none" if (malformed or n_signals is None or n_signals <= 0) else _fidelity_gap(
        live_scored=scored,
        backtest_skip_mirofish=bool(backtest_skip_mirofish),
    )
    llm_model, llm_provider = _extract_llm_meta(last_scan, usable if not malformed else [], diagnostics)
    signal_count = 0 if n_signals is None else n_signals
    verdict = _verdict(n_signals=signal_count, malformed=malformed, gap=gap)
    if malformed:
        verdict = "fail"
        gap = "none"
        action = "last_scan.signals is present but is not a list"
    elif signal_count <= 0:
        verdict = "skip"
        gap = "none"
        action = _SKIP_ACTIONS["skip"]
    else:
        action = _recommended_action(gap, llm_model=llm_model)

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "live_mirofish_scored": scored,
        "live_mirofish_missing": missing,
        "backtest_skip_mirofish": bool(backtest_skip_mirofish),
        "fidelity_gap": gap,
        "llm_model": llm_model,
        "llm_provider": llm_provider,
        "recommended_action": action,
        "n_signals": signal_count,
        "rank_v2_mirofish_weight": RANK_V2_MIROFISH_WEIGHT,
    }
    return report


def write_mirofish_live_compare_report(
    report: dict[str, Any],
    skill_dir: Path,
) -> Path:
    out = skill_dir / "validation_artifacts" / ARTIFACT_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return out


def load_last_scan(
    *,
    sqlite_path: Path | None = None,
    diagnostics_json: Path | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Load a last_scan-like dict from JSON or local SQLite AppState."""
    meta: dict[str, Any] = {"source": None}

    if diagnostics_json is not None and diagnostics_json.exists():
        try:
            payload = json.loads(diagnostics_json.read_text(encoding="utf-8"))
        except Exception as exc:
            return None, {"source": "diagnostics_json", "error": str(exc)}
        if not isinstance(payload, dict):
            return None, {"source": "diagnostics_json", "error": "invalid JSON object"}
        if isinstance(payload.get("last_scan"), dict):
            payload = payload["last_scan"]
        meta.update(
            {
                "source": "diagnostics_json",
                "path": str(diagnostics_json),
                "scan_at": payload.get("at"),
                "signals_found": payload.get("signals_found"),
            }
        )
        if "signals" in payload or "diagnostics" in payload:
            return payload, meta
        return {"diagnostics": payload, "signals": []}, meta

    if sqlite_path is not None and sqlite_path.exists():
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker

            from webapp.models import AppState

            engine = create_engine(
                f"sqlite:///{sqlite_path.resolve().as_posix()}",
                connect_args={"check_same_thread": False},
            )
            Session = sessionmaker(bind=engine)
            db = Session()
            try:
                row = (
                    db.query(AppState)
                    .filter(AppState.user_id == "local", AppState.key == "last_scan")
                    .first()
                )
                if not row:
                    return None, {"source": "sqlite", "path": str(sqlite_path), "error": "no last_scan row"}
                raw = row.value_json
                if not isinstance(raw, dict):
                    return None, {"source": "sqlite", "path": str(sqlite_path), "error": "last_scan not a dict"}
                meta.update(
                    {
                        "source": "sqlite",
                        "path": str(sqlite_path),
                        "scan_at": raw.get("at"),
                        "signals_found": raw.get("signals_found"),
                    }
                )
                return raw, meta
            finally:
                db.close()
        except Exception as exc:
            return None, {"source": "sqlite", "path": str(sqlite_path), "error": str(exc)}

    if sqlite_path is not None:
        return None, {"source": "sqlite", "path": str(sqlite_path), "error": "sqlite file missing"}
    return None, {"source": None, "error": "no live scan source provided"}
