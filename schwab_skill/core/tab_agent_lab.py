"""Overnight prompt lab for the five tab agents.

The scheduler starts this in its own process. From 6:00 PM to 7:00 AM
America/Chicago, including weekends, each tab runs generations of rival
prompts. A winner that beats the live prompt on the train window and on the
sealed holdout is installed as shadow through the governor. Live text is
never replaced here.

The loop is resumable: one state file per night is replaced atomically after
each generation. A crash retries the unfinished generation and does not
install a second shadow for a night that already finished.
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
TABS = ("today", "debate", "research", "backtest", "system")


@dataclass
class LabConfig:
    enabled: bool = False
    concurrency: int = 6
    candidates: int = 8
    model: str = ""
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
    tabs: tuple[str, ...] = TABS

    @classmethod
    def load(cls, skill_dir: Path | None = None) -> LabConfig:
        from config import (
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
            tabs=get_tab_agent_lab_tabs(skill_dir),
        )


class Budget:
    """Approximate token counter. A cap of 0 does not stop the night."""

    def __init__(self, cap: int, usd_per_1k: float) -> None:
        self.cap = max(0, int(cap))
        self.usd_per_1k = float(usd_per_1k)
        self.tokens = 0
        self._lock = threading.Lock()

    def charge(self, *parts: str) -> bool:
        used = sum(len(part or "") for part in parts) // 4
        with self._lock:
            if used:
                self.tokens += used
            return self.cap <= 0 or self.tokens < self.cap

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
    markers = ("429", "rate limit", "ratelimit", "timeout", "timed out", "502", "503", "overloaded")
    return any(marker in text for marker in markers)


def call_with_backoff(
    fn: Callable[[], str],
    *,
    sleeper: Callable[[float], None] | None = None,
    attempts: int = 6,
    on_failure: Callable[[Exception], None] | None = None,
) -> str:
    """Call the model. Rate limits and transient API errors wait and retry.

    A hard failure returns an empty string so the night loop keeps going.
    ``on_failure`` owns that log line when the caller is tallying the generation.
    """
    pause = sleeper or (lambda _seconds: None)
    delay = 1.0
    for attempt in range(max(1, attempts)):
        try:
            return str(fn() or "")
        except Exception as exc:
            if not _retryable(exc) or attempt >= attempts - 1:
                if on_failure is not None:
                    on_failure(exc)
                else:
                    LOG.warning("Lab model call failed: %s", exc)
                return ""
            LOG.info("Lab model call retrying after %s seconds: %s", delay, exc)
            try:
                pause(delay)
            except Exception as sleep_exc:
                LOG.warning("Lab backoff sleep failed: %s", sleep_exc)
            delay = min(delay * 2, 30.0)
    return ""


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


def _blank_state(night: str, tabs: tuple[str, ...]) -> dict[str, Any]:
    return {
        "night_id": night,
        "finished": False,
        "orders_touched": False,
        "tokens": 0,
        "tabs": {
            tab: {"next_generation": 0, "completed": [], "candidates": [], "installed": False, "errors": []}
            for tab in tabs
        },
        "decision_cache": {},
    }


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
    """Run generations until the window, a cap, or a broken model stops the night."""
    root = Path(skill_dir)
    cfg = config or LabConfig.load(root)
    now_fn = clock or (lambda: datetime.now(CHICAGO))
    started = now_fn()
    night = night_id(started)
    state = load_state(root)
    if state.get("night_id") != night or not isinstance(state.get("tabs"), dict):
        state = _blank_state(night, cfg.tabs)
    if state.get("finished") and isinstance(state.get("report"), dict):
        LOG.info("Lab night %s already finished", night)
        return state["report"]

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
    if hooks is None:
        propose, decide_inner = _hooks(root, cfg, use_llm=use_llm, budget=budget, sleeper=sleeper, guard=guard)
    else:
        propose, decide_inner = hooks
    decide = _caching_decide(decide_inner, cache, lock)

    while in_lab_window(now_fn()) and not budget.exhausted:
        progressed = False
        for tab in cfg.tabs:
            if not in_lab_window(now_fn()) or budget.exhausted or guard.aborted:
                break
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
                live_key = f"live_{tab}"
                if live_key not in state:
                    state[live_key] = {
                        "train": _score_live(
                            tab,
                            live_texts,
                            train,
                            config=objective_cfg,
                            decide=decide,
                            drift_weight=cfg.drift_weight,
                            skill_dir=root,
                        ),
                        "holdout": _score_live(
                            tab,
                            live_texts,
                            holdout,
                            config=objective_cfg,
                            decide=decide,
                            drift_weight=cfg.drift_weight,
                            skill_dir=root,
                        ),
                    }
                if guard.aborted:
                    _record_model_stop(bucket, guard)
                else:
                    rng = random.Random(f"{night}:{tab}:{generation}")
                    produced = run_generation(
                        tab=tab,
                        generation=generation,
                        count=cfg.candidates,
                        live_texts=live_texts,
                        parents=list(bucket.get("candidates") or []),
                        train_cases=train,
                        holdout_cases=holdout,
                        config=objective_cfg,
                        propose=propose,
                        decide=decide,
                        drift_weight=cfg.drift_weight,
                        rng=rng,
                        skill_dir=root,
                        workers=cfg.concurrency,
                    )
                    ran_generation = True
            except Exception as exc:
                LOG.warning("Lab generation failed for %s: %s", tab, exc)
                bucket.setdefault("errors", []).append(str(exc))
                save_state(root, state)
                if guard.aborted:
                    break
                continue
            if not ran_generation:
                state["tokens"] = budget.tokens
                save_state(root, state)
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
            state["tokens"] = budget.tokens
            save_state(root, state)
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
        if guard.aborted or not progressed:
            break

    if not finalize:
        state["tokens"] = budget.tokens
        save_state(root, state)
        return {
            "night_id": night,
            "finished": False,
            "orders_touched": False,
            "tokens": budget.tokens,
            "stopped_early": bool(guard.aborted),
            "stop_reason": guard.reason if guard.aborted else "",
        }

    report = finalize_night(root, state, config=cfg, objective=objective_cfg, promote=promote, budget=budget)
    report["stopped_early"] = bool(guard.aborted)
    report["stop_reason"] = guard.reason if guard.aborted else ""
    state["finished"] = True
    state["tokens"] = budget.tokens
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
            )
            second = _model_call(
                skill_dir,
                config,
                system,
                (texts.get("round2") or "") + "\n" + first,
                budget,
                sleeper,
                guard,
            )
            return parse_decision(second or first, tab="debate", quality_threshold=60.0, prompt_key="round2")
        key = next(iter(texts))
        raw = _model_call(skill_dir, config, texts[key], json.dumps(payload), budget, sleeper, guard)
        return parse_decision(raw, tab=tab, quality_threshold=60.0, prompt_key=key)

    return propose, decide


def _record_model_stop(bucket: dict[str, Any], guard: ModelFailureGuard) -> None:
    reason = guard.reason or "model_failure"
    errors = bucket.setdefault("errors", [])
    if reason not in errors:
        errors.append(reason)


def _model_call(
    skill_dir: Path,
    config: LabConfig,
    system: str,
    user: str,
    budget: Budget,
    sleeper: Callable[[float], None] | None,
    guard: ModelFailureGuard | None = None,
) -> str:
    if budget.exhausted or (guard is not None and guard.aborted):
        return ""

    def _once() -> str:
        import config as skill_config
        from engine_analysis import _call_llm

        # Copy: config._load_env caches the parsed dict, and the lab may set the model.
        env = dict(skill_config._load_env(skill_dir))
        if config.model:
            env["LLM_MODEL_NAME"] = config.model
        return _call_llm(user, system, env, max_tokens=700)

    failed = False

    def _on_failure(exc: Exception) -> None:
        nonlocal failed
        failed = True
        if guard is not None:
            guard.note_failure(exc)
        else:
            LOG.warning("Lab model call failed: %s", exc)

    text = call_with_backoff(_once, sleeper=sleeper, on_failure=_on_failure)
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
        "note": "Shadow only. Live prompts change later, through the governor, after live samples.",
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
