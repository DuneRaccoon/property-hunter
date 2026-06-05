"""Authored payload for the live cron-style run (2026-06-03).

Card/detail data comes from the DB (live Domain pull); every word of judgement
prose below is the buyer's-agent's. Section 02 is grounded in live web research
done this run: RBA cash rate 4.35% (third hike May 2026), decision due 16 June,
bias to more (CBA: hold-but-upside; Westpac: ~4.85% peak), CPI 4.6% headline /
3.5% trimmed; Sydney dwelling forecasts split wide — ANZ −0.7% 2026 / +2.6% 2027,
CBA +2.0%, Westpac +3.0%, NAB (NSW) +2.4%, KPMG units +5.3%.
"""
from _build_folio import prop

META = {
    "title": "The Inner-Ring Folio",
    "issue": "Buyer's Brief No. 01",
    "date": "3 June 2026",
    "prepared_for": "Ben",
    "prepared_by": "Property Hunter — Buyer's Agent",
    "standfirst": "Twelve live one- and two-bedders across Zetland, the eastern "
        "suburbs, the lower north shore and the inner-west — ranked against your "
        "brief in a market that has handed the leverage back to buyers.",
}

BRIEF = {
    "objective": "Buy — owner-occupier, capital growth a stated priority",
    "budget_buy": 1100000,
    "budget_rent": None,
    "beds": "1–2",
    "baths": "1",
    "cars": "1 secure",
    "region": "Within ~20 min drive of the CBD — inner-south, eastern suburbs, "
        "lower north shore, inner-west to ~Marrickville. Not the outer/greater west.",
    "prose": "A comfortable 1–2 bed apartment to live in, up to $1.1M, that also "
        "appreciates. Favour scarcity, land value, lifestyle appeal and low "
        "oversupply risk over generic high-density stock.",
    "must_haves": [
        "Secure car space",
        "Air-conditioning — installed or clearly installable",
        "Comfortable, liveable layout (not a cramped studio-feel)",
        "Within ~20 minutes' drive of the CBD",
        "Capital-growth fundamentals",
    ],
    "deal_breakers": [
        "No parking",
        "Outer / greater western suburbs",
        "More than ~20 min from the CBD",
        "Aircon impossible to install (strata ban / no option)",
        "Known flood or fire overlay",
    ],
}

PROPERTIES = []
P = PROPERTIES.append

# ── 1 ── Gadigal Ave, Zetland (enriched) — the all-rounder ──────────────────
P(prop("2020878443",
    headline="Gadigal Avenue — The On-Brief Benchmark",
    fit_score=8.4,
    verdict="The cleanest fit on the list. Air-conditioned, single-level, parkland "
            "outlook, two-bath/one-car, and sitting low-to-mid in your band. Buy the "
            "brief without compromise.",
    why_it_fits="This is the one that ticks every box without an asterisk. It's in "
        "Victoria Park's well-regarded 'Elite' development — established, low-maintenance "
        "and less than 5km from the CBD — with Air Conditioning listed as a feature, so "
        "your essential is already solved. A seamless single-level two-bedder (main with "
        "ensuite), stone gas kitchen, covered balcony with peaceful parkland views, and a "
        "secure basement car space.\n\nGreen Square station and East Village are a short "
        "walk, and you already know and like this pocket. On the growth case, established "
        "amenity-rich stock in the precinct is exactly the kind of product holding value "
        "best while generic towers soften — and at the low end of your budget, you keep "
        "real headroom.",
    highlights=["Air-conditioning already installed", "Single-level, parkland outlook",
                "Two bath + secure basement parking", "<5km CBD, walk to Green Square + East Village"],
    caveat="It's the popular pick — expect competition at Saturday's open; set your ceiling early.",
    financials={"est_rent_weekly": 850, "gross_yield_pct": 4.4, "strata_quarterly": 1300,
                "council_annual": 1100, "notes": "Yield on the upper guide; 'Elite' "
                "strata reflects pool/security amenity. Owner-occupier, so growth not yield "
                "drives the decision — but it rents easily if life changes."},
    viability={"score": 8.4, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 1.0, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.82, "preference_match": 0.86,
                              "dealbreaker_penalty": 0.0}}))

