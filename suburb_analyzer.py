#!/usr/bin/env python3
"""Suburb analysis for the buyer's agent.

Reads everything Property Hunter has stored in SQLite and turns it into a
ranked view of suburbs: how active each one is, where the money sits, and
how well it fits the buyer's budget. The cron-driven buyer's agent uses this
to decide which suburbs are worth searching this run instead of forever
hammering the same hardcoded three.

Two jobs:
1. ``analyze_suburbs`` — per-suburb stats (count, median/avg/min/max, $/bed,
   spread) for a given mode (sale/rent/sold), enriched with an affordability
   fit against the buyer's budget and a composite "winning suburb" score.
2. ``candidate_suburbs`` — expand the suburbs we already know about (e.g. the
   buyer's seed suburbs + anything we've observed nearby by postcode) into a
   list the agent can feed back into fresh Domain searches.

The fuzzy half — turning a prose ``region_hint`` like "inner-south Sydney,
walkable to the CBD" into suburb names — is left to the agent's reasoning.
This module only does the deterministic, data-backed scoring.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

# Dollar amounts in Domain price guides: $950,000 / $1.2m / $1,045,000 etc.
_DOLLARS_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*([mMkK])?")


def parse_price_display(text: Optional[str]) -> Optional[float]:
    """Pull a representative number out of a price-guide string.

    Search cards only carry a display string ("$950,000 - $1,045,000",
    "FROM $910,000", "Contact agent"). We extract every dollar figure,
    normalise m/k suffixes, and return the midpoint of a range (or the
    single value). Returns None when there's no number to find.
    """
    if not text:
        return None
    values: List[float] = []
    for amount, suffix in _DOLLARS_RE.findall(text):
        try:
            num = float(amount.replace(",", ""))
        except ValueError:
            continue
        if suffix in ("m", "M"):
            num *= 1_000_000
        elif suffix in ("k", "K"):
            num *= 1_000
        # Ignore noise like "$0" or implausibly small guides.
        if num >= 1000:
            values.append(num)
    if not values:
        return None
    return sum(values) / len(values)

from db import DEFAULT_DB_PATH, PropertyDB

try:
    from buyer_profile import parse_buyer_md, DEFAULT_BUYER
except Exception:  # buyer_profile is optional for pure-DB use
    parse_buyer_md = None
    DEFAULT_BUYER = None


def _effective_price(row: Dict[str, Any]) -> Optional[float]:
    """Best single price for a listing row, regardless of mode.

    Sold listings carry sold_price; live listings carry a from/to range
    (often the same number, or a midpoint when both present).
    """
    if row.get("sold_price"):
        return float(row["sold_price"])
    lo, hi = row.get("price_from"), row.get("price_to")
    if lo and hi:
        return (float(lo) + float(hi)) / 2.0
    if lo:
        return float(lo)
    if hi:
        return float(hi)
    # Card-only data: no parsed range, fall back to the display string.
    return parse_price_display(row.get("price_display"))


def _median(values: List[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _affordability(median_price: Optional[float], budget_max: Optional[float]) -> Optional[float]:
    """0..1 fit of a suburb's median against the budget ceiling.

    1.0 = comfortably under budget (median <= 80% of ceiling).
    ~0.5 = median sits right at the ceiling.
    0.0 = median is well over budget (>= 130% of ceiling).
    """
    if not median_price or not budget_max:
        return None
    ratio = median_price / budget_max
    if ratio <= 0.8:
        return 1.0
    if ratio >= 1.3:
        return 0.0
    # Linear ramp between 0.8 (->1.0) and 1.3 (->0.0).
    return max(0.0, min(1.0, 1.0 - (ratio - 0.8) / 0.5))


def analyze_suburbs(
    db: PropertyDB,
    *,
    mode: str = "sold",
    budget_min: Optional[float] = None,
    budget_max: Optional[float] = None,
    min_count: int = 1,
) -> List[Dict[str, Any]]:
    """Return per-suburb stats for ``mode``, ranked by a composite score.

    Score blends affordability fit (does the median fit the budget?) with
    market activity (how many listings we've actually observed there). A
    suburb you can afford but never see stock in is useless; so is a busy
    suburb you can't afford. The product rewards both.
    """
    rows = db.conn.execute(
        """
        SELECT suburb, state, postcode, sold_price, price_from, price_to, price_display, beds
        FROM listings
        WHERE mode = ? AND suburb IS NOT NULL
        """,
        (mode,),
    ).fetchall()

    by_suburb: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        row = dict(r)
        price = _effective_price(row)
        if price is None:
            continue
        if budget_min and price < budget_min:
            continue
        key = (row["suburb"], row["state"], row["postcode"])
        bucket = by_suburb.setdefault(key, {"prices": [], "beds": []})
        bucket["prices"].append(price)
        if row.get("beds"):
            bucket["beds"].append(float(row["beds"]))

    results: List[Dict[str, Any]] = []
    max_count = max((len(b["prices"]) for b in by_suburb.values()), default=1)

    for (suburb, state, postcode), bucket in by_suburb.items():
        prices = bucket["prices"]
        if len(prices) < min_count:
            continue
        median_price = _median(prices)
        avg_beds = _median(bucket["beds"]) if bucket["beds"] else None
        per_bed = (median_price / avg_beds) if (median_price and avg_beds) else None
        afford = _affordability(median_price, budget_max)
        activity = len(prices) / max_count  # 0..1 relative to busiest suburb

        # Composite: if we have a budget, weight affordability and activity
        # together; otherwise rank purely on activity.
        if afford is not None:
            score = round(afford * (0.4 + 0.6 * activity), 4)
        else:
            score = round(activity, 4)

        results.append({
            "suburb": suburb,
            "state": state,
            "postcode": postcode,
            "count": len(prices),
            "median_price": round(median_price) if median_price else None,
            "min_price": round(min(prices)),
            "max_price": round(max(prices)),
            "avg_beds": round(avg_beds, 1) if avg_beds else None,
            "price_per_bed": round(per_bed) if per_bed else None,
            "affordability": round(afford, 3) if afford is not None else None,
            "activity": round(activity, 3),
            "score": score,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def candidate_suburbs(
    db: PropertyDB,
    *,
    seed_suburbs: Optional[List[str]] = None,
    seed_postcodes: Optional[List[str]] = None,
    mode: Optional[str] = None,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    """Suggest suburbs to search, drawn from what we've already observed.

    Starts from the buyer's seed suburbs/postcodes and surfaces every other
    suburb we've stored that shares one of those postcodes (cheap proxy for
    "nearby"). Returns suburbs with the listing counts so the agent can pick
    the liveliest ones to expand a search into.
    """
    seed_pcs = set(p for p in (seed_postcodes or []) if p)
    # Pull postcodes for any named seed suburbs too.
    if seed_suburbs:
        placeholders = ",".join("?" for _ in seed_suburbs)
        for r in db.conn.execute(
            f"SELECT DISTINCT postcode FROM listings WHERE suburb IN ({placeholders}) AND postcode IS NOT NULL",
            seed_suburbs,
        ).fetchall():
            seed_pcs.add(r["postcode"])

    where = ["suburb IS NOT NULL"]
    params: List[Any] = []
    if seed_pcs:
        where.append(f"postcode IN ({','.join('?' for _ in seed_pcs)})")
        params.extend(sorted(seed_pcs))
    if mode:
        where.append("mode = ?")
        params.append(mode)

    rows = db.conn.execute(
        f"""
        SELECT suburb, state, postcode, COUNT(*) AS n
        FROM listings
        WHERE {' AND '.join(where)}
        GROUP BY suburb, state, postcode
        ORDER BY n DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _budget_from_buyer(mode: str) -> Dict[str, Optional[float]]:
    """Pull the relevant budget band out of buyer.md for this mode."""
    if not parse_buyer_md or not DEFAULT_BUYER or not Path(DEFAULT_BUYER).exists():
        return {"min": None, "max": None}
    front, _ = parse_buyer_md(Path(DEFAULT_BUYER))
    budget = front.get("budget") or {}
    band = budget.get("rent") if mode == "rent" else budget.get("buy")
    band = band or {}
    return {"min": band.get("min"), "max": band.get("max")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="suburb_analyzer.py")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--mode", default="sold", choices=["sale", "rent", "sold"])
    ap.add_argument("--budget-min", type=float, help="Override buyer.md budget floor")
    ap.add_argument("--budget-max", type=float, help="Override buyer.md budget ceiling")
    ap.add_argument("--min-count", type=int, default=1, help="Min listings to include a suburb")
    ap.add_argument("--candidates", action="store_true", help="List candidate suburbs near seeds instead of stats")
    ap.add_argument("--seed-suburb", action="append", default=[], help="Seed suburb (repeatable)")
    ap.add_argument("--seed-postcode", action="append", default=[], help="Seed postcode (repeatable)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with PropertyDB(Path(args.db)) as db:
        if args.candidates:
            out = candidate_suburbs(
                db,
                seed_suburbs=args.seed_suburb or None,
                seed_postcodes=args.seed_postcode or None,
                mode=None if args.mode == "sold" else args.mode,
            )
            if args.json:
                print(json.dumps(out, indent=2))
            else:
                for r in out:
                    print(f"  {r['suburb']} {r['state']} {r['postcode']} — {r['n']} listings")
            return 0

        budget = _budget_from_buyer(args.mode)
        bmin = args.budget_min if args.budget_min is not None else budget["min"]
        bmax = args.budget_max if args.budget_max is not None else budget["max"]

        stats = analyze_suburbs(db, mode=args.mode, budget_min=bmin, budget_max=bmax, min_count=args.min_count)
        if args.json:
            print(json.dumps(stats, indent=2))
            return 0

        print(f"Suburb analysis — mode={args.mode}  budget={bmin or 0}-{bmax or 'any'}  ({len(stats)} suburbs)")
        print(f"{'suburb':<22} {'n':>4} {'median':>11} {'$/bed':>9} {'afford':>7} {'score':>6}")
        for r in stats:
            med = f"${r['median_price']:,}" if r["median_price"] else "-"
            ppb = f"${r['price_per_bed']:,}" if r["price_per_bed"] else "-"
            af = f"{r['affordability']:.2f}" if r["affordability"] is not None else "-"
            print(f"{r['suburb'][:22]:<22} {r['count']:>4} {med:>11} {ppb:>9} {af:>7} {r['score']:>6.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
