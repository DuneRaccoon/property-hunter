#!/usr/bin/env python3
"""Quick headed-Chrome Domain connectivity preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from buyer_profile import DEFAULT_BUYER, buyer_to_searches, parse_buyer_md
from domain_cli import DEFAULT_PROFILE_DIR
from source_providers import DomainListingProvider


def _sample_filters(buyer_path: Path) -> Dict[str, Any]:
    front, _ = parse_buyer_md(buyer_path)
    searches = buyer_to_searches(front)
    for search in searches:
        if (search.get("filters") or {}).get("mode") != "sold":
            return search["filters"]
    return searches[0]["filters"] if searches else {"mode": "sale", "suburbs": ["Zetland NSW 2017"]}


def check_domain(*, buyer_path: Path, headed: bool, limit: int) -> Dict[str, Any]:
    filters = _sample_filters(buyer_path)
    provider = DomainListingProvider()
    try:
        payload = provider.search(filters, headed=headed, limit=limit)
        blocked = bool(payload.blocked_markers)
        ok = not blocked and bool(payload.listings)
        return {
            "status": "ok" if ok else ("blocked" if blocked else "empty"),
            "provider": payload.provider,
            "profile_dir": str(DEFAULT_PROFILE_DIR),
            "source_url": payload.source_url,
            "count": len(payload.listings),
            "total_results": payload.total_results,
            "blocked_markers": payload.blocked_markers,
            "filters": filters,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "provider": provider.name,
            "profile_dir": str(DEFAULT_PROFILE_DIR),
            "error": str(exc),
            "filters": filters,
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="domain_connection_check.py")
    parser.add_argument("--buyer", default=str(DEFAULT_BUYER))
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--headed", action="store_true", default=True)
    parser.add_argument("--headless", dest="headed", action="store_false")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = check_domain(buyer_path=Path(args.buyer), headed=args.headed, limit=args.limit)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"status: {result['status']}")
        print(f"provider: {result.get('provider')}")
        print(f"profile: {result.get('profile_dir')}")
        if result.get("count") is not None:
            print(f"listings: {result['count']}")
        if result.get("blocked_markers"):
            print(f"blocked: {', '.join(result['blocked_markers'])}")
        if result.get("source_url"):
            print(f"url: {result['source_url']}")
        if result.get("error"):
            print(f"error: {result['error']}")
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
