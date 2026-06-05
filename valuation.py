#!/usr/bin/env python3
"""Comparable-led sale/rent fairness reads."""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from db import DEFAULT_DB_PATH, PropertyDB

try:
    from suburb_analyzer import parse_price_display
except Exception:  # pragma: no cover
    parse_price_display = lambda value: None  # noqa: E731


def price_point(listing: Dict[str, Any]) -> Optional[float]:
    if listing.get("price_exact"):
        return float(listing["price_exact"])
    lo, hi = listing.get("price_from"), listing.get("price_to")
    if lo and hi:
        return (float(lo) + float(hi)) / 2.0
    return float(lo or hi) if (lo or hi) else None


def _addr_suburb(listing: Dict[str, Any]) -> Optional[str]:
    addr = listing.get("address") or {}
    return addr.get("suburb") if isinstance(addr, dict) else listing.get("suburb")


def _ptype(listing: Dict[str, Any]) -> str:
    ptype = listing.get("property_type") or listing.get("property_types") or ""
    if isinstance(ptype, list):
        ptype = " ".join(str(p) for p in ptype)
    return str(ptype).lower()


NEARBY_SUBURBS: Dict[str, Tuple[str, ...]] = {
    "zetland": ("waterloo", "rosebery", "alexandria", "kensington"),
    "waterloo": ("zetland", "rosebery", "alexandria", "surry hills"),
    "rosebery": ("zetland", "waterloo", "alexandria", "kensington"),
    "randwick": ("kensington", "coogee", "clovelly", "waverley"),
    "bondi junction": ("waverley", "queens park", "bondi", "woollahra"),
    "crows nest": ("st leonards", "wollstonecraft", "north sydney", "cammeray"),
    "north sydney": ("crows nest", "st leonards", "wollstonecraft", "milsons point"),
    "marrickville": ("dulwich hill", "st peters", "newtown", "tempe"),
}


def _suburb_candidates(suburb: Optional[str], *, include_nearby: bool = True) -> List[str]:
    if not suburb:
        return []
    primary = str(suburb).strip()
    if not include_nearby:
        return [primary]
    nearby = NEARBY_SUBURBS.get(primary.lower(), ())
    return [primary, *nearby]


def _bed_delta(target: Any, candidate: Any) -> Optional[float]:
    try:
        if target is None or candidate is None:
            return None
        return abs(float(candidate) - float(target))
    except (TypeError, ValueError):
        return None


def _price_per_bed(price: Optional[float], beds: Any) -> Optional[float]:
    try:
        b = float(beds)
    except (TypeError, ValueError):
        return None
    if not price or b <= 0:
        return None
    return round(float(price) / b)


def _suburb_sql(suburbs: Iterable[str]) -> Tuple[str, List[Any]]:
    suburbs = [s for s in suburbs if s]
    if not suburbs:
        return "", []
    placeholders = ",".join("lower(?)" for _ in suburbs)
    return f" AND lower(suburb) IN ({placeholders})", suburbs


