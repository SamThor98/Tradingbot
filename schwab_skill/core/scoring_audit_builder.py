"""Build live-parity score stacks for offline scoring audit rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

COMPONENT_COLUMNS = ("pts_52w", "pts_sma", "pts_volume", "pts_mirofish")
STACK_COLUMNS = (
    "edge_score",
    "reliability_score",
    "execution_score",
    "composite_score",
    "rank_score",
    "p_up_calibrated",
    "ev_10d",
)
ENRICHED_COLUMNS = COMPONENT_COLUMNS + STACK_COLUMNS


def _sec_score_to_tag(score: float) -> str:
    if score >= 0.67:
        return "high"
    if score >= 0.33:
        return "medium"
    if score <= -0.67:
        return "high"
    return "unknown"


def _reconstruct_components(row: pd.Series, *, stage2_floor: float) -> dict[str, Any]:
    floor_span = max(0.01, 1.0 - stage2_floor)
    pct = float(row.get("pct_from_52w_high") or 0.0)
    sma_pct = float(row.get("close_vs_sma200_pct") or 0.0)
    vol_ratio = float(row.get("avg_vcp_volume_ratio") or 1.0)
    pts_52w = max(0.0, (pct - stage2_floor) / floor_span) * 40.0
    pts_sma = min(25.0, max(0.0, sma_pct * 100.0))
    pts_volume = max(0.0, 20.0 - vol_ratio * 20.0)
    signal_score = float(row.get("signal_score") or 0.0)
    pts_mirofish = max(0.0, min(15.0, signal_score - pts_52w - pts_sma - pts_volume))
    return {
        "pts_52w": round(pts_52w, 2),
        "pts_sma": round(pts_sma, 2),
        "pts_volume": round(pts_volume, 2),
        "pts_mirofish": round(pts_mirofish, 2),
        "pct_from_52w_high": pct,
        "avg_vcp_volume_ratio": vol_ratio,
    }


def signal_dict_from_audit_row(row: pd.Series, *, skill_dir: Path, stage2_floor: float) -> dict[str, Any]:
    """Reconstruct a scanner-parity signal row from an advisory audit CSV row."""
    price = 100.0
    close_vs_sma200 = float(row.get("close_vs_sma200_pct") or 0.0)
    close_vs_sma50 = float(row.get("close_vs_sma50_pct") or 0.0)
    sma200 = price / (1.0 + close_vs_sma200) if close_vs_sma200 > -0.99 else price
    sma50 = price / (1.0 + close_vs_sma50) if close_vs_sma50 > -0.99 else price
    avg_vol = 1_000_000.0
    latest_vol = max(1.0, float(row.get("volume_ratio") or 1.0) * avg_vol)

    for col in COMPONENT_COLUMNS:
        if col in row.index and pd.notna(row.get(col)):
            components = {
                "pts_52w": float(row.get("pts_52w") or 0.0),
                "pts_sma": float(row.get("pts_sma") or 0.0),
                "pts_volume": float(row.get("pts_volume") or 0.0),
                "pts_mirofish": float(row.get("pts_mirofish") or 0.0),
                "pct_from_52w_high": float(row.get("pct_from_52w_high") or 0.0),
                "avg_vcp_volume_ratio": float(row.get("avg_vcp_volume_ratio") or 1.0),
            }
            break
    else:
        components = _reconstruct_components(row, stage2_floor=stage2_floor)

    return {
        "ticker": str(row.get("ticker") or "").upper(),
        "signal_score": float(row.get("signal_score") or 0.0),
        "price": price,
        "sma_50": sma50,
        "sma_200": sma200,
        "latest_volume": latest_vol,
        "avg_vol_50": avg_vol,
        "sector_rel_21d": float(row.get("sector_rel_21d") or 0.0),
        "score_components": components,
        "breakout_confirmed": bool(int(row.get("breakout_confirmed") or 0)),
        "mirofish_result": {
            "continuation_probability": float(row.get("miro_continuation_prob") or 0.5),
            "bull_trap_probability": float(row.get("miro_bull_trap_prob") or 0.5),
        },
        "sec_risk_tag": _sec_score_to_tag(float(row.get("sec_risk_score") or 0.0)),
        "forensic_flags": [],
        "data_provider": "audit_dataset",
        "data_provider_primary": True,
        "used_fallback_data": False,
    }


def enrich_with_live_score_stack(df: pd.DataFrame, skill_dir: Path | str | None = None) -> pd.DataFrame:
    """Apply advisory model + ``_apply_score_stack`` for live-parity stack scores."""
    from config import get_stage2_52w_pct
    from signal_scanner import _apply_score_stack

    skill_dir_p = Path(skill_dir or Path(__file__).resolve().parent.parent)
    stage2_floor = float(get_stage2_52w_pct(skill_dir_p))
    try:
        from advisory_model import score_signal_advisory
    except Exception:
        score_signal_advisory = None  # type: ignore[assignment,misc]

    out_rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        base = row.to_dict()
        signal = signal_dict_from_audit_row(row, skill_dir=skill_dir_p, stage2_floor=stage2_floor)
        if score_signal_advisory is not None:
            try:
                advisory = score_signal_advisory(signal, skill_dir=skill_dir_p)
                if advisory is not None:
                    signal["advisory"] = advisory.to_dict()
            except Exception:
                pass
        try:
            signal = _apply_score_stack(signal, skill_dir=skill_dir_p)
        except Exception:
            out_rows.append(base)
            continue

        for col in COMPONENT_COLUMNS:
            comp_val = (signal.get("score_components") or {}).get(col)
            if comp_val is not None:
                base[col] = comp_val
        for col in STACK_COLUMNS:
            if signal.get(col) is not None:
                base[col] = signal.get(col)
        base["score_stack_source"] = "live"
        out_rows.append(base)

    enriched = pd.DataFrame(out_rows)
    enriched["score_stack_source"] = enriched.get("score_stack_source", "live")
    return enriched


def has_live_stack(df: pd.DataFrame) -> bool:
    for col in ("composite_score", "rank_score"):
        if col in df.columns and df[col].notna().sum() >= max(30, int(len(df) * 0.5)):
            return True
    return False
