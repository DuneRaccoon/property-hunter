#!/usr/bin/env python3
"""Build the Saturday deep PROPERTY FOLIO PDF — run dated 2026-06-08.

Curated 12-property shortlist for Ben's buy brief. Structured facts come from the
DB (raw_json + decision panels); all judgement prose + the live-researched Section
02 market are authored by Dune 2. Six listings are fully enriched (real
description, agent contacts, full gallery + floorplan); the other six are from
search-card data only (detail-page enrichment was Akamai-blocked this run after a
crash + rate-limit), so they are presented honestly as on-brief-but-inspect-first
at lower confidence.

Read-only against the DB. Run from project root with the venv active.
"""
from __future__ import annotations

import json
from pathlib import Path

from db import DEFAULT_DB_PATH, PropertyDB
from buyer_profile import DEFAULT_BUYER, parse_buyer_md
from decision_engine import analyse_listing
import report_builder

OUT = Path(__file__).resolve().parent.parent / "reports" / "saturday_folio_2026-06-08.pdf"

# ---------------------------------------------------------------------------
# Authored judgement payload, keyed by listing id.
# ---------------------------------------------------------------------------
AUTHORED = {
    # ---------------- ZETLAND (enriched) ----------------
    "2020884546": {
        "fit_score": 8.8,
        "verdict": "The lead. A garden-aspect two-bed two-bath in Zetland with reverse-cycle air-con already in — the hard must-have isn't a maybe, it's printed in the listing.",
        "why_it_fits": (
            "This is the cleanest match the run produced. A first-floor two-bedroom, two-bathroom apartment in "
            "Zetland — the suburb you already live in and like — overlooking the complex's manicured central gardens "
            "rather than a road or a light well. It clears every hard criterion: two beds, two baths, secure car "
            "space, and crucially the description spells out 'reverse cycle a/c', so air-conditioning (your one "
            "non-negotiable) is confirmed installed, not a strata gamble.\n\n"
            "Beyond the checklist it has the things that hold value: high ceilings, engineered floors, a wall of "
            "floor-to-ceiling sliders feeding a generous covered balcony, and a stone gas kitchen. The back-of-block "
            "position is the quiet, light end of the building — exactly the tightly-held kind of stock that outperforms "
            "the generic tower product the market is wary of right now."
        ),
        "highlights": [
            "Reverse-cycle air-con confirmed in the listing — must-have satisfied",
            "Two baths incl. ensuite + secure car space — clears every hard criterion",
            "Garden-aspect, back-of-block: quiet, light, tightly-held position",
            "Covered balcony + floor-to-ceiling glass, high ceilings",
            "Zetland — your suburb, walkable to Green Square + East Village",
            "Guide starts $950k — real headroom under the $1.1M cap",
        ],
        "caveat": (
            "Resort-style complex (pool/gym) means a material strata levy — pull the S184 certificate and the last two "
            "AGM minutes before offering, and confirm the actual quarterly figure feeds your holding-cost maths.",
        ),
        "financials": {
            "est_rent_weekly": 880,
            "gross_yield_pct": 4.4,
            "strata_quarterly": 1400,
            "council_annual": 1100,
            "notes": (
                "Yield on the upper guide ($1.045M) at ~$880/wk, in line with the Zetland two-bed band. Strata is an "
                "estimate for an amenities building and is the biggest holding-cost unknown — verify on the report."
            ),
        },
        "enriched": True,
    },
    "2020745119": {
        "fit_score": 8.7,
        "verdict": "The space-and-value pick. 104sqm on title — a corner two-bedder in 'Symphony' guiding $900k–$990k, the most apartment-per-dollar on the list.",
        "why_it_fits": (
            "A prime corner position in the 'Symphony' building with an unusually large 104sqm on title — for a "
            "two-bed apartment under $1M that is genuine scale, not a compact two-bedder dressed up. Two beds, two "
            "baths, secure parking; light pours in through walls of glass that open to a green-fringed balcony.\n\n"
            "The appeal is the price-to-size ratio in a softening unit market: you're buying the bottom of the Zetland "
            "two-bed band ($900k–$990k guide) for an above-average floorplate. Oversized living, a stone-and-gas "
            "kitchen with island bench, and two big robed bedrooms make it a comfortable owner-occupier home rather "
            "than investor-grade minimum. Confirm air-con at inspection — near-new Zetland stock almost always has it, "
            "but the description doesn't put it beyond doubt."
        ),
        "highlights": [
            "104sqm on title — biggest floorplate per dollar on the list",
            "Corner position, dual-aspect light, green-fringed balcony",
            "Two baths + secure car space",
            "$900k–$990k guide — bottom of the Zetland two-bed band",
            "Stone/gas island kitchen, high ceilings, oversized rooms",
        ],
        "caveat": (
            "Air-con isn't explicitly named in the description (it lists finishes, not climate) — verify the split "
            "system on inspection. Guide last refreshed 3 Jun; reconfirm it hasn't moved.",
        ),
        "financials": {
            "est_rent_weekly": 900,
            "gross_yield_pct": 4.7,
            "strata_quarterly": 1300,
            "council_annual": 1050,
            "notes": (
                "Yield on the $990k upper guide at ~$900/wk — the large floorplate supports the upper rent band. "
                "Strata estimate for the building; confirm on the S184."
            ),
        },
        "enriched": True,
    },
    "2020878443": {
        "fit_score": 8.5,
        "verdict": "Infrastructure-in-the-ground play. Single-level two-bed two-bath in Victoria Park's 'Elite', <5km to the CBD, with the precinct premium already delivered, not promised.",
        "why_it_fits": (
            "A single-level two-bedroom, two-bathroom apartment in the 'Elite' release of Victoria Park, inside the "
            "Green Square renewal precinct and under 5km from the CBD. Two beds, two baths, secure parking, lift "
            "access — no deal-breakers tripped.\n\n"
            "The growth case is the precinct itself: East Village retail, Joynton Park and the Gunyama aquatic centre "
            "are built and open, so the infrastructure premium is in the ground rather than a forecast. In-complex "
            "pool and gym give it a rentability edge if it ever flips to investment. At a guide starting $950k in a "
            "market where Sydney units have just softened, there's leverage to negotiate the top end down."
        ),
        "highlights": [
            "Single-level two-bed two-bath + secure car space",
            "Victoria Park 'Elite' — Green Square renewal precinct, <5km CBD",
            "Delivered amenity: East Village, Joynton Park, Gunyama pool",
            "Air-con installed, lift access",
            "Pool + gym in-complex — rentability edge",
        ],
        "caveat": (
            "Guide tops at $1.045M — at the upper end it eats most of the budget before stamp duty and strata. The "
            "pool/gym levy is material; get the S184 and last two AGMs before offering.",
        ),
        "financials": {
            "est_rent_weekly": 880,
            "gross_yield_pct": 4.4,
            "strata_quarterly": 1500,
            "council_annual": 1100,
            "notes": "Yield on the $1.045M upper guide at ~$880/wk. Strata for an amenities building is the key unknown.",
        },
        "enriched": True,
    },
    "2020887612": {
        "fit_score": 8.0,
        "verdict": "The premium one. A dual-level whole-floor-master two-bedder with CBD views and three balconies — but the guide runs to the very top of budget.",
        "why_it_fits": (
            "Flowing over two levels with district-to-CBD views, this is the most 'home' of the Zetland set: a "
            "whole-floor master with ensuite, robes, balcony and a study nook, a second robed bedroom, open-plan "
            "living opening to an alfresco balcony, and a stone-wrapped gas kitchen with European appliances. Two "
            "beds, two baths, secure parking, three balconies, air-con — it satisfies the brief and then some.\n\n"
            "It's the lifestyle/aspect upgrade of the list. The trade is price: the $1.0M–$1.1M guide spends the whole "
            "budget, so it only makes sense if the view and the dual-level format genuinely beat the cheaper "
            "single-level options for you."
        ),
        "highlights": [
            "Dual-level, dual-aspect with CBD/district views",
            "Whole-floor master: ensuite, robes, balcony, study nook",
            "Three balconies, alfresco living, European stone/gas kitchen",
            "Two baths + secure parking, air-con",
        ],
        "caveat": (
            "$1.0M–$1.1M guide leaves nothing for stamp duty/strata under the $1.1M cap — you'd be buying at the "
            "ceiling. Dual-level can mean stairs/heat zoning; check the climate setup across both floors.",
        ),
        "financials": {
            "est_rent_weekly": 950,
            "gross_yield_pct": 4.5,
            "strata_quarterly": 1500,
            "council_annual": 1150,
            "notes": "Yield on the $1.1M ceiling at ~$950/wk; the view and format command the upper rent band. Verify strata.",
        },
        "enriched": True,
    },
    "2020119175": {
        "fit_score": 7.6,
        "verdict": "On-brief and near-new, but pitched hard at investors and sold furnished — a fine home, just read the marketing with a cold eye.",
        "why_it_fits": (
            "A near-new (≈1 year old) fully-furnished two-bed two-bath in the heart of Zetland, 300m to Gunyama Park, "
            "500m to the Gunyama aquatic centre and 1.2km to Green Square station with its direct CBD and airport "
            "line. Two beds, two baths, secure parking, air-con — it ticks the hard criteria and sits in the precinct "
            "you know.\n\n"
            "The honest read: the listing leans heavily on 'high rental yield', is sold furnished, and the contact is "
            "a rentals manager. None of that disqualifies it as an owner-occupier home, but it signals investor stock "
            "in a building that may carry a higher investor-tenant mix — relevant to both strata behaviour and "
            "resale. Buy the apartment, not the yield pitch."
        ),
        "highlights": [
            "Near-new (~1yr) two-bed two-bath, fully furnished",
            "300m to Gunyama Park, 1.2km to Green Square station",
            "Air-con installed, secure parking",
            "Direct CBD + airport rail line",
        ],
        "caveat": (
            "Heavily investor-pitched ('high rental yield', furnished, rentals-manager contact) — check the "
            "owner-occupier/investor ratio and whether furniture is included or a separate negotiation. Higher "
            "investor mix can mean more rental churn next door.",
        ),
        "financials": {
            "est_rent_weekly": 950,
            "gross_yield_pct": 4.7,
            "strata_quarterly": 1300,
            "council_annual": 1050,
            "notes": "Furnished can lift gross rent (~$950/wk) but depreciates; for owner-occupier use, weight the home, not the yield.",
        },
        "enriched": True,
    },
    # ---------------- EASTERN SUBURBS ----------------
    "2020774371": {
        "fit_score": 8.2,
        "verdict": "Eastern-suburbs scarcity. A leafy 'Yalara' two-bedder with sea glimpses, pool and lift, sitting between Randwick village and Coogee Beach.",
        "why_it_fits": (
            "On level three of the landmark 'Yalara', a bright two-bedroom apartment with leafy sea glimpses through "
            "every window, opening to a balcony over peaceful gardens, with onsite pool, leisure facilities and lift "
            "access. It adjoins Fred Hollows Reserve and sits partway between Coogee Beach and Randwick village — "
            "blue-chip eastern-suburbs land that is tightly held and historically a strong capital-growth performer.\n\n"
            "Two beds, one bath, secure car space, lift. The one-bathroom format is the only ding against the "
            "two-bath Zetland leads, but the location quality and scarcity are a notch above. At a $1.05M asking it's "
            "near the top of budget, so negotiate on the strata and any dated interiors."
        ),
        "highlights": [
            "Randwick — tightly-held eastern suburbs, strong long-run growth",
            "Sea glimpses, leafy reserve aspect, pool + lift in building",
            "Between Coogee Beach and Randwick village",
            "Secure car space, balcony",
            "Multiple inspections incl. 11 Jun — easy to walk",
        ],
        "caveat": (
            "One bathroom only, and 'lightly refreshed' interiors may want updating. $1.05M is near the ceiling; "
            "confirm air-con (not explicitly named) and the strata for a pool building before offering.",
        ),
        "financials": {
            "est_rent_weekly": 850,
            "gross_yield_pct": 4.2,
            "strata_quarterly": 1600,
            "council_annual": 1000,
            "notes": "Yield on $1.05M at ~$850/wk. Pool building = higher strata; the eastern-suburbs land value is the real return driver.",
        },
        "enriched": True,
    },
    # ---------------- LOWER NORTH SHORE (card-level) ----------------
    "2020877241": {
        "fit_score": 6.8,
        "verdict": "Blue-chip lower-north-shore two-bedder at a $950k guide — the location is right; the listing detail isn't in hand yet.",
        "why_it_fits": (
            "A two-bedroom apartment in Cammeray guiding $950k — comfortably inside budget and in exactly the "
            "lower-north-shore pocket you're open to. Cammeray is leafy, tightly held and well-served (Miller Street "
            "village, easy CBD access via the future Victoria Cross Metro at North Sydney), the kind of scarcity-backed "
            "address that defends value in a soft market.\n\n"
            "Two beds, one bath, secure parking on the card. The catch is information: Domain's detail page was blocked "
            "this run, so there's no full description, no confirmed air-con and no strata figure. On fundamentals it "
            "belongs on the list; rank it once you've read the detail and walked it (inspection 6 & 10 Jun)."
        ),
        "highlights": [
            "Cammeray — blue-chip lower north shore, tightly held",
            "$950k guide, inside budget for a two-bed",
            "Secure car space on the card",
            "Inspections 6 & 10 June",
        ],
        "caveat": (
            "Card-only data: air-con (a hard must-have), strata and aspect all unconfirmed. Don't rank above the "
            "enriched leads until the detail page and an inspection confirm it.",
        ),
        "financials": {
            "est_rent_weekly": 800,
            "gross_yield_pct": 4.4,
            "strata_quarterly": None,
            "council_annual": 1000,
            "notes": "Rent/yield estimated from the Cammeray two-bed band. Strata unknown — card-only data.",
        },
        "enriched": False,
    },
    "2020620410": {
        "fit_score": 6.7,
        "verdict": "Neutral Bay two-bedder at $995k — harbourside-fringe lifestyle inside budget, but it's a card-only listing for now.",
        "why_it_fits": (
            "A two-bedroom apartment in Neutral Bay at a $995k asking — inside budget, on the lower north shore, with "
            "the harbour, Military Road dining and a quick CBD hop via bus/ferry. It's a lifestyle-and-scarcity address "
            "that historically holds value well.\n\n"
            "Two beds, one bath, secure parking on the card. As with the other north-shore picks this run, the detail "
            "page didn't enrich, so air-con and strata are unconfirmed. There's an inspection on 9 June — the soonest "
            "of the list — so it's an easy one to verify first."
        ),
        "highlights": [
            "Neutral Bay — lower north shore, harbour-fringe lifestyle",
            "$995k, inside budget for a two-bed",
            "Secure car space on the card",
            "Inspection 9 June — soonest on the list",
        ],
        "caveat": (
            "Card-only: air-con and strata unconfirmed. One bathroom. Verify the aspect (some Neutral Bay blocks face "
            "busy roads) at the 9 June inspection.",
        ),
        "financials": {
            "est_rent_weekly": 820,
            "gross_yield_pct": 4.3,
            "strata_quarterly": None,
            "council_annual": 1000,
            "notes": "Rent/yield from the Neutral Bay two-bed band. Strata unknown — card-only.",
        },
        "enriched": False,
    },
    "2020768035": {
        "fit_score": 6.0,
        "verdict": "Crows Nest one-bedder at $919k on the Metro — right pocket, but a one-bed is the weaker growth and resale profile.",
        "why_it_fits": (
            "A one-bedroom apartment in Crows Nest at $919k, inside budget and in a lower-north-shore precinct that "
            "just got a structural demand upgrade with the Crows Nest Metro station open. One bed, one bath, secure "
            "car space.\n\n"
            "Two reasons it sits mid-pack: a one-bedder is the weakest capital-growth and resale profile of the "
            "shortlist for an owner-occupier, and it's card-only this run so air-con and strata are unconfirmed. The "
            "Metro tailwind is real, but a two-bed in the same suburb would compound better."
        ),
        "highlights": [
            "Crows Nest — lower north shore, Metro now open",
            "$919k, inside budget",
            "Secure car space on the card",
            "Inspections 4 & 6 June",
        ],
        "caveat": (
            "One-bed = weakest growth/resale of the list. Card-only: air-con + strata unconfirmed. Check the building "
            "isn't directly on the Pacific Highway noise line.",
        ),
        "financials": {
            "est_rent_weekly": 680,
            "gross_yield_pct": 3.9,
            "strata_quarterly": None,
            "council_annual": 950,
            "notes": "One-bed rent band estimate. Metro proximity supports rent; growth profile thinner than the two-beds.",
        },
        "enriched": False,
    },
    "2020879079": {
        "fit_score": 5.8,
        "verdict": "North Sydney one-bedder guiding $1,080k — premium address, but paying near-budget for a one-bed is weak value.",
        "why_it_fits": (
            "A one-bedroom apartment in North Sydney guiding $1,080k — a premium lower-north-shore location set to "
            "benefit from the Victoria Cross Metro and the precinct's commercial renewal. One bed, one bath, secure "
            "car space.\n\n"
            "It's on the list for location, but it's the weakest value: a $1,080k guide spends almost the whole budget "
            "on a one-bedroom, where the same money buys a two-bed two-bath in Zetland with better growth and resale "
            "breadth. Include it as a comparison point, not a front-runner. Card-only, so air-con/strata unconfirmed."
        ),
        "highlights": [
            "North Sydney — premium LNS, Victoria Cross Metro precinct",
            "Secure car space on the card",
            "Inspection 6 June",
        ],
        "caveat": (
            "Near-budget price for a one-bed = thin value vs the two-bed leads. Card-only: air-con + strata "
            "unconfirmed. Only pursue if the specific building/aspect is exceptional.",
        ),
        "financials": {
            "est_rent_weekly": 750,
            "gross_yield_pct": 3.6,
            "strata_quarterly": None,
            "council_annual": 1000,
            "notes": "Lowest yield on the list — a one-bed at a two-bed price. Location is the only argument; growth profile is thin.",
        },
        "enriched": False,
    },
    # ---------------- INNER-WEST / EASTERN VALUE (card-level) ----------------
    "2020897604": {
        "fit_score": 6.2,
        "verdict": "The value play. A two-bed in Marrickville guiding $775k — most headroom under budget, but it's the western edge of the brief and the listing is bare.",
        "why_it_fits": (
            "A two-bedroom apartment in Marrickville guiding $775k — the cheapest entry by a wide margin, leaving real "
            "headroom under the $1.1M cap. Marrickville is the agreed inner-west boundary of the brief: on-brief, but "
            "at its western edge, with strong cafe/transport amenity and the Metro line through Marrickville lifting "
            "connectivity. Two beds, one bath, secure parking.\n\n"
            "Card-only this run — no description, no confirmed air-con, no strata. A guide this far below the band begs "
            "the question of why (stock, aspect, or an underquote pitch), so read the detail and inspect before "
            "reading the number as a bargain."
        ),
        "highlights": [
            "Two beds at $775k — most headroom under budget",
            "Marrickville — inner-west, Metro-connected, on the brief boundary",
            "Secure parking on the card",
            "Inspections 6 & 11 June",
        ],
        "caveat": (
            "Western edge of the brief. A guide this low under-prices the band — confirm it's not an underquote or a "
            "stock issue. Air-con + strata unconfirmed (card-only).",
        ),
        "financials": {
            "est_rent_weekly": 720,
            "gross_yield_pct": 4.8,
            "strata_quarterly": None,
            "council_annual": 950,
            "notes": "Highest headline yield reflects the low guide — verify the guide is genuine, not an underquote. Strata unknown.",
        },
        "enriched": False,
    },
    "2020897532": {
        "fit_score": 6.0,
        "verdict": "Waverley one-bedder, auction guide $750k — eastern-suburbs value entry, but a one-bed and card-only.",
        "why_it_fits": (
            "A one-bedroom apartment in Waverley with an auction guide of $750k — an affordable foothold in the "
            "eastern suburbs, walking distance to Bondi Junction's transport and retail and a short run to the "
            "beaches. One bed, one bath, secure car space.\n\n"
            "It's a value/location entry rather than a growth engine: one-bed format caps the upside, and auction "
            "guides in this market frequently clear above the number. Card-only this run, so air-con and strata are "
            "unconfirmed. Worth a look as an eastern-suburbs comparable; not a lead."
        ),
        "highlights": [
            "Waverley — eastern suburbs, walk to Bondi Junction",
            "Auction guide $750k — affordable entry",
            "Secure car space on the card",
            "Inspections 6 & 11 June",
        ],
        "caveat": (
            "One-bed = capped growth. Auction guide often clears above; set a hard limit. Card-only: air-con + strata "
            "unconfirmed.",
        ),
        "financials": {
            "est_rent_weekly": 650,
            "gross_yield_pct": 4.5,
            "strata_quarterly": None,
            "council_annual": 900,
            "notes": "Yield on the $750k guide at ~$650/wk; expect the auction to clear above guide. Strata unknown.",
        },
        "enriched": False,
    },
}

