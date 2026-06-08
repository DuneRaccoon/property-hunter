#!/usr/bin/env python3
"""Ad-hoc: build the Saturday PROPERTY FOLIO PDF for the 2026-06-06 inspection run.

Pulls the four shortlisted listings' stored data + decision panels from the DB,
merges in the agent's (Dune 2's) authored judgement prose and the live-researched
market section, and renders the folio via report_builder.build_report.

Read-only against the DB. Run from project root with PYTHONPATH=. and the venv.
"""
from __future__ import annotations

import json
from pathlib import Path

from db import DEFAULT_DB_PATH, PropertyDB
from buyer_profile import DEFAULT_BUYER, parse_buyer_md
from decision_engine import analyse_listing
import report_builder

OUT = Path(__file__).resolve().parent.parent / "reports" / "saturday_folio_2026-06-06.pdf"

# ---------------------------------------------------------------------------
# Authored judgement payload, keyed by listing id. Prose is Dune 2's call;
# structured facts come from the DB. The Zetland lead is fully enriched (real
# description, agent contacts, 15 photos + floorplan); the other three are from
# search-card data only (detail-page enrichment was blocked this run), so they
# are presented honestly as on-brief-but-inspect-first at lower confidence.
# ---------------------------------------------------------------------------
AUTHORED = {
    "2020878443": {
        "fit_score": 8.6,
        "verdict": "The standout. A single-level two-bedder, two bathrooms, secure parking and a sub-1km walk to Green Square \u2014 and a guide that's built to actually transact in a softening market.",
        "why_it_fits": (
            "This is as on-brief as the run produced. A single-level two-bedroom apartment in Victoria Park's "
            "'Elite' release, inside the Green Square renewal precinct, under 5km from the CBD and a short flat walk "
            "to Green Square station. It clears the hard criteria cleanly: two beds, two baths, a secure car space, "
            "air-conditioning installed, and lift access \u2014 no compromises on the deal-breakers.\n\n"
            "The growth story is the precinct itself: East Village retail, Joynton Park and the Gunyama aquatic centre "
            "are delivered, not promised, so the infrastructure premium is already in the ground rather than a bet. "
            "In-complex pool and gym give it a real rentability edge if it ever flips to an investment. At a guide that "
            "starts at $950k in a market where Sydney units have just softened, the buyer has leverage on the upper end."
        ),
        "highlights": [
            "Two bathrooms incl. main ensuite \u2014 rare at this price band",
            "Secure basement car space \u2014 clears the parking deal-breaker",
            "Under 1km flat walk to Green Square station",
            "Air-conditioning installed (a hard must-have, satisfied)",
            "Indoor pool + gym in-complex \u2014 rentability edge",
            "Opposite Joynton Park \u2014 good for Gulby",
        ],
        "caveat": (
            "Guide tops out at $1.045M \u2014 at the upper end it eats most of the $1.1M budget, leaving little for "
            "stamp duty and strata. The strata levy on a pool/gym building will be material; get the S184 and the "
            "last two AGM minutes before offering."
        ),
        "financials": {
            "est_rent_weekly": 880,
            "gross_yield_pct": 4.4,
            "strata_quarterly": 1500,
            "council_annual": 1100,
            "notes": (
                "Yield calculated on the upper guide ($1.045M) at an estimated $880/wk \u2014 in line with the Zetland "
                "two-bedder band. Strata is an estimate for a pool/gym building and is the single biggest unknown; "
                "confirm on the strata report before offering."
            ),
        },
        "enriched": True,
    },
    "2020798790": {
        "fit_score": 6.6,
        "verdict": "On-brief eastern-suburbs two-bedder at a tight guide, but the listing is thin \u2014 inspect before you fall for the postcode.",
        "why_it_fits": (
            "A two-bedroom apartment in Randwick at a $975k\u2013$985k guide sits comfortably inside budget and inside "
            "the eastern-suburbs zone Ben likes, well within the 20-minute CBD-drive ring. Two beds, one bath, one car "
            "covers the core criteria.\n\n"
            "The honest caveat: this run only captured the search-card data \u2014 no full description, no confirmed "
            "aircon, no strata figure. Aircon is a hard must-have, so its presence is the first thing to verify at the "
            "Saturday inspection. Treat the fit score as provisional until the detail page is read."
        ),
        "highlights": [
            "Eastern-suburbs (Randwick) \u2014 inside Ben's preferred zone",
            "Tight $975k\u2013$985k guide, comfortably under budget",
            "Two beds + secure parking on the card",
            "Within the 20-min CBD-drive ring",
        ],
        "caveat": (
            "Detail page wasn't enriched this run \u2014 aircon (a hard must-have), strata cost and outlook are all "
            "unconfirmed. Don't rank it above the Zetland lead until you've walked it."
        ),
        "financials": {
            "est_rent_weekly": 800,
            "gross_yield_pct": 4.2,
            "strata_quarterly": None,
            "council_annual": 1000,
            "notes": "Rent and yield are estimates from the Randwick two-bedder band. Strata unknown \u2014 card-only data.",
        },
        "enriched": False,
    },
    "2020768035": {
        "fit_score": 5.8,
        "verdict": "A one-bedder on the Pacific Highway \u2014 lower-north-shore location is right, but it's a one-bed on a main road. Walk it before you warm to it.",
        "why_it_fits": (
            "A one-bedroom apartment in Crows Nest at $919k is inside budget and squarely in the lower-north-shore "
            "pocket Ben is open to, with the Metro now running. One bed, one bath, one car.\n\n"
            "Two flags keep it provisional: a Pacific Highway address raises a main-road noise/outlook risk that needs "
            "an in-person check, and a one-bedder is the weaker capital-growth and resale profile of the shortlist. "
            "Card-only data this run, so aircon and strata are unconfirmed."
        ),
        "highlights": [
            "Crows Nest \u2014 lower north shore, on the Metro",
            "$919k, inside budget",
            "Secure car space on the card",
        ],
        "caveat": (
            "Pacific Highway = main-road noise/outlook risk; verify in person. One-bed is the weakest growth/resale "
            "profile of the four. Aircon + strata unconfirmed (card-only)."
        ),
        "financials": {
            "est_rent_weekly": 700,
            "gross_yield_pct": 4.0,
            "strata_quarterly": None,
            "council_annual": 950,
            "notes": "One-bed rent band estimate. Main-road position may soften both rent and resale. Strata unknown.",
        },
        "enriched": False,
    },
    "2020897604": {
        "fit_score": 6.2,
        "verdict": "The value play \u2014 a two-bed in Marrickville at a $775k guide. Cheapest entry on the list, but it's the western edge of the brief and the listing is bare.",
        "why_it_fits": (
            "A two-bedroom apartment in Marrickville guiding $775k is the cheapest entry of the shortlist by a wide "
            "margin, leaving real headroom under the $1.1M budget. Marrickville is the agreed inner-west boundary of "
            "the brief \u2014 on-brief, but at its western edge. Two beds, one bath, one car.\n\n"
            "Card-only data this run: no description, no confirmed aircon, no strata. The low guide is attractive but "
            "begs the question of why \u2014 read the detail and inspect before reading too much into the number."
        ),
        "highlights": [
            "Two beds at a $775k guide \u2014 most headroom under budget",
            "Marrickville \u2014 inner-west, on the brief's boundary",
            "Secure parking on the card",
        ],
        "caveat": (
            "Western edge of the brief. A guide this low under-prices the band \u2014 confirm there's no underquote or "
            "stock issue. Aircon + strata unconfirmed (card-only)."
        ),
        "financials": {
            "est_rent_weekly": 680,
            "gross_yield_pct": 4.6,
            "strata_quarterly": None,
            "council_annual": 950,
            "notes": "Higher headline yield reflects the low guide; verify the guide is genuine, not an underquote. Strata unknown.",
        },
        "enriched": False,
    },
}

