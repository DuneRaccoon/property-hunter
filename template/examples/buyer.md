---
# Hard criteria — parsed deterministically into Domain search filters.
# Edit these freely; the buyer's agent turns them into live searches.
objective: both           # buy | rent | both
buyer_type: owner_occupier  # owner_occupier | investor — drives how viability is scored
budget:
  buy:
    min: 0
    max: 1200000          # dollars
  rent:
    min: 0
    max: 900              # dollars per week
beds_min: 2
beds_max: null
baths_min: 1
cars_min: 1
property_types: [apartment, townhouse]   # Domain ptypes: house, apartment, townhouse, villa, duplex, studio, land, ...
exclude_under_offer: true
sort: dateupdated-desc

# Where to look. List explicit suburbs, and/or name a broader region for the
# agent to expand into candidate suburbs via suburb analysis.
locations:
  suburbs:
    - Zetland NSW 2017
    - Waterloo NSW 2017
    - Rosebery NSW 2018
  region_hint: "Inner-south Sydney, walkable to the CBD, near a train station"

# Track comparable sold listings for the same criteria (market context).
track_sold: true
---

## What I'm actually looking for

A low-maintenance 2-bedroom apartment or small townhouse in inner-south Sydney,
ideally within walking distance of a train station and good cafes. Owner-occupier,
not a hard-core investor — I care about lifestyle and capital growth potential.

### Strong preferences (the agent should weigh these, not hard-filter)
- Secure parking and a lock-up garage if possible
- North-facing / good natural light
- Balcony or courtyard
- Building with a pool/gym is a nice-to-have, not essential
- Avoid busy main roads and flight paths
- Bonus: period charm, or new builds with good finishes

### Deal-breakers
- Ground floor on a main road
- No parking at all
- Known flood / fire overlay
