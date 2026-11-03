"""
System orchestration for the trading bot.

Schedules Morning Brief (9:25 AM ET), signal scan (9:30 AM ET),
operator desk (8:30 AM ET weekdays when OPERATOR_DESK_MODE is not off),
tradeable-universe Discord brief (8:30 AM CT weekdays),
hold reminders (3:30 PM ET), research close (4:00 PM ET: score hypotheses,
then self-study), experiment brief (4:15 PM ET weekdays), and
weekly digest (Sunday 6:00 PM ET). Global try/except fires
critical error alert on crash.
"""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import schedule

from logger_setup import get_logger, setup_logging
from notifier import send_alert
from schwab_auth import DualSchwabAuth

SKILL_DIR = Path(__file__).resolve().parent
TZ_NY = ZoneInfo("America/New_York")


def daily_heartbeat(skill_dir: Path | str | None = None) -> None:
    """
    Check both Schwab API connections, get account value, send status via notifier.
    Now called internally by build_morning_brief as a sub-check.
    """
    skill_dir = Path(skill_dir or SKILL_DIR)
    env_path = skill_dir / ".env"
    log = get_logger(__name__)

    auth = DualSchwabAuth(skill_dir=skill_dir)
    status_parts = []

    try:
        auth.get_market_token()
        status_parts.append("Market Session: OK")
    except Exception as e:
        status_parts.append(f"Market Session: FAILED ({e})")
        log.warning("Market session check failed: %s", e)

    try:
        auth.get_account_token()
        status_parts.append("Account Session: OK")
    except Exception as e:
        status_parts.append(f"Account Session: FAILED ({e})")
        log.warning("Account session check failed: %s", e)

    account_value = None
    try:
        from execution import get_account_status
        result = get_account_status(auth=auth, skill_dir=skill_dir)
        if isinstance(result, dict):
            accounts = result.get("accounts", [])
            for acc in accounts:
                sec = acc.get("securitiesAccount", acc)
                equity = sec.get("currentBalances", {}).get("equity")
                cash = sec.get("currentBalances", {}).get("cashBalance")
                if equity is not None:
                    account_value = float(equity)
                    break
                if cash is not None and account_value is None:
                    account_value = float(cash)
            if account_value is not None:
                status_parts.append(f"Account Value: ${account_value:,.2f}")
        else:
            status_parts.append(f"Account Fetch: {result}")
    except Exception as e:
        status_parts.append(f"Account Fetch: FAILED ({e})")
        log.warning("Account fetch failed: %s", e)

    msg = "Trading Bot Daily Heartbeat\n" + "\n".join(status_parts)
    send_alert(msg, kind="heartbeat", env_path=env_path)
    log.info("Heartbeat sent: %s", msg)


def build_morning_brief(skill_dir: Path | str | None = None) -> None:
    """
    Comprehensive morning briefing at 9:25 AM ET.
    Sends a structured embed with fields for market, sectors, and portfolio.
    """
    from datetime import timezone

    from notifier import send_embed_alert

    skill_dir = Path(skill_dir or SKILL_DIR)
    env_path = skill_dir / ".env"
    log = get_logger(__name__)
    now = datetime.now(TZ_NY)

    embed: dict = {
        "title": f"Morning Brief - {now.strftime('%b %d, %Y')}",
        "color": 0x3498DB,
        "fields": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Scan runs at 9:30 ET | Use /check <ticker> for a quick look"},
    }

    auth = None
    try:
        auth = DualSchwabAuth(skill_dir=skill_dir)
    except Exception as e:
        log.warning("Morning brief auth failed: %s", e)

    try:
        from market_data import get_daily_history
        spy_df = get_daily_history("SPY", days=5, auth=auth, skill_dir=skill_dir)
        if not spy_df.empty and len(spy_df) >= 2:
            spy_price = float(spy_df["close"].iloc[-1])
            spy_prev = float(spy_df["close"].iloc[-2])
            spy_chg = (spy_price - spy_prev) / spy_prev * 100
            embed["fields"].append({
                "name": "Market",
                "value": f"SPY: **${spy_price:,.2f}** ({spy_chg:+.1f}% 1d)",
                "inline": True,
            })
    except Exception as e:
        log.warning("Morning brief SPY: %s", e)

    try:
        from sector_strength import get_sector_heatmap
        heatmap = get_sector_heatmap(auth, skill_dir)
        winning_rows = [r for r in heatmap.get("rows", []) if r["winning"]]
        if winning_rows:
            names = ", ".join(f"**{r['etf']}**" for r in winning_rows[:5])
            embed["fields"].append({
                "name": "Winning Sectors",
                "value": names,
                "inline": True,
            })
    except Exception as e:
        log.warning("Morning brief sectors: %s", e)

    try:
        from execution import get_account_status
        if auth:
            acct = get_account_status(auth=auth, skill_dir=skill_dir)
            if isinstance(acct, dict):
                pos_count = 0
                total_val = 0.0
                day_pl = 0.0
                for acc in acct.get("accounts", []):
                    sec = acc.get("securitiesAccount", acc)
                    for pos in sec.get("positions", []):
                        q = pos.get("longQuantity", 0) or pos.get("shortQuantity", 0)
                        if q > 0:
                            pos_count += 1
                            total_val += pos.get("marketValue", 0)
                            day_pl += pos.get("currentDayProfitLoss", 0)
                    eq = sec.get("currentBalances", {}).get("equity")
                    if eq and total_val == 0:
                        total_val = float(eq)
                if pos_count > 0:
                    embed["fields"].append({
                        "name": "Portfolio Snapshot",
                        "value": f"{pos_count} positions | ${total_val:,.0f} | Day P/L: ${day_pl:+,.0f}",
                        "inline": False,
                    })
    except Exception as e:
        log.warning("Morning brief portfolio: %s", e)

    try:
        from execution import get_execution_safety_summary
        safety = get_execution_safety_summary(skill_dir=skill_dir, days=1)
        ev = safety.get("events", {})
        guardrail_blocks = ev.get("guardrail_blocked_order", 0)
        exits_allowed = ev.get("guardrail_exit_allowed", 0)
        stop_ok = ev.get("stop_protection_attached", 0)
        stop_fail = ev.get("stop_protection_failed", 0)
        shadow_count = ev.get("action_shadow", 0)
        live_count = ev.get("action_live", 0)
        embed["fields"].append({
            "name": "Execution Safety - 24h",
            "value": (
                f"Blocks: {guardrail_blocks} | Exit bypass: {exits_allowed}\n"
                f"Stops ok/fail: {stop_ok}/{stop_fail}\n"
                f"Shadow/Live: {shadow_count}/{live_count}"
            ),
            "inline": False,
        })
    except Exception as e:
        log.warning("Morning brief execution safety: %s", e)

    send_embed_alert(embed, env_path=env_path)
    log.info("Morning brief sent")


