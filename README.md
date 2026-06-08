# Property Hunter

Pi-local buyer's/renter's-agent pipeline over Domain.com.au. A scheduled hunt
fetches listings that match Ben's brief, persists them, scores each one, and
delivers a digest (and, on Saturdays, a folio PDF).

## How it fits together

```
buyer.md ─► buyer_profile ─┐
                           ├─► hunts.json ─► hunt_runner.py  (cron entry point)
                           │                     │
                           │                     ├─ source_providers ─► domain_cli ─► Domain.com.au
                           │                     │                          (fetch via CDP → parse)
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
- **`domain_cli.py`** — builds Domain search URLs, fetches HTML, and parses the
  embedded `digitalData` / `__NEXT_DATA__` / JSON-LD into normalized listings.
  Three transports via `--fetcher`: `cdp` (default in the pipeline), `http`,
  `playwright`.
- **`source_providers.py`** — `DomainListingProvider` wraps `domain_cli` and is
  what the hunt orchestration talks to (keeps the pipeline scraper-agnostic).

> **Akamai note.** Domain hard-blocks automation-launched browsers. The pipeline
> fetches with `fetcher="cdp"`, attaching over Chrome DevTools Protocol to the
> genuine already-running **OpenClaw browser** (same residential Pi IP), which
> loads Domain fine. `domain_cli.ensure_browser()` self-heals: it probes the CDP
> endpoint (`$DOMAIN_CDP_URL`, default `http://127.0.0.1:18800`) and runs
> `openclaw browser start` if it's down. The old headed-Playwright evasion
> approach is obsolete.

### Brief → searches
- **`buyer.md`** — Ben's brief: YAML front-matter (hard criteria) + prose (soft
  prefs / deal-breakers).
- **`buyer_profile.py`** — translates the front-matter into buy/rent/sold searches.
- **`hunts.json`** — the concrete saved searches the cron runs.

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

# Ad-hoc fetch + parse of a search page.
python domain_cli.py search --url "https://www.domain.com.au/sale/zetland-nsw-2017/" --limit 10

# Score a single saved listing JSON.
python decision_engine.py --listing-json listing.json

# Sold comparables for the brief's suburbs.
python sales_report.py --days 90
```

### Tests

```bash
cd tests && PYTHONPATH=..:. ../venv/bin/python -m unittest test_decision_engine
```

## API

```bash
source venv/bin/activate
uvicorn domain_api:app --host 127.0.0.1 --port 8787
```

Endpoints: `GET /health`, `POST /domain/search`, `POST /domain/listing`,
`POST /reports/daily`.

```bash
curl -s http://127.0.0.1:8787/domain/search \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.domain.com.au/sale/zetland-nsw-2017/","limit":10}'
```

## Schedule

Two OpenClaw crons (tz Australia/Sydney) drive it:
- **`property-hunter-weekday-0630`** (`30 6 * * 1-5`) — light run; only messages
  strong new/changed candidates.
- **`property-hunter-saturday-deep-0700`** (`0 7 * * 6`) — full folio PDF.

Both ensure the OpenClaw browser is running first, then call `hunt_runner.py`.
