from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

import numpy as np
import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))
DEFAULT_OUTCOMES_PATH = SKILL_DIR / ".trade_outcomes.json"

PnlField = Literal[
    "pnl",
    "net_pnl",
    "pnl_usd",
    "realized_pnl",
    "net_return",
    "return_pct",
    "pnl_pct",
]
FeatureField = Literal["mirofish_conviction", "advisory_prob", "agent_uncertainty"]


class TradeOutcome(TypedDict, total=False):
    order_id: str
    ticker: str
    date: str
    side: str
    qty: int
    fill_price: float
    mirofish_conviction: float
    advisory_prob: float
    agent_uncertainty: float
    pnl: float
    net_pnl: float
    pnl_usd: float
    realized_pnl: float
    net_return: float
    return_pct: float
    pnl_pct: float


MIROFISH_BINS = [-np.inf, 50.0, 60.0, 70.0, 80.0, 90.0, 101.0]
MIROFISH_LABELS = ["<50", "50-60", "60-70", "70-80", "80-90", "90-100"]

ADVISORY_BINS = [-np.inf, 0.4, 0.5, 0.6, 0.7, np.inf]
ADVISORY_LABELS = ["<0.4", "0.4-0.5", "0.5-0.6", "0.6-0.7", ">0.7"]

UNCERTAINTY_BINS = [0.0, 0.33, 0.66, 1.000001]
UNCERTAINTY_LABELS = ["Low", "Medium", "High"]

PNL_CANDIDATE_FIELDS: tuple[PnlField, ...] = (
    "pnl",
    "net_pnl",
    "pnl_usd",
    "realized_pnl",
    "net_return",
    "return_pct",
    "pnl_pct",
)

FEATURE_CANDIDATES: dict[FeatureField, tuple[str, ...]] = {
    "mirofish_conviction": ("mirofish_conviction",),
    "advisory_prob": ("advisory_prob", "advisory_probability", "p_up_10d"),
    "agent_uncertainty": ("agent_uncertainty", "uncertainty"),
}


def _load_outcomes_from_json(path: Path) -> list[TradeOutcome]:
    if not path.exists():
        raise FileNotFoundError(f"Outcomes file not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected list payload in {path}, got {type(payload).__name__}")
    return cast(list[TradeOutcome], payload)


def _load_outcomes_from_db() -> list[TradeOutcome]:
    """
    Stub for ORM-backed loading.

    Replace this with your own table/query once your normalized outcomes model
    is available in `webapp/models.py`. Example shape:

    ```python
    from sqlalchemy import select
    from webapp.db import SessionLocal
    from webapp.models import TradeOutcome  # TODO: create/import your model

    with SessionLocal() as session:
        rows = session.execute(
            select(
                TradeOutcome.ticker,
                TradeOutcome.pnl,
                TradeOutcome.mirofish_conviction,
                TradeOutcome.advisory_prob,
                TradeOutcome.agent_uncertainty,
            )
        ).all()
    return [dict(row._mapping) for row in rows]
    ```
    """
    raise NotImplementedError("DB source is a stub. Use --source json for now.")


def load_trade_outcomes(
    source: Literal["json", "db"] = "json",
    outcomes_path: Path = DEFAULT_OUTCOMES_PATH,
) -> list[TradeOutcome]:
    if source == "json":
        return _load_outcomes_from_json(outcomes_path)
    return _load_outcomes_from_db()


def _first_available_numeric(df: pd.DataFrame, candidates: tuple[str, ...]) -> pd.Series:
    for column in candidates:
        if column in df.columns:
            return pd.to_numeric(df[column], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype="float64")


