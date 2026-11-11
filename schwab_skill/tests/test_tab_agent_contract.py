"""Prompt revisions keep the tool, schema, and role tokens the runner parses."""

from __future__ import annotations

import json

import pytest

from core.backtest_tab_agent import BacktestTabAgent, live_prompt_text
from core.debate_tab_agent import DebateTabAgent
from core.debate_tab_agent import live_prompt_text as debate_live_prompt
from core.research_quick_check import resolve_quick_check_decision
from core.tab_agent import TabAgentError, get_tab_agent
from core.tab_agent_contract import missing_anchors
from core.today_tab_agent import TodayTabAgent, live_grok_role_text, resolve_for_scan
from engine_analysis import AGENT_SYSTEMS
from webapp.strategy_chat import STRATEGY_CHAT_SYSTEM, strategy_chat_system

PICK = (
    "Pick the strongest Stage 2 equities with a tight stop and a clean chart. "
    "Prefer a smoother equity curve. Stay in cash when the tape is weak."
)


def test_builtin_prompts_keep_their_own_contracts() -> None:
    for tab in ("backtest", "today", "debate", "research", "system"):
        agent = get_tab_agent(tab)
        for key, text in agent.prompt_catalog().items():
            assert missing_anchors(tab, key, text) == [], f"{tab}/{key}"


@pytest.mark.parametrize("tab", ["backtest", "today", "debate", "research", "system"])
def test_a_pick_blurb_is_not_stored(tmp_path, tab: str) -> None:
    agent = get_tab_agent(tab)
    agent.registry(tmp_path)
    for key in agent.prompt_catalog():
        result = agent.propose(
            tmp_path,
            llm=lambda _system, _user: json.dumps({"text": PICK, "rationale": "Sample may be small."}),
            prompt_key=key,
        )
        assert result["created"] == []
        assert result["skipped"][0]["reason"] == "contract_broken"
        assert result["skipped"][0]["missing"]
    reviews = tmp_path / ".tab_agent_reviews"
    assert not reviews.exists() or not any(reviews.glob("*.json"))


@pytest.mark.parametrize("tab", ["backtest", "today", "debate", "research", "system"])
def test_appending_a_sentence_keeps_the_contract(tmp_path, tab: str) -> None:
    agent = get_tab_agent(tab)
    reg = agent.registry(tmp_path)
    for key in agent.prompt_catalog():
        live = reg.live_version(tab, key)
        assert live is not None
        revised = str(live["text"]) + " Prefer smoother out-of-sample equity."

        def _llm(_system: str, _user: str, body: str = revised) -> str:
            return json.dumps({"text": body, "rationale": "Sample may be small."})

        result = agent.propose(tmp_path, llm=_llm, prompt_key=key)
        assert result["created"], result["skipped"]
        assert result["created"][0]["text"].endswith("Prefer smoother out-of-sample equity.")
        assert missing_anchors(tab, key, result["created"][0]["text"]) == []


