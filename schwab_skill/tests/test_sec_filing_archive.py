"""Point-in-time SEC filing archive. The live snapshot stays at six filings."""

from __future__ import annotations

import json

from sec_enrichment import (
    backfill_filing_archive,
    cached_filings_by_ticker,
    filing_rows_from_columns,
    merge_filing_rows,
    merge_ticker_archive,
    pit_business_from_filings,
)


def _columns() -> dict:
    return {
        "form": ["8-K", "4", "10-Q", "10-K"],
        "filingDate": ["2024-08-01", "2024-07-01", "2024-05-01", "2016-02-01"],
        "accessionNumber": ["0001-16-000001", "0001-16-000002", "0001-16-000003", "0001-16-000004"],
        "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm"],
        "primaryDocDescription": ["restatement of revenue", "owner", "10-Q", "10-K"],
        "items": ["4.02", "", "", ""],
    }


def test_column_parser_keeps_every_target_form_and_an_old_date() -> None:
    rows = filing_rows_from_columns(_columns(), "0000320193")
    forms = [row["form"] for row in rows]
    assert forms == ["8-K", "10-Q", "10-K"]
    assert rows[-1]["date"] == "2016-02-01"
    assert rows[0]["accession"] == "0001-16-000001"
    assert "guidance_signal" not in rows[0]


def test_merge_keeps_an_older_filing_when_the_refresh_is_newer() -> None:
    older = [{"accession": "old", "form": "10-K", "date": "2016-02-01", "items": [], "url": "u"}]
    newer = [{"accession": "new", "form": "10-Q", "date": "2024-05-01", "items": [], "url": "v"}]
    merged = merge_filing_rows(older, newer)
    assert {row["accession"] for row in merged} == {"old", "new"}
    assert merged[0]["date"] == "2024-05-01"


def test_archive_feeds_the_backtest_lookup_and_live_cache_stays_separate(tmp_path) -> None:
    merge_ticker_archive("AAPL", filing_rows_from_columns(_columns(), "320193"), skill_dir=tmp_path)
    cache = {
        "tickers": {
            "AAPL": {
                "payload": {
                    "recent_filings": [
                        {"form": "10-Q", "date": "2025-11-01", "description": "", "url": "", "items": []}
                    ]
                }
            }
        }
    }
    (tmp_path / ".sec_cache.json").write_text(json.dumps(cache), encoding="utf-8")
    rows = cached_filings_by_ticker(tmp_path)["AAPL"]
    dates = {row["date"] for row in rows}
    assert "2016-02-01" in dates
    assert "2025-11-01" in dates
    assert len(json.loads((tmp_path / ".sec_cache.json").read_text(encoding="utf-8"))["tickers"]["AAPL"]["payload"]["recent_filings"]) == 1


def test_pit_window_is_the_six_filings_newest_at_entry() -> None:
    filings = [{"form": "8-K", "date": "2010-01-01", "description": "restatement", "items": ["4.02"]}]
    filings.extend(
        {"form": "10-Q", "date": f"2024-0{i}-01", "description": "10-Q", "items": []} for i in range(1, 7)
    )
    observed = pit_business_from_filings(filings, "2024-09-01")
    assert observed is not None
    assert observed["sec_risk_tag"] == "low"
    assert observed["business_as_of"] == "2024-06-01"


class _Response:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


def test_backfill_refuses_a_placeholder_user_agent(tmp_path) -> None:
    calls: list[str] = []
    result = backfill_filing_archive(
        ["AAPL"],
        skill_dir=tmp_path,
        user_agent="SchwabTradingBot contact@example.com",
        sleep_s=0,
        get=lambda *_args, **_kwargs: calls.append("hit") or _Response({}),
    )
    assert result["ok"] is False
    assert calls == []


def test_backfill_reads_a_shard_until_the_archive_reaches_earliest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("config.is_real_edgar_user_agent", lambda _ua: True)
    calls: list[str] = []

    def fake_get(url: str, headers=None, timeout=None) -> _Response:
        calls.append(url)
        if "company_tickers" in url:
            return _Response({"0": {"ticker": "AAPL", "cik_str": 320193}})
        if url.endswith("CIK0000320193.json"):
            recent = _columns()
            recent["filingDate"] = ["2024-08-01", "2024-07-01", "2024-05-01", "2024-02-01"]
            return _Response(
                {
                    "filings": {
                        "recent": recent,
                        "files": [
                            {
                                "name": "CIK0000320193-submissions-001.json",
                                "filingFrom": "2015-01-01",
                                "filingTo": "2023-12-31",
                            }
                        ],
                    }
                }
            )
        return _Response(
            {
                "form": ["10-K"],
                "filingDate": ["2016-02-01"],
                "accessionNumber": ["0001-16-000099"],
                "primaryDocument": ["old.htm"],
                "primaryDocDescription": ["10-K"],
                "items": [""],
            }
        )

    result = backfill_filing_archive(
        ["AAPL"],
        skill_dir=tmp_path,
        earliest="2016-01-01",
        user_agent="Research Desk research@oldlogan.example",
        sleep_s=0,
        get=fake_get,
    )
    assert result["ok"] is True
    dates = {row["date"] for row in cached_filings_by_ticker(tmp_path)["AAPL"]}
    assert "2016-02-01" in dates
    assert any(url.endswith("CIK0000320193-submissions-001.json") for url in calls)