def build_weekly_digest(skill_dir: Path | str | None = None) -> None:
    """
    Weekly performance digest -- scheduled for Sundays at 6 PM ET.
    Sends a structured embed with fields for signals, fills, and self-study.
    """
    import json
    from datetime import timezone

    from notifier import send_embed_alert

    skill_dir = Path(skill_dir or SKILL_DIR)
    env_path = skill_dir / ".env"
    log = get_logger(__name__)
    now = datetime.now(TZ_NY)
    week_start = (now - timedelta(days=7)).strftime("%b %d")
    week_end = now.strftime("%b %d, %Y")

    embed: dict = {
        "title": f"Weekly Digest - {week_start} to {week_end}",
        "color": 0x9B59B6,
        "fields": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Self-study runs daily at 4 PM ET"},
    }

    try:
        history_path = skill_dir / ".signal_alert_history.json"
        signals_this_week = 0
        if history_path.exists():
            data = json.loads(history_path.read_text())
            if isinstance(data, dict):
                cutoff = (now - timedelta(days=7)).isoformat()
                for ticker, entries in data.items():
                    if isinstance(entries, list):
                        signals_this_week += sum(
                            1 for e in entries
                            if isinstance(e, dict) and (e.get("timestamp", "") or e.get("date", "")) >= cutoff
                        )
                    elif isinstance(entries, dict) and (entries.get("timestamp", "") or entries.get("date", "")) >= cutoff:
                        signals_this_week += 1
        embed["fields"].append({
            "name": "Signals",
            "value": f"**{signals_this_week}** generated this week",
            "inline": True,
        })
    except Exception as e:
        log.warning("Weekly digest signals: %s", e)

    try:
        outcomes_path = skill_dir / ".trade_outcomes.json"
        if outcomes_path.exists():
            outcomes = json.loads(outcomes_path.read_text())
            if isinstance(outcomes, list):
                cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
                week_trades = [o for o in outcomes if (o.get("date", "") or "") >= cutoff]
                buys = sum(1 for o in week_trades if (o.get("side", "").upper()) == "BUY")
                sells = sum(1 for o in week_trades if (o.get("side", "").upper()) == "SELL")
                embed["fields"].append({
                    "name": "Fills",
                    "value": f"**{buys}** buy(s), **{sells}** sell(s)",
                    "inline": True,
                })
    except Exception as e:
        log.warning("Weekly digest outcomes: %s", e)

    try:
        study_path = skill_dir / ".self_study.json"
        if study_path.exists():
            study = json.loads(study_path.read_text())
            win_rate = study.get("win_rate")
            rt_count = study.get("round_trips_count", 0)
            suggested = study.get("suggested_min_conviction")
            by_conv = study.get("by_conviction", {})

            insights = []
            if rt_count > 0 and win_rate is not None:
                insights.append(f"{rt_count} round trips, {win_rate:.0f}% win rate")

            best_band = None
            best_wr = 0
            for band, info in by_conv.items():
                wr = info.get("win_rate", 0)
                if wr > best_wr and info.get("count", 0) >= 2:
                    best_wr = wr
                    best_band = band
            if best_band:
                insights.append(f"Best band: {best_band} ({best_wr:.0f}%)")

            by_sector = study.get("by_sector", {})
            best_sec = None
            best_sec_ret = -999
            for sec, info in by_sector.items():
                avg_ret = info.get("avg_return_pct", -999)
                if avg_ret > best_sec_ret and info.get("count", 0) >= 2:
                    best_sec_ret = avg_ret
                    best_sec = sec
            if best_sec:
                insights.append(f"Best sector: {best_sec} ({best_sec_ret:+.1f}%)")

            if suggested:
                insights.append(f"Min conviction: {suggested}")

            if insights:
                embed["fields"].append({
                    "name": "Self-Study",
                    "value": "\n".join(insights),
                    "inline": False,
                })
    except Exception as e:
        log.warning("Weekly digest self-study: %s", e)

    try:
        from execution import get_execution_safety_summary
        safety = get_execution_safety_summary(skill_dir=skill_dir, days=7)
        ev = safety.get("events", {})
        lines = [
            f"Guardrail blocks: {ev.get('guardrail_blocked_order', 0)}",
            f"Exit bypass allowed: {ev.get('guardrail_exit_allowed', 0)}",
            f"Stop attached/failed: {ev.get('stop_protection_attached', 0)}/{ev.get('stop_protection_failed', 0)}",
            f"Shadow/Live actions: {ev.get('action_shadow', 0)}/{ev.get('action_live', 0)}",
        ]
        top_reasons = safety.get("top_reasons") or []
        if top_reasons:
            first = top_reasons[0]
            lines.append(f"Top failure reason: {first.get('reason')} ({first.get('count')})")
        embed["fields"].append({
            "name": "Execution Safety - 7d",
            "value": "\n".join(lines),
            "inline": False,
        })
    except Exception as e:
        log.warning("Weekly digest execution safety: %s", e)

    try:
        from signal_scanner import get_signal_quality_summary
        quality = get_signal_quality_summary(skill_dir=skill_dir, days=7)
        d = quality.get("diagnostics", {})
        lines = [
            f"Scans: {quality.get('scan_count', 0)} | Signals: {quality.get('signals_total', 0)}",
            f"Avg score: {quality.get('avg_signal_score', 0):.1f} | Avg conviction: {quality.get('avg_conviction', 0):.1f}",
            f"Would-filter/filtered: {d.get('quality_gates_would_filter', 0)}/{d.get('quality_gates_filtered', 0)}",
            f"Rank-filter shadow: {d.get('rank_filter_would_drop_any', 0)} | Stage2 shadow: {d.get('stage2_shadow_would_filter', 0)}",
            f"Entry-timing shadow: {d.get('entry_shadow_would_filter_any', 0)}",
            f"Weak breakout vol: {d.get('low_breakout_volume', 0)} | Weak MiroFish: {d.get('weak_mirofish_alignment', 0)}",
        ]
        embed["fields"].append({
            "name": "Signal Quality - 7d",
            "value": "\n".join(lines),
            "inline": False,
        })
    except Exception as e:
        log.warning("Weekly digest signal quality: %s", e)

    try:
        from signal_scanner import get_signal_quality_summary

        quality = get_signal_quality_summary(skill_dir=skill_dir, days=7)
        d = quality.get("diagnostics", {})
        sec_lines = [
            f"Tagged signals: {d.get('sec_tagged_signals', 0)} | Recent 8-K: {d.get('sec_recent_8k_count', 0)}",
            f"High-risk tags: {d.get('sec_high_risk_tag_count', 0)} | Data failures: {d.get('sec_data_failures', 0)}",
            f"Hint shadow/live: {d.get('sec_score_hint_shadow_adjustments', 0)}/{d.get('sec_score_hint_applied_count', 0)}",
        ]
        embed["fields"].append({
            "name": "SEC Enrichment - 7d",
            "value": "\n".join(sec_lines),
            "inline": False,
        })
    except Exception as e:
        log.warning("Weekly digest SEC diagnostics: %s", e)

    send_embed_alert(embed, env_path=env_path)
    log.info("Weekly digest sent")