ORDER = [
    "2020884546",  # Zetland Joynton — LEAD (aircon confirmed)
    "2020745119",  # Zetland Rose Valley — space/value
    "2020878443",  # Zetland Gadigal — Elite/precinct
    "2020774371",  # Randwick Alison — eastern scarcity
    "2020887612",  # Zetland O'Dea — premium dual-level
    "2020119175",  # Zetland Letitia — near-new (investor caveat)
    "2020877241",  # Cammeray — LNS card
    "2020620410",  # Neutral Bay — LNS card
    "2020897604",  # Marrickville — value card
    "2020768035",  # Crows Nest — LNS 1-bed card
    "2020897532",  # Waverley — eastern 1-bed card
    "2020879079",  # North Sydney — premium 1-bed card
]


def build_property(db: PropertyDB, front, lid: str) -> dict:
    row = db.conn.execute("SELECT raw_json FROM listings WHERE id=?", (lid,)).fetchone()
    listing = json.loads(row["raw_json"])
    media = db.listing_media(lid) or {}
    authored = AUTHORED[lid]

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
    # Pull stored inspections from the DB if raw_json carries none.
    if not inspections:
        rows = db.conn.execute(
            "SELECT start_time, end_time FROM inspections WHERE listing_id=? ORDER BY start_time", (lid,)
        ).fetchall()
        inspections = [{"start": r["start_time"], "end": r["end_time"]} for r in rows]

    # Pull stored agent contacts (mobile/email) from the DB when raw_json lacks them.
    if not any(a.get("mobile") or a.get("email") for a in agents):
        rows = db.conn.execute(
            """SELECT ag.name, ag.mobile, ag.landline, ag.email FROM agents ag
                 JOIN listing_agents la ON la.agent_id = ag.id WHERE la.listing_id=?""",
            (lid,),
        ).fetchall()
        merged = []
        for r in rows:
            d = {"name": r["name"]}
            if r["mobile"] or r["landline"]:
                d["mobile"] = r["mobile"] or r["landline"]
            if r["email"]:
                d["email"] = r["email"]
            if d.get("name"):
                merged.append(d)
        # de-dupe by name, prefer entries with contact
        best = {}
        for d in merged:
            n = d["name"]
            if n not in best or (d.get("mobile") or d.get("email")):
                best[n] = d
        if best:
            agents = list(best.values())

    description = listing.get("description") or ""

    try:
        decision = analyse_listing(listing, front, db=db)
    except Exception:
        decision = {}

    prop = {
        "headline": listing.get("headline") or addr.get("display") or addr.get("street") or f"{authored['fit_score']}/10 candidate",
        "address": addr.get("display") or addr.get("street") or (addr.get("suburb") or ""),
        "suburb": addr.get("suburb") or listing.get("suburb") or "",
        "price": listing.get("price") or "",
        "beds": int(listing["beds"]) if listing.get("beds") is not None else None,
        "baths": int(listing["baths"]) if listing.get("baths") is not None else None,
        "cars": int(listing["cars"]) if listing.get("cars") is not None else None,
        "property_type": (listing.get("property_type") or "Apartment"),
        "url": listing.get("url") or "",
        "description": description,
        "features": listing.get("features") or [],
        "images": {"hero": hero, "gallery": gallery, "floorplan": (floorplans[0] if floorplans else None)},
        "agency": {"name": agency.get("name")} if isinstance(agency, dict) and agency.get("name") else {},
        "agents": agents,
        "inspections": inspections,
        "fit_score": authored["fit_score"],
        "verdict": authored["verdict"],
        "why_it_fits": authored["why_it_fits"],
        "highlights": authored["highlights"],
        "caveat": authored["caveat"][0] if isinstance(authored["caveat"], tuple) else authored["caveat"],
        "financials": authored["financials"],
    }
    for k in ("valuation", "risks", "due_diligence", "action_plan"):
        if decision.get(k):
            prop[k] = decision[k]
    return prop


