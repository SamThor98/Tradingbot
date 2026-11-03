"""Point-in-time fundamentals and insider Form 4 features for research panels.

Sharadar is the only source here. Every value is keyed on the **filing date**
(``date`` in the Sharadar API, mapped to ``pit_date``), never the fiscal period
end, so a trade can only see numbers that were public before it was entered.

Nothing in this module invents a value. Missing stays missing: a ticker with no
filing before the entry date gets ``NaN``, not 0.0.

Used offline only (panel build / research screens). The live scanner does not
import this.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

LOG = logging.getLogger(__name__)

# As-reported TTM fields worth testing as ranking inputs. Names match Sharadar.
FUNDAMENTAL_RAW_FIELDS: tuple[str, ...] = (
    "roe",
    "roa",
    "fcf",
    "de",
    "debt",
    "netmargin",
    "grossmargin",
    "currentratio",
    "assetturnover",
    "pe",
    "pb",
    "ps",
    "evebitda",
    "divyield",
    "marketcap",
    "revenue",
    "equity",
    "ebitda",
    "eps",
    "fcfps",
    "bvps",
)

# Open-market buy / sell. Grants ("A") and option exercises ("M") are noise for
# conviction, so they are counted separately rather than folded into net buying.
INSIDER_BUY_CODES = frozenset({"P"})
INSIDER_SELL_CODES = frozenset({"S"})

# A fundamental row older than this is treated as unusable rather than stale-but-fine.
MAX_FUNDAMENTAL_STALENESS_DAYS = 400
DEFAULT_INSIDER_WINDOWS: tuple[int, ...] = (90, 180)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _batched(items: Sequence[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def _fetch_batch_with_split(
    adapter: Any,
    table: str,
    batch: list[str],
    base_params: dict[str, Any],
    *,
    max_pages: int,
    errors: list[str],
) -> list[dict[str, Any]]:
    """Query a ticker batch, halving it on failure.

    The API rejects oversized ticker lists and occasionally a single bad symbol,
    so a failed batch is split rather than dropped. A single ticker that still
    fails is recorded and skipped.
    """
    params = dict(base_params)
    params["ticker"] = ",".join(batch)
    try:
        status, raw, err = adapter._page(table, params, max_pages=max_pages)
    except Exception as exc:
        status, raw, err = 0, [], f"exception:{exc}"
    if not err:
        return raw
    if len(batch) == 1:
        errors.append(f"{table}_ticker_failed:{batch[0]}:{err}:status={status}")
        return []
    mid = len(batch) // 2
    out = _fetch_batch_with_split(
        adapter, table, batch[:mid], base_params, max_pages=max_pages, errors=errors
    )
    out.extend(
        _fetch_batch_with_split(
            adapter, table, batch[mid:], base_params, max_pages=max_pages, errors=errors
        )
    )
    return out


def fetch_fundamentals_frame(
    adapter: Any,
    tickers: Sequence[str],
    *,
    start: str,
    batch_size: int = 20,
    max_pages: int = 20,
) -> tuple[pd.DataFrame, list[str]]:
    """Full as-reported TTM history per ticker, keyed on filing date.

    Returns ``(frame, errors)``. Errors are surfaced, never swallowed into an
    empty-but-ok frame.
    """
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    symbols = sorted({str(t).strip().upper() for t in tickers if str(t).strip()})
    base = {"dimension": "ART", "from": start, "sort": "date.asc"}
    for batch in _batched(symbols, batch_size):
        raw = _fetch_batch_with_split(
            adapter, "fundamentals", batch, base, max_pages=max_pages, errors=errors
        )
        for item in raw:
            pit_date = str(item.get("date") or "")[:10]
            ticker = str(item.get("ticker") or "").upper()
            if not pit_date or not ticker:
                continue
            record: dict[str, Any] = {"ticker": ticker, "pit_date": pit_date}
            for field in FUNDAMENTAL_RAW_FIELDS:
                record[field] = _to_float(item.get(field))
            rows.append(record)
    if not rows:
        return pd.DataFrame(columns=["ticker", "pit_date", *FUNDAMENTAL_RAW_FIELDS]), errors
    frame = pd.DataFrame(rows)
    frame["pit_date"] = pd.to_datetime(frame["pit_date"], errors="coerce")
    frame = frame.dropna(subset=["pit_date"])
    frame = frame.sort_values(["ticker", "pit_date"]).drop_duplicates(
        subset=["ticker", "pit_date"], keep="last"
    )
    return frame.reset_index(drop=True), errors


def fetch_insider_events(
    adapter: Any,
    tickers: Sequence[str],
    *,
    start: str,
    batch_size: int = 10,
    max_pages: int = 40,
) -> tuple[pd.DataFrame, list[str]]:
    """Form 4 rows collapsed to one record per (ticker, filing date).

    Raw filings are aggregated inside the batch loop so the whole Form 4
    universe never has to sit in memory at once.
    """
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    symbols = sorted({str(t).strip().upper() for t in tickers if str(t).strip()})
    base = {"from": start, "sort": "date.asc"}
    for batch in _batched(symbols, batch_size):
        raw = _fetch_batch_with_split(
            adapter, "insiders", batch, base, max_pages=max_pages, errors=errors
        )
        if not raw:
            continue
        rows: list[dict[str, Any]] = []
        for item in raw:
            pit_date = str(item.get("date") or "")[:10]
            ticker = str(item.get("ticker") or "").upper()
            if not pit_date or not ticker:
                continue
            code = str(item.get("transactioncode") or "").strip().upper()
            shares = _to_float(item.get("transactionshares")) or 0.0
            price = _to_float(item.get("transactionpricepershare"))
            value = _to_float(item.get("transactionvalue"))
            if value is None and price is not None:
                value = abs(shares) * price
            value = abs(value or 0.0)
            is_buy = code in INSIDER_BUY_CODES
            is_sell = code in INSIDER_SELL_CODES
            officer = str(item.get("isofficer") or "").strip().upper() == "Y"
            director = str(item.get("isdirector") or "").strip().upper() == "Y"
            rows.append(
                {
                    "ticker": ticker,
                    "pit_date": pit_date,
                    "buy_value": value if is_buy else 0.0,
                    "sell_value": value if is_sell else 0.0,
                    "buy_shares": abs(shares) if is_buy else 0.0,
                    "sell_shares": abs(shares) if is_sell else 0.0,
                    "n_buys": 1 if is_buy else 0,
                    "n_sells": 1 if is_sell else 0,
                    "n_insider_buyers_senior": 1 if (is_buy and (officer or director)) else 0,
                }
            )
        if not rows:
            continue
        batch_df = pd.DataFrame(rows)
        agg = batch_df.groupby(["ticker", "pit_date"], as_index=False).sum(numeric_only=True)
        frames.append(agg)
    if not frames:
        return pd.DataFrame(
            columns=[
                "ticker",
                "pit_date",
                "buy_value",
                "sell_value",
                "buy_shares",
                "sell_shares",
                "n_buys",
                "n_sells",
                "n_insider_buyers_senior",
            ]
        ), errors
    out = pd.concat(frames, ignore_index=True)
    out["pit_date"] = pd.to_datetime(out["pit_date"], errors="coerce")
    out = out.dropna(subset=["pit_date"])
    return out.sort_values(["ticker", "pit_date"]).reset_index(drop=True), errors


def attach_fundamentals_asof(
    trades: pd.DataFrame,
    fundamentals: pd.DataFrame,
    *,
    entry_col: str = "entry_date",
    max_staleness_days: int = MAX_FUNDAMENTAL_STALENESS_DAYS,
) -> pd.DataFrame:
    """As-of join the newest filing strictly before each entry date."""
    out = trades.reset_index(drop=True).copy()
    for field in FUNDAMENTAL_RAW_FIELDS:
        out[f"fund_{field}"] = np.nan
    out["fund_staleness_days"] = np.nan
    if fundamentals is None or fundamentals.empty:
        return out

    left = pd.DataFrame(
        {
            "_row_id": range(len(out)),
            "ticker": out["ticker"].astype(str).str.upper(),
            entry_col: pd.to_datetime(out[entry_col], errors="coerce"),
        }
    ).dropna(subset=[entry_col])
    left = left.sort_values(entry_col)

    right = fundamentals.copy()
    right["ticker"] = right["ticker"].astype(str).str.upper()
    right["pit_date"] = pd.to_datetime(right["pit_date"], errors="coerce")
    right = right.dropna(subset=["pit_date"]).sort_values("pit_date")

    merged = pd.merge_asof(
        left,
        right,
        left_on=entry_col,
        right_on="pit_date",
        by="ticker",
        direction="backward",
        # A filing dated the entry day may have hit the tape after the entry.
        allow_exact_matches=False,
    )
    staleness = (merged[entry_col] - merged["pit_date"]).dt.days
    fresh = staleness.le(max_staleness_days).fillna(False)
    merged = merged.assign(_staleness=staleness.where(fresh))
    indexed = merged.set_index("_row_id")
    for field in FUNDAMENTAL_RAW_FIELDS:
        values = pd.to_numeric(indexed[field], errors="coerce").where(fresh.values)
        out[f"fund_{field}"] = values.reindex(range(len(out))).to_numpy()
    out["fund_staleness_days"] = indexed["_staleness"].reindex(range(len(out))).to_numpy()
    return out


def attach_insider_windows(
    trades: pd.DataFrame,
    events: pd.DataFrame,
    *,
    entry_col: str = "entry_date",
    windows: Sequence[int] = DEFAULT_INSIDER_WINDOWS,
) -> pd.DataFrame:
    """Trailing-window insider buying pressure strictly before each entry.

    Emits net dollar buying, buy/sell counts, and a bounded ratio per window.
    """
    out = trades.copy()
    cols: list[str] = []
    for win in windows:
        cols.extend(
            [
                f"insider_net_buy_value_{win}d",
                f"insider_buy_count_{win}d",
                f"insider_sell_count_{win}d",
                f"insider_buy_ratio_{win}d",
                f"insider_senior_buys_{win}d",
            ]
        )
    for col in cols:
        out[col] = pd.NA
    if events is None or events.empty:
        return out

    ev = events.copy()
    ev["ticker"] = ev["ticker"].astype(str).str.upper()
    ev["pit_date"] = pd.to_datetime(ev["pit_date"], errors="coerce")
    ev = ev.dropna(subset=["pit_date"])
    by_ticker: dict[str, pd.DataFrame] = {t: g.sort_values("pit_date") for t, g in ev.groupby("ticker")}

    entries = pd.to_datetime(out[entry_col], errors="coerce")
    tickers = out["ticker"].astype(str).str.upper()
    # Only tickers with any Form 4 history can produce a number; others stay NaN.
    results: dict[str, list[Any]] = {col: [] for col in cols}
    for ticker, entry in zip(tickers, entries):
        hist = by_ticker.get(ticker)
        if hist is None or pd.isna(entry):
            for col in cols:
                results[col].append(pd.NA)
            continue
        dates = hist["pit_date"]
        for win in windows:
            lo = entry - pd.Timedelta(days=int(win))
            mask = (dates >= lo) & (dates < entry)
            sub = hist[mask]
            buy_v = float(sub["buy_value"].sum()) if not sub.empty else 0.0
            sell_v = float(sub["sell_value"].sum()) if not sub.empty else 0.0
            n_buy = int(sub["n_buys"].sum()) if not sub.empty else 0
            n_sell = int(sub["n_sells"].sum()) if not sub.empty else 0
            senior = int(sub["n_insider_buyers_senior"].sum()) if not sub.empty else 0
            total = buy_v + sell_v
            results[f"insider_net_buy_value_{win}d"].append(buy_v - sell_v)
            results[f"insider_buy_count_{win}d"].append(n_buy)
            results[f"insider_sell_count_{win}d"].append(n_sell)
            results[f"insider_buy_ratio_{win}d"].append((buy_v / total) if total > 0 else pd.NA)
            results[f"insider_senior_buys_{win}d"].append(senior)
    for col in cols:
        out[col] = results[col]
    return out


def derive_fundamental_ratios(frame: pd.DataFrame) -> pd.DataFrame:
    """Blended ratios built from the as-of fields. Division guards, no fills."""
    out = frame.copy()

    def col(name: str) -> pd.Series:
        return pd.to_numeric(out.get(f"fund_{name}"), errors="coerce")

    marketcap = col("marketcap")
    fcf = col("fcf")
    pe = col("pe")
    ebitda = col("ebitda")
    debt = col("debt")

    positive_cap = marketcap.where(marketcap > 0)
    # Yield forms are better behaved than raw multiples (no blow-up near zero).
    out["fund_fcf_yield"] = fcf / positive_cap
    out["fund_earnings_yield"] = 1.0 / pe.where(pe > 0)
    out["fund_debt_to_ebitda"] = debt / ebitda.where(ebitda > 0)
    out["fund_log_marketcap"] = np.log(positive_cap)
    return out
