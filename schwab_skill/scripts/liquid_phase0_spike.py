"""Phase 0 spike: can we get historical funding/OI/candles for Liquid (xyz) perps?"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.hyperliquid.xyz/info"
ROOT = Path(__file__).resolve().parent.parent


def post(payload: dict, timeout: int = 20):
    r = requests.post(API, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def main() -> None:
    out: dict = {"checked_at": datetime.now(timezone.utc).isoformat(), "api": API}

    # 1. xyz dex universe + live contexts (OI, funding, price)
    try:
        meta_ctx = post({"type": "metaAndAssetCtxs", "dex": "xyz"})
        meta, ctxs = meta_ctx[0], meta_ctx[1]
        universe = [a["name"] for a in meta.get("universe", [])]
        out["xyz_universe_n"] = len(universe)
        out["xyz_universe_sample"] = universe[:25]
        by_name = {}
        for a, c in zip(meta.get("universe", []), ctxs):
            by_name[a["name"]] = {
                "funding": c.get("funding"), "openInterest": c.get("openInterest"),
                "markPx": c.get("markPx"), "dayNtlVlm": c.get("dayNtlVlm"),
                "maxLeverage": a.get("maxLeverage"),
            }
        for probe in ("xyz:AAPL", "xyz:CVX", "xyz:BIIB", "xyz:NVDA", "xyz:DELL"):
            out[f"ctx_{probe}"] = by_name.get(probe)
    except Exception as e:
        out["meta_error"] = f"{type(e).__name__}: {e}"

    # 2. Funding history depth for xyz:AAPL
    try:
        now_ms = int(time.time() * 1000)
        fh = post({"type": "fundingHistory", "coin": "xyz:AAPL", "startTime": 0, "endTime": now_ms})
        if fh:
            first, last = fh[0], fh[-1]
            out["funding_history_aapl"] = {
                "n_returned": len(fh),
                "first_time": first.get("time"),
                "first_iso": datetime.fromtimestamp(first["time"] / 1000, tz=timezone.utc).isoformat(),
                "last_iso": datetime.fromtimestamp(last["time"] / 1000, tz=timezone.utc).isoformat(),
                "sample": first,
                "note": "API may paginate at 500 rows; first_time may not be listing inception",
            }
        else:
            out["funding_history_aapl"] = {"n_returned": 0}
    except Exception as e:
        out["funding_error"] = f"{type(e).__name__}: {e}"

    # 3. Candle history
    try:
        now_ms = int(time.time() * 1000)
        candles = post({"type": "candleSnapshot", "req": {"coin": "xyz:AAPL", "interval": "1d", "startTime": 0, "endTime": now_ms}})
        if candles:
            out["candles_aapl_1d"] = {
                "n": len(candles),
                "first_iso": datetime.fromtimestamp(candles[0]["t"] / 1000, tz=timezone.utc).isoformat(),
                "last_iso": datetime.fromtimestamp(candles[-1]["t"] / 1000, tz=timezone.utc).isoformat(),
            }
        else:
            out["candles_aapl_1d"] = {"n": 0}
    except Exception as e:
        out["candle_error"] = f"{type(e).__name__}: {e}"

    # 4. Coverage vs last 90d scan_results
    try:
        db = ROOT / "webapp" / "webapp.db"
        con = sqlite3.connect(str(db))
        rows = con.execute(
            "SELECT DISTINCT ticker FROM scan_results WHERE created_at >= datetime('now','-90 days')"
        ).fetchall()
        scan_tickers = {r[0] for r in rows}
        try:
            universe = out.get("xyz_universe_sample") and [a["name"] for a in meta.get("universe", [])]  # type: ignore[name-defined]
        except Exception:
            universe = []
        liquid_tickers = {u.split(":", 1)[-1] for u in universe}
        hits = sorted(scan_tickers & liquid_tickers)
        out["coverage_90d"] = {
            "scan_distinct_tickers": len(scan_tickers),
            "liquid_covered": len(hits),
            "coverage_pct": round(100 * len(hits) / max(1, len(scan_tickers)), 1),
            "covered_tickers": hits,
        }
    except Exception as e:
        out["coverage_error"] = f"{type(e).__name__}: {e}"

    dest = ROOT / "validation_artifacts" / "liquid_phase0_spike.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    print("WROTE", dest)


if __name__ == "__main__":
    main()
