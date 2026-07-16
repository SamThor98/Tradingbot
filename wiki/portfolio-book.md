---
source: schwab_skill/webapp/routes/book.py, schwab_skill/core/book_service.py
created: 2026-07-16
updated: 2026-07-16
tags: [portfolio, book, calendar, tax, journal, ui]
---

# Portfolio Book

> Research → Portfolio → **Book**: realized P/L calendar, tax estimate, and per-ticker journal.

## Placement

- Portfolio sub-tabs: Positions · Risk · **Book**
- Inside Book: Calendar | Tax | Journal (segmented)
- Deeplinks: `?section=book`, `book-calendar`, `book-tax`, `book-journal`
- Positions row → Journal; calendar day fills → Add note

## Data model (v1)

| Concern | Source |
|---------|--------|
| Trades / realized P/L | Schwab `GET …/transactions?types=TRADE` (single `SCHWAB_ACCOUNT_HASH`) |
| Lot matching | Local FIFO on OPENING/CLOSING equity legs |
| MTM | Daily EOD snapshots (`portfolio_equity_snapshots`); day Δ of marked open equity (positions only) |
| Tax prefs + journal | Local SQLite (`book_tax_prefs`, `book_journal_*`); schema ready for SaaS `user_id` |

## Tax estimate

- ST/LT buckets + simple netting; editable federal + state rates
- Dollar estimate only after rates saved
- No wash-sale engine; disclaimer required in UI

## Snapshot cadence

- Bot schedule: weekdays 16:15 ET
- Manual: Book → Capture today

## Related Pages

- [[frontend-route-contract]] — deeplink aliases
- [[local-dashboard-endpoints]] — `/api/book/*` routes
- [[webapp-dashboard]] — Research / Portfolio home

---

*Last compiled: 2026-07-16*
