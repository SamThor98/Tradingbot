"""Rebuild lab cases from saved backtest chunks. Analysis only.

The chunk files were produced by the live Stage 2 and VCP rules. This module
reads a whitelist of entry fields, refills the point-in-time card from bars
cut at the entry date, and labels the 20-trading-day scale-out from those
bars. Outcome fields and model scores in the chunk are never read.

Fetching history hits Schwab. The script refuses that unless ``--fetch`` is set.
"""

from __future__ import annotations

import json
import pickle
from collections import defaultdict
from collections.abc import Iterator, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.tab_agent_lab_plan import anonymize_card, era_for, is_pre_cutoff, lab_decision_card
from core.tab_agent_lab_score import LabCase

ENTRY_WHITELIST = ("ticker", "entry_date", "entry_price", "stop_pct", "sector_etf", "regime_bucket")
REBUILD_FILE = ".tab_agent_rebuild.jsonl"
BAR_CACHE = ".tab_agent_rebuild/bars.pkl"

_FORBIDDEN_EXACT = {
    "return",
    "net_return",
    "mfe",
    "mae",
    "ohlc_path",
    "p_up_calibrated",
    "ev_10d",
    "rank_score_v2",
    "reliability_score",
    "signal_score",
}


def forbidden_field(key: str) -> bool:
    name = str(key or "")
    if name in _FORBIDDEN_EXACT:
        return True
    if name.startswith("exit_"):
        return True
    if name.startswith("advisory_"):
        return True
    return False


def entry_from_trade(raw: Mapping[str, Any], *, era: str) -> dict[str, Any]:
    """Copy the whitelist only. Forbidden keys are not accessed."""
    entry: dict[str, Any] = {"era": str(era or "")}
    for key in ENTRY_WHITELIST:
        if key not in raw:
            continue
        if forbidden_field(key):
            raise KeyError(key)
        entry[key] = raw[key]
    ticker = str(entry.get("ticker") or "").upper().strip()
    entry["ticker"] = ticker
    entry["entry_date"] = str(entry.get("entry_date") or "")[:10]
    return entry


def iter_backtest_entries(chunk_root: Path | str) -> Iterator[dict[str, Any]]:
    """Yield whitelist entries from ``<era>/chunk_*.json`` trade lists."""
    root = Path(chunk_root)
    if not root.exists():
        return
    for era_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        for chunk_path in sorted(era_dir.glob("chunk_*.json")):
            if chunk_path.name.endswith("_tickers.json"):
                continue
            try:
                payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            trades = payload.get("trades") if isinstance(payload, dict) else None
            if not isinstance(trades, list):
                continue
            for raw in trades:
                if isinstance(raw, Mapping):
                    yield entry_from_trade(raw, era=era_dir.name)


def leakage_audit(chunk_root: Path | str) -> dict[str, Any]:
    """Count forbidden keys present in the files and confirm they were not copied."""
    root = Path(chunk_root)
    ignored: dict[str, int] = defaultdict(int)
    output_keys: set[str] = set()
    entries = 0
    for era_dir in sorted(path for path in root.iterdir() if path.is_dir()) if root.exists() else []:
        for chunk_path in sorted(era_dir.glob("chunk_*.json")):
            if chunk_path.name.endswith("_tickers.json"):
                continue
            try:
                payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            trades = payload.get("trades") if isinstance(payload, dict) else None
            if not isinstance(trades, list):
                continue
            for raw in trades:
                if not isinstance(raw, dict):
                    continue
                for key in raw:
                    if forbidden_field(str(key)):
                        ignored[str(key)] += 1
                entry = entry_from_trade(raw, era=era_dir.name)
                output_keys.update(entry)
                entries += 1
    leaked = sorted(key for key in output_keys if forbidden_field(key) or key not in set(ENTRY_WHITELIST) | {"era"})
    return {
        "entries": entries,
        "ignored_fields": dict(sorted(ignored.items())),
        "forbidden_in_output": leaked,
        "whitelist": list(ENTRY_WHITELIST),
    }


def _week_key(as_of: str) -> str:
    try:
        day = date.fromisoformat(str(as_of)[:10])
    except ValueError:
        return "unknown"
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_era: dict[str, int] = defaultdict(int)
    by_week: dict[str, int] = defaultdict(int)
    for entry in entries:
        by_era[str(entry.get("era") or "unknown")] += 1
        by_week[_week_key(str(entry.get("entry_date") or ""))] += 1
    return {
        "entries": len(entries),
        "by_era": dict(sorted(by_era.items())),
        "by_week": dict(sorted(by_week.items())),
    }


