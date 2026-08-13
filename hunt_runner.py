#!/usr/bin/env python3
"""Scheduled property hunter.

Runs saved Domain searches (see hunts.json), detects listings that are new
since the last run, and prints a digest. Designed to be driven by cron.

State (which listing IDs we have already seen per hunt) lives in
.cache/hunt_seen.json so reruns only surface genuinely new matches.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from db import DEFAULT_DB_PATH, PropertyDB
from buyer_profile import DEFAULT_BUYER, parse_buyer_md
from decision_engine import analyse_listing
from domain_cli import listing_url_for_id, sold_status_from_tags
from report_ux import format_daily_digest
from source_providers import DomainListingProvider, ListingProvider

HERE = Path(__file__).resolve().parent
DEFAULT_HUNTS = HERE / "hunts.json"


def load_hunts(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [h for h in data.get("hunts", []) if h.get("enabled", True)]


def _decision_summary(decision: Dict[str, Any]) -> Dict[str, Any]:
    valuation = decision.get("valuation") or {}
    case = decision.get("valuation_case") or {}
    estimate = case.get("independent_estimate") or {}
    asking = case.get("asking_comparison") or {}
    risks = decision.get("risks") or {}
    action = (decision.get("action_plan") or {}).get("best_next_action") or {}
    diligence = decision.get("due_diligence") or {}
    viability = decision.get("viability") or {}
    return {
        "viability_score": viability.get("score"),
        "viability_band": viability.get("band"),
        "price_fairness": valuation.get("fairness"),
        "valuation_confidence": valuation.get("confidence"),
        "independent_estimate": estimate.get("point"),
        "independent_range": (
            [estimate.get("low"), estimate.get("high")]
            if estimate.get("low") is not None or estimate.get("high") is not None
            else None
        ),
        "independent_confidence": estimate.get("confidence"),
        "asking_vs_estimate": asking.get("verdict"),
        "top_risk": risks.get("summary"),
        "due_diligence": diligence.get("summary"),
        "next_action": action.get("label"),
        "next_action_deadline": action.get("deadline"),
    }


def card_summary(listing: Dict[str, Any], decision: Dict[str, Any] | None = None) -> Dict[str, Any]:
    addr = listing.get("address") or {}
    summary = {
        "id": listing.get("id"),
        "price": listing.get("price"),
        "address": addr.get("display") or ", ".join(
            str(x) for x in (addr.get("street"), addr.get("suburb"), addr.get("state")) if x
        ),
        "beds": listing.get("beds"),
        "baths": listing.get("baths"),
        "cars": listing.get("cars"),
        "url": listing.get("url") or listing_url_for_id(listing.get("id", "")),
        "inspection": listing.get("inspection") or listing.get("inspections"),
    }
    if decision:
        summary["decision"] = _decision_summary(decision)
    return summary


def stored_card_summary(db: PropertyDB, listing_id: str, *, since: str | None = None) -> Dict[str, Any]:
    row = db.conn.execute(
        "SELECT id, address_display, price_display, beds, baths, cars, url FROM listings WHERE id=?",
        (str(listing_id),),
    ).fetchone()
    if not row:
        return card_summary({"id": listing_id})
    return {
        "id": row["id"],
        "price": row["price_display"],
        "address": row["address_display"],
        "beds": row["beds"],
        "baths": row["baths"],
        "cars": row["cars"],
        "url": row["url"] or listing_url_for_id(str(row["id"])),
        "lifecycle": db.lifecycle_summary(str(row["id"]), since=since),
    }


def run_hunt(
    hunt: Dict[str, Any],
    *,
    headed: bool,
    mark: bool,
    db: PropertyDB,
    front: Dict[str, Any],
    provider: ListingProvider | None = None,
) -> Dict[str, Any]:
    name = hunt["name"]
    filters = hunt["filters"]
    mode = filters.get("mode", "sale")
    provider = provider or DomainListingProvider()
    db.upsert_hunt(name, filters, enabled=hunt.get("enabled", True))
    previous_run_at = db.previous_run_at(name)

    payload = provider.search(filters, headed=headed, limit=hunt.get("max_items"))
    url = payload.source_url
    listings = payload.listings
    blocked = bool(payload.blocked_markers)

    seen_ids = db.seen_ids(name)
    # Cards tagged sold/leased/under-offer are off-market — persist them for
    # history/comps but never surface them as live "new" or "changed" candidates
    # (a sold listing must not read as a buy opportunity).
    offmarket_ids = {str(l["id"]) for l in listings if l.get("id") and sold_status_from_tags(l)}
    new_listings = [
        l for l in listings
        if l.get("id") and l["id"] not in seen_ids and str(l["id"]) not in offmarket_ids
    ]

    if hunt.get("enrich") and new_listings:
        for i, card in enumerate(new_listings):
            try:
                detail = provider.listing(str(card["id"]), headed=headed)
                if detail:
                    new_listings[i] = detail
            except Exception as exc:
                new_listings[i] = {**card, "_enrich_error": str(exc)}

    # Persist every listing we saw (not just the new ones) so history/stats accrue.
    for listing in listings:
        if listing.get("id"):
            db.upsert_listing(listing, mode=mode)
    for listing in new_listings:  # re-upsert enriched detail over the card
        if listing.get("id"):
            db.upsert_listing(listing, mode=mode)

    decisions: Dict[str, Dict[str, Any]] = {}
    for listing in new_listings:
        if listing.get("id"):
            try:
                decisions[str(listing["id"])] = analyse_listing(listing, front, db=db)
            except Exception as exc:
                decisions[str(listing["id"])] = {"error": str(exc)}

    all_ids = [l["id"] for l in listings if l.get("id")]
    new_ids = [l["id"] for l in new_listings if l.get("id")]
    new_id_set = {str(x) for x in new_ids}
    changed_ids = set() if blocked else (
        db.changed_listing_ids(all_ids, since=previous_run_at) - new_id_set - offmarket_ids
    )
    by_id = {str(l["id"]): l for l in listings if l.get("id")}
    changed = []
    for lid in sorted(changed_ids):
        summary = card_summary(by_id.get(lid, {"id": lid}))
        summary["lifecycle"] = db.lifecycle_summary(lid, since=previous_run_at)
        changed.append(summary)
    stale_ids = [] if blocked else sorted(str(lid) for lid in seen_ids - {str(lid) for lid in all_ids})
    if stale_ids:
        db.mark_listings_stale(stale_ids)
    stale = [stored_card_summary(db, lid, since=previous_run_at) for lid in stale_ids[:10]]
    if mark:
        db.record_run(
            name, url,
            total_results=payload.total_results,
            page_count=payload.page_count,
            new_ids=new_ids, all_ids=all_ids,
            blocked=blocked,
        )

    supply = db.supply_trend(name)

    return {
        "name": name,
        "url": url,
        "provider": payload.provider,
        "total_results": payload.total_results,
        "page_count": payload.page_count,
        "supply": supply,
        "new_count": len(new_listings),
        "changed_count": len(changed),
        "stale_count": len(stale_ids),
        "new": [card_summary(l, decisions.get(str(l.get("id")))) for l in new_listings],
        "changed": changed,
        "stale": stale,
        "blocked": payload.blocked_markers,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hunt_runner.py")
    ap.add_argument("--hunts", default=str(DEFAULT_HUNTS))
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--name", help="Run only this hunt")
    ap.add_argument("--headed", action="store_true", default=True, help="Headed browser (default on; needed to beat Akamai)")
    ap.add_argument("--headless", dest="headed", action="store_false")
    ap.add_argument("--no-mark", dest="mark", action="store_false", help="Dry run: do not record the run in the DB")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of a text digest")
    args = ap.parse_args(argv)

    hunts = load_hunts(Path(args.hunts))
    if args.name:
        hunts = [h for h in hunts if h.get("name") == args.name]
    front, _ = parse_buyer_md(DEFAULT_BUYER)

    with PropertyDB(Path(args.db)) as db:
        results = [run_hunt(h, headed=args.headed, mark=args.mark, db=db, front=front) for h in hunts]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hunts": results,
        "total_new": sum(r["new_count"] for r in results),
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(format_daily_digest(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