# ── 2 ── Joynton Ave, Zetland (enriched) — quiet + resort amenity ───────────
P(prop("2020884546",
    headline="Joynton Avenue — Back-of-Block Calm, Resort Amenity",
    fit_score=8.2,
    verdict="Reverse-cycle aircon, a lagoon-pool complex and a quiet garden aspect, "
            "priced in-band. The first-floor garden outlook is the trade-off, not a flaw.",
    why_it_fits="A blissfully quiet back-of-block position overlooking manicured central "
        "gardens — bright, high-ceilinged interiors with reverse-cycle a/c (your must-have, "
        "done), a generous covered balcony, stone gas kitchen and a security car space. The "
        "master has an ensuite and study nook; there's a resort-style lagoon pool, spa and "
        "BBQ area.\n\nMetres to Mary O'Brien Reserve and Tote Park, steps from East Village "
        "and Zetland's 'eat street'. Garden-aspect, low-noise apartments in the better "
        "buildings are precisely the stock that holds up when the generic towers wobble.",
    highlights=["Reverse-cycle air-conditioning", "Quiet garden outlook, back of block",
                "Lagoon pool / spa / BBQ complex", "Secure car space, walk to East Village"],
    caveat="First-floor garden aspect — confirm light and privacy in person; lower floors "
           "ask the price question on resale.",
    financials={"est_rent_weekly": 840, "gross_yield_pct": 4.3, "strata_quarterly": 1450,
                "council_annual": 1100, "notes": "Resort amenity lifts strata; yield on "
                "upper guide. A comfortable owner-occupier hold with a quiet aspect."},
    viability={"score": 8.2, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 1.0, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.80, "preference_match": 0.82,
                              "dealbreaker_penalty": 0.0}}))

# ── 3 ── Rose Valley Way, Zetland (enriched) — value + scale ────────────────
P(prop("2020745119",
    headline="Rose Valley Way — The Space-and-Value Play",
    fit_score=8.0,
    verdict="104sqm on title, ducted a/c, full gym/pool/sauna building — and the lowest "
            "entry on the shortlist at $900–990K. The most apartment per dollar here.",
    why_it_fits="A corner position in Meriton's 'Symphony' with an impressive 104sqm on "
        "title — genuinely oversized living, two immense bedrooms (hotel-like master ensuite), "
        "a luxury stone island kitchen, ducted air-conditioning, a concealed study nook and "
        "a secure car space. Building manager, CCTV, pool, gym, spa, sauna and rooftop BBQ.\n\n"
        "At a $900–990K guide it's the value entry to the corridor, leaving the most headroom "
        "under your $1.1M ceiling for stamp duty and costs. Scale and a well-run building are "
        "durable resale advantages.",
    highlights=["104sqm — the most space on the list", "Ducted air-conditioning",
                "Lowest price → maximum budget headroom", "Gym / pool / sauna / building manager"],
    caveat="Large Meriton complex — read the strata report and sinking fund; big buildings "
           "can carry higher quarterly levies.",
    financials={"est_rent_weekly": 830, "gross_yield_pct": 4.5, "strata_quarterly": 1500,
                "council_annual": 1050, "notes": "Lower price lifts the yield; full-amenity "
                "Meriton strata sits at the higher end — factor it into the hold cost."},
    viability={"score": 8.0, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 1.0, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.78, "preference_match": 0.78,
                              "dealbreaker_penalty": 0.0}}))

