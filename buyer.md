---
# Hard criteria — parsed deterministically into Domain search filters.
# Edit these freely; the buyer's agent turns them into live searches.
objective: buy            # buy | rent | both
buyer_type: owner_occupier  # owner_occupier | investor — drives how viability is scored
budget:
  buy:
    min: 0
    max: 1100000          # dollars
  rent:
    min: 0
    max: 900              # dollars per week (unused while objective: buy)
beds_min: 1
beds_max: 2
baths_min: 1
cars_min: 1
property_types: [apartment]   # Domain ptypes: house, apartment, townhouse, villa, duplex, studio, land, ...
exclude_under_offer: true
sort: dateupdated-desc

# Where to look. List explicit suburbs, and/or name a broader region for the
# agent to expand into candidate suburbs via suburb analysis.
locations:
  suburbs:
    - Zetland NSW 2017
    - Bondi Junction NSW 2022
    - Randwick NSW 2031
    - Crows Nest NSW 2065
    - North Sydney NSW 2060
    - Marrickville NSW 2204
  region_hint: "Within ~20 minutes' drive of the Sydney CBD. Open across the inner-south (currently live in Zetland and like it), the eastern suburbs, the lower north shore, and the inner-west as far out as Marrickville. Not interested in the outer/greater western suburbs."

# Track comparable sold listings for the same criteria (market context).
track_sold: true
---

## What I'm actually looking for

A comfortable 1 or 2-bedroom apartment to live in, budget up to $1.1M. This is an
owner-occupier purchase but **capital growth potential is a priority** — I want an
asset that appreciates, so favour locations and buildings with strong long-term
fundamentals (scarcity, land value, lifestyle appeal, low oversupply risk).

Location-wise I'm flexible on neighbourhood but it must be **within ~20 minutes'
drive of the Sydney CBD**. I currently live in Zetland and like it, I like the
eastern suburbs, and the lower north shore is nice too. I'm happy to go as far as
the **inner-west up to about Marrickville** — so more inner-city in feel. I'm
**not keen on the outer/greater western suburbs**.

### Strong preferences (the agent should weigh these, not hard-filter)
- **Air-conditioning is essential** — either already installed, OR clearly able to
  be installed (e.g. strata allows a split system, suitable wall/window, no obvious
  blocker). Reject only if aircon is genuinely impossible.
- Comfortable, liveable layout — not a cramped studio-feel one-bedder.
- Good natural light and aspect.
- Capital-growth fundamentals: tightly-held / low-supply pockets beat generic
  high-density towers; proximity to transport, parks and lifestyle amenity.
- Balcony, courtyard or some outdoor space.
- Period charm or a well-finished newer build both welcome.

### Deal-breakers
- No parking space.
- Outer / greater western suburbs (inner-west up to ~Marrickville is fine).
- More than ~20 minutes' drive from the CBD.
- A building/apartment where air-conditioning can never be installed (strata ban,
  no feasible option).
- Known flood / fire overlay.
