"""In-memory PIT adapter for tests. Membership is list-date based, not Wikipedia-now."""

from __future__ import annotations

from typing import Any

from research.pit_protocols import PIT_FUNDAMENTAL_FIELDS

# SNAP IPO 2017-03-02, SNOW IPO 2020-09-16 — must be absent from 2016-06-01.
DEFAULT_FIXTURE: dict[str, Any] = {
    "list_dates": {
        "AAPL": "1980-12-12",
        "MSFT": "1986-03-13",
        "XOM": "1920-01-01",
        "SNAP": "2017-03-02",
        "SNOW": "2020-09-16",
    },
    "delist_dates": {},
    "wikipedia_now": ["AAPL", "MSFT", "XOM", "SNAP", "SNOW"],
    "fundamentals": {
        "2016-06-01": {
            "AAPL": {"market_cap": 5.5e11, "float_shares": 5.2e9, "roe": 0.37},
            "MSFT": {"market_cap": 4.0e11, "float_shares": 7.8e9, "roe": 0.22},
            "XOM": {"market_cap": 3.6e11, "float_shares": 4.1e9, "roe": 0.08},
        }
    },
}


class StubPITProvider:
    """Deterministic PIT universe + fundamentals. Never fabricates missing fields."""

    provider_id = "stub"

    def __init__(self, fixture: dict[str, Any] | None = None) -> None:
        self._fx = dict(fixture or DEFAULT_FIXTURE)
        self._quality: dict[str, Any] = {
            "status": "ok",
            "reason": "stub_fixture",
            "provider": self.provider_id,
        }

    def universe_asof(self, asof: str) -> list[str]:
        asof_s = str(asof).strip()[:10]
        listed = self._fx.get("list_dates") or {}
        delisted = self._fx.get("delist_dates") or {}
        out: list[str] = []
        for raw, listed_on in listed.items():
            tkr = str(raw).upper()
            if str(listed_on)[:10] > asof_s:
                continue
            gone = delisted.get(tkr) or delisted.get(raw)
            if gone and str(gone)[:10] <= asof_s:
                continue
            out.append(tkr)
        out.sort()
        self._quality = {
            "status": "ok",
            "reason": "stub_asof",
            "provider": self.provider_id,
            "asof": asof_s,
            "n": len(out),
        }
        return out

    def wikipedia_now(self) -> list[str]:
        raw = self._fx.get("wikipedia_now") or list((self._fx.get("list_dates") or {}).keys())
        return sorted({str(t).upper() for t in raw})

    def fundamentals_asof(self, ticker: str, asof: str) -> dict[str, float | None]:
        tkr = str(ticker).upper()
        asof_s = str(asof).strip()[:10]
        table: dict[str, Any] = self._fx.get("fundamentals") or {}
        eligible = [d for d in table if str(d)[:10] <= asof_s]
        if not eligible:
            return {k: None for k in PIT_FUNDAMENTAL_FIELDS}
        snap_date = max(eligible)
        row = (table.get(snap_date) or {}).get(tkr) or {}
        out: dict[str, float | None] = {}
        for key in PIT_FUNDAMENTAL_FIELDS:
            val = row.get(key)
            if val is None:
                out[key] = None
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                out[key] = None
                continue
            if fval != fval:  # NaN
                out[key] = None
                continue
            out[key] = fval
        return out

    def data_quality(self) -> dict[str, Any]:
        return dict(self._quality)
