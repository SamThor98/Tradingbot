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
SEC_FILING_ARCHIVE_FILE = ".sec_filing_archive.json"
SEC_SUBMISSION_SHARD_URL = "https://data.sec.gov/submissions/{name}"
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
}
# Common filings. They stay visible as medium risk and do not trip the
# composite-score cap. 8.01 is a catch-all; distress language in the
# description still matches HIGH_RISK_8K_TERMS.
MEDIUM_RISK_8K_ITEMS: dict[str, str] = {
    "1.01": "Entry into a Material Definitive Agreement",
    "2.02": "Results of Operations and Financial Condition (earnings)",
    "2.03": "Creation of Material Direct Financial Obligation",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure of Directors or Principal Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
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


def pit_business_from_filings(
    filings: list[dict[str, Any]] | None,
    entry_date: str,
) -> dict[str, str] | None:
    """Risk tag from filings whose date is on or before ``entry_date``.

    A filing after the entry is omitted. An empty window is not an observation.
    """
    entry = str(entry_date or "").strip()[:10]
    if len(entry) < 10 or not filings:
        return None
    kept: list[dict[str, Any]] = []
    for filing in filings:
        if not isinstance(filing, dict):
            continue
        day = str(filing.get("date") or "").strip()[:10]
        if len(day) >= 10 and day <= entry:
            kept.append(filing)
    if not kept:
        return None
    # Same window as the live snapshot: the six filings that were newest at entry.
    kept.sort(key=lambda row: str(row.get("date") or ""), reverse=True)
    kept = kept[:6]
    tag, _reasons = _risk_tag_from_filings(kept)
    if tag not in {"low", "medium", "high"}:
        return None
    as_of = max(str(filing.get("date") or "")[:10] for filing in kept)
    return {"business_as_of": as_of, "sec_risk_tag": tag}


def _archive_path(skill_dir: Path | None = None) -> Path:
    return (skill_dir or SKILL_DIR) / SEC_FILING_ARCHIVE_FILE


def _load_archive(skill_dir: Path | None = None) -> dict[str, Any]:
    path = _archive_path(skill_dir)
    if not path.exists():
        return {"tickers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("tickers"), dict):
            return data
    except Exception:
        pass
    return {"tickers": {}}


def _save_archive(archive: dict[str, Any], skill_dir: Path | None = None) -> None:
    from _io_utils import atomic_write_json

    atomic_write_json(_archive_path(skill_dir), archive, indent=2)


def _filing_key(row: dict[str, Any]) -> str:
    accession = str(row.get("accession") or "").strip()
    if accession:
        return accession
    return "|".join(
        [
            str(row.get("form") or ""),
            str(row.get("date") or "")[:10],
            str(row.get("url") or ""),
        ]
    )


def filing_rows_from_columns(block: dict[str, Any] | None, cik: str) -> list[dict[str, Any]]:
    """Every 10-K, 10-Q, and 8-K in one EDGAR submissions table.

    The live snapshot still keeps six of these. The archive keeps the whole table.
    A guidance line is copied only when that table already carries one.
    """
    table = block if isinstance(block, dict) else {}
    forms = table.get("form") or []
    dates = table.get("filingDate") or []
    accessions = table.get("accessionNumber") or []
    docs = table.get("primaryDocument") or []
    descriptions = table.get("primaryDocDescription") or []
    items_per_filing = table.get("items") or []
    rows: list[dict[str, Any]] = []
    cik_path = str(cik or "").lstrip("0")
    for i, form in enumerate(forms):
        if form not in TARGET_FORMS:
            continue
        accession = str(accessions[i]) if i < len(accessions) else ""
        doc = str(docs[i]) if i < len(docs) else ""
        filing_date = str(dates[i]) if i < len(dates) else ""
        desc = str(descriptions[i]) if i < len(descriptions) else ""
        items_field = items_per_filing[i] if i < len(items_per_filing) else None
        compact = accession.replace("-", "")
        url = ""
        if cik_path and compact and doc:
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_path}/{compact}/{doc}"
        row: dict[str, Any] = {
            "form": str(form),
            "date": filing_date[:10],
            "description": desc[:160],
            "url": url,
            "items": _parse_items_field(items_field),
            "accession": accession,
        }
        guidance = table.get("guidance_signal")
        if isinstance(guidance, list) and i < len(guidance) and guidance[i]:
            row["guidance_signal"] = str(guidance[i])
        rows.append(row)
    return rows


def merge_filing_rows(
    existing: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge by accession. A later refresh does not drop an older filing."""
    merged: dict[str, dict[str, Any]] = {}
    for row in list(existing or []) + list(incoming or []):
        if not isinstance(row, dict):
            continue
        key = _filing_key(row)
        prior = merged.get(key)
        current = dict(row)
        if prior:
            if not current.get("items") and prior.get("items"):
                current["items"] = prior["items"]
            if not current.get("guidance_signal") and prior.get("guidance_signal"):
                current["guidance_signal"] = prior["guidance_signal"]
            if not current.get("url") and prior.get("url"):
                current["url"] = prior["url"]
        merged[key] = current
    return sorted(merged.values(), key=lambda row: str(row.get("date") or ""), reverse=True)


def merge_ticker_archive(
    ticker: str,
    rows: list[dict[str, Any]],
    *,
    skill_dir: Path | None = None,
    shards: list[dict[str, Any]] | None = None,
    loaded_shard: str | None = None,
) -> None:
    """Append filing rows for one ticker. Live ``recent_filings`` is left at six."""
    name = str(ticker or "").upper().strip()
    if not name:
        return
    archive = _load_archive(skill_dir)
    tickers = archive.setdefault("tickers", {})
    entry = tickers.get(name) if isinstance(tickers.get(name), dict) else {}
    filings = merge_filing_rows(entry.get("filings") if isinstance(entry, dict) else [], rows)
    known = {str(item.get("name") or ""): dict(item) for item in (entry.get("shards") or []) if isinstance(item, dict)}
    for shard in shards or []:
        if not isinstance(shard, dict):
            continue
        shard_name = str(shard.get("name") or "").strip()
        if not shard_name:
            continue
        prior = known.get(shard_name) or {}
        known[shard_name] = {
            "name": shard_name,
            "filing_from": str(shard.get("filingFrom") or shard.get("filing_from") or prior.get("filing_from") or ""),
            "filing_to": str(shard.get("filingTo") or shard.get("filing_to") or prior.get("filing_to") or ""),
            "loaded": bool(prior.get("loaded")),
        }
    if loaded_shard and loaded_shard in known:
        known[loaded_shard]["loaded"] = True
    tickers[name] = {"filings": filings, "shards": list(known.values())}
    try:
        _save_archive(archive, skill_dir)
    except Exception as exc:
        LOG.debug("SEC filing archive write failed for %s: %s", name, exc)


def cached_filings_by_ticker(skill_dir: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Filing lists already on disk. The archive wins. The six-filing cache fills gaps."""
    out: dict[str, list[dict[str, Any]]] = {}
    archive = _load_archive(skill_dir)
    archived = archive.get("tickers") if isinstance(archive, dict) else None
    if isinstance(archived, dict):
        for ticker, entry in archived.items():
            filings = entry.get("filings") if isinstance(entry, dict) else None
            if isinstance(filings, list) and filings:
                out[str(ticker).upper()] = [row for row in filings if isinstance(row, dict)]
    cache = _load_cache(skill_dir)
    tickers = cache.get("tickers") if isinstance(cache, dict) else None
    if not isinstance(tickers, dict):
        return out
    for ticker, entry in tickers.items():
        payload = entry.get("payload") if isinstance(entry, dict) else None
        filings = payload.get("recent_filings") if isinstance(payload, dict) else None
        if not isinstance(filings, list) or not filings:
            continue
        name = str(ticker).upper()
        out[name] = merge_filing_rows(out.get(name), [row for row in filings if isinstance(row, dict)])
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
                reasons.append(f"8-K item {code_str}: {MEDIUM_RISK_8K_ITEMS[code_str]}")
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
        return "medium", (reasons or ["recent 8-K with material item code"])[:3]
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
        # Reclassify from the stored filings so a tag-rule change is not
        # stuck behind the cache TTL. The hard score cap keys off this tag.
        if payload.get("ok") and isinstance(payload.get("recent_filings"), list):
            tag, reasons = _risk_tag_from_filings(payload["recent_filings"])
            payload["risk_tag"] = tag
            payload["risk_reasons"] = reasons[:3]
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
        body = sub.json()
        filings_node = (body.get("filings", {}) or {}) if isinstance(body, dict) else {}
        recent = filings_node.get("recent", {}) or {}
        archive_rows = filing_rows_from_columns(recent, cik)
        shard_list = list(filings_node.get("files") or [])
        if archive_rows or shard_list:
            merge_ticker_archive(
                tkr,
                archive_rows,
                skill_dir=skill_dir,
                shards=shard_list,
            )
        filings = _clamp_recent_filings(archive_rows, max_items=6)
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


def _submission_columns(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("form"), list):
        return payload
    recent = (payload.get("filings") or {}).get("recent") if isinstance(payload.get("filings"), dict) else None
    if isinstance(recent, dict):
        return recent
    return {}


def backfill_filing_archive(
    tickers: list[str],
    *,
    skill_dir: Path | None = None,
    earliest: str = "2016-01-01",
    user_agent: str | None = None,
    sleep_s: float = 0.2,
    get: Any = None,
) -> dict[str, Any]:
    """Pull EDGAR submission tables through ``earliest`` into the filing archive.

    The live six-filing cache is not rewritten. Shard files are fetched only
    until the archive has a filing on or before ``earliest``.
    """
    import requests

    ua = _safe_user_agent(user_agent)
    if ua is None:
        from config import get_edgar_user_agent

        ua = _safe_user_agent(get_edgar_user_agent(skill_dir))
    if ua is None:
        return {"ok": False, "error": "EDGAR user agent missing", "tickers": 0, "requests": 0}
    requester = get or requests.get
    headers = {"User-Agent": ua}
    floor = str(earliest or "")[:10]
    names = [str(ticker).upper().strip() for ticker in tickers if str(ticker).strip()]
    try:
        index = requester(SEC_INDEX_URL, headers=headers, timeout=30)
        index.raise_for_status()
        index_body = index.json()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "tickers": 0, "requests": 1}
    cik_by_ticker: dict[str, str] = {}
    for row in index_body.values() if isinstance(index_body, dict) else []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("ticker") or "").upper()
        if symbol:
            cik_by_ticker[symbol] = str(row.get("cik_str") or "").zfill(10)
    requests_made = 1
    filled = 0
    for ticker in names:
        cik = cik_by_ticker.get(ticker)
        if not cik:
            continue
        time.sleep(max(0.0, float(sleep_s)))
        try:
            main = requester(SEC_SUBMISSION_URL.format(cik=cik), headers=headers, timeout=30)
            main.raise_for_status()
            body = main.json()
        except Exception as exc:
            LOG.warning("SEC archive backfill failed for %s: %s", ticker, exc)
            requests_made += 1
            continue
        requests_made += 1
        node = body.get("filings") if isinstance(body, dict) and isinstance(body.get("filings"), dict) else {}
        recent = node.get("recent") if isinstance(node, dict) else {}
        shards = list(node.get("files") or []) if isinstance(node, dict) else []
        merge_ticker_archive(ticker, filing_rows_from_columns(recent, cik), skill_dir=skill_dir, shards=shards)
        archive = _load_archive(skill_dir)
        entry = (archive.get("tickers") or {}).get(ticker) or {}
        filings = list(entry.get("filings") or [])
        oldest = min((str(row.get("date") or "")[:10] for row in filings if row.get("date")), default="")
        pending = [
            shard
            for shard in (entry.get("shards") or [])
            if isinstance(shard, dict) and shard.get("name") and not shard.get("loaded")
        ]
        pending.sort(key=lambda shard: str(shard.get("filing_to") or ""), reverse=True)
        for shard in pending:
            if oldest and oldest <= floor:
                break
            time.sleep(max(0.0, float(sleep_s)))
            shard_name = str(shard.get("name"))
            try:
                shard_resp = requester(SEC_SUBMISSION_SHARD_URL.format(name=shard_name), headers=headers, timeout=30)
                shard_resp.raise_for_status()
                parsed = shard_resp.json()
                shard_body = parsed if isinstance(parsed, dict) else {}
            except Exception as exc:
                LOG.warning("SEC archive shard failed for %s %s: %s", ticker, shard_name, exc)
                requests_made += 1
                continue
            requests_made += 1
            rows = filing_rows_from_columns(_submission_columns(shard_body), cik)
            merge_ticker_archive(ticker, rows, skill_dir=skill_dir, loaded_shard=shard_name)
            if rows:
                shard_oldest = min(str(row.get("date") or "")[:10] for row in rows if row.get("date"))
                if not oldest or shard_oldest < oldest:
                    oldest = shard_oldest
        filled += 1
    return {"ok": True, "tickers": filled, "requests": requests_made, "earliest": floor}