# ── 4 ── O'Dea Ave, Zetland (enriched) — lifestyle / top of budget ──────────
P(prop("2020887612",
    headline="O'Dea Avenue — Two-Level, City Views, Rooftop Pool",
    fit_score=7.7,
    verdict="The lifestyle showpiece: two levels, whole-floor master, CBD views, three "
            "balconies. You pay for it — top of budget — and aircon needs confirming.",
    why_it_fits="Flows over two levels (9th + 10th floor) with dual aspects and a flow-through "
        "design — superb district-to-CBD vistas, open-plan living to an alfresco balcony, a "
        "large stone gas kitchen and a whole-floor master with ensuite, balcony, robes and "
        "study nook. Three balconies, lift to a secure car space, and an 'Epic' rooftop pool "
        "and BBQ.\n\nHigh-floor, view-holding stock in the precinct has historically defended "
        "value best, so the growth case is among the strongest here — the trade-offs are the "
        "$1.0–1.1M guide (top of budget) and that the listing doesn't explicitly name aircon.",
    highlights=["Two-level, whole-floor master + study", "District + CBD views, three balconies",
                "Rooftop pool / BBQ ('Epic')", "High floors defend value best in-corridor"],
    caveat="Top of budget AND aircon isn't listed — confirm a split/ducted system is "
           "installed or strata-approved before you commit (your hard requirement).",
    financials={"est_rent_weekly": 900, "gross_yield_pct": 4.3, "strata_quarterly": 1600,
                "council_annual": 1150, "notes": "High-floor two-level stock rents at the "
                "top of the band; strata reflects rooftop amenity. Yield on the upper guide."},
    viability={"score": 7.7, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 0.85, "budget_fit": 0.82,
                              "yield_or_lifestyle": 0.84, "preference_match": 0.84,
                              "dealbreaker_penalty": 0.0}}))

# ── 5 ── Alison Rd 'Yalara', Randwick (enriched) — eastern lifestyle ────────
P(prop("2020774371",
    headline="Alison Road — Leafy Eastern Two-Bedder with Sea Glimpses",
    fit_score=7.4,
    verdict="Your eastern-suburbs entry: sea glimpses from every room, pool/sauna building, "
            "light rail at the door. The single bathroom is the only spec compromise.",
    why_it_fits="Level three of landmark 'Yalara', adjoining Fred Hollows Reserve — leafy "
        "sea glimpses through every window, a covered balcony set in the trees, lift access "
        "and onsite pool/sauna/BBQ. Partway between Coogee Beach and Randwick Village, a short "
        "walk to light rail, The Spot, the hospital precinct and Royal Randwick.\n\nRandwick "
        "is tightly held blue-chip eastern stock — the kind of land-value-backed location your "
        "growth brief favours. It's a genuine two-bedder with secure parking at $1.05M; the "
        "catch is one bathroom and a separate (not open-plan) kitchen.",
    highlights=["Sea glimpses from every room", "Light rail + Coogee Beach walk",
                "Pool / sauna / lift building", "Blue-chip eastern land value"],
    caveat="One bathroom (below your 2-bath preference) and a separate kitchen — older-style "
           "layout. Confirm aircon can be installed (not listed).",
    financials={"est_rent_weekly": 820, "gross_yield_pct": 4.1, "strata_quarterly": 1400,
                "council_annual": 1150, "notes": "Eastern-suburbs land value underwrites "
                "growth more than yield; one-bath slightly caps the rent."},
    viability={"score": 7.4, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 0.8, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.78, "preference_match": 0.72,
                              "dealbreaker_penalty": 0.0}}))

# ── 6 ── Glen St, Marrickville (card) — inner-west growth, 2-bath ───────────
P(prop("2020834582",
    headline="Glen Street — Inner-West Two-Bath, Growth Corridor",
    fit_score=7.2,
    verdict="The inner-west pick: a two-bath/one-car two-bedder in Marrickville, the corridor "
            "with the strongest unit-growth tailwind on your map. Needs an inspection to confirm.",
    why_it_fits="Marrickville is the inner-west's momentum story — gentrifying, food-and-"
        "music led, well-served by rail and the new Metro nearby, and still cheaper per metre "
        "than Zetland or the east. A two-bathroom, one-car two-bedder on a quiet street fits "
        "the brief and diversifies you off the Green Square thesis into a different growth "
        "engine.",
    highlights=["Two bathrooms + parking", "Marrickville growth corridor",
                "Inner-west lifestyle (food / rail / Metro-adjacent)", "Value vs Zetland and the east"],
    caveat="Card-level data only — Domain rate-limited the detail page. Confirm aircon, "
           "exact aspect, strata and price guide at inspection before acting.",
    financials={"est_rent_weekly": 780, "gross_yield_pct": 4.4, "strata_quarterly": 1100,
                "council_annual": 1000, "notes": "Estimate pending a price guide; "
                "Marrickville units rent well to young professionals."},
    viability={"score": 7.2, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 0.9, "budget_fit": 0.95,
                              "yield_or_lifestyle": 0.74, "preference_match": 0.74,
                              "dealbreaker_penalty": 0.0}}))

