"""Overnight prompt lab for the five tab agents.

The scheduler starts this in its own process. From 6:00 PM to 7:00 AM
America/Chicago, including weekends, each tab runs generations of rival
prompts. A winner that beats the live prompt on the train window and on the
sealed holdout is installed as shadow through the governor. Live text is
never replaced here.

The loop is resumable: one state file per night is replaced atomically after
each generation. A crash retries the unfinished generation and does not
install a second shadow for a night that already finished. A transient model
failure (429, timeout, 5xx, or an open circuit) waits and retries. It does
not finish the night or fall through to an empty generation.

Lab model calls can target xAI through ``TAB_AGENT_LAB_MODEL``,
``TAB_AGENT_LAB_BASE_URL``, and ``TAB_AGENT_LAB_PROVIDER``. Those values are
applied to an in-memory env copy. ``LLM_MODEL_NAME`` and ``LLM_BASE_URL`` are
not written to ``.env``, so scan-time MiroFish keeps its own route when those
two are unset.

The live baseline is scored on ``TAB_AGENT_LAB_BASELINE_SAMPLE`` cases per
window (default 40), and candidates are scored on that same draw. Completed
baseline scores live in ``.tab_agent_lab/baseline_cache.json``, keyed by the
prompt, the sample, and the lab model. A partial pass checkpoints after each
decided case and resumes. The window end and the nightly token cap are checked
between calls, and spend is written to ``state.json`` as each call is charged.

Before the prompt generations, the night runs an offline setup-rule search on
the stored trade pool (``core.setup_rule_search``). That phase does not call
the model unless ``SETUP_RULE_SEARCH_LLM`` is on, does not spend the $50 cap
when it is off, and does not change orders or the live scan. Passing entry
filters are written into the morning report and, as recommendations only, into
``data/shadow_setup_filter/recommended_rules.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from core.tab_agent_lab_score import (
    DECISION_KEYS,
    LabCase,
    objective_for,
    synthetic_cases,
)
from core.tab_agent_lab_tournament import (
    offline_decide,
    offline_propose,
    run_generation,
    score_texts,
    select_winner,
)
from core.tab_agent_objective import ObjectiveConfig

LOG = logging.getLogger(__name__)

CHICAGO = ZoneInfo("America/Chicago")
STATE_FILE = ".tab_agent_lab/state.json"
REPORT_DIR = ".tab_agent_lab/reports"
PID_FILE = ".tab_agent_lab/lab.pid"
LOG_FILE = ".tab_agent_lab/lab.log"
BASELINE_CACHE_FILE = ".tab_agent_lab/baseline_cache.json"
DEFAULT_BASELINE_SAMPLE = 40
TABS = ("today", "debate", "research", "backtest", "system")


@dataclass
class LabConfig:
    enabled: bool = False
    concurrency: int = 6
    candidates: int = 8
    model: str = ""
    base_url: str = ""
    provider: str = ""
    token_cap: int = 0
    holdout_weeks: int = 6
    holdout_days: int = 0
    embargo_days: int = 20
    margin: float = 0.05
    min_holdout: int = 8
    min_participation: float = 0.60
    min_longs: int = 8
    max_cases: int = 180
    min_train_cases: int = 150
    min_train_dates: int = 15
    min_holdout_cases: int = 200
    min_holdout_dates: int = 10
    train_per_era: int = 300
    rebuild: bool = False
    reveal_live_ticker: bool = False
    drift_weight: float = 0.15
    max_generations: int = 0
    usd_per_1k: float = 0.01
    baseline_sample: int = DEFAULT_BASELINE_SAMPLE
    tabs: tuple[str, ...] = TABS

    @classmethod
    def load(cls, skill_dir: Path | None = None) -> LabConfig:
        from config import (
            get_tab_agent_lab_base_url,
            get_tab_agent_lab_baseline_sample,
            get_tab_agent_lab_candidates,
            get_tab_agent_lab_concurrency,
            get_tab_agent_lab_drift_weight,
            get_tab_agent_lab_embargo_days,
            get_tab_agent_lab_enabled,
            get_tab_agent_lab_holdout_days,
            get_tab_agent_lab_holdout_weeks,
            get_tab_agent_lab_margin,
            get_tab_agent_lab_max_cases,
            get_tab_agent_lab_max_generations,
            get_tab_agent_lab_min_holdout_cases,
            get_tab_agent_lab_min_holdout_dates,
            get_tab_agent_lab_min_holdout_samples,
            get_tab_agent_lab_min_longs,
            get_tab_agent_lab_min_participation,
            get_tab_agent_lab_min_train_cases,
            get_tab_agent_lab_min_train_dates,
            get_tab_agent_lab_model,
            get_tab_agent_lab_provider,
            get_tab_agent_lab_rebuild_enabled,
            get_tab_agent_lab_reveal_live_ticker,
            get_tab_agent_lab_tabs,
            get_tab_agent_lab_token_cap,
            get_tab_agent_lab_train_per_era,
            get_tab_agent_lab_usd_per_1k,
        )

        return cls(
            enabled=get_tab_agent_lab_enabled(skill_dir),
            concurrency=get_tab_agent_lab_concurrency(skill_dir),
            candidates=get_tab_agent_lab_candidates(skill_dir),
            model=get_tab_agent_lab_model(skill_dir),
            base_url=get_tab_agent_lab_base_url(skill_dir),
            provider=get_tab_agent_lab_provider(skill_dir),
            token_cap=get_tab_agent_lab_token_cap(skill_dir),
            holdout_weeks=get_tab_agent_lab_holdout_weeks(skill_dir),
            holdout_days=get_tab_agent_lab_holdout_days(skill_dir),
            embargo_days=get_tab_agent_lab_embargo_days(skill_dir),
            margin=get_tab_agent_lab_margin(skill_dir),
            min_holdout=get_tab_agent_lab_min_holdout_samples(skill_dir),
            min_participation=get_tab_agent_lab_min_participation(skill_dir),
            min_longs=get_tab_agent_lab_min_longs(skill_dir),
            max_cases=get_tab_agent_lab_max_cases(skill_dir),
            min_train_cases=get_tab_agent_lab_min_train_cases(skill_dir),
            min_train_dates=get_tab_agent_lab_min_train_dates(skill_dir),
            min_holdout_cases=get_tab_agent_lab_min_holdout_cases(skill_dir),
            min_holdout_dates=get_tab_agent_lab_min_holdout_dates(skill_dir),
            train_per_era=get_tab_agent_lab_train_per_era(skill_dir),
            rebuild=get_tab_agent_lab_rebuild_enabled(skill_dir),
            reveal_live_ticker=get_tab_agent_lab_reveal_live_ticker(skill_dir),
            drift_weight=get_tab_agent_lab_drift_weight(skill_dir),
            max_generations=get_tab_agent_lab_max_generations(skill_dir),
            usd_per_1k=get_tab_agent_lab_usd_per_1k(skill_dir),
            baseline_sample=get_tab_agent_lab_baseline_sample(skill_dir),
            tabs=get_tab_agent_lab_tabs(skill_dir),
        )


class Budget:
    """Approximate token counter. A cap of 0 does not stop the night."""

    def __init__(self, cap: int, usd_per_1k: float) -> None:
        self.cap = max(0, int(cap))
        self.usd_per_1k = float(usd_per_1k)
        self.tokens = 0
        self._lock = threading.Lock()
        self._on_charge: Callable[[int], None] | None = None

    def set_on_charge(self, fn: Callable[[int], None] | None) -> None:
        """Called after a successful charge with the running token total."""
        self._on_charge = fn

    def charge(self, *parts: str) -> bool:
        used = sum(len(part or "") for part in parts) // 4
        with self._lock:
            if used:
                self.tokens += used
            tokens = self.tokens
            allowed = self.cap <= 0 or self.tokens < self.cap
        listener = self._on_charge
        if used and listener is not None:
            listener(tokens)
        return allowed

    @property
    def exhausted(self) -> bool:
        return self.cap > 0 and self.tokens >= self.cap

    @property
    def spend_usd(self) -> float:
        return round(self.tokens / 1000.0 * self.usd_per_1k, 4)


class ModelFailureGuard:
    """Stop a night once one exception is a majority of the generation's model calls.

    The check runs as calls finish. Two identical hard failures with nothing
    succeeding yet are already a majority, so a broken loader does not keep
    going for the rest of the book.
    """

    _min_identical = 2

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.attempts = 0
        self.failures: dict[str, int] = {}
        self.aborted = False
        self.reason = ""

    def begin_generation(self) -> None:
        with self._lock:
            if self.aborted:
                return
            self.attempts = 0
            self.failures.clear()

    def note_success(self) -> None:
        with self._lock:
            self.attempts += 1

    def note_failure(self, exc: Exception) -> None:
        key = f"{type(exc).__name__}: {exc}"
        trip = False
        warn = False
        count = 0
        attempts = 0
        with self._lock:
            self.attempts += 1
            count = self.failures.get(key, 0) + 1
            self.failures[key] = count
            attempts = self.attempts
            if not self.aborted and count >= self._min_identical and count * 2 > attempts:
                self.aborted = True
                self.reason = key
                trip = True
            elif count == 1:
                warn = True
        if trip:
            LOG.error(
                "Lab stopping the night: %s of %s model calls in this generation failed with the same error: %s",
                count,
                attempts,
                key,
            )
        elif warn:
            LOG.warning("Lab model call failed: %s", exc)


def in_lab_window(now: datetime) -> bool:
    """True from 18:00 inclusive until 07:00 exclusive, every day."""
    local = _chicago(now)
    minutes = local.hour * 60 + local.minute
    return minutes >= 18 * 60 or minutes < 7 * 60


def night_id(now: datetime) -> str:
    """The evening's calendar date. A 2 AM run still belongs to the previous evening."""
    local = _chicago(now)
    if local.hour < 7:
        local = local - timedelta(days=1)
    return local.date().isoformat()