def build_false_positive_report(skill_dir: Path | str | None = None) -> dict | None:
    """Weekly false-positive report over decision packets -- Sundays 6:15 PM ET.

    Closes the Phase 4 feedback loop: backfill matured packet outcomes, run the
    trade-review diagnostics (false positives by regime, edge decay by setup,
    execution drag by condition), derive advisory tuning proposals, send a
    Discord embed, and persist a JSON artifact under ``validation_artifacts/``.
    """
    import json
    from datetime import timezone

    from notifier import send_embed_alert

    skill_dir = Path(skill_dir or SKILL_DIR)
    env_path = skill_dir / ".env"
    log = get_logger(__name__)

    # Resolve matured packets first so the report reflects fresh outcomes.
    # (Idempotent with the 6 PM digest backfill -- already-resolved packets skip.)
    backfill: dict = {}
    try:
        from core.outcome_backfill import run_local_backfill

        backfill = run_local_backfill(skill_dir)
        log.info(
            "FP report backfill: resolved=%s/%s", backfill.get("resolved"), backfill.get("total")
        )
    except Exception as e:
        log.warning("FP report backfill failed: %s", e)

    try:
        from core import decision_packet
        from core.trade_review import weekly_report
        from core.weight_feedback import propose

        packets = decision_packet.load_packets(skill_dir)
        report = weekly_report(packets)
        proposals = propose(report)
    except Exception as e:
        log.warning("False-positive report failed: %s", e)
        return None

    artifact = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backfill": backfill,
        "report": report,
        "tuning_proposals": proposals,
    }

    try:
        artifacts_dir = skill_dir / "validation_artifacts"
        artifacts_dir.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (artifacts_dir / f"weekly_false_positive_report_{stamp}.json").write_text(
            json.dumps(artifact, indent=2), encoding="utf-8"
        )
        (artifacts_dir / "latest_weekly_false_positive_report.json").write_text(
            json.dumps(artifact, indent=2), encoding="utf-8"
        )
    except Exception as e:
        log.warning("FP report artifact write failed: %s", e)

    embed: dict = {
        "title": "Weekly False-Positive Report",
        "color": 0xE67E22,
        "fields": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Decision-packet learning loop - proposals are advisory only"},
    }
    embed["fields"].append({
        "name": "Coverage",
        "value": (
            f"**{report.get('resolved_packets', 0)}**/{report.get('total_packets', 0)} packets resolved "
            f"({report.get('coverage_pct', 0)}%)"
        ),
        "inline": False,
    })

    fp_lines = [
        f"{regime}: {stats.get('fp_rate')} ({stats.get('losses')}/{stats.get('resolved')})"
        for regime, stats in (report.get("false_positives_by_regime") or {}).items()
    ]
    if fp_lines:
        embed["fields"].append({
            "name": "False positives by regime",
            "value": "\n".join(fp_lines[:6]),
            "inline": False,
        })

    decay_lines = [
        f"{setup}: decay={stats.get('edge_decay')} (n={stats.get('resolved')})"
        for setup, stats in (report.get("edge_decay_by_setup") or {}).items()
        if stats.get("edge_decay") is not None
    ]
    if decay_lines:
        embed["fields"].append({
            "name": "Edge decay by setup",
            "value": "\n".join(decay_lines[:6]),
            "inline": False,
        })

    drag_lines = [
        f"{cond}: {stats.get('avg_slippage_bps')} bps (n={stats.get('samples')})"
        for cond, stats in (report.get("execution_drag_by_condition") or {}).items()
        if stats.get("avg_slippage_bps") is not None
    ]
    if drag_lines:
        embed["fields"].append({
            "name": "Execution drag by condition",
            "value": "\n".join(drag_lines[:6]),
            "inline": False,
        })

    top_proposals = (proposals.get("proposals") or [])[:3]
    if top_proposals:
        embed["fields"].append({
            "name": f"Tuning proposals ({proposals.get('count', 0)})",
            "value": "\n".join(
                f"{p.get('target')} -> {p.get('direction')} [{p.get('scope')}]" for p in top_proposals
            ),
            "inline": False,
        })
    else:
        embed["fields"].append({
            "name": "Tuning proposals",
            "value": "None (insufficient resolved samples or all metrics within bounds)",
            "inline": False,
        })

    send_embed_alert(embed, env_path=env_path)
    log.info("Weekly false-positive report sent (proposals=%s)", proposals.get("count", 0))
    return artifact


