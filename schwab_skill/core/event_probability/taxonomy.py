"""Standardized event taxonomy.

Categories are provider-agnostic on purpose: a Polymarket market, a Kalshi
contract, or a hand-entered event all resolve to the same category id so the
exposure catalog and dashboard never encode one vendor's naming.
"""

from __future__ import annotations

MONETARY_POLICY = "monetary_policy"
INFLATION = "inflation"
RECESSION = "recession"
ELECTION = "election"
TAX_POLICY = "tax_policy"
REGULATION = "regulation"
TARIFF = "tariff"
TRADE_RESTRICTION = "trade_restriction"
EXPORT_CONTROL = "export_control"
GEOPOLITICAL_CONFLICT = "geopolitical_conflict"
COMMODITY_SUPPLY = "commodity_supply"
ENERGY_POLICY = "energy_policy"
DRUG_APPROVAL = "drug_approval"
GOVERNMENT_SPENDING = "government_spending"
AI_REGULATION = "AI_regulation"
SEMICONDUCTOR_POLICY = "semiconductor_policy"
COMPANY_SPECIFIC = "company_specific"
OTHER = "other"

EVENT_CATEGORIES: frozenset[str] = frozenset(
    {
        MONETARY_POLICY,
        INFLATION,
        RECESSION,
        ELECTION,
        TAX_POLICY,
        REGULATION,
        TARIFF,
        TRADE_RESTRICTION,
        EXPORT_CONTROL,
        GEOPOLITICAL_CONFLICT,
        COMMODITY_SUPPLY,
        ENERGY_POLICY,
        DRUG_APPROVAL,
        GOVERNMENT_SPENDING,
        AI_REGULATION,
        SEMICONDUCTOR_POLICY,
        COMPANY_SPECIFIC,
        OTHER,
    }
)

# Ordered: the first keyword hit wins, so put narrow categories before broad
# ones (export control before regulation, semiconductor policy before tariff).
_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (EXPORT_CONTROL, ("export control", "export restriction", "export ban", "entity list")),
    (SEMICONDUCTOR_POLICY, ("semiconductor", "chip act", "chips act", "fab subsid", "euv")),
    (AI_REGULATION, ("ai regulation", "ai act", "ai safety bill", "model licensing")),
    (DRUG_APPROVAL, ("fda approval", "fda approve", "phase 3", "phase iii", "drug approval")),
    (TARIFF, ("tariff", "import duty", "customs duty")),
    (TRADE_RESTRICTION, ("trade restriction", "trade ban", "sanction", "embargo")),
    (MONETARY_POLICY, ("fed ", "fomc", "rate cut", "rate hike", "interest rate", "central bank")),
    (INFLATION, ("inflation", "cpi ", "pce ", "core cpi")),
    (RECESSION, ("recession", "gdp contraction", "hard landing")),
    (ELECTION, ("election", "presidential race", "midterm", "primary winner")),
    (TAX_POLICY, ("tax rate", "corporate tax", "tax bill", "tax reform")),
    (GEOPOLITICAL_CONFLICT, ("invasion", "war", "military strike", "conflict escalat")),
    (COMMODITY_SUPPLY, ("opec", "oil supply", "production cut", "commodity supply", "wheat supply")),
    (ENERGY_POLICY, ("energy policy", "pipeline approval", "drilling permit", "renewable mandate")),
    (GOVERNMENT_SPENDING, ("government shutdown", "spending bill", "appropriation", "stimulus")),
    (REGULATION, ("antitrust", "regulat", "sec rule", "ftc ", "doj suit")),
)


def normalize_category(raw: str | None) -> str:
    """Coerce an arbitrary category label to a known taxonomy id."""
    value = str(raw or "").strip()
    if not value:
        return OTHER
    if value in EVENT_CATEGORIES:
        return value
    lowered = value.lower().replace("-", "_").replace(" ", "_")
    for known in EVENT_CATEGORIES:
        if known.lower() == lowered:
            return known
    return OTHER


def infer_category(*texts: str | None) -> str:
    """Best-effort category from free text (provider question/description).

    Deliberately keyword-based rather than a model: every category assignment
    must be explainable and reproducible for a historical replay.
    """
    haystack = " ".join(str(part or "") for part in texts).lower()
    if not haystack.strip():
        return OTHER
    for category, keywords in _KEYWORD_RULES:
        for keyword in keywords:
            if keyword in haystack:
                return category
    return OTHER
