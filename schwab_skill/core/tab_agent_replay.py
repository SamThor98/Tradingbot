"""Historical replay samples for the Today, Debate, and Research tab agents.

The prompt sees only fields stored on the scan date. Bars after the 20-day
scale-out horizon are not used to label the trade. The model may still know
later events; replay samples are weighted below live samples, and a promotion
still needs the live-sample floor.

Analysis only. This module does not place or size orders.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger(__name__)

REPLAY_FILE = ".tab_agent_replay.jsonl"
TABS = ("today", "debate", "research")
CALLS_PER_CASE = {"today": 1, "debate": 2, "research": 1}
CAVEAT = (
    "Replay sends only point-in-time fields, but the model may already know later prices, "
    "news, and filings. Replay samples are weighted below live samples and cannot by themselves "
    "authorize a promotion."
)

_PIT_FIELDS = (
    "ticker",
    "symbol",
    "price",
    "entry_price",
    "close",
    "stop_pct",
    "stop_price",
    "stage_2",
    "vcp",
    "signal_score",
    "rank_score",
    "score",
    "composite_score",
    "setup_type",
    "sma_50",
    "sma_200",
    "avg_dollar_volume",
    "dollar_volume",
    "spread_pct",
    "relative_volume",
    "rs_rating",
    "sector",
    "industry",
)
_LEAK_FIELDS = {
    "outcome",
    "realized_return_pct",
    "by_horizon",
    "future_return",
    "future_close",
    "label",
    "resolved_at",
}


@dataclass
class ReplayBook:
    returns: list[float]
    dates: list[str]
    weights: list[float] | None
    sources: list[str] | None


def replay_path(skill_dir: Path | None) -> Path:
    root = Path(skill_dir) if skill_dir is not None else Path(".")
    return root / REPLAY_FILE


def load_samples(skill_dir: Path | None) -> list[dict[str, Any]]:
    path = replay_path(skill_dir)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        LOG.warning("Replay sample read failed: %s", exc)
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("source") == "replay":
            rows.append(row)
    return rows


def append_sample(skill_dir: Path, sample: dict[str, Any]) -> None:
    path = replay_path(skill_dir)
    line = json.dumps(sample, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def attach_replay(
    returns: list[float],
    dates: list[str],
    *,
    skill_dir: Path | None,
    tab_id: str,
    version_id: str,
    weight: float,
    since_ts: str | None = None,
) -> ReplayBook:
    """Append stored replay rows for one prompt version. Live rows stay at weight 1."""
    live_returns = list(returns)
    live_dates = list(dates)
    if skill_dir is None or not version_id:
        return ReplayBook(live_returns, live_dates, None, None)
    cutoff = str(since_ts or "")[:10]
    clipped = max(0.0, min(1.0, float(weight)))
    extra_returns: list[float] = []
    extra_dates: list[str] = []
    for sample in load_samples(skill_dir):
        if str(sample.get("tab_id") or "") != str(tab_id):
            continue
        if str(sample.get("version_id") or "") != str(version_id):
            continue
        as_of = str(sample.get("as_of") or "")[:10]
        if cutoff and as_of < cutoff:
            continue
        try:
            extra_returns.append(float(sample["realized_return_pct"]))
        except (KeyError, TypeError, ValueError):
            continue
        extra_dates.append(as_of)
    if not extra_returns:
        return ReplayBook(live_returns, live_dates, None, None)
    return ReplayBook(
        live_returns + extra_returns,
        live_dates + extra_dates,
        [1.0] * len(live_returns) + [clipped] * len(extra_returns),
        ["live"] * len(live_returns) + ["replay"] * len(extra_returns),
    )


def pit_payload(signal: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Fields the live prompt is allowed to see. Outcomes and future keys stay out."""
    refs = signal.get("refs") if isinstance(signal.get("refs"), dict) else {}
    payload: dict[str, Any] = {
        "as_of": as_of,
        "instruction": "Decide from these fields only. Long or cash. Never a short or an option.",
    }
    for key in _PIT_FIELDS:
        if key in _LEAK_FIELDS:
            continue
        if key in signal and key not in _LEAK_FIELDS:
            payload[key] = signal[key]
        elif key in refs:
            payload[key] = refs[key]
    return payload


