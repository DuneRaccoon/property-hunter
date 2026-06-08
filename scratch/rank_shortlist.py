#!/usr/bin/env python3
"""Ad-hoc: rank active sale listings from the DB for the Saturday folio.

Pulls each non-stale 'sale' listing's stored raw_json, re-scores via the
decision engine, and prints the top N by viability. Read-only. Run with
PYTHONPATH=.. from the project root.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from db import DEFAULT_DB_PATH, PropertyDB
from buyer_profile import DEFAULT_BUYER, parse_buyer_md
from decision_engine import analyse_listing

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 8


def main() -> int:
    front, _ = parse_buyer_md(DEFAULT_BUYER)
    rows_out = []
    with PropertyDB(DEFAULT_DB_PATH) as db:
        rows = db.conn.execute(
            """
            SELECT id, raw_json, status, suburb, price_display, beds, baths, cars,
                   property_type, last_seen
            FROM listings
            WHERE mode='sale'
            """
        ).fetchall()
        for row in rows:
            status = (row["status"] or "").lower()
            if status in {"sold", "withdrawn"}:
                continue
            try:
                listing = json.loads(row["raw_json"])
            except Exception:
                continue
            ltype = (listing.get("listing_type") or "").lower()
            if ltype == "project":  # off-the-plan project shells, skip
                continue
            beds = listing.get("beds")
            if beds is not None and beds > 2:
                continue
            try:
                decision = analyse_listing(listing, front, db=db)
            except Exception as exc:
                continue
            via = decision.get("viability") or {}
            val = decision.get("valuation") or {}
            risks = decision.get("risks") or {}
            rows_out.append({
                "id": row["id"],
                "score": via.get("score") or 0,
                "band": via.get("band"),
                "suburb": row["suburb"],
                "price": row["price_display"],
                "beds": row["beds"], "baths": row["baths"], "cars": row["cars"],
                "fairness": val.get("fairness"),
                "confidence": val.get("confidence"),
                "comp_count": val.get("comparable_count"),
                "top_risk": (risks.get("top") or [{}])[0].get("label") if risks.get("top") else None,
                "url": listing.get("url"),
                "address": (listing.get("address") or {}).get("display") or (listing.get("address") or {}).get("street"),
            })
    rows_out.sort(key=lambda r: r["score"], reverse=True)
    print(f"# active sale candidates scored: {len(rows_out)}")
    for r in rows_out[:LIMIT]:
        print(json.dumps(r, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
