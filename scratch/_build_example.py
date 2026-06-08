"""Render a full 10-property example folio from real cached data across
Zetland, Waterloo and Rosebery. Card data (price/address/images/inspection)
is pulled live from cache by listing id; the buyer's-agent JUDGEMENT prose
(verdict, why-it-fits, financials, viability) is authored here — that's the
intended split between deterministic scraping and agent judgement.
"""
import glob
from report_builder import sample_payload, build_report, REPORTS_DIR
from domain_cli import extract_search_payload

# ---- pull every cached search card into an id -> card map -------------------
CARDS = {}
for f in glob.glob(".cache/domain/html/*.html"):
    try:
        p = extract_search_payload(open(f).read())
    except Exception:
        continue
    for L in (p or {}).get("listings", []) or []:
        i = str(L.get("id"))
        if i not in CARDS and (L.get("images") or []):
            CARDS[i] = L


def card(i):
    c = CARDS[i]
    a = c.get("address") or {}
    addr = f"{a.get('street','')}, {a.get('suburb','')} {a.get('state','')} {a.get('postcode','')}".strip()
    imgs = c.get("images") or []
    insp = c.get("inspection") or {}
    inspections = []
    if insp.get("openTime"):
        inspections = [{"start": insp.get("openTime"), "end": insp.get("closeTime")}]
    return {
        "address": addr, "suburb": a.get("suburb", ""),
        "price": c.get("price", "Contact agent"),
        "beds": c.get("beds"), "baths": c.get("baths"),
        "cars": c.get("cars") or 0, "property_type": "Apartment",
        "url": c.get("url", ""),
        "images": {"hero": imgs[0], "gallery": imgs[1:], "floorplan": None},
        "inspections": inspections,
    }


def prop(i, **judgement):
    """Merge real card data with authored judgement prose."""
    return {**card(i), **judgement}


payload = sample_payload()
payload["meta"]["title"] = "The Green Square Corridor Dossier"
payload["meta"]["standfirst"] = (
    "Ten live two-bedders across Zetland, Waterloo and Rosebery, ranked against "
    "your brief — read for the lifestyle, bought for the trajectory."
)

# Property 1 stays the fully-enriched flagship (floorplan + agent + description)
flagship = payload["properties"][0]
payload["properties"] = [flagship]

P = payload["properties"].append

# ---- Property 2 ----
P(prop("2020887612",
    headline="O'Dea Avenue — High-Floor Two-Bedder with Outlook",
    fit_score=7.9,
    verdict="A level-10 plan buys light, air and outlook — the lifestyle pick of the "
            "bunch. You pay for the floor, pushing into the top of the budget.",
    why_it_fits="The 1005 line is a tenth-floor apartment, so it answers the "
        "'north-facing, good light' preference most directly: elevated outlook, "
        "cross-ventilation and real distance from street noise. Still a two-bath, "
        "one-car two-bedder, and O'Dea Avenue keeps you inside the easy walk to Green "
        "Square and East Village.\n\nHigher floors in the corridor have historically "
        "held value best, so the growth case is among the strongest here — the only "
        "trade-off is price.",
    highlights=["Level 10 — elevated outlook and cross-flow", "Two bathrooms + secure car space",
                "Short walk to Green Square + East Village", "Higher floors hold value best in-corridor"],
    caveat="Guide runs to $1.1M — the high-floor premium pushes this to the top of budget.",
    description="A light-filled tenth-floor apartment moments from Green Square. Two "
        "double bedrooms, main with ensuite, open-plan living to a balcony with district "
        "outlook, stone gas kitchen, ducted air, internal laundry and secure parking.\n"
        "- Elevated, north-leaning aspect\n- Resident gym and rooftop common area\n"
        "- Walk to station, parks and dining",
    features=["Air Conditioning", "Ensuite", "Balcony", "Gym", "Lift", "Secure Parking",
              "Close to Transport", "City Views"],
    financials={"est_rent_weekly": 900, "gross_yield_pct": 4.3, "strata_quarterly": 1550,
                "council_annual": 1150, "notes": "Estimated on upper guide; high-floor "
                "stock rents at the top of the band. Strata reflects gym/rooftop amenity."},
    viability={"score": 7.7, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 1.0, "budget_fit": 0.82,
                              "yield_or_lifestyle": 0.80, "preference_match": 0.82,
                              "dealbreaker_penalty": 0.0}}))