def _chicago(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=CHICAGO)
    return now.astimezone(CHICAGO)


def _retryable(exc: Exception) -> bool:
    text = f"{exc.__class__.__name__} {exc}".lower()
    markers = (
        "429",
        "rate limit",
        "ratelimit",
        "resource-exhausted",
        "resource exhausted",
        "at capacity",
        "timeout",
        "timed out",
        "500",
        "502",
        "503",
        "504",
        "overloaded",
        "circuit",
    )
    return any(marker in text for marker in markers)


def _retry_after_seconds(exc: Exception) -> float | None:
    """Numeric ``Retry-After`` seconds, or an exception's ``retry_after`` attribute."""
    raw = getattr(exc, "retry_after", None)
    if raw is None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) if response is not None else None
        getter = getattr(headers, "get", None) if headers is not None else None
        if callable(getter):
            raw = getter("Retry-After")
            if raw is None:
                raw = getter("retry-after")
    if raw is None or raw == "":
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def _backoff_seconds(delay: float, exc: Exception, rng: random.Random) -> float:
    """Exponential delay plus jitter. A Retry-After header is a floor, not a hint."""
    header = _retry_after_seconds(exc)
    if header is None:
        return delay + rng.uniform(0.0, delay * 0.25)
    base = max(delay, header)
    return base + rng.uniform(0.0, min(1.0, base * 0.1))


class NightBoundary(Exception):
    """The window ended or the nightly cap was hit between calls.

    ``night_boundary`` is how the tournament re-raises this without importing
    the lab module (that import would be circular).
    """

    night_boundary = True

    def __init__(self, reason: str) -> None:
        self.reason = reason if reason in {"window_end", "budget_cap"} else "window_end"
        super().__init__(self.reason)


class LLMUnavailable(Exception):
    """The lab model could not be called. The night waits instead of scoring blanks.

    ``lab_hold`` is how the tournament re-raises this without importing the lab
    module (that import would be circular).
    """

    lab_hold = True

    def __init__(self, retry_after: float = 30.0, reason: str = "llm_unavailable") -> None:
        self.retry_after = max(0.0, float(retry_after))
        self.reason = reason or "llm_unavailable"
        super().__init__(self.reason)


def _transient_llm_failure(exc: Exception) -> bool:
    if getattr(exc, "lab_hold", False):
        return True
    return _retryable(exc)


def call_with_backoff(
    fn: Callable[[], str],
    *,
    sleeper: Callable[[float], None] | None = None,
    attempts: int = 6,
    on_failure: Callable[[Exception], None] | None = None,
    rng: random.Random | None = None,
) -> str:
    """Call the model. Rate limits and transient API errors wait and retry.

    A hard failure returns an empty string so the night loop keeps going.
    ``on_failure`` owns that log line when the caller is tallying the generation.
    ``LLMUnavailable`` propagates so the night can wait instead of scoring a blank.
    """
    pause = sleeper or (lambda _seconds: None)
    draw = rng or random.Random()
    delay = 1.0
    for attempt in range(max(1, attempts)):
        try:
            return str(fn() or "")
        except Exception as exc:
            if getattr(exc, "lab_hold", False):
                raise
            if not _retryable(exc) or attempt >= attempts - 1:
                if on_failure is not None:
                    on_failure(exc)
                else:
                    LOG.warning("Lab model call failed: %s", exc)
                return ""
            sleep_for = _backoff_seconds(delay, exc, draw)
            LOG.info("Lab model call retrying after %.1f seconds: %s", sleep_for, exc)
            try:
                pause(sleep_for)
            except Exception as sleep_exc:
                LOG.warning("Lab backoff sleep failed: %s", sleep_exc)
            delay = min(delay * 2, 30.0)
    return ""


def _seconds_until_window_end(now: datetime) -> float:
    """Seconds until 07:00 America/Chicago. Zero once the window has closed."""
    local = _chicago(now)
    if not in_lab_window(local):
        return 0.0
    end = local.replace(hour=7, minute=0, second=0, microsecond=0)
    if local.hour >= 18:
        end = end + timedelta(days=1)
    return max(0.0, (end - local).total_seconds())


def _wait_out_circuit(sleeper: Callable[[float], None], now_fn: Callable[[], datetime]) -> None:
    """Sleep until the shared LLM circuit closes, or until the lab window ends.

    An open circuit used to return an empty string. The generator then stored
    a contract drop and, after three of those, finished the night.
    """
    from engine_analysis import llm_circuit_remaining

    remaining = llm_circuit_remaining()
    if remaining <= 0:
        return
    left = _seconds_until_window_end(now_fn())
    if left <= 0:
        raise LLMUnavailable(0.0, "llm_unavailable")
    pause_for = min(remaining, left)
    LOG.info("Lab waiting %.0fs for the LLM circuit to close", pause_for)
    try:
        sleeper(pause_for)
    except Exception as exc:
        LOG.warning("Lab circuit wait failed: %s", exc)
    if _seconds_until_window_end(now_fn()) <= 0:
        raise LLMUnavailable(0.0, "llm_unavailable")
    still = llm_circuit_remaining()
    if still > 0.25:
        raise LLMUnavailable(still, "circuit_open")


def _silent_llm_generation(use_llm: bool, produced: list[dict[str, Any]], tokens_before: int, budget: Budget) -> bool:
    """True when a generation dropped every candidate without receiving model text.

    A real reply that fails the contract still charges tokens, so the three-strike
    rule still applies to those. An empty reply does not.
    """
    if not use_llm or budget.exhausted or budget.tokens != tokens_before:
        return False
    return bool(produced) and all(bool(row.get("dropped")) for row in produced)


def _state_path(skill_dir: Path) -> Path:
    return skill_dir / STATE_FILE