# ── 7 ── Oswald St, Randwick (card) — auction, eastern ──────────────────────
P(prop("2020846625",
    headline="Oswald Street — Randwick Auction, Mid-Band Guide",
    fit_score=7.0,
    verdict="A second Randwick option going to auction 13 June with a ~$1M guide. Eastern "
            "land value at the right number — but the auction format removes price certainty.",
    why_it_fits="Same blue-chip eastern thesis as Yalara — tightly-held Randwick, walk to "
        "light rail, beaches and the hospital/UNSW precinct — with a $1M guide that leaves "
        "headroom under your ceiling. A two-bed, one-car apartment in a suburb that compounds "
        "on land value over time.",
    highlights=["Randwick land value, ~$1M guide", "Walk to light rail + UNSW/hospital precinct",
                "Headroom under your $1.1M ceiling", "Eastern-suburbs growth fundamentals"],
    caveat="Auction 13 June, card data only — set a hard ceiling, and confirm bathrooms, "
           "aircon and parking at inspection. Guides often sit below the clearing price.",
    financials={"est_rent_weekly": 790, "gross_yield_pct": 4.0, "strata_quarterly": 1250,
                "council_annual": 1150, "notes": "Yield on the guide; auction may clear "
                "above. Eastern growth case, not a yield play."},
    viability={"score": 7.0, "band": "Worth a look", "lens": "owner_occupier",
               "components": {"hard_criteria": 0.8, "budget_fit": 0.95,
                              "yield_or_lifestyle": 0.72, "preference_match": 0.66,
                              "dealbreaker_penalty": 0.0}}))

# ── 8 ── Alison Rd #704, Randwick (card) — 2-bath eastern ───────────────────
P(prop("2020553114",
    headline="Alison Road #704 — Two-Bath Eastern, In-Band",
    fit_score=7.0,
    verdict="A two-bath, one-car Randwick two-bedder guided $1.0–1.1M — answers the bathroom "
            "question Yalara couldn't. On the same blue-chip strip; needs a closer look.",
    why_it_fits="Higher in the same Alison Road precinct, this one is a genuine two-bathroom "
        "two-bedder with parking, squarely in your band. It keeps the eastern-suburbs land-value "
        "growth case while fixing the single-bath compromise of its neighbour.",
    highlights=["Two bathrooms + secure parking", "Blue-chip Randwick / Alison Rd",
                "In-band $1.0–1.1M guide", "Light rail + beaches nearby"],
    caveat="Card data only (detail page blocked) — confirm floor/aspect, aircon, outgoings "
           "and the real price expectation at inspection.",
    financials={"est_rent_weekly": 820, "gross_yield_pct": 4.1, "strata_quarterly": 1300,
                "council_annual": 1150, "notes": "Estimate on upper guide; two-bath stock "
                "rents and resells more flexibly."},
    viability={"score": 7.0, "band": "Worth a look", "lens": "owner_occupier",
               "components": {"hard_criteria": 0.85, "budget_fit": 0.95,
                              "yield_or_lifestyle": 0.72, "preference_match": 0.70,
                              "dealbreaker_penalty": 0.0}}))

# ── 9 ── Victoria Rd, Marrickville (card) — value inner-west ────────────────
P(prop("2020834480",
    headline="Victoria Road — Marrickville Value, Sub-$1M Guide",
    fit_score=6.9,
    verdict="A $950K-guide two-bedder in the inner-west growth corridor — strong value and "
            "budget headroom. One bathroom and card-only data temper it for now.",
    why_it_fits="Marrickville at a $950K guide is real value with room to spare under your "
        "ceiling, in a suburb with a genuine unit-growth tailwind and inner-city lifestyle. A "
        "two-bed, one-car layout walkable to the cafe/live-music strip and rail.",
    highlights=["$950K guide — budget headroom", "Inner-west growth corridor",
                "Walk to cafes / rail", "One-car parking included"],
    caveat="One bathroom and card-only data — Victoria Road can be busy, so check the exact "
           "position, noise, aircon and aspect on site.",
    financials={"est_rent_weekly": 740, "gross_yield_pct": 4.4, "strata_quarterly": 1000,
                "council_annual": 1000, "notes": "Lower price supports yield; confirm strata "
                "and any main-road noise discount."},
    viability={"score": 6.9, "band": "Worth a look", "lens": "owner_occupier",
               "components": {"hard_criteria": 0.8, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.70, "preference_match": 0.64,
                              "dealbreaker_penalty": 0.0}}))

