"""Operator-desk command routing and the local new-buy pause."""

from __future__ import annotations

import json

import config
from discord_operator import (
    DENIAL_MESSAGE,
    OperatorContext,
    OperatorIntent,
    apply_trading_pause,
    begin_operator_scan,
    compose_pending_embed,
    compose_today_embed,
    describe_regime,
    end_operator_scan,
    execute_operator_intent,
    gather_today_embed,
    help_text,
    is_authorized_operator,
    mutation_denial,
    operator_denial,
    parse_operator_text,
)
from operator_desk import load_signal_gate


def test_parse_desk_phrases():
    assert parse_operator_text("today").name == "today"
    assert parse_operator_text("how's the book").name == "portfolio"
    assert parse_operator_text("check nvda").ticker == "NVDA"
    assert parse_operator_text("check BRK.B").ticker == "BRK.B"
    pause = parse_operator_text("pause buys")
    assert pause is not None and pause.name == "halt" and pause.mutating is True
    resume = parse_operator_text("resume buys")
    assert resume is not None and resume.name == "resume" and resume.mutating is True
    scan = parse_operator_text("!scan")
    assert scan is not None and scan.name == "scan" and scan.mutating is True
    assert parse_operator_text("<@123> today").name == "today"
    assert parse_operator_text("please scan") is None
    assert parse_operator_text("!nope").name == "unknown"
    assert parse_operator_text("check").ticker is None
    report = parse_operator_text("!report AAPL tech")
    assert report is not None and report.ticker == "AAPL" and report.section == "tech"
    assert parse_operator_text("!help").name == "help"


def test_operator_identity_fails_closed(monkeypatch):
    monkeypatch.delenv("DISCORD_USER_ID", raising=False)
    assert is_authorized_operator("42", {}) is False
    denial = operator_denial("42", {})
    assert denial is not None and "DISCORD_USER_ID" in denial
    assert is_authorized_operator("42", {"DISCORD_USER_ID": "42"}) is True
    assert is_authorized_operator("7", {"DISCORD_USER_ID": "42"}) is False
    assert mutation_denial(OperatorIntent("today", False), "1", {}) is None
    monkeypatch.setenv("DISCORD_USER_ID", "99")
    assert is_authorized_operator("42", {"DISCORD_USER_ID": "42"}) is False
    assert is_authorized_operator("99", {}) is True


def test_operator_scan_is_single_flight():
    assert begin_operator_scan() is True
    try:
        assert begin_operator_scan() is False
    finally:
        end_operator_scan()
    assert begin_operator_scan() is True
    end_operator_scan()


def test_unauthorized_scan_does_not_run(tmp_path, monkeypatch):
    called = {"scan": False}

    def _boom(_ctx):
        called["scan"] = True
        raise AssertionError("scan should not run")

    monkeypatch.setattr("discord_operator._run_scan", _boom)
    last_scan: dict = {}
    reply = execute_operator_intent(
        OperatorIntent("scan", True),
        OperatorContext(skill_dir=tmp_path, last_scan=last_scan),
        authorized=False,
    )
    assert reply.content == DENIAL_MESSAGE
    assert called["scan"] is False
    assert last_scan == {}


def test_unauthorized_pause_does_not_write(tmp_path, monkeypatch):
    monkeypatch.delenv("USER_TRADING_HALTED", raising=False)
    execute_operator_intent(
        OperatorIntent("halt", True),
        OperatorContext(skill_dir=tmp_path),
        authorized=False,
    )
    assert config.user_trading_halt_state_path(tmp_path).exists() is False
    assert config.get_user_trading_halted(tmp_path) is False


def test_pause_and_resume_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("USER_TRADING_HALTED", raising=False)
    ctx = OperatorContext(skill_dir=tmp_path)
    paused = execute_operator_intent(OperatorIntent("halt", True), ctx, authorized=True)
    assert paused.content == "New buys paused."
    assert config.get_user_trading_halted(tmp_path) is True
    resumed = execute_operator_intent(OperatorIntent("resume", True), ctx, authorized=True)
    assert resumed.content == "New buys resumed."
    assert config.get_user_trading_halted(tmp_path) is False


