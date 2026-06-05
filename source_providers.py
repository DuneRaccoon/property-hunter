#!/usr/bin/env python3
"""Listing provider abstraction.

Domain remains the first provider, but hunt orchestration should not be welded
directly to one scraper forever. Providers return normalised listing dicts plus
source attribution so downstream scoring knows where each observation came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from domain_cli import (
    build_search_url,
    extract_listing_payload,
    extract_search_payload,
    fetch_html,
    listing_url_for_id,
)


@dataclass
class ListingSearchResult:
    provider: str
    source_url: str
    total_results: Optional[int]
    page_count: Optional[int]
    listings: List[Dict[str, Any]]
    blocked_markers: List[str]


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


class DomainListingProvider:
    name = "domain"

    def search(self, filters: Dict[str, Any], *, headed: bool, limit: Optional[int] = None) -> ListingSearchResult:
        # Fetched through a genuine running browser (fetcher="cdp"), so Domain's
        # combined ?suburb=a,b,c query form loads fine in one request — no need to
        # fan out per suburb. (The old per-suburb fan-out only existed to dodge the
        # Akamai block that hit the automated Playwright context, not this session.)
        url = build_search_url(**filters)
        html = fetch_html(url, fetcher="cdp", headed=headed, no_cache=True)
        payload = extract_search_payload(html, source_url=url, limit=limit)
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
        url = listing_url_for_id(listing_id)
        html = fetch_html(url, fetcher="cdp", headed=headed, no_cache=True)
        payload = extract_listing_payload(html, source_url=url, listing_id=listing_id)
        listing = payload.get("listing")
        return _with_source(listing, provider=self.name, url=url) if listing else None
