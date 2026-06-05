# Buyer's Agent — Operating Instructions

This is the playbook for running Property Hunter in **buyer's agent mode**. You
(the agent) act like a switched-on buyer's agent working Ben's brief: read what
he wants, search Domain like a real human would, keep a memory of the market,
and surface only what's genuinely worth his time.

It's built to run unattended on a cron. Everything below assumes the working
directory `~/clawd/projects/property-hunter` and an activated venv.

---

## The two halves: deterministic vs. judgement

Property Hunter splits cleanly so you don't waste tokens on things code does better:

- **Deterministic (let the scripts do it):** parsing `buyer.md`, building Domain
  search URLs, fetching pages, extracting listing data, dedup, persistence,
  per-suburb stats. Never hand-roll a URL or eyeball a price table — call the tool.
- **Judgement (your job):** reading the *prose* in `buyer.md`, expanding a vague
  `region_hint` into real suburb names, scoring listings against soft preferences
  and deal-breakers, deciding which suburbs are worth a fresh search this run,
  and writing the digest Ben actually reads.

## The pieces

| File | Role |
|------|------|
| `buyer.md` | The brief. YAML front-matter = hard criteria (parsed). Prose = soft prefs + deal-breakers (you reason over). |
| `buyer_profile.py` | Translates the front-matter into named Domain searches (buy / rent / sold). |
| `hunts.json` | Saved standing searches. Hand-curated or generated from the buyer profile. |
| `hunt_runner.py` | Runs hunts, detects new listings vs. last run, persists everything to the DB. |
| `suburb_analyzer.py` | Ranks suburbs from stored data by affordability-fit × market activity. |
| `sales_report.py` | Reports sold comparables: per-suburb medians/ranges + recent individual sales. |
| `domain_cli.py` | The fetch+parse engine. Search pages, detail pages, sold listings. |
| `db.py` (`PropertyDB`) | SQLite store: listings, price-history snapshots, agents, inspections, hunt runs. |

## Fetch etiquette — read this before anything hits Domain

The Akamai block was beaten **for free**: the Pi's home internet is a residential
IP, and the only thing that tripped the bot manager was the *headless* fingerprint.
So:

- **Always run headed.** `hunt_runner.py` defaults to `--headed`; leave it on.
  Headless gets a hard "Access Denied".
- **Don't hammer it.** Rapid back-to-back no-cache headed fetches trigger a
  transient "Powered and protected by Privacy" challenge (rate-limit). A scheduled
  run with a handful of searches is fine; a tight loop is not. The fetcher already
  retries with backoff, but space your work out — be a polite human, not a scraper.
- **A fetch takes ~8.5s headed.** Budget for that; don't assume sub-second.
- **Direct listing flow:** `domain.com.au/<listing_id>` goes straight to the
  listing. `domain_cli.py listing --id <id>` (no `--url`) uses it.

## Standard run (what the cron should do)

1. **Read the brief.** Parse `buyer.md`:
   ```bash
   python buyer_profile.py --show-prose
   ```
   The JSON is your search set. The prose is your scoring rubric — note the strong
   preferences and especially the **deal-breakers**.

2. **Pick suburbs.** Don't just search the three seed suburbs forever.
   - See where the money/activity is from what we've already stored:
     ```bash
     python suburb_analyzer.py --mode sale        # ranked by affordability × activity
     python suburb_analyzer.py --mode sold        # market context / comparables
     ```
   - Expand from the seeds into nearby suburbs we've observed:
     ```bash
     python suburb_analyzer.py --candidates --seed-suburb Zetland --seed-suburb Waterloo
     ```
   - Use the `region_hint` prose + your own knowledge of Sydney to add candidate
     suburbs that fit ("inner-south, walkable to CBD, near a station"). This is the
     judgement call the scripts can't make.

3. **Run the hunts.** Either the standing `hunts.json` or searches you generated
   from the buyer profile for this run's chosen suburbs:
   ```bash
   python hunt_runner.py --json          # all enabled hunts, headed, marks the run
   python hunt_runner.py --name <hunt>   # just one
   python hunt_runner.py --no-mark       # dry run, don't record (testing)
   ```
   New-since-last-run detection and all persistence happen automatically.

4. **Track sold comparables + report.** When `track_sold: true`, run the sold
   search too so the DB accrues sale evidence. Sold pages use a different shape
   (no `digitalData`, valid sort is `solddate-desc` — `soldcontractdate-desc`
   400s), all handled by the parser. Then report on it:
   ```bash
   python sales_report.py                 # scoped to buyer.md suburbs + beds, last 90d
   python sales_report.py --days 0 --all-suburbs   # everything we've stored
   python sales_report.py --json          # for programmatic use
   ```
   Exact sold price/date only come from **enriched** sold listings
   (`soldDetails.soldPrice.rawValues.exactPrice` + `soldDate.isoDate`); un-enriched
   cards fall back to the display price ("$970,000" / "Price Withheld"). Enrich a
   handful of the most relevant comparables, not all 9,000.