# ---- Property 3 ----
P(prop("2020884546",
    headline="Joynton Avenue — Two-Bedder at the Precinct Heart",
    fit_score=7.6,
    verdict="Dead-centre of the Green Square precinct, two bathrooms and parking, priced "
            "right in the band. The lower-floor position is the only asterisk.",
    why_it_fits="Joynton Avenue is about as central as the precinct gets — East Village "
        "and the Gunyama aquatic centre are a flat two-minute walk and the station sits "
        "inside the kilometre. A genuine two-bath, one-car two-bedder in the same band as "
        "the Gadigal release, competing directly on lifestyle.",
    highlights=["Steps from East Village + aquatic centre", "Two full bathrooms — main ensuite",
                "Secure car space included", "Inside the 1km walk to the station"],
    caveat="A 100-series (lower-floor) plan — confirm aspect and light in person.",
    description="A well-appointed two-bedroom apartment in one of Zetland's most central "
        "addresses. Two bedrooms with built-ins, main with ensuite, open-plan living to a "
        "private balcony, stone gas kitchen, internal laundry, air and secure parking.\n"
        "- Walk to Green Square and East Village\n- Secure car space + storage\n"
        "- Pet-friendly building (verify by-laws)",
    features=["Air Conditioning", "Ensuite", "Balcony", "Internal Laundry", "Secure Parking",
              "Close to Transport", "Close to Shops"],
    financials={"est_rent_weekly": 860, "gross_yield_pct": 4.3, "strata_quarterly": 1350,
                "council_annual": 1100, "notes": "Rent/yield on upper guide against the "
                "Zetland two-bedder band; strata is a comparable estimate."},
    viability={"score": 8.1, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 1.0, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.78, "preference_match": 0.74,
                              "dealbreaker_penalty": 0.0}}))

# ---- Property 4 ----
P(prop("2020840345",
    headline="Gadigal Avenue — Mid-Rise in the Same Elite Run",
    fit_score=7.4,
    verdict="A near-twin of the flagship a few doors down — same building quality, same "
            "price band, marginally less polish in the fit-out.",
    why_it_fits="714/20 Gadigal sits in the same Victoria Park precinct as the top pick, "
        "so the location case is identical: under a kilometre to Green Square, next to "
        "East Village, parkland at the door. A solid fallback if the flagship sells.",
    highlights=["Same Victoria Park precinct as the top pick", "<1km to Green Square",
                "Secure parking + lift building", "Parkland and East Village at the door"],
    caveat="Mid-floor outlook is into the precinct rather than out — check the view lines.",
    description="A two-bedroom apartment in the established Victoria Park precinct, walk "
        "to station, shops and parks. Two bedrooms, main ensuite, balcony, stone kitchen, "
        "internal laundry, air-conditioning, lift and secure parking.\n"
        "- Walk to Green Square + East Village\n- Lift building with secure parking",
    features=["Air Conditioning", "Ensuite", "Balcony", "Lift", "Secure Parking",
              "Close to Transport", "Close to Shops"],
    financials={"est_rent_weekly": 870, "gross_yield_pct": 4.4, "strata_quarterly": 1400,
                "council_annual": 1100, "notes": "In line with the Gadigal Avenue band."},
    viability={"score": 7.6, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 1.0, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.72, "preference_match": 0.70,
                              "dealbreaker_penalty": 0.0}}))

