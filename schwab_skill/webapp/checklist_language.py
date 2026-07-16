"""Plain-language additions for pre-trade checklist payloads."""

from __future__ import annotations

from typing import Any

_BLOCK_REASON_PLAIN: dict[str, str] = {
    "max_daily_trades_reached": "Daily live trade limit is already reached.",
    "event_risk_block": "A risk event (for example earnings) is blocking this buy.",
    "regime_v2_block": "Market regime score is below your entry threshold.",
}


def _plain_block_reasons(codes: list[str]) -> list[str]:
    out: list[str] = []
    for c in codes:
        s = str(c or "").strip()
        if not s:
            continue
        out.append(_BLOCK_REASON_PLAIN.get(s, f"Policy hold: {s.replace('_', ' ')}."))
    return out


def _checklist_lines(checklist: dict[str, Any]) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    est = checklist.get("risk_percent_estimate")
    if est is not None:
        lines.append(
            {
                "label": "Estimated order size",
                "value_plain": f"About {est}% of your max account cap for this trade.",
            }
        )
    sizing_rationale = str(checklist.get("position_sizing_rationale") or "").strip()
    if sizing_rationale:
        lines.append({"label": "Sizing rationale", "value_plain": sizing_rationale})
    rec_usd = checklist.get("recommended_notional_usd")
    staged_usd = checklist.get("estimated_notional_usd")
    if rec_usd is not None:
        staged_part = f" Staged trade: ~${float(staged_usd):,.0f}." if staged_usd is not None else ""
        lines.append(
            {
                "label": "Recommended budget",
                "value_plain": f"~${int(rec_usd):,} notional per sizing policy.{staged_part}",
            }
        )
    sector_etf = str(checklist.get("portfolio_sector_etf") or "").strip()
    sector_now = checklist.get("portfolio_sector_weight_pct")
    sector_after = checklist.get("portfolio_sector_weight_after_pct")
    if sector_etf and sector_now is not None:
        after_txt = f" → {sector_after}% after this buy" if sector_after is not None else ""
        lines.append(
            {
                "label": "Sector exposure",
                "value_plain": f"{sector_etf} currently {sector_now}% of portfolio{after_txt}.",
            }
        )
    max_d = checklist.get("max_daily_trades")
    live_t = checklist.get("live_trades_today")
    if max_d is not None:
        lt = live_t if live_t is not None else "—"
        lines.append(
            {
                "label": "Daily live trades",
                "value_plain": f"{lt} used today of {max_d} allowed.",
            }
        )
    er_raw: Any = checklist.get("event_risk")
    er: dict[str, Any] = er_raw if isinstance(er_raw, dict) else {}
    flagged = bool(er.get("flagged"))
    mode = str(er.get("mode") or "off")
    action = str(er.get("action") or "")
    if mode == "live":
        if flagged:
            lines.append(
                {
                    "label": "Earnings / event risk",
                    "value_plain": f"Flagged; action is {action or 'review'}."
                    if action
                    else "Flagged; review before sending live.",
                }
            )
        else:
            lines.append(
                {
                    "label": "Earnings / event risk",
                    "value_plain": "No block from event calendar on this setup.",
                }
            )
    elif mode not in ("", "off"):
        lines.append(
            {
                "label": "Earnings / event risk",
                "value_plain": "Rules are not applied to live orders (monitoring only).",
            }
        )
    rg_raw: Any = checklist.get("regime_status")
    rg: dict[str, Any] = rg_raw if isinstance(rg_raw, dict) else {}
    rmode = str(rg.get("mode") or "off")
    if rmode == "live":
        score = rg.get("score")
        lines.append(
            {
                "label": "Regime gate",
                "value_plain": f"Score {score}; live gate is on."
                if score is not None
                else "Regime gate is on for live orders.",
            }
        )
    return lines


def with_plain_language(checklist: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of checklist with block_reasons_plain and checklist_lines."""
    out = dict(checklist)
    br = out.get("block_reasons")
    codes = [str(x) for x in br] if isinstance(br, list) else []
    out["block_reasons_plain"] = _plain_block_reasons(codes)
    out["checklist_lines"] = _checklist_lines(out)
    return out
