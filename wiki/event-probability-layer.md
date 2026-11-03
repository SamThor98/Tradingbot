---
source: schwab_skill/core/event_probability/, schwab_skill/docs/EVENT_PROBABILITY_LAYER.md
created: 2026-08-27
updated: 2026-09-02
tags: [research, valuation, prediction-market, dcf, point-in-time]
---

# Event Probability Layer

> Research-side layer that turns external event probabilities into a
> scenario-priced DCF and per-event sensitivities. Not a trade signal, and not
> the same thing as the [[prediction-market]] scan overlay.

## What it does

An event probability enters as an **upstream input to company valuation**, not
as a score. For each mapped event the layer prices two DCF scenarios and weights
them:

```
EV = P_adj * Value(inputs + impact_if_yes) + (1 - P_adj) * Value(inputs + impact_if_no)
```

and reports the derivative `Value_yes - Value_no` as `$ per +1pp` of event
probability. Output is visible in Research → Quick check → Event risk and via
`/api/event-probability/{ticker}`. It never touches scan ranking, sizing, or
advisory (see [[signal-scanner]]).

## Modules

`core/event_probability/`: `providers` (stub / polymarket / historical_file,
fetching **by `source_market_id`**, not by ticker search) → `normalizer` →
`calibrator` → `repository`. Then `mapper` (exposure catalog) + `valuation`
(5-year FCF + perpetuity DCF, matching the report DCF) → `impact` →
`sensitivity` / `signal`. `service` orchestrates `ingest()` and
`ticker_payload()`.

## Calibration

`adjusted = w * raw + (1 - w) * prior`, where `w` combines liquidity (0.5),
spread (0.3), and freshness (0.2), capped by `EVENT_PROB_MAX_MARKET_WEIGHT`
(0.85) so no single market fully drives a valuation. Zero or missing liquidity
forces `w = 0` — the event falls back entirely to the prior. Raw and adjusted
probabilities are both persisted.

## Point-in-time

Two tables (see [[database-schema]]): `event_probability_events` for current
state, `event_probability_history` **append-only**, unique on
`(event_id, timestamp)`. `EventRepository.event_as_of()` rebuilds an event from
rows at or before `as_of` and recomputes change lookbacks from that slice, so a
replay cannot see a probability published later.

Mappings live in git at `research/event_exposure_catalog.json` with
`effective_from` / `effective_to`. **Changing a mapping means closing the old
row and appending a new one**; editing an open row in place rewrites history.

Caveat: the default valuation loader prefers cached [[companyfacts-pricing]]
inputs, then the report DCF. Historical replays must still inject frozen
`ValuationInputs`.

## Event Mispricing Score

`ems_gap_pct = implied_move_pct - realized_return_pct` over
`EVENT_PROB_EMS_LOOKBACK_DAYS` (default 7). Research triage only — not in
`composite_score`, rank v2, or [[meta-policy]].

## Config

`EVENT_PROB_*` (see [[plugin-modes-config]] and [[feature-flags]]), all off by
default. `EVENT_PROB_MODE=live` means **the panel is shown**, not that anything
mutates a scan score. Provider `stub` is fixture-backed and offline; Polymarket's
Gamma API needs no credential today.

## Not the prediction-market overlay

| | [[prediction-market]] overlay | This layer |
| --- | --- | --- |
| Flags | `PRED_MARKET_*` | `EVENT_PROB_*` |
| Lookup | Ticker search | Event-first by market id |
| Effect | Mutates Stage B `signal_score`, sizing, advisory | Produces a valuation + sensitivities |
| `live` means | Score mutation applied | Panel visible |
| Event → company | Heuristic match | Operator-authored catalog |

The two are independent; promoting one does not promote the other.

## Known limitations (Version 1)

Events assumed independent (`ASSUME_INDEPENDENT_EVENTS`); no Monte Carlo —
distribution comes from enumerated scenarios (max 6 events priced individually,
the rest folded into an expected-value factor); valuation inputs not PIT by
default; mappings hand-authored; descriptive event fields not versioned;
cross-sectional EMS z-score deferred.

## Related Pages

- [[prediction-market]] — The separate Stage B score overlay; do not conflate
- [[database-schema]] — `event_probability_events` / `event_probability_history`
- [[local-dashboard-endpoints]] — `/api/event-probability/*`
- [[companyfacts-pricing]] — Statement warehouse this layer prices against
- [[frontend-route-contract]] — `event-risk` deep-link alias
- [[pit-research-platform]] — Point-in-time discipline this layer follows
- [[signal-scanner]] — Explicitly untouched by this layer

---
*Last compiled: 2026-08-27*