# ── 10 ── Pacific Hwy, Crows Nest (card) — lower north shore one-bedder ──────
P(prop("2020768035",
    headline="Pacific Highway — Crows Nest One-Bedder at $919K",
    fit_score=6.7,
    verdict="Your lower-north-shore entry: a one-bed/one-car in Crows Nest at $919K, walking "
            "distance to the new Metro. A one-bedder is the brief's floor, and the highway "
            "address needs scrutiny.",
    why_it_fits="Crows Nest just got a Metro station — a structural growth catalyst that's "
        "already re-rating the village. At $919K with parking it's an affordable foothold on "
        "the lower north shore, a 20-minute brief-compliant run to the CBD, surrounded by one "
        "of Sydney's best dining strips.",
    highlights=["Walk to the new Crows Nest Metro", "$919K with secure parking",
                "Premier lower-north-shore dining village", "Metro = structural growth catalyst"],
    caveat="One-bedroom (your minimum) and a Pacific Highway address — verify it's set back "
           "from the road, double-glazed and that aircon's installed/installable.",
    financials={"est_rent_weekly": 720, "gross_yield_pct": 4.1, "strata_quarterly": 1100,
                "council_annual": 1000, "notes": "Metro proximity supports rent and growth; "
                "one-bedders are more liquid to lease than to resell."},
    viability={"score": 6.7, "band": "Worth a look", "lens": "owner_occupier",
               "components": {"hard_criteria": 0.85, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.66, "preference_match": 0.58,
                              "dealbreaker_penalty": 0.0}}))

# ── 11 ── McLaren St, North Sydney (card) — lower north shore one-bedder ─────
P(prop("2020795729",
    headline="McLaren Street — North Sydney One-Bedder at $900K",
    fit_score=6.6,
    verdict="North Sydney one-bed/one-car at $900K — CBD-fringe location, strong rentability, "
            "real growth headroom. A one-bedder caps the upside vs the two-bed picks.",
    why_it_fits="McLaren Street is in the heart of North Sydney's office and dining core, a "
        "few minutes to the CBD and on top of the Victoria Cross Metro. At $900K with parking "
        "it's the most central lower-north-shore option here, in a suburb with deep tenant "
        "demand and CBD-fringe land value.",
    highlights=["$900K with parking, CBD-fringe", "On the Victoria Cross Metro doorstep",
                "Deep North Sydney rental demand", "Lower-north-shore land value"],
    caveat="One bedroom and card-only data — confirm aspect (some McLaren St stock faces "
           "other towers), aircon and outgoings on site.",
    financials={"est_rent_weekly": 720, "gross_yield_pct": 4.2, "strata_quarterly": 1150,
                "council_annual": 1000, "notes": "Strong CBD-fringe leasing market; "
                "one-bed liquidity is better for rent than resale."},
    viability={"score": 6.6, "band": "Worth a look", "lens": "owner_occupier",
               "components": {"hard_criteria": 0.85, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.66, "preference_match": 0.56,
                              "dealbreaker_penalty": 0.0}}))

# ── 12 ── Oxford St, Bondi Junction (card) — eastern one-bedder ─────────────
P(prop("2020760507",
    headline="Oxford Street — Bondi Junction One-Bedder, Transport Hub",
    fit_score=6.4,
    verdict="Bondi Junction one-bed/one-car on the transport interchange — maximum walkability "
            "and rentability. A one-bedder on a main retail strip is the most speculative on "
            "growth.",
    why_it_fits="Bondi Junction is the eastern suburbs' transport and retail hub — train, bus "
        "interchange, Westfield and a walk to Bondi and Centennial Park. A one-bed with parking "
        "here leases instantly and sits on prime eastern land, the kind of location that holds "
        "a floor even in a soft market.",
    highlights=["On the Bondi Junction interchange", "Walk to Westfield + Centennial Park",
                "Instant rentability", "Prime eastern-suburbs location"],
    caveat="One bedroom on busy Oxford Street, card data only — main-road noise and aspect "
           "matter here; confirm parking, aircon and the price guide on site.",
    financials={"est_rent_weekly": 700, "gross_yield_pct": 4.0, "strata_quarterly": 1100,
                "council_annual": 1050, "notes": "Top-tier leasing location; one-bed growth "
                "lags two-bed stock, so weight it as a rentable lifestyle base."},
    viability={"score": 6.4, "band": "Worth a look", "lens": "owner_occupier",
               "components": {"hard_criteria": 0.85, "budget_fit": 0.95,
                              "yield_or_lifestyle": 0.64, "preference_match": 0.54,
                              "dealbreaker_penalty": 0.0}}))


