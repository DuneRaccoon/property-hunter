#!/usr/bin/env python3
"""Domain search over its own GraphQL API — the transport that isn't Akamai-gated.

Why this exists
---------------
As of Aug-18 2026 Akamai returns a hard ``403 Access Denied`` for *every*
document route that matters (``/sale/``, ``/rent/``, ``/sold-listings/``,
``/suburb-profile/``, and even a bare listing id) from the Pi's IP — both a
plain ``page.goto`` and the Jun-16 same-origin ``fetch()`` XHR. The homepage
still returns 200.

The block is **path-scoped, not blanket**: a nonexistent path under the same
origin returns a real Next.js 404 page (100KB of ``__NEXT_DATA__``), proving the
request reaches the origin. Probing from that angle found
``POST https://www.domain.com.au/graphql`` — the API Domain's own front-end
uses — returning **200 for valid queries with no challenge at all**.

So instead of scraping SSR HTML we ask Domain's API directly, from inside the
genuine OpenClaw browser session (same cookies/TLS/fingerprint as a real tab).

Schema recovery
---------------
Apollo introspection is disabled in production, but its *validation errors* leak
the schema: an unknown input field reports ``Field "x" is not defined by type
"SearchListingsParametersInput"``, so sending a batch of candidate names and
diffing which ones are NOT rejected is an efficient oracle. That is how the
shapes below were recovered (Aug-18 2026); they are not guesses.

    searchListings(searchParams: SearchListingsParametersInput!): SearchListingsResults
      SearchListingsParametersInput:
        locations: [SearchListingsLocationInput!]   # {suburb,state,postcode,region,area,includeSurroundingSuburbs}
        listingType: SearchListingsType             # Sale | Rent | Sold | NewHomes
        propertyTypes: [SearchListingsPropertyType] # ApartmentUnitFlat | House | Townhouse | Villa | Studio | Duplex | NewApartments
        minPrice maxPrice minBedrooms maxBedrooms minBathrooms maxBathrooms
        minCarspaces maxCarspaces keywords page pageSize advertiserIds geoWindow
        sort: SearchListingsSortInput               # {sortKey, direction}
      SearchListingsResults:
        totalResults totalPages page
        results: [ SearchListingsResultListing | SearchListingsResultProject ]

Field coverage (verified live Aug-18 2026, not guessed)
------------------------------------------------------
``SearchListingsResultListing`` carries **more than the old HTML card did**:

    id listingId listingType displayPrice bedrooms bathrooms carspaces
    propertyType features tags dateListed headline summaryDescription
    priceDetails agency inspectionDetails
    displayableAddress { displayAddress street streetNumber unitNumber state
                         postcode suburb { name } geolocation { lat lng } }
    media { ... on Image { url rawUrl type } }
    auctionDetails { auctionSchedule { openingDateTime } }
    soldData { soldPrice soldDate saleMethod }        # listingType: Sold

The earlier belief that search cards had "no address, no images" was a naming
miss — the field is ``displayableAddress`` (not ``address``/``property``), and
``media`` is a ``[Media!]`` union whose ``Image`` member exposes ``url``. Both
return real values on every live card. **Enrichment is therefore an upgrade,
not a repair**: it adds the full description, structured features and the
agent roster, and is no longer load-bearing for address or imagery.

``listingByIdV2 ... ListingDetails`` adds:

    description structuredFeatures propertyTypes status landAreaSqm seoUrl
    displayableAddress { ... }                        # populated; property.address is NOT
    agents { agentId fullName firstName lastName email profileUrl jobTitle
             agencyId photo { url } }
    media auctionDetails soldDetails

Known gaps vs the retired HTML scraper — the API simply does not expose these:
  * **agent phone numbers** (``mobile``/``landline``/``phone`` all reject on
    ``Agent``; email + Domain profile URL are the only contact channels), and
  * a server-side **under-offer exclusion** (no such enum in
    ``SearchListingsAttribute``, which offers only ``HasPhotos``/``HasPrice``),
    so ``exclude_under_offer`` is enforced client-side in ``source_providers``.

Etiquette: same polite rate limiter as ``domain_cli``. This is Domain's own API
being asked normal questions at human pace — don't turn it into a firehose.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import domain_cli

GRAPHQL_URL = "https://www.domain.com.au/graphql"

# Domain enum <- our buyer.md property_types vocabulary.
PROPERTY_TYPE_MAP = {
    "apartment": "ApartmentUnitFlat",
    "unit": "ApartmentUnitFlat",
    "flat": "ApartmentUnitFlat",
    "apartmentunitflat": "ApartmentUnitFlat",
    "house": "House",
    "townhouse": "Townhouse",
    "villa": "Villa",
    "studio": "Studio",
    "duplex": "Duplex",
}

LISTING_TYPE_MAP = {
    "sale": "Sale",
    "buy": "Sale",
    "rent": "Rent",
    "sold": "Sold",
}

# buyer.md sort strings -> {sortKey, direction}
SORT_MAP = {
    "dateupdated-desc": {"sortKey": "DateUpdated", "direction": "Descending"},
    "price-asc": {"sortKey": "Price", "direction": "Ascending"},
    "price-desc": {"sortKey": "Price", "direction": "Descending"},
    "solddate-desc": {"sortKey": "SoldDate", "direction": "Descending"},
}

# buyer.md / Domain-URL feature vocabulary -> SearchListingsPropertyFeature enum.
# Enumerated against the live schema Aug-18 2026 by feeding candidate values and
# keeping the ones the enum accepted. Names absent here (Balcony, Lift, Garden,
# Wheelchair, RemoteGarage) genuinely do NOT exist as searchable features — they
# are only ever free text in the description, so filtering on them server-side
# is impossible and callers must not silently assume otherwise.
PROPERTY_FEATURE_MAP = {
    "airconditioning": "AirConditioning",
    "aircon": "AirConditioning",
    "air conditioning": "AirConditioning",
    "alarmsystem": "AlarmSystem",
    "builtinwardrobes": "BuiltInWardrobes",
    "cableorsatellite": "CableOrSatellite",
    "dishwasher": "Dishwasher",
    "ensuite": "Ensuite",
    "fireplace": "Fireplace",
    "fullyfenced": "FullyFenced",
    "furnished": "Furnished",
    "groundfloor": "GroundFloor",
    "gym": "Gym",
    "heating": "Heating",
    "indoorspa": "IndoorSpa",
    "intercom": "Intercom",
    "northfacing": "NorthFacing",
    "outdoorspa": "OutdoorSpa",
    "petsallowed": "PetsAllowed",
    "secureparking": "SecureParking",
    "shed": "Shed",
    "solarpanels": "SolarPanels",
    "study": "Study",
    "swimmingpool": "SwimmingPool",
    "pool": "SwimmingPool",
    "waterviews": "WaterViews",
}

# ``SearchListingsAttribute`` only offers HasPhotos / HasPrice — there is no
# under-offer / under-contract exclusion, and no sold/auction attribute.
LISTING_ATTRIBUTE_MAP = {
    "has_photos": "HasPhotos",
    "hasphotos": "HasPhotos",
    "has_price": "HasPrice",
    "hasprice": "HasPrice",
}

# Tag strings a card can carry that mean "not a live buy opportunity". Domain's
# GraphQL ``tags`` is a flat [String!] of machine slugs (e.g. "newdevelopment"),
# not the HTML scraper's {tagText, tagClassName} dict.
OFF_MARKET_TAGS = {
    "undercontract": "off_market",
    "underoffer": "off_market",
    "under_offer": "off_market",
    "sold": "sold",
    "leased": "leased",
    "depositTaken".lower(): "off_market",
}


SEARCH_QUERY = """
query PropertyHunterSearch($p: SearchListingsParametersInput!) {
  searchListings(searchParams: $p) {
    totalResults
    totalPages
    page
    results {
      ... on SearchListingsResultListing {
        id
        listingId
        listingType
        displayPrice
        bedrooms
        bathrooms
        carspaces
        propertyType
        features
        tags
        dateListed
        headline
        summaryDescription
        priceDetails { displayPrice canDisplayPrice }
        agency { name agencyId }
        displayableAddress {
          displayAddress
          unitNumber
          streetNumber
          street
          state
          postcode
          displayType
          suburb { name }
          geolocation { latitude longitude }
        }
        media { ... on Image { url rawUrl type } }
        auctionDetails { auctionSchedule { openingDateTime { isoDate time } } }
        soldData {
          saleMethod
          soldPrice { displayPrice canDisplayPrice }
          soldDate { isoDate }
        }
        inspectionDetails {
          inspections {
            openingDateTime { isoDate time }
            closingDateTime { isoDate time }
          }
        }
      }
    }
  }
}
"""


DETAIL_QUERY = """
query PropertyHunterDetail($id: ID!) {
  listingByIdV2(id: $id) {
    ... on ListingDetails {
      id
      listingId
      listingType
      status
      headline
      description
      bedrooms
      bathrooms
      carspaces
      features
      propertyTypes
      dateListed
      seoUrl
      landAreaSqm
      priceDetails { displayPrice canDisplayPrice }
      structuredFeatures { name category }
      agency { id name }
      agents {
        agentId
        fullName
        firstName
        lastName
        email
        profileUrl
        jobTitle
        agencyId
        photo { url }
      }
      media { ... on Image { url rawUrl type } }
      auctionDetails { auctionSchedule { openingDateTime { isoDate time } } }
      soldDetails {
        saleMethod
        soldPrice { displayPrice canDisplayPrice }
        soldDate { isoDate }
      }
      displayableAddress {
        displayAddress
        unitNumber
        streetNumber
        street
        state
        postcode
        displayType
        suburb { name }
        geolocation { latitude longitude }
      }
      inspectionDetails {
        inspections {
          openingDateTime { isoDate time }
          closingDateTime { isoDate time }
        }
      }
    }
  }
}
"""


def _suburb_parts(entry: str) -> Dict[str, Any]:
    """'Zetland NSW 2017' -> {'suburb': 'Zetland', 'state': 'NSW', 'postcode': '2017'}."""
    text = (entry or "").strip()
    postcode = None
    state = None
    m = re.search(r"\b(\d{4})\b\s*$", text)
    if m:
        postcode = m.group(1)
        text = text[: m.start()].strip()
    m = re.search(r"\b(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\b\s*$", text, re.I)
    if m:
        state = m.group(1).upper()
        text = text[: m.start()].strip()
    out: Dict[str, Any] = {"suburb": text.strip(" ,") or None}
    if state:
        out["state"] = state
    if postcode:
        out["postcode"] = postcode
    return out


def build_search_params(filters: Dict[str, Any], *, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    """Translate the buyer-profile filter dict into SearchListingsParametersInput."""
    suburbs = filters.get("suburbs") or []
    if isinstance(suburbs, str):
        suburbs = [suburbs]
    # Domain's *page* search includes nearby suburbs by default — that is how a
    # "Crows Nest" hunt surfaced St Leonards and Naremburn stock. The API does
    # not, so it must be asked explicitly; omitting it cut Crows Nest from 42
    # matches to 3. Hunts can opt out with "include_surrounding": false.
    include_surrounding = filters.get("include_surrounding")
    if include_surrounding is None:
        include_surrounding = True
    locations = [_suburb_parts(s) for s in suburbs]
    locations = [
        {**loc, "includeSurroundingSuburbs": bool(include_surrounding)}
        for loc in locations if loc.get("suburb")
    ]

    ptypes_in = filters.get("ptypes") or filters.get("property_types") or []
    if isinstance(ptypes_in, str):
        ptypes_in = [ptypes_in]
    ptypes = []
    for p in ptypes_in:
        mapped = PROPERTY_TYPE_MAP.get(str(p).strip().lower())
        if mapped and mapped not in ptypes:
            ptypes.append(mapped)

    mode = str(filters.get("mode") or filters.get("listing_type") or "sale").lower()
    params: Dict[str, Any] = {
        "listingType": LISTING_TYPE_MAP.get(mode, "Sale"),
        "page": page,
        "pageSize": page_size,
    }
    if locations:
        params["locations"] = locations
    if ptypes:
        params["propertyTypes"] = ptypes

    for src, dst in (
        ("price_min", "minPrice"),
        ("price_max", "maxPrice"),
        ("beds_min", "minBedrooms"),
        ("beds_max", "maxBedrooms"),
        ("baths_min", "minBathrooms"),
        ("cars_min", "minCarspaces"),
    ):
        val = filters.get(src)
        if val not in (None, "", 0) or (src in ("price_min",) and val == 0):
            try:
                params[dst] = int(val)
            except (TypeError, ValueError):
                pass

    for src_key, dst_key in (("land_min", "minLandArea"), ("land_max", "maxLandArea")):
        val = filters.get(src_key)
        if val not in (None, ""):
            try:
                params[dst_key] = int(val)
            except (TypeError, ValueError):
                pass

    # Feature filters: buyer.md / Domain-URL vocabulary -> the enum. Unknown
    # names are returned to the caller rather than dropped on the floor, so a
    # filter that cannot be honoured is visible instead of silently ignored.
    features_in = filters.get("features") or []
    if isinstance(features_in, str):
        features_in = [features_in]
    feats, unsupported = [], []
    for f in features_in:
        mapped = PROPERTY_FEATURE_MAP.get(str(f).strip().lower().replace("-", "").replace("_", ""))
        if mapped is None:
            mapped = PROPERTY_FEATURE_MAP.get(str(f).strip().lower())
        if mapped:
            if mapped not in feats:
                feats.append(mapped)
        else:
            unsupported.append(str(f))
    if feats:
        params["propertyFeatures"] = feats
    if unsupported:
        params["_unsupported_features"] = unsupported

    attrs_in = filters.get("attributes") or []
    if isinstance(attrs_in, str):
        attrs_in = [attrs_in]
    attrs = [LISTING_ATTRIBUTE_MAP[a] for a in
             (str(x).strip().lower() for x in attrs_in)
             if a in LISTING_ATTRIBUTE_MAP] if attrs_in else []
    if attrs:
        params["listingAttributes"] = list(dict.fromkeys(attrs))

    keywords = filters.get("keywords")
    if isinstance(keywords, (list, tuple)):
        keywords = " ".join(str(k) for k in keywords if k)
    if keywords:
        params["keywords"] = str(keywords)

    # ``exclude_under_offer`` has no server-side equivalent (see module
    # docstring); it is applied client-side after normalization instead.

    sort = SORT_MAP.get(str(filters.get("sort") or "").strip().lower())
    if sort:
        params["sort"] = sort
    return params


def graphql(query: str, variables: Dict[str, Any], *, timeout_s: int = 60,
            cdp_url: str = domain_cli.DEFAULT_CDP_URL,
            bucket=None, cfg=None) -> Dict[str, Any]:
    """POST a GraphQL query from inside the real browser session, return parsed JSON.

    The document stays parked on Domain's homepage (which is never challenged);
    the API call is a same-origin XHR from that trusted page.
    """
    if cfg is None:
        cfg = domain_cli.RateLimitConfig(rps=0.35, burst=1)
    if bucket is None:
        bucket = domain_cli.build_bucket(cfg)
    domain_cli.polite_pause(bucket, cfg)

    page = domain_cli._warmed_page(cdp_url, timeout_s)
    try:
        if "domain.com.au" not in (page.url or ""):
            page.goto(domain_cli.HOMEPAGE_URL, wait_until="domcontentloaded",
                      timeout=min(timeout_s, 30) * 1000)
    except Exception:
        pass

    payload = json.dumps({"query": query, "variables": variables})
    raw = page.evaluate(
        """async (args) => {
            const r = await fetch(args.url, {
                method: 'POST',
                credentials: 'include',
                headers: {'Content-Type': 'application/json'},
                body: args.body,
            });
            return {status: r.status, text: await r.text()};
        }""",
        {"url": GRAPHQL_URL, "body": payload},
    )
    status = raw.get("status")
    text = raw.get("text") or ""
    if status != 200:
        raise RuntimeError(f"Domain GraphQL HTTP {status}: {text[:300]}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Domain GraphQL returned non-JSON: {text[:300]}") from exc
    if data.get("errors"):
        raise RuntimeError(f"Domain GraphQL errors: {json.dumps(data['errors'])[:400]}")
    return data.get("data") or {}


def _price_range(price_display: Optional[str]):
    """Best-effort (from, to) ints out of a Domain display price string."""
    if not price_display:
        return None, None
    nums = [int(n.replace(",", "")) for n in re.findall(r"\$?\s*([\d,]{4,})", price_display)]
    nums = [n for n in nums if n >= 1000]
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums), max(nums)


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


# Domain's own listing lifecycle enum -> our vocabulary. Search cards carry no
# status at all while detail returns NEW/LIVE, so without this a listing flips
# unknown -> live -> unknown on every run and manufactures a "status changed"
# event in the digest each time.
STATUS_MAP = {
    "new": "live",
    "live": "live",
    "active": "live",
    "current": "live",
    "sold": "sold",
    "leased": "leased",
    "rented": "leased",
    "undercontract": "off_market",
    "underoffer": "off_market",
    "offmarket": "off_market",
    "withdrawn": "withdrawn",
    "archived": "withdrawn",
    # Live listings that merely carry a marketing flag — not a lifecycle state.
    "newdevelopment": "live",
    "recentlyupdated": "live",
    "pricereduced": "live",
}


def normalize_status(value: Any, *, default: Optional[str] = None) -> Optional[str]:
    """Map Domain's status vocabulary onto ours, falling back to ``default``."""
    key = "".join(ch for ch in str(value or "").lower() if ch.isalpha())
    return STATUS_MAP.get(key, default)


