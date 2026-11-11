"""Role contracts for tab-agent prompt revisions.

A candidate may change wording. It may not drop the tool names, JSON schema,
brace placeholders, or threshold tokens the runner parses. The 2026-11-09
backtest review was a strategy-chat prompt rewritten as a stock-picking blurb:
that text cannot call queue_backtest, so the book stays empty. The same gate
covers Today, Debate, Research, and System. This module does not place orders.
"""

from __future__ import annotations

import logging

LOG = logging.getLogger(__name__)

KEEP_CONTRACT = (
    " Keep every tool name, JSON schema field, brace placeholder, and threshold token already in the prompt."
    " Do not replace the role with a stock-picking blurb."
)

# Substrings the built-in prompt already contains. A revision must keep each one.
# Values after an equals sign may change; the token name may not.
_ANCHORS: dict[tuple[str, str], tuple[str, ...]] = {
    ("backtest", "strategy_chat"): (
        "Scan studio",
        "validate_strategy_spec",
        "queue_backtest",
        "strategy_ids",
        "universe_preset",
        "task_id",
        "Never claim live trading",
    ),
    ("today", "institutional_trend"): (
        "Institutional Trend-Follower",
        "Return EXACTLY one strict JSON object",
        "conviction_score",
        "continuation_probability",
    ),
    ("today", "mean_reversion"): (
        "Mean-Reversion",
        "Return EXACTLY one strict JSON object",
        "fade_score",
    ),
    ("today", "retail_fomo"): (
        "Retail FOMO",
        "Return EXACTLY one strict JSON object",
        "conviction_score",
        "continuation_probability",
    ),
    ("today", "grok_research"): ("You are the research agent", "testable hypotheses"),
    ("today", "grok_reasoning"): ("You are the reasoning agent", "weak points"),
    ("today", "grok_code_review"): ("You are the code-review agent", "missing tests"),
    ("today", "grok_quant_analysis"): ("You are the quantitative analyst", "only numbers present"),
    ("today", "grok_adversarial_review"): ("You are the adversarial reviewer", "falsify"),
    ("debate", "system"): (
        "Reply with JSON only",
        '"seats"',
        "trend, setup, risk, skeptic, business",
        "qual_business",
        "never recommend a short",
    ),
    ("debate", "round1"): ("Score each seat", "checklist"),
    ("debate", "round2"): ("at most 15 points", "round1"),
    ("debate", "quality"): ("QUALITY_THRESHOLD=", "stays in cash", "Do not place orders"),
    ("research", "verdict_rules"): (
        "Never suggest a short",
        "STRONG_SETUP_MIN_SCORE=",
        "STRONG SETUP",
        "WATCH",
        "HOLD OFF",
        "NO DATA",
    ),
    ("research", "rationale"): (
        "{ticker}",
        "{verdict}",
        "{stage_2}",
        "{vcp}",
        "{score}",
        "{stop_pct}",
    ),
    ("system", "confidence_wording"): (
        "HIGH_CONFIDENCE_MIN=",
        "LOW_CONFIDENCE_MAX=",
        "not a short",
    ),
}


def missing_anchors(tab_id: str, prompt_key: str, text: str) -> list[str]:
    """Tokens the revision dropped. Empty when the role contract still holds."""
    required = _ANCHORS.get((str(tab_id or ""), str(prompt_key or "")))
    if not required:
        return []
    body = text or ""
    return [anchor for anchor in required if anchor not in body]


def keeps_contract(tab_id: str, prompt_key: str, text: str) -> bool:
    return not missing_anchors(tab_id, prompt_key, text)


def anchor_reminder(tab_id: str, prompt_key: str) -> str:
    """Tell the improver which tokens are load-bearing. Empty for an unknown key."""
    required = _ANCHORS.get((str(tab_id or ""), str(prompt_key or "")))
    if not required:
        return ""
    return "Keep these tokens verbatim or the revision is discarded: " + "; ".join(required)


def contract_skip(tab_id: str, prompt_key: str, text: str) -> dict[str, str] | None:
    """Skip payload when a proposal would replace the role. None when it is safe to store."""
    missing = missing_anchors(tab_id, prompt_key, text)
    if not missing:
        return None
    LOG.info(
        "Tab-agent proposal dropped contract tokens for %s/%s: %s",
        tab_id,
        prompt_key,
        "; ".join(missing),
    )
    return {
        "prompt_key": str(prompt_key),
        "reason": "contract_broken",
        "missing": "; ".join(missing),
    }