def test_backtest_blurb_names_the_tools_it_dropped_and_is_not_served(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TAB_AGENT_BACKTEST_ENABLED", "true")
    seen: dict[str, str] = {}

    def _llm(system: str, user: str) -> str:
        seen["system"] = system
        seen["user"] = user
        return json.dumps({"text": PICK, "rationale": "Sample is too small to prove an edge."})

    agent = BacktestTabAgent()
    result = agent.propose(tmp_path, llm=_llm, prompt_key="strategy_chat")
    assert result["created"] == []
    assert "validate_strategy_spec" in result["skipped"][0]["missing"]
    assert "queue_backtest" in result["skipped"][0]["missing"]
    assert "validate_strategy_spec" in seen["user"]
    assert "ticker-picking prompt" in seen["system"]
    served = live_prompt_text(tmp_path)
    assert served is not None and served.startswith("You help users design and run historical backtests")
    assert strategy_chat_system("Recent backtests:\nnone.").startswith(STRATEGY_CHAT_SYSTEM)


def test_corrupted_live_strategy_prompt_falls_back_to_the_builtin(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TAB_AGENT_BACKTEST_ENABLED", "true")
    agent = BacktestTabAgent()
    reg = agent.registry(tmp_path)
    live = reg.live_version("backtest", "strategy_chat")
    assert live is not None
    reg._mutate(
        "backtest",
        str(live["id"]),
        lambda row, _rows: row.__setitem__("text", PICK),
    )
    assert live_prompt_text(tmp_path) is None
    assert strategy_chat_system("Recent backtests:\nnone.").startswith(STRATEGY_CHAT_SYSTEM)


def test_approve_and_govern_refuse_a_strategy_chat_blurb(tmp_path) -> None:
    agent = BacktestTabAgent()
    reg = agent.registry(tmp_path)
    live = reg.live_version("backtest", "strategy_chat")
    assert live is not None
    row = reg.add_version(
        "backtest",
        prompt_key="strategy_chat",
        text=PICK,
        parent=str(live["id"]),
        rationale="Replaced the tool prompt with a pick.",
        status="shadow",
    )
    with pytest.raises(TabAgentError, match="validate_strategy_spec"):
        agent.approve(tmp_path, str(row["id"]))
    assert "queue_backtest" in str(reg.live_version("backtest", "strategy_chat")["text"])
    result = agent.govern(tmp_path)
    assert any(action.get("action") == "contract_reject" for action in result["actions"])
    assert reg.get("backtest", str(row["id"]))["status"] == "rejected"
    assert "queue_backtest" in str(reg.live_version("backtest", "strategy_chat")["text"])


def test_today_does_not_call_a_persona_or_grok_role_that_dropped_its_schema(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "true")
    monkeypatch.setenv("TAB_AGENT_SHADOW_ENABLED", "true")
    agent = TodayTabAgent()
    reg = agent.registry(tmp_path)
    persona = reg.live_version("today", "institutional_trend")
    role = reg.live_version("today", "grok_research")
    assert persona is not None and role is not None
    reg._mutate("today", str(persona["id"]), lambda row, _rows: row.__setitem__("text", PICK))
    reg._mutate("today", str(role["id"]), lambda row, _rows: row.__setitem__("text", PICK))
    reg.add_version(
        "today",
        prompt_key="mean_reversion",
        text=PICK,
        parent=str(reg.live_version("today", "mean_reversion")["id"]),
        rationale="picker",
        status="shadow",
    )
    resolved, shadow, bindings = resolve_for_scan(tmp_path)
    assert resolved["institutional_trend"] == AGENT_SYSTEMS["institutional_trend"]
    assert "mean_reversion" not in shadow
    assert all(row["prompt_key"] != "institutional_trend" for row in bindings)
    assert live_grok_role_text(tmp_path, "research") is None


def test_debate_and_research_fall_back_when_the_live_prompt_drops_its_tokens(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TAB_AGENT_DEBATE_ENABLED", "true")
    monkeypatch.setenv("TAB_AGENT_RESEARCH_ENABLED", "true")
    debate = DebateTabAgent()
    reg = debate.registry(tmp_path)
    system = reg.live_version("debate", "system")
    assert system is not None
    reg._mutate("debate", str(system["id"]), lambda row, _rows: row.__setitem__("text", PICK))
    assert debate_live_prompt(tmp_path, "system") is None

    research = get_tab_agent("research")
    research_reg = research.registry(tmp_path)
    rules = research_reg.live_version("research", "verdict_rules")
    assert rules is not None
    research_reg._mutate("research", str(rules["id"]), lambda row, _rows: row.__setitem__("text", PICK))
    decision = resolve_quick_check_decision(
        ticker="AAPL",
        has_price_data=True,
        stage_2=True,
        vcp=True,
        signal_score=70,
        skill_dir=tmp_path,
    )
    assert decision["verdict"] == "STRONG SETUP"
    assert decision["prompt_version"] == "builtin"