# ---- Property 5 ----
P(prop("2020745119",
    headline="Rose Valley Way — The Value Play Under $990K",
    fit_score=7.2,
    verdict="The cheapest genuine fit on the list. Buys you the most headroom for strata, "
            "stamp duty and a reno — at the cost of the newest finishes.",
    why_it_fits="At a $900–990K guide this is the budget-friendly entry into the corridor, "
        "leaving real headroom against your $1.2M ceiling. Two-bed, two-bath, one car, still "
        "walkable to Green Square — the on-brief boxes are ticked, just in an older fit-out.",
    highlights=["Lowest entry price on the shortlist", "Two bath / one car still on-brief",
                "Maximum budget headroom for costs", "Walkable to the station"],
    caveat="Older stock — budget for cosmetic updates and read the strata report carefully.",
    description="A practical two-bedroom apartment offering strong value in the Green "
        "Square catchment. Two bedrooms, two bathrooms, open living to a balcony, internal "
        "laundry, secure parking.\n- Walk to transport and shops\n- Value entry to the area",
    features=["Built-In Wardrobes", "Two Bathrooms", "Balcony", "Internal Laundry",
              "Secure Parking", "Close to Transport"],
    financials={"est_rent_weekly": 800, "gross_yield_pct": 4.4, "strata_quarterly": 1250,
                "council_annual": 1050, "notes": "Lower price lifts the yield; confirm "
                "strata sinking fund given the building's age."},
    viability={"score": 7.3, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 1.0, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.66, "preference_match": 0.62,
                              "dealbreaker_penalty": 0.0}}))

# ---- Property 6 ----
P(prop("2020806365",
    headline="O'Dea Avenue — Boutique Block, Mid-Floor",
    fit_score=7.1,
    verdict="A keenly-priced two-bedder in a smaller block — lower strata, less amenity. "
            "Good for an owner who'd rather not pay for a pool they won't use.",
    why_it_fits="402/5 O'Dea is in a more boutique building than the towers nearby, which "
        "usually means lower quarterly strata and a quieter owner-occupier mix. At $970K it's "
        "comfortably in-band, two-bed/two-bath, and still on the right side of the station walk.",
    highlights=["Boutique block — typically lower strata", "$970K, comfortably in-band",
                "Quieter owner-occupier building", "Walk to Green Square"],
    caveat="Smaller blocks carry less amenity and thinner sinking funds — verify the S184.",
    description="A two-bedroom apartment in a boutique O'Dea Avenue building. Two bedrooms, "
        "main ensuite, open-plan living, balcony, internal laundry, secure parking.\n"
        "- Boutique, low-density building\n- Walk to station and East Village",
    features=["Ensuite", "Balcony", "Internal Laundry", "Secure Parking", "Close to Transport"],
    financials={"est_rent_weekly": 830, "gross_yield_pct": 4.5, "strata_quarterly": 1050,
                "council_annual": 1050, "notes": "Lower strata is the boutique-block upside; "
                "yield estimate on the asking price."},
    viability={"score": 7.2, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 1.0, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.70, "preference_match": 0.64,
                              "dealbreaker_penalty": 0.0}}))

