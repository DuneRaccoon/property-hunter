#!/usr/bin/env python3
"""Preflight Domain access for the property hunter.

This is intentionally browser-only by default. It exercises the same CDP path
the scheduled hunter uses and returns a small machine-readable status so cron
can fail loudly before corrupting downstream assumptions.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from buyer_profile import DEFAULT_BUYER, buyer_to_searches, parse_buyer_md
from domain_cli import DEFAULT_CDP_URL, build_search_url, extract_search_payload, fetch_html


def _first_buy_filters() -> Dict[str, Any]:
    front, _ = parse_buyer_md(DEFAULT_BUYER)
    for hunt in buyer_to_searches(front):
        filters = hunt.get("filters") or {}
        if filters.get("mode", "sale") == "sale":
            return filters
    return {
        "mode": "sale",
        "suburbs": ["Zetland NSW 2017"],
        "price_min": 0,
        "price_max": 1_200_000,
        "beds_min": 1,
        "baths_min": 1,
        "cars_min": 1,
        "ptypes": ["apartment"],
        "exclude_under_offer": True,
    }


def check_domain(*, timeout_s: int, cdp_url: str) -> Dict[str, Any]:
    filters = _first_buy_filters()
    url = build_search_url(**filters)
    try:
        html = fetch_html(url, fetcher="cdp", timeout_s=timeout_s, no_cache=True, cdp_url=cdp_url)
        payload = extract_search_payload(html, source_url=url, limit=5)
    except Exception as exc:
        return {
            "status": "failed",
            "provider": "domain",
            "cdp_url": cdp_url,
            "url": url,
            "error": str(exc),
            "filters": filters,
        }

    blocked = bool(payload.get("blocked_markers"))
    return {
        "status": "blocked" if blocked else "ok",
        "provider": "domain",
        "cdp_url": cdp_url,
        "url": url,
        "blocked_markers": payload.get("blocked_markers"),
        "search_result_count": payload.get("search_result_count"),
        "returned": payload.get("count"),
        "sample_listing_ids": payload.get("listing_ids", [])[:5],
        "filters": filters,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="domain_connection_check.py")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args(argv)

    result = check_domain(timeout_s=args.timeout, cdp_url=args.cdp_url)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Domain status: {result['status']}")
        print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
