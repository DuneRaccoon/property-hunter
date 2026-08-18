#!/usr/bin/env python3
"""Listing provider abstraction.

Domain remains the first provider, but hunt orchestration should not be welded
directly to one scraper forever. Providers return normalised listing dicts plus
source attribution so downstream scoring knows where each observation came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

import domain_cli
import domain_graphql
import realestate_cli


@dataclass
class ListingSearchResult:
    provider: str
    source_url: str
    total_results: Optional[int]
    page_count: Optional[int]
    listings: List[Dict[str, Any]]
    blocked_markers: List[str]
    # Filters the provider could not express server-side (reported, not silently
    # dropped) so a digest never implies a criterion was applied when it wasn't.
    unsupported_filters: List[str] = field(default_factory=list)
    # True when the provider's result set is larger than the window it read, so
    # a listing's absence from `listings` says nothing about whether it is gone.
    # Computed before client-side filtering, which legitimately removes rows.
    truncated: bool = False


class ListingProvider(Protocol):
    name: str

    def search(self, filters: Dict[str, Any], *, headed: bool, limit: Optional[int] = None) -> ListingSearchResult:
        ...

    def listing(self, listing_id: str, *, headed: bool) -> Optional[Dict[str, Any]]:
        ...


def _with_source(listing: Dict[str, Any], *, provider: str, url: str) -> Dict[str, Any]:
    sources = list(listing.get("sources") or [])
    source = {"provider": provider, "url": url}
    if source not in sources:
        sources.append(source)
    return {**listing, "source_provider": provider, "source_url": url, "sources": sources}


def _blocked_markers(value: Any) -> List[str]:
    if value is True:
        return ["blocked"]
    if value in (False, None, ""):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _passes_filters(listing: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    """Client-side enforcement of range filters a provider can't express in its
    search URL. Missing values pass (we don't drop a listing for absent data)."""
    def _n(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    checks = (
        ("beds", "beds_min", "ge"), ("beds", "beds_max", "le"),
        ("baths", "baths_min", "ge"), ("cars", "cars_min", "ge"),
        ("price_from", "price_max", "le_price"),
    )
    for attr, fkey, op in checks:
        bound = filters.get(fkey)
        if bound is None:
            continue
        val = _n(listing.get(attr))
        if val is None:
            continue
        if op == "ge" and val < bound:
            return False
        if op == "le" and val > bound:
            return False
        if op == "le_price" and _n(listing.get("price_to") or val) is not None and val > bound:
            # only drop when the low end already exceeds the ceiling
            if val > bound:
                return False
    return True


def _is_off_market_excluded(listing: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    """True when the hunt asked to skip under-offer/sold stock and this is some.

    Domain's search schema has no under-offer exclusion, so the check lives here.
    Sold *hunts* obviously keep sold listings — the flag is only honoured when
    the hunt set it.
    """
    if not filters.get("exclude_under_offer"):
        return False
    status = str(listing.get("status") or "").lower()
    return status in ("off_market", "sold", "leased", "under_offer")


def _is_project_profile(listing: Dict[str, Any]) -> bool:
    """True for a REA new-development *profile* rather than a real listing.

    REA mixes ``/project/`` entries (e.g. a whole Meriton building) into search
    results. They carry no price, beds, baths or cars — just marketing copy and
    a gallery — so they can't be scored, priced or compared, and Domain never
    surfaced their equivalent. Keeping them would put blank spreads in the folio.
    """
    url = listing.get("url") or ""
    if "/project/" not in url:
        return False
    return not any(listing.get(f) is not None for f in ("price_from", "beds", "baths", "cars"))


class DomainListingProvider:
    name = "domain"

    def search(self, filters: Dict[str, Any], *, headed: bool, limit: Optional[int] = None) -> ListingSearchResult:
        # Fetched through a genuine running browser (fetcher="cdp"), so Domain's
        # combined ?suburb=a,b,c query form loads fine in one request — no need to
        # fan out per suburb. (The old per-suburb fan-out only existed to dodge the
        # Akamai block that hit the automated Playwright context, not this session.)
        url = domain_cli.build_search_url(**filters)
        html = domain_cli.fetch_html(url, fetcher="cdp", headed=headed, no_cache=True)
        payload = domain_cli.extract_search_payload(html, source_url=url, limit=limit)
        listings = [_with_source(l, provider=self.name, url=url) for l in payload.get("listings", [])]
        return ListingSearchResult(
            provider=self.name,
            source_url=url,
            total_results=payload.get("search_result_count"),
            page_count=payload.get("count"),
            listings=listings,
            blocked_markers=_blocked_markers(payload.get("blocked_markers")),
        )

    def listing(self, listing_id: str, *, headed: bool) -> Optional[Dict[str, Any]]:
        url = domain_cli.listing_url_for_id(listing_id)
        html = domain_cli.fetch_html(url, fetcher="cdp", headed=headed, no_cache=True)
        payload = domain_cli.extract_listing_payload(html, source_url=url, listing_id=listing_id)
        listing = payload.get("listing")
        return _with_source(listing, provider=self.name, url=url) if listing else None


class RealestateListingProvider:
    """realestate.com.au provider — the Domain fallback.

    Same interface and same normalized output as ``DomainListingProvider`` so it
    is a drop-in. REA's search slug can only express *minimum* bedrooms, so the
    remaining range filters (beds_max, baths_min, cars_min) are enforced
    client-side here to keep results honest against the hunt's criteria.
    """

    name = "realestate"

    def search(self, filters: Dict[str, Any], *, headed: bool, limit: Optional[int] = None) -> ListingSearchResult:
        mode = filters.get("mode", "sale")
        url = realestate_cli.build_search_url(**filters)
        html = realestate_cli.fetch_html(url, fetcher="cdp", headed=headed, no_cache=True)
        payload = realestate_cli.extract_search_payload(html, source_url=url, mode=mode)
        fetched = payload.get("listings", [])
        total = payload.get("search_result_count")
        truncated = bool(total is not None and total > len(fetched))
        kept = [
            l for l in fetched
            if _passes_filters(l, filters) and not _is_project_profile(l)
        ]
        if limit:
            kept = kept[:limit]
        listings = [_with_source(l, provider=self.name, url=url) for l in kept]
        return ListingSearchResult(
            provider=self.name,
            source_url=url,
            total_results=payload.get("search_result_count"),
            page_count=len(listings),
            listings=listings,
            blocked_markers=_blocked_markers(payload.get("blocked_markers")),
        )

    def listing(self, listing_id: str, *, headed: bool) -> Optional[Dict[str, Any]]:
        url = realestate_cli.listing_url_for_id(listing_id)
        html = realestate_cli.fetch_html(url, fetcher="cdp", headed=headed, no_cache=True)
        payload = realestate_cli.extract_listing_payload(html, source_url=url, listing_id=listing_id)
        listing = payload.get("listing")
        return _with_source(listing, provider=self.name, url=url) if listing else None


class DomainGraphQLProvider:
    """Domain via its own GraphQL API — the transport Akamai doesn't gate.

    From Aug-18 2026 every Domain *document* route (``/sale/``, ``/rent/``,
    ``/sold-listings/``, bare listing ids) returns 403 from the Pi's IP, by both
    ``page.goto`` and the same-origin ``fetch()`` XHR. ``POST /graphql`` — the
    API Domain's own front-end calls — is not challenged and returns 200.

    Both halves of the pipeline run on it: ``search`` and ``listing``
    (enrichment). Nothing here touches the HTML scraper, which would 403.

    Two filters have no server-side equivalent in the schema and are enforced
    here instead, so the hunt's stated criteria still hold:

    * ``exclude_under_offer`` — ``SearchListingsAttribute`` has no such value.
    * ``beds_max`` / ``baths_min`` / ``cars_min`` bounds are expressible, but
      re-checked anyway for parity with the REA provider.
    """

    name = "domain_graphql"

    def search(self, filters: Dict[str, Any], *, headed: bool, limit: Optional[int] = None) -> ListingSearchResult:
        # Filter client-side *before* honouring the limit, so an under-offer card
        # can't consume one of the caller's N slots.
        payload = domain_graphql.search(filters, limit=None)
        fetched = payload.get("listings", [])
        # Paging, not counts: `totalResults` includes development-project cards
        # that normalize to nothing, so a count comparison over-reports
        # truncation on project-heavy suburbs (Zetland, North Sydney).
        truncated = bool(payload.get("more_pages"))
        kept = [
            l for l in fetched
            if _passes_filters(l, filters) and not _is_off_market_excluded(l, filters)
        ]
        if limit:
            kept = kept[:limit]
        listings = [
            _with_source(l, provider=self.name, url=domain_graphql.GRAPHQL_URL)
            for l in kept
        ]
        # A filter the API cannot express is surfaced, not swallowed: the digest
        # must never imply a criterion was applied when it wasn't.
        markers = _blocked_markers(payload.get("blocked_markers"))
        return ListingSearchResult(
            provider=self.name,
            source_url=domain_graphql.GRAPHQL_URL,
            total_results=payload.get("total_results"),
            page_count=payload.get("page_count"),
            listings=listings,
            blocked_markers=markers,
            unsupported_filters=list(payload.get("unsupported_filters") or []),
            truncated=truncated,
        )

    def listing(self, listing_id: str, *, headed: bool) -> Optional[Dict[str, Any]]:
        listing = domain_graphql.listing_detail(str(listing_id))
        return _with_source(listing, provider=self.name, url=domain_graphql.GRAPHQL_URL) if listing else None


# Registry so hunts can name providers and the runner can build a fallback chain.
PROVIDERS: Dict[str, type] = {
    DomainGraphQLProvider.name: DomainGraphQLProvider,
    DomainListingProvider.name: DomainListingProvider,
    RealestateListingProvider.name: RealestateListingProvider,
}
# The HTML scraper ("domain") is 403 from this IP on every route that matters,
# so it is deliberately NOT in the default chain — leaving it in only bought a
# guaranteed-failing hop between the working API and the REA fallback.
DEFAULT_PROVIDER_CHAIN = ["domain_graphql", "realestate"]


def build_provider_chain(names: Optional[List[str]] = None) -> List[ListingProvider]:
    """Instantiate an ordered provider fallback chain from names."""
    chosen = names or DEFAULT_PROVIDER_CHAIN
    return [PROVIDERS[n]() for n in chosen if n in PROVIDERS]