def _build_round_trip_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"ticker", "side", "fill_price"}
    if not required.issubset(set(frame.columns)):
        return pd.DataFrame()

    working = frame.copy()
    working["ticker"] = working["ticker"].astype(str).str.upper().str.strip()
    working["side"] = working["side"].astype(str).str.upper().str.strip()
    working["fill_price"] = pd.to_numeric(working["fill_price"], errors="coerce")

    if "qty" in working.columns:
        qty_series = pd.to_numeric(working["qty"], errors="coerce")
    else:
        qty_series = pd.Series(1.0, index=working.index, dtype="float64")
    working["qty"] = qty_series.fillna(1.0).clip(lower=1.0)

    if "date" in working.columns:
        working["date"] = pd.to_datetime(working["date"], errors="coerce")
    else:
        working["date"] = pd.NaT

    working = working.reset_index(drop=True)
    working["_row_order"] = np.arange(len(working))
    working = working.sort_values(by=["date", "_row_order"], kind="stable")

    buy_queues: dict[str, list[dict[str, float]]] = {}
    closed_rows: list[dict[str, float]] = []

    for row in working.itertuples(index=False):
        ticker = cast(str, getattr(row, "ticker"))
        side = cast(str, getattr(row, "side"))
        fill_price = cast(float, getattr(row, "fill_price"))
        qty = float(cast(float, getattr(row, "qty")))

        if not ticker or pd.isna(fill_price):
            continue

        if side == "BUY":
            buy_queues.setdefault(ticker, []).append(
                {
                    "qty": qty,
                    "fill_price": float(fill_price),
                    "mirofish_conviction": float(
                        pd.to_numeric(
                            getattr(row, "mirofish_conviction", np.nan),
                            errors="coerce",
                        )
                    ),
                    "advisory_prob": float(
                        pd.to_numeric(
                            getattr(row, "advisory_prob", np.nan),
                            errors="coerce",
                        )
                    ),
                    "agent_uncertainty": float(
                        pd.to_numeric(
                            getattr(row, "agent_uncertainty", np.nan),
                            errors="coerce",
                        )
                    ),
                }
            )
            continue

        if side != "SELL":
            continue

        queue = buy_queues.get(ticker, [])
        qty_left = qty
        while qty_left > 0 and queue:
            lot = queue[0]
            matched_qty = min(qty_left, lot["qty"])
            if matched_qty <= 0:
                break

            pnl = (float(fill_price) - lot["fill_price"]) * matched_qty
            closed_rows.append(
                {
                    "pnl": pnl,
                    "mirofish_conviction": lot["mirofish_conviction"],
                    "advisory_prob": lot["advisory_prob"],
                    "agent_uncertainty": lot["agent_uncertainty"],
                }
            )

            lot["qty"] -= matched_qty
            qty_left -= matched_qty
            if lot["qty"] <= 0:
                queue.pop(0)

    return pd.DataFrame.from_records(closed_rows)


def _latest_close_by_ticker(ticker: str) -> float | None:
    try:
        from market_data import get_daily_history

        history = get_daily_history(ticker=ticker, days=30, skill_dir=SKILL_DIR)
        if history.empty or "close" not in history.columns:
            return None
        value = pd.to_numeric(history["close"], errors="coerce").dropna()
        if value.empty:
            return None
        px = float(value.iloc[-1])
        return px if px > 0 else None
    except Exception:
        return None


def _latest_close_with_debug(ticker: str, debug: bool) -> float | None:
    try:
        from market_data import get_daily_history_with_meta

        history, meta = get_daily_history_with_meta(
            ticker=ticker,
            days=30,
            skill_dir=SKILL_DIR,
        )
        if history.empty or "close" not in history.columns:
            if debug:
                print(
                    f"[pricing] {ticker}: no close data "
                    f"(provider={meta.get('provider')}, "
                    f"fallback={meta.get('used_fallback')}, "
                    f"reason={meta.get('fallback_reason')})"
                )
            return None
        values = pd.to_numeric(history["close"], errors="coerce").dropna()
        if values.empty:
            if debug:
                print(f"[pricing] {ticker}: close series empty after numeric coercion")
            return None
        px = float(values.iloc[-1])
        if px <= 0:
            if debug:
                print(f"[pricing] {ticker}: non-positive close price={px}")
            return None
        if debug:
            print(
                f"[pricing] {ticker}: close={px:.4f} "
                f"(provider={meta.get('provider')}, "
                f"fallback={meta.get('used_fallback')}, "
                f"reason={meta.get('fallback_reason')})"
            )
        return px
    except Exception as exc:
        if debug:
            print(f"[pricing] {ticker}: exception during lookup ({type(exc).__name__}: {exc})")
    return _latest_close_by_ticker(ticker)