def _sold_comps(db: PropertyDB, listing: Dict[str, Any], limit: int = 12) -> List[Dict[str, Any]]:
    suburb = _addr_suburb(listing)
    beds = listing.get("beds")
    ptype = _ptype(listing)
    suburb_filter, suburb_params = _suburb_sql(_suburb_candidates(suburb))
    rows = db.conn.execute(
        f"""
        SELECT id, address_display, suburb, price_display, price_from, price_to,
               sold_price, sold_date, beds, baths, cars, property_type, url
        FROM listings
        WHERE mode='sold'
          {suburb_filter}
        ORDER BY COALESCE(sold_date, last_seen) DESC
        LIMIT 80
        """,
        suburb_params,
    ).fetchall()
    comps: List[Dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        sold_price = (
            r.get("sold_price")
            or r.get("price_from")
            or r.get("price_to")
            or parse_price_display(r.get("price_display"))
        )
        if not sold_price:
            continue
        r["sold_price"] = float(sold_price)
        score = 0
        row_ptype = str(r.get("property_type") or "").lower()
        if ptype and row_ptype and ptype not in row_ptype:
            continue
        bed_delta = _bed_delta(beds, r.get("beds"))
        if bed_delta is not None and bed_delta <= 0.5:
            score += 3
        elif bed_delta is not None and bed_delta <= 1:
            score += 1
        if ptype and r.get("property_type") and ptype in str(r["property_type"]).lower():
            score += 2
        if suburb and r.get("suburb") and suburb.lower() == str(r["suburb"]).lower():
            score += 3
        elif suburb and r.get("suburb") and str(r["suburb"]).lower() in NEARBY_SUBURBS.get(suburb.lower(), ()):
            score += 1
        if score >= 3:
            r["comp_score"] = score
            r["price_per_bed"] = _price_per_bed(r["sold_price"], r.get("beds"))
            comps.append(r)
    comps.sort(key=lambda x: (x["comp_score"], x.get("sold_date") or ""), reverse=True)
    return comps[:limit]


def _rental_comps(db: PropertyDB, listing: Dict[str, Any], limit: int = 12) -> List[Dict[str, Any]]:
    suburb = _addr_suburb(listing)
    beds = listing.get("beds")
    ptype = _ptype(listing)
    suburb_filter, suburb_params = _suburb_sql(_suburb_candidates(suburb))
    rows = db.conn.execute(
        f"""
        SELECT id, address_display, suburb, price_display, price_from, price_to,
               beds, baths, cars, property_type, url, last_seen
        FROM listings
        WHERE mode='rent'
          {suburb_filter}
        ORDER BY last_seen DESC
        LIMIT 100
        """,
        suburb_params,
    ).fetchall()
    comps: List[Dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        rent = r.get("price_from") or r.get("price_to") or parse_price_display(r.get("price_display"))
        if not rent or float(rent) > 5000:
            continue
        r["weekly_rent"] = float(rent)
        score = 0
        row_ptype = str(r.get("property_type") or "").lower()
        if ptype and row_ptype and ptype not in row_ptype:
            continue
        bed_delta = _bed_delta(beds, r.get("beds"))
        if bed_delta is not None and bed_delta <= 0.5:
            score += 3
        elif bed_delta is not None and bed_delta <= 1:
            score += 1
        if ptype and r.get("property_type") and ptype in str(r["property_type"]).lower():
            score += 2
        if suburb and r.get("suburb") and suburb.lower() == str(r["suburb"]).lower():
            score += 3
        elif suburb and r.get("suburb") and str(r["suburb"]).lower() in NEARBY_SUBURBS.get(suburb.lower(), ()):
            score += 1
        if score >= 3:
            r["comp_score"] = score
            r["rent_per_bed"] = _price_per_bed(r["weekly_rent"], r.get("beds"))
            comps.append(r)
    comps.sort(key=lambda x: (x["comp_score"], x.get("last_seen") or ""), reverse=True)
    return comps[:limit]


def _fairness(ask: Optional[float], median: Optional[float], values: List[float]) -> str:
    if not ask or not median or not values:
        return "unknown"
    low, high = min(values), max(values)
    if ask < low * 0.95:
        return "cheap or underquoted"
    if ask <= median * 0.97:
        return "good value"
    if ask <= median * 1.05:
        return "fair"
    if ask <= high * 1.05:
        return "stretched"
    return "overpriced"


def _posture(label: str) -> str:
    return {
        "cheap or underquoted": "Treat the guide sceptically; verify with the agent and expect competition.",
        "good value": "Inspect quickly and be ready to move if due diligence clears.",
        "fair": "Proceed, but anchor any offer to the strongest comparable sales.",
        "stretched": "Only chase if the property is materially superior to the comps.",
        "overpriced": "Pass or wait for a price correction unless there is a unique reason.",
        "unknown": "Pull more comparable evidence before making a price call.",
    }[label]


def _rental_posture(label: str) -> str:
    return {
        "cheap or underquoted": "Apply fast, but confirm the rent is not missing fees or a short lease catch.",
        "good value": "Inspect and have the application ready; this is attractive rent for the evidence.",
        "fair": "Proceed if the inspection clears liveability and application terms.",
        "stretched": "Only apply if the property is materially better than the rental comps.",
        "overpriced": "Pass unless there is a lifestyle reason that outweighs the rent premium.",
        "unknown": "Pull more rental evidence before treating the rent as fair.",
    }[label]


def _budget_max(front: Optional[Dict[str, Any]], objective: str) -> Optional[float]:
    budget = (front or {}).get("budget") or {}
    key = "rent" if objective == "rent" else "buy"
    raw = budget.get(key) if isinstance(budget, dict) else None
    if isinstance(raw, dict):
        raw = raw.get("max")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _median_or_none(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [float(v) for v in values if v]
    return statistics.median(clean) if clean else None


def value_listing(
    listing: Dict[str, Any],
    db: Optional[PropertyDB] = None,
    *,
    front: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    close_db = False
    if db is None:
        db = PropertyDB(Path(DEFAULT_DB_PATH))
        close_db = True
    try:
        objective = "rent" if listing.get("mode") == "rent" or (front or {}).get("objective") in ("rent", "renter") else "buy"
        ask = price_point(listing)
        if objective == "rent":
            comps = _rental_comps(db, listing)
            values = [float(c["weekly_rent"]) for c in comps if c.get("weekly_rent")]
            median = statistics.median(values) if values else None
            label = _fairness(ask, median, values)
            confidence = "high" if len(values) >= 8 else ("medium" if len(values) >= 4 else ("low" if values else "none"))
            budget_max = _budget_max(front, "rent")
            annual = ask * 52 if ask else None
            return {
                "mode": "rent",
                "asking_weekly": ask,
                "comparable_count": len(values),
                "comparable_range": [min(values), max(values)] if values else None,
                "median_comparable": median,
                "fairness": label,
                "confidence": confidence,
                "negotiation_posture": _rental_posture(label),
                "rent_per_bed": _price_per_bed(ask, listing.get("beds")),
                "median_rent_per_bed": _median_or_none(c.get("rent_per_bed") for c in comps),
                "weekly_affordability": "within budget" if ask and budget_max and ask <= budget_max else ("over budget" if ask and budget_max else "unknown"),
                "annual_rent_burden": annual,
                "application_urgency": "high" if label in ("cheap or underquoted", "good value") and confidence in ("medium", "high") else ("medium" if label == "fair" else "low"),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "comps": comps[:6],
            }
        comps = _sold_comps(db, listing)
        values = [float(c["sold_price"]) for c in comps if c.get("sold_price")]
        median = statistics.median(values) if values else None
        label = _fairness(ask, median, values)
        confidence = "high" if len(values) >= 8 else ("medium" if len(values) >= 4 else ("low" if values else "none"))
        return {
            "mode": "buy",
            "asking_midpoint": ask,
            "comparable_count": len(values),
            "comparable_range": [min(values), max(values)] if values else None,
            "median_comparable": median,
            "price_per_bed": _price_per_bed(ask, listing.get("beds")),
            "median_price_per_bed": _median_or_none(c.get("price_per_bed") for c in comps),
            "fairness": label,
            "confidence": confidence,
            "negotiation_posture": _posture(label),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "comps": comps[:6],
        }
    finally:
        if close_db:
            db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="valuation.py")
    ap.add_argument("--listing-json", required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = ap.parse_args(argv)
    raw = json.loads(Path(args.listing_json).read_text(encoding="utf-8"))
    with PropertyDB(Path(args.db)) as db:
        print(json.dumps(value_listing(raw.get("listing", raw), db), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
