# Property Hunter

Pi-local buyer's/renter's-agent pipeline over Domain.com.au. A scheduled hunt
fetches listings that match Ben's brief, persists them, scores each one, and
delivers a digest (and, on Saturdays, a folio PDF).

## How it fits together

```
buyer.md ─► buyer_profile ─┐
                           ├─► hunts.json ─► hunt_runner.py  (cron entry point)
                           │                     │
                           │                     ├─ source_providers ─┬─ domain_graphql ─► Domain GraphQL API
                           │                     │        (fallback     │      (search + enrich)
                           │                     │         chain)       └─ realestate_cli ─► realestate.com.au
                           │                     │                             (fetch via CDP → parse)
                           │                     ├─ db.py  (SQLite: listings + history)
                           │                     ├─ decision_engine.analyse_listing
                           │                     │     ├─ due_diligence
                           │                     │     ├─ valuation
                           │                     │     ├─ risk
                           │                     │     ├─ viability
                           │                     │     └─ action_plan
                           │                     └─ report_ux  (text digest)
                           └─► report_builder.py ─► folio PDF (+ inspection_plan, market_sources)
```

### Fetch layer
- **`domain_graphql.py`** — **the primary source.** Talks to `POST
  https://www.domain.com.au/graphql`, the API Domain's own front-end uses, from
  inside the running OpenClaw browser (same cookies/TLS/fingerprint as a real
  tab). Covers **both** halves of the pipeline: `search()` and `listing_detail()`
  (enrichment). Search cards already carry the address, the full image gallery,
  typed features and real inspection datetimes; enrichment adds the full
  description, structured features and the agent roster.
  Two schema limits, handled explicitly rather than silently: the API exposes
  **no agent phone number** (email + Domain profile URL only), and has **no
  under-offer exclusion**, so `exclude_under_offer` is enforced client-side in
  `source_providers`. A `features:` filter that has no enum equivalent is
  returned as `unsupported_filters` instead of being dropped.
- **`realestate_cli.py`** — the **Domain fallback**. A deliberate mirror of
  `domain_cli.py` (same public surface, same normalized listing dict) for
  realestate.com.au. REA ships data in `window.ArgonautExchange` (a doubly
  stringified urql cache) instead of `__NEXT_DATA__`, and sits behind **Kasada**
  (`KPSDK`/`ips.js`) instead of Akamai — both handled here. IDs are namespaced
  `rea:<id>` so REA and Domain never collide on `listings.id`.
- **`domain_cli.py`** — the retired HTML scraper. Still imported for its URL
  builders, rate limiter, CDP page helper and `sold_status_from_tags`, but its
  *fetch* path is **403 from this IP on every Domain route that matters**, so
  `DomainListingProvider` is no longer in the default chain. Kept for the
  offline parsers and as a reference; do not route new work through it.
- **`source_providers.py`** — wraps the fetchers behind one interface.
  `build_provider_chain()` yields the ordered fallback chain (default
  `[domain_graphql, realestate]`); `hunt_runner` tries each in turn and uses the
  first that isn't blocked, so **every hunt auto-falls-back to REA when Domain
  is blocked** — no `hunts.json` change needed. A hunt may pin its own order
  with a `"providers": [...]` field. Range filters a provider can't express in
  its query (`beds_max`/`baths_min`/`cars_min`, and `exclude_under_offer` for
  Domain) are enforced client-side (`_passes_filters`, `_is_off_market_excluded`).
- **`backfill_detail.py`** — re-enriches rows the hunt never did. `run_hunt`
  only enriches listings that are *new since the last run*, so anything already
  seen keeps whatever its search card gave it — which is how a shortlisted
  listing ends up with a good address and no agent contact. Scopes:
  `--scope address|agents|description|any`, `--active-only`, `--dry-run`.
  API-authoritative, falls back to parsing the stored SEO URL for delisted rows,
  and is resumable.

> **Blocking note.** Akamai hard-blocks Domain's *document* routes (`/sale/`,
> `/sold-listings/`, bare listing ids) from this IP — via `page.goto` **and**
> same-origin XHR. `POST /graphql` on the same origin is not challenged, which
> is why the pipeline moved to it. Everything still goes over Chrome DevTools
> Protocol to the genuine already-running **OpenClaw browser**;
> `domain_cli.ensure_browser()` self-heals by probing `$DOMAIN_CDP_URL`
> (default `http://127.0.0.1:18800`) and running `openclaw browser start` if
> it's down.

### Hunt bookkeeping

Three rules in `hunt_runner.py` that are easy to get wrong, and each produced a
stream of phantom events in the digest before they were fixed:

- **`max_items` is a reporting cap, not a fetch cap.** The full result window is
  fetched and compared; only the digest and the enrichment pass are capped.
  Truncating first made listings rotate in and out of a 12-row window and read
  as withdrawn one day, relisted the next.
