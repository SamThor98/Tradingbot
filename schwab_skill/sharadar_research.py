"""Research-only Sharadar overlays (dossier, decision card, pricing, forensic).

Reuses ``get_sharadar_research_snapshot``. Does not change scan ranking,
``FORENSIC_FILTER_MODE``, or live ``PIT_UNIVERSE_PROVIDER``. Missing values
stay ``None`` — never filled with zeros.
"""

from __future__ import annotations

import logging
from typing import Any

from sharadar_data import is_sharadar_usable

LOG = logging.getLogger(__name__)

_ACTION_TOKENS = (
    "split",
    "dividend",
    "spinoff",
    "spin-off",
    "spin off",
    "stocksplit",
    "stock split",
    "div",
)
_BUY_CODES = frozenset({"P", "B"})
_SELL_CODES = frozenset({"S", "D"})


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        fval = float(value)
    except (TypeError, ValueError):
        return None
    if fval != fval or abs(fval) == float("inf"):
        return None
    return fval


def _is_finite(value: Any) -> bool:
    return _safe_float(value) is not None


def _safe_div(numer: float | None, denom: float | None) -> float | None:
    if numer is None or denom is None:
        return None
    if abs(float(denom)) < 1e-12:
        return None
    return float(numer) / float(denom)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def ratio_as_finnhub_pct(value: Any) -> float | None:
    """Map Sharadar ratios (0.25, 1.47) onto Finnhub percent-point units (25, 147)."""
    n = _safe_float(value)
    if n is None:
        return None
    if abs(n) <= 5.0:
        return n * 100.0
    return n


def dossier_metric_overrides(sharadar_payload: dict[str, Any] | None) -> dict[str, float]:
    """Finite ART/daily cells keyed as Finnhub metric names. Empty dict if unusable."""
    if not is_sharadar_usable(sharadar_payload):
        return {}
    daily = (sharadar_payload or {}).get("daily") or {}
    fund = (sharadar_payload or {}).get("fundamentals") or {}
    out: dict[str, float] = {}

    def _put(key: str, raw: Any, *, as_pct: bool = False) -> None:
        val = ratio_as_finnhub_pct(raw) if as_pct else _safe_float(raw)
        if val is None:
            return
        out[key] = val

    _put("pe_ttm", daily.get("pe"))
    _put("ps_ttm", daily.get("ps"))
    _put("pb_annual", daily.get("pb"))
    _put("ev_to_ebitda", daily.get("evebitda"))
    _put("roe_ttm", fund.get("roe"), as_pct=True)
    _put("roa_ttm", fund.get("roa"), as_pct=True)
    _put("current_ratio_quarterly", fund.get("currentratio"))
    _put("debt_to_equity_quarterly", fund.get("de"))
    _put("net_margin_ttm", fund.get("netmargin"), as_pct=True)
    _put("gross_margin_ttm", fund.get("grossmargin"), as_pct=True)
    return out