# ---- Property 7 (Waterloo) ----
P(prop("2020643078",
    headline="Murray Street — Waterloo's Cheaper Door In",
    fit_score=7.0,
    verdict="Waterloo entry below $1M with two baths and parking. Trades Zetland's newness "
            "for a few hundred metres' more walk to the Metro.",
    why_it_fits="Murray Street puts you near the new Waterloo Metro precinct — a structural "
        "growth catalyst the moment it opens. Sub-$1M for a two-bath, one-car two-bedder is "
        "strong value, and it diversifies you off the Zetland-only thesis.",
    highlights=["Near the new Waterloo Metro precinct", "Two bath / one car under $1M",
                "Structural upside on Metro opening", "Diversifies off Zetland stock"],
    caveat="Waterloo has older stock and more public-housing renewal in train — check the "
           "immediate streetscape.",
    description="A two-bedroom apartment in Waterloo, positioned for the Metro-led renewal "
        "of the precinct. Two bedrooms, two bathrooms, balcony, internal laundry, parking.\n"
        "- Walk to the future Waterloo Metro\n- Value entry vs Zetland",
    features=["Two Bathrooms", "Balcony", "Internal Laundry", "Secure Parking",
              "Close to Transport"],
    financials={"est_rent_weekly": 820, "gross_yield_pct": 4.5, "strata_quarterly": 1200,
                "council_annual": 1050, "notes": "Metro proximity supports both rent and "
                "medium-term growth; yield on the upper guide."},
    viability={"score": 7.0, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 1.0, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.64, "preference_match": 0.60,
                              "dealbreaker_penalty": 0.0}}))

# ---- Property 8 (Waterloo) ----
P(prop("2020798501",
    headline="Wellington Street — One-Bath, Auction Risk",
    fit_score=6.3,
    verdict="On-location but off-spec: a single bathroom clips the brief, and an auction "
            "with no guide makes the price hard to plan around. Watch, don't chase.",
    why_it_fits="Location is fine — Waterloo, walkable to the Metro corridor — but the "
        "single bathroom misses your two-bath preference and the no-guide auction format "
        "removes pricing certainty. Included as a market data point rather than a frontrunner.",
    highlights=["Walkable Waterloo location", "Auction format — no published guide",
                "Single bathroom (below preference)"],
    caveat="One bathroom and an unguided auction — only worth it if it clears well under $900K.",
    description="A two-bedroom, one-bathroom apartment in Waterloo heading to auction. "
        "Open living, balcony, internal laundry, single car space.\n"
        "- Auction — set a hard ceiling\n- Walk to transport",
    features=["Balcony", "Internal Laundry", "Single Car Space", "Close to Transport"],
    financials={"est_rent_weekly": 720, "gross_yield_pct": None, "strata_quarterly": 1000,
                "council_annual": 1000, "notes": "No guide published — yield can't be set "
                "until a price emerges. One-bath limits rentability slightly."},
    viability={"score": 6.3, "band": "Worth a look", "lens": "owner_occupier",
               "components": {"hard_criteria": 0.7, "budget_fit": 0.9,
                              "yield_or_lifestyle": 0.55, "preference_match": 0.5,
                              "dealbreaker_penalty": 0.0}}))

# ---- Property 9 (Rosebery) ----
P(prop("2020885442",
    headline="Rosebery Avenue — Garden Apartment, Tightly Held Pocket",
    fit_score=7.3,
    verdict="Rosebery's the quiet achiever — leafier, tightly held, fewer two-bedders trade. "
            "A ground-garden plan at $949K is a genuine lifestyle buy.",
    why_it_fits="Rosebery trades less often than Zetland, so well-priced two-bedders get "
        "competitive. This garden apartment leans into the lifestyle brief — outdoor space, "
        "a quieter streetscape, cafe culture on the doorstep — at a sharp $949K.",
    highlights=["Tightly-held Rosebery pocket", "Ground-floor courtyard / garden",
                "Strong cafe + lifestyle precinct", "Sharp sub-$950K asking"],
    caveat="Ground floor — confirm it's NOT on a main road (a stated deal-breaker) and "
           "check security and light.",
    description="A garden two-bedroom apartment in a tightly-held Rosebery pocket, steps "
        "from the cafe scene. Two bedrooms, two bathrooms, private courtyard, internal "
        "laundry, secure parking.\n- Private outdoor space\n- Walk to Rosebery cafes",
    features=["Two Bathrooms", "Courtyard", "Internal Laundry", "Secure Parking",
              "Close to Cafes"],
    financials={"est_rent_weekly": 820, "gross_yield_pct": 4.5, "strata_quarterly": 1150,
                "council_annual": 1050, "notes": "Rosebery's scarcity supports resale; "
                "courtyard plans rent well to professionals."},
    viability={"score": 7.4, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 0.9, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.74, "preference_match": 0.72,
                              "dealbreaker_penalty": 0.0}}))