- **A truncated result never marks anything stale.** If the provider stopped
  paging with more pages left (`more_pages`), a listing's absence says nothing.
  Truncation is measured by page exhaustion, not by comparing counts —
  `totalResults` includes development-*project* cards that normalize to nothing.
- **Staleness is resolved across all hunts, not within one.** It is observed per
  hunt but recorded against the *listing*, and hunts overlap heavily now that
  surrounding suburbs are included — the same Zetland unit appears in the
  Randwick result set. `resolve_stale()` marks a listing withdrawn only when it
  is absent from every hunt in the run.

Status is normalized to `live | sold | leased | off_market | withdrawn`
(`domain_graphql.normalize_status`) so a search-only observation and an enriched
one agree; otherwise every listing logged a status change on every run.
`backfill_detail.py --normalize-status` migrates legacy raw values once.

### Brief → searches
- **`buyer.md`** — Ben's brief: YAML front-matter (hard criteria) + prose (soft
  prefs / deal-breakers).
- **`buyer_profile.py`** — translates the front-matter into buy/rent/sold searches.
- **`hunts.json`** — the concrete saved searches the cron runs. Per-hunt keys:
  `max_items` (reporting cap), `enrich` (fetch detail for new listings; on for
  buy hunts, off for the sold comp sweep), `providers` (pin the fallback order),
  and `filters.include_surrounding` (default **true** — Domain's page search
  includes nearby suburbs, and omitting it cut Crows Nest from 42 matches to 3).

### Persistence
- **`db.py`** — `PropertyDB` (SQLite, WAL): listings + append-only price/status
  snapshots, agents, inspections, hunts/runs. Live DB at
  `data/property_hunter.sqlite3`. Tracks listing lifecycle events (price drops,
  status changes, stale, **relists**) and **market supply** per saved search
  (`hunt_runs.total_results` read as a trend via `db.supply_trend`).

### Decision pipeline (`decision_engine.analyse_listing`)
`due_diligence` · `valuation` · `risk` · `viability` · `action_plan` — each a
focused module returning a slice of the per-listing decision.

### Reporting tools
- **`report_ux.py`** — text digest (used by `hunt_runner`).
- **`report_builder.py`** — premium "PROPERTY FOLIO" PDF (folio palette, 2-page
  spreads). Deterministic layout; the agent authors the judgement prose payload.
- **`agent_report.py`** — agent-facing shortlist report from the DB.
- **`sales_report.py`** — sold-comparables report (median/range/$ per bed).
- **`suburb_analyzer.py`** — ranks suburbs by affordability-fit × activity.
- **`inspection_plan.py`**, **`market_sources.py`** — inspection scheduling +
  market-source freshness checks (used by `report_builder`).

### Playbooks / planning
- **`buyers_agent.md`** — the operating playbook the cron agent follows.
- **`BUYERS_RENTERS_AGENT_TASK_PLAN.md`** — enhancement task plan.

### Other
- `scratch/` — dated one-off dev scripts (not part of the pipeline).
- `tests/` — `unittest` suite for the decision pipeline.

## Running

```bash
source venv/bin/activate

# Scheduled hunt (cron entry point) — fetches via CDP, persists, scores, digests.
python hunt_runner.py --json

# Ad-hoc search straight against Domain's GraphQL API.
python domain_graphql.py --suburb "Zetland NSW 2017" --price-max 1100000 --limit 10

# Re-enrich rows the hunt never enriched (missing address / agents / description).
python backfill_detail.py --dry-run

# Score a single saved listing JSON.
python decision_engine.py --listing-json listing.json

# Sold comparables for the brief's suburbs.
python sales_report.py --days 90
```

### Tests

```bash
PYTHONPATH=. venv/bin/python -m unittest discover -s tests
```

## API

```bash
source venv/bin/activate
uvicorn domain_api:app --host 127.0.0.1 --port 8787
```

Endpoints: `GET /health`, `POST /domain/search`, `POST /domain/listing`,
`POST /reports/daily`. All of them run the same provider chain as the cron,
so they inherit the GraphQL transport and the REA fallback.

```bash
# Structured filters only — a raw Domain URL can no longer be fetched (Akamai).
curl -s http://127.0.0.1:8787/domain/search \
  -H 'Content-Type: application/json' \
  -d '{"filters":{"mode":"sale","suburbs":["Zetland NSW 2017"],"price_max":1100000},"limit":10}'
```

## Schedule

Two OpenClaw crons (tz Australia/Sydney) drive it:
- **`property-hunter-weekday-0630`** (`30 6 * * 1-5`) — light run; only messages
  strong new/changed candidates.
- **`property-hunter-saturday-deep-0700`** (`0 7 * * 6`) — full folio PDF.

Both ensure the OpenClaw browser is running first, then call `hunt_runner.py`.