def _build_mark_to_market_frame(
    frame: pd.DataFrame,
    *,
    lookup_live_prices: bool,
    debug_pricing: bool,
) -> tuple[pd.DataFrame, bool]:
    required = {"ticker", "side", "fill_price"}
    if not required.issubset(set(frame.columns)):
        return pd.DataFrame(), False

    working = frame.copy()
    working["ticker"] = working["ticker"].astype(str).str.upper().str.strip()
    working["side"] = working["side"].astype(str).str.upper().str.strip()
    working["fill_price"] = pd.to_numeric(working["fill_price"], errors="coerce")

    if "qty" in working.columns:
        qty_series = pd.to_numeric(working["qty"], errors="coerce")
    else:
        qty_series = pd.Series(1.0, index=working.index, dtype="float64")
    working["qty"] = qty_series.fillna(1.0).clip(lower=1.0)

    if "date" in working.columns:
        working["date"] = pd.to_datetime(working["date"], errors="coerce")
    else:
        working["date"] = pd.NaT

    working = working.reset_index(drop=True)
    working["_row_order"] = np.arange(len(working))
    working = working.sort_values(by=["date", "_row_order"], kind="stable")

    buy_queues: dict[str, list[dict[str, float]]] = {}
    for row in working.itertuples(index=False):
        ticker = cast(str, getattr(row, "ticker"))
        side = cast(str, getattr(row, "side"))
        qty = float(cast(float, getattr(row, "qty")))
        fill_price_raw = getattr(row, "fill_price")

        if not ticker:
            continue

        if side == "BUY":
            if pd.isna(fill_price_raw):
                continue
            fill_price = float(cast(float, fill_price_raw))
            buy_queues.setdefault(ticker, []).append(
                {
                    "qty": qty,
                    "fill_price": fill_price,
                    "mirofish_conviction": float(
                        pd.to_numeric(
                            getattr(row, "mirofish_conviction", np.nan),
                            errors="coerce",
                        )
                    ),
                    "advisory_prob": float(
                        pd.to_numeric(
                            getattr(row, "advisory_prob", np.nan),
                            errors="coerce",
                        )
                    ),
                    "agent_uncertainty": float(
                        pd.to_numeric(
                            getattr(row, "agent_uncertainty", np.nan),
                            errors="coerce",
                        )
                    ),
                }
            )
            continue

        if side != "SELL":
            continue

        queue = buy_queues.get(ticker, [])
        qty_left = qty
        while qty_left > 0 and queue:
            lot = queue[0]
            matched_qty = min(qty_left, lot["qty"])
            if matched_qty <= 0:
                break
            lot["qty"] -= matched_qty
            qty_left -= matched_qty
            if lot["qty"] <= 0:
                queue.pop(0)

    close_cache: dict[str, float | None] = {}
    mtm_rows: list[dict[str, float]] = []
    used_entry_proxy = False
    for ticker, lots in buy_queues.items():
        if lookup_live_prices:
            if ticker not in close_cache:
                close_cache[ticker] = _latest_close_with_debug(
                    ticker=ticker,
                    debug=debug_pricing,
                )
            last_close = close_cache[ticker]
        else:
            last_close = None

        for lot in lots:
            if lot["qty"] <= 0:
                continue
            mark_price = last_close if last_close is not None else lot["fill_price"]
            if last_close is None:
                used_entry_proxy = True
            pnl = (mark_price - lot["fill_price"]) * lot["qty"]
            mtm_rows.append(
                {
                    "pnl": pnl,
                    "mirofish_conviction": lot["mirofish_conviction"],
                    "advisory_prob": lot["advisory_prob"],
                    "agent_uncertainty": lot["agent_uncertainty"],
                }
            )

    return pd.DataFrame.from_records(mtm_rows), used_entry_proxy


def normalize_records(
    records: list[TradeOutcome],
    *,
    allow_unrealized_fallback: bool,
    lookup_live_prices: bool,
    debug_pricing: bool,
) -> tuple[pd.DataFrame, str]:
    if not records:
        return pd.DataFrame(), "empty"

    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return frame, "empty"

    normalized = pd.DataFrame(index=frame.index)
    normalized["pnl"] = _first_available_numeric(frame, PNL_CANDIDATE_FIELDS)
    for feature_name, aliases in FEATURE_CANDIDATES.items():
        normalized[feature_name] = _first_available_numeric(frame, aliases)

    if normalized["pnl"].notna().sum() > 0:
        return normalized, "realized_direct"

    if normalized["pnl"].notna().sum() == 0:
        derived = _build_round_trip_frame(frame)
        if not derived.empty:
            return derived, "realized_round_trip"

        if allow_unrealized_fallback:
            mtm, used_entry_proxy = _build_mark_to_market_frame(
                frame,
                lookup_live_prices=lookup_live_prices,
                debug_pricing=debug_pricing,
            )
            if not mtm.empty:
                if used_entry_proxy:
                    return mtm, "unrealized_entry_proxy"
                return mtm, "unrealized_mark_to_market"

        candidates = ", ".join(PNL_CANDIDATE_FIELDS)
        raise ValueError(
            "No usable PnL column found and no closed BUY/SELL round trips could "
            f"be derived. Checked direct fields: {candidates}"
        )
    return normalized, "realized_direct"


