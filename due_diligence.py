#!/usr/bin/env python3
"""Due-diligence checklist generation for buyer and renter reports."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class DueDiligenceItem:
    category: str
    status: str
    severity: str
    reason: str
    recommended_action: str
    source: str


def _price_point(listing: Dict[str, Any]) -> Optional[float]:
    if listing.get("price_exact"):
        return float(listing["price_exact"])
    lo, hi = listing.get("price_from"), listing.get("price_to")
    if lo and hi:
        return (float(lo) + float(hi)) / 2.0
    return float(lo or hi) if (lo or hi) else None


def _ptype(listing: Dict[str, Any]) -> str:
    ptype = listing.get("property_type") or listing.get("property_types") or ""
    if isinstance(ptype, list):
        ptype = " ".join(str(p) for p in ptype)
    return str(ptype).lower()


def _has_floorplan(listing: Dict[str, Any]) -> bool:
    return any(isinstance(i, dict) and str(i.get("type") or "").lower() == "floorplan" for i in listing.get("images") or [])


def _blob(listing: Dict[str, Any]) -> str:
    parts = [str(listing.get("description") or ""), str(listing.get("headline") or "")]
    for item in listing.get("features") or []:
        if isinstance(item, dict):
            parts.extend(str(v) for v in item.values() if v)
        else:
            parts.append(str(item))
    return " ".join(parts).lower()


def estimate_stamp_duty_nsw(price: Optional[float]) -> Optional[float]:
    """Approximate NSW transfer duty for residential purchases.

    This is good enough for cash-requirement planning, not a legal/tax quote.
    """
    if not price or price <= 0:
        return None
    bands = [
        (0, 16000, 0, 0.0125),
        (16000, 35000, 200, 0.015),
        (35000, 93000, 485, 0.0175),
        (93000, 351000, 1500, 0.035),
        (351000, 1168000, 10530, 0.045),
        (1168000, 0, 47295, 0.055),
    ]
    for low, high, base, rate in bands:
        if high == 0 or price <= high:
            return round(base + (price - low) * rate)
    return None


def build_due_diligence(listing: Dict[str, Any], front: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    front = front or {}
    objective = str(front.get("objective") or listing.get("mode") or "buy").lower()
    ptype = _ptype(listing)
    price = _price_point(listing)
    budget = front.get("budget") or {}
    items: List[DueDiligenceItem] = []

    def add(category: str, status: str, severity: str, reason: str, action: str, source: str = "listing") -> None:
        items.append(DueDiligenceItem(category, status, severity, reason, action, source))

    if objective in ("buy", "both", "sale") and listing.get("mode") != "rent":
        add("legal", "required", "critical", "Contract terms are not available from the listing data.", "Request contract and send to conveyancer before bidding or signing.")
        if any(x in ptype for x in ("apartment", "unit", "studio")):
            add("strata", "required", "critical", "Apartment purchase requires strata health, levies, defects and capital works review.", "Request strata report, AGM minutes, capital works fund balance and levies.")
        elif any(x in ptype for x in ("house", "townhouse", "villa", "duplex")):
            add("building_pest", "required", "critical", "Non-apartment stock carries direct structure/site risk.", "Book building and pest inspection before exchange.")

        status = str(listing.get("status") or "").lower()
        price_text = str(listing.get("price") or "").lower()
        if "auction" in status or "auction" in price_text or listing.get("auction"):
            add("auction", "required", "critical", "Auction removes cooling-off protection and compresses due diligence.", "Finish contract, strata/building and finance checks before auction day.")
        else:
            add("cooling_off", "check", "major", "Private treaty usually has cooling-off, but waivers can remove it.", "Confirm cooling-off and section 66W position with conveyancer.")

        buy_max = (budget.get("buy") or {}).get("max")
        if price and buy_max:
            deposit = round(price * 0.10)
            duty = estimate_stamp_duty_nsw(price)
            total = deposit + (duty or 0)
            sev = "major" if price > buy_max else "watch"
            add("finance", "estimate", sev, f"Guide midpoint is ${price:,.0f}; 10% deposit plus approximate NSW duty is ~${total:,.0f}.", "Confirm pre-approval, deposit liquidity and stamp-duty treatment.", "calculated")
        else:
            add("finance", "unknown", "major", "No clear price point for cash-requirement estimate.", "Ask agent for guide and recent comparable evidence.")

    if objective in ("rent", "both") or listing.get("mode") == "rent":
        blob = _blob(listing)
        rent = price
        if rent:
            add("upfront_cash", "estimate", "major", f"Expected upfront cash is roughly 4 weeks bond + 2 weeks rent: ${rent * 6:,.0f}.", "Confirm bond, first payment and lease start date.", "calculated")
        else:
            add("upfront_cash", "unknown", "major", "No clear weekly rent found.", "Confirm rent and upfront payment terms.")
        add("lease", "check", "major", "Lease length and break terms are not available from the listing data.", "Ask for lease term, start date, break fee and renewal expectations.")
        if "pet" not in blob:
            add("pets", "unknown", "major", "Pet suitability is not confirmed in the listing data.", "Ask the property manager whether pets are permitted and whether approval is required.")
        if not any(token in blob for token in ("air conditioning", "air-conditioning", "aircon", "a/c", "heating", "heater")):
            add("climate", "unknown", "major", "Heating/cooling is not confirmed.", "Check aircon/heating at inspection and ask what systems are installed.")
        cars_min = front.get("cars_min")
        cars = listing.get("cars")
        if cars_min and (cars is None or float(cars or 0) < float(cars_min)):
            add("parking_storage", "unknown", "major", "Parking/storage does not clearly meet the renter brief.", "Ask whether parking, storage cage or permit parking is included.")
        add("application", "required", "major", "Good rentals move quickly.", "Prepare ID, payslips, references and pet profile before inspection.")

    if not _has_floorplan(listing):
        add("layout", "unknown", "watch", "Floorplan missing from available data.", "Request floorplan or measure usability at inspection.")
    if not (listing.get("inspections") or listing.get("inspection")):
        add("inspection", "unknown", "major", "No visible inspection time.", "Contact agent for next open/private inspection.")
    if not listing.get("price"):
        add("price", "unknown", "major", "Listing does not expose a clear price/rent guide.", "Ask agent for guide and comparable basis.")
    if not listing.get("agents"):
        add("agent", "unknown", "watch", "Agent details missing from current payload.", "Run detail-page enrichment or open Domain listing.")

    severity_order = {"critical": 0, "major": 1, "watch": 2, "minor": 3}
    items.sort(key=lambda x: severity_order.get(x.severity, 9))
    return {
        "items": [asdict(i) for i in items],
        "critical_count": sum(1 for i in items if i.severity == "critical"),
        "unknown_count": sum(1 for i in items if i.status == "unknown"),
        "summary": f"{sum(1 for i in items if i.severity == 'critical')} critical checks, {sum(1 for i in items if i.status == 'unknown')} unknowns",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="due_diligence.py")
    ap.add_argument("--listing-json", required=True)
    args = ap.parse_args(argv)
    raw = json.loads(Path(args.listing_json).read_text(encoding="utf-8"))
    print(json.dumps(build_due_diligence(raw.get("listing", raw)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