def qualifying_corporate_events(
    actions: Any,
    events: Any,
) -> list[dict[str, Any]]:
    """Splits, dividends, spinoffs that can invalidate a multiple. Never fabricate."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in list(actions or []) + list(events or []):
        if not isinstance(raw, dict):
            continue
        date = _safe_str(raw.get("date"))[:10]
        label = _safe_str(raw.get("event") or raw.get("action") or raw.get("name"))
        if not date and not label:
            continue
        blob = f"{label} {raw.get('action') or ''}".lower()
        if not any(tok in blob for tok in _ACTION_TOKENS):
            continue
        key = (date, label.lower())
        if key in seen:
            continue
        seen.add(key)
        item: dict[str, Any] = {"date": date, "event": label}
        value = _safe_float(raw.get("value"))
        if value is not None:
            item["value"] = value
        rows.append(item)
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    return rows


def sloan_from_art(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    netinc = _safe_float(row.get("netinc"))
    ncfo = _safe_float(row.get("ncfo"))
    assets = _safe_float(row.get("assets"))
    if netinc is None or ncfo is None or assets is None:
        return None
    ratio = _safe_div(netinc - ncfo, assets)
    if ratio is None:
        return None
    return {
        "sloan_ratio": float(ratio),
        "net_income": float(netinc),
        "ocf": float(ncfo),
        "total_assets": float(assets),
    }


def altman_from_art(row: dict[str, Any] | None, daily: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    daily = daily if isinstance(daily, dict) else {}
    assets = _safe_float(row.get("assets"))
    if assets is None:
        return None
    wc = _safe_float(row.get("workingcapital"))
    if wc is None:
        ca = _safe_float(row.get("assetsc"))
        cl = _safe_float(row.get("liabilitiesc"))
        if ca is not None and cl is not None:
            wc = ca - cl
    retained = _safe_float(row.get("retearn"))
    ebit = _safe_float(row.get("ebit") if row.get("ebit") is not None else row.get("opinc"))
    revenue = _safe_float(row.get("revenue"))
    liabilities = _safe_float(row.get("liabilities"))
    market_cap = _safe_float(daily.get("marketcap"))
    if market_cap is None:
        market_cap = _safe_float(row.get("marketcap"))

    a_val = _safe_div(wc, assets)
    b_val = _safe_div(retained, assets)
    c_val = _safe_div(ebit, assets)
    d_val = _safe_div(market_cap, liabilities)
    e_val = _safe_div(revenue, assets)
    if any(v is None for v in (a_val, b_val, c_val, d_val, e_val)):
        return None
    z_score = (
        (1.2 * float(a_val))
        + (1.4 * float(b_val))
        + (3.3 * float(c_val))
        + (0.6 * float(d_val))
        + (1.0 * float(e_val))
    )
    zone = "safe" if z_score > 3.0 else "grey" if z_score >= 1.8 else "distress"
    return {
        "z_score": float(z_score),
        "zone": zone,
        "components": {
            "A_working_capital_over_assets": float(a_val),
            "B_retained_earnings_over_assets": float(b_val),
            "C_ebit_over_assets": float(c_val),
            "D_market_cap_over_liabilities": float(d_val),
            "E_revenue_over_assets": float(e_val),
        },
    }


def _gross_margin(row: dict[str, Any]) -> float | None:
    gm = _safe_float(row.get("grossmargin"))
    if gm is not None:
        return gm if abs(gm) <= 5.0 else gm / 100.0
    revenue = _safe_float(row.get("revenue"))
    gp = _safe_float(row.get("gp"))
    cor = _safe_float(row.get("cor"))
    if gp is None and revenue is not None and cor is not None:
        gp = revenue - cor
    return _safe_div(gp, revenue)


def beneish_from_art(current: dict[str, Any] | None, prior: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(current, dict) or not isinstance(prior, dict):
        return None
    rev_c = _safe_float(current.get("revenue"))
    rev_p = _safe_float(prior.get("revenue"))
    recv_c = _safe_float(current.get("receivables"))
    recv_p = _safe_float(prior.get("receivables"))
    ta_c = _safe_float(current.get("assets"))
    ta_p = _safe_float(prior.get("assets"))
    ca_c = _safe_float(current.get("assetsc"))
    ca_p = _safe_float(prior.get("assetsc"))
    ppe_c = _safe_float(current.get("ppnenet"))
    ppe_p = _safe_float(prior.get("ppnenet"))
    dep_c = _safe_float(current.get("depamor"))
    dep_p = _safe_float(prior.get("depamor"))
    sga_c = _safe_float(current.get("sgna"))
    sga_p = _safe_float(prior.get("sgna"))
    ni_c = _safe_float(current.get("netinc"))
    ocf_c = _safe_float(current.get("ncfo"))
    gm_c = _gross_margin(current)
    gm_p = _gross_margin(prior)
    debt_c = _safe_float(current.get("debt"))
    debt_p = _safe_float(prior.get("debt"))
    cl_c = _safe_float(current.get("liabilitiesc"))
    cl_p = _safe_float(prior.get("liabilitiesc"))

    dsri = _safe_div(_safe_div(recv_c, rev_c), _safe_div(recv_p, rev_p))
    gmi = _safe_div(gm_p, gm_c)
    aq_c = _safe_div(
        (ta_c - ca_c - ppe_c) if ta_c is not None and ca_c is not None and ppe_c is not None else None,
        ta_c,
    )
    aq_p = _safe_div(
        (ta_p - ca_p - ppe_p) if ta_p is not None and ca_p is not None and ppe_p is not None else None,
        ta_p,
    )
    aqi = _safe_div(aq_c, aq_p)
    sgi = _safe_div(rev_c, rev_p)
    dep_rate_c = _safe_div(dep_c, (dep_c + ppe_c) if dep_c is not None and ppe_c is not None else None)
    dep_rate_p = _safe_div(dep_p, (dep_p + ppe_p) if dep_p is not None and ppe_p is not None else None)
    depi = _safe_div(dep_rate_p, dep_rate_c)
    sgai = _safe_div(_safe_div(sga_c, rev_c), _safe_div(sga_p, rev_p))
    lev_c = _safe_div(
        ((debt_c or 0.0) + (cl_c or 0.0)) if (debt_c is not None or cl_c is not None) else None,
        ta_c,
    )
    lev_p = _safe_div(
        ((debt_p or 0.0) + (cl_p or 0.0)) if (debt_p is not None or cl_p is not None) else None,
        ta_p,
    )
    lvgi = _safe_div(lev_c, lev_p)
    tata = _safe_div((ni_c - ocf_c) if ni_c is not None and ocf_c is not None else None, ta_c)
    if any(v is None for v in (dsri, gmi, aqi, sgi, depi, sgai, lvgi, tata)):
        return None
    m_score = (
        -4.84
        + (0.920 * float(dsri))
        + (0.528 * float(gmi))
        + (0.404 * float(aqi))
        + (0.892 * float(sgi))
        + (0.115 * float(depi))
        - (0.172 * float(sgai))
        + (4.679 * float(tata))
        - (0.327 * float(lvgi))
    )
    return {
        "m_score": float(m_score),
        "components": {
            "dsri": float(dsri),
            "gmi": float(gmi),
            "aqi": float(aqi),
            "sgi": float(sgi),
            "depi": float(depi),
            "sgai": float(sgai),
            "lvgi": float(lvgi),
            "tata": float(tata),
        },
        "likely_manipulator": bool(m_score > -1.78),
    }


def forensic_from_sharadar(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Sloan / Beneish / Altman from ART rows when coverage is enough. No zeros."""
    empty = {
        "ok": False,
        "sloan": None,
        "beneish": None,
        "altman": None,
        "forensic_flags": [],
        "data_lineage": "",
    }
    if not is_sharadar_usable(payload):
        return empty
    history = list((payload or {}).get("fundamentals_history") or [])
    current = dict((payload or {}).get("fundamentals") or {})
    if not current and history:
        current = dict(history[0])
    prior = dict(history[1]) if len(history) > 1 else {}
    daily = (payload or {}).get("daily") or {}
    sloan = sloan_from_art(current)
    beneish = beneish_from_art(current, prior)
    altman = altman_from_art(current, daily)
    ok = any(x is not None for x in (sloan, beneish, altman))
    return {
        "ok": ok,
        "sloan": sloan,
        "beneish": beneish,
        "altman": altman,
        "forensic_flags": [],
        "data_lineage": "sharadar_art" if ok else "",
        "datekey": _safe_str(current.get("datekey"))[:10],
    }