def run_scheduler() -> None:
    """Run main loop with Morning Brief (9:25 AM ET), scan (9:30), hold reminders, self-study, and weekly digest."""
    setup_logging()
    log = get_logger(__name__)
    log.info("Trading bot starting. Morning Brief at 9:25 AM ET, Weekly Digest Sun 6 PM ET.")

    try:
        from discord_confirm import start_confirm_bot
        start_confirm_bot(SKILL_DIR / ".env")
    except Exception as e:
        log.warning("Discord confirm bot failed to start: %s (signals will use webhook)", e)

    _last_brief_minute: int | None = None

    def _run_morning_brief_if_scheduled() -> None:
        nonlocal _last_brief_minute
        now = datetime.now(TZ_NY)
        key = now.hour * 60 + now.minute
        if now.hour == 9 and now.minute == 25 and key != _last_brief_minute:
            _last_brief_minute = key
            try:
                from core.operator_notify_state import send_morning_brief_if_due

                outcome = send_morning_brief_if_due(SKILL_DIR, now, build_morning_brief)
                if outcome != "sent":
                    log.info("Morning brief skipped: %s", outcome)
            except Exception as e:
                log.warning("Morning brief failed: %s", e)
                daily_heartbeat()

    _last_pead_warm_minute: int | None = None

    def _run_pead_warm_if_scheduled() -> None:
        nonlocal _last_pead_warm_minute
        now = datetime.now(TZ_NY)
        key = now.hour * 60 + now.minute
        if now.hour == 9 and now.minute == 18 and key != _last_pead_warm_minute:
            _last_pead_warm_minute = key
            try:
                from earnings_signal import maybe_warm_earnings_for_scan
                from signal_scanner import _load_watchlist

                watchlist = _load_watchlist(SKILL_DIR)
                summary = maybe_warm_earnings_for_scan(watchlist, SKILL_DIR)
                log.info("PEAD earnings warm: %s", summary)
            except Exception as e:
                log.warning("PEAD earnings warm failed: %s", e)

    _last_signal_minute: int | None = None

    def _run_signal_scan_if_scheduled() -> None:
        nonlocal _last_signal_minute
        now = datetime.now(TZ_NY)
        key = now.hour * 60 + now.minute
        if now.hour == 9 and now.minute == 30 and key != _last_signal_minute:
            _last_signal_minute = key
            try:
                from signal_scanner import run_scan_and_notify
                n = run_scan_and_notify(skill_dir=SKILL_DIR)
                log.info("Signal scan: %d signals found, Discord notifications sent.", n)
            except Exception as e:
                log.warning("Signal scan failed: %s", e)

    _last_hold_reminder_minute: int | None = None

    def _run_hold_reminder_if_scheduled() -> None:
        nonlocal _last_hold_reminder_minute
        now = datetime.now(TZ_NY)
        key = now.hour * 60 + now.minute
        if now.hour == 15 and now.minute == 30 and key != _last_hold_reminder_minute:
            _last_hold_reminder_minute = key
            try:
                from hold_reminder import check_hold_period_and_alert
                n = check_hold_period_and_alert(skill_dir=SKILL_DIR)
                if n > 0:
                    log.info("Hold reminder: %d alerts sent.", n)
            except Exception as e:
                log.warning("Hold reminder failed: %s", e)

    _last_self_study_minute: int | None = None

    def _run_self_study_if_scheduled() -> None:
        nonlocal _last_self_study_minute
        now = datetime.now(TZ_NY)
        key = now.hour * 60 + now.minute
        if now.hour == 16 and now.minute == 0 and key != _last_self_study_minute:
            _last_self_study_minute = key
            try:
                from core.outcome_backfill import run_local_backfill

                bf = run_local_backfill(SKILL_DIR)
                log.info(
                    "Decision-packet backfill: resolved=%s/%s skipped=%s",
                    bf.get("resolved"),
                    bf.get("total"),
                    bf.get("skipped"),
                )
            except Exception as e:
                log.warning("Decision-packet backfill failed: %s", e)
            try:
                from config import get_tab_agent_today_enabled
                from core.today_tab_agent import TodayTabAgent

                if get_tab_agent_today_enabled(SKILL_DIR):
                    governed = TodayTabAgent().govern(SKILL_DIR)
                    log.info("Today tab agent govern: actions=%s", len(governed.get("actions") or []))
                from config import get_tab_agent_debate_enabled
                from core.debate_tab_agent import DebateTabAgent

                if get_tab_agent_debate_enabled(SKILL_DIR):
                    debate = DebateTabAgent().govern(SKILL_DIR)
                    log.info("Debate tab agent govern: actions=%s", len(debate.get("actions") or []))
                from config import get_tab_agent_research_enabled
                from core.research_tab_agent import ResearchTabAgent

                if get_tab_agent_research_enabled(SKILL_DIR):
                    research = ResearchTabAgent().govern(SKILL_DIR)
                    log.info("Research tab agent govern: actions=%s", len(research.get("actions") or []))
                from config import get_tab_agent_backtest_enabled
                from core.backtest_tab_agent import BacktestTabAgent

                if get_tab_agent_backtest_enabled(SKILL_DIR):
                    backtest = BacktestTabAgent().govern(SKILL_DIR)
                    log.info("Backtest tab agent govern: actions=%s", len(backtest.get("actions") or []))
                from config import get_tab_agent_system_enabled
                from core.system_tab_agent import SystemTabAgent

                if get_tab_agent_system_enabled(SKILL_DIR):
                    system = SystemTabAgent().govern(SKILL_DIR)
                    log.info("System tab agent govern: actions=%s", len(system.get("actions") or []))
            except Exception as e:
                log.warning("Today tab agent govern failed: %s", e)
            if now.weekday() < 5:
                try:
                    from core.tab_agent_digest import write_daily_digest

                    digest = write_daily_digest(SKILL_DIR, now.date())
                    log.info("Tab agent digest: %s errors=%s", digest.get("day"), len(digest.get("errors") or []))
                except Exception as e:
                    log.warning("Tab agent digest failed: %s", e)
            try:
                from core.research_close import run_research_close

                result = run_research_close(SKILL_DIR)
                cards = result.get("hypothesis_cards") if isinstance(result.get("hypothesis_cards"), dict) else {}
                network = cards.get("network_status") if isinstance(cards.get("network_status"), dict) else {}
                log.info(
                    "Research close: score_ok=%s round_trips=%s score_error=%s study_error=%s "
                    "cards_scored=%s cards_missing=%s promote_not_false=%s cards_error=%s",
                    result.get("score_ok"),
                    result.get("round_trips_count"),
                    result.get("score_error"),
                    result.get("study_error"),
                    cards.get("scored"),
                    cards.get("artifact_missing"),
                    network.get("promote_not_false"),
                    result.get("hypothesis_cards_error"),
                )
            except Exception as e:
                log.warning("Research close failed: %s", e)

    _last_experiment_brief_minute: int | None = None

    def _run_experiment_brief_if_scheduled() -> None:
        nonlocal _last_experiment_brief_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if now.weekday() >= 5:
            return
        if not (now.hour == 16 and now.minute == 15 and key != _last_experiment_brief_minute):
            return
        _last_experiment_brief_minute = key
        try:
            from core.experiment_collector import deliver_experiment_briefs

            result = deliver_experiment_briefs(SKILL_DIR, notify=True, now=now)
            log.info(
                "Experiment brief: skipped=%s posted=%s",
                result.get("skipped"),
                result.get("posted"),
            )
        except Exception as e:
            log.warning("Experiment brief failed: %s", e)

    _last_weekly_minute: int | None = None

    def _run_weekly_digest_if_scheduled() -> None:
        nonlocal _last_weekly_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if now.weekday() == 6 and now.hour == 18 and now.minute == 0 and key != _last_weekly_minute:
            _last_weekly_minute = key

            def _digest_after_backfill() -> None:
                # Resolve matured decision packets before the digest so the
                # Sunday note includes newly closed outcomes.
                try:
                    from core.outcome_backfill import run_local_backfill

                    bf = run_local_backfill(SKILL_DIR)
                    log.info(
                        "Decision-packet backfill: resolved=%s/%s skipped=%s",
                        bf.get("resolved"),
                        bf.get("total"),
                        bf.get("skipped"),
                    )
                except Exception as e:
                    log.warning("Decision-packet backfill failed: %s", e)
                build_weekly_digest()

            try:
                from core.operator_notify_state import send_weekly_digest_if_due

                outcome = send_weekly_digest_if_due(SKILL_DIR, now, _digest_after_backfill)
                if outcome != "sent":
                    log.info("Weekly digest skipped: %s", outcome)
            except Exception as e:
                log.warning("Weekly digest failed: %s", e)

    _last_fp_report_minute: int | None = None

    def _run_false_positive_report_if_scheduled() -> None:
        nonlocal _last_fp_report_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if now.weekday() == 6 and now.hour == 18 and now.minute == 15 and key != _last_fp_report_minute:
            _last_fp_report_minute = key
            try:
                build_false_positive_report()
            except Exception as e:
                log.warning("Weekly false-positive report failed: %s", e)

    _last_evolve_minute: int | None = None

    def _run_evolve_if_scheduled() -> None:
        nonlocal _last_evolve_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if now.weekday() == 4 and now.hour == 17 and now.minute == 0 and key != _last_evolve_minute:
            _last_evolve_minute = key
            try:
                from evolve_logic import LearningEngine
                engine = LearningEngine(skill_dir=SKILL_DIR)
                result = engine.run(apply=False)
                log.info("Evolve logic: status=%s, updates=%d",
                         result.get("status"), result.get("updates_count", 0))
                if result.get("status") == "ok" and result.get("updates_count", 0) > 0:
                    send_alert(
                        f"Learning Engine found {result['updates_count']} threshold adjustment(s). "
                        "Review strategy_update.json and run challenger scan to validate.",
                        kind="self_study",
                        env_path=SKILL_DIR / ".env",
                    )
            except Exception as e:
                log.warning("Evolve logic failed: %s", e)

    _last_challenger_minute: int | None = None

    def _run_challenger_if_scheduled() -> None:
        nonlocal _last_challenger_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if now.weekday() == 5 and now.hour == 10 and now.minute == 0 and key != _last_challenger_minute:
            _last_challenger_minute = key
            try:
                from challenger_mode import ChallengerRunner
                runner = ChallengerRunner(skill_dir=SKILL_DIR)
                result = runner.run()
                log.info("Challenger scan: status=%s", result.get("status"))
                if result.get("status") == "ok":
                    comp = result.get("comparison", {})
                    send_alert(
                        f"Challenger vs Champion: **{comp.get('verdict', '?')}** "
                        f"(score delta: {comp.get('score_delta', 0):+.1f})\n"
                        f"Champion: {comp.get('champion', {}).get('count', 0)} signals, "
                        f"avg score {comp.get('champion', {}).get('avg_score', 0)}\n"
                        f"Challenger: {comp.get('challenger', {}).get('count', 0)} signals, "
                        f"avg score {comp.get('challenger', {}).get('avg_score', 0)}",
                        kind="self_study",
                        env_path=SKILL_DIR / ".env",
                    )
            except Exception as e:
                log.warning("Challenger scan failed: %s", e)

    _last_book_snapshot_minute: int | None = None
    _last_book_ytd_export_minute: int | None = None

    def _run_book_eod_snapshot_if_scheduled() -> None:
        """Post-close Book MTM snapshot (weekdays ~16:15 ET)."""
        nonlocal _last_book_snapshot_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if now.weekday() >= 5:
            return
        if not (now.hour == 16 and now.minute == 15 and key != _last_book_snapshot_minute):
            return
        _last_book_snapshot_minute = key
        try:
            from core.book_service import capture_book_snapshot
            from webapp.db import SessionLocal

            db = SessionLocal()
            try:
                result = capture_book_snapshot(db, skill_dir=SKILL_DIR, user_id="local")
                log.info(
                    "Book EOD snapshot: ok=%s date=%s",
                    result.get("ok"),
                    result.get("snapshot_date"),
                )
            finally:
                db.close()
        except Exception as e:
            log.warning("Book EOD snapshot failed: %s", e)

    def _run_book_ytd_export_if_scheduled() -> None:
        """Post-close Book YTD Excel refresh (weekdays, default 16:30 ET)."""
        nonlocal _last_book_ytd_export_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if now.weekday() >= 5:
            return
        try:
            from config import get_book_ytd_export_enabled, get_book_ytd_export_hhmm

            if not get_book_ytd_export_enabled(SKILL_DIR):
                return
            hour, minute = get_book_ytd_export_hhmm(SKILL_DIR)
        except Exception as e:
            log.warning("Book YTD export schedule config failed: %s", e)
            return
        if not (now.hour == hour and now.minute == minute and key != _last_book_ytd_export_minute):
            return
        _last_book_ytd_export_minute = key
        try:
            from core.book_export import export_ytd_workbook

            result = export_ytd_workbook(
                skill_dir=SKILL_DIR,
                tax_year=now.year,
                source="eod",
            )
            ok = bool(result.get("ok"))
            log.info(
                "Book YTD export: ok=%s path=%s error=%s",
                ok,
                result.get("path"),
                result.get("error"),
            )
            if ok:
                send_alert(
                    f"Book YTD Excel export OK ({result.get('tax_year')}): "
                    f"{result.get('closed_count', 0)} closed · "
                    f"{result.get('path')}",
                    kind="self_study",
                    env_path=SKILL_DIR / ".env",
                )
            else:
                send_alert(
                    f"Book YTD Excel export FAILED: {result.get('error') or 'unknown'} "
                    f"(path={result.get('path')})",
                    kind="self_study",
                    env_path=SKILL_DIR / ".env",
                )
        except Exception as e:
            log.warning("Book YTD export failed: %s", e)
            send_alert(
                f"Book YTD Excel export FAILED: {e}",
                kind="self_study",
                env_path=SKILL_DIR / ".env",
            )

    def _run_operator_desk_if_scheduled() -> None:
        try:
            from operator_desk import tick_operator_desk
        except Exception as exc:
            log.warning("Operator desk config failed: %s", exc)
            return
        tick_operator_desk(SKILL_DIR, datetime.now(TZ_NY))

    def _run_tradeable_brief_if_scheduled() -> None:
        try:
            from core.tradeable_brief import tick_tradeable_brief

            tick_tradeable_brief(SKILL_DIR)
        except Exception as exc:
            log.warning("Tradeable brief failed: %s", exc)

    _last_liquid_collect_key: int | None = None

    def _run_liquid_collect_if_scheduled() -> None:
        nonlocal _last_liquid_collect_key
        try:
            from config import get_liquid_collect_enabled, get_liquid_overlay_mode

            if get_liquid_overlay_mode(SKILL_DIR) == "off" or not get_liquid_collect_enabled(SKILL_DIR):
                return
        except Exception as e:
            log.warning("Liquid collect config failed: %s", e)
            return
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if now.minute != 5 or key == _last_liquid_collect_key:
            return
        _last_liquid_collect_key = key
        try:
            from core.liquid_positioning.provider import HyperliquidXyzProvider
            from core.liquid_positioning.settings import build_liquid_config
            from core.liquid_positioning.store import ensure_table, store_snapshots

            ensure_table(SKILL_DIR)
            snapshots, diag = HyperliquidXyzProvider(build_liquid_config(SKILL_DIR)).get_snapshots()
            diag.stored = store_snapshots(snapshots, SKILL_DIR)
            log.info("Liquid collect: %s", diag.to_dict())
        except Exception as e:
            log.warning("Liquid collect failed: %s", e)

    schedule.every().minute.do(_run_liquid_collect_if_scheduled)
    schedule.every().minute.do(_run_morning_brief_if_scheduled)
    schedule.every().minute.do(_run_operator_desk_if_scheduled)
    schedule.every().minute.do(_run_tradeable_brief_if_scheduled)
    schedule.every().minute.do(_run_pead_warm_if_scheduled)
    schedule.every().minute.do(_run_signal_scan_if_scheduled)
    schedule.every().minute.do(_run_hold_reminder_if_scheduled)
    schedule.every().minute.do(_run_self_study_if_scheduled)
    schedule.every().minute.do(_run_experiment_brief_if_scheduled)
    schedule.every().minute.do(_run_weekly_digest_if_scheduled)
    schedule.every().minute.do(_run_false_positive_report_if_scheduled)
    schedule.every().minute.do(_run_evolve_if_scheduled)
    schedule.every().minute.do(_run_challenger_if_scheduled)
    schedule.every().minute.do(_run_book_eod_snapshot_if_scheduled)
    _last_tab_agent_minute: int | None = None

    def _run_tab_agent_propose_if_scheduled() -> None:
        """Sunday 18:20 ET: propose Today prompt revisions. Never approves them."""
        nonlocal _last_tab_agent_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if not (now.weekday() == 6 and now.hour == 18 and now.minute == 20 and key != _last_tab_agent_minute):
            return
        _last_tab_agent_minute = key
        try:
            from config import get_tab_agent_today_enabled

            if not get_tab_agent_today_enabled(SKILL_DIR):
                log.info("Today tab agent proposal skipped: TAB_AGENT_TODAY_ENABLED is off")
                return
            from core.today_tab_agent import TodayTabAgent

            result = TodayTabAgent().propose(SKILL_DIR)
            log.info("Today tab agent proposals: created=%s", len(result.get("created") or []))
        except Exception as e:
            log.warning("Today tab agent proposal failed: %s", e)

    _last_debate_agent_minute: int | None = None

    def _run_debate_agent_propose_if_scheduled() -> None:
        """Sunday 18:25 ET: propose a shadow debate prompt. Promotion is the 16:00 govern pass."""
        nonlocal _last_debate_agent_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if not (now.weekday() == 6 and now.hour == 18 and now.minute == 25 and key != _last_debate_agent_minute):
            return
        _last_debate_agent_minute = key
        try:
            from config import get_tab_agent_debate_enabled
            from core.debate_tab_agent import DebateTabAgent

            if not get_tab_agent_debate_enabled(SKILL_DIR):
                log.info("Debate tab agent proposal skipped: TAB_AGENT_DEBATE_ENABLED is off")
                return
            result = DebateTabAgent().propose(SKILL_DIR)
            log.info("Debate tab agent proposals: created=%s", len(result.get("created") or []))
        except Exception as e:
            log.warning("Debate tab agent proposal failed: %s", e)

    _last_research_agent_minute: int | None = None

    def _run_research_agent_propose_if_scheduled() -> None:
        """Sunday 18:30 ET: propose a shadow quick-check prompt. Off unless the flag is set."""
        nonlocal _last_research_agent_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if not (now.weekday() == 6 and now.hour == 18 and now.minute == 30 and key != _last_research_agent_minute):
            return
        _last_research_agent_minute = key
        try:
            from config import get_tab_agent_research_enabled
            from core.research_tab_agent import ResearchTabAgent

            if not get_tab_agent_research_enabled(SKILL_DIR):
                log.info("Research tab agent proposal skipped: TAB_AGENT_RESEARCH_ENABLED is off")
                return
            result = ResearchTabAgent().propose(SKILL_DIR)
            log.info("Research tab agent proposals: created=%s", len(result.get("created") or []))
        except Exception as e:
            log.warning("Research tab agent proposal failed: %s", e)

    schedule.every().minute.do(_run_book_ytd_export_if_scheduled)
    schedule.every().minute.do(_run_tab_agent_propose_if_scheduled)
    schedule.every().minute.do(_run_debate_agent_propose_if_scheduled)
    _last_backtest_agent_minute: int | None = None

    def _run_backtest_agent_propose_if_scheduled() -> None:
        """Sunday 18:35 ET: propose a shadow strategy-chat prompt. Off unless the flag is set."""
        nonlocal _last_backtest_agent_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if not (now.weekday() == 6 and now.hour == 18 and now.minute == 35 and key != _last_backtest_agent_minute):
            return
        _last_backtest_agent_minute = key
        try:
            from config import get_tab_agent_backtest_enabled
            from core.backtest_tab_agent import BacktestTabAgent

            if not get_tab_agent_backtest_enabled(SKILL_DIR):
                log.info("Backtest tab agent proposal skipped: TAB_AGENT_BACKTEST_ENABLED is off")
                return
            result = BacktestTabAgent().propose(SKILL_DIR)
            log.info("Backtest tab agent proposals: created=%s", len(result.get("created") or []))
        except Exception as e:
            log.warning("Backtest tab agent proposal failed: %s", e)

    schedule.every().minute.do(_run_research_agent_propose_if_scheduled)
    _last_system_agent_minute: int | None = None

    def _run_system_agent_propose_if_scheduled() -> None:
        """Sunday 18:40 ET: propose shadow confidence wording. Off unless the flag is set."""
        nonlocal _last_system_agent_minute
        now = datetime.now(TZ_NY)
        key = now.day * 10000 + now.hour * 60 + now.minute
        if not (now.weekday() == 6 and now.hour == 18 and now.minute == 40 and key != _last_system_agent_minute):
            return
        _last_system_agent_minute = key
        try:
            from config import get_tab_agent_system_enabled
            from core.system_tab_agent import SystemTabAgent

            if not get_tab_agent_system_enabled(SKILL_DIR):
                log.info("System tab agent proposal skipped: TAB_AGENT_SYSTEM_ENABLED is off")
                return
            result = SystemTabAgent().propose(SKILL_DIR)
            log.info("System tab agent proposals: created=%s", len(result.get("created") or []))
        except Exception as e:
            log.warning("System tab agent proposal failed: %s", e)

    schedule.every().minute.do(_run_backtest_agent_propose_if_scheduled)
    schedule.every().minute.do(_run_system_agent_propose_if_scheduled)
    try:
        from core.operator_notify_state import send_morning_brief_if_due

        outcome = send_morning_brief_if_due(SKILL_DIR, datetime.now(TZ_NY), build_morning_brief)
        if outcome != "sent":
            log.info("Morning brief catch-up skipped: %s", outcome)
    except Exception as e:
        log.warning("Morning brief catch-up failed: %s", e)

    try:
        while True:
            schedule.run_pending()
            import time
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Shutdown requested")
    except Exception as e:
        log.exception("Critical error")
        send_alert(
            f"Trading bot CRASH: {e}. Check logs immediately.",
            kind="crash",
            env_path=SKILL_DIR / ".env",
        )
        raise


if __name__ == "__main__":
    run_scheduler()
