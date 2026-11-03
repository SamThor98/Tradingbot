# Event Probability Layer (Version 1)

Feeds external event probabilities into company valuation. An event probability
is treated as an **upstream input to a scenario-priced DCF**, never as a signal
on its own: the layer answers "what is this company worth if this event resolves
YES, weighted by how likely the market says that is", and shows the operator the
sensitivity behind the answer.

It is **not** the same thing as the Polymarket scan overlay — see
[Relationship to the PRED_MARKET overlay](#relationship-to-the-pred_market-overlay).

## Pipeline

```
provider -> normalize -> calibrate -> persist (+append history)
                                          |
exposure catalog (event -> ticker/variable)
                                          v
                            scenario DCF -> impact / sensitivity / EMS -> API -> panel
```

| Module | Responsibility |
| --- | --- |
| `core/event_probability/providers.py` | Fetch markets by `source_market_id` (stub / polymarket / historical_file) |
| `normalizer.py` | `RawMarket` -> `NormalizedEvent`, probability-change lookbacks |
| `calibrator.py` | Blend the raw probability toward a prior based on market quality |
| `taxonomy.py` | Provider-agnostic event categories |
| `mapper.py` | Versioned event -> company/variable exposure catalog |
| `valuation.py` | Frozen `ValuationInputs` and the 5-year FCF + perpetuity DCF |
| `impact.py` | Scenario enumeration, expected value, distribution metrics |
| `sensitivity.py` | `d(value)/d(probability)`, per +1pp |
| `signal.py` | Event Mispricing Score (implied move vs realized return) |
| `repository.py` | Current state + append-only history, point-in-time reads |
| `service.py` | `ingest()` and `ticker_payload()` orchestration |

## Data model

`event_probability_events` holds the **current** state of each event (both raw
and adjusted probability, 1d/7d/30d changes, volume/liquidity/spread,
confidence, resolution status). `event_probability_history` is **append-only**:
one row per successful fetch, unique on `(event_id, timestamp)`. Old rows are
never updated, which is what makes a historical replay honest. Migration:
`alembic/versions/saas010_event_probability.py`.

Exposure mappings live in git, not SQL:
`research/event_exposure_catalog.json`, each row carrying `effective_from` /
`effective_to`.

> **Editing a mapping:** set `effective_to` on the existing row and append a new
> row starting at that timestamp. Editing an open row in place silently rewrites
> history and is the one action that breaks point-in-time correctness here.

## Probability calibration

Raw prediction-market prices are not assumed calibrated:

```
adjusted = w * raw + (1 - w) * prior          w in [0, EVENT_PROB_MAX_MARKET_WEIGHT]
```

`prior` is the last adjusted probability for that event (0.5 if it has none).
`w` is a weighted blend of liquidity (0.5), spread (0.3), and freshness (0.2).
Zero or missing liquidity forces `w = 0` — a price nobody traded against carries
no information, so the event falls back entirely to the prior rather than being
half-trusted. Both probabilities are always persisted, and `calibrate()` is a
pure function, so a real calibration model can replace the blend later without a
schema change.

## Valuation

Scenarios are priced with the same 5-year FCF + perpetuity model as the report
DCF, so an event-adjusted value is directly comparable to the DCF the operator
already sees. Supported shocks (`FUNDAMENTAL_VARIABLES`):

| `fundamental_variable` | Effect |
| --- | --- |
| `revenue_growth`, `fcf_growth`, `operating_margin` | additive delta on FCF growth |
| `wacc`, `discount_rate` | additive delta on the discount rate |
| `terminal_growth` | additive delta on perpetuity growth |
| `terminal_multiple`, `price_multiple` | multiplicative scalar on equity value |

For one binary event:

```
EV = P_adj * Value(inputs + impact_if_yes) + (1 - P_adj) * Value(inputs + impact_if_no)
```

`materiality` scales the declared impact **once**, inside the shock. The
sensitivity and mispricing figures therefore already embed it and must not
scale by it again.

Sensitivity is exact, not a numerical difference: the derivative of the binary
expected value with respect to the probability is `Value_yes - Value_no`,
reported as `$ per +1pp` and `% of price per +1pp`.

## Multiple events

Version 1 enumerates the full scenario tree and weights each branch by the
product of the individual probabilities. That product is only valid under
`ASSUME_INDEPENDENT_EVENTS = True`, which is **wrong for correlated macro
events** — two "recession" markets are not independent coin flips. The
enumeration is factored so `scenario_probability()` is the single seam a
conditional/correlated model would replace.

Above `MAX_ENUMERATED_EVENTS` (6, i.e. 64 priced scenarios) the lowest-impact
events are folded into a deterministic expected-value factor instead of being
enumerated; their ids are reported in `truncated_event_ids`.

Distribution metrics (expected, median, p10/p25/p75/p90, expected return,
P(>0), P(>25%), P(>50%), P(loss)) are computed over that discrete,
probability-weighted scenario set. There is **no RNG anywhere in this layer** —
same inputs, same output, every time.

## Event Mispricing Score (research only)

```
implied_move_pct = sum over events of (dP over lookback in pp) * (% of price per pp)
ems_gap_pct      = implied_move_pct - realized_return_pct
```

A large positive gap means event probabilities implied upside the price has not
delivered. It is a triage signal, deliberately **not** wired into
`composite_score`, rank v2, sizing, or advisory. An event with no baseline
observation in the window contributes nothing and is counted in
`missing_change_events` — a missing change is not a zero change.

## Point-in-time behavior

`EventRepository.event_as_of()` rebuilds an event from history rows at or before
`as_of` and recomputes the change metrics from that slice, so a replay cannot
observe a probability published later. A settlement after `as_of` does not leak
backwards. Mappings are filtered by their effective window.

The gap to know about: the default valuation loader prefers cached SEC
companyfacts, then the report DCF. It is not a filing-date-accurate PIT series.
A backtest must inject its own frozen `ValuationInputs` via the service's
`valuation_loader`.

## API

| Route | Auth | Purpose |
| --- | --- | --- |
| `GET /api/event-probability/{ticker}?as_of=` | local API key | Panel payload |
| `POST /api/event-probability/ingest` | local API key | Fetch configured markets, append history |

SaaS parity lives in `webapp/routes/tenant_event_probability.py` (Supabase JWT).
Both return the standard `ApiResponse(ok, data, error)` envelope. Deliberately
**not** folded into `/api/check/{ticker}` — Quick Check stays fast and the panel
loads in parallel.

Dashboard: Research -> Quick check -> Event risk (`#eventProbabilitySection`),
deep link alias `event-risk`.

## Configuration

All flags default to off. See `env.example` for the annotated block.

| Variable | Default | Notes |
| --- | --- | --- |
| `EVENT_PROB_ENABLED` | `false` | Master enable |
| `EVENT_PROB_MODE` | `off` | `off` / `shadow` / `live`; **live = visible in UI**, not scan-score mutation |
| `EVENT_PROB_PROVIDER` | `stub` | `stub` / `polymarket` / `historical_file` |
| `EVENT_PROB_TIMEOUT_MS` | `1500` | Per-request provider timeout |
| `EVENT_PROB_CACHE_TTL_SEC` | `300` | Payload cache TTL |
| `EVENT_PROB_MAX_EVENT_AGE_HOURS` | `72` | Beyond this, weight shifts to the prior |
| `EVENT_PROB_MIN_LIQUIDITY` | `1000` | Liquidity floor for market trust |
| `EVENT_PROB_MAX_SPREAD` | `0.08` | Spread at which trust decays to zero |
| `EVENT_PROB_MAX_MARKET_WEIGHT` | `0.85` | `< 1.0` keeps prior weight in every event |
| `EVENT_PROB_EMS_LOOKBACK_DAYS` | `7` | Mispricing window |
| `EVENT_PROB_HISTORICAL_SNAPSHOT_PATH` | `""` | Required when provider is `historical_file` |

Polymarket's public Gamma API needs no credential today. A keyed source must
resolve its secret through a `config.py` getter — never hard-code one in
`providers.py`.

## Ingest

Pull-on-demand via `POST /api/event-probability/ingest`, plus an optional hourly
(`:05`) job in `main.py`'s `schedule` loop that no-ops unless the layer is
active. No Celery beat in Version 1. History is written on every successful
fetch; a repeat at the same timestamp is reported as a duplicate, not stored
twice.

## Observability

Ingest returns `IngestDiagnostics`: `processed`, `stored`, `duplicates`,
`malformed`, `stale`, `invalid_probability`, `provider_errors`,
`mapping_errors`, `error_codes`. Structured warnings cover provider failure and
rate limits, stale events, clamped probabilities, rejected catalog rows,
duplicate mappings, and valuation failures. Provider failures fail safe: the
event is skipped and the panel shows it as missing or stale rather than the
valuation crashing.

## Relationship to the PRED_MARKET overlay

Two independent systems that both happen to read Polymarket. Neither flag family
affects the other, and promoting one does not promote the other.

| | `PRED_MARKET_*` overlay | `EVENT_PROB_*` layer |
| --- | --- | --- |
| Code | `prediction_market.py` | `core/event_probability/` |
| Lookup | Ticker search / string matching | Event-first by `source_market_id` |
| Effect | Adjusts Stage B `signal_score`, size multiplier, advisory | Produces a scenario-priced valuation and sensitivities |
| Consumer | Scanner ranking and gates | Research dashboard, backtest research |
| `live` means | Score mutation is applied | The panel is shown |
| Event -> company | Heuristic match | Operator-authored catalog |

This layer does not modify `signal_scanner.py` ranking, the overlay math,
position sizing, or advisory output.

## Known limitations (Version 1)

- **Events are assumed independent.** Correlated macro events are mispriced by
  the scenario weighting.
- **No Monte Carlo.** The distribution comes from enumerated discrete
  scenarios, so it has no within-scenario dispersion.
- **Valuation inputs are not point-in-time** by default (see above).
- **Mappings are hand-authored.** There is no ticker/industry master table and
  no automatic event-to-company inference.
- **Only the listed shock variables are supported.** An unrecognized
  `fundamental_variable` is rejected at catalog load.
- **Descriptive event fields are not versioned.** A renamed market shows its
  current name in a historical replay; probabilities are still correct.
- **Cross-sectional EMS z-scores are deferred.** Single-name `ems_gap` only.
- **Nothing here alters scan rank, sizing, or advisory.**

## Validation

From `schwab_skill/`:

```bash
python -m ruff check core/event_probability webapp/routes/event_probability.py webapp/models.py config.py
python -m pytest -q tests/test_event_probability.py
```

Browser: `https://127.0.0.1:8182/` -> Research -> Quick check on a ticker in the
exposure catalog (`NVDA` with the shipped seed rows), after
`POST /api/event-probability/ingest`.