ORDER = ["2020878443", "2020798790", "2020768035", "2020897604"]


def _title(s: str) -> str:
    return s if s else s


def build_property(db: PropertyDB, front, lid: str) -> dict:
    row = db.conn.execute("SELECT raw_json FROM listings WHERE id=?", (lid,)).fetchone()
    listing = json.loads(row["raw_json"])
    media = db.listing_media(lid) or {}
    authored = AUTHORED[lid]

    # Images: prefer enriched media photos; fall back to card images.
    photos = media.get("photos") or listing.get("images") or []
    floorplans = media.get("floorplans") or []
    hero = photos[0] if photos else None
    gallery = photos[1:9] if len(photos) > 1 else []

    addr = listing.get("address") or {}
    agents = [
        {k: a.get(k) for k in ("name", "mobile", "email") if a.get(k)}
        for a in (listing.get("agents") or [])
    ]
    agency = listing.get("agency") or {}
    insp = listing.get("inspection") or {}
    inspections = []
    if insp.get("openTime"):
        inspections.append({"start": insp["openTime"], "end": insp.get("closeTime")})
    elif listing.get("inspections"):
        inspections = listing["inspections"]

    # Decision panels from the engine (valuation / risk / due diligence / action).
    try:
        decision = analyse_listing(listing, front, db=db)
    except Exception:
        decision = {}

    prop = {
        "headline": listing.get("headline") or addr.get("display") or f"{authored['fit_score']}/10 candidate",
        "address": addr.get("display") or addr.get("street") or "",
        "suburb": addr.get("suburb") or listing.get("suburb") or "",
        "price": listing.get("price") or "",
        "beds": int(listing["beds"]) if listing.get("beds") is not None else None,
        "baths": int(listing["baths"]) if listing.get("baths") is not None else None,
        "cars": int(listing["cars"]) if listing.get("cars") is not None else None,
        "property_type": (listing.get("property_type") or "Apartment"),
        "url": listing.get("url") or "",
        "description": listing.get("description") or "",
        "features": listing.get("features") or [],
        "images": {"hero": hero, "gallery": gallery, "floorplan": (floorplans[0] if floorplans else None)},
        "agency": {"name": agency.get("name")} if agency.get("name") else {},
        "agents": agents,
        "inspections": inspections,
        "fit_score": authored["fit_score"],
        "verdict": authored["verdict"],
        "why_it_fits": authored["why_it_fits"],
        "highlights": authored["highlights"],
        "caveat": authored["caveat"],
        "financials": authored["financials"],
    }
    # Spread decision panels at top level (report_builder._decision_panels reads them there).
    for k in ("valuation", "risks", "due_diligence", "action_plan"):
        if decision.get(k):
            prop[k] = decision[k]
    return prop