def parse_decision(text: str, *, tab: str, quality_threshold: float) -> str | None:
    """Map one model reply to long or cash. A short suggestion is rejected."""
    if _suggests_short(text):
        return None
    if tab == "debate":
        match = re.search(r"(?:debate[_\s-]*score|score)\s*[:=]?\s*(\d{1,3})", text, re.IGNORECASE)
        if match:
            score = int(match.group(1))
            if score <= 100:
                return "long" if score >= float(quality_threshold) else "cash"
    upper = text.upper()
    if "HOLD OFF" in upper or "NO TRADE" in upper or "STAY IN CASH" in upper:
        return "cash"
    if "STRONG SETUP" in upper or re.search(r"\bWATCH\b", upper) or re.search(r"\bENDORSE\b", upper):
        return "long"
    if re.search(r"\bCASH\b", upper):
        return "cash"
    return None


def _suggests_short(text: str) -> bool:
    upper = text.upper()
    if "SHORT" not in upper and "INVERSE" not in upper and "PUT" not in upper:
        return False
    cleaned = (
        upper.replace("NO SHORT", "")
        .replace("NEVER SHORT", "")
        .replace("DON'T SHORT", "")
        .replace("DO NOT SHORT", "")
        .replace("NOT A SHORT", "")
    )
    return "SHORT" in cleaned or "INVERSE" in cleaned or re.search(r"\bPUT\b", cleaned) is not None


def maturity_calendar_days(horizon_days: int) -> int:
    """Calendar days that cover ``horizon_days`` trading sessions."""
    return int(math.ceil(int(horizon_days) * 7 / 5))


def truncate_history(frame: Any, created: datetime, horizon_days: int) -> Any | None:
    """Keep the entry bar and the next ``horizon_days`` bars. Later bars stay out."""
    from core.outcome_backfill import _locate_entry

    if frame is None or getattr(frame, "empty", True):
        return None
    try:
        entry_pos = _locate_entry(frame, created)
    except Exception as exc:
        LOG.debug("Replay entry locate failed: %s", exc)
        return None
    if entry_pos is None:
        return None
    end = int(entry_pos) + int(horizon_days) + 1
    if end > len(frame):
        return None
    return frame.iloc[:end].copy()