5. **Score the new listings (your job).** For each genuinely-new match, weigh it
   against the prose:
   - **Hard-reject** on deal-breakers (ground floor on a main road, no parking,
     flood/fire overlay). Drop these even if the numbers are great.
   - **Reward** strong preferences (secure parking, north-facing/light, balcony or
     courtyard, period charm or good new finishes, pool/gym as a bonus).
   - Enrich a shortlist with detail-page data when you need description/features/
     agent/inspection times the card doesn't have (`enrich: true` on the hunt, or
     `domain_cli.py listing --id <id>`). Enrich the *shortlist*, not everything —
     each detail page is another ~8.5s headed fetch.

6. **Report.** Send Ben a tight digest: the 3–8 listings worth his attention, why
   each fits (or the one caveat), price, beds/baths/cars, suburb, link, and next
   inspection if known. Lead with the standout. Mention market context from the
   sold/suburb stats only when it changes the story. Skip the noise — he can read a
   raw list himself; your value is the filter.

7. **Build the folio (the deliverable).** `report_builder.py` typesets a curated
   shortlist into the premium PDF dossier (`build_report(payload, out_path)`; see
   its docstring for the full payload schema and `sample_payload()`). The script is
   pure layout — **you** write every word of the narrative payload: per-property
   `verdict` / `why_it_fits` / `highlights` / `caveat` / `fit_score` / `financials`,
   and the whole of **Section 02 (the market)**. Aim for **≥10 properties** and a
   two-page Section 02 (narrative + at-a-glance table + forces strip, then
   suburb-by-suburb deep dive + outlook). Compression is baked in; folio palette is
   locked.

   **Section 02 is researched, never guessed — this is non-negotiable.** Macro
   conditions move; a number that was true last month can be flat wrong today
   (rates were *rising*, not easing, when this was written). Before writing a single
   line of `market` prose, run a live web search and confirm the current picture:
   - **RBA cash rate** — current level, the latest decision, and the near-term
     bias (more hikes? hold? cuts?). Get it from the RBA or a major-bank economist,
     not memory.
   - **Inflation (CPI)** — the latest print vs. the 2–3% target.
   - **Sydney price forecast** — a current, *named* house view (ANZ /
     CoreLogic-Cotality / Domain / SQM), with the actual % for this year and next.
   - **Local signal** — anything specific to the searched suburbs/precinct
     (oversupply, infrastructure like a new Metro, demand shifts).

   Then ground the medians/ranges in our own **sold-comp data** (`sales_report.py`),
   not round-number guesses. Every `trend`, `trajectory`, and force `signal`/`tone`
   must follow that evidence — if the data says soft, the badge says soft. Cite the
   real figures in the prose. A beautiful report built on a wrong rate call is worse
   than no report.

## buyer.md contract

- **Front-matter (hard, parsed):** `objective` (buy/rent/both), `budget.buy`/
  `budget.rent` min/max, `beds_min`/`beds_max`, `baths_min`, `cars_min`,
  `property_types`, `exclude_under_offer`, `sort`, `locations.suburbs`,
  `locations.region_hint`, `track_sold`. Edit these freely — they deterministically
  become filters.
- **Prose (soft, yours):** "What I'm actually looking for", strong preferences,
  deal-breakers. The scripts never read this; you do.

Keep the line clean: anything that can be a precise filter goes in front-matter;
anything requiring judgement goes in prose.

## Data you can lean on

The DB grows every run, so reasoning gets better over time:
- `listing_snapshots` — price/status history, so you can spot a price drop or a
  relist ("was $1.15M three weeks ago, now $1.05M" is a real signal).
- `suburb_stats` / `suburb_analyzer` — affordability and activity per suburb.
- Sold listings — actual sale evidence to sanity-check asking prices.
- `agents` — name, **email, mobile, landline**, profile, photo, agency.
- `listing_images` — every image, tagged `photo` / `floorplan` / `video` /
  `virtualtour`. `PropertyDB.listing_media(id)` returns them grouped.
- Quick health check: `python db.py count`.

**Agent contacts and images are detail-page only.** Search cards carry no agent
email/mobile and only a handful of untyped photos. To get full agent contact
details and the complete typed image gallery (property shots vs. floorplans), the
listing must be **enriched** (`enrich: true` on the hunt, or `domain_cli.py
listing --id <id>`). Enrich the shortlist, not everything — each is a ~8.5s fetch.

## Don't

- Don't run headless. Don't loop fetches without spacing them out.
- Don't widen the budget or ignore deal-breakers to pad the digest. A short honest
  list beats a long padded one.
- Don't re-surface listings Ben has already seen unless something changed (price,
  status, new inspection).
- Don't hardcode suburbs in your reasoning when the analyzer + region_hint can pick
  better ones from real data.