def market_section() -> dict:
    return {
        "standfirst": (
            "This is a selective-buyer's market, and the macro backdrop is working in a disciplined buyer's favour. "
            "The RBA cash rate is 4.35% after the May hike — the third increase of 2026 — with the next decision live "
            "on 16 June and the Bank still flagging upside inflation risk. April CPI is 4.2% year-on-year, above the "
            "2–3% target, with headline tipped to peak near 4.8% mid-year. Borrowing capacity is capped and vendor "
            "confidence is thinner, which rewards patience below $1.1M."
        ),
        "overview": (
            "Rates are rising, not easing — that is the single most important fact for this campaign, and it caps how "
            "much anyone can pay. Yet Sydney units are the resilient end of the market: affordability is pushing "
            "buyers down the price ladder into apartments, the rental vacancy rate has tightened to 1.1% (from 1.3% a "
            "year ago), and the major forecasters still pencil in unit growth for the year — Domain at about +7% "
            "(toward a ~$892k city median), with the broader pack at +5–6.5%. The dissenting voice is ANZ, which now "
            "sees Sydney values dipping ~0.7% across 2026 under rate pressure before bouncing ~2.6% in 2027. Read "
            "together: the budget end is supported but not booming, so inspect the best assets, negotiate hard on the "
            "top of every guide, and do not stretch for generic high-density stock just because the number looks neat."
        ),
        "forces": [
            {
                "label": "Cash rate",
                "signal": "4.35% — rising",
                "tone": "negative",
                "body": (
                    "The RBA lifted the cash rate to 4.35% in May, its third hike of 2026, fully unwinding last year's "
                    "easing. The next decision is on 16 June; the Bank says inflation risks remain tilted to the "
                    "upside, so the bias is hold-to-hawkish, not easing. Borrowing capacity stays capped."
                ),
            },
            {
                "label": "Inflation",
                "signal": "CPI 4.2% y/y",
                "tone": "negative",
                "body": (
                    "April CPI eased to 4.2% from March's 4.6%, but trimmed-mean (core) actually ticked up to 3.4% — "
                    "both above the 2–3% target. Headline is tipped to peak ~4.8% mid-2026 and underlying to stay "
                    "above 3% until mid-2027, keeping the RBA cautious and holding costs elevated."
                ),
            },
            {
                "label": "Sydney units",
                "signal": "forecast +5–7% '26",
                "tone": "positive",
                "body": (
                    "Domain forecasts Sydney units up ~7% in 2026 toward a ~$892k median; the broader pack sits at "
                    "+5–6.5%, driven by affordability switching and investor demand. Units are the resilient budget "
                    "end — exactly Ben's brief — provided the building isn't generic oversupply."
                ),
            },
            {
                "label": "Rental vacancy",
                "signal": "1.1% — very tight",
                "tone": "positive",
                "body": (
                    "SQM data has Sydney's vacancy rate at 1.1% in April, down from 1.3% a year ago. A sub-1.5% "
                    "vacancy underpins rents and gives any of these apartments a genuine fallback as an investment if "
                    "plans change — and supports the ~4.2–4.8% gross yields on this list."
                ),
            },
        ],
        "suburbs": [
            {"name": "Zetland", "median": "~$1.0M", "range": "$0.90M–$1.10M", "trend": "steady",
             "note": "Most active of the shortlist (5 of 12 picks). Consistent new stock keeps two-bedder pricing honest "
                     "— favour tightly-held, garden-aspect positions over generic tower product, which is the oversupply risk here."},
            {"name": "Randwick", "median": "~$1.05M", "range": "$0.90M–$1.20M", "trend": "firm",
             "note": "Eastern-suburbs land, tightly held; fewer sub-$1M two-bedders trade, so a $1.05M asking for a "
                     "pool/lift building with sea glimpses is worth the inspection. Strongest long-run growth pocket on the list."},
            {"name": "Cammeray / Neutral Bay", "median": "~$0.97M", "range": "$0.90M–$1.10M", "trend": "firm",
             "note": "Blue-chip lower north shore, scarcity-backed. Both card-only this run — fundamentals put them on "
                     "the list; confirm air-con and strata before ranking. Victoria Cross Metro lifts the whole precinct."},
            {"name": "Crows Nest / North Sydney", "median": "~$0.95M", "range": "$0.80M–$1.10M", "trend": "steady",
             "note": "Metro-led demand now live. One-bedders are entry-priced but the weaker growth/resale profile — a "
                     "two-bed compounds better for the same budget."},
            {"name": "Marrickville", "median": "~$0.82M", "range": "$0.70M–$0.98M", "trend": "value",
             "note": "Inner-west value edge of the brief; cheapest two-bed entry ($775k guide) but verify low guides "
                     "aren't underquotes, and that it's not past the western boundary in feel."},
            {"name": "Waverley", "median": "~$0.80M", "range": "$0.70M–$0.95M", "trend": "value",
             "note": "Eastern-suburbs value near Bondi Junction transport. One-bed entry; auction guides here routinely "
                     "clear above the number, so set a hard ceiling."},
        ],
        "outlook": (
            "Base case for the next fortnight: do not chase. Lead with the fully-enriched Zetland candidates — the "
            "Joynton Avenue two-bed-two-bath with confirmed reverse-cycle air-con is the cleanest match — and use the "
            "Randwick 'Yalara' as the eastern-suburbs scarcity comparison. Treat the six card-only listings as "
            "inspect-first comparables until Domain detail pages can be refreshed. Use the 16 June RBA decision as the "
            "next macro checkpoint: a hold steadies the market, another hike adds to your negotiating leverage on the "
            "top of every guide. Note: today's live Domain search returned access-denied pages after a rate-limit, so "
            "this folio is curated from this morning's fresh market memory, not an all-new scrape."
        ),
        "sources": [
            {"key": "rba_cash_rate", "label": "RBA cash rate", "value": "4.35% (next decision 16 Jun)",
             "source_name": "Reserve Bank of Australia / CBA economics", "source_url": "https://www.rba.gov.au/",
             "published_at": "2026-05-05", "observed_at": "2026-06-08", "freshness_days": 34},
            {"key": "abs_cpi", "label": "CPI inflation", "value": "4.2% headline / 3.4% trimmed-mean (April)",
             "source_name": "Australian Bureau of Statistics", "source_url": "https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia/latest-release",
             "published_at": "2026-05-28", "observed_at": "2026-06-08", "freshness_days": 11},
            {"key": "domain_sydney_units", "label": "Sydney unit forecast", "value": "Domain: ~+7% in 2026 (~$892k median)",
             "source_name": "Domain Insight — 2026 forecast", "source_url": "https://insight.domain.com.au/research-insights/industry-news/shock-house-price-predictions-for-2026-sydney-median-nears-2-million/",
             "published_at": "2026-02-01", "observed_at": "2026-06-08", "freshness_days": 127},
            {"key": "anz_forecast", "label": "ANZ view", "value": "Sydney values ~-0.7% in 2026, +2.6% in 2027",
             "source_name": "ANZ Research (April 2026 update)", "source_url": "https://www.anz.com.au/",
             "published_at": "2026-04-15", "observed_at": "2026-06-08", "freshness_days": 54},
            {"key": "sqm_vacancy", "label": "Rental vacancy", "value": "1.1% (April 2026, from 1.3% y/y)",
             "source_name": "SQM Research", "source_url": "https://sqmresearch.com.au/",
             "published_at": "2026-05-01", "observed_at": "2026-06-08", "freshness_days": 38},
        ],
    }