def scale_out_return(
    case: dict[str, Any],
    frame: Any,
    skill_dir: Path | None,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """20-day scale-out on history that ends at the horizon.

    A missing stop is left missing. The shared labeler then uses a fixed-horizon
    return instead of inventing an R multiple.
    """
    from core.outcome_backfill import _load_exit_params, label_packet_horizons

    created = case.get("created_at")
    if not isinstance(created, datetime):
        return None
    params = _load_exit_params(skill_dir)
    horizon = int(params["primary"])
    window = truncate_history(frame, created, horizon)
    if window is None:
        return None
    packet: dict[str, Any] = {
        "ticker": case.get("ticker"),
        "created_at": created.isoformat(),
        "entry_price": case.get("entry_price"),
        "side": "long",
        "outcome": {"label": "pending"},
    }
    if case.get("stop_pct") not in (None, ""):
        packet["stop_pct"] = case.get("stop_pct")
    if case.get("stop_price") not in (None, ""):
        packet["stop_price"] = case.get("stop_price")
    label_packet_horizons(packet, history_provider=lambda _ticker: window, params=params, now=now)
    outcome = packet.get("outcome") if isinstance(packet.get("outcome"), dict) else {}
    if str(outcome.get("label") or "pending") in {"pending", "unknown", ""}:
        return None
    try:
        if int(outcome.get("horizon_days")) != horizon:
            return None
        return {
            "realized_return_pct": float(outcome["realized_return_pct"]),
            "exit_model": str(outcome.get("exit_model") or "scale_out"),
        }
    except (TypeError, ValueError, KeyError):
        return None


def collect_cases(
    skill_dir: Path,
    *,
    since: datetime,
    now: datetime,
    horizon_days: int,
    db_path: Path | None = None,
    user_id: str = "local",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Matured staged packets and scan_results rows. Recent scans stay out."""
    from core.decision_packet import load_local_scan_signals, load_packets

    gap = maturity_calendar_days(horizon_days)
    cases: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(ticker: str, created: datetime, signal: dict[str, Any]) -> None:
        if created < since:
            return
        if (now.date() - created.date()).days < gap:
            return
        symbol = str(ticker or signal.get("ticker") or signal.get("symbol") or "").upper()
        if not symbol:
            return
        day = created.date().isoformat()
        if (symbol, day) in seen:
            return
        seen.add((symbol, day))
        refs = signal.get("refs") if isinstance(signal.get("refs"), dict) else {}
        entry = signal.get("entry_price")
        if entry is None:
            entry = signal.get("price")
        if entry is None:
            entry = refs.get("price")
        stop = signal.get("stop_pct")
        if stop is None:
            stop = refs.get("stop_pct")
        stop_price = signal.get("stop_price")
        if stop_price is None:
            stop_price = refs.get("stop_price")
        cases.append(
            {
                "ticker": symbol,
                "as_of": day,
                "created_at": created,
                "entry_price": entry,
                "stop_pct": stop,
                "stop_price": stop_price,
                "signal": signal,
            }
        )

    for packet in load_packets(skill_dir):
        if not isinstance(packet, dict):
            continue
        created = _parse_dt(packet.get("created_at"))
        if created is None:
            continue
        _add(str(packet.get("ticker") or ""), created, packet)
    database = db_path if db_path is not None else Path(skill_dir) / "webapp" / "webapp.db"
    if database.exists():
        for row in load_local_scan_signals(database, since=since, user_id=user_id):
            signal = row.get("signal") if isinstance(row.get("signal"), dict) else {}
            created = row.get("created_at")
            if not isinstance(created, datetime):
                continue
            _add(str(signal.get("ticker") or ""), created, signal)
    cases.sort(key=lambda item: item["created_at"])
    if limit is not None and limit >= 0:
        return cases[: int(limit)]
    return cases


def run_replay(
    skill_dir: Path,
    *,
    tab: str,
    since: str,
    limit: int | None = None,
    dry_run: bool = False,
    max_llm_calls: int = 25,
    now: datetime | None = None,
    db_path: Path | None = None,
    user_id: str = "local",
    complete: Callable[[str, str], str] | None = None,
    history_provider: Callable[[str], Any] | None = None,
    versions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score matured scans with the live prompt and any shadow. Dry-run only estimates."""
    from config import get_tab_agent_replay_usd_per_call
    from core.tab_agent_objective import load_objective

    root = Path(skill_dir)
    tab_id = str(tab or "").strip().lower()
    if tab_id not in TABS:
        raise ValueError(f"Replay tab must be one of {', '.join(TABS)}.")
    since_dt = _parse_since(since)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    config = load_objective(root)
    chosen = list(versions) if versions is not None else _live_and_shadow(root, tab_id)
    cases = collect_cases(
        root,
        since=since_dt,
        now=clock,
        horizon_days=config.horizon_days,
        db_path=db_path,
        user_id=user_id,
        limit=limit,
    )
    calls_each = CALLS_PER_CASE[tab_id]
    estimated = len(cases) * len(chosen) * calls_each
    price = get_tab_agent_replay_usd_per_call(root)
    cap = max(0, int(max_llm_calls))
    report: dict[str, Any] = {
        "ok": True,
        "dry_run": bool(dry_run),
        "tab": tab_id,
        "since": since_dt.date().isoformat(),
        "cases": len(cases),
        "versions": len(chosen),
        "calls_per_case": calls_each,
        "estimated_llm_calls": estimated,
        "estimated_cost_usd": round(estimated * price, 4),
        "usd_per_call": price,
        "max_llm_calls": cap,
        "calls_under_cap": min(estimated, cap),
        "llm_calls": 0,
        "stored": 0,
        "skipped_unlabeled": 0,
        "skipped_duplicate": 0,
        "skipped_unparsed": 0,
        "skipped_short": 0,
        "truncated": estimated > cap,
        "source": "replay",
        "replay_weight": config.replay_weight,
        "min_live_samples": config.min_live_samples,
        "caveat": CAVEAT,
        "estimate_note": "Dry-run assumes every matured case can be labeled. A live run skips cases whose price window is short.",
    }
    if dry_run:
        return report
    if not chosen:
        report["ok"] = False
        report["error"] = "no_live_or_shadow_prompt"
        return report
    caller = complete or _default_complete
    provider = history_provider or _default_history(root)
    history_cache: dict[str, Any] = {}
    seen = {
        (str(row.get("version_id")), str(row.get("ticker")), str(row.get("as_of"))[:10]) for row in load_samples(root)
    }
    used = 0
    ordered = sorted(
        chosen, key=lambda row: (0 if row.get("status") == "live" else 1, str(row.get("prompt_key") or ""))
    )
    for case in cases:
        labeled = _cached_label(case, provider, history_cache, root, clock)
        if labeled is None:
            report["skipped_unlabeled"] += 1
            continue
        for version in ordered:
            key = (str(version.get("id") or ""), str(case["ticker"]), str(case["as_of"]))
            if key in seen:
                report["skipped_duplicate"] += 1
                continue
            if used + calls_each > cap:
                report["truncated"] = True
                report["llm_calls"] = used
                return report
            payload = pit_payload(case["signal"] if isinstance(case.get("signal"), dict) else {}, case["as_of"])
            user = json.dumps(payload, default=str)
            texts: list[str] = []
            try:
                for step in range(calls_each):
                    prompt_user = user if step == 0 else user + "\n\nPrior round:\n" + texts[-1][:2000]
                    texts.append(str(caller(str(version.get("text") or ""), prompt_user) or ""))
                    used += 1
            except Exception as exc:
                LOG.warning("Replay LLM call failed: %s", exc)
                report["ok"] = False
                report["error"] = str(exc)
                report["llm_calls"] = used
                return report
            decision = parse_decision(
                texts[-1] if texts else "", tab=tab_id, quality_threshold=config.quality_threshold
            )
            report["llm_calls"] = used
            if decision is None:
                if texts and _suggests_short(texts[-1]):
                    report["skipped_short"] += 1
                else:
                    report["skipped_unparsed"] += 1
                continue
            realized = 0.0 if decision == "cash" else float(labeled["realized_return_pct"])
            sample = {
                "source": "replay",
                "tab_id": tab_id,
                "prompt_key": version.get("prompt_key"),
                "version_id": version.get("id"),
                "role": version.get("status"),
                "ticker": case["ticker"],
                "as_of": case["as_of"],
                "decision": decision,
                "realized_return_pct": realized,
                "horizon_days": config.horizon_days,
                "exit_model": labeled.get("exit_model") or "scale_out",
                "weight": config.replay_weight,
                "created_at": clock.isoformat(),
            }
            append_sample(root, sample)
            seen.add(key)
            report["stored"] += 1
    report["llm_calls"] = used
    report["truncated"] = False
    return report


def _cached_label(
    case: dict[str, Any],
    provider: Callable[[str], Any],
    cache: dict[str, Any],
    skill_dir: Path,
    now: datetime,
) -> float | None:
    ticker = str(case.get("ticker") or "")
    if ticker not in cache:
        try:
            cache[ticker] = provider(ticker)
        except Exception as exc:
            LOG.warning("Replay history failed for %s: %s", ticker, exc)
            cache[ticker] = None
    return scale_out_return(case, cache.get(ticker), skill_dir, now=now)


def _live_and_shadow(skill_dir: Path, tab_id: str) -> list[dict[str, Any]]:
    from core.tab_agent import get_tab_agent

    agent = get_tab_agent(tab_id)
    registry = agent.registry(skill_dir)
    return [row for row in registry.versions(tab_id) if row.get("status") in {"live", "shadow"}]


def _default_complete(system: str, user: str) -> str:
    from engine_analysis import _call_llm, _load_env

    return _call_llm(user, system, _load_env())


def _default_history(skill_dir: Path) -> Callable[[str], Any]:
    from core.outcome_backfill import _default_history_provider

    return _default_history_provider(skill_dir, days=800)


def _parse_since(raw: str) -> datetime:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("--since is required (YYYY-MM-DD).")
    if "T" in text or " " in text:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        parsed = datetime.fromisoformat(text + "T00:00:00")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_dt(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
