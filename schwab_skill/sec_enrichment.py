"""
SEC enrichment utilities with local caching.

Provides:
- ticker -> CIK lookup
- recent filing metadata (10-K, 10-Q, 8-K)
- lightweight risk/event tagging for downstream scoring and reporting
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
SEC_CACHE_FILE = ".sec_cache.json"
SEC_INDEX_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSION_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
TARGET_FORMS = {"10-K", "10-Q", "8-K"}
DEFAULT_USER_AGENT = "SchwabTradingBot contact@example.com"

HIGH_RISK_8K_TERMS = (
    "bankruptcy",
    "chapter 11",
    "going concern",
    "material weakness",
    "delisting",
    "default",
    "resignation",
    "restatement",
    "investigation",
    "litigation",
)

# SEC 8-K item codes that materially affect risk. Sourced from
# https://www.sec.gov/about/forms/form8-k.pdf. Keyword matching against the
# filing `description` field misses almost everything because EDGAR usually
# stores `description="FORM 8-K"`; item codes are deterministic and free.
HIGH_RISK_8K_ITEMS: dict[str, str] = {
    "1.02": "Termination of Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.04": "Triggering Events Accelerating a Direct Financial Obligation",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting / Failure to Satisfy Listing Rule",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements (restatement)",
    "5.02": "Departure of Directors or Principal Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "8.01": "Other Events",  # often used for material litigation / investigation announcements
}
MEDIUM_RISK_8K_ITEMS: dict[str, str] = {
    "1.01": "Entry into a Material Definitive Agreement",
    "2.02": "Results of Operations and Financial Condition (earnings)",
    "2.03": "Creation of Material Direct Financial Obligation",
    "5.01": "Changes in Control of Registrant",
    "7.01": "Regulation FD Disclosure",
}


def _parse_items_field(raw: Any) -> list[str]:
    """Normalize the EDGAR `items` field, which can be a string or list per filing."""
    if raw is None:
        return []
    if isinstance(raw, list):
        items_raw = ",".join(str(x) for x in raw)
    else:
        items_raw = str(raw)
    out: list[str] = []
    for token in items_raw.replace(";", ",").split(","):
        t = token.strip()
        # EDGAR formats vary: "1.02", "Item 1.02", "1.02 - Termination ..."
        if t.lower().startswith("item"):
            t = t[4:].strip()
        # Strip trailing description text, keep just the leading code.
        t = t.split(" ")[0].split("-")[0].strip()
        if t:
            out.append(t)
    return out


def _cache_path(skill_dir: Path | None = None) -> Path:
    return (skill_dir or SKILL_DIR) / SEC_CACHE_FILE


def _load_cache(skill_dir: Path | None = None) -> dict[str, Any]:
    path = _cache_path(skill_dir)
    if not path.exists():
        return {"tickers": {}}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and isinstance(data.get("tickers"), dict):
            return data
    except Exception:
        pass
    return {"tickers": {}}


def _save_cache(cache: dict[str, Any], skill_dir: Path | None = None) -> None:
    from _io_utils import atomic_write_json

    atomic_write_json(_cache_path(skill_dir), cache, indent=2)


# Failed SEC payloads (`ok=False`) cache for at most 15 minutes so a transient
# EDGAR rate-limit / network hiccup doesn't pin the ticker's risk_tag to
# "unknown" for the full `cache_hours` window. Successful payloads keep the
# operator-configured TTL.
_FAILED_PAYLOAD_TTL_HOURS = 0.25


def _is_fresh(entry: dict[str, Any] | None, cache_hours: float) -> bool:
    if not entry:
        return False
    ts = float(entry.get("timestamp", 0) or 0)
    if ts <= 0:
        return False
    payload = entry.get("payload") if isinstance(entry, dict) else None
    payload_ok = isinstance(payload, dict) and bool(payload.get("ok"))
    effective_ttl = float(cache_hours) if payload_ok else min(float(cache_hours), _FAILED_PAYLOAD_TTL_HOURS)
    age_h = (time.time() - ts) / 3600.0
    return age_h <= effective_ttl


def _clamp_recent_filings(filings: list[dict[str, Any]], max_items: int = 6) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in filings[:max_items]:
        out.append(
            {
                "form": str(f.get("form", "")),
                "date": str(f.get("date", "")),
                "description": str(f.get("description", ""))[:160],
                "url": str(f.get("url", "")),
                "items": list(f.get("items") or []),
            }
        )
    return out


def _risk_tag_from_filings(filings: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Classify risk using SEC 8-K item codes (primary) and description keywords (fallback).

    Item codes are deterministic and exhaustive for materiality (this is what
    SEC requires the filer to declare); description-keyword matching used to
    miss nearly everything because EDGAR usually stores `description="FORM
    8-K"`. We still keep the keyword check as a belt-and-suspenders fallback.
    """
    reasons: list[str] = []
    has_high_item = False
    has_medium_item = False
    for f in filings:
        form = (f.get("form") or "").upper()
        if form != "8-K":
            continue
        items = list(f.get("items") or [])
        for code in items:
            code_str = str(code).strip()
            if code_str in HIGH_RISK_8K_ITEMS:
                reasons.append(f"8-K item {code_str}: {HIGH_RISK_8K_ITEMS[code_str]}")
                has_high_item = True
            elif code_str in MEDIUM_RISK_8K_ITEMS:
                has_medium_item = True
        # Keyword fallback (catches the rare cases where the filer surfaces
        # risk-relevant text in `description`).
        desc = (f.get("description") or "").lower()
        if desc:
            for term in HIGH_RISK_8K_TERMS:
                if term in desc:
                    reason = f"8-K keyword: {term}"
                    if reason not in reasons:
                        reasons.append(reason)
                    has_high_item = True
                    break
    if has_high_item:
        return "high", reasons[:3]
    if has_medium_item:
        return "medium", ["recent 8-K with material item code"]
    if any((f.get("form") or "").upper() == "8-K" for f in filings):
        return "medium", ["recent 8-K present"]
    return "low", []