def main() -> int:
    front, _ = parse_buyer_md(DEFAULT_BUYER)
    with PropertyDB(DEFAULT_DB_PATH) as db:
        props = [build_property(db, front, lid) for lid in ORDER]

    payload = {
        "meta": {
            "title": "The Saturday Folio",
            "issue": "Vol. II · 8 June 2026",
            "eyebrow": "A CURATED BUYING DOSSIER",
            "date": "8 June 2026",
            "prepared_for": "Ben",
            "prepared_by": "Dune 2 · Buying Agent",
            "standfirst": (
                "Twelve candidates worth comparing, led by a garden-aspect Zetland two-bed-two-bath with "
                "air-conditioning confirmed in the listing. Six are fully enriched; six are inspect-first card "
                "reads after today's Domain detail pages were rate-limited."
            ),
            "closing": (
                "Compiled by your buying agent on the Pi from this morning's run. The market data is freshly "
                "researched (8 June): RBA 4.35% and rising, April CPI 4.2%, Sydney units forecast +5–7% for 2026, "
                "rental vacancy 1.1%. Six properties are fully enriched (description, agent contacts, full gallery + "
                "floorplan); the other six are search-card reads only — confirm air-con (your one non-negotiable), "
                "strata and aspect in person before ranking them. Yields and outgoings are estimates; verify strata, "
                "council and contract terms before offering. Re-test the macro after the 16 June RBA decision."
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
                "Owner-occupier with capital-growth priority. A low-maintenance 1–2 bedroom apartment under $1.1M, "
                "air-conditioning essential (installed or installable), secure parking, within roughly a 20-minute "
                "drive of the CBD. Open across the inner-south (lives in Zetland, likes it), eastern suburbs, lower "
                "north shore and the inner-west as far as Marrickville — not the outer/greater west."
            ),
            "must_haves": [
                "Air-conditioning (installed or installable)",
                "Secure car space",
                "Within ~20 min drive of the CBD",
                "1–2 bedrooms, under $1.1M",
                "Capital-growth fundamentals (scarcity over oversupply)",
            ],
            "deal_breakers": [
                "Outer / greater west",
                "No parking",
                "Aircon impossible to install",
                "Flood / fire overlay",
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
