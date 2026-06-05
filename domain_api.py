#!/usr/bin/env python3
"""Local API wrapper for the Domain structured-data fetcher."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from domain_cli import (
    DEFAULT_CACHE_DIR,
    DEFAULT_PROFILE_DIR,
    DEFAULT_UA,
    SEARCH_MODES,
    build_search_url,
    extract_listing_payload,
    extract_search_payload,
    fetch_html,
    listing_url_for_id,
)


class FetchOptions(BaseModel):
    fetcher: Literal["playwright", "http"] = "playwright"
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


def fetch_with_options(req: FetchOptions, url: str) -> str:
    return fetch_html(
        url,
        fetcher=req.fetcher,
        ua=req.ua,
        rps=req.rps,
        burst=req.burst,
        timeout_s=req.timeout_s,
        cache_dir=Path(req.cache_dir),
        no_cache=req.no_cache,
        headed=req.headed,
        profile_dir=Path(req.profile_dir),
        proxy=req.proxy,
    )


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/domain/search")
def domain_search(req: SearchRequest):
    url = req.resolve_url()
    html = fetch_with_options(req, url)
    return extract_search_payload(html, source_url=url, limit=req.limit)


@app.post("/domain/listing")
def domain_listing(req: ListingRequest):
    url = req.url or (listing_url_for_id(req.id) if req.id else None)
    if not url:
        raise HTTPException(status_code=422, detail="Provide either 'url' or 'id'.")
    html = fetch_with_options(req, url)
    return extract_listing_payload(html, source_url=url, listing_id=req.id)


@app.post("/reports/daily")
def daily_report(req: ReportRequest):
    url = req.resolve_url()
    html = fetch_with_options(req, url)
    payload = extract_search_payload(html, source_url=url, limit=req.limit)
    listings = payload.get("listings", [])[: req.max_items]

    if req.enrich:
        enriched = []
        for card in listings[: req.enrich_max]:
            listing_id = card.get("id")
            if not listing_id:
                enriched.append(card)
                continue
            detail_url = listing_url_for_id(listing_id)
            try:
                detail_html = fetch_with_options(req, detail_url)
                detail = extract_listing_payload(detail_html, source_url=detail_url, listing_id=listing_id)
                enriched.append(detail.get("listing") or card)
            except Exception as exc:  # keep the card on failure rather than dropping it
                card = {**card, "_enrich_error": str(exc)}
                enriched.append(card)
        listings = enriched + listings[req.enrich_max :]

    return {
        "source_url": url,
        "blocked_markers": payload.get("blocked_markers"),
        "search_result_count": payload.get("search_result_count"),
        "returned": len(listings),
        "enriched": req.enrich,
        "highlights": listings,
        "events": payload.get("events", []),
    }