def _bucketize(df: pd.DataFrame) -> pd.DataFrame:
    bucketed = df.copy()

    bucketed["mirofish_bucket"] = pd.cut(
        bucketed["mirofish_conviction"],
        bins=MIROFISH_BINS,
        labels=MIROFISH_LABELS,
        right=False,
        include_lowest=True,
    )
    bucketed["advisory_bucket"] = pd.cut(
        bucketed["advisory_prob"],
        bins=ADVISORY_BINS,
        labels=ADVISORY_LABELS,
        right=False,
        include_lowest=True,
    )
    bucketed["uncertainty_bucket"] = pd.cut(
        bucketed["agent_uncertainty"],
        bins=UNCERTAINTY_BINS,
        labels=UNCERTAINTY_LABELS,
        right=False,
        include_lowest=True,
    )
    return bucketed


def _cohort_metrics(
    df: pd.DataFrame,
    cohort_column: str,
    cohort_labels: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for label in cohort_labels:
        subset = df.loc[df[cohort_column] == label, "pnl"].dropna()
        n = int(subset.shape[0])
        if n == 0:
            rows.append(
                {
                    "bucket": label,
                    "trades": 0,
                    "win_rate_pct": np.nan,
                    "avg_pnl": np.nan,
                    "total_pnl": 0.0,
                    "expectancy": np.nan,
                }
            )
            continue

        wins = subset[subset > 0]
        losses = subset[subset < 0]

        win_rate = float((subset > 0).mean())
        loss_rate = 1.0 - win_rate
        avg_win = float(wins.mean()) if not wins.empty else 0.0
        avg_loss = float(losses.mean()) if not losses.empty else 0.0
        expectancy = (win_rate * avg_win) - (loss_rate * abs(avg_loss))

        rows.append(
            {
                "bucket": label,
                "trades": n,
                "win_rate_pct": win_rate * 100.0,
                "avg_pnl": float(subset.mean()),
                "total_pnl": float(subset.sum()),
                "expectancy": float(expectancy),
            }
        )

    result = pd.DataFrame(rows).set_index("bucket")
    return result.round(4)


def _print_report(name: str, report: pd.DataFrame) -> None:
    print(f"\n=== {name} ===")
    if report.empty:
        print("No rows available for this cohort.")
        return
    print(report.to_string())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cohort analysis for momentum trade outcomes.",
    )
    parser.add_argument(
        "--source",
        choices=("json", "db"),
        default="json",
        help="Data source for outcomes.",
    )
    parser.add_argument(
        "--outcomes-path",
        type=Path,
        default=DEFAULT_OUTCOMES_PATH,
        help="Path to outcomes JSON list.",
    )
    parser.add_argument(
        "--allow-unrealized-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow mark-to-market fallback for open positions when realized PnL is unavailable.",
    )
    parser.add_argument(
        "--lookup-live-prices",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Try live/daily market price lookup for unrealized fallback. "
            "Default false uses entry-price proxy for fast deterministic output."
        ),
    )
    parser.add_argument(
        "--debug-pricing",
        action="store_true",
        help="Print per-ticker pricing lookup diagnostics for unrealized fallback.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        records = load_trade_outcomes(source=args.source, outcomes_path=args.outcomes_path)
        base_df, source_kind = normalize_records(
            records,
            allow_unrealized_fallback=args.allow_unrealized_fallback,
            lookup_live_prices=args.lookup_live_prices,
            debug_pricing=args.debug_pricing,
        )
    except (FileNotFoundError, ValueError, NotImplementedError, json.JSONDecodeError) as exc:
        print(f"Error loading outcomes: {exc}")
        return 1

    if base_df.empty:
        print("No trade outcomes found.")
        return 0

    bucketed = _bucketize(base_df)

    mirofish_report = _cohort_metrics(bucketed, "mirofish_bucket", MIROFISH_LABELS)
    advisory_report = _cohort_metrics(bucketed, "advisory_bucket", ADVISORY_LABELS)
    uncertainty_report = _cohort_metrics(
        bucketed,
        "uncertainty_bucket",
        UNCERTAINTY_LABELS,
    )

    _print_report("MiroFish Conviction Cohorts", mirofish_report)
    _print_report("Advisory Probability Cohorts", advisory_report)
    _print_report("Agent Uncertainty Cohorts", uncertainty_report)

    if source_kind == "unrealized_mark_to_market":
        print(
            "\nNote: using mark-to-market fallback from open BUY lots "
            "(provisional, not realized outcomes)."
        )
    elif source_kind == "unrealized_entry_proxy":
        print(
            "\nNote: using unrealized fallback with entry-price proxy for open BUY lots "
            "(PnL defaults to 0 when live price is unavailable/disabled)."
        )

    missing_features = [
        name for name in FEATURE_CANDIDATES if bucketed[name].notna().sum() == 0
    ]
    if missing_features:
        print(
            "\nWarning: missing usable feature values for "
            + ", ".join(missing_features)
            + ". Corresponding buckets may be empty."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
