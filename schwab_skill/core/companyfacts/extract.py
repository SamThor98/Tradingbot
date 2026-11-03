"""Map SEC companyfacts XBRL into annual statements used by the DCF."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Duration concepts, tried in order. Values are USD.
REVENUE_CONCEPTS: tuple[str, ...] = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
OPERATING_CF_CONCEPTS: tuple[str, ...] = ("NetCashProvidedByUsedInOperatingActivities",)
CAPEX_CONCEPTS: tuple[str, ...] = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
)
FCF_CONCEPTS: tuple[str, ...] = ("FreeCashFlow",)
NET_INCOME_CONCEPTS: tuple[str, ...] = ("NetIncomeLoss",)

# Instant concepts (balance sheet / shares). Latest observation wins.
DEBT_TOTAL_CONCEPTS: tuple[str, ...] = (
    "LongTermDebt",
    "LongTermDebtAndCapitalLeaseObligations",
    "DebtInstrumentCarryingAmount",
)
DEBT_COMPONENT_CONCEPTS: tuple[tuple[str, ...], ...] = (
    ("LongTermDebtNoncurrent", "LongTermDebtNoncurrent"),
    ("LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"),
)
CASH_CONCEPTS: tuple[str, ...] = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "CashCashEquivalentsAndShortTermInvestments",
)
SHARES_CONCEPTS: tuple[str, ...] = (
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingDiluted",
)

_ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
_PERIOD_FORMS = _ANNUAL_FORMS | frozenset({"10-Q", "10-Q/A", "6-K", "8-K"})
_FY_FRAMES = frozenset({"FY", "Q4"})
_USD_UNIT_KEYS = ("USD", "usd")
_SHARE_UNIT_KEYS = ("shares", "pure", "number")


@dataclass(frozen=True, slots=True)
class FactPoint:
    end: str
    val: float
    form: str
    fp: str
    fy: int | None
    filed: str
    accn: str
    concept: str


@dataclass
class ExtractedStatements:
    ticker: str
    cik: str
    entity_name: str
    revenue: list[FactPoint] = field(default_factory=list)
    operating_cf: list[FactPoint] = field(default_factory=list)
    capex: list[FactPoint] = field(default_factory=list)
    fcf: list[FactPoint] = field(default_factory=list)
    net_income: list[FactPoint] = field(default_factory=list)
    cash: FactPoint | None = None
    debt: FactPoint | None = None
    shares: FactPoint | None = None
    errors: list[str] = field(default_factory=list)
    source_concepts: dict[str, str] = field(default_factory=dict)

    def last_fcf(self) -> float | None:
        if self.fcf:
            return float(self.fcf[-1].val)
        return None

    def net_debt(self) -> float:
        debt = float(self.debt.val) if self.debt is not None else 0.0
        cash = float(self.cash.val) if self.cash is not None else 0.0
        return debt - cash

    def as_of(self) -> str:
        ends = [p.end for p in self.fcf]
        if self.shares is not None:
            ends.append(self.shares.end)
        return max(ends) if ends else ""

    def filed(self) -> str:
        filed = [p.filed for p in self.fcf if p.filed]
        if self.shares is not None and self.shares.filed:
            filed.append(self.shares.filed)
        return max(filed) if filed else ""


def extract_statements(payload: dict[str, Any], *, ticker: str = "") -> ExtractedStatements:
    """Pure mapping: companyfacts JSON -> annual series. No I/O."""
    cik = str(payload.get("cik") or "").zfill(10) if payload.get("cik") is not None else ""
    entity = str(payload.get("entityName") or "")
    facts = payload.get("facts") if isinstance(payload.get("facts"), dict) else {}
    us_gaap = facts.get("us-gaap") if isinstance(facts.get("us-gaap"), dict) else {}
    dei = facts.get("dei") if isinstance(facts.get("dei"), dict) else {}
    namespaces = {"dei": dei, "us-gaap": us_gaap}

    out = ExtractedStatements(ticker=str(ticker or "").strip().upper(), cik=cik, entity_name=entity)

    rev, rev_c = _annual_series(namespaces, REVENUE_CONCEPTS)
    ocf, ocf_c = _annual_series(namespaces, OPERATING_CF_CONCEPTS)
    capex, capex_c = _annual_series(namespaces, CAPEX_CONCEPTS)
    tagged_fcf, fcf_c = _annual_series(namespaces, FCF_CONCEPTS)
    ni, ni_c = _annual_series(namespaces, NET_INCOME_CONCEPTS)

    out.revenue = rev
    out.operating_cf = ocf
    out.capex = capex
    out.net_income = ni
    if rev_c:
        out.source_concepts["revenue"] = rev_c
    if ocf_c:
        out.source_concepts["operating_cf"] = ocf_c
    if capex_c:
        out.source_concepts["capex"] = capex_c
    if ni_c:
        out.source_concepts["net_income"] = ni_c

    if tagged_fcf:
        out.fcf = tagged_fcf
        if fcf_c:
            out.source_concepts["fcf"] = fcf_c
    else:
        out.fcf = _derive_fcf(ocf, capex)
        if out.fcf:
            out.source_concepts["fcf"] = "operating_cf - capex"

    cash, cash_c = _latest_instant(namespaces, CASH_CONCEPTS, usd=True)
    shares, shares_c = _latest_instant(namespaces, SHARES_CONCEPTS, usd=False)
    debt, debt_c = _latest_debt(namespaces)

    out.cash = cash
    out.shares = shares
    out.debt = debt
    if cash_c:
        out.source_concepts["cash"] = cash_c
    if shares_c:
        out.source_concepts["shares"] = shares_c
    if debt_c:
        out.source_concepts["debt"] = debt_c

    if len(out.fcf) < 2:
        out.errors.append("insufficient_fcf_history")
    if out.last_fcf() is None or out.last_fcf() == 0.0:
        out.errors.append("fcf_unavailable")
    if out.shares is None or float(out.shares.val) <= 0:
        out.errors.append("shares_outstanding_unavailable")
    return out


def _gaap_or_dei(namespaces: dict[str, dict[str, Any]], concept: str) -> dict[str, Any] | None:
    for ns in ("dei", "us-gaap"):
        block = namespaces.get(ns) or {}
        item = block.get(concept)
        if isinstance(item, dict):
            return item
    return None


def _unit_rows(concept_payload: dict[str, Any], *, usd: bool) -> list[dict[str, Any]]:
    units = concept_payload.get("units")
    if not isinstance(units, dict):
        return []
    keys = _USD_UNIT_KEYS if usd else _SHARE_UNIT_KEYS
    for key in keys:
        rows = units.get(key)
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, dict)]
    if not usd:
        for key, rows in units.items():
            if str(key).lower() in {"usd", "usd/shares"}:
                continue
            if isinstance(rows, list) and rows:
                return [row for row in rows if isinstance(row, dict)]
    return []


def _parse_point(row: dict[str, Any], concept: str) -> FactPoint | None:
    try:
        val = float(row.get("val"))
    except (TypeError, ValueError):
        return None
    end = str(row.get("end") or "").strip()
    if not end:
        return None
    fy_raw = row.get("fy")
    fy: int | None
    try:
        fy = int(fy_raw) if fy_raw is not None and str(fy_raw).strip() != "" else None
    except (TypeError, ValueError):
        fy = None
    return FactPoint(
        end=end[:10],
        val=val,
        form=str(row.get("form") or "").strip(),
        fp=str(row.get("fp") or "").strip().upper(),
        fy=fy,
        filed=str(row.get("filed") or "").strip()[:10],
        accn=str(row.get("accn") or "").strip(),
        concept=concept,
    )


def _dedup_latest_filed(points: list[FactPoint]) -> list[FactPoint]:
    best: dict[tuple[str, str], FactPoint] = {}
    for pt in points:
        key = (pt.end, pt.form)
        prev = best.get(key)
        if prev is None or pt.filed >= prev.filed:
            best[key] = pt
    return sorted(best.values(), key=lambda p: (p.end, p.filed))


def _prefer_annual(points: list[FactPoint]) -> list[FactPoint]:
    """One observation per fiscal year-end, preferring 10-K FY frames."""
    if not points:
        return []
    annual = [p for p in points if p.form in _ANNUAL_FORMS and p.fp in _FY_FRAMES]
    pool = annual if len(annual) >= 2 else [p for p in points if p.form in _PERIOD_FORMS] or points
    by_year: dict[str, FactPoint] = {}
    for pt in pool:
        year = pt.end[:4]
        prev = by_year.get(year)
        annual_pt = pt.form in _ANNUAL_FORMS and pt.fp in _FY_FRAMES
        prev_annual = bool(prev and prev.form in _ANNUAL_FORMS and prev.fp in _FY_FRAMES)
        if prev is None:
            by_year[year] = pt
            continue
        if annual_pt and not prev_annual:
            by_year[year] = pt
            continue
        if annual_pt == prev_annual and (pt.end, pt.filed) >= (prev.end, prev.filed):
            by_year[year] = pt
    return [by_year[k] for k in sorted(by_year)]


def _annual_series(namespaces: dict[str, dict[str, Any]], concepts: tuple[str, ...]) -> tuple[list[FactPoint], str]:
    for concept in concepts:
        raw = _gaap_or_dei(namespaces, concept)
        if not raw:
            continue
        points = [_parse_point(row, concept) for row in _unit_rows(raw, usd=True)]
        cleaned = _prefer_annual(_dedup_latest_filed([p for p in points if p is not None]))
        if cleaned:
            return cleaned, concept
    return [], ""


def _latest_instant(
    namespaces: dict[str, dict[str, Any]],
    concepts: tuple[str, ...],
    *,
    usd: bool,
) -> tuple[FactPoint | None, str]:
    best: FactPoint | None = None
    best_concept = ""
    for concept in concepts:
        raw = _gaap_or_dei(namespaces, concept)
        if not raw:
            continue
        points = [_parse_point(row, concept) for row in _unit_rows(raw, usd=usd)]
        cleaned = [p for p in points if p is not None]
        if not cleaned:
            continue
        cand = max(cleaned, key=lambda p: (p.filed, p.end))
        if best is None or (cand.filed, cand.end) >= (best.filed, best.end):
            best = cand
            best_concept = concept
    return best, best_concept


def _latest_debt(namespaces: dict[str, dict[str, Any]]) -> tuple[FactPoint | None, str]:
    total, concept = _latest_instant(namespaces, DEBT_TOTAL_CONCEPTS, usd=True)
    if total is not None:
        return total, concept
    parts: list[FactPoint] = []
    used: list[str] = []
    for group in DEBT_COMPONENT_CONCEPTS:
        pt, name = _latest_instant(namespaces, group, usd=True)
        if pt is not None:
            parts.append(pt)
            used.append(name)
    if not parts:
        return None, ""
    newest = max(parts, key=lambda p: (p.filed, p.end))
    summed = FactPoint(
        end=newest.end,
        val=sum(p.val for p in parts),
        form=newest.form,
        fp=newest.fp,
        fy=newest.fy,
        filed=newest.filed,
        accn=newest.accn,
        concept="+".join(used),
    )
    return summed, summed.concept


def _derive_fcf(ocf: list[FactPoint], capex: list[FactPoint]) -> list[FactPoint]:
    capex_by_end = {p.end: p for p in capex}
    capex_by_year = {p.end[:4]: p for p in capex}
    out: list[FactPoint] = []
    for cf in ocf:
        cap = capex_by_end.get(cf.end) or capex_by_year.get(cf.end[:4])
        if cap is None:
            continue
        # SEC PaymentsToAcquire* is almost always a positive outflow.
        fcf_val = float(cf.val) - abs(float(cap.val))
        out.append(
            FactPoint(
                end=cf.end,
                val=fcf_val,
                form=cf.form,
                fp=cf.fp,
                fy=cf.fy,
                filed=max(cf.filed, cap.filed),
                accn=cf.accn,
                concept="derived_fcf",
            )
        )
    return out
