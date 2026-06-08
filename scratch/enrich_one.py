#!/usr/bin/env python3
"""Ad-hoc: enrich a single listing by id (fetch detail page via CDP), persist
to the DB, and dump the enriched normalized listing JSON to scratch/enriched/.

One fetch per process so the CDP browser only needs one clean connect. Run with
PYTHONPATH=.. from the project root, after restarting the OpenClaw browser.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from db import DEFAULT_DB_PATH, PropertyDB
from source_providers import DomainListingProvider

OUT = Path(__file__).resolve().parent / "enriched"
OUT.mkdir(exist_ok=True)


def main() -> int:
    listing_id = sys.argv[1]
    provider = DomainListingProvider()
    detail = provider.listing(str(listing_id), headed=True)
    if not detail:
        print(f"NO DETAIL for {listing_id}", file=sys.stderr)
        return 1
    with PropertyDB(DEFAULT_DB_PATH) as db:
        db.upsert_listing(detail, mode="sale")
        media = db.listing_media(str(listing_id))
    (OUT / f"{listing_id}.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    desc = (detail.get("description") or "")
    print(f"OK {listing_id}: desc={len(desc)}chars agents={len(detail.get('agents') or [])} "
          f"images={len(detail.get('images') or [])} media_groups={ {k:len(v) for k,v in (media or {}).items()} }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
