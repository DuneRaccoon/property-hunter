#!/usr/bin/env python3
"""Local HTTP wrapper around the listing pipeline.

Routes ``/domain/*`` through ``source_providers``' fallback chain (GraphQL API
first, realestate.com.au second) rather than calling the HTML scraper directly.
The scraper 403s on every Domain route that matters from this IP as of Aug-18
2026, so the previous direct-``fetch_html`` implementation returned an Akamai
block page for every request. Request and response shapes are unchanged.

``FetchOptions`` (fetcher/ua/proxy/cache) is kept for wire compatibility with
existing callers but is now inert: transport is the provider's concern.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from domain_cli import (
    DEFAULT_CACHE_DIR,
    DEFAULT_CDP_URL,
    DEFAULT_PROFILE_DIR,
    DEFAULT_UA,
    SEARCH_MODES,
    build_search_url,
    listing_url_for_id,
)
from source_providers import build_provider_chain


class FetchOptions(BaseModel):
    fetcher: Literal["cdp", "playwright", "http"] = "cdp"
    cdp_url: str = DEFAULT_CDP_URL
    ua: str = DEFAULT_UA
    rps: float = Field(default=0.35, gt=0)
    burst: int = Field(default=1, ge=1)
    timeout_s: int = Field(default=60, ge=5)
    cache_dir: str = str(DEFAULT_CACHE_DIR)
    no_cache: bool = False
    headed: bool = False
    profile_dir: str = str(DEFAULT_PROFILE_DIR)
    proxy: Optional[str] = Field(default_factory=lambda: os.environ.get("DOMAIN_PROXY"))


class SearchFilters(BaseModel):
    """Dynamic Domain search filters. Used when no explicit url is given."""

    mode: Literal["sale", "rent", "sold"] = "sale"
    suburbs: List[str] = Field(default_factory=list, description="Slugs or 'Name STATE postcode'")
    region: Optional[str] = None
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    beds_min: Optional[int] = None
    beds_max: Optional[int] = None
    baths_min: Optional[int] = None
    cars_min: Optional[int] = None
    ptypes: List[str] = Field(default_factory=list)
    exclude_under_offer: bool = False
    features: List[str] = Field(default_factory=list)
    keywords: Optional[str] = None
    sort: Optional[str] = None
    page: Optional[int] = None

    def to_url(self) -> str:
        return build_search_url(
            mode=self.mode,
            suburbs=self.suburbs,
            region=self.region,
            price_min=self.price_min,
            price_max=self.price_max,
            beds_min=self.beds_min,
            beds_max=self.beds_max,
            baths_min=self.baths_min,
            cars_min=self.cars_min,
            ptypes=self.ptypes,
            exclude_under_offer=self.exclude_under_offer,
            features=self.features,
            keywords=self.keywords,
            sort=self.sort,
            page=self.page,
        )


class SearchRequest(FetchOptions):
    url: Optional[str] = None
    filters: Optional[SearchFilters] = None
    limit: Optional[int] = None

    def resolve_url(self) -> str:
        if self.url:
            return self.url
        if self.filters and (self.filters.suburbs or self.filters.region):
            return self.filters.to_url()
        raise HTTPException(status_code=422, detail="Provide 'url' or 'filters' with suburbs/region.")


class ListingRequest(FetchOptions):
    url: Optional[str] = None
    id: Optional[str] = None


class ReportRequest(SearchRequest):
    max_items: int = Field(default=12, ge=1, le=100)
    enrich: bool = Field(default=False, description="Fetch full detail pages for each result")
    enrich_max: int = Field(default=10, ge=1, le=40)


app = FastAPI(title="Property Hunter Domain API", version="0.1.0")


def _search(filters: "SearchFilters", limit: Optional[int] = None) -> dict:
    """Run the provider chain and return a payload in the legacy response shape."""
    payload = filters.model_dump()
    payload["mode"] = filters.mode
    for provider in build_provider_chain():
        try:
            result = provider.search(payload, headed=True, limit=limit)
        except Exception:
            continue
        if not result.blocked_markers:
            return {
                "provider": result.provider,
                "source_url": result.source_url,
                "search_result_count": result.total_results,
                "count": len(result.listings),
                "listings": result.listings,
                "blocked_markers": [],
                "unsupported_filters": list(result.unsupported_filters),
            }
    raise HTTPException(status_code=502, detail="Every listing provider was blocked or errored.")


def _listing(listing_id: str) -> dict:
    for provider in build_provider_chain():
        try:
            detail = provider.listing(str(listing_id), headed=True)
        except Exception:
            continue
        if detail:
            return {"listing": detail}
    raise HTTPException(status_code=404, detail=f"No provider could resolve listing {listing_id}.")


@app.get("/health")
def health():
    return {"ok": True, "providers": [p.name for p in build_provider_chain()]}


@app.post("/domain/search")
def domain_search(req: SearchRequest):
    if not req.filters:
        raise HTTPException(
            status_code=422,
            detail="Provide 'filters'. A raw 'url' can no longer be fetched: Domain's "
                   "document routes are Akamai-blocked and the API takes structured params.",
        )
    return _search(req.filters, limit=req.limit)


@app.post("/domain/listing")
def domain_listing(req: ListingRequest):
    listing_id = req.id
    if not listing_id and req.url:
        tail = req.url.rstrip("/").rsplit("-", 1)[-1]
        listing_id = tail if tail.isdigit() else None
    if not listing_id:
        raise HTTPException(status_code=422, detail="Provide 'id' (or a URL ending in one).")
    return _listing(listing_id)


@app.post("/reports/daily")
def daily_report(req: ReportRequest):
    if not req.filters:
        raise HTTPException(status_code=422, detail="Provide 'filters'.")
    payload = _search(req.filters, limit=req.limit)
    listings = payload["listings"][: req.max_items]

    if req.enrich:
        chain = build_provider_chain()
        enriched = []
        for card in listings[: req.enrich_max]:
            listing_id = card.get("id")
            if not listing_id:
                enriched.append(card)
                continue
            try:
                detail = _listing(str(listing_id)).get("listing")
                # Merge rather than replace: detail is richer but not a superset
                # (tags and the off-market status live only on the search card).
                enriched.append({**card, **{k: v for k, v in (detail or {}).items()
                                            if v not in (None, [], {})}} if detail else card)
            except Exception as exc:  # keep the card on failure rather than dropping it
                enriched.append({**card, "_enrich_error": str(exc)})
        listings = enriched + listings[req.enrich_max :]

    return {
        "provider": payload["provider"],
        "source_url": payload["source_url"],
        "blocked_markers": payload["blocked_markers"],
        "unsupported_filters": payload["unsupported_filters"],
        "search_result_count": payload["search_result_count"],
        "returned": len(listings),
        "enriched": req.enrich,
        "highlights": listings,
    }