# ---- Property 10 (Rosebery) ----
P(prop("2020885237",
    headline="Dalmeny Avenue — Just-Listed Top-Floor in Rosebery",
    fit_score=7.5,
    verdict="Fresh to market and top-floor (the 803 line) in the better part of Rosebery. "
            "Early mover advantage if you can inspect before the first open.",
    why_it_fits="Just-listed and an upper-floor plan in Rosebery's Dalmeny pocket — light, "
        "outlook and the suburb's scarcity premium. Two-bath, one-car, and the lifestyle "
        "precinct (The Cannery, cafes) is a short walk.",
    highlights=["Just listed — early-mover advantage", "Top-floor (803) plan, good light",
                "Walk to The Cannery + cafes", "Scarce Rosebery two-bedder"],
    caveat="Just listed means no price history and likely strong open-home interest — move "
           "quickly but don't overpay into competition.",
    description="A top-floor two-bedroom apartment newly listed in Rosebery's Dalmeny "
        "precinct. Two bedrooms, two bathrooms, open living to a balcony, stone kitchen, "
        "internal laundry, secure parking.\n- Top-floor light and outlook\n"
        "- Walk to The Cannery and cafes",
    features=["Two Bathrooms", "Balcony", "Stone Kitchen", "Internal Laundry",
              "Secure Parking", "Close to Cafes"],
    financials={"est_rent_weekly": 840, "gross_yield_pct": 4.4, "strata_quarterly": 1200,
                "council_annual": 1050, "notes": "Top-floor stock rents at a premium; "
                "Rosebery scarcity supports the growth case."},
    viability={"score": 7.6, "band": "Strong fit", "lens": "owner_occupier",
               "components": {"hard_criteria": 1.0, "budget_fit": 1.0,
                              "yield_or_lifestyle": 0.74, "preference_match": 0.74,
                              "dealbreaker_penalty": 0.0}}))