def load_state(skill_dir: Path) -> dict[str, Any]:
    path = _state_path(skill_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("Lab state unreadable, starting this night clean: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_state(skill_dir: Path, state: dict[str, Any]) -> None:
    from _io_utils import atomic_write_json

    atomic_write_json(_state_path(skill_dir), state, indent=2)


def _blank_phase() -> dict[str, Any]:
    return {
        "seconds": 0.0,
        "tokens": 0,
        "calls": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_resumes": 0,
    }


def _blank_state(night: str, tabs: tuple[str, ...]) -> dict[str, Any]:
    return {
        "night_id": night,
        "finished": False,
        "orders_touched": False,
        "tokens": 0,
        "spend_usd": 0.0,
        "phases": {"baseline": _blank_phase(), "generations": _blank_phase()},
        "baseline_sample": DEFAULT_BASELINE_SAMPLE,
        "baseline_sample_ids": {},
        "tabs": {
            tab: {"next_generation": 0, "completed": [], "candidates": [], "installed": False, "errors": []}
            for tab in tabs
        },
        "decision_cache": {},
    }


def select_baseline_sample(cases: list[LabCase], size: int) -> list[LabCase]:
    """Return ``size`` cases spread across the window, in the original order.

    ``size`` <= 0 keeps every case. The draw depends only on case order,
    ``as_of``, ``case_id``, and ``size``, so the same book hashes to the same
    baseline cache key on a later night.
    """
    if size <= 0 or len(cases) <= size:
        return list(cases)
    order = sorted(range(len(cases)), key=lambda index: (cases[index].as_of, cases[index].case_id, index))
    last = len(order) - 1
    chosen: list[int] = []
    seen: set[int] = set()
    for step in range(size):
        slot = round(step * last / (size - 1)) if size > 1 else 0
        index = order[min(last, max(0, slot))]
        if index in seen:
            continue
        seen.add(index)
        chosen.append(index)
    if len(chosen) < size:
        for index in order:
            if index in seen:
                continue
            seen.add(index)
            chosen.append(index)
            if len(chosen) == size:
                break
    chosen.sort()
    return [cases[index] for index in chosen]


def _case_fingerprint(case: LabCase) -> list[Any]:
    return [
        case.case_id,
        case.as_of,
        case.ticker,
        case.long_return_pct,
        case.signal_score,
        case.stated_confidence,
        case.avg_dollar_volume,
        case.spread_pct,
        case.source,
        bool(case.pre_cutoff),
        case.era,
        case.cohort_return_pct,
    ]


def lab_model_id(config: LabConfig) -> str:
    """Lab model id for the baseline cache. Does not read or write ``LLM_MODEL_NAME``."""
    return (config.model or "").strip() or "default"


def baseline_cache_key(
    tab: str,
    texts: dict[str, str],
    train: list[LabCase],
    holdout: list[LabCase],
    model_id: str,
    *,
    provider: str = "",
    base_url: str = "",
) -> str:
    """Hash of the prompt, the eval sample, and the lab model identity."""
    payload = {
        "tab": tab,
        "texts": {key: str(texts.get(key) or "") for key in sorted(texts)},
        "model": model_id or "default",
        "provider": (provider or "").strip().lower(),
        "base_url": (base_url or "").strip(),
        "train": [_case_fingerprint(case) for case in train],
        "holdout": [_case_fingerprint(case) for case in holdout],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _baseline_cache_path(skill_dir: Path) -> Path:
    return skill_dir / BASELINE_CACHE_FILE


def load_baseline_cache(skill_dir: Path) -> dict[str, Any]:
    path = _baseline_cache_path(skill_dir)
    empty: dict[str, Any] = {"version": 1, "entries": {}}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("Lab baseline cache unreadable, scoring again: %s", exc)
        return empty
    if not isinstance(data, dict) or int(data.get("version") or 0) != 1:
        return empty
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return empty
    data["entries"] = entries
    return data


def save_baseline_cache(skill_dir: Path, cache: dict[str, Any]) -> None:
    from _io_utils import atomic_write_json

    payload = {"version": 1, "entries": cache.get("entries") if isinstance(cache.get("entries"), dict) else {}}
    atomic_write_json(_baseline_cache_path(skill_dir), payload, indent=2)


def _resolve_sample(cases: list[LabCase], stored_ids: Any, size: int) -> list[LabCase]:
    """Reuse a night's stored ids when every one is still in the book."""
    if isinstance(stored_ids, list) and stored_ids:
        by_id = {case.case_id: case for case in cases}
        picked = [by_id[case_id] for case_id in stored_ids if isinstance(case_id, str) and case_id in by_id]
        if len(picked) == len(stored_ids):
            return picked
    return select_baseline_sample(cases, size)


def _persistable_candidate(row: dict[str, Any]) -> dict[str, Any]:
    kept = dict(row)
    kept.pop("generator_user", None)
    return kept


def load_live_texts(skill_dir: Path, tab: str) -> dict[str, str]:
    from core.tab_agent import get_tab_agent

    agent = get_tab_agent(tab)
    reg = agent.registry(skill_dir)
    catalog = agent.prompt_catalog()
    texts: dict[str, str] = {}
    for key in DECISION_KEYS[tab]:
        live = reg.live_version(tab, key)
        text = str(live.get("text") or "").strip() if live else ""
        if not text:
            text = str(catalog.get(key) or "").strip()
        texts[key] = text
    return texts


def _caching_decide(
    inner: Callable[..., str | None],
    cache: dict[str, str],
    lock: threading.Lock,
) -> Callable[..., str | None]:
    def decide(*, tab: str, texts: dict[str, str], case: LabCase) -> str | None:
        raw = json.dumps({"tab": tab, "texts": texts, "case": case.case_id}, sort_keys=True)
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with lock:
            if key in cache:
                return cache[key] or None
        side = inner(tab=tab, texts=texts, case=case)
        with lock:
            cache[key] = side or ""
        return side

    return decide


def _score_live(
    tab: str,
    texts: dict[str, str],
    cases: list[LabCase],
    *,
    config: ObjectiveConfig,
    decide: Callable[..., str | None],
    drift_weight: float,
    skill_dir: Path,
    stop: Callable[[], None] | None = None,
    remembered: dict[str, str] | None = None,
    on_side: Callable[[LabCase, str | None], None] | None = None,
) -> dict[str, Any]:
    book = score_texts(
        tab,
        texts,
        cases,
        config=config,
        live_texts=texts,
        decide=decide,
        drift_weight=drift_weight,
        skill_dir=skill_dir,
        stop=stop,
        remembered=remembered,
        on_side=on_side,
    )
    from core.tab_agent_lab_tournament import _brief

    brief = _brief(book)
    if isinstance(book.get("ex_pre_cutoff"), dict):
        brief["ex_pre"] = _brief(book["ex_pre_cutoff"])
    if book.get("slices"):
        brief["slices"] = book.get("slices")
    brief["used_pre_cutoff"] = bool(book.get("used_pre_cutoff"))
    return brief


def _load_night_cases(
    root: Path,
    cfg: LabConfig,
    *,
    cases: list[LabCase] | None,
    horizon_days: int,
) -> tuple[list[LabCase], int, int, str]:
    if cases is not None:
        return list(cases), 0, 0, "injected"
    from core.tab_agent_lab_score import load_lab_book

    book = load_lab_book(root, horizon_days=horizon_days)
    loaded = list(book.cases)
    if cfg.rebuild:
        from core.tab_agent_rebuild import load_rebuild_cases

        loaded.extend(load_rebuild_cases(root))
    return loaded, book.fixtures_excluded, book.duplicates_removed, "stored"


def _night_plan(cases: list[LabCase], cfg: LabConfig, *, fixtures_excluded: int, duplicates_removed: int) -> Any:
    from core.tab_agent_lab_plan import plan_split

    return plan_split(
        cases,
        holdout_weeks=cfg.holdout_weeks,
        holdout_days=cfg.holdout_days or None,
        embargo_days=cfg.embargo_days,
        train_per_era=cfg.train_per_era,
        min_train_cases=cfg.min_train_cases,
        min_train_dates=cfg.min_train_dates,
        min_holdout_cases=cfg.min_holdout_cases,
        min_holdout_dates=cfg.min_holdout_dates,
        fixtures_excluded=fixtures_excluded,
        duplicates_removed=duplicates_removed,
    )


def _blocked_report(night: str, plan: Any, *, prior_tokens: int) -> dict[str, Any]:
    """A failed gate. ``prior_tokens`` is recorded and no new call is made."""
    payload = plan.as_dict()
    return {
        "night_id": night,
        "window": "18:00-07:00 America/Chicago, weekends included",
        "orders_touched": False,
        "tokens": 0,
        "prior_tokens": int(prior_tokens),
        "spend_usd": 0.0,
        "token_cap": 0,
        "candidates_tried": 0,
        "generations": {},
        "tabs": {},
        "cases": payload,
        "trust_gate": payload,
        "trust_reason": plan.reason,
        "stopped_early": True,
        "stop_reason": "trust_gate",
        "note": "Shadow only. The trust gate stopped the night before any model call.",
    }


def _ensure_setup_rules(skill_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Offline search. A finished payload is reused if the night resumes."""
    from config import get_setup_rule_search_enabled

    existing = state.get("setup_rules")
    if isinstance(existing, dict) and existing.get("complete") and not existing.get("pending_hypotheses"):
        return state
    if not get_setup_rule_search_enabled(skill_dir):
        state["setup_rules"] = {
            "complete": True,
            "skipped": True,
            "reason": "disabled",
            "cost_usd": 0.0,
            "orders_touched": False,
            "live_scan_changed": False,
            "rules": [],
            "passing": [],
        }
        save_state(skill_dir, state)
        return state
    try:
        from core.setup_rule_search import run_setup_rule_phase

        payload = run_setup_rule_phase(skill_dir)
    except Exception as exc:
        LOG.exception("Setup-rule phase failed")
        payload = {
            "complete": True,
            "ok": False,
            "reason": "search_failed",
            "error": str(exc),
            "cost_usd": 0.0,
            "orders_touched": False,
            "live_scan_changed": False,
            "stops_changed": False,
            "rules": [],
            "passing": [],
        }
    payload["complete"] = True
    payload.setdefault("orders_touched", False)
    payload.setdefault("live_scan_changed", False)
    payload.setdefault("cost_usd", 0.0)
    state["setup_rules"] = payload
    save_state(skill_dir, state)
    return state


def _maybe_llm_hypotheses(
    skill_dir: Path,
    state: dict[str, Any],
    *,
    config: LabConfig,
    budget: Budget,
    guard: ModelFailureGuard,
    clock: Callable[[], datetime],
    sleeper: Callable[[float], None],
    use_llm: bool,
) -> None:
    """Optional proposals. Default off. Evaluation stays the deterministic search."""
    from config import get_setup_rule_search_llm

    current = state.get("setup_rules") if isinstance(state.get("setup_rules"), dict) else {}
    if not use_llm or not get_setup_rule_search_llm(skill_dir) or current.get("llm_applied"):
        return
    if current.get("reason") in {"pool_missing", "pool_empty", "disabled"}:
        current["llm_applied"] = True
        state["setup_rules"] = current
        return
    from core.setup_rule_search import hypothesis_prompt, parse_hypotheses, run_setup_rule_phase

    features = current.get("features") if isinstance(current.get("features"), dict) else {}
    present = [str(name) for name in (features.get("features_present") or [])]
    grids_raw = current.get("feature_grids") if isinstance(current.get("feature_grids"), dict) else {}
    grids = {
        str(feature): tuple(float(value) for value in values)
        for feature, values in grids_raw.items()
        if isinstance(values, list)
    }
    system = "You propose entry-filter hypotheses. You do not place orders, change stops, or change the live scan."
    user = hypothesis_prompt(present, grids)
    try:
        text = _model_call(skill_dir, config, system, user, budget, sleeper, guard, clock)
    except NightBoundary:
        raise
    except Exception as exc:
        LOG.warning("Setup-rule hypothesis call failed: %s", exc)
        current["llm_applied"] = True
        current["llm_error"] = str(exc)
        state["setup_rules"] = current
        save_state(skill_dir, state)
        return
    extras = parse_hypotheses(text, allowed=set(present))
    if extras:
        try:
            payload = run_setup_rule_phase(skill_dir, extra_rules=extras)
        except Exception as exc:
            LOG.warning("Setup-rule hypothesis evaluation failed: %s", exc)
            payload = current
            payload["llm_error"] = str(exc)
        payload["complete"] = True
        payload["llm_applied"] = True
        payload["llm_hypotheses"] = [rule.rule_id for rule in extras]
        state["setup_rules"] = payload
    else:
        current["llm_applied"] = True
        current["llm_hypotheses"] = []
        state["setup_rules"] = current
    save_state(skill_dir, state)


def run_night(
    skill_dir: Path | str,
    *,
    config: LabConfig | None = None,
    clock: Callable[[], datetime] | None = None,
    promote: bool = True,
    use_llm: bool = False,
    cases: list[LabCase] | None = None,
    objective: ObjectiveConfig | None = None,
    sleeper: Callable[[float], None] | None = None,
    finalize: bool = True,
    hooks: tuple[Callable[..., str | None], Callable[..., str | None]] | None = None,
) -> dict[str, Any]:
    """Run generations until the window, the token cap, or a broken model stops the night.

    Rate limits, timeouts, server errors, and an open LLM circuit wait and
    retry. They do not mark the night finished.
    """
    root = Path(skill_dir)
    cfg = config or LabConfig.load(root)
    now_fn = clock or (lambda: datetime.now(CHICAGO))
    started = now_fn()
    if not in_lab_window(started):
        LOG.info("Lab not started: outside 18:00-07:00 America/Chicago")
        return {
            "night_id": night_id(started),
            "started": False,
            "stop_reason": "outside_window",
            "orders_touched": False,
            "tokens": 0,
            "spend_usd": 0.0,
            "live_scan_changed": False,
            "note": "The lab does not start outside 18:00-07:00 America/Chicago.",
        }
    night = night_id(started)
    state = load_state(root)
    if state.get("night_id") != night or not isinstance(state.get("tabs"), dict):
        state = _blank_state(night, cfg.tabs)
    if state.get("finished") and isinstance(state.get("report"), dict):
        LOG.info("Lab night %s already finished", night)
        return state["report"]

    state = _ensure_setup_rules(root, state)
    objective_cfg = objective or objective_for(root)
    loaded, fixtures_excluded, duplicates_removed, source_name = _load_night_cases(
        root,
        cfg,
        cases=cases,
        horizon_days=objective_cfg.horizon_days,
    )
    plan = _night_plan(
        loaded,
        cfg,
        fixtures_excluded=fixtures_excluded,
        duplicates_removed=duplicates_removed,
    )
    state["case_counts"] = plan.as_dict() | {"source": source_name}
    if not plan.ok:
        LOG.warning("Lab trust gate blocked the night: %s", plan.reason)
        report = _blocked_report(night, plan, prior_tokens=int(state.get("tokens") or 0))
        report["setup_rules"] = state.get("setup_rules")
        state["finished"] = True
        state["tokens"] = 0
        state["report"] = report
        save_state(root, state)
        write_night_report(root, report)
        return report

    train, holdout = plan.train, plan.holdout
    budget = Budget(cfg.token_cap, cfg.usd_per_1k)
    budget.tokens = int(state.get("tokens") or 0)
    cache = state.get("decision_cache") if isinstance(state.get("decision_cache"), dict) else {}
    state["decision_cache"] = cache
    lock = threading.Lock()
    guard = ModelFailureGuard()
    pause = sleeper if sleeper is not None else time.sleep
    if hooks is None:
        propose, decide_inner = _hooks(
            root,
            cfg,
            use_llm=use_llm,
            budget=budget,
            sleeper=pause,
            guard=guard,
            clock=now_fn,
        )
    else:
        propose, decide_inner = hooks
    state_lock = threading.Lock()
    phase: dict[str, Any] = {"name": "", "mark": now_fn()}
    halt = {"reason": ""}
    llm_blocked = False
    forced_reason = ""

    def _phase_bucket(name: str) -> dict[str, Any]:
        phases = state.setdefault("phases", {})
        if not isinstance(phases, dict):
            phases = {}
            state["phases"] = phases
        bucket = phases.get(name)
        if not isinstance(bucket, dict):
            bucket = _blank_phase()
            phases[name] = bucket
        return bucket

    def _flush_seconds() -> None:
        name = str(phase.get("name") or "")
        if not name:
            return
        elapsed = (now_fn() - phase["mark"]).total_seconds()
        bucket = _phase_bucket(name)
        bucket["seconds"] = round(float(bucket.get("seconds") or 0.0) + max(0.0, elapsed), 3)
        phase["mark"] = now_fn()

    def _save_unlocked() -> None:
        state["tokens"] = budget.tokens
        state["spend_usd"] = round(budget.tokens / 1000.0 * float(cfg.usd_per_1k), 4)
        try:
            save_state(root, state)
        except OSError as exc:
            LOG.warning("Lab state checkpoint failed: %s", exc)

    def _persist_state() -> None:
        with state_lock:
            _flush_seconds()
            _save_unlocked()

    def _on_charge(tokens: int) -> None:
        with state_lock:
            previous = int(state.get("tokens") or 0)
            added = max(0, int(tokens) - previous)
            name = str(phase.get("name") or "") or "generations"
            bucket = _phase_bucket(name)
            bucket["tokens"] = int(bucket.get("tokens") or 0) + added
            state["tokens"] = int(tokens)
            state["spend_usd"] = round(int(tokens) / 1000.0 * float(cfg.usd_per_1k), 4)
            _flush_seconds()
            _save_unlocked()

    def _enter(name: str) -> None:
        with state_lock:
            _flush_seconds()
            phase["name"] = name
            phase["mark"] = now_fn()

    def _note_call() -> None:
        name = str(phase.get("name") or "")
        if not name:
            return
        with state_lock:
            bucket = _phase_bucket(name)
            bucket["calls"] = int(bucket.get("calls") or 0) + 1

    def _bump_cache(field: str) -> None:
        with state_lock:
            bucket = _phase_bucket("baseline")
            bucket[field] = int(bucket.get(field) or 0) + 1

    def stop() -> None:
        reason = str(halt.get("reason") or "")
        if not reason and not in_lab_window(now_fn()):
            reason = "window_end"
        if not reason and budget.exhausted:
            reason = "budget_cap"
        if reason:
            halt["reason"] = reason
            raise NightBoundary(reason)

    def _metered(**kwargs: Any) -> str | None:
        _note_call()
        return decide_inner(**kwargs)

    decide = _caching_decide(_metered, cache, lock)
    budget.set_on_charge(_on_charge)
    stored_ids = state.get("baseline_sample_ids") if isinstance(state.get("baseline_sample_ids"), dict) else {}
    train_sample = _resolve_sample(train, stored_ids.get("train"), cfg.baseline_sample)
    holdout_sample = _resolve_sample(holdout, stored_ids.get("holdout"), cfg.baseline_sample)
    sample_ids = {
        "train": [case.case_id for case in train_sample],
        "holdout": [case.case_id for case in holdout_sample],
    }
    state["baseline_sample"] = cfg.baseline_sample
    state["baseline_sample_ids"] = sample_ids
    state["spend_usd"] = round(budget.tokens / 1000.0 * float(cfg.usd_per_1k), 4)
    cache_doc = load_baseline_cache(root)
    _persist_state()

    def _checkpoint_baseline(entry: dict[str, Any]) -> None:
        entries = cache_doc.setdefault("entries", {})
        if isinstance(entries, dict):
            entries[str(entry.get("key") or "")] = entry
        try:
            save_baseline_cache(root, cache_doc)
        except OSError as exc:
            LOG.warning("Lab baseline checkpoint failed: %s", exc)

    def _store_side(entry: dict[str, Any], split: str, case: LabCase, side: str | None) -> None:
        if side in {"long", "cash"}:
            decisions = entry.setdefault("decisions", {})
            if not isinstance(decisions, dict):
                decisions = {}
                entry["decisions"] = decisions
            split_rows = decisions.setdefault(split, {})
            if not isinstance(split_rows, dict):
                split_rows = {}
                decisions[split] = split_rows
            split_rows[case.case_id] = side
            entry["complete"] = False
            _checkpoint_baseline(entry)
        _persist_state()

    def _ensure_baseline(tab: str, texts: dict[str, str]) -> None:
        live_key = f"live_{tab}"
        stored = state.get(live_key) if isinstance(state.get(live_key), dict) else None
        if (
            isinstance(stored, dict)
            and isinstance(stored.get("train"), dict)
            and isinstance(stored.get("holdout"), dict)
            and stored.get("sample_ids") == sample_ids
        ):
            return
        state.pop(live_key, None)
        _enter("baseline")
        model_id = lab_model_id(cfg)
        key = baseline_cache_key(
            tab,
            texts,
            train_sample,
            holdout_sample,
            model_id,
            provider=cfg.provider,
            base_url=cfg.base_url,
        )
        entries = cache_doc.setdefault("entries", {})
        raw_entry = entries.get(key) if isinstance(entries, dict) else None
        entry: dict[str, Any]
        if isinstance(raw_entry, dict):
            entry = raw_entry
        else:
            entry = {
                "key": key,
                "tab": tab,
                "model_id": model_id,
                "complete": False,
                "train_ids": list(sample_ids["train"]),
                "holdout_ids": list(sample_ids["holdout"]),
                "decisions": {"train": {}, "holdout": {}},
                "train": None,
                "holdout": None,
            }
        decisions = entry.get("decisions") if isinstance(entry.get("decisions"), dict) else {}
        train_decisions = decisions.get("train") if isinstance(decisions.get("train"), dict) else {}
        holdout_decisions = decisions.get("holdout") if isinstance(decisions.get("holdout"), dict) else {}
        entry["decisions"] = {"train": dict(train_decisions), "holdout": dict(holdout_decisions)}
        entry["key"] = key
        if entry.get("complete") and isinstance(entry.get("train"), dict) and isinstance(entry.get("holdout"), dict):
            state[live_key] = {
                "train": entry["train"],
                "holdout": entry["holdout"],
                "sample_ids": sample_ids,
                "cache": "hit",
            }
            _bump_cache("cache_hits")
            _persist_state()
            LOG.info("Lab baseline cache hit for %s", tab)
            return
        prior = len(entry["decisions"]["train"]) + len(entry["decisions"]["holdout"])
        if prior:
            _bump_cache("cache_resumes")
            LOG.info("Lab baseline resume for %s stored=%s", tab, prior)
        else:
            _bump_cache("cache_misses")
        _checkpoint_baseline(entry)

        def _on_train(case: LabCase, side: str | None) -> None:
            _store_side(entry, "train", case, side)

        def _on_holdout(case: LabCase, side: str | None) -> None:
            _store_side(entry, "holdout", case, side)

        try:
            train_brief = _score_live(
                tab,
                texts,
                train_sample,
                config=objective_cfg,
                decide=decide,
                drift_weight=cfg.drift_weight,
                skill_dir=root,
                stop=stop,
                remembered=entry["decisions"]["train"],
                on_side=_on_train,
            )
            holdout_brief = _score_live(
                tab,
                texts,
                holdout_sample,
                config=objective_cfg,
                decide=decide,
                drift_weight=cfg.drift_weight,
                skill_dir=root,
                stop=stop,
                remembered=entry["decisions"]["holdout"],
                on_side=_on_holdout,
            )
        except NightBoundary:
            entry["complete"] = False
            _checkpoint_baseline(entry)
            raise
        if guard.aborted:
            entry["complete"] = False
            _checkpoint_baseline(entry)
            _persist_state()
            return
        entry["train"] = train_brief
        entry["holdout"] = holdout_brief
        entry["complete"] = True
        _checkpoint_baseline(entry)
        state[live_key] = {
            "train": train_brief,
            "holdout": holdout_brief,
            "sample_ids": sample_ids,
            "cache": "scored",
        }
        _persist_state()

    _maybe_llm_hypotheses(
        root,
        state,
        config=cfg,
        budget=budget,
        guard=guard,
        clock=now_fn,
        sleeper=pause,
        use_llm=use_llm,
    )

    try:
        while in_lab_window(now_fn()) and not budget.exhausted and not halt["reason"]:
            progressed = False
            hold: LLMUnavailable | None = None
            for tab in cfg.tabs:
                if guard.aborted:
                    break
                if not in_lab_window(now_fn()):
                    raise NightBoundary("window_end")
                if budget.exhausted:
                    raise NightBoundary("budget_cap")
                bucket = state["tabs"].setdefault(
                    tab, {"next_generation": 0, "completed": [], "candidates": [], "installed": False, "errors": []}
                )
                generation = int(bucket.get("next_generation") or 0)
                if bucket.get("stalled"):
                    continue
                if cfg.max_generations and generation >= cfg.max_generations:
                    continue
                if generation in set(bucket.get("completed") or []):
                    bucket["next_generation"] = generation + 1
                    continue
                guard.begin_generation()
                ran_generation = False
                produced: list[dict[str, Any]] = []
                try:
                    live_texts = load_live_texts(root, tab)
                    if not any(live_texts.values()):
                        bucket["errors"].append("no_live_prompt")
                        bucket["completed"].append(generation)
                        bucket["next_generation"] = generation + 1
                        continue
                    _ensure_baseline(tab, live_texts)
                    if guard.aborted:
                        _record_model_stop(bucket, guard)
                    else:
                        _enter("generations")
                        tokens_before = budget.tokens
                        rng = random.Random(f"{night}:{tab}:{generation}")
                        produced = run_generation(
                            tab=tab,
                            generation=generation,
                            count=cfg.candidates,
                            live_texts=live_texts,
                            parents=list(bucket.get("candidates") or []),
                            train_cases=train_sample,
                            holdout_cases=holdout_sample,
                            config=objective_cfg,
                            propose=propose,
                            decide=decide,
                            drift_weight=cfg.drift_weight,
                            rng=rng,
                            skill_dir=root,
                            workers=cfg.concurrency,
                            stop=stop,
                        )
                        if _silent_llm_generation(use_llm, produced, tokens_before, budget):
                            raise LLMUnavailable(30.0, "llm_unavailable")
                        ran_generation = True
                except NightBoundary:
                    raise
                except LLMUnavailable as exc:
                    hold = exc
                    llm_blocked = True
                    LOG.warning("Lab model unavailable for %s; holding this generation: %s", tab, exc.reason)
                    break
                except Exception as exc:
                    LOG.warning("Lab generation failed for %s: %s", tab, exc)
                    bucket.setdefault("errors", []).append(str(exc))
                    _persist_state()
                    if guard.aborted:
                        break
                    continue
                if not ran_generation:
                    _persist_state()
                    break
                for row in produced:
                    _log_candidate(row)
                    bucket.setdefault("candidates", []).append(_persistable_candidate(row))
                bucket.setdefault("completed", []).append(generation)
                bucket["next_generation"] = generation + 1
                if produced and all(row.get("dropped") for row in produced):
                    bucket["empty_streak"] = int(bucket.get("empty_streak") or 0) + 1
                else:
                    bucket["empty_streak"] = 0
                if int(bucket.get("empty_streak") or 0) >= 3:
                    bucket.setdefault("errors", []).append("no_candidates")
                    bucket["stalled"] = True
                    LOG.warning("Lab stopped %s after three generations with no usable prompt", tab)
                if guard.aborted:
                    _record_model_stop(bucket, guard)
                _persist_state()
                progressed = True
                LOG.info(
                    "Lab %s generation %s stored %s candidates tokens=%s",
                    tab,
                    generation,
                    len(produced),
                    budget.tokens,
                )
                if guard.aborted:
                    break
            if hold is not None:
                _enter("")
                _persist_state()
                if not in_lab_window(now_fn()):
                    break
                left = _seconds_until_window_end(now_fn())
                if left <= 0:
                    break
                wait = min(left, max(hold.retry_after, 1.0))
                LOG.info("Lab backing off %.0fs before retrying the model", wait)
                try:
                    pause(wait)
                except Exception as sleep_exc:
                    LOG.warning("Lab backoff sleep failed: %s", sleep_exc)
                continue
            if guard.aborted or not progressed:
                break
    except NightBoundary as boundary:
        forced_reason = boundary.reason
        finalize = True
        LOG.info("Lab stopped between calls: %s", boundary.reason)

    _enter("")
    _persist_state()

    if not finalize:
        return {
            "night_id": night,
            "finished": False,
            "orders_touched": False,
            "tokens": budget.tokens,
            "spend_usd": round(budget.tokens / 1000.0 * float(cfg.usd_per_1k), 4),
            "stopped_early": bool(guard.aborted),
            "stop_reason": guard.reason if guard.aborted else "",
        }

    report = finalize_night(root, state, config=cfg, objective=objective_cfg, promote=promote, budget=budget)
    outage = llm_blocked and budget.tokens == 0 and not guard.aborted and not in_lab_window(now_fn())
    if outage and forced_reason == "window_end":
        forced_reason = ""
    if outage:
        report["status"] = "llm_unavailable"
        report["stop_reason"] = "llm_unavailable"
        report["stopped_early"] = False
        report["note"] = (
            "The model stayed unavailable until the window ended. "
            "No prompt was scored. Spend is $0. Shadow only. Live prompts were not changed."
        )
    elif forced_reason:
        report["stopped_early"] = True
        report["stop_reason"] = forced_reason
    elif guard.aborted:
        report["stopped_early"] = True
        report["stop_reason"] = guard.reason
    elif budget.exhausted:
        report["stopped_early"] = True
        report["stop_reason"] = "budget_cap"
    else:
        report["stopped_early"] = False
        report["stop_reason"] = ""
    state["finished"] = True
    state["tokens"] = budget.tokens
    state["spend_usd"] = report.get("spend_usd")
    state["report"] = report
    save_state(root, state)
    write_night_report(root, report)
    return report


def _log_candidate(row: dict[str, Any]) -> None:
    train = row.get("train") if isinstance(row.get("train"), dict) else {}
    sealed = row.get("sealed") if isinstance(row.get("sealed"), dict) else {}
    LOG.info(
        "Lab candidate %s operator=%s dropped=%s train_sortino=%s holdout_sortino=%s train_n=%s holdout_n=%s",
        row.get("id"),
        row.get("operator"),
        row.get("dropped"),
        train.get("sortino_adj"),
        sealed.get("sortino_adj"),
        train.get("n"),
        sealed.get("n"),
    )


def _hooks(
    skill_dir: Path,
    config: LabConfig,
    *,
    use_llm: bool,
    budget: Budget,
    sleeper: Callable[[float], None] | None,
    guard: ModelFailureGuard | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[Callable[..., str | None], Callable[..., str | None]]:
    if not use_llm:
        return offline_propose, offline_decide

    def _blocked() -> bool:
        return budget.exhausted or (guard is not None and guard.aborted)

    def propose(**kwargs: Any) -> str | None:
        from core.tab_agent_contract import keeps_contract
        from core.tab_agent_lab_tournament import critic_user, generator_user
        from core.today_tab_agent import parse_prompt_proposal

        if _blocked():
            return None
        user = generator_user(
            tab=str(kwargs["tab"]),
            prompt_key=str(kwargs["prompt_key"]),
            operator=str(kwargs["operator"]),
            parent_text=str(kwargs.get("parent_text") or ""),
            partner_text=kwargs.get("partner_text"),
            history=list(kwargs.get("history") or []),
        )
        draft = _model_call(
            skill_dir,
            config,
            "You revise one analysis prompt. You do not place orders.",
            user,
            budget,
            sleeper,
            guard,
            clock,
        )
        parsed = parse_prompt_proposal(draft)
        if parsed is None:
            return None
        critique = _model_call(
            skill_dir,
            config,
            "You are the critic. You do not place orders.",
            critic_user(parsed["text"], tab=str(kwargs["tab"]), prompt_key=str(kwargs["prompt_key"])),
            budget,
            sleeper,
            guard,
            clock,
        )
        revised = parse_prompt_proposal(critique) or parsed
        text = str(revised.get("text") or "")
        if keeps_contract(str(kwargs["tab"]), str(kwargs["prompt_key"]), text):
            return text
        reminder = generator_user(
            tab=str(kwargs["tab"]),
            prompt_key=str(kwargs["prompt_key"]),
            operator="repair",
            parent_text=text,
            partner_text=None,
            history=[],
        )
        repaired = parse_prompt_proposal(
            _model_call(
                skill_dir,
                config,
                "Restore the required tokens. You do not place orders.",
                reminder,
                budget,
                sleeper,
                guard,
                clock,
            )
        )
        if repaired and keeps_contract(str(kwargs["tab"]), str(kwargs["prompt_key"]), str(repaired.get("text") or "")):
            return str(repaired["text"])
        return None

    def decide(*, tab: str, texts: dict[str, str], case: LabCase) -> str | None:
        from core.tab_agent_replay import parse_decision

        if _blocked():
            return None
        from core.tab_agent_lab_plan import decision_payload

        payload = decision_payload(case, reveal_ticker=config.reveal_live_ticker)
        if tab == "debate":
            system = texts.get("system") or ""
            first = _model_call(
                skill_dir,
                config,
                system,
                (texts.get("round1") or "") + "\n" + json.dumps(payload),
                budget,
                sleeper,
                guard,
                clock,
            )
            second = _model_call(
                skill_dir,
                config,
                system,
                (texts.get("round2") or "") + "\n" + first,
                budget,
                sleeper,
                guard,
                clock,
            )
            return parse_decision(second or first, tab="debate", quality_threshold=60.0, prompt_key="round2")
        key = next(iter(texts))
        raw = _model_call(skill_dir, config, texts[key], json.dumps(payload), budget, sleeper, guard, clock)
        return parse_decision(raw, tab=tab, quality_threshold=60.0, prompt_key=key)

    return propose, decide


def _record_model_stop(bucket: dict[str, Any], guard: ModelFailureGuard) -> None:
    reason = guard.reason or "model_failure"
    errors = bucket.setdefault("errors", [])
    if reason not in errors:
        errors.append(reason)


def _lab_targets_xai(config: LabConfig) -> bool:
    """True when this lab call should use xAI. Explicit ``openai`` wins."""
    provider = (config.provider or "").strip().lower()
    if provider == "openai":
        return False
    if provider == "xai":
        return True
    model = (config.model or "").strip().lower()
    base = (config.base_url or "").strip().lower()
    return model.startswith("grok") or "x.ai" in base


def _openai_route_key(env: dict[str, str]) -> str:
    """Key order used when the lab is not calling xAI. Never prefers ``XAI_API_KEY``."""
    return (
        env.get("MIROFISH_API_KEY")
        or os.environ.get("MIROFISH_API_KEY", "")
        or os.environ.get("OPENAI_API_KEY", "")
        or os.environ.get("OPENAI_KEY", "")
        or ""
    )


def _model_call(
    skill_dir: Path,
    config: LabConfig,
    system: str,
    user: str,
    budget: Budget,
    sleeper: Callable[[float], None] | None,
    guard: ModelFailureGuard | None = None,
    clock: Callable[[], datetime] | None = None,
) -> str:
    if guard is not None and guard.aborted:
        return ""
    now_fn = clock or (lambda: datetime.now(CHICAGO))
    pause = sleeper or (lambda _seconds: None)
    # A direct call with no night clock (routing tests) is not a windowed night.
    # Production always passes the Chicago clock from ``run_night``.
    if clock is not None and not in_lab_window(now_fn()):
        raise NightBoundary("window_end")
    if budget.exhausted:
        raise NightBoundary("budget_cap")

    def _bounded_pause(seconds: float) -> None:
        """Retry sleep stops at 07:00. It does not run the clock past the window."""
        if clock is None:
            pause(seconds)
            return
        if not in_lab_window(now_fn()):
            raise LLMUnavailable(0.0, "llm_unavailable")
        left = _seconds_until_window_end(now_fn())
        pause(min(max(0.0, float(seconds)), left))
        if not in_lab_window(now_fn()):
            raise LLMUnavailable(0.0, "llm_unavailable")

    def _once() -> str:
        import config as skill_config
        from engine_analysis import _call_llm

        # An open circuit must wait here. Returning "" is what let the night
        # store contract drops and finish during the 2026-11-23 outage.
        _wait_out_circuit(pause, now_fn)

        # Copy: config._load_env caches the parsed dict. Model and base URL are
        # set on this copy only. Never write LLM_MODEL_NAME or LLM_BASE_URL
        # back to .env; those keys change scan-time MiroFish.
        env = dict(skill_config._load_env(skill_dir))
        if config.model:
            env["LLM_MODEL_NAME"] = config.model
        if not _lab_targets_xai(config):
            # A grok model with provider=openai must keep the OpenAI key order.
            # _call_llm would otherwise treat the model id as an xAI route.
            routed_model = str(env.get("LLM_MODEL_NAME") or os.environ.get("LLM_MODEL_NAME") or "")
            routed_base = str(env.get("LLM_BASE_URL") or os.environ.get("LLM_BASE_URL") or "")
            if routed_model.lower().startswith("grok") or "x.ai" in routed_base.lower():
                return _call_llm(
                    user,
                    system,
                    env,
                    max_tokens=700,
                    api_key=_openai_route_key(env),
                    raise_on_error=True,
                )
            return _call_llm(user, system, env, max_tokens=700, raise_on_error=True)
        from core.llm_endpoint import XAI_DEFAULT_BASE_URL, resolve_llm_endpoint

        base = (config.base_url or "").strip() or XAI_DEFAULT_BASE_URL
        env["LLM_BASE_URL"] = base
        overrides = {"LLM_BASE_URL": base}
        if config.model:
            overrides["LLM_MODEL_NAME"] = config.model
        endpoint = resolve_llm_endpoint(env, overrides=overrides)
        return _call_llm(
            user,
            system,
            env,
            max_tokens=700,
            api_key=endpoint.api_key,
            base_url=endpoint.base_url,
            model=endpoint.model,
            raise_on_error=True,
        )

    failed = False
    last_exc: Exception | None = None

    def _on_failure(exc: Exception) -> None:
        nonlocal failed, last_exc
        failed = True
        last_exc = exc
        # Transient outages must not trip the hard-failure guard. That guard
        # stops the night after two identical loader errors.
        if _transient_llm_failure(exc):
            LOG.warning("Lab model call failed: %s", exc)
            return
        if guard is not None:
            guard.note_failure(exc)
        else:
            LOG.warning("Lab model call failed: %s", exc)

    text = call_with_backoff(_once, sleeper=_bounded_pause, on_failure=_on_failure)
    if last_exc is not None and _transient_llm_failure(last_exc):
        header = _retry_after_seconds(last_exc)
        raise LLMUnavailable(30.0 if header is None else max(header, 1.0), "llm_unavailable")
    if failed:
        return text
    if guard is not None:
        guard.note_success()
    if text:
        budget.charge(system, user, text)
    return text


def finalize_night(
    skill_dir: Path,
    state: dict[str, Any],
    *,
    config: LabConfig,
    objective: ObjectiveConfig,
    promote: bool,
    budget: Budget,
) -> dict[str, Any]:
    """Pick at most one shadow per tab and write the morning numbers."""
    tabs_out: dict[str, Any] = {}
    for tab in config.tabs:
        bucket = state.get("tabs", {}).get(tab) if isinstance(state.get("tabs"), dict) else {}
        if not isinstance(bucket, dict):
            continue
        live = state.get(f"live_{tab}") if isinstance(state.get(f"live_{tab}"), dict) else {}
        live_train = live.get("train") if isinstance(live.get("train"), dict) else {}
        live_holdout = live.get("holdout") if isinstance(live.get("holdout"), dict) else {}
        candidates = [row for row in bucket.get("candidates") or [] if isinstance(row, dict)]
        winner, failures = select_winner(
            candidates,
            live_train,
            live_holdout,
            margin=config.margin,
            min_train=objective.min_samples,
            min_holdout=config.min_holdout,
            drawdown_limit=objective.drawdown_tolerance,
            min_participation=config.min_participation,
            min_longs=config.min_longs,
        )
        shadow: dict[str, Any] = {"installed": False, "version_ids": [], "orders_touched": False}
        if winner is not None and promote and not bucket.get("installed"):
            try:
                shadow = _install(skill_dir, state, tab, winner, live_train)
                bucket["installed"] = bool(shadow.get("installed"))
                bucket["shadow_ids"] = shadow.get("version_ids") or []
            except Exception as exc:
                LOG.warning("Lab shadow install failed for %s: %s", tab, exc)
                shadow = {"installed": False, "reason": str(exc), "orders_touched": False}
        elif bucket.get("installed"):
            shadow = {
                "installed": True,
                "version_ids": bucket.get("shadow_ids") or [],
                "reason": "already_installed",
                "orders_touched": False,
            }
        best = _best_train(candidates)
        tabs_out[tab] = {
            "generations": len(bucket.get("completed") or []),
            "candidates_tried": len(candidates),
            "live_train": live_train,
            "live_holdout": live_holdout,
            "best_train": _public_score(best, "train"),
            "best_holdout": _public_score(best, "sealed"),
            "winner": None
            if winner is None
            else _public_score(winner, "train") | {"id": winner.get("id"), "holdout": winner.get("sealed")},
            "shadow": shadow,
            "gate_failures": failures,
            "errors": bucket.get("errors") or [],
            "source_sortino": _source_sortino(live_train, live_holdout),
        }
    counts = state.get("case_counts") if isinstance(state.get("case_counts"), dict) else {}
    return {
        "night_id": state.get("night_id"),
        "window": "18:00-07:00 America/Chicago, weekends included",
        "orders_touched": False,
        "tokens": budget.tokens,
        "spend_usd": budget.spend_usd,
        "token_cap": config.token_cap,
        "cases": counts,
        "candidates_tried": sum(int(row.get("candidates_tried") or 0) for row in tabs_out.values()),
        "generations": {tab: row.get("generations") for tab, row in tabs_out.items()},
        "tabs": tabs_out,
        "source_sortino": _primary_source_sortino(tabs_out),
        "usage": _usage_report(state, budget, config),
        "setup_rules": state.get("setup_rules"),
        "note": "Shadow only. Live prompts change later, through the governor, after live samples.",
    }


def _usage_report(state: dict[str, Any], budget: Budget, config: LabConfig) -> dict[str, Any]:
    """Baseline versus generation time, calls, cache, and spend for the morning report."""
    phases = state.get("phases") if isinstance(state.get("phases"), dict) else {}
    baseline = phases.get("baseline") if isinstance(phases.get("baseline"), dict) else {}
    generations = phases.get("generations") if isinstance(phases.get("generations"), dict) else {}
    baseline_calls = int(baseline.get("calls") or 0)
    generation_calls = int(generations.get("calls") or 0)
    baseline_seconds = float(baseline.get("seconds") or 0.0)
    generation_seconds = float(generations.get("seconds") or 0.0)
    call_total = baseline_calls + generation_calls
    second_total = baseline_seconds + generation_seconds
    sample = state.get("baseline_sample")
    return {
        "baseline_seconds": round(baseline_seconds, 3),
        "generation_seconds": round(generation_seconds, 3),
        "baseline_tokens": int(baseline.get("tokens") or 0),
        "generation_tokens": int(generations.get("tokens") or 0),
        "baseline_calls": baseline_calls,
        "generation_calls": generation_calls,
        "baseline_cache_hits": int(baseline.get("cache_hits") or 0),
        "baseline_cache_misses": int(baseline.get("cache_misses") or 0),
        "baseline_cache_resumes": int(baseline.get("cache_resumes") or 0),
        "baseline_sample": int(sample) if sample is not None else config.baseline_sample,
        "baseline_sample_ids": state.get("baseline_sample_ids")
        if isinstance(state.get("baseline_sample_ids"), dict)
        else {},
        "baseline_call_share": round(baseline_calls / call_total, 4) if call_total else 0.0,
        "baseline_time_share": round(baseline_seconds / second_total, 4) if second_total else 0.0,
        "tokens": budget.tokens,
        "spend_usd": budget.spend_usd,
    }


def _slice_brief(book: Any, key: str) -> dict[str, Any] | None:
    if not isinstance(book, dict):
        return None
    slices = book.get("slices")
    if not isinstance(slices, dict):
        return None
    row = slices.get(key)
    if not isinstance(row, dict):
        return None
    return {"sortino_adj": row.get("sortino_adj"), "n": row.get("n"), "n_longs": row.get("n_longs")}


def _source_sortino(live_train: Any, live_holdout: Any) -> dict[str, Any]:
    return {
        "rebuild_pre_cutoff": _slice_brief(live_train, "rebuild_pre_cutoff"),
        "rebuild_post_cutoff": _slice_brief(live_train, "rebuild_post_cutoff"),
        "live_train": _slice_brief(live_train, "live"),
        "live_holdout": _slice_brief(live_holdout, "live"),
    }


def _primary_source_sortino(tabs_out: dict[str, Any]) -> dict[str, Any]:
    row = tabs_out.get("today")
    if not isinstance(row, dict) and tabs_out:
        row = next(iter(tabs_out.values()))
    if not isinstance(row, dict):
        return _source_sortino({}, {})
    stored = row.get("source_sortino")
    if isinstance(stored, dict):
        return stored
    return _source_sortino(row.get("live_train"), row.get("live_holdout"))


def _install(
    skill_dir: Path, state: dict[str, Any], tab: str, winner: dict[str, Any], live_train: dict[str, Any]
) -> dict[str, Any]:
    from core.tab_agent_governor import install_lab_shadow

    texts = winner.get("texts") if isinstance(winner.get("texts"), dict) else {}
    rationale = f"Overnight lab {state.get('night_id')} {winner.get('id')} operator={winner.get('operator')}"
    result = install_lab_shadow(
        skill_dir,
        tab,
        {str(key): str(value) for key, value in texts.items()},
        rationale=rationale,
        train_stats=winner.get("train") if isinstance(winner.get("train"), dict) else live_train,
        holdout_stats=winner.get("sealed") if isinstance(winner.get("sealed"), dict) else {},
    )
    save_state(skill_dir, state)
    return result


def _best_train(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    from core.tab_agent_lab_score import rank_key

    ranked = [row for row in candidates if isinstance(row.get("train"), dict) and not row.get("dropped")]
    if not ranked:
        return None
    ranked.sort(key=lambda row: rank_key(row["train"]), reverse=True)
    return ranked[0]


def _public_score(row: dict[str, Any] | None, field: str) -> dict[str, Any] | None:
    if row is None:
        return None
    book = row.get(field) if isinstance(row.get(field), dict) else {}
    return {
        "id": row.get("id"),
        "operator": row.get("operator"),
        "prompt_key": row.get("prompt_key"),
        "sortino": book.get("sortino"),
        "sortino_adj": book.get("sortino_adj"),
        "sortino_method": book.get("sortino_method"),
        "max_drawdown": book.get("max_drawdown"),
        "n_longs": book.get("n_longs"),
        "smoothness": book.get("smoothness"),
        "n": book.get("n"),
        "win_rate": book.get("win_rate"),
        "penalty": book.get("penalty"),
    }


def write_night_report(skill_dir: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    from _io_utils import atomic_write_json

    night = str(report.get("night_id") or "dry-run")
    directory = skill_dir / REPORT_DIR
    json_path = directory / f"{night}.json"
    md_path = directory / f"{night}.md"
    atomic_write_json(json_path, report, indent=2)
    md_path.write_text(render_report_md(report), encoding="utf-8")
    return json_path, md_path


def render_report_md(report: dict[str, Any]) -> str:
    lines = [
        f"# Overnight prompt lab {report.get('night_id')}",
        "",
        str(report.get("note") or ""),
        "",
        f"Window: {report.get('window')}",
        f"Candidates tried: {report.get('candidates_tried')}",
        f"Tokens: {report.get('tokens')} spend_usd={report.get('spend_usd')} cap={report.get('token_cap')}",
        f"Orders touched: {report.get('orders_touched')}",
    ]
    usage = report.get("usage") if isinstance(report.get("usage"), dict) else {}
    if usage:
        lines.append(
            "Baseline: "
            f"{usage.get('baseline_seconds')}s calls={usage.get('baseline_calls')} "
            f"tokens={usage.get('baseline_tokens')} "
            f"call_share={usage.get('baseline_call_share')} "
            f"time_share={usage.get('baseline_time_share')}"
        )
        lines.append(
            "Generations: "
            f"{usage.get('generation_seconds')}s calls={usage.get('generation_calls')} "
            f"tokens={usage.get('generation_tokens')}"
        )
        lines.append(
            "Baseline cache: "
            f"hits={usage.get('baseline_cache_hits')} "
            f"misses={usage.get('baseline_cache_misses')} "
            f"resumes={usage.get('baseline_cache_resumes')} "
            f"sample={usage.get('baseline_sample')}"
        )
    if report.get("status"):
        lines.append(f"Status: {report.get('status')}")
    if report.get("stopped_early"):
        lines.append(f"Stopped early: {report.get('stop_reason')}")
    if report.get("trust_reason"):
        lines.extend(["", str(report.get("trust_reason"))])
    lines.append("")
    sources = report.get("source_sortino")
    if isinstance(sources, dict) and any(sources.values()):
        lines.append("Source Sortino:")
        for name, row in sources.items():
            if isinstance(row, dict):
                lines.append(f"- {name} sortino={row.get('sortino_adj')} n={row.get('n')}")
        lines.append("")
    if report.get("sample_floor_note"):
        lines.extend([str(report["sample_floor_note"]), ""])
    for tab, row in (report.get("tabs") or {}).items():
        if not isinstance(row, dict):
            continue
        lines.append(f"## {tab}")
        lines.append(f"Generations: {row.get('generations')} candidates: {row.get('candidates_tried')}")
        lines.append(f"Live train: {_fmt_book(row.get('live_train'))}")
        lines.append(f"Live holdout: {_fmt_book(row.get('live_holdout'))}")
        lines.append(f"Best train: {_fmt_book(row.get('best_train'))}")
        lines.append(f"That candidate's holdout: {_fmt_book(row.get('best_holdout'))}")
        shadow = row.get("shadow") if isinstance(row.get("shadow"), dict) else {}
        lines.append(
            f"Shadow: installed={shadow.get('installed')} versions={shadow.get('version_ids')} reason={shadow.get('reason')}"
        )
        if row.get("gate_failures"):
            lines.append(f"Gate failures: {row.get('gate_failures')}")
        if row.get("errors"):
            lines.append(f"Errors: {row.get('errors')}")
        lines.append("")
    setup = report.get("setup_rules")
    if isinstance(setup, dict):
        from core.setup_rule_search import render_setup_rules_md

        lines.append(render_setup_rules_md(setup).rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _fmt_book(book: Any) -> str:
    if not isinstance(book, dict) or not book:
        return "none"
    return (
        f"{book.get('id') or ''} sortino={book.get('sortino_adj', book.get('sortino'))} "
        f"maxDD={book.get('max_drawdown')} smooth={book.get('smoothness')} n={book.get('n')}"
    ).strip()


def report_for_digest(skill_dir: Path | str, day: date) -> dict[str, Any] | None:
    """The night that ended on ``day`` (the morning after the 6 PM start)."""
    root = Path(skill_dir)
    night = (day - timedelta(days=1)).isoformat()
    path = root / REPORT_DIR / f"{night}.json"
    if not path.exists():
        same_day = root / REPORT_DIR / f"{day.isoformat()}.json"
        path = same_day if same_day.exists() else path
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def launch_lab_process(skill_dir: Path | str, argv: list[str] | None = None) -> dict[str, Any]:
    """Start the lab in a separate process. A live pid is left alone."""
    root = Path(skill_dir)
    pid_path = root / PID_FILE
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_pid(pid_path)
    if existing and pid_alive(existing):
        return {"launched": False, "reason": "already_running", "pid": existing}
    command = argv or [sys.executable, str(_script_path()), "--until-window-end", "--skill-dir", str(root)]
    log_path = root / LOG_FILE
    flags = 0
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    handle = log_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
    finally:
        handle.close()
    pid_path.write_text(str(proc.pid), encoding="utf-8")
    LOG.info("Lab process started pid=%s", proc.pid)
    return {"launched": True, "reason": "started", "pid": proc.pid}


def _read_pid(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "tab_agent_lab.py"


def scheduler_tick(
    skill_dir: Path | str, now: datetime, *, launch: Callable[[Path], dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Minute hook. Returns immediately. Failures stay in this process."""
    try:
        root = Path(skill_dir)
        config = LabConfig.load(root)
        if not config.enabled:
            return {"launched": False, "reason": "disabled"}
        if not in_lab_window(now):
            return {"launched": False, "reason": "outside_window"}
        starter = launch or (lambda path: launch_lab_process(path))
        return starter(root)
    except Exception as exc:
        LOG.warning("Tab-agent lab tick failed: %s", exc)
        return {"launched": False, "reason": "error", "error": str(exc)}


def plan_night(skill_dir: Path | str, *, config: LabConfig | None = None) -> dict[str, Any]:
    """Split and gate the stored cases. Does not call a model or write the night report."""
    root = Path(skill_dir)
    cfg = config or LabConfig.load(root)
    objective = objective_for(root)
    loaded, fixtures_excluded, duplicates_removed, source_name = _load_night_cases(
        root,
        cfg,
        cases=None,
        horizon_days=objective.horizon_days,
    )
    plan = _night_plan(
        loaded,
        cfg,
        fixtures_excluded=fixtures_excluded,
        duplicates_removed=duplicates_removed,
    )
    payload = plan.as_dict()
    payload["source"] = source_name
    payload["rebuild"] = bool(cfg.rebuild)
    payload["dry_run"] = True
    payload["plan"] = True
    return payload


def run_dry(
    skill_dir: Path | str,
    *,
    tab: str | None = None,
    candidates: int = 4,
    generations: int = 1,
    cases: list[LabCase] | None = None,
) -> dict[str, Any]:
    """One short offline generation. Writes a report and does not install a shadow."""
    root = Path(skill_dir)
    chosen = (tab,) if tab else ("today",)
    for name in chosen:
        if name not in TABS:
            raise ValueError(f"Unknown tab {name}")
    config = LabConfig(
        enabled=True,
        concurrency=1,
        candidates=max(2, candidates),
        token_cap=0,
        holdout_weeks=6,
        embargo_days=0,
        margin=0.05,
        min_holdout=8,
        min_train_cases=1,
        min_train_dates=1,
        min_holdout_cases=1,
        min_holdout_dates=1,
        train_per_era=0,
        max_generations=max(1, generations),
        tabs=chosen,
    )
    sample = cases if cases is not None else synthetic_cases(chosen[0])
    when = datetime(2026, 11, 11, 18, 30, tzinfo=CHICAGO)
    calls = {"n": 0}

    def clock() -> datetime:
        calls["n"] += 1
        if calls["n"] > 8 + generations * 4:
            return datetime(2026, 11, 12, 7, 0, tzinfo=CHICAGO)
        return when

    objective = objective_for(None)
    objective = ObjectiveConfig(
        metric=objective.metric,
        min_samples=12,
        drawdown_tolerance=objective.drawdown_tolerance,
        horizon_days=objective.horizon_days,
    )
    # Isolate the dry-run from a real night file by using the skill dir the caller passed.
    report = run_night(
        root,
        config=config,
        clock=clock,
        promote=False,
        use_llm=False,
        cases=sample,
        objective=objective,
    )
    report["dry_run"] = True
    report["cases_source"] = "caller" if cases is not None else "synthetic"
    report["min_train_samples"] = 12
    report["min_holdout_samples"] = config.min_holdout
    report["sample_floor_note"] = (
        "Dry-run uses a train floor of 12 so the synthetic book can name a winner. "
        "The night loop uses TAB_AGENT_MIN_SAMPLES."
    )
    write_night_report(root, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Overnight tab-agent prompt lab. Analysis prompts only.")
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dry-run", action="store_true", help="One offline generation. Does not install a shadow.")
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print the date split and trust gate for stored cases. No model calls.",
    )
    parser.add_argument("--until-window-end", action="store_true", help="Run until 7:00 AM America/Chicago.")
    parser.add_argument(
        "--setup-rules",
        action="store_true",
        help="Offline setup-rule search only. Does not start the night and does not trade.",
    )
    parser.add_argument(
        "--trades",
        type=Path,
        default=None,
        help="With --setup-rules, a chunk directory, JSON list, or JSONL file.",
    )
    parser.add_argument("--tab", default="", help="Dry-run one tab. Default: today.")
    parser.add_argument("--candidates", type=int, default=4)
    parser.add_argument("--generations", type=int, default=1)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.plan:
            report = plan_night(args.skill_dir)
            print(json.dumps(report, indent=2, default=str))
            return 0
        if args.setup_rules:
            from core.setup_rule_search import render_setup_rules_md, run_setup_rule_phase

            report = run_setup_rule_phase(args.skill_dir, trades_path=args.trades, write_shadow=True)
            text = render_setup_rules_md(report)
            directory = Path(args.skill_dir) / REPORT_DIR
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(CHICAGO).date().isoformat()
            md_path = directory / f"setup-rules-{stamp}.md"
            json_path = directory / f"setup-rules-{stamp}.json"
            md_path.write_text(text, encoding="utf-8")
            json_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
            print(text)
            print(f"wrote {md_path}")
            return 0
        if args.dry_run:
            report = run_dry(
                args.skill_dir,
                tab=args.tab or "today",
                candidates=args.candidates,
                generations=args.generations,
            )
            print(json.dumps(_dry_summary(report), indent=2, default=str))
            return 0
        if args.until_window_end:
            config = LabConfig.load(args.skill_dir)
            run_night(args.skill_dir, config=config, promote=True, use_llm=True)
            return 0
        parser.print_help()
        return 2
    except Exception as exc:
        LOG.exception("Tab-agent lab stopped: %s", exc)
        if args.dry_run:
            print(str(exc), file=sys.stderr)
            return 1
        return 0


def _dry_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Stdout view of one dry-run. Full books stay in the report file."""
    tabs = {}
    for name, row in (report.get("tabs") or {}).items():
        if not isinstance(row, dict):
            continue
        shadow = row.get("shadow") if isinstance(row.get("shadow"), dict) else {}
        tabs[name] = {
            "generations": row.get("generations"),
            "candidates_tried": row.get("candidates_tried"),
            "live_train": row.get("live_train"),
            "live_holdout": row.get("live_holdout"),
            "best_train": row.get("best_train"),
            "best_holdout": row.get("best_holdout"),
            "winner": row.get("winner"),
            "shadow_installed": shadow.get("installed"),
            "gate_failures": row.get("gate_failures"),
        }
    return {
        "dry_run": True,
        "night_id": report.get("night_id"),
        "orders_touched": False,
        "cases_source": report.get("cases_source"),
        "tokens": report.get("tokens"),
        "spend_usd": report.get("spend_usd"),
        "min_train_samples": report.get("min_train_samples"),
        "min_holdout_samples": report.get("min_holdout_samples"),
        "sample_floor_note": report.get("sample_floor_note"),
        "tabs": tabs,
    }