def overlay_research_forensic(
    yf_snapshot: dict[str, Any] | None,
    sharadar_payload: dict[str, Any] | None,
    *,
    skill_dir: Any = None,
) -> dict[str, Any]:
    """Prefer ART scores on research surfaces; keep yfinance when ART cannot compute.

    Does not change the live scanner path or ``FORENSIC_FILTER_MODE``.
    """
    from config import get_forensic_altman_min, get_forensic_beneish_max, get_forensic_sloan_max

    out = dict(yf_snapshot or {})
    art = forensic_from_sharadar(sharadar_payload)
    if not art.get("ok"):
        return out
    lineage: list[str] = []
    used_art = False
    if art.get("sloan") is not None:
        out["sloan"] = art["sloan"]
        lineage.append("sloan:sharadar_art")
        used_art = True
    elif out.get("sloan") is not None:
        lineage.append("sloan:yfinance")
    if art.get("beneish") is not None:
        out["beneish"] = art["beneish"]
        lineage.append("beneish:sharadar_art")
        used_art = True
    elif out.get("beneish") is not None:
        lineage.append("beneish:yfinance")
    if art.get("altman") is not None:
        out["altman"] = art["altman"]
        lineage.append("altman:sharadar_art")
        used_art = True
    elif out.get("altman") is not None:
        lineage.append("altman:yfinance")
    if not used_art:
        return out
    out["ok"] = any(out.get(k) is not None for k in ("sloan", "beneish", "altman"))
    out["data_lineage"] = ",".join(lineage)
    sloan_max = get_forensic_sloan_max(skill_dir)
    beneish_max = get_forensic_beneish_max(skill_dir)
    altman_min = get_forensic_altman_min(skill_dir)
    flags: list[str] = []
    sloan = out.get("sloan") if isinstance(out.get("sloan"), dict) else None
    beneish = out.get("beneish") if isinstance(out.get("beneish"), dict) else None
    altman = out.get("altman") if isinstance(out.get("altman"), dict) else None
    if sloan is not None:
        ratio = sloan.get("sloan_ratio")
        if isinstance(ratio, (int, float)) and float(ratio) > float(sloan_max):
            flags.append("sloan_high")
    if beneish is not None:
        m_score = beneish.get("m_score")
        if isinstance(m_score, (int, float)) and float(m_score) > float(beneish_max):
            flags.append("beneish_manipulator")
    if altman is not None:
        z_score = altman.get("z_score")
        if isinstance(z_score, (int, float)) and float(z_score) < float(altman_min):
            flags.append("altman_distress")
    out["forensic_flags"] = flags
    return out