def market_section() -> dict:
    return {
        "standfirst": (
            "This is a selective-buyer market, not a chase-anything market. The RBA cash rate is 4.35% after the "
            "5 May hike, the next decision is due 16 June, and April CPI is still 4.2% year-on-year versus the "
            "2-3% target band. Domain's latest Sydney read is mixed: houses have stalled under rate pressure, but "
            "units are still holding up because affordability is pushing buyers down the price ladder."
        ),
        "overview": (
            "The clean read is that borrowing capacity is capped, vendor confidence is thinner, and Sydney buyers are "
            "more price-sensitive. That helps Ben if he stays disciplined below $1.1M, but it does not make every "
            "apartment cheap. Domain's March-quarter report had Sydney houses edging down 0.04% while Sydney units "
            "rose 0.6% to a record $848,227 and 3.5% over the year. Domain's 2026 forecast still expects national "
            "unit prices to rise about 5%, but its own economists now flag a two-half year with rate-hike risk taking "
            "heat out of the second half. Translation: inspect the best assets, negotiate hard, and do not stretch for "
            "generic high-density stock just because the guide looks neat."
        ),
        "forces": [
            {
                "label": "Cash rate",
                "signal": "4.35% - restrictive",
                "tone": "negative",
                "body": (
                    "The RBA increased the cash rate by 25bp on 5 May to 4.35%, with the next decision due at 2.30pm "
                    "AEST on 16 June. The May statement says inflation risks remain tilted to the upside, so the bias is "
                    "restrictive, not easing."
                ),
            },
            {
                "label": "Inflation",
                "signal": "CPI 4.2% y/y",
                "tone": "negative",
                "body": (
                    "ABS April CPI eased from March's 4.6% to 4.2% year-on-year, but it is still above the RBA's 2-3% "
                    "target band. Housing inflation was 6.3% y/y, so apartment holding costs remain a real part of the "
                    "decision."
                ),
            },
            {
                "label": "Sydney values",
                "signal": "units +0.6% q/q",
                "tone": "mixed",
                "body": (
                    "Domain's March-quarter report showed Sydney houses stalling, but units rose 0.6% for the quarter "
                    "and 3.5% annually. Affordable units are more resilient than expensive houses, but buyers are more "
                    "selective."
                ),
            },
            {
                "label": "Units vs houses",
                "signal": "budget end supported",
                "tone": "positive",
                "body": (
                    "Domain's 2026 forecast expects unit prices to rise about 5% nationally and notes affordability is "
                    "pushing demand toward units. That supports Ben's sub-$1.1M brief, provided the building quality is "
                    "not generic oversupply."
                ),
            },
        ],
        "suburbs": [
            {"name": "Zetland", "median": "~$1.0M", "range": "$0.95M\u2013$1.10M", "trend": "steady",
             "note": "Most active of the shortlist; consistent new stock keeps two-bedder pricing honest. Lead market."},
            {"name": "Randwick", "median": "~$1.0M", "range": "$0.90M\u2013$1.15M", "trend": "firm",
             "note": "Eastern-suburbs tightly held; fewer sub-$1M two-bedders trade, so a tight guide is worth a look."},
            {"name": "Crows Nest", "median": "~$0.92M", "range": "$0.80M\u2013$1.05M", "trend": "steady",
             "note": "Metro-led demand; one-bedders entry-priced but weaker growth profile than two-beds."},
            {"name": "Marrickville", "median": "~$0.85M", "range": "$0.72M\u2013$0.98M", "trend": "value",
             "note": "Inner-west value edge of the brief; cheapest two-bed entry but verify low guides aren't underquotes."},
        ],
        "outlook": (
            "Base case for the next fortnight: do not chase. Use the 16 June RBA decision as the next macro checkpoint, "
            "lead with the fully-enriched Zetland candidates, and treat card-only listings as inspection comparables "
            "until Domain detail pages can be refreshed. The run found no fresh parsed search results today because "
            "Domain served access-denied pages; the folio is therefore a curated market-memory folio, not a clean "
            "all-fresh scrape."
        ),
        "sources": [
            {"key": "rba_cash_rate", "label": "RBA cash rate", "value": "4.35% (hold/hike live 16 Jun)",
             "source_name": "Reserve Bank of Australia", "source_url": "https://www.rba.gov.au/",
             "published_at": "2026-05-05", "observed_at": "2026-06-06", "freshness_days": 60},
            {"key": "abs_cpi", "label": "CPI inflation (y/y)", "value": "4.2% headline in April 2026",
             "source_name": "Australian Bureau of Statistics", "source_url": "https://www.abs.gov.au/",
             "published_at": "2026-05-27", "observed_at": "2026-06-06", "freshness_days": 90},
            {"key": "domain_sydney_units", "label": "Sydney units", "value": "+0.6% q/q, +3.5% y/y",
             "source_name": "Domain House Price Report - March 2026", "source_url": "https://www.domain.com.au/research/house-price-report/march-2026/",
             "published_at": "2026-04-30", "observed_at": "2026-06-06", "freshness_days": 90},
            {"key": "domain_forecast", "label": "2026 unit forecast", "value": "Domain: units about +5% nationally",
             "source_name": "Domain 2026 forecast coverage", "source_url": "https://www.domain.com.au/news/more-subdued-what-property-prices-will-do-in-2026-1470724/",
             "published_at": "2026-01-05", "observed_at": "2026-06-06", "freshness_days": 180},
        ],
    }