def test_resume_does_not_clear_env_halt(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_TRADING_HALTED", "1")
    text = apply_trading_pause(tmp_path, paused=False)
    assert "USER_TRADING_HALTED" in text
    assert config.get_user_trading_halted(tmp_path) is True


def test_corrupt_halt_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("USER_TRADING_HALTED", raising=False)
    config.user_trading_halt_state_path(tmp_path).write_text("not-json", encoding="utf-8")
    assert config.get_user_trading_halted(tmp_path) is True
    config.user_trading_halt_state_path(tmp_path).write_text("{}", encoding="utf-8")
    assert config.get_user_trading_halted(tmp_path) is True


def test_explicit_false_file_is_not_halted(tmp_path, monkeypatch):
    monkeypatch.delenv("USER_TRADING_HALTED", raising=False)
    config.set_user_trading_halt_state(False, tmp_path)
    assert config.get_user_trading_halted(tmp_path) is False


def _stub_today_sources(monkeypatch, *, regime: str, quality: str) -> None:
    monkeypatch.setattr("discord_operator.describe_regime", lambda _skill_dir: regime)
    monkeypatch.setattr("discord_operator.describe_data_quality", lambda _skill_dir: quality)
    monkeypatch.setattr(
        "discord_confirm._build_status_embed",
        lambda _skill_dir: {
            "title": "System Status",
            "color": 1,
            "fields": [
                {"name": "Bot", "value": "Online", "inline": True},
                {"name": "Last Scan", "value": "Not yet", "inline": True},
            ],
        },
    )
    monkeypatch.setattr(
        "discord_confirm._build_portfolio_embed",
        lambda _skill_dir: {
            "title": "Portfolio Snapshot",
            "description": "No open positions.",
            "fields": [],
        },
    )


def test_today_quotes_loader_summary(tmp_path, monkeypatch):
    monkeypatch.delenv("USER_TRADING_HALTED", raising=False)
    payload = {
        "ranking": [
            {
                "config_id": "control_legacy",
                "pf_mean_treatment": 1.25,
                "worst_era_pf_treatment": 1.05,
            }
        ]
    }
    (tmp_path / "phase1_progress_signal_gate.json").write_text(json.dumps(payload), encoding="utf-8")
    summary = str(load_signal_gate(tmp_path)["summary"])
    _stub_today_sources(monkeypatch, regime="SPY above 200 SMA ($500.00 vs $490.00)", quality="degraded: stale quote")
    embed = gather_today_embed(tmp_path, pending_count=2)
    assert embed["title"] == "Today"
    assert summary in embed["description"]
    assert "New buys allowed." in embed["description"]
    pending = next(field for field in embed["fields"] if field["name"] == "Pending")
    assert "**2**" in pending["value"]
    quality = next(field for field in embed["fields"] if field["name"] == "Data quality")
    assert quality["value"] == "degraded: stale quote"
    assert "report" not in json.dumps(embed).lower()


def test_today_missing_gate_does_not_invent_a_profit_factor(tmp_path, monkeypatch):
    monkeypatch.delenv("USER_TRADING_HALTED", raising=False)
    summary = str(load_signal_gate(tmp_path)["summary"])
    assert "missing" in summary.lower()
    _stub_today_sources(monkeypatch, regime="unavailable (SPY regime data missing)", quality="unknown")
    embed = gather_today_embed(tmp_path, pending_count=0)
    assert summary in embed["description"]
    assert "pf mean" not in embed["description"].lower()


def test_today_card_shows_file_pause(tmp_path, monkeypatch):
    monkeypatch.delenv("USER_TRADING_HALTED", raising=False)
    config.set_user_trading_halt_state(True, tmp_path)
    embed = compose_today_embed(
        status={"color": 1, "fields": []},
        portfolio={"description": "No open positions."},
        pending_count=0,
        gate_summary="phase1_progress_signal_gate.json is missing. Promotion status is unknown.",
        halted=config.get_user_trading_halted(tmp_path),
        regime_line="unavailable (SPY regime data missing)",
        data_quality_line="ok",
    )
    assert "New buys paused." in embed["description"]
    assert embed["color"] == 0xE74C3C


def test_pending_embed_states_market_buy():
    embed = compose_pending_embed({"abc": {"ticker": "AAPL", "qty": 4, "price": 100.0}})
    assert embed["fields"][0]["name"] == "AAPL"
    assert "BUY 4 @ MARKET" in embed["fields"][0]["value"]


def test_help_lists_the_desk():
    text = help_text()
    assert "`today`" in text
    assert "pause buys" in text
    assert "resume buys" in text
    assert "`pending`" in text
    reply = execute_operator_intent(
        OperatorIntent("help", False),
        OperatorContext(skill_dir=config.SKILL_DIR),
        authorized=False,
    )
    assert reply.content is not None and "Operator desk" in reply.content


def test_check_intent_uses_quick_check(tmp_path, monkeypatch):
    monkeypatch.setattr("full_report.quick_check", lambda ticker: {"title": ticker})
    reply = execute_operator_intent(
        OperatorIntent("check", False, ticker="AAPL"),
        OperatorContext(skill_dir=tmp_path),
        authorized=False,
    )
    assert reply.embeds[0]["title"] == "AAPL"


def test_regime_line_does_not_call_missing_data_bearish(tmp_path, monkeypatch):
    monkeypatch.setattr("schwab_auth.DualSchwabAuth", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        "sector_strength.is_market_regime_bullish",
        lambda auth, skill_dir: (False, {"data_unavailable": True}),
    )
    assert describe_regime(tmp_path) == "unavailable (SPY regime data missing)"
