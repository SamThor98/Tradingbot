"""Operator desk: filter, grade, and brief without a live dashboard."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import requests

from operator_desk import (
    FIELD_LIMIT,
    DashboardTools,
    baseline_backtest_spec,
    brief_embed,
    claim_desk_slot,
    compose_brief,
    deliberate_name,
    desk_slot_key,
    empty_book_decision,
    filter_signals,
    load_signal_gate,
    research_candidates,
    research_scan_body,
    run_operator_desk,
    run_scan_agent,
    set_local_scan_status,
    strategy_labels,
)
from webapp.scan_payload import parse_scan_run_body


class _FakeTools:
    def __init__(self, routes: dict[tuple[str, str], dict]) -> None:
        self.routes = routes
        self.posts: list[tuple[str, dict | None]] = []

    def get(self, path: str, *, params=None, timeout: float = 30) -> dict:
        return dict(self.routes.get(("GET", path)) or {"ok": False, "data": None, "error": f"missing {path}"})

    def post(self, path: str, body=None, *, params=None, timeout: float = 60) -> dict:
        self.posts.append((path, body))
        return dict(self.routes.get(("POST", path)) or {"ok": False, "data": None, "error": f"missing {path}"})


def _ledger(path: Path, pf: float, worst: float) -> None:
    path.write_text(
        json.dumps(
            {
                "ranking": [
                    {
                        "config_id": "control_legacy",
                        "pf_mean_treatment": pf,
                        "worst_era_pf_treatment": worst,
                        "passes_guardrails": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_gate_quotes_ledger_and_does_not_clear(tmp_path: Path) -> None:
    _ledger(tmp_path / "phase1_progress_signal_gate.json", 1.089, 0.903)
    gate = load_signal_gate(tmp_path)
    assert gate["pf_mean_treatment"] == 1.089
    assert gate["worst_era_pf_treatment"] == 0.903
    assert gate["clears_promotion"] is False
    assert "does not clear promotion" in gate["summary"]


def test_filter_drops_low_score_and_bad_quality() -> None:
    kept, dropped = filter_signals(
        [
            {"ticker": "AAA", "signal_score": 72, "data_quality": "ok"},
            {"ticker": "BBB", "signal_score": 40, "data_quality": "ok"},
            {"ticker": "CCC", "signal_score": 80, "data_quality": "stale"},
        ],
        {"scan_blocked": False, "data_quality": "ok"},
    )
    assert [row["ticker"] for row in kept] == ["AAA", "BBB"]
    assert kept[1]["below_bar"] is True
    assert [row["ticker"] for row in dropped] == ["CCC"]


def test_deliberate_splits_when_seats_disagree() -> None:
    card = deliberate_name(
        {
            "ticker": "AAA",
            "signal_score": 70,
            "data_quality": "ok",
            "book": "shortlist",
            "row": {
                "ticker": "AAA",
                "signal_score": 70,
                "_data_quality": "ok",
                "trend_stack_ok": True,
                "sma200_rising": True,
                "vcp_present": False,
                "score_components": {"pts_volume": 0, "pts_52w": 10},
                "stop_pct": 0.08,
                "price": 100,
                "sma_50": 90,
            },
        }
    )
    assert card["stance"] in {"split", "pass", "constructive", "abstain"}
    assert card["opinions"][0]["source"] == "scanner"
    assert card["opinions"][1]["source"] == "grade"
    assert "buy" not in card["reasoning"].lower()
    assert card["book"] == "shortlist"


def test_desk_brief_uses_stored_backtest_and_posts_discord(tmp_path: Path) -> None:
    _ledger(tmp_path / "phase1_progress_signal_gate.json", 1.089, 0.903)
    tools = _FakeTools(
        {
            ("GET", "/api/health"): {"ok": True, "data": {"status": "ok"}, "error": None},
            ("POST", "/api/scan"): {"ok": True, "data": {"started": True, "job_id": "job1", "status": "running"}, "error": None},
            ("GET", "/api/scan/status"): {
                "ok": True,
                "data": {
                    "status": "completed",
                    "signals_found": 1,
                    "signals": [
                        {
                            "ticker": "AAA",
                            "signal_score": 72,
                            "data_quality": "ok",
                            "trend_stack_ok": True,
                            "sma200_rising": True,
                            "vcp_present": True,
                            "score_components": {"pts_volume": 20, "pts_52w": 10},
                            "stop_pct": 0.08,
                            "price": 100,
                            "sma_50": 90,
                        }
                    ],
                    "shortlist_signals": [],
                    "diagnostics_summary": {
                        "scan_blocked": False,
                        "data_quality": "ok",
                        "data_quality_reasons": [],
                        "top_blockers": [],
                    },
                    "diagnostics": {
                        "correlation_guard_mode": "off",
                        "correlation_guard_would_demote": 2,
                        "correlation_guard_demoted": 0,
                    },
                },
                "error": None,
            },
            ("GET", "/api/backtest-runs"): {
                "ok": True,
                "data": [
                    {
                        "id": "run1",
                        "status": "success",
                        "result_summary": {"profit_factor_net": 1.05, "total_trades": 12},
                    }
                ],
                "error": None,
            },
            ("POST", "/api/backtest-runs"): {"ok": True, "data": {"run_id": "run2"}, "error": None},
        }
    )
    sent: list[dict] = []

    def _send(embed: dict) -> bool:
        sent.append(embed)
        return True

    brief = run_operator_desk(
        tools,
        skill_dir=tmp_path,
        run_scan=True,
        queue_backtest=True,
        notify_discord=True,
        today=date(2026, 10, 6),
        sender=_send,
    )
    assert "does not clear promotion" in brief["text"]
    assert "AAA" in brief["text"]
    assert "1.05" in brief["text"]
    assert brief["discord_sent"] is True
    assert sent and sent[0]["title"] == "Operator Desk"
    posted = {field["name"]: field["value"] for field in sent[0]["fields"]}
    assert "Review AAA" in sent[0]["description"]
    assert "Gate short" in sent[0]["description"]
    assert "AAA" in posted["Setups"]
    assert "Ranked by signal_score" not in posted["Setups"]
    assert all(len(field["value"]) <= 1024 for field in sent[0]["fields"])
    scan_body = tools.posts[0][1]
    assert isinstance(scan_body, dict)
    assert scan_body["universe_preset"] == "sp1500"
    overrides = scan_body["strategy_overrides"]
    assert isinstance(overrides, dict)
    assert overrides["signal_universe_mode"] == "broad"
    assert overrides["quality_watchlist_prefilter_enabled"] is False
    assert "trend_breakout" in scan_body["strategy_ids"]
    assert "pead_primary" in scan_body["strategy_ids"]
    assert tools.posts[1][0] == "/api/backtest-runs"
    queued = tools.posts[1][1]
    assert isinstance(queued, dict)
    spec = queued["spec"]
    assert isinstance(spec, dict)
    assert spec["start_date"] == "2025-10-05"
    assert spec["end_date"] == "2026-10-05"
    assert "buy" not in brief["text"].lower()
    watch = tmp_path / "validation_artifacts" / "live_stack_watch.jsonl"
    row = json.loads(watch.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["data_quality"] == "ok"
    assert row["signals_found"] == 1
    assert row["correlation_guard_mode"] == "off"
    assert row["correlation_guard_would_demote"] == 2
    assert row["correlation_guard_demoted"] == 0


def test_loopback_skips_tls_verify() -> None:
    client = DashboardTools("https://127.0.0.1:8182")
    assert client.verify is False
    remote = DashboardTools("https://example.com")
    assert remote.verify is True


class _RetrySession:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0
        self.closed = 0

    def request(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise requests.exceptions.ConnectionError(
                "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
            )

        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"ok": True, "data": {"status": "running"}, "error": None}

        return _Resp()

    def close(self) -> None:
        self.closed += 1


def test_dropped_connection_retries_once_on_a_fresh_socket() -> None:
    session = _RetrySession(1)
    client = DashboardTools("https://127.0.0.1:8182", session=session)
    out = client.get("/api/scan/status")
    assert out["ok"] is True
    assert out["data"]["status"] == "running"
    assert session.calls == 2
    assert session.closed == 1


def test_dropped_connection_is_reported_after_one_retry() -> None:
    session = _RetrySession(2)
    client = DashboardTools("https://127.0.0.1:8182", session=session)
    out = client.get("/api/health")
    assert out["ok"] is False
    assert "RemoteDisconnected" in str(out["error"])
    assert session.calls == 2


def test_in_process_scan_status_skips_http_while_the_job_is_active() -> None:
    http_gets: list[str] = []

    class _Tools:
        def post(self, path: str, body=None, *, params=None, timeout: float = 60) -> dict:
            return {"ok": True, "data": {"job_id": "job"}, "error": None}

        def get(self, path: str, *, params=None, timeout: float = 30) -> dict:
            http_gets.append(path)
            return {"ok": False, "data": None, "error": "HTTP should not be used"}

    def reader() -> dict:
        return {
            "status": "completed",
            "signals_found": 1,
            "signals": [{"ticker": "AAA", "signal_score": 70, "data_quality": "ok"}],
            "shortlist_signals": [],
            "diagnostics": {"watchlist_size": 10, "universe_preset": "sp1500"},
            "diagnostics_summary": {"data_quality": "ok"},
        }

    previous = set_local_scan_status(reader)
    try:
        result = run_scan_agent(
            _Tools(),
            run_scan=True,
            timeout_s=30,
            sleeper=lambda _s: None,
            clock=lambda: 0,
        )
    finally:
        set_local_scan_status(previous)
    assert result["ok"] is True
    assert result["data"]["signals_found"] == 1
    assert http_gets == []


def test_idle_local_snapshot_still_reads_http_for_last_scan() -> None:
    class _Tools:
        def get(self, path: str, *, params=None, timeout: float = 30) -> dict:
            return {
                "ok": True,
                "data": {
                    "status": "idle",
                    "last_scan": {
                        "signals_found": 1,
                        "signals": [{"ticker": "BBB", "signal_score": 60, "data_quality": "ok"}],
                        "diagnostics_summary": {"data_quality": "ok"},
                        "diagnostics": {"watchlist_size": 4, "universe_preset": "sp1500"},
                    },
                },
                "error": None,
            }

        def post(self, path: str, body=None, *, params=None, timeout: float = 60) -> dict:
            return {"ok": False, "data": None, "error": "no post"}

    previous = set_local_scan_status(lambda: {"status": "idle", "signals": []})
    try:
        result = run_scan_agent(_Tools(), run_scan=False, timeout_s=5, sleeper=lambda _s: None, clock=lambda: 0)
    finally:
        set_local_scan_status(previous)
    assert result["ok"] is True
    assert result["data"]["signals"][0]["ticker"] == "BBB"


def test_baseline_window_is_one_year() -> None:
    spec = baseline_backtest_spec(date(2026, 10, 6))
    assert spec["start_date"] == "2025-10-05"
    assert spec["end_date"] == "2026-10-05"
    assert spec["universe_preset"] == "sp1500"
    assert spec["overrides"]["signal_universe_mode"] == "broad"
    assert spec["overrides"]["quality_watchlist_prefilter_enabled"] is False


def test_research_scan_body_is_a_valid_full_book() -> None:
    body = research_scan_body()
    parsed = parse_scan_run_body(body)
    assert parsed["universe_preset"] == "sp1500"
    assert parsed["env_overrides"]["SIGNAL_UNIVERSE_MODE"] == "broad"
    assert parsed["env_overrides"]["QUALITY_WATCHLIST_PREFILTER_ENABLED"] == "false"
    assert "weekly_swing" in parsed["strategy_ids"]
    assert "monthly_position" in parsed["strategy_ids"]


def test_research_candidates_keep_the_higher_score() -> None:
    rows = research_candidates(
        [{"ticker": "LOW", "signal_score": 10, "data_quality": "ok"}],
        [
            {"ticker": "HIGH", "signal_score": 80, "data_quality": "ok"},
            {"ticker": "LOW", "signal_score": 99, "data_quality": "ok"},
            {"ticker": "BAD", "signal_score": 90, "data_quality": "stale"},
        ],
        {},
    )
    assert [row["ticker"] for row in rows] == ["HIGH", "LOW"]
    assert rows[0]["book"] == "shortlist"


def test_empty_book_names_the_final_cut() -> None:
    scan = {
        "data": {
            "signals_found": 0,
            "data_quality": "ok",
            "funnel": {
                "watchlist": 100,
                "final": 0,
                "stages": [
                    {"key": "stage2", "value": 12, "filtered": 88},
                    {"key": "final", "value": 0, "filtered": 12},
                ],
            },
            "drop_counts": [
                {"key": "stage2_fail", "value": 88},
                {"key": "entry_timing_blocked", "value": 9},
                {"key": "pts_52w_cap_blocked", "value": 3},
            ],
        }
    }
    text = empty_book_decision(scan) or ""
    assert "12 of 100 cleared Stage 2" in text
    assert "final cut removed 12" in text
    assert "entry-timing gate" in text
    assert "52-week extension cap" in text
    assert "No ticker was left to grade" in text


def _graded(
    ticker: str,
    score: float,
    *,
    setup_fails: bool = False,
    stop_fails: bool = False,
    books: str | None = None,
) -> dict:
    seats = [
        {"seat": "Trend", "line": "Stack holds."},
        {"seat": "Setup", "line": "Contraction fails." if setup_fails else "Contraction holds."},
        {"seat": "Risk", "line": "Stop is through the 50-day." if stop_fails else "Stop sits under the 50-day."},
        {"seat": "Skeptic", "line": "Extension is inside the cap."},
    ]
    if books:
        seats.append({"seat": "Business", "line": books})
    return {
        "ticker": ticker,
        "signal_score": score,
        "letter": "F" if stop_fails else "B",
        "stance": "split" if setup_fails or stop_fails else "constructive",
        "below_bar": score < 50,
        "seats": seats,
        "strategies": ["Stage 2 breakout", "10-month SMA"] if score >= 40 else ["Stage 2 breakout"],
    }


def test_strategy_labels_keep_every_pass() -> None:
    labels = strategy_labels(
        {
            "strategy_attribution": {
                "passed_ids": ["trend_breakout", "monthly_position", "weekly_swing"],
                "passed_names": ["Stage 2 / VCP breakout", "10-month SMA (Faber)", "Weekly Stage 2"],
            }
        }
    )
    assert labels == ["Stage 2 breakout", "10-month SMA", "Weekly Stage 2"]


def test_desk_slot_is_weekday_0830(tmp_path: Path) -> None:
    assert desk_slot_key(datetime(2026, 10, 6, 8, 30), hour=8, minute=30) == "2026-10-06T0830"
    assert desk_slot_key(datetime(2026, 10, 6, 10, 5), hour=8, minute=30) is None
    assert desk_slot_key(datetime(2026, 10, 10, 8, 30), hour=8, minute=30) is None
    assert claim_desk_slot(tmp_path, "2026-10-06T0830") is True
    assert claim_desk_slot(tmp_path, "2026-10-06T0830") is False


def test_discord_brief_keeps_each_name_intact() -> None:
    names = [
        _graded("FTNT", 44),
        _graded("CRWD", 42),
        _graded("CRBG", 40, setup_fails=True, stop_fails=True, books="Books pass the dated checklist."),
        _graded("DELL", 38),
        _graded("PANW", 38),
        _graded("SMTC", 37, setup_fails=True),
        _graded("EHC", 34, setup_fails=True, stop_fails=True),
        _graded("AMN", 34, setup_fails=True),
    ]
    embed = brief_embed(
        "ignored wall of text",
        data_quality="ok",
        gate={
            "found": True,
            "config_id": "control_legacy",
            "pf_mean_treatment": 1.089,
            "worst_era_pf_treatment": 0.903,
            "clears_promotion": False,
        },
        scan={
            "ok": True,
            "data": {
                "fresh": False,
                "watchlist_size": 1504,
                "universe_preset": "sp1500",
                "signals_found": 40,
                "shortlist_count": 63,
                "top_blockers": [
                    "stage2_fail=511",
                    "breakout_not_confirmed=142",
                    "quality_gates_filtered=23",
                ],
            },
        },
        names=names,
        cleared_count=0,
        dropped_count=0,
        backtest={
            "ok": True,
            "data": {
                "latest_run_id": "11f9e3c160ce4c7eb722a656b6c66de7",
                "latest_metrics": {"profit_factor_net": 0.0, "total_trades": 0},
                "queued_run_id": None,
                "queue_error": None,
            },
        },
    )
    fields = embed["fields"]
    assert [field["name"] for field in fields] == ["Setups"]
    assert embed["description"] == "No entry · 0 cleared 50\nGate short · PF 1.089 · worst 0.903"
    research = fields[0]["value"]
    assert "Stack holds" not in research
    assert "books pass" not in research
    assert "11f9e3c160ce4c7eb722a656b6c66de7" not in embed["description"]
    assert "**FTNT** 44 B · Stage 2 breakout · 10-month SMA" in research
    assert "**CRBG** 40 F · Stage 2 breakout · 10-month SMA · split · setup fails · stop through 50d" in research
    for ticker in ("FTNT", "CRWD", "CRBG", "DELL", "PANW", "SMTC", "EHC", "AMN"):
        hits = [field["value"] for field in fields if ticker in field["value"]]
        assert len(hits) == 1
        assert f"**{ticker}**" in hits[0]
    assert all(len(field["value"]) <= FIELD_LIMIT for field in fields)


def test_discord_brief_splits_on_name_boundaries() -> None:
    names = [
        {
            "ticker": f"N{i:02d}",
            "signal_score": 80 - i,
            "letter": "B",
            "stance": "constructive " + ("x" * 180),
            "below_bar": False,
            "seats": [],
        }
        for i in range(8)
    ]
    embed = brief_embed(
        "",
        gate={"found": False, "summary": "ledger missing"},
        scan={"ok": True, "data": {"watchlist_size": 10, "signals_found": 12, "shortlist_count": 12, "fresh": True}},
        names=names,
        cleared_count=12,
        dropped_count=0,
        backtest={"ok": True, "summary": "no replay", "data": {}},
    )
    research = [field for field in embed["fields"] if str(field["name"]).startswith("Setups")]
    assert len(research) >= 2
    joined = "\n".join(field["value"] for field in research)
    for i in range(8):
        ticker = f"N{i:02d}"
        assert joined.count(f"**{ticker}**") == 1
        owners = [field["value"] for field in research if f"**{ticker}**" in field["value"]]
        assert len(owners) == 1
    assert all(len(field["value"]) <= FIELD_LIMIT for field in embed["fields"])


def test_compose_mentions_empty_book() -> None:
    text = compose_brief(
        gate={"summary": "ledger"},
        scan={"summary": "scan empty"},
        names=[],
        cleared_count=0,
        dropped_count=2,
        backtest={"summary": "no replay"},
        equity=None,
        health_ok=True,
    )
    assert "No gradable name" in text
    assert "No orders" in text


def test_unfinished_scan_does_not_claim_an_empty_book() -> None:
    text = compose_brief(
        gate={"summary": "ledger"},
        scan={"ok": False, "summary": "Scan is still running. No research list until it finishes."},
        names=[],
        cleared_count=0,
        dropped_count=0,
        backtest={"summary": "no replay"},
        equity=None,
        health_ok=True,
    )
    assert "No gradable name" not in text
    assert "still running" in text


class _StatusSequence:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.index = 0

    def get(self, path: str, *, params=None, timeout: float = 30) -> dict:
        payload = self.payloads[min(self.index, len(self.payloads) - 1)]
        self.index += 1
        return {"ok": True, "data": payload, "error": None}

    def post(self, path: str, body=None, *, params=None, timeout: float = 60) -> dict:
        return {"ok": True, "data": {"job_id": "job"}, "error": None}


def test_dropped_status_check_is_retried_and_stays_off_discord(tmp_path: Path) -> None:
    _ledger(tmp_path / "phase1_progress_signal_gate.json", 1.089, 0.903)
    raw = "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"

    class _Blip:
        def __init__(self) -> None:
            self.status_reads = 0

        def get(self, path: str, *, params=None, timeout: float = 30) -> dict:
            if path == "/api/scan/status":
                self.status_reads += 1
                if self.status_reads == 1:
                    return {"ok": False, "data": None, "error": raw}
                return {
                    "ok": True,
                    "data": {
                        "status": "completed",
                        "signals_found": 1,
                        "signals": [
                            {
                                "ticker": "AAA",
                                "signal_score": 72,
                                "data_quality": "ok",
                                "trend_stack_ok": True,
                                "sma200_rising": True,
                                "vcp_present": True,
                                "score_components": {"pts_volume": 20, "pts_52w": 10},
                                "stop_pct": 0.08,
                                "price": 100,
                                "sma_50": 90,
                            }
                        ],
                        "shortlist_signals": [],
                        "diagnostics_summary": {"scan_blocked": False, "data_quality": "ok"},
                    },
                    "error": None,
                }
            if path == "/api/health":
                return {"ok": True, "data": {"status": "ok"}, "error": None}
            if path == "/api/backtest-runs":
                return {"ok": True, "data": [], "error": None}
            return {"ok": False, "data": None, "error": f"missing {path}"}

        def post(self, path: str, body=None, *, params=None, timeout: float = 60) -> dict:
            return {"ok": True, "data": {"job_id": "job1", "status": "running"}, "error": None}

    sent: list[dict] = []

    def _send(embed: dict) -> bool:
        sent.append(embed)
        return True

    brief = run_operator_desk(
        _Blip(),
        skill_dir=tmp_path,
        run_scan=True,
        notify_discord=True,
        sleeper=lambda _s: None,
        clock=lambda: 0,
        sender=_send,
    )
    assert brief["scan"]["ok"] is True
    assert "AAA" in brief["text"]
    assert sent
    description = sent[0]["description"]
    assert "RemoteDisconnected" not in description
    assert "Connection aborted" not in description
    assert "Waiting on the scan" not in str(sent[0]["fields"])
    assert "Review AAA" in description


def test_status_blackout_brief_hides_the_exception() -> None:
    raw = "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
    times = iter([0, 0, 50, 120])
    tools = _StatusSequence([{"status": "running"}])

    def get(path: str, *, params=None, timeout: float = 30) -> dict:
        return {"ok": False, "data": None, "error": raw}

    tools.get = get  # type: ignore[method-assign]
    result = run_scan_agent(
        tools,
        run_scan=True,
        timeout_s=100,
        sleeper=lambda _s: None,
        clock=lambda: next(times),
    )
    assert result["ok"] is False
    assert "RemoteDisconnected" not in result["summary"]
    assert "Could not read scan status" in result["summary"]
    embed = brief_embed(
        "",
        gate={
            "found": True,
            "pf_mean_treatment": 1.089,
            "worst_era_pf_treatment": 0.903,
            "clears_promotion": False,
        },
        scan={"ok": False, "summary": raw},
        names=[],
    )
    assert "RemoteDisconnected" not in embed["description"]
    assert "Could not read scan status" in embed["description"]
    assert "Gate short" in embed["description"]
    setups = next(field["value"] for field in embed["fields"] if field["name"] == "Setups")
    assert setups == "Waiting on the scan."


def test_scan_agent_waits_through_watchdog_until_completed() -> None:
    tools = _StatusSequence(
        [
            {"status": "running"},
            {"status": "failed", "error": "Scan watchdog timeout: exceeded 3600s runtime (3601s elapsed)."},
            {
                "status": "completed",
                "signals_found": 1,
                "signals": [{"ticker": "AAA", "signal_score": 70, "data_quality": "ok"}],
                "diagnostics": {"watchlist_size": 1504, "universe_preset": "sp1500"},
                "diagnostics_summary": {"data_quality": "ok"},
            },
        ]
    )
    clock = iter([0, 0, 10, 20]).__next__
    result = run_scan_agent(
        tools,
        run_scan=False,
        wait_if_running=True,
        timeout_s=100,
        sleeper=lambda _s: None,
        clock=clock,
    )
    assert result["ok"] is True
    assert result["data"]["signals_found"] == 1