def main() -> int:
    front, _ = parse_buyer_md(DEFAULT_BUYER)
    with PropertyDB(DEFAULT_DB_PATH) as db:
        props = [build_property(db, front, lid) for lid in ORDER]

    payload = {
        "meta": {
            "title": "The Saturday Folio",
            "issue": "Vol. I \u00b7 6 June 2026",
            "eyebrow": "A CURATED BUYING DOSSIER",
            "date": "6 June 2026",
            "prepared_for": "Ben",
            "prepared_by": "Dune 2 \u00b7 Buying Agent",
            "standfirst": (
                "Four candidates worth comparing, led by a single-level Zetland apartment that clears the brief "
                "outright. Today's Domain search pages were blocked, so this folio is useful but not clean-room fresh."
            ),
            "closing": (
                "Compiled by your buying agent on the Pi from this morning's run. Domain returned hard access-denied "
                "pages for the live search and one test detail refresh, so the property layer is curated from stored "
                "market memory. The Zetland lead is fully enriched; the other three are from search-card data only. "
                "Confirm aircon, strata and outlook in person. "
                "Yields and outgoings are estimates; verify strata, council and contract terms before offering. "
                "Re-test the macro after the 16 June RBA decision."
            ),
        },
        "brief": {
            "objective": "buy",
            "budget_buy": 1100000,
            "budget_rent": None,
            "beds": 2,
            "baths": 1,
            "cars": 1,
            "region": "Inner-south / eastern / lower north shore / inner-west (to Marrickville), within ~20min of the CBD",
            "prose": (
                "Owner-occupier with capital-growth priority. A low-maintenance 1\u20132 bedroom apartment under $1.1M, "
                "aircon essential (installed or installable), secure parking, within roughly a 20-minute drive of the "
                "CBD. Open across the inner-south (lives in Zetland, likes it), eastern suburbs, lower north shore and "
                "the inner-west as far as Marrickville \u2014 not the outer/greater west."
            ),
            "must_haves": [
                "Air-conditioning (installed or installable)",
                "Secure car space",
                "Within ~20 min drive of the CBD",
                "1\u20132 bedrooms, under $1.1M",
            ],
            "deal_breakers": [
                "Outer / greater west",
                "No parking",
                "Flood / fire / heritage overlay",
            ],
        },
        "market": market_section(),
        "properties": props,
    }

    OUT.parent.mkdir(exist_ok=True)
    report_builder.build_report(payload, str(OUT), palette="folio")
    print(f"OK wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
