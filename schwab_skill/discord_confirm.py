"""
Discord bot for trade confirmation: sends Approve/Reject buttons before executing.
Requires DISCORD_BOT_TOKEN and DISCORD_CONFIRM_CHANNEL_ID in .env.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
import uuid
from pathlib import Path
from typing import Any

from discord_operator import (
    SCAN_ALREADY_RUNNING,
    SCAN_STARTED,
    OperatorContext,
    OperatorIntent,
    OperatorReply,
    begin_operator_scan,
    end_operator_scan,
    execute_operator_intent,
    mutation_denial,
    operator_denial,
    parse_operator_text,
)

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
_pending: dict[str, dict] = {}
_lock = threading.Lock()
_bot_start_time: float | None = None
_last_scan_info: dict[str, Any] = {}

EMBED_COLORS = {
    "green": 0x2ECC71,
    "yellow": 0xF39C12,
    "red": 0xE74C3C,
    "blue": 0x3498DB,
    "purple": 0x9B59B6,
    "teal": 0x1ABC9C,
    "orange": 0xE67E22,
}
MAX_PORTFOLIO_ROWS = 10
MAX_SECTOR_ROWS = 12


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _stamp_embed(embed_dict: dict, footer_text: str = "") -> dict:
    """Add a UTC timestamp and optional footer to an embed dict."""
    embed_dict["timestamp"] = _utc_now_iso()
    if footer_text:
        embed_dict["footer"] = {"text": footer_text}
    return embed_dict


def _friendly_error(prefix: str) -> str:
    return f"{prefix} Please try again in a moment."


# MiroFish agent display names
AGENT_DISPLAY_NAMES = {
    "institutional_trend": "Institutional Trend-Follower",
    "mean_reversion": "Mean-Reversion Bot",
    "retail_fomo": "Retail FOMO Trader",
}


def _build_conviction_meter(score: int, width: int = 21) -> str:
    """Build a visual bar from -100 to +100 with cursor. score in [-100, 100]."""
    score = max(-100, min(100, int(score)))
    # Map -100..100 to position 0..width
    pos = round((score + 100) / 200 * width) if width > 0 else 0
    pos = max(0, min(width, pos))
    bar = "░" * pos + "●" + "░" * (width - pos)
    return f"`{bar}`\nBearish `-100` ← **{score:+d}** → `+100` Bullish"


def _get_simulation_viewer_url() -> str:
    """Base URL for simulation viewer (configurable via SIMULATION_VIEWER_URL)."""
    env = _load_env(SKILL_DIR / ".env")
    return (env.get("SIMULATION_VIEWER_URL") or "http://127.0.0.1:3000").strip().rstrip("/")


def _build_mirofish_embed(mirofish_result: dict | None):
    """
    Build a Discord Embed from MiroFish result: Conviction Meter + influential agent summary.
    Includes a local link to open the interactive social graph when simulation_id is present.
    Returns None if no usable data.
    """
    import discord

    if not mirofish_result:
        return None
    conv = mirofish_result.get("conviction_score")
    summary = mirofish_result.get("summary") or "—"
    votes = mirofish_result.get("agent_votes") or []
    sim_id = mirofish_result.get("simulation_id")
    cont_prob = mirofish_result.get("continuation_probability")
    bull_prob = mirofish_result.get("bull_trap_probability")

    def _fmt_prob(p: object) -> str | None:
        try:
            f = float(p)  # type: ignore[arg-type]
            if f != f:
                return None
            f = max(0.0, min(1.0, f))
            return f"{f * 100.0:.0f}%"
        except Exception:
            return None

    embed = discord.Embed(
        title="MiroFish Market Sentiment",
        description=f"**{summary}**",
        color=0x9B59B6,
    )
    if conv is not None:
        embed.add_field(
            name="Conviction Meter",
            value=_build_conviction_meter(conv),
            inline=False,
        )

    # Optional: show scenario probabilities if produced by the simulation.
    cont_s = _fmt_prob(cont_prob)
    bull_s = _fmt_prob(bull_prob)
    if cont_s and bull_s:
        embed.add_field(
            name="Scenario Probabilities",
            value=f"Continuation next 1-2w: {cont_s}\nBull Trap next 1-2w: {bull_s}",
            inline=False,
        )
    # Sort by |score| descending to show most influential first
    sorted_votes = sorted(votes, key=lambda a: abs(a.get("score", 0)), reverse=True)
    if sorted_votes:
        lines = []
        for a in sorted_votes[:3]:
            name = AGENT_DISPLAY_NAMES.get(a.get("name", ""), a.get("name", "Agent"))
            s = a.get("score", 0)
            reason = (a.get("reason") or "").strip()[:120]
            # Optional: include per-agent scenario probabilities when available.
            cont_agent_s = _fmt_prob(a.get("continuation_probability"))
            bull_agent_s = _fmt_prob(a.get("bull_trap_probability"))
            probs_suffix = ""
            if cont_agent_s and bull_agent_s:
                probs_suffix = f" (A:{cont_agent_s} / B:{bull_agent_s})"
            if reason:
                lines.append(f"**{name}** ({s:+d})\n*{reason}{probs_suffix}*")
            else:
                lines.append(f"**{name}** ({s:+d}){probs_suffix}")
        embed.add_field(
            name="Agent Sentiment",
            value="\n\n".join(lines) if lines else "—",
            inline=False,
        )
    if sim_id:
        base = _get_simulation_viewer_url()
        link = f"{base}/simulation/{sim_id}"
        embed.add_field(
            name="Live View",
            value=f"[Open interactive social graph]({link})",
            inline=False,
        )
    return embed


def _load_env(env_path: Path | None = None) -> dict:
    path = env_path or SKILL_DIR / ".env"
    if not path.exists():
        return {}
    vals = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip('"\'')
    return vals


_confirmation_queue: queue.Queue = queue.Queue()
_bot_ready = threading.Event()
_bot_started = False


def request_trade_confirmation(signal: dict, skill_dir: Path, mock: bool = False) -> bool:
    """
    Queue a trade for Discord confirmation. Returns True if queued (bot running).
    mock=True: no real order on Approve, just "Mock executed" message.
    """
    if not _bot_ready.is_set():
        return False
    if mock:
        signal = dict(signal)
        signal["_mock"] = True
    _confirmation_queue.put((signal, skill_dir))
    return True


def request_mock_confirmation(
    ticker: str = "AAPL",
    price: float = 225.0,
    skill_dir: Path | None = None,
    sma_50: float | None = None,
    sma_200: float | None = None,
    signal_score: float | None = None,
    mirofish_summary: str | None = None,
    mirofish_conviction: int | float | None = None,
    mirofish_result: dict | None = None,
) -> bool:
    """Queue a mock trade for testing the confirmation flow."""
    skill_dir = skill_dir or SKILL_DIR
    signal = {"ticker": ticker, "price": price, "sector_etf": "XLK"}
    if sma_50 is not None:
        signal["sma_50"] = sma_50
    if sma_200 is not None:
        signal["sma_200"] = sma_200
    if signal_score is not None:
        signal["signal_score"] = signal_score
    if mirofish_summary is not None:
        signal["mirofish_summary"] = mirofish_summary
    if mirofish_conviction is not None:
        signal["mirofish_conviction"] = mirofish_conviction
    if mirofish_result is not None:
        signal["mirofish_result"] = mirofish_result
    elif mirofish_summary is not None or mirofish_conviction is not None:
        import uuid
        mock_sim_id = f"sim_{uuid.uuid4().hex[:12]}"
        mock_result = {
            "simulation_id": mock_sim_id,
            "ticker": ticker,
            "conviction_score": int(mirofish_conviction) if mirofish_conviction is not None else 0,
            "summary": mirofish_summary or "—",
            "agent_votes": [
                {"name": "institutional_trend", "score": 60, "reason": "Momentum favors continuation"},
                {"name": "mean_reversion", "score": 25, "reason": "Moderate extension, some caution"},
                {"name": "retail_fomo", "score": 50, "reason": "Positive news sentiment"},
            ],
            "seed_preview": "(mock trade)",
        }
        signal["mirofish_result"] = mock_result
        try:
            from engine_analysis import _persist_simulation
            _persist_simulation(mock_sim_id, mock_result, skill_dir)
        except Exception as e:
            LOG.debug("Could not persist mock simulation: %s", e)
    return request_trade_confirmation(signal, skill_dir, mock=True)


def _build_portfolio_embed(skill_dir: Path) -> dict:
    """Build a portfolio embed using embed fields (mobile-friendly, no code blocks)."""
    import json
    from datetime import date

    from execution import get_account_status
    from schwab_auth import DualSchwabAuth

    auth = DualSchwabAuth(skill_dir=skill_dir)
    result = get_account_status(auth=auth, skill_dir=skill_dir)

    if isinstance(result, str):
        return _stamp_embed(
            {"title": "Portfolio Snapshot", "description": result, "color": EMBED_COLORS["red"]},
            "Use /check TICKER for a quick verdict",
        )

    accounts = result.get("accounts", [])
    positions = []
    total_value = 0.0
    total_day_pl = 0.0

    for acc in accounts:
        sec = acc.get("securitiesAccount", acc)
        for pos in sec.get("positions", []):
            inst = pos.get("instrument", {})
            sym = inst.get("symbol", "?")
            qty = pos.get("longQuantity", 0) or pos.get("shortQuantity", 0)
            if qty == 0:
                continue
            avg_cost = pos.get("averagePrice", 0)
            mkt_val = pos.get("marketValue", 0)
            current = pos.get("currentDayProfitLoss", 0)
            current_price = (mkt_val / qty) if qty else 0
            pl_pct = ((current_price - avg_cost) / avg_cost * 100) if avg_cost else 0

            positions.append({
                "sym": sym, "qty": int(qty), "avg_cost": avg_cost,
                "current": current_price, "pl_pct": pl_pct,
                "day_pl": current, "mkt_val": mkt_val,
            })
            total_value += mkt_val
            total_day_pl += current

    tracker_path = skill_dir / ".positions_tracker.json"
    entry_dates: dict[str, str] = {}
    if tracker_path.exists():
        try:
            data = json.loads(tracker_path.read_text())
            if isinstance(data, dict):
                for sym, info in data.items():
                    if isinstance(info, dict) and info.get("entry_date"):
                        entry_dates[sym.upper()] = info["entry_date"]
        except Exception:
            pass

    if not positions:
        return _stamp_embed(
            {"title": "Portfolio Snapshot", "description": "No open positions.", "color": EMBED_COLORS["blue"]},
            "Use /check TICKER for a quick verdict",
        )

    today = date.today()
    positions.sort(key=lambda p: abs(float(p.get("mkt_val", 0) or 0)), reverse=True)
    color = EMBED_COLORS["green"] if total_day_pl >= 0 else EMBED_COLORS["red"]
    embed: dict[str, Any] = {
        "title": f"Portfolio Snapshot - {len(positions)} position(s)",
        "description": f"Total: **${total_value:,.0f}** | Day P/L: **${total_day_pl:+,.0f}**",
        "color": color,
        "fields": [],
    }

    for p in positions[:MAX_PORTFOLIO_ROWS]:
        ed = entry_dates.get(p["sym"].upper())
        days_held = None
        if ed:
            try:
                days_held = (today - date.fromisoformat(ed)).days
            except Exception:
                pass
        days_str = f"{days_held}d" if days_held is not None else "?"
        pl_sign = "+" if p["pl_pct"] >= 0 else ""
        embed["fields"].append({
            "name": f"{p['sym']}  {pl_sign}{p['pl_pct']:.1f}%",
            "value": (
                f"{p['qty']} shares @ ${p['avg_cost']:.2f}\n"
                f"Now ${p['current']:.2f} | Held {days_str}"
            ),
            "inline": True,
        })
    if len(positions) > MAX_PORTFOLIO_ROWS:
        embed["fields"].append(
            {
                "name": "Additional Positions",
                "value": f"+{len(positions) - MAX_PORTFOLIO_ROWS} more position(s) not shown.",
                "inline": False,
            }
        )

    return _stamp_embed(embed, "Use /check TICKER for a quick verdict")


def _build_sectors_embed(skill_dir: Path) -> dict:
    """Build a sector heatmap using inline embed fields (mobile-friendly)."""
    from schwab_auth import DualSchwabAuth
    from sector_strength import get_sector_heatmap

    auth = DualSchwabAuth(skill_dir=skill_dir)
    heatmap = get_sector_heatmap(auth, skill_dir)

    rows = heatmap.get("rows", [])
    spy_ret = heatmap.get("spy_return", 0)
    winning = heatmap.get("winning_count", 0)
    total = heatmap.get("total", 0)

    if not rows:
        return _stamp_embed(
            {"title": "Sector Heatmap", "description": "No sector data available.", "color": EMBED_COLORS["yellow"]},
            "Use /sectors to refresh",
        )

    embed: dict[str, Any] = {
        "title": "Sector Heatmap (21d vs SPY)",
        "description": f"**{winning}/{total}** sectors beating SPY ({spy_ret:+.1f}%)",
        "color": EMBED_COLORS["blue"],
        "fields": [],
    }

    rows = sorted(rows, key=lambda r: float(r.get("return_pct", 0) or 0), reverse=True)
    for r in rows[:MAX_SECTOR_ROWS]:
        pct = r["return_pct"]
        status = "WINNING" if r["winning"] else "lagging"
        bar_len = min(8, max(0, int(abs(pct) * 2)))
        bar = chr(0x2588) * bar_len + chr(0x2591) * (8 - bar_len)
        embed["fields"].append({
            "name": f"{r['etf']} {r['name']}",
            "value": f"`{bar}` {pct:+.1f}%  **{status}**",
            "inline": True,
        })
    if len(rows) > MAX_SECTOR_ROWS:
        embed["fields"].append(
            {
                "name": "Additional Sectors",
                "value": f"+{len(rows) - MAX_SECTOR_ROWS} more sector(s) not shown.",
                "inline": False,
            }
        )

    return _stamp_embed(embed, "Scan filters for winning sectors only")


def _build_status_embed(skill_dir: Path) -> dict:
    """Build a bot status embed showing uptime, API health, and last scan info."""
    import time as _time

    fields = []

    if _bot_start_time:
        uptime_s = _time.time() - _bot_start_time
        hours = int(uptime_s // 3600)
        mins = int((uptime_s % 3600) // 60)
        fields.append({"name": "Bot", "value": f"Online ({hours}h {mins}m)", "inline": True})
    else:
        fields.append({"name": "Bot", "value": "Starting...", "inline": True})

    market_ok = False
    account_ok = False
    try:
        from schwab_auth import DualSchwabAuth
        auth = DualSchwabAuth(skill_dir=skill_dir)
        try:
            auth.get_market_token()
            market_ok = True
        except Exception:
            pass
        try:
            auth.get_account_token()
            account_ok = True
        except Exception:
            pass
    except Exception:
        pass

    fields.append({"name": "Market API", "value": "**OK**" if market_ok else "**FAIL**", "inline": True})
    fields.append({"name": "Account API", "value": "**OK**" if account_ok else "**FAIL**", "inline": True})

    scan_info = _last_scan_info
    if scan_info.get("time"):
        fields.append({
            "name": "Last Scan",
            "value": f"{scan_info['time']} ({scan_info.get('signals', 0)} signals)",
            "inline": True,
        })
    else:
        fields.append({"name": "Last Scan", "value": "Not yet", "inline": True})

    fields.append({"name": "Next Scan", "value": "9:30 AM ET (daily)", "inline": True})

    color = EMBED_COLORS["green"] if (market_ok and account_ok) else EMBED_COLORS["yellow"]
    return _stamp_embed(
        {"title": "System Status", "color": color, "fields": fields},
        "All systems operational" if (market_ok and account_ok) else "Some APIs unavailable",
    )


def _run_bot_async(env: dict) -> None:
    import discord
    from discord import app_commands

    intents = discord.Intents.default()
    intents.message_content = True
    _FAST_INTENTS = frozenset({"ping", "help", "unknown", "pending", "halt", "resume"})

    def _operator_context() -> OperatorContext:
        with _lock:
            pending = {key: dict(value) for key, value in _pending.items()}
        return OperatorContext(
            skill_dir=SKILL_DIR,
            pending=pending,
            last_scan=_last_scan_info,
        )

    def _embed_batches(reply: OperatorReply) -> list[list[discord.Embed]]:
        embeds = [discord.Embed.from_dict(item) for item in reply.embeds if isinstance(item, dict)]
        if not embeds:
            return []
        return [embeds[index:index + 10] for index in range(0, len(embeds), 10)]

    async def _run_intent(intent: OperatorIntent, *, authorized: bool) -> OperatorReply:
        try:
            return await asyncio.to_thread(
                execute_operator_intent,
                intent,
                _operator_context(),
                authorized=authorized,
            )
        except Exception:
            LOG.exception("Operator intent failed: %s", intent.name)
            return OperatorReply(content=_friendly_error("That command failed."))

    _scan_tasks: set[asyncio.Task] = set()

    async def _confirm_channel(client: discord.Client):
        try:
            channel_id = int(env.get("DISCORD_CONFIRM_CHANNEL_ID", "0") or "0")
        except (TypeError, ValueError):
            return None
        if not channel_id:
            return None
        channel = client.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await client.fetch_channel(channel_id)
        except Exception:
            LOG.warning("Could not fetch confirm channel for scan result")
            return None

    async def _send_channel_reply(channel, reply: OperatorReply) -> None:
        if channel is None:
            LOG.warning("Scan finished with no Discord channel for the result")
            return
        batches = _embed_batches(reply)
        content = reply.content
        try:
            if not batches:
                await channel.send(content or "Done.")
                return
            await channel.send(content=content, embeds=batches[0])
            for batch in batches[1:]:
                await channel.send(embeds=batch)
        except Exception:
            LOG.exception("Operator channel send failed")

    async def _background_scan(client: discord.Client, channel, intent: OperatorIntent, authorized: bool) -> None:
        try:
            reply = await _run_intent(intent, authorized=authorized)
            target = channel if channel is not None else await _confirm_channel(client)
            await _send_channel_reply(target, reply)
            LOG.info("Discord scan finished")
        finally:
            end_operator_scan()

    async def _begin_discord_scan(
        client: discord.Client,
        channel,
        intent: OperatorIntent,
        authorized: bool,
        *,
        interaction: discord.Interaction | None = None,
        message: discord.Message | None = None,
    ) -> None:
        """Ack within Discord's interaction window, then scan off the webhook token.

        A full-universe scan runs longer than the 15-minute interaction token.
        The completion message is a normal channel send.
        """
        if not authorized:
            return

        async def _say(text: str, *, ephemeral: bool) -> None:
            if interaction is not None:
                await interaction.response.send_message(text, ephemeral=ephemeral)
                return
            if message is not None:
                await message.reply(text)

        if not begin_operator_scan():
            try:
                await _say(SCAN_ALREADY_RUNNING, ephemeral=True)
            except Exception:
                LOG.exception("Could not send scan-busy reply")
            return
        try:
            await _say(SCAN_STARTED, ephemeral=False)
        except Exception:
            end_operator_scan()
            LOG.exception("Could not acknowledge Discord scan")
            return
        LOG.info("Discord scan accepted")
        task = asyncio.create_task(_background_scan(client, channel, intent, authorized))
        _scan_tasks.add(task)
        task.add_done_callback(_scan_tasks.discard)

    async def _send_followup(
        interaction: discord.Interaction,
        reply: OperatorReply,
        *,
        ephemeral: bool = False,
    ) -> None:
        batches = _embed_batches(reply)
        content = reply.content
        try:
            if not batches:
                await interaction.followup.send(content or "Done.", ephemeral=ephemeral)
                return
            payload: dict[str, Any] = {"embeds": batches[0], "ephemeral": ephemeral}
            if content:
                payload["content"] = content
            await interaction.followup.send(**payload)
            for batch in batches[1:]:
                await interaction.followup.send(embeds=batch, ephemeral=ephemeral)
        except Exception:
            LOG.exception("Operator followup failed")
            channel = interaction.channel
            if channel is None:
                return
            try:
                if batches:
                    fallback: dict[str, Any] = {"embeds": batches[0]}
                    if content:
                        fallback["content"] = content
                    await channel.send(**fallback)
                    for batch in batches[1:]:
                        await channel.send(embeds=batch)
                elif content:
                    await channel.send(content)
            except Exception:
                LOG.warning("Operator channel send failed")

    async def _send_message_reply(
        message: discord.Message,
        reply: OperatorReply,
        *,
        edit_message: discord.Message | None = None,
    ) -> None:
        batches = _embed_batches(reply)
        content = reply.content
        try:
            if edit_message is not None:
                if batches:
                    await edit_message.edit(content=content, embeds=batches[0])
                else:
                    await edit_message.edit(content=content or "Done.")
                for batch in batches[1:]:
                    await message.channel.send(embeds=batch)
                return
            if not batches:
                await message.reply(content or "Done.")
                return
            await message.reply(content=content, embeds=batches[0])
            for batch in batches[1:]:
                await message.channel.send(embeds=batch)
        except Exception:
            LOG.exception("Operator message reply failed")

    async def _slash(interaction: discord.Interaction, intent: OperatorIntent) -> None:
        user_id = getattr(getattr(interaction, "user", None), "id", None)
        denial = mutation_denial(intent, user_id, env)
        if denial:
            LOG.info("Discord mutation denied")
            try:
                await interaction.response.send_message(denial, ephemeral=True)
            except Exception:
                LOG.exception("Could not send operator denial")
            return
        authorized = operator_denial(user_id, env) is None
        if intent.name == "scan":
            await _begin_discord_scan(
                interaction.client,
                interaction.channel,
                intent,
                authorized,
                interaction=interaction,
            )
            return
        ephemeral = intent.name == "ping"
        if intent.name in _FAST_INTENTS:
            reply = await _run_intent(intent, authorized=authorized)
            batches = _embed_batches(reply)
            try:
                if batches:
                    payload: dict[str, Any] = {"embeds": batches[0], "ephemeral": ephemeral}
                    if reply.content:
                        payload["content"] = reply.content
                    await interaction.response.send_message(**payload)
                else:
                    await interaction.response.send_message(reply.content or "Done.", ephemeral=ephemeral)
            except Exception:
                LOG.exception("Operator slash response failed: %s", intent.name)
            return
        try:
            await interaction.response.defer(ephemeral=False, thinking=True)
        except discord.InteractionResponded:
            return
        except Exception as exc:
            LOG.exception("Defer failed for %s: %s", intent.name, exc)
            return
        reply = await _run_intent(intent, authorized=authorized)
        await _send_followup(interaction, reply)

    class TradeConfirmView(discord.ui.View):
        def __init__(self, trade_id: str, ticker: str):
            # Keep buttons valid longer; 10 minutes was easy to miss in real usage.
            super().__init__(timeout=3600)
            self.trade_id = trade_id
            self.ticker = ticker

        async def _deny_unless_operator(self, interaction: discord.Interaction) -> bool:
            denial = operator_denial(getattr(interaction.user, "id", None), env)
            if not denial:
                return False
            LOG.info("Discord mutation denied")
            try:
                await interaction.response.send_message(denial, ephemeral=True)
            except Exception:
                LOG.exception("Could not send operator denial")
            return True

        async def _mark_message_done(self, interaction: discord.Interaction, status: str) -> None:
            """Disable buttons and annotate message so users see final state."""
            try:
                for item in self.children:
                    item.disabled = True
                if interaction.message and interaction.message.embeds:
                    embed = interaction.message.embeds[0].copy()
                    embed.set_footer(text=status)
                    await interaction.message.edit(embed=embed, view=self)
                elif interaction.message:
                    await interaction.message.edit(view=self)
            except Exception as e:
                LOG.debug("TradeConfirmView: failed to update message state: %s", e)

        async def _send_ephemeral_or_channel(
            self,
            interaction: discord.Interaction,
            message: str,
        ) -> None:
            """Prefer ephemeral followup; fallback to channel message when token is stale."""
            try:
                await interaction.followup.send(message, ephemeral=True)
                return
            except Exception:
                pass
            try:
                if interaction.channel:
                    await interaction.channel.send(message)
            except Exception:
                pass

        @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
        async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if await self._deny_unless_operator(interaction):
                return
            try:
                # CRITICAL: Defer FIRST - Discord requires response within 3 seconds
                await interaction.response.defer(ephemeral=True)
            except Exception as e:
                LOG.exception("Approve: failed to defer interaction: %s", e)
                return
            with _lock:
                trade = _pending.get(self.trade_id)
            if not trade:
                await self._send_ephemeral_or_channel(
                    interaction,
                    "Trade expired, already handled, or bot was restarted. Please run a fresh scan.",
                )
                await self._mark_message_done(interaction, "Trade expired or already handled")
                return
            if trade.get("_mock"):
                msg = f"**Mock executed:** Would have placed BUY {trade['qty']} {trade['ticker']} @ MARKET (~${trade['qty'] * trade.get('price', 0):,.2f})"
                await self._send_ephemeral_or_channel(interaction, msg)
                with _lock:
                    _pending.pop(self.trade_id, None)
                await self._mark_message_done(interaction, "Approved (mock)")
                self.stop()
                return
            try:
                from execution import place_order
                sig = trade.get("signal") or {}
                result = await asyncio.to_thread(
                    place_order,
                    trade["ticker"], trade["qty"], "BUY", "MARKET",
                    auth=None, skill_dir=trade["skill_dir"],
                    price_hint=trade.get("price"),
                    mirofish_conviction=sig.get("mirofish_conviction"),
                    sector_etf=sig.get("sector_etf"),
                )
                if isinstance(result, dict) and result.get("orderId"):
                    msg = f"Order placed: {trade['qty']} {trade['ticker']} @ MARKET. Order ID: {result.get('orderId', 'N/A')}"
                elif isinstance(result, str) and "Error" not in result:
                    msg = result
                else:
                    msg = f"Failed: {result}"
                await self._send_ephemeral_or_channel(interaction, f"**Executed:** {msg}")
                with _lock:
                    _pending.pop(self.trade_id, None)
                await self._mark_message_done(interaction, "Approved and processed")
                self.stop()
            except Exception as e:
                LOG.exception("Approve: error executing trade: %s", e)
                await self._send_ephemeral_or_channel(interaction, f"Error: {e}")

        @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
        async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            if await self._deny_unless_operator(interaction):
                return
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception as e:
                LOG.exception("Reject: failed to defer: %s", e)
                return
            with _lock:
                existed = _pending.pop(self.trade_id, None)
            self.stop()
            if existed:
                await self._send_ephemeral_or_channel(interaction, "Trade rejected.")
                await self._mark_message_done(interaction, "Rejected")
            else:
                await self._send_ephemeral_or_channel(
                    interaction,
                    "Trade was already handled or expired. Run a fresh scan if needed.",
                )
                await self._mark_message_done(interaction, "Trade expired or already handled")

        @discord.ui.button(label="Check", style=discord.ButtonStyle.secondary)
        async def check_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
            try:
                await interaction.response.defer(ephemeral=False, thinking=True)
            except Exception as e:
                LOG.exception("Check button: failed to defer: %s", e)
                return
            intent = OperatorIntent("check", False, ticker=self.ticker.upper().strip())
            try:
                reply = await asyncio.to_thread(
                    execute_operator_intent,
                    intent,
                    _operator_context(),
                    authorized=True,
                )
            except Exception as e:
                LOG.exception("Check button failed for %s: %s", self.ticker, e)
                reply = OperatorReply(content=_friendly_error(f"Check failed for **{self.ticker}**."))
            await _send_followup(interaction, reply)

        async def on_timeout(self):
            with _lock:
                _pending.pop(self.trade_id, None)
            for item in self.children:
                item.disabled = True
            try:
                if self.message and self.message.embeds:
                    embed = self.message.embeds[0].copy()
                    embed.set_footer(text="Trade confirmation expired - run a fresh scan")
                    await self.message.edit(embed=embed, view=self)
                elif self.message:
                    await self.message.edit(view=self)
            except Exception:
                pass

    class ConfirmBot(discord.Client):
        def __init__(self):
            super().__init__(intents=intents)
            self.tree = app_commands.CommandTree(self)

        async def setup_hook(self):
            @self.tree.command(name="ping", description="Check that the bot responds")
            async def ping(interaction: discord.Interaction):
                await _slash(interaction, OperatorIntent("ping", False))

            @self.tree.command(name="help", description="Show the operator desk commands")
            async def help_cmd(interaction: discord.Interaction):
                await _slash(interaction, OperatorIntent("help", False))

            @self.tree.command(name="today", description="Today card: regime, book, pending confirms, gate")
            async def today(interaction: discord.Interaction):
                await _slash(interaction, OperatorIntent("today", False))

            @self.tree.command(name="pending", description="Show open trade confirmations")
            async def pending(interaction: discord.Interaction):
                await _slash(interaction, OperatorIntent("pending", False))

            @self.tree.command(name="pause", description="Pause new buys")
            async def pause(interaction: discord.Interaction):
                await _slash(interaction, OperatorIntent("halt", True))

            @self.tree.command(name="resume", description="Resume new buys")
            async def resume(interaction: discord.Interaction):
                await _slash(interaction, OperatorIntent("resume", True))

            @self.tree.command(name="scan", description="Run a new signal scan (Stage 2 + VCP)")
            async def scan(interaction: discord.Interaction):
                await _slash(interaction, OperatorIntent("scan", True))

            @self.tree.command(name="check", description="Quick 3-line verdict for a ticker (fast)")
            @app_commands.describe(ticker="Stock ticker symbol (e.g. AAPL)")
            async def check(interaction: discord.Interaction, ticker: str):
                await _slash(interaction, OperatorIntent("check", False, ticker=ticker.upper().strip()))

            @self.tree.command(name="portfolio", description="Show current positions with P/L")
            async def portfolio(interaction: discord.Interaction):
                await _slash(interaction, OperatorIntent("portfolio", False))

            @self.tree.command(name="sectors", description="Show sector heatmap vs SPY")
            async def sectors(interaction: discord.Interaction):
                await _slash(interaction, OperatorIntent("sectors", False))

            @self.tree.command(name="status", description="Show bot health, API status, and last scan info")
            async def status(interaction: discord.Interaction):
                await _slash(interaction, OperatorIntent("status", False))

            @self.tree.command(name="report", description="Generate a full financial report for a ticker")
            @app_commands.describe(
                ticker="Stock ticker symbol (e.g. AAPL)",
                section="Optional: tech, dcf, comps, health, edgar, mirofish",
                skip_mirofish="Skip MiroFish LLM simulation (faster)",
                skip_edgar="Skip SEC EDGAR filing lookup",
            )
            async def report(
                interaction: discord.Interaction,
                ticker: str,
                section: str | None = None,
                skip_mirofish: bool = False,
                skip_edgar: bool = False,
            ):
                await _slash(
                    interaction,
                    OperatorIntent(
                        "report",
                        False,
                        ticker=ticker.upper().strip(),
                        section=section.lower().strip() if section else None,
                        skip_mirofish=skip_mirofish,
                        skip_edgar=skip_edgar,
                    ),
                )

        async def on_message(self, message):
            if message.author.bot:
                return
            try:
                channel_id = int(env.get("DISCORD_CONFIRM_CHANNEL_ID", "0") or "0")
            except (TypeError, ValueError):
                return
            if not channel_id or message.channel.id != channel_id:
                return
            intent = parse_operator_text(message.content or "")
            if intent is None:
                return
            user_id = getattr(message.author, "id", None)
            denial = mutation_denial(intent, user_id, env)
            if denial:
                LOG.info("Discord mutation denied")
                try:
                    await message.reply(denial)
                except Exception:
                    LOG.exception("Could not send operator denial")
                return
            authorized = operator_denial(user_id, env) is None
            if intent.name == "scan":
                await _begin_discord_scan(
                    self,
                    message.channel,
                    intent,
                    authorized,
                    message=message,
                )
                return
            working = None
            if intent.name not in _FAST_INTENTS:
                try:
                    working = await message.reply("Working on it...")
                except Exception:
                    LOG.warning("Operator ack failed for %s", intent.name)
            reply = await _run_intent(intent, authorized=authorized)
            await _send_message_reply(message, reply, edit_message=working)

        async def on_ready(self):
            global _bot_start_time
            import time as _time
            _bot_start_time = _time.time()
            channel_id = int(env.get("DISCORD_CONFIRM_CHANNEL_ID", "0"))
            if channel_id:
                LOG.info("Discord confirm bot ready: %s", self.user)
                _bot_ready.set()
            else:
                LOG.warning("DISCORD_CONFIRM_CHANNEL_ID not set, confirmations disabled")
            asyncio.create_task(self._process_queue())
            asyncio.create_task(self._sync_slash_commands())

        async def _sync_slash_commands(self) -> None:
            try:
                guild_id = env.get("DISCORD_GUILD_ID", "").strip()
                if guild_id:
                    guild = discord.Object(id=int(guild_id))
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    LOG.info("Slash commands synced to guild %s", guild_id)
                else:
                    await self.tree.sync()
                    LOG.info("Slash commands synced globally")
            except Exception as e:
                LOG.warning("Slash command sync failed: %s", e)

        async def _process_queue(self):
            channel_id = int(env.get("DISCORD_CONFIRM_CHANNEL_ID", "0"))
            if not channel_id:
                LOG.warning("DISCORD_CONFIRM_CHANNEL_ID not set, confirmations disabled")
                return
            channel = self.get_channel(channel_id)
            if not channel:
                channel = await self.fetch_channel(channel_id)

            def get_from_queue():
                return _confirmation_queue.get(timeout=2)

            while not self.is_closed():
                try:
                    signal, skill_dir = await asyncio.to_thread(get_from_queue)
                except queue.Empty:
                    await asyncio.sleep(0.5)
                    continue
                except RuntimeError as e:
                    # During shutdown, asyncio may refuse to schedule new work on the
                    # executor/event loop, causing: "cannot schedule new futures after shutdown".
                    # Treat this as a normal exit condition.
                    if "cannot schedule new futures after shutdown" in str(e):
                        LOG.info("Confirm queue worker exiting due to shutdown: %s", e)
                        return
                    raise
                except asyncio.CancelledError:
                    return
                t = signal["ticker"]
                p = signal["price"]
                sector = signal["sector_etf"]
                label = signal.get("_alert_label", "")
                # Never block the event loop: sizing can hit Schwab (seconds) and causes
                # "The application did not respond" on /scan and Approve/Reject.
                from execution import get_position_size_usd

                def _qty_for_signal() -> int:
                    pos_usd = get_position_size_usd(ticker=t, price=p, skill_dir=skill_dir)
                    return max(1, int(pos_usd / p)) if p > 0 else 10

                qty = await asyncio.to_thread(_qty_for_signal)
                trade_id = str(uuid.uuid4())[:8]
                with _lock:
                    _pending[trade_id] = {
                        "ticker": t, "qty": qty, "skill_dir": skill_dir,
                        "signal": signal, "price": p, "_mock": signal.get("_mock", False),
                    }
                title = "[Mock] Signal Confirmation" if signal.get("_mock") else "Signal Confirmation"
                embed = discord.Embed(
                    title=title,
                    description=f"**Buy {t}** {label}\nStage 2 + VCP breakout in a winning sector. Review details below.",
                    color=0x3498DB,
                )
                order_value = qty * p
                embed.add_field(
                    name="Order",
                    value=(
                        f"**BUY {qty}** @ **MARKET**\n"
                        f"Ref **${p:,.2f}** · est. **${order_value:,.2f}**"
                    ),
                    inline=True,
                )
                sma50 = signal.get("sma_50")
                sma200 = signal.get("sma_200")
                sma50_str = f"${sma50:,.2f}" if isinstance(sma50, (int, float)) else "—"
                sma200_str = f"${sma200:,.2f}" if isinstance(sma200, (int, float)) else "—"
                embed.add_field(name="Technical", value=(
                    f"SMA 50: {sma50_str}\n"
                    f"SMA 200: {sma200_str}\n"
                    f"Sector: **{sector}**"
                ), inline=True)
                setup_score = signal.get("signal_score")
                if setup_score is not None:
                    embed.add_field(name="Setup Score", value=f"**{setup_score:.0f}/100**", inline=True)
                embed.set_footer(text="Approve sends this market buy • Reject cancels • Check for a quick verdict")

                # MiroFish embed: Conviction Meter + influential agent sentiment
                mirofish_result = signal.get("mirofish_result")
                mirofish_embed = _build_mirofish_embed(mirofish_result)
                embeds_to_send = [embed]
                if mirofish_embed:
                    embeds_to_send.append(mirofish_embed)

                view = TradeConfirmView(trade_id, t)
                try:
                    sent = await channel.send(embeds=embeds_to_send, view=view)
                    view.message = sent
                    from alert_history import record_alert_sent
                    record_alert_sent(t, skill_dir)
                    LOG.info("Sent confirm prompt to channel %s for %s", channel_id, t)
                except Exception as e:
                    LOG.warning(
                        "Failed to send confirm message (channel %s, ticker %s). "
                        "Check: bot has Send Messages + Embed Links in that channel. Error: %s",
                        channel_id, t, e,
                    )
                    with _lock:
                        _pending.pop(trade_id, None)

    token = env.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        LOG.warning("DISCORD_BOT_TOKEN not set, trade confirmation bot disabled")
        return
    client = ConfirmBot()
    try:
        client.run(token)
    except Exception as e:
        LOG.warning("Discord confirm bot failed (invalid token?): %s", e)


def start_confirm_bot(env_path: Path | None = None) -> threading.Thread | None:
    """Start the Discord confirmation bot in a background thread. Idempotent - safe to call multiple times."""
    global _bot_started
    if _bot_started:
        return None
    env = _load_env(env_path or SKILL_DIR / ".env")
    if not env.get("DISCORD_BOT_TOKEN", "").strip() or not env.get("DISCORD_CONFIRM_CHANNEL_ID", "").strip():
        return None
    _bot_started = True
    for k, v in env.items():
        if k not in os.environ:
            os.environ[k] = v
    thread = threading.Thread(
        target=lambda: _run_bot_async(env),
        daemon=True,
        name="DiscordConfirmBot",
    )
    thread.start()
    return thread


def run_confirm_bot() -> None:
    """Run the confirm bot until its thread exits. Does not start the scheduler."""
    import time

    from logger_setup import setup_logging

    setup_logging()
    thread = start_confirm_bot(SKILL_DIR / ".env")
    if thread is None:
        LOG.warning("Discord confirm bot did not start (missing token or channel)")
        return
    if not _bot_ready.wait(timeout=30):
        LOG.warning("Discord confirm bot did not become ready within 30s")
    while thread.is_alive():
        time.sleep(5)
    LOG.warning("Discord confirm bot thread exited")


def ensure_bot_ready(timeout: float = 15.0) -> bool:
    """Ensure confirm bot is running. Starts it if needed. Returns True when ready."""
    env = _load_env(SKILL_DIR / ".env")
    if not env.get("DISCORD_BOT_TOKEN", "").strip():
        return False
    if _bot_ready.is_set():
        return True
    start_confirm_bot(SKILL_DIR / ".env")
    return _bot_ready.wait(timeout=timeout)


if __name__ == "__main__":
    run_confirm_bot()
