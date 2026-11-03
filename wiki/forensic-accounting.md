---
source: Brain/Strategies/Forensic Accounting.md
created: 2026-04-13
updated: 2026-09-21
tags: [strategy, fundamentals, risk]
---

# Forensic Accounting

> Financial health checks flagging accounting irregularities or distress before entry.

## Checks

| Metric | Env Var | Default | Flag |
|--------|---------|---------|------|
| Sloan Ratio | `FORENSIC_SLOAN_MAX` | 0.10 | Accrual risk above threshold |
| Beneish M-Score | `FORENSIC_BENEISH_MAX` | -1.78 | Manipulation above threshold |
| Altman Z-Score | `FORENSIC_ALTMAN_MIN` | 1.80 | Distress below threshold |

## Filter Modes

off / shadow (default) / soft / hard

Research dossier and decision-card notes may overlay Sloan / Beneish / Altman from Sharadar ART when coverage is enough (`data_lineage` includes `sharadar_art`). The live scanner path in `forensic_accounting.py` and `FORENSIC_FILTER_MODE` stay on yfinance. See [[sharadar-bundle]].

## Related Pages

- [[signal-scanner]] — forensic flags in quality gates
- [[quality-gates]] — forensic results feed weak reasons
- [[feature-flags]] — `FORENSIC_ENABLED`, `FORENSIC_FILTER_MODE`
- [[sharadar-bundle]] — research overlay from ART; scanner path unchanged

---

*Last compiled: 2026-09-21*
