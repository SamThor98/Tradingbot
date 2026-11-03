"""Storage for Liquid positioning snapshots (``liquid_snapshots`` table).

Mirrors the feature_store pattern: reuse the webapp DB engine when
available (SQLite local / Postgres SaaS), else a standalone engine.
Rows are append-only point-in-time observations, safe for as-of joins.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from core.liquid_positioning.schema import LiquidSnapshot

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parents[2]

Base = declarative_base()


class LiquidSnapshotRecord(Base):  # type: ignore[valid-type,misc]
    __tablename__ = "liquid_snapshots"

    id = Column(String(40), primary_key=True, default=lambda: uuid.uuid4().hex[:16])
    ticker = Column(String(24), nullable=False, index=True)
    source_coin = Column(String(32), nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False, index=True)
    mark_px = Column(Float, nullable=True)
    funding_rate = Column(Float, nullable=True)
    open_interest_coin = Column(Float, nullable=True)
    open_interest_usd = Column(Float, nullable=True)
    day_volume_usd = Column(Float, nullable=True)
    premium = Column(Float, nullable=True)
    max_leverage = Column(Integer, nullable=True)
    is_thin = Column(Integer, nullable=False, default=0)


def _get_session(skill_dir: Path | None = None) -> Session:
    try:
        from webapp.db import SessionLocal

        return SessionLocal()
    except Exception:
        pass
    import os

    db_url = os.getenv("DATABASE_URL", f"sqlite:///{(skill_dir or SKILL_DIR) / 'webapp' / 'webapp.db'}")
    if db_url.startswith("postgres://"):
        db_url = "postgresql+psycopg2://" + db_url[len("postgres://"):]
    kwargs: dict[str, Any] = {}
    if db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = create_engine(db_url, **kwargs)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng)()


def ensure_table(skill_dir: Path | None = None) -> None:
    try:
        from webapp.db import engine

        Base.metadata.create_all(bind=engine)
    except Exception:
        _get_session(skill_dir).close()


def store_snapshots(snapshots: list[LiquidSnapshot], skill_dir: Path | None = None) -> int:
    if not snapshots:
        return 0
    db = _get_session(skill_dir)
    try:
        Base.metadata.create_all(bind=db.get_bind())
        for s in snapshots:
            db.add(
                LiquidSnapshotRecord(
                    ticker=s.ticker,
                    source_coin=s.source_coin,
                    ts=s.ts,
                    mark_px=s.mark_px,
                    funding_rate=s.funding_rate,
                    open_interest_coin=s.open_interest_coin,
                    open_interest_usd=s.open_interest_usd,
                    day_volume_usd=s.day_volume_usd,
                    premium=s.premium,
                    max_leverage=s.max_leverage,
                    is_thin=1 if s.is_thin else 0,
                )
            )
        db.commit()
        return len(snapshots)
    except Exception as e:
        db.rollback()
        LOG.warning("Liquid snapshot store failed: %s", e)
        return 0
    finally:
        db.close()


class LiquidFundingDailyRecord(Base):  # type: ignore[valid-type,misc]
    """Daily aggregate of hourly funding history (backfilled, point-in-time)."""

    __tablename__ = "liquid_funding_daily"

    id = Column(String(40), primary_key=True, default=lambda: uuid.uuid4().hex[:16])
    ticker = Column(String(24), nullable=False, index=True)
    date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD (UTC)
    funding_mean = Column(Float, nullable=True)
    funding_min = Column(Float, nullable=True)
    funding_max = Column(Float, nullable=True)
    funding_last = Column(Float, nullable=True)
    n_hours = Column(Integer, nullable=False, default=0)


def store_funding_daily(ticker: str, daily_rows: list[dict[str, Any]], skill_dir: Path | None = None) -> int:
    """Upsert daily funding aggregates for one ticker. Returns rows written."""
    if not daily_rows:
        return 0
    db = _get_session(skill_dir)
    try:
        Base.metadata.create_all(bind=db.get_bind())
        existing = {
            r.date: r
            for r in db.query(LiquidFundingDailyRecord)
            .filter(LiquidFundingDailyRecord.ticker == ticker.upper())
            .all()
        }
        written = 0
        for row in daily_rows:
            rec = existing.get(row["date"])
            if rec is None:
                rec = LiquidFundingDailyRecord(ticker=ticker.upper(), date=row["date"])
                db.add(rec)
            rec.funding_mean = row.get("funding_mean")
            rec.funding_min = row.get("funding_min")
            rec.funding_max = row.get("funding_max")
            rec.funding_last = row.get("funding_last")
            rec.n_hours = int(row.get("n_hours") or 0)
            written += 1
        db.commit()
        return written
    except Exception as e:
        db.rollback()
        LOG.warning("Liquid funding daily store failed for %s: %s", ticker, e)
        return 0
    finally:
        db.close()


def get_funding_daily(ticker: str, days: int = 365, skill_dir: Path | None = None) -> list[dict[str, Any]]:
    db = _get_session(skill_dir)
    try:
        Base.metadata.create_all(bind=db.get_bind())
        rows = (
            db.query(LiquidFundingDailyRecord)
            .filter(LiquidFundingDailyRecord.ticker == ticker.upper())
            .order_by(LiquidFundingDailyRecord.date.desc())
            .limit(max(1, days))
            .all()
        )
        out = [
            {
                "date": r.date,
                "funding_mean": r.funding_mean,
                "funding_min": r.funding_min,
                "funding_max": r.funding_max,
                "funding_last": r.funding_last,
                "n_hours": r.n_hours,
            }
            for r in rows
        ]
        out.reverse()
        return out
    finally:
        db.close()


def get_latest_snapshots(skill_dir: Path | None = None) -> list[dict[str, Any]]:
    """Most recent snapshot per ticker, newest OI first."""
    db = _get_session(skill_dir)
    try:
        Base.metadata.create_all(bind=db.get_bind())
        rows = (
            db.query(LiquidSnapshotRecord)
            .order_by(LiquidSnapshotRecord.ts.desc())
            .limit(5000)
            .all()
        )
        seen: dict[str, LiquidSnapshotRecord] = {}
        for r in rows:
            seen.setdefault(r.ticker, r)
        out = [
            {
                "ticker": r.ticker,
                "source_coin": r.source_coin,
                "ts": r.ts.isoformat() if r.ts else None,
                "mark_px": r.mark_px,
                "funding_rate": r.funding_rate,
                "open_interest_usd": r.open_interest_usd,
                "day_volume_usd": r.day_volume_usd,
                "is_thin": bool(r.is_thin),
            }
            for r in seen.values()
        ]
        out.sort(key=lambda d: -(d["open_interest_usd"] or 0))
        return out
    finally:
        db.close()


def snapshot_coverage(skill_dir: Path | None = None) -> dict[str, Any]:
    db = _get_session(skill_dir)
    try:
        Base.metadata.create_all(bind=db.get_bind())
        total = db.query(LiquidSnapshotRecord).count()
        tickers = db.query(LiquidSnapshotRecord.ticker).distinct().count()
        latest = (
            db.query(LiquidSnapshotRecord.ts).order_by(LiquidSnapshotRecord.ts.desc()).first()
        )
        return {
            "rows": int(total),
            "tickers": int(tickers),
            "latest_ts": latest[0].isoformat() if latest and latest[0] else None,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        db.close()
