#!/usr/bin/env python3
"""Sales reporting over sold listings.

The buyer's agent tracks sold comparables (``track_sold`` in buyer.md) so it can
answer "what's actually selling, and for how much?" — the market context that
makes an asking price meaningful. This module turns the ``mode='sold'`` rows in
the DB into two reports:

1. ``recent_sales`` — the individual sold listings (address, price, date,
   beds/baths, sale method), newest first, optionally filtered to the buyer's
   suburbs / bed count / recency window.
2. ``sales_summary`` — per-suburb roll-up: number sold, median / range, $/bed,
   and the date span covered.

Exact sold price + date come from *enriched* sold listings (detail pages give
``soldDetails.soldPrice.rawValues.exactPrice`` and ``soldDate.isoDate``).
Un-enriched sold cards still carry a display price ("$970,000" / "Price
Withheld"), which we parse as a fallback so the report isn't empty before
enrichment runs.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from db import DEFAULT_DB_PATH, PropertyDB
from suburb_analyzer import parse_price_display

try:
    from buyer_profile import parse_buyer_md, DEFAULT_BUYER
except Exception:
    parse_buyer_md = None
    DEFAULT_BUYER = None


def _sold_price(row: Dict[str, Any]) -> Optional[float]:
    """Exact sold price if we have it, else parse the display string."""
    if row.get("sold_price"):
        return float(row["sold_price"])
    return parse_price_display(row.get("price_display"))


def _sold_date(row: Dict[str, Any]) -> Optional[datetime]:
    raw = row.get("sold_date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def recent_sales(
    db: PropertyDB,
    *,
    days: Optional[int] = 90,
    suburbs: Optional[List[str]] = None,
    beds_min: Optional[int] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Individual sold listings, newest first.

    ``days`` filters by sold_date when present (rows with no date are kept only
    when ``days`` is None, so we never silently drop un-dated card-only sales
    from an unfiltered report). ``suburbs`` matches the stored suburb name.
    """
    rows = [dict(r) for r in db.conn.execute(
        "SELECT id, suburb, state, postcode, address_display, sold_price, sold_date,"
        " sale_method, price_display, beds, baths, cars, property_type, url"
        " FROM listings WHERE mode='sold'"
    ).fetchall()]

    cutoff = None
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    sub_set = {s.lower() for s in suburbs} if suburbs else None
    out: List[Dict[str, Any]] = []
    for r in rows:
        if sub_set and (r.get("suburb") or "").lower() not in sub_set:
            continue
        if beds_min and (r.get("beds") or 0) < beds_min:
            continue
        dt = _sold_date(r)
        if cutoff is not None:
            if dt is None or dt.replace(tzinfo=dt.tzinfo or timezone.utc) < cutoff:
                continue
        out.append({
            "id": r["id"],
            "suburb": r["suburb"],
            "address": r.get("address_display"),
            "sold_price": _sold_price(r),
            "sold_date": (dt.date().isoformat() if dt else None),
            "sale_method": r.get("sale_method"),
            "beds": r.get("beds"),
            "baths": r.get("baths"),
            "cars": r.get("cars"),
            "property_type": r.get("property_type"),
            "url": r.get("url"),
        })

    out.sort(key=lambda x: (x["sold_date"] or "", x["sold_price"] or 0), reverse=True)
    return out[:limit]


def sales_summary(
    db: PropertyDB,
    *,
    days: Optional[int] = 180,
    suburbs: Optional[List[str]] = None,
    beds_min: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Per-suburb roll-up of sold listings: count, median, range, $/bed."""
    sales = recent_sales(db, days=days, suburbs=suburbs, beds_min=beds_min, limit=100000)
    by_suburb: Dict[str, Dict[str, Any]] = {}
    for s in sales:
        if s["sold_price"] is None:
            continue
        b = by_suburb.setdefault(s["suburb"], {"prices": [], "beds": [], "dates": []})
        b["prices"].append(s["sold_price"])
        if s["beds"]:
            b["beds"].append(float(s["beds"]))
        if s["sold_date"]:
            b["dates"].append(s["sold_date"])

    summary: List[Dict[str, Any]] = []
    for suburb, b in by_suburb.items():
        prices = b["prices"]
        med = statistics.median(prices)
        avg_beds = statistics.median(b["beds"]) if b["beds"] else None
        summary.append({
            "suburb": suburb,
            "sold_count": len(prices),
            "median_price": round(med),
            "min_price": round(min(prices)),
            "max_price": round(max(prices)),
            "price_per_bed": round(med / avg_beds) if avg_beds else None,
            "first_sold": min(b["dates"]) if b["dates"] else None,
            "last_sold": max(b["dates"]) if b["dates"] else None,
        })
    summary.sort(key=lambda x: x["sold_count"], reverse=True)
    return summary


def _buyer_context() -> Dict[str, Any]:
    """Pull suburbs + beds_min from buyer.md to scope the report by default."""
    if not parse_buyer_md or not DEFAULT_BUYER or not Path(DEFAULT_BUYER).exists():
        return {}
    front, _ = parse_buyer_md(Path(DEFAULT_BUYER))
    locs = (front.get("locations") or {}).get("suburbs") or []
    # Stored suburb is the bare name ("Zetland"); buyer.md lists "Zetland NSW 2017".
    suburbs = [str(s).split(" NSW")[0].split(" VIC")[0].split(",")[0].strip() for s in locs]
    return {"suburbs": [s for s in suburbs if s] or None, "beds_min": front.get("beds_min")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sales_report.py")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--days", type=int, default=90, help="Recency window (0 = no date filter)")
    ap.add_argument("--suburb", action="append", default=[], help="Limit to suburb (repeatable)")
    ap.add_argument("--beds-min", type=int)
    ap.add_argument("--all-suburbs", action="store_true", help="Ignore buyer.md suburb scope")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ctx = {} if args.all_suburbs else _buyer_context()
    suburbs = args.suburb or ctx.get("suburbs")
    beds_min = args.beds_min if args.beds_min is not None else ctx.get("beds_min")
    days = None if args.days == 0 else args.days

    with PropertyDB(Path(args.db)) as db:
        summary = sales_summary(db, days=days, suburbs=suburbs, beds_min=beds_min)
        sales = recent_sales(db, days=days, suburbs=suburbs, beds_min=beds_min, limit=args.limit)

        if args.json:
            print(json.dumps({"summary": summary, "recent_sales": sales}, indent=2))
            return 0

        scope = ", ".join(suburbs) if suburbs else "all suburbs"
        window = f"last {days} days" if days else "all time"
        print(f"Sales Report — {scope} — {window}")
        print(f"\n## By suburb ({len(summary)})")
        for s in summary:
            ppb = f"${s['price_per_bed']:,}/bd" if s["price_per_bed"] else "-"
            print(f"  {s['suburb']:<18} {s['sold_count']:>3} sold | median ${s['median_price']:,} "
                  f"| ${s['min_price']:,}–${s['max_price']:,} | {ppb}")
        print(f"\n## Recent sales ({len(sales)})")
        for s in sales:
            price = f"${s['sold_price']:,.0f}" if s["sold_price"] else "Withheld"
            date = s["sold_date"] or "?"
            print(f"  {date}  {price:>12}  {s['beds'] or '?'}bd/{s['baths'] or '?'}ba  "
                  f"{s['address'] or s['suburb']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
