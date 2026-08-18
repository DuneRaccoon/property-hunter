#!/usr/bin/env python3
"""Backfill listing detail onto rows the hunt never enriched.

Why this exists
---------------
The Aug-18 2026 cutover to Domain's GraphQL API replaced *search* but left
enrichment pointed at the HTML detail page, which 403s. Search cards were being
normalized with ``"address": {}``, so every listing stored in that window is a
price and a bare listing-id URL — unusable in a shortlist or a folio. Rows from
the earlier scraper era are also missing addresses, but for a different reason:
they were never enriched at all.

A row is "incomplete" when it is missing an **address**, an **agent** or a
**description** — the three things a shortlist entry and a folio spread need.

Two distinct causes, both repaired here:

* Rows stored during the Aug-18 cutover window, whose search cards normalized
  to ``"address": {}`` because enrichment still pointed at the 403ing scraper.
* Rows the hunt simply never enriched: ``run_hunt`` only enriches listings that
  are *new since the last run*, so anything already seen keeps whatever the
  search card gave it, forever. That is why listings on the current shortlist
  could have a perfectly good address and still no agent contact.

Two repair paths. **The API is authoritative**; the URL is the fallback:

1. **Live, via ``listingByIdV2``** — returns the structured address, and lands
   images, agents, the full description and inspections at the same time.
2. **Offline, from the stored URL** — Domain's canonical SEO URL encodes the
   address (``/705-178-livingstone-road-marrickville-nsw-2204-2021067641``),
   which is free but lossy: the slug's single ``-`` separator cannot tell a
   unit from a street-number range, so ``735-737 Elizabeth Street`` parses as
   ``735/737``, and a street type dropped from the slug stays dropped. Good
   enough to identify a delisted row, not good enough to put in a folio — so it
   is only used when the API has nothing (``--offline-only`` forces it).

Path 1 is rate-limited by ``domain_graphql``'s own polite bucket and is
resumable: rerunning only picks up what is still missing. Listings that have
left Domain return nothing, fall back to the URL, and are counted as ``gone``
when that fails too — not retried forever.

    python3 backfill_detail.py --dry-run
    python3 backfill_detail.py --scope agents --active-only
    python3 backfill_detail.py --scope address --limit 50
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

import domain_graphql
from db import DEFAULT_DB_PATH, PropertyDB

MISSING_ADDRESS = "(address_display IS NULL OR address_display = '')"
MISSING_AGENTS = "(NOT EXISTS (SELECT 1 FROM listing_agents la WHERE la.listing_id = listings.id))"
MISSING_DESCRIPTION = "(description IS NULL OR length(description) < 200)"

# Sold comps only ever need address + price, and re-enriching thousands of them
# would be a lot of API calls for data no report reads.
LIVE_ONLY = "mode <> 'sold'"

SCOPES = {
    "address": MISSING_ADDRESS,
    "agents": f"{MISSING_AGENTS} AND {LIVE_ONLY}",
    "description": f"{MISSING_DESCRIPTION} AND {LIVE_ONLY}",
    "any": f"({MISSING_ADDRESS} OR (({MISSING_AGENTS} OR {MISSING_DESCRIPTION}) AND {LIVE_ONLY}))",
}


def incomplete_rows(db: PropertyDB, *, scope: str = "any",
                    provider: str | None = None,
                    active_only: bool = False) -> List[Dict[str, Any]]:
    where = SCOPES[scope]
    sql = f"SELECT id, url, mode, source_provider FROM listings WHERE {where}"
    params: List[Any] = []
    if provider:
        sql += " AND COALESCE(source_provider, '?') = ?"
        params.append(provider)
    if active_only:
        # Skip rows already known to be off the market — enriching a sold
        # listing spends a call to learn nothing we would act on.
        sql += " AND COALESCE(LOWER(status), '') NOT IN ('sold','leased','off_market','withdrawn')"
    sql += " ORDER BY last_seen DESC"
    return [dict(r) for r in db.conn.execute(sql, params).fetchall()]


def rows_missing_address(db: PropertyDB, *, provider: str | None = None) -> List[Dict[str, Any]]:
    return incomplete_rows(db, scope="address", provider=provider)


def address_from_stored_url(url: str | None) -> Dict[str, Any] | None:
    """Recover an address from a canonical Domain SEO URL, or None.

    ``/project/`` URLs are development profiles, not addressable listings, and
    a bare ``/<listing-id>`` carries nothing — both return None so the caller
    falls through to the API rather than storing a mangled slug as an address.
    """
    if not url or "/project/" in url:
        return None
    parsed = domain_graphql.address_from_seo_url(url)
    return parsed if parsed.get("display") and parsed.get("suburb") else None


def backfill(db: PropertyDB, *, limit: int | None, dry_run: bool,
             provider: str | None, offline_only: bool,
             scope: str = "any", active_only: bool = False) -> Dict[str, int]:
    rows = incomplete_rows(db, scope=scope, provider=provider, active_only=active_only)
    if limit:
        rows = rows[:limit]

    def apply_url_fallback(lid: str, url: str | None) -> bool:
        parsed = address_from_stored_url(url)
        if not parsed:
            return False
        print(f"  url  {lid}  {parsed['display']}  (slug-parsed — may be approximate)")
        if not dry_run:
            db.conn.execute(
                "UPDATE listings SET address_display=?, street=COALESCE(street,?),"
                " suburb=COALESCE(suburb,?), state=COALESCE(state,?), postcode=COALESCE(postcode,?)"
                " WHERE id=?",
                (parsed["display"], parsed.get("street"), parsed.get("suburb"),
                 parsed.get("state"), parsed.get("postcode"), lid),
            )
            db.conn.commit()
        return True

    stats = {"considered": len(rows), "from_url": 0, "from_api": 0, "gone": 0, "failed": 0}
    for row in rows:
        lid, url, mode = str(row["id"]), row["url"], row["mode"] or "sale"

        if offline_only:
            stats["from_url"] += apply_url_fallback(lid, url)
            continue

        try:
            detail = domain_graphql.listing_detail(lid)
        except Exception as exc:
            stats["failed"] += 1
            print(f"  ERR  {lid}  {exc}", file=sys.stderr)
            continue

        if detail:
            stats["from_api"] += 1
            print(f"  api  {lid}  {detail['address']['display']}"
                  f"  [{len(detail.get('images') or [])} imgs, {len(detail.get('agents') or [])} agents]")
            if not dry_run:
                db.upsert_listing({**detail, "source_provider": "domain_graphql"}, mode=mode)
            continue

        # Delisted: Domain no longer serves the detail. The stored URL is all
        # that is left, so an approximate address beats a blank row in a comp set.
        if apply_url_fallback(lid, url):
            stats["from_url"] += 1
        else:
            stats["gone"] += 1
            print(f"  gone {lid}  (no detail, no parseable URL)")

    return stats


def normalize_stored_statuses(db: PropertyDB, *, dry_run: bool = False) -> Dict[str, int]:
    """Rewrite legacy status values into the normalized vocabulary, once.

    Rows written before ``domain_graphql.normalize_status`` existed hold Domain's
    raw enum (``LIVE``, ``SOLD``, ``ARCHIVED``, ``NEW_DEVELOPMENT``). Left alone,
    the next run compares a raw value against a normalized one and records a
    ``status_change`` event for every listing in the database — 490 of them on
    the first run after the cutover. This is idempotent: a second pass is a no-op.
    """
    changes: Dict[str, int] = {}
    rows = db.conn.execute(
        "SELECT DISTINCT status FROM listings WHERE status IS NOT NULL AND status <> ''"
    ).fetchall()
    for row in rows:
        raw = row["status"]
        mapped = domain_graphql.normalize_status(raw)
        if not mapped or mapped == raw:
            continue
        n = db.conn.execute("SELECT count(*) c FROM listings WHERE status = ?", (raw,)).fetchone()["c"]
        changes[f"{raw} -> {mapped}"] = n
        if not dry_run:
            db.conn.execute("UPDATE listings SET status = ? WHERE status = ?", (mapped, raw))
    if not dry_run and changes:
        db.conn.commit()
    return changes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="backfill_addresses.py")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--limit", type=int, help="Only process the N most recently seen")
    ap.add_argument("--scope", choices=sorted(SCOPES), default="any",
                    help="What counts as incomplete (default: any of address/agents/description)")
    ap.add_argument("--active-only", action="store_true",
                    help="Skip listings already known to be sold/leased/withdrawn")
    ap.add_argument("--provider", help="Restrict to one source_provider ('?' for legacy rows)")
    ap.add_argument("--offline-only", action="store_true",
                    help="Only repair rows whose stored URL already carries the address")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--normalize-status", action="store_true",
                    help="One-time: rewrite legacy raw Domain status values, then exit")
    args = ap.parse_args(argv)

    if args.normalize_status:
        with PropertyDB(Path(args.db)) as db:
            for label, n in sorted(normalize_stored_statuses(db, dry_run=args.dry_run).items()):
                print(f"  {label}: {n}")
        return 0

    with PropertyDB(Path(args.db)) as db:
        before = len(incomplete_rows(db, scope=args.scope, active_only=args.active_only))
        stats = backfill(db, limit=args.limit, dry_run=args.dry_run,
                         provider=args.provider, offline_only=args.offline_only,
                         scope=args.scope, active_only=args.active_only)
        after = len(incomplete_rows(db, scope=args.scope, active_only=args.active_only))

    print(f"\nincomplete ({args.scope}): {before} -> {after}")
    print("  ".join(f"{k}={v}" for k, v in stats.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