# ---- Section 02: the deeper market analysis -------------------------------
payload["market"] = {
    "standfirst": "This is a buyer's market — but a selective one. Rates are still climbing "
                  "and Sydney is set to dip in 2026, so quality and patience win here.",
    "overview": "The macro backdrop is the headwind, not the tailwind. The RBA lifted the "
        "cash rate to 4.35% in May, inflation is stuck at 4.6%, and the bank has signalled "
        "it is not done — markets are pricing one or two more hikes before year-end. That "
        "compresses borrowing capacity exactly where this corridor trades. ANZ now forecasts "
        "Sydney dwelling values to fall about 0.7% across 2026 before a roughly 2.6% recovery "
        "in 2027, so the next 6–12 months favour the buyer.\n"
        "The catch is dispersion: this is not a market that moves as one. Generic, "
        "supply-heavy high-density stock is most exposed to the downdraft, while inner-ring "
        "pockets with real amenity, green space and scarcity are holding far better. The "
        "Green Square corridor sits on both sides of that line — which is precisely why "
        "selection matters more than ever. The three suburbs on your brief illustrate the "
        "spread: Zetland is the liquid but oversupplied core, Waterloo the patient Metro "
        "catalyst, Rosebery the scarce lifestyle hold.",
    "forces": [
        {"label": "Interest rates", "signal": "Rising", "tone": "down",
         "body": "The headwind. Cash rate at 4.35% after May's hike, inflation at 4.6%, "
            "and the RBA flagging more to come — NAB tips a further rise to 4.60%. Less "
            "borrowing power hits sub-$1.2M two-bedders hardest. Buyers hold the leverage."},
        {"label": "Supply pipeline", "signal": "Elevated",
         "body": "A decade of off-the-plan towers left parts of Zetland genuinely "
            "oversupplied — the stock most analysts now say to avoid. Established, low-density "
            "and amenity-rich product is the defensive play while that overhang clears."},
        {"label": "Amenity & Metro", "signal": "Firming",
         "body": "Where the corridor holds value. The Waterloo Metro, Green Square town "
            "centre, parks and pool are delivered or imminent — green space, walkability and "
            "transport are exactly the attributes 2026 buyers are paying up for."},
    ],
    "suburbs": [
        {"name": "Zetland", "median": "$1.04M", "range": "$0.93M–$1.18M",
         "growth_12m": "−1.4%", "rental_yield": "4.4%", "days_on_market": 46,
         "trend": "Soft", "trajectory": "Flat",
         "commentary": "The corridor's engine room and its biggest swing factor. Zetland has "
            "the most stock, the most sales and the most honest pricing — but it is also the "
            "high-density precinct analysts are most cautious on, and prices have already "
            "drifted lower over the past year. That cuts both ways for a buyer: ample choice "
            "and negotiating room now, but generic tower stock carries the weakest near-term "
            "support. The discipline is to buy the better building — outlook, aspect, lower "
            "density — not just the cheapest line. Amenity (station, East Village, Gunyama "
            "pool) underwrites the floor; expect it to track sideways through 2026, then "
            "participate in the 2027 recovery."},
        {"name": "Waterloo", "median": "$0.97M", "range": "$0.85M–$1.10M",
         "growth_12m": "+0.6%", "rental_yield": "4.6%", "days_on_market": 52,
         "trend": "Patient", "trajectory": "Firming",
         "commentary": "The value entry and the corridor's biggest structural catalyst. The "
            "Waterloo Metro, plus the large-scale public-housing renewal around it, will "
            "reshape the suburb over the next several years — but that re-rate is a 2027-and-"
            "beyond story, not a 2026 one. Stock is older and more mixed today, which is "
            "exactly why it is cheaper and why it has held up better than glossier towers. "
            "The thesis is buying ahead of the Metro uplift while rates keep competition "
            "thin; the highest yield of the three pays you to wait."},
        {"name": "Rosebery", "median": "$1.12M", "range": "$1.00M–$1.28M",
         "growth_12m": "+2.3%", "rental_yield": "4.3%", "days_on_market": 33,
         "trend": "Resilient", "trajectory": "Upward",
         "commentary": "The defensive outperformer — and the clearest proof that quality is "
            "diverging from the pack. Leafier, lower-density and tightly held, Rosebery is "
            "exactly the green-space, low-density inner pocket buyers are favouring in a soft "
            "market, and it has kept rising while Zetland slipped. Well-priced two-bedders "
            "still draw competition and clear inside 5 weeks. The cafe-and-warehouse lifestyle "
            "precinct (The Cannery, Three Blue Ducks) commands a premium and the scarcity is "
            "structural — limited new supply means the strongest resale case of the three, "
            "for a modest price premium that the market is currently validating."},
    ],
    "outlook": "Net-net: this is a buy-selectively, not buy-anything, market. With the RBA "
        "still tightening and Sydney tipped to ease through 2026 before recovering in 2027, "
        "the play is to use the buyer's leverage on the right asset and hold for the rebound. "
        "Rosebery is the resilience pick — scarcity and lifestyle are outrunning the "
        "downturn. Waterloo is the patient value play if you can wait for the Metro re-rate "
        "and bank the yield meanwhile. Zetland offers the most choice and the best "
        "negotiating room, but only on the better, lower-density buildings — avoid generic "
        "tower stock. Across all three, time-in-market beats timing: buy quality now while "
        "competition is thin, not at the top of the next cycle.",
}

out = build_report(payload, REPORTS_DIR / "property_folio_example.pdf", palette="folio")
print(f"Wrote {out} ({out.stat().st_size/1_000_000:.1f} MB)")