def _unfilled_without_bars(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Field groups still empty when the only inputs are the whitelist."""
    from core.tab_agent_replay import missing_pit_fields

    counts: dict[str, int] = defaultdict(int)
    for entry in entries:
        signal = {
            "ticker": entry.get("ticker"),
            "price": entry.get("entry_price"),
            "entry_price": entry.get("entry_price"),
            "stop_pct": entry.get("stop_pct"),
            "sector_etf": entry.get("sector_etf"),
            "regime_bucket": entry.get("regime_bucket"),
        }
        for name in missing_pit_fields(signal):
            counts[name] += 1
    return {
        "bars_loaded": False,
        "note": "Dry-run does not fetch bars. Technical groups stay empty until --fetch.",
        "unfilled_field_counts": dict(sorted(counts.items())),
        "entries": len(entries),
    }


def dry_run_report(chunk_root: Path | str) -> dict[str, Any]:
    """Counts, unfilled groups, and a leakage audit. No market-data calls."""
    entries = list(iter_backtest_entries(chunk_root))
    audit = leakage_audit(chunk_root)
    return {
        "ok": not audit["forbidden_in_output"],
        "dry_run": True,
        "fetched": False,
        "orders_touched": False,
        "chunk_root": str(chunk_root),
        **summarize_entries(entries),
        "unfilled": _unfilled_without_bars(entries),
        "leakage": audit,
    }


def card_from_bars(
    entry: Mapping[str, Any],
    frame: Any,
    skill_dir: Path | None,
    *,
    benchmarks: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Fill the shared card from bars on or before the entry. Recompute the score."""
    from core.tab_agent_replay import _bars_on_or_before, fill_case_from_history
    from stage_analysis import compute_signal_components

    as_of = str(entry.get("entry_date") or "")[:10]
    created = datetime.fromisoformat(as_of).replace(tzinfo=timezone.utc)
    case: dict[str, Any] = {
        "ticker": str(entry.get("ticker") or "").upper(),
        "as_of": as_of,
        "created_at": created,
        "entry_price": entry.get("entry_price"),
        "stop_pct": entry.get("stop_pct"),
        "signal": {
            "ticker": str(entry.get("ticker") or "").upper(),
            "price": entry.get("entry_price"),
            "entry_price": entry.get("entry_price"),
            "stop_pct": entry.get("stop_pct"),
            "sector_etf": entry.get("sector_etf"),
            "regime_bucket": entry.get("regime_bucket"),
        },
    }
    fill_case_from_history(case, frame, skill_dir, benchmarks)
    prefix = _bars_on_or_before(frame, created)
    if prefix is not None:
        try:
            components = compute_signal_components(prefix, skill_dir=skill_dir)
        except Exception:
            components = {}
        if isinstance(components, dict):
            case["signal"]["signal_score"] = components.get("score")
            if components.get("pct_from_52w_high") is not None:
                case["signal"]["pct_from_52w_high"] = components.get("pct_from_52w_high")
    return lab_decision_card(case["signal"], as_of)


def build_rebuild_cases(
    entries: list[Mapping[str, Any]],
    history_provider: Callable[[str], Any],
    skill_dir: Path | None,
    *,
    benchmarks: Callable[[str], Any] | None = None,
    now: datetime | None = None,
) -> list[LabCase]:
    """Label each whitelist entry. The payload is anonymized and tagged rebuild."""
    from core.tab_agent_replay import scale_out_return

    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    cases: list[LabCase] = []
    for entry in entries:
        ticker = str(entry.get("ticker") or "").upper().strip()
        as_of = str(entry.get("entry_date") or "")[:10]
        if not ticker or len(as_of) < 10:
            continue
        try:
            frame = history_provider(ticker)
        except Exception:
            continue
        created = datetime.fromisoformat(as_of).replace(tzinfo=timezone.utc)
        raw_case = {
            "ticker": ticker,
            "as_of": as_of,
            "created_at": created,
            "entry_price": entry.get("entry_price"),
            "stop_pct": entry.get("stop_pct"),
            "signal": {},
        }
        labeled = scale_out_return(raw_case, frame, skill_dir, now=clock)
        if labeled is None:
            continue
        card = card_from_bars(entry, frame, skill_dir, benchmarks=benchmarks)
        anon = anonymize_card(card, case_id=f"{ticker}:{as_of}")
        score = card.get("signal_score")
        try:
            score_value = None if score is None else float(score)
        except (TypeError, ValueError):
            score_value = None
        volume = card.get("avg_dollar_volume")
        try:
            volume_value = None if volume is None else float(volume)
        except (TypeError, ValueError):
            volume_value = None
        spread = card.get("spread_pct")
        try:
            spread_value = None if spread is None else float(spread)
        except (TypeError, ValueError):
            spread_value = None
        cases.append(
            LabCase(
                case_id=f"{ticker}:{as_of}",
                as_of=as_of,
                ticker=ticker,
                long_return_pct=float(labeled["realized_return_pct"]),
                signal_score=score_value,
                avg_dollar_volume=volume_value,
                spread_pct=spread_value,
                source="rebuild",
                payload=anon,
                pre_cutoff=is_pre_cutoff(as_of),
                era=era_for(as_of, str(entry.get("era") or "")),
            )
        )
    return cases


def load_rebuild_cases(skill_dir: Path | str) -> list[LabCase]:
    path = Path(skill_dir) / REBUILD_FILE
    if not path.exists():
        return []
    rows: list[LabCase] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else None
        try:
            ret = float(raw.get("long_return_pct"))
        except (TypeError, ValueError):
            continue
        ticker = str(raw.get("ticker") or "").upper()
        as_of = str(raw.get("as_of") or "")[:10]
        if not ticker or len(as_of) < 10:
            continue
        rows.append(
            LabCase(
                case_id=str(raw.get("case_id") or f"{ticker}:{as_of}"),
                as_of=as_of,
                ticker=ticker,
                long_return_pct=ret,
                signal_score=_float_or_none(raw.get("signal_score")),
                avg_dollar_volume=_float_or_none(raw.get("avg_dollar_volume")),
                spread_pct=_float_or_none(raw.get("spread_pct")),
                source="rebuild",
                payload=payload,
                pre_cutoff=bool(raw.get("pre_cutoff")),
                era=str(raw.get("era") or ""),
                cohort_return_pct=_float_or_none(raw.get("cohort_return_pct")),
            )
        )
    return rows


def write_rebuild_cases(skill_dir: Path | str, cases: list[LabCase]) -> Path:
    path = Path(skill_dir) / REBUILD_FILE
    lines = []
    for case in cases:
        lines.append(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "as_of": case.as_of,
                    "ticker": case.ticker,
                    "long_return_pct": case.long_return_pct,
                    "signal_score": case.signal_score,
                    "avg_dollar_volume": case.avg_dollar_volume,
                    "spread_pct": case.spread_pct,
                    "source": "rebuild",
                    "pre_cutoff": case.pre_cutoff,
                    "era": case.era,
                    "payload": case.payload,
                },
                default=str,
            )
        )
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class BarCache:
    """One in-memory map, flushed to a single file. Each symbol is fetched once."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.frames: dict[str, Any] = {}
        if path is not None and path.exists():
            try:
                with path.open("rb") as handle:
                    loaded = pickle.load(handle)
                if isinstance(loaded, dict):
                    self.frames = loaded
            except (OSError, pickle.UnpicklingError, EOFError):
                self.frames = {}

    def provider(
        self,
        fetch: Callable[..., Any],
        *,
        start_date: date | str,
        end_date: date | str,
    ) -> Callable[[str], Any]:
        def _get(ticker: str) -> Any:
            key = str(ticker or "").upper().strip()
            if key not in self.frames:
                frame, _meta = fetch(key, start_date=start_date, end_date=end_date)
                self.frames[key] = frame
            return self.frames[key]

        return _get

    def flush(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("wb") as handle:
            pickle.dump(self.frames, handle, protocol=4)


def schwab_history_fetch(skill_dir: Path | None) -> Callable[..., Any]:
    """Date-ranged daily bars. The default 800-day provider is not used."""
    from market_data import get_daily_history_with_meta

    def _fetch(ticker: str, *, start_date: date | str, end_date: date | str) -> Any:
        return get_daily_history_with_meta(
            ticker,
            skill_dir=skill_dir,
            start_date=start_date,
            end_date=end_date,
        )

    return _fetch