def _insider_net(rows: list[dict[str, Any]]) -> dict[str, int] | None:
    buys = 0
    sells = 0
    counted = False
    for row in rows:
        code = _safe_str(row.get("transactioncode")).upper()
        if not code:
            continue
        counted = True
        if code in _BUY_CODES:
            buys += 1
        elif code in _SELL_CODES:
            sells += 1
    if not counted:
        return None
    return {"buys": buys, "sells": sells}


def decision_card_overlay(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Compact display payload for the decision card. Null chips omitted by the UI."""
    usable = is_sharadar_usable(payload)
    daily = (payload or {}).get("daily") or {}
    holders = [h for h in list((payload or {}).get("holders") or []) if isinstance(h, dict)]
    insiders = [r for r in list((payload or {}).get("insiders") or []) if isinstance(r, dict)]
    pit = (payload or {}).get("pit_fields") or {}
    inst = _safe_float(pit.get("institutional_ownership"))
    as_of = _safe_str((payload or {}).get("as_of") or daily.get("date"))[:10]
    dq = _safe_str((payload or {}).get("data_quality") or (payload or {}).get("status") or "n/a")
    cache_note = "cached" if (payload or {}).get("from_cache") else "live"
    provenance = f"Sharadar daily · as-of {as_of or 'n/a'} · {cache_note} · data_quality={dq}"
    chips: list[dict[str, Any]] = []
    if usable:
        for key, label in (
            ("marketcap", "Market cap"),
            ("evebitda", "EV / EBITDA"),
            ("pe", "P/E"),
            ("ps", "P/S"),
        ):
            val = _safe_float(daily.get(key))
            if val is None:
                continue
            chips.append({"id": key, "label": label, "value": val})
    forensic = forensic_from_sharadar(payload) if usable else {"ok": False}
    return {
        "available": bool(usable and (chips or holders[:1] or insiders[:1] or forensic.get("ok"))),
        "status": _safe_str((payload or {}).get("status") or "disabled"),
        "enabled": bool((payload or {}).get("enabled")),
        "data_quality": dq,
        "as_of": as_of,
        "from_cache": bool((payload or {}).get("from_cache")),
        "provenance": provenance,
        "chips": chips,
        "holders": holders[:5],
        "institutional_ownership": inst,
        "insiders": insiders[:8],
        "insider_net": _insider_net(insiders[:8]),
        "actions": qualifying_corporate_events(
            (payload or {}).get("actions"),
            (payload or {}).get("events"),
        )[:8],
        "forensic": forensic if forensic.get("ok") else None,
    }


def pricing_cross_check(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Labeled Sharadar column for the pricing panel. Not blended into DCF."""
    usable = is_sharadar_usable(payload)
    daily = (payload or {}).get("daily") or {}
    fund = (payload or {}).get("fundamentals") or {}
    errors = list((payload or {}).get("errors") or [])
    status = _safe_str((payload or {}).get("status") or "disabled")
    if (payload or {}).get("enabled") is False:
        status = "disabled"
    shares = _safe_float(fund.get("sharesbas"))
    if shares is None:
        shares = _safe_float(fund.get("shareswa"))
    return {
        "available": bool(usable),
        "status": status,
        "enabled": bool((payload or {}).get("enabled")),
        "data_quality": _safe_str((payload or {}).get("data_quality") or status),
        "as_of": _safe_str((payload or {}).get("as_of") or daily.get("date"))[:10],
        "from_cache": bool((payload or {}).get("from_cache")),
        "errors": errors,
        "label": "Sharadar Bundle",
        "ev": _safe_float(daily.get("ev")) if usable else None,
        "evebitda": _safe_float(daily.get("evebitda")) if usable else None,
        "fcf": _safe_float(fund.get("fcf")) if usable else None,
        "shares": shares if usable else None,
        "marketcap": _safe_float(daily.get("marketcap")) if usable else None,
        "datekey": _safe_str(fund.get("datekey"))[:10] if usable else "",
        "daily_date": _safe_str(daily.get("date"))[:10] if usable else "",
    }


def _compare_side(payload: dict[str, Any] | None) -> dict[str, Any]:
    daily = (payload or {}).get("daily") or {}
    fund = (payload or {}).get("fundamentals") or {}
    return {
        "ticker": _safe_str((payload or {}).get("ticker")).upper(),
        "enabled": bool((payload or {}).get("enabled")),
        "status": _safe_str((payload or {}).get("status") or "disabled"),
        "data_quality": _safe_str((payload or {}).get("data_quality") or ""),
        "as_of": _safe_str((payload or {}).get("as_of") or daily.get("date"))[:10],
        "from_cache": bool((payload or {}).get("from_cache")),
        "errors": list((payload or {}).get("errors") or []),
        "name": _safe_str(((payload or {}).get("profile") or {}).get("name")),
        "roe": _safe_float(fund.get("roe")),
        "revenue": _safe_float(fund.get("revenue")),
        "fcf": _safe_float(fund.get("fcf")),
        "evebitda": _safe_float(daily.get("evebitda")),
        "pe": _safe_float(daily.get("pe")),
        "ps": _safe_float(daily.get("ps")),
        "marketcap": _safe_float(daily.get("marketcap")),
        "datekey": _safe_str(fund.get("datekey"))[:10],
        "daily_date": _safe_str(daily.get("date"))[:10],
    }


def compare_bundle(
    left_payload: dict[str, Any] | None,
    right_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    left = _compare_side(left_payload)
    right = _compare_side(right_payload)
    return {
        "left": left,
        "right": right,
        "fields": [
            {"id": "roe", "label": "ROE TTM"},
            {"id": "revenue", "label": "Revenue TTM"},
            {"id": "fcf", "label": "Free cash flow"},
            {"id": "evebitda", "label": "EV / EBITDA"},
            {"id": "pe", "label": "P/E"},
        ],
    }


def snapshot_for_research(ticker: str, *, skill_dir: Any = None, refresh: bool = False) -> dict[str, Any]:
    """Single client entry for research overlays. Never raises."""
    from pathlib import Path

    from sharadar_data import get_sharadar_research_snapshot

    try:
        return get_sharadar_research_snapshot(
            ticker,
            skill_dir=Path(skill_dir) if skill_dir is not None else None,
            refresh=refresh,
        )
    except Exception as exc:
        LOG.warning("Sharadar research snapshot failed for %s: %s", ticker, exc)
        return {
            "ok": False,
            "enabled": False,
            "status": "error",
            "ticker": str(ticker or "").strip().upper(),
            "errors": [f"sharadar_overlay_error:{type(exc).__name__}"],
            "data_quality": "error",
        }