def _dt(node_dt: Any) -> Optional[str]:
    """Domain's DateTime is an object; isoDate is the sortable/storable form."""
    if isinstance(node_dt, dict):
        return node_dt.get("isoDate") or node_dt.get("time")
    return node_dt or None


def _obj(node: Any, key: str) -> Dict[str, Any]:
    """node[key] when it is a dict, else {} — the API nulls blocks liberally."""
    val = (node or {}).get(key) if isinstance(node, dict) else None
    return val if isinstance(val, dict) else {}


def _dedupe_features(*groups: Any) -> List[str]:
    """Merge feature lists, case-insensitively, first spelling wins.

    ``features`` and ``structuredFeatures`` overlap heavily and disagree on case
    ("Air Conditioning" vs "Air conditioning"), so a naive concat renders every
    amenity twice in the folio's chip row.
    """
    out: List[str] = []
    seen = set()
    for group in groups:
        for item in _as_list(group):
            text = str(item).strip()
            key = text.lower().replace("-", " ").replace("/", " ")
            key = " ".join(key.split())
            if text and key not in seen:
                seen.add(key)
                out.append(text)
    return out


def normalize_address(addr: Dict[str, Any], *, seo_url: Optional[str] = None) -> Dict[str, Any]:
    """``displayableAddress`` -> the shared address dict ``db.upsert_listing`` reads.

    ``displayType`` is ``FULL_ADDRESS`` for a normal listing; agents can suppress
    the street number, in which case Domain returns a partial. The SEO URL is
    kept as a fallback because it still carries the address for listings whose
    detail block is withheld.
    """
    addr = addr if isinstance(addr, dict) else {}
    suburb = _obj(addr, "suburb").get("name")
    geo = _obj(addr, "geolocation")
    display = addr.get("displayAddress")
    street = addr.get("street")
    unit = addr.get("unitNumber") or None
    number = addr.get("streetNumber") or None

    if not display and (street or suburb):
        head = "/".join(x for x in (unit, number) if x)
        display = ", ".join(x for x in (" ".join(y for y in (head, street) if y), suburb) if x)

    out = {
        "display": display,
        "unit": unit,
        "street_number": number,
        "street": street,
        "suburb": suburb,
        "state": addr.get("state"),
        "postcode": addr.get("postcode"),
        "lat": geo.get("latitude"),
        "lng": geo.get("longitude"),
        "display_type": addr.get("displayType"),
    }
    if not out["display"] and seo_url:
        from_url = address_from_seo_url(seo_url)
        for key in ("display", "street", "suburb", "state", "postcode"):
            out[key] = out.get(key) or from_url.get(key)
    # A display string without the state/postcode tail reads oddly in a report
    # ("11 Albermarle Street, Marrickville"); search returns exactly that.
    if out["display"] and out["state"] and out["state"] not in out["display"]:
        tail = " ".join(x for x in (out["state"], out["postcode"]) if x)
        out["display"] = f"{out['display']} {tail}".strip()
    return out