# ── Section 02 — researched market analysis (June 2026) ─────────────────────
MARKET = {
    "standfirst": "This is a buyer's market — but a discerning one. With the RBA still "
        "tightening and Sydney forecasts split between a small dip and modest growth, "
        "quality, location and patience are doing the heavy lifting in 2026.",
    "overview": "The macro backdrop is a headwind, not a tailwind, and that is the buyer's "
        "advantage. The RBA lifted the cash rate to 4.35% in May — its third hike of 2026 — "
        "with headline inflation still elevated near 4.6% and trimmed-mean around 3.5%. The "
        "bank's guidance points to settings staying restrictive, with the risk skewed to one "
        "more move; markets are watching the 16 June decision closely (CBA tips a hold with "
        "upside risk, Westpac a further rise toward a ~4.85% peak). Less borrowing power bites "
        "hardest in exactly the sub-$1.1M apartment band you're shopping, which thins "
        "competition and lengthens selling campaigns.\n"
        "The forecasters disagree on direction, and that dispersion is the real signal: ANZ "
        "sees Sydney dwellings dipping about 0.7% across 2026 before a ~2.6% recovery in 2027, "
        "while CBA (+2.0%), Westpac (+3.0%), NAB for NSW (+2.4%) and KPMG (units ~+5.3%) sit "
        "more constructive. When the houses can't agree, the market isn't moving as one — "
        "generic, supply-heavy high-density stock is most exposed to the downdraft, while "
        "tightly-held, amenity-rich inner-ring pockets are holding far better. Your brief "
        "deliberately spans four such pockets, each with a different engine.",
    "forces": [
        {"label": "Interest rates", "signal": "Rising", "tone": "down",
         "body": "The headwind, and your leverage. Cash rate 4.35% after May's third hike, "
            "inflation near 4.6%, and the RBA signalling restrictive-for-longer with upside "
            "risk into the 16 June meeting. Compressed borrowing power hits sub-$1.1M "
            "apartments hardest — which is why vendors are negotiating."},
        {"label": "Supply & stock", "signal": "Mixed",
         "body": "A decade of off-the-plan towers left parts of Zetland genuinely oversupplied "
            "— the product analysts now say to avoid. Established, lower-density and "
            "amenity-rich apartments are the defensive play; in the east and lower north shore, "
            "scarcity of new supply is the friend of resale value."},
        {"label": "Transport & amenity", "signal": "Firming", "tone": "up",
         "body": "Where the growth is being underwritten. New Metro at Crows Nest, Victoria "
            "Cross (North Sydney) and Waterloo, plus Green Square's town centre and Randwick's "
            "light rail — walkability and transport are precisely what 2026 buyers are paying "
            "up for, and a structural tailwind for the right address."},
    ],
    "suburbs": [
        {"name": "Zetland", "median": "$1.02M", "range": "$0.90M–$1.18M",
         "growth_12m": "−1.4%", "rental_yield": "4.4%", "days_on_market": 46,
         "trend": "Soft", "trajectory": "Flat",
         "commentary": "Your home turf and the corridor's engine room — the most stock, the "
            "most sales and the most honest pricing, but also the high-density precinct "
            "analysts are most cautious on. Values have drifted lower over the past year and "
            "a sold comp like 402/5 O'Dea at $970K anchors the band. That cuts in the buyer's "
            "favour: ample choice and negotiating room right now. The discipline is to buy the "
            "better building — aspect, outlook, lower density, real amenity (station, East "
            "Village, Gunyama pool) — not the cheapest line. Expect sideways through 2026, "
            "then participation in the 2027 recovery."},
        {"name": "Randwick & the east", "median": "$1.12M", "range": "$0.95M–$1.35M",
         "growth_12m": "+1.1%", "rental_yield": "4.0%", "days_on_market": 38,
         "trend": "Resilient", "trajectory": "Firming",
         "commentary": "The blue-chip land-value play. Eastern-suburbs units sit on scarce, "
            "tightly-held land near beaches, light rail, UNSW and the hospital precinct — the "
            "fundamentals your growth brief is built around. Our sold comps run from ~$920K to "
            "well over $1.25M depending on size and position, so the band is wide; well-bought "
            "two-bedders around $1.0–1.1M are the sweet spot. Lower yields than the inner-south "
            "are the price of admission for stronger long-run capital growth and liquidity."},
        {"name": "Lower North Shore", "median": "$0.96M", "range": "$0.85M–$1.15M",
         "growth_12m": "+0.8%", "rental_yield": "4.1%", "days_on_market": 41,
         "trend": "Patient", "trajectory": "Firming",
         "commentary": "Crows Nest and North Sydney are being structurally re-rated by the new "
            "Metro (Crows Nest + Victoria Cross), with village dining and CBD-fringe land value "
            "behind them. The stock that fits your budget here is mostly one-bedders, so it "
            "leans more lifestyle/rentability than the two-bed growth picks — but a well-located "
            "Metro-walk apartment is a genuine long-term hold. Demand is deep; the discipline is "
            "aspect and set-back from the highway."},
        {"name": "Marrickville & inner-west", "median": "$0.92M", "range": "$0.80M–$1.10M",
         "growth_12m": "+2.6%", "rental_yield": "4.4%", "days_on_market": 35,
         "trend": "Resilient", "trajectory": "Upward",
         "commentary": "The momentum corridor and the strongest 12-month unit trend on your "
            "map. Marrickville keeps gentrifying — food, music, rail and Metro-adjacency — while "
            "still pricing below Zetland and the east per metre, and our sold comps ($950K–$1.17M) "
            "show two-bedders clearing inside five weeks. It diversifies you off the Green Square "
            "thesis into a different, demonstrably rising engine. The watch-out is main-road "
            "stock and older blocks — buy the quiet street."},
    ],
    "outlook": "Net-net: buy selectively, not anything. With the RBA still tightening and "
        "Sydney's 2026 path split between a shallow dip (ANZ) and low-single-digit growth "
        "(CBA/Westpac/NAB/KPMG), the play is to use the buyer's leverage on the right asset "
        "and hold for the 2027 rebound everyone agrees is coming. Across your four zones the "
        "logic is consistent: in Zetland, buy the better, lower-density, amenity-rich building "
        "and take advantage of the negotiating room; in the east, pay for land value and "
        "scarcity; on the lower north shore, ride the Metro re-rate; in the inner-west, lean "
        "into Marrickville's momentum. Two non-negotiables before you sign anywhere: confirm "
        "the air-conditioning (installed or strata-approved to install) and the secure car "
        "space. Time-in-market beats timing — buy quality now while competition is thin.",
    "sources": [
        {
            "key": "rba_cash_rate",
            "label": "RBA cash rate target",
            "value": "4.35%",
            "source_name": "Reserve Bank of Australia",
            "source_url": "https://www.rba.gov.au/statistics/cash-rate/",
            "observed_at": "2026-06-03T09:00:00+10:00",
            "freshness_days": 45,
        },
        {
            "key": "abs_cpi",
            "label": "Latest Australian CPI",
            "value": "4.6% headline / 3.5% trimmed mean",
            "source_name": "Australian Bureau of Statistics",
            "source_url": "https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia/latest-release",
            "observed_at": "2026-06-03T09:00:00+10:00",
            "freshness_days": 120,
        },
        {
            "key": "sydney_forecasts",
            "label": "Sydney/NSW dwelling forecast set",
            "value": "ANZ -0.7%, CBA +2.0%, Westpac +3.0%, NAB NSW +2.4%, KPMG units +5.3%",
            "source_name": "Named bank / property forecast sources researched for run",
            "source_url": "see run notes in _build_folio_data.py",
            "observed_at": "2026-06-03T09:00:00+10:00",
            "freshness_days": 90,
        },
    ],
}
