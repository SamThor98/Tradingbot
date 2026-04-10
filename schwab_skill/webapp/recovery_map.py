"""Plain-language recovery hints for broker / data / order errors (shared by local + SaaS)."""

from __future__ import annotations

from typing import Any


def map_failure(message: str, source: str = "unknown") -> dict[str, Any]:
    raw = str(message or "").strip()
    msg = raw.lower()
    mapped: dict[str, Any] = {
        "source": source,
        "code": "unknown_error",
        "title": "Unexpected error",
        "summary": "The system hit an unknown error.",
        "fix_path": "Review logs, then retry the action.",
        "action": "retry",
    }
    if any(k in msg for k in ("not authenticated", "token", "oauth", "401", "unauthorized")):
        mapped.update(
            {
                "code": "auth_error",
                "title": "Authentication issue",
                "summary": "Broker authorization is missing or expired.",
                "fix_path": "Use Connect Schwab in the onboarding wizard, then retry.",
                "action": "reauth",
            }
        )
    elif any(
        k in msg
        for k in (
            "quote",
            "market data",
            "df_empty",
            "timeout",
            "connection",
            "circuit breaker",
            "connection unstable",
            "schwab connection unstable",
        )
    ):
        mapped.update(
            {
                "code": "data_error",
                "title": "Market data issue",
                "summary": "Market data could not be fetched reliably.",
                "fix_path": "Refresh market OAuth or wait if the circuit breaker tripped.",
                "action": "retry",
            }
        )
    elif any(k in msg for k in ("guardrail", "regime", "sector block", "event risk block")):
        mapped.update(
            {
                "code": "risk_block",
                "title": "Risk policy blocked the action",
                "summary": "The action was intentionally blocked by safety gates.",
                "fix_path": "Review pre-trade checklist and lower risk or wait for regime change.",
                "action": "review_checklist",
            }
        )
    elif any(k in msg for k in ("order", "api error", "status_code", "rejected")):
        mapped.update(
            {
                "code": "order_error",
                "title": "Order submission failed",
                "summary": "The broker rejected or failed to process the order.",
                "fix_path": "Retry once; if repeated, re-authenticate and confirm account permissions.",
                "action": "retry_or_reauth",
            }
        )
    mapped["raw_error"] = raw[:260]
    return mapped