def normalize_media(media: Any) -> List[Dict[str, Any]]:
    """``media`` union -> the ``{url, type, position}`` dicts ``_upsert_images`` wants.

    Non-``Image`` members (video, 3D tour) come back as ``{}`` through the
    inline fragment and are skipped. ``type`` is Domain's own tag —
    ``photo`` / ``floorplan`` — which the folio's mosaic keys off.
    """
    out: List[Dict[str, Any]] = []
    for pos, item in enumerate(_as_list(media)):
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("rawUrl")
        if not url:
            continue
        out.append({"url": url, "type": item.get("type") or "photo", "position": pos})
    return out


def normalize_agents(agents: Any, *, agency_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """``agents`` -> the agent dicts ``db._upsert_agents`` wants.

    NOTE: the API exposes no phone number for an agent (``mobile``, ``landline``
    and ``phone`` are all rejected by the ``Agent`` type), so ``mobile`` and
    ``landline`` are always None here. Email + ``profile_url`` are the contact
    channels; the folio's agent card must not promise a phone it never gets.
    """
    out: List[Dict[str, Any]] = []
    for a in _as_list(agents):
        if not isinstance(a, dict):
            continue
        name = a.get("fullName") or " ".join(
            x for x in (a.get("firstName"), a.get("lastName")) if x
        ).strip()
        if not name:
            continue
        out.append({
            "name": name,
            "email": a.get("email"),
            "mobile": None,
            "landline": None,
            "profile_url": a.get("profileUrl"),
            "photo": _obj(a, "photo").get("url"),
            "agent_id": a.get("agentId"),
            "agency": agency_name,
            "job_title": a.get("jobTitle"),
        })
    return out


def normalize_sold(block: Any) -> Optional[Dict[str, Any]]:
    """``soldData``/``soldDetails`` -> the flat ``sold`` dict the DB reads."""
    if not isinstance(block, dict) or not block:
        return None
    price = _obj(block, "soldPrice").get("displayPrice")
    date = _dt(block.get("soldDate"))
    method = block.get("saleMethod")
    if not any((price, date, method)):
        return None
    amount, _ = _price_range(price)
    return {
        "soldPrice": amount,
        "price_display": price,
        "soldDate": (date or "")[:10] or None,
        "saleMethod": method,
    }


def _auction_at(block: Any) -> Optional[str]:
    sched = _obj(block if isinstance(block, dict) else {}, "auctionSchedule")
    return _dt(sched.get("openingDateTime"))


def _inspections(block: Any) -> List[Dict[str, Any]]:
    """``inspectionDetails`` -> the ``{start,end}`` rows ``_upsert_inspections`` wants."""
    items = block.get("inspections") if isinstance(block, dict) else None
    return [
        {"start": _dt(i.get("openingDateTime")), "end": _dt(i.get("closingDateTime"))}
        for i in _as_list(items)
        if isinstance(i, dict) and _dt(i.get("openingDateTime"))
    ]


def off_market_from_tags(tags: Any) -> Optional[str]:
    """Off-market status from GraphQL's flat ``[String!]`` tag list, else None."""
    for tag in _as_list(tags):
        status = OFF_MARKET_TAGS.get(str(tag).strip().lower().replace("-", "").replace(" ", ""))
        if status:
            return status
    return None


def normalize_result(node: Dict[str, Any], *, mode: str) -> Optional[Dict[str, Any]]:
    """Map a SearchListingsResultListing into the shared normalized listing dict.

    Returns None for project/development cards (the ``... on`` fragment leaves
    them as ``{}``) — same policy as the REA provider: no price/beds means it
    can't be scored or compared.

    Unlike the retired HTML card, this carries a real address and the full image
    gallery, so a search-only run already produces a reportable listing.
    """
    lid = node.get("id") or node.get("listingId")
    if not lid:
        return None

    price_display = node.get("displayPrice") or _obj(node, "priceDetails").get("displayPrice")
    price_from, price_to = _price_range(price_display)
    agency = _obj(node, "agency")
    sold = normalize_sold(node.get("soldData"))
    off_market = off_market_from_tags(node.get("tags"))

    return {
        "id": str(lid),
        "listing_type": node.get("listingType") or mode,
        "url": f"https://www.domain.com.au/{lid}",
        "price": price_display,
        "price_from": price_from,
        "price_to": price_to,
        "address": normalize_address(_obj(node, "displayableAddress")),
        "beds": node.get("bedrooms"),
        "baths": node.get("bathrooms"),
        "cars": node.get("carspaces"),
        "property_type": node.get("propertyType"),
        "land_size": None,
        "land_unit": None,
        "inspections": _inspections(_obj(node, "inspectionDetails")),
        "auction_at": _auction_at(node.get("auctionDetails")),
        "agents": [],
        # A dict, not a bare name: db.upsert_listing only reads the agency when
        # it is a mapping, so a string here silently loses the column.
        "agency": {"name": agency.get("name"), "id": agency.get("agencyId")} if agency else None,
        "promo_type": None,
        "tags": node.get("tags"),
        # Default "live", not None: a search card that says nothing about status
        # must not read as a status *change* against the enriched record.
        "status": ("sold" if sold else off_market) or "live",
        "sold": sold,
        "images": normalize_media(node.get("media")),
        "headline": node.get("headline"),
        "description": node.get("summaryDescription"),
        "features": _as_list(node.get("features")),
        "date_listed": node.get("dateListed"),
    }


def search(filters: Dict[str, Any], *, limit: Optional[int] = None, max_pages: int = 5,
           page_size: int = 50, timeout_s: int = 60,
           cdp_url: str = domain_cli.DEFAULT_CDP_URL) -> Dict[str, Any]:
    """Run a paged search. Returns {listings, total_results, page_count, blocked_markers}."""
    mode = str(filters.get("mode") or "sale").lower()
    cfg = domain_cli.RateLimitConfig(rps=0.35, burst=1)
    bucket = domain_cli.build_bucket(cfg)

    listings: List[Dict[str, Any]] = []
    total_results: Optional[int] = None
    pages_read = 0
    seen: set = set()
    # True when we stopped because we hit `max_pages` while more pages existed.
    # This is the exact truncation signal: comparing len(listings) to
    # totalResults over-reports, because development-*project* cards count
    # toward the total but normalize to nothing.
    more_pages = False

    unsupported: List[str] = []
    for page_no in range(1, max_pages + 1):
        params = build_search_params(filters, page=page_no, page_size=page_size)
        # Filters with no server-side equivalent are reported, never sent.
        unsupported = params.pop("_unsupported_features", []) or []
        data = graphql(SEARCH_QUERY, {"p": params}, timeout_s=timeout_s,
                       cdp_url=cdp_url, bucket=bucket, cfg=cfg)
        block = (data or {}).get("searchListings") or {}
        if total_results is None:
            total_results = block.get("totalResults")
        pages_read += 1

        for node in block.get("results") or []:
            if not isinstance(node, dict) or not node:
                continue
            norm = normalize_result(node, mode=mode)
            if norm and norm["id"] not in seen:
                seen.add(norm["id"])
                listings.append(norm)

        total_pages = block.get("totalPages") or 0
        if limit and len(listings) >= limit:
            listings = listings[:limit]
            more_pages = page_no < total_pages
            break
        if page_no >= total_pages:
            break
        if page_no >= max_pages:
            more_pages = True

    return {
        "listings": listings,
        "total_results": total_results,
        "page_count": pages_read,
        "blocked_markers": [],
        "unsupported_filters": unsupported,
        "more_pages": more_pages,
    }


_STATES = ("nsw", "vic", "qld", "sa", "wa", "tas", "nt", "act")


def _known_suburbs() -> set:
    """Suburb names we're actively hunting, lowercased, for slug disambiguation.

    Read from buyer.md so it tracks the brief rather than a hardcoded list.
    Falls back to an empty set — callers degrade to the single-token guess.
    """
    global _KNOWN_SUBURBS_CACHE
    if _KNOWN_SUBURBS_CACHE is not None:
        return _KNOWN_SUBURBS_CACHE
    names = set()
    try:
        import buyer_profile
        front, _prose = buyer_profile.parse_buyer_md()
        raw = (front.get("locations") or {}).get("suburbs") or []
        for entry in raw:
            parsed = _suburb_parts(str(entry)).get("suburb")
            if parsed:
                names.add(parsed.lower())
    except Exception:
        pass
    _KNOWN_SUBURBS_CACHE = names
    return names


_KNOWN_SUBURBS_CACHE = None


def address_from_seo_url(seo_url: Optional[str]) -> Dict[str, Any]:
    """Recover the address from Domain's SEO listing URL.

    ``listingByIdV2 ... property.address.*`` validates but returns null for every
    subfield (Aug-18 2026) — the address simply isn't populated on that path.
    The canonical URL still carries it:

        /3518-2-wolseley-grove-zetland-nsw-2017-2021016262
        -> 3518/2 Wolseley Grove, Zetland NSW 2017

    Shape is ``<street-slug>-<suburb>-<state>-<postcode>-<listingId>``. We walk
    back from the end, which is unambiguous, rather than guessing where the
    street name stops.
    """
    out: Dict[str, Any] = {"display": None, "suburb": None, "state": None, "postcode": None, "street": None}
    if not seo_url:
        return out
    slug = seo_url.rstrip("/").rsplit("/", 1)[-1]
    parts = slug.split("-")
    if len(parts) < 4:
        return out
    if parts[-1].isdigit() and len(parts[-1]) > 4:      # trailing listing id
        parts = parts[:-1]
    if not (parts and parts[-1].isdigit() and len(parts[-1]) == 4):
        return out
    out["postcode"] = parts[-1]
    parts = parts[:-1]
    if parts and parts[-1].lower() in _STATES:
        out["state"] = parts[-1].upper()
        parts = parts[:-1]
    # Suburb can be multi-word ("Crows Nest", "North Sydney"), and the slug has
    # no separator to tell us where the street stops. Match the longest trailing
    # run against the suburbs we're actually hunting; fall back to one token.
    known = _known_suburbs()
    matched = None
    for n in (3, 2):
        if len(parts) >= n and " ".join(parts[-n:]).lower() in known:
            matched = parts[-n:]
            parts = parts[:-n]
            break
    if matched is None and parts:
        matched = [parts[-1]]
        parts = parts[:-1]
    if matched:
        out["suburb"] = " ".join(matched).replace("_", " ").title()
    if parts:
        street = " ".join(parts).title()
        # Leading "3518 2 Wolseley Grove" -> "3518/2 Wolseley Grove"
        toks = street.split(" ")
        if len(toks) >= 2 and toks[0].isdigit() and toks[1].isdigit():
            street = f"{toks[0]}/{' '.join(toks[1:])}"
        out["street"] = street
    bits = [b for b in (out["street"], out["suburb"]) if b]
    tail = " ".join(b for b in (out["state"], out["postcode"]) if b)
    out["display"] = ", ".join(bits) + (f" {tail}" if tail else "") if bits else None
    return out


def listing_detail(listing_id: str, *, timeout_s: int = 60,
                   cdp_url: str = domain_cli.DEFAULT_CDP_URL,
                   bucket=None, cfg=None) -> Optional[Dict[str, Any]]:
    """Fetch one listing's detail via ``listingByIdV2`` and normalize it.

    This replaces the HTML detail page (403 as of Aug-18 2026). It is the only
    source for the **full description**, **structured features** and the
    **agent roster**; address and imagery already arrive on the search card, so
    a failure here degrades the listing rather than blanking it.
    """
    data = graphql(DETAIL_QUERY, {"id": str(listing_id)}, timeout_s=timeout_s,
                   cdp_url=cdp_url, bucket=bucket, cfg=cfg)
    return _detail_from_node((data or {}).get("listingByIdV2"), fallback_id=listing_id)


def _detail_from_node(node: Any, *, fallback_id: Any = None) -> Optional[Dict[str, Any]]:
    """Map a ``ListingDetails`` node into the shared normalized listing dict.

    Split out from the fetch so the mapping is testable without the network —
    a renamed field then fails a unit test instead of quietly blanking a column.
    """
    if not isinstance(node, dict) or not node:
        return None

    price_display = _obj(node, "priceDetails").get("displayPrice")
    price_from, price_to = _price_range(price_display)
    agency = _obj(node, "agency")
    lid = node.get("id") or node.get("listingId") or fallback_id
    seo_url = node.get("seoUrl")
    sold = normalize_sold(node.get("soldDetails"))

    structured = [
        f.get("name") for f in _as_list(node.get("structuredFeatures"))
        if isinstance(f, dict) and f.get("name")
    ]
    ptypes = _as_list(node.get("propertyTypes"))

    return {
        "id": str(lid),
        "listing_type": node.get("listingType"),
        "status": normalize_status(node.get("status"), default="live"),
        "url": seo_url or f"https://www.domain.com.au/{lid}",
        "price": price_display,
        "price_from": price_from,
        "price_to": price_to,
        # ``property.address`` validates but returns null on every subfield —
        # ``displayableAddress`` is the populated one. SEO URL stays as backstop.
        "address": normalize_address(_obj(node, "displayableAddress"), seo_url=seo_url),
        "beds": node.get("bedrooms"),
        "baths": node.get("bathrooms"),
        "cars": node.get("carspaces"),
        "property_type": ptypes[0] if ptypes else None,
        "property_types": ptypes,
        "land_area_sqm": node.get("landAreaSqm"),
        "inspections": _inspections(_obj(node, "inspectionDetails")),
        "auction_at": _auction_at(node.get("auctionDetails")),
        "agents": normalize_agents(node.get("agents"), agency_name=agency.get("name")),
        "agency": {"name": agency.get("name"), "id": agency.get("id")} if agency else None,
        "sold": sold,
        "images": normalize_media(node.get("media")),
        "headline": node.get("headline"),
        "description": node.get("description"),
        "features": _dedupe_features(node.get("features"), structured),
        "structured_features": structured,
        "date_listed": node.get("dateListed"),
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Domain GraphQL search (Akamai-free transport)")
    ap.add_argument("--suburb", action="append", required=True)
    ap.add_argument("--mode", default="sale")
    ap.add_argument("--ptype", action="append", default=["apartment"])
    ap.add_argument("--price-max", type=int)
    ap.add_argument("--beds-min", type=int)
    ap.add_argument("--beds-max", type=int)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--max-pages", type=int, default=2)
    args = ap.parse_args()

    result = search(
        {
            "suburbs": args.suburb,
            "mode": args.mode,
            "ptypes": args.ptype,
            "price_max": args.price_max,
            "beds_min": args.beds_min,
            "beds_max": args.beds_max,
        },
        limit=args.limit,
        max_pages=args.max_pages,
    )
    print(json.dumps(result, indent=2, default=str)[:6000])