def _safe_user_agent(user_agent: str | None) -> str | None:
    """Return a usable EDGAR User-Agent or ``None`` if the operator hasn't set one.

    Previously this silently substituted a placeholder ``contact@example.com``
    address when the env var was missing or malformed. SEC's fair-access
    policy explicitly forbids that — see
    https://www.sec.gov/os/accessing-edgar-data — and SEC will rate-limit /
    IP-ban requests using fake contact info, silently breaking enrichment for
    every ticker. We now refuse to send the request at all when the UA isn't
    real, and let callers downgrade SEC enrichment to "unavailable" so the
    rest of the pipeline keeps working.
    """
    try:
        from config import is_real_edgar_user_agent
    except ImportError:
        ua = (user_agent or "").strip()
        return ua if (len(ua) >= 12 and "@" in ua and "example.com" not in ua.lower()) else None
    if is_real_edgar_user_agent(user_agent):
        return str(user_agent).strip()
    return None


def fetch_sec_snapshot(
    ticker: str,
    *,
    skill_dir: Path | None = None,
    user_agent: str | None = None,
    cache_hours: float = 12.0,
    enabled: bool = True,
) -> dict[str, Any]:
    """
    Return SEC snapshot for a ticker.
    Response fields:
      ok, ticker, cik, recent_filings, risk_tag, risk_reasons, recent_8k,
      filing_recency_days, from_cache, error
    """
    tkr = ticker.upper().strip()
    if not enabled:
        return {
            "ok": False,
            "ticker": tkr,
            "cik": "",
            "recent_filings": [],
            "risk_tag": "unknown",
            "risk_reasons": [],
            "recent_8k": False,
            "filing_recency_days": None,
            "from_cache": False,
            "error": "SEC enrichment disabled",
        }

    cache = _load_cache(skill_dir)
    entry = (cache.get("tickers") or {}).get(tkr)
    if _is_fresh(entry, cache_hours):
        payload: dict[str, Any] = dict((entry or {}).get("payload") or {})
        payload["from_cache"] = True
        return payload

    import requests
    ua = _safe_user_agent(user_agent)
    payload = {
        "ok": False,
        "ticker": tkr,
        "cik": "",
        "recent_filings": [],
        "risk_tag": "unknown",
        "risk_reasons": [],
        "recent_8k": False,
        "filing_recency_days": None,
        "from_cache": False,
        "error": "",
    }
    if ua is None:
        payload["error"] = (
            "EDGAR_USER_AGENT missing or contains placeholder/example contact; "
            "SEC requires a real operator email and will IP-ban fake UAs. "
            "Set EDGAR_USER_AGENT='YourCompanyName real-contact@yourdomain' in .env."
        )
        LOG.warning("SEC enrichment refused for %s: %s", tkr, payload["error"])
        return payload

    try:
        idx = requests.get(SEC_INDEX_URL, headers={"User-Agent": ua}, timeout=20)
        idx.raise_for_status()
        tickers_data = idx.json()

        cik = None
        for v in tickers_data.values():
            if (v.get("ticker") or "").upper() == tkr:
                cik = str(v.get("cik_str", "")).zfill(10)
                break
        if not cik:
            payload["error"] = f"CIK not found for {tkr}"
            return payload
        payload["cik"] = cik

        sub = requests.get(SEC_SUBMISSION_URL.format(cik=cik), headers={"User-Agent": ua}, timeout=20)
        sub.raise_for_status()
        recent = (sub.json().get("filings", {}) or {}).get("recent", {}) or {}
        forms = recent.get("form", []) or []
        dates = recent.get("filingDate", []) or []
        accessions = recent.get("accessionNumber", []) or []
        docs = recent.get("primaryDocument", []) or []
        descriptions = recent.get("primaryDocDescription", []) or []
        items_per_filing = recent.get("items", []) or []

        filings: list[dict[str, Any]] = []
        for i in range(min(len(forms), 80)):
            form = forms[i]
            if form not in TARGET_FORMS:
                continue
            accession = str(accessions[i]).replace("-", "")
            doc = str(docs[i])
            filing_date = str(dates[i]) if i < len(dates) else ""
            desc = str(descriptions[i]) if i < len(descriptions) else ""
            items_field = items_per_filing[i] if i < len(items_per_filing) else None
            url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{accession}/{doc}"
            filings.append(
                {
                    "form": form,
                    "date": filing_date,
                    "description": desc,
                    "url": url,
                    "items": _parse_items_field(items_field),
                }
            )
            if len(filings) >= 8:
                break

        filings = _clamp_recent_filings(filings, max_items=6)
        payload["recent_filings"] = filings
        payload["recent_8k"] = any((f.get("form") or "").upper() == "8-K" for f in filings)
        payload["filing_recency_days"] = None
        if filings and filings[0].get("date"):
            from datetime import date

            try:
                d = date.fromisoformat(str(filings[0]["date"]))
                payload["filing_recency_days"] = (date.today() - d).days
            except Exception:
                payload["filing_recency_days"] = None

        risk_tag, reasons = _risk_tag_from_filings(filings)
        payload["risk_tag"] = risk_tag
        payload["risk_reasons"] = reasons[:3]
        payload["ok"] = True
        payload["error"] = ""
        return payload
    except Exception as e:
        payload["error"] = str(e)
        return payload
    finally:
        # Cache even failures briefly, to avoid hammering SEC on repeated failures.
        cache.setdefault("tickers", {})[tkr] = {"timestamp": time.time(), "payload": payload}
        try:
            _save_cache(cache, skill_dir)
        except Exception as e:
            LOG.debug("SEC cache write failed: %s", e)
