"""Runtime adapter for PROB_RANK_MODE (off / shadow / live)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from research.feature_engine import compute_feature_row, window_asof
from research.infer import attach_prob_rank_block
from research.paths import models_dir
from research.train import load_model_artifact

LOG = logging.getLogger(__name__)

_MODEL_CACHE: dict[str, Any] = {"key": None, "artifact": None}


def resolve_model_dir(skill_dir: Path | None = None, override: str | None = None) -> Path | None:
    """Resolve model directory from override or newest research_store/models/*/artifact.json."""
    if override and str(override).strip():
        path = Path(str(override).strip())
        if not path.is_absolute() and skill_dir is not None:
            path = Path(skill_dir) / path
        if (path / "artifact.json").is_file() and (path / "model.txt").is_file():
            return path
        LOG.warning("PROB_RANK_MODEL_DIR invalid or incomplete: %s", path)
        return None
    root = models_dir(skill_dir)
    if not root.exists():
        return None
    candidates = [p for p in root.iterdir() if p.is_dir() and (p / "artifact.json").is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def get_cached_artifact(skill_dir: Path | None = None, model_dir: str | None = None) -> dict[str, Any] | None:
    path = resolve_model_dir(skill_dir, model_dir)
    if path is None:
        return None
    key = str(path.resolve())
    if _MODEL_CACHE.get("key") == key and _MODEL_CACHE.get("artifact") is not None:
        return _MODEL_CACHE["artifact"]
    try:
        artifact = load_model_artifact(path)
    except Exception as exc:
        LOG.warning("Failed to load prob-rank model from %s: %s", path, exc)
        return None
    _MODEL_CACHE["key"] = key
    _MODEL_CACHE["artifact"] = artifact
    return artifact


def clear_model_cache() -> None:
    _MODEL_CACHE["key"] = None
    _MODEL_CACHE["artifact"] = None


def score_signal_with_bars(
    signal: dict[str, Any],
    bars: pd.DataFrame,
    *,
    skill_dir: Path | None = None,
    asof_date: str | pd.Timestamp | None = None,
    include_shap: bool | None = None,
    artifact: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Attach ``prob_rank`` (+ expected_return fields) onto ``signal`` using PIT bars.

    Returns the prob_rank block, or None if scoring failed.
    """
    from config import get_prob_rank_include_shap, get_prob_rank_model_dir

    art = artifact or get_cached_artifact(skill_dir, get_prob_rank_model_dir(skill_dir))
    if art is None:
        return None
    if asof_date is None:
        pit = window_asof(bars, bars.index[-1])
        asof = str(pit.index[-1].date())
    else:
        asof = str(pd.Timestamp(asof_date).date())
        pit = window_asof(bars, asof)
    ticker = str(signal.get("ticker") or "").upper()
    extras = {
        "signal_score": signal.get("signal_score"),
        "rank_score_v2": signal.get("rank_score_v2"),
        "sector_rel_21d": signal.get("sector_rel_21d"),
        "pead_surprise_pct": signal.get("pead_surprise_pct"),
        "forensic_sloan": signal.get("forensic_sloan"),
        "sec_risk_score": signal.get("sec_risk_score"),
        "advisory_p_up_10d": (signal.get("advisory") or {}).get("p_up_10d") or signal.get("p_up_calibrated"),
    }
    row = compute_feature_row(
        ticker=ticker,
        df=pit,
        asof_date=asof,
        extras={k: v for k, v in extras.items() if v is not None},
        require_stage2=False,
        skill_dir=skill_dir,
        bar_provider=str(signal.get("data_provider") or "") or None,
    )
    if row is None:
        return None
    shap = bool(get_prob_rank_include_shap(skill_dir)) if include_shap is None else bool(include_shap)
    block = attach_prob_rank_block(art, row, include_shap=shap)
    signal["prob_rank"] = block
    signal["expected_return_40d"] = block.get("expected_return_40d")
    signal["prob_rank_confidence"] = block.get("confidence")
    signal["prob_rank_model_id"] = block.get("model_id")
    try:
        from config import get_prob_rank_kelly_cap, get_prob_rank_sizing_mode
        from research.portfolio import size_multiplier_for_signal

        sizing_mode = get_prob_rank_sizing_mode(skill_dir)
        mult = size_multiplier_for_signal(
            signal,
            mode=sizing_mode,
            kelly_cap=get_prob_rank_kelly_cap(skill_dir),
        )
        signal["prob_rank_size_multiplier"] = mult
        block["size_multiplier"] = mult
        block["sizing_mode"] = sizing_mode
        signal["prob_rank"] = block
    except Exception:
        pass
    return block


def apply_prob_rank_cohort(
    signals: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    skill_dir: Path,
) -> list[dict[str, Any]]:
    """
    Cohort-level prob-rank: cross-sectional ranks; live mode keeps top-N.

    Shadow/off: never drops. Live: keeps top ``PROB_RANK_TOP_N`` by expected_return_40d.
    Signals without scores are kept in shadow; demoted in live (placed after scored).
    """
    from config import get_prob_rank_mode, get_prob_rank_top_n

    mode = get_prob_rank_mode(skill_dir)
    diagnostics["prob_rank_mode"] = mode
    if mode == "off" or not signals:
        return signals

    scored = [s for s in signals if s.get("expected_return_40d") is not None]
    diagnostics["prob_rank_scored"] = len(scored)
    diagnostics["prob_rank_unscored"] = len(signals) - len(scored)
    if not scored:
        diagnostics["prob_rank_skipped"] = "no_scores"
        return signals

    ordered = sorted(scored, key=lambda s: float(s.get("expected_return_40d") or -1e9), reverse=True)
    n = len(ordered)
    for i, signal in enumerate(ordered):
        rank = i + 1
        block = signal.get("prob_rank") if isinstance(signal.get("prob_rank"), dict) else {}
        block = dict(block)
        block["cross_section_rank"] = rank
        block["cross_section_n"] = n
        block["mode"] = mode
        signal["prob_rank"] = block
        signal["prob_rank_cross_section_rank"] = rank

    top_n = max(1, int(get_prob_rank_top_n(skill_dir)))
    diagnostics["prob_rank_top_n"] = top_n
    would_keep_ids = {id(s) for s in ordered[:top_n]}
    diagnostics["prob_rank_would_keep"] = len(would_keep_ids)
    diagnostics["prob_rank_would_drop"] = max(0, n - top_n)
    for signal in signals:
        block = signal.get("prob_rank") if isinstance(signal.get("prob_rank"), dict) else {}
        if signal.get("expected_return_40d") is None:
            signal["prob_rank_selection"] = {"mode": mode, "would_keep": mode != "live", "reason": "unscored"}
            continue
        keep = id(signal) in would_keep_ids
        signal["prob_rank_selection"] = {
            "mode": mode,
            "would_keep": keep,
            "rank": block.get("cross_section_rank"),
            "top_n": top_n,
        }

    if mode != "live":
        return signals

    # Live: keep top-N scored; drop other scored; keep unscored only if no scores? drop unscored
    kept = [s for s in ordered[:top_n]]
    diagnostics["prob_rank_dropped"] = len(signals) - len(kept)
    # Prefer prob-rank sort for remaining pipeline
    for s in kept:
        er = s.get("expected_return_40d")
        if er is not None:
            s["sort_score"] = float(er)
    return kept


def score_candidates_for_backtest_day(
    signal: dict[str, Any],
    bars: pd.DataFrame,
    asof_date: str | pd.Timestamp,
    *,
    skill_dir: Path | None = None,
) -> None:
    """Best-effort shadow/live scoring for a backtest candidate (mutates signal)."""
    from config import get_prob_rank_mode

    if get_prob_rank_mode(skill_dir) == "off":
        return
    try:
        score_signal_with_bars(
            signal,
            bars,
            skill_dir=skill_dir,
            asof_date=asof_date,
            include_shap=False,
        )
    except Exception as exc:
        LOG.debug("prob-rank backtest score skipped for %s: %s", signal.get("ticker"), exc)
