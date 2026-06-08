#!/usr/bin/env python3
"""Red-flag detection for buyer/renter shortlists.

This module is deliberately deterministic. It scans the listing data we already
have and returns structured risk items the digest, viability scorer, and folio
can consume without asking an LLM to invent due diligence.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SEVERITY_WEIGHT = {
    "dealbreaker": 1.0,
    "major": 0.65,
    "minor": 0.3,
    "watch": 0.15,
}

# Sydney suburbs with well-known apartment oversupply / high-density pipelines.
# Used as a soft "watch" signal on capital-growth risk, not a hard reject.
HIGH_DENSITY_SUBURBS = {
    "zetland", "waterloo", "mascot", "wolli creek", "rhodes", "sydney olympic park",
    "homebush", "epping", "macquarie park", "parramatta", "north ryde", "meadowbank",
    "arncliffe", "rosebery", "green square",
}


@dataclass(frozen=True)
class RiskItem:
    severity: str
    category: str
    label: str
    reason: str
    evidence: str = ""
    known: bool = True


def text_blob(listing: Dict[str, Any]) -> str:
    parts: List[str] = [
        str(listing.get("headline") or ""),
        str(listing.get("description") or ""),
        str(listing.get("price") or ""),
        str(listing.get("status") or ""),
    ]
    for key in ("features", "structured_features", "property_types"):
        for item in listing.get(key) or []:
            if isinstance(item, dict):
                parts.append(" ".join(str(v) for v in item.values() if v))
            else:
                parts.append(str(item))
    return " ".join(parts).lower()


def property_type(listing: Dict[str, Any]) -> str:
    ptype = listing.get("property_type") or listing.get("property_types") or ""
    if isinstance(ptype, list):
        ptype = " ".join(str(p) for p in ptype)
    return str(ptype).lower()


def _suburb(listing: Dict[str, Any]) -> str:
    addr = listing.get("address") or {}
    suburb = addr.get("suburb") if isinstance(addr, dict) else listing.get("suburb")
    return str(suburb or "").strip().lower()


def _has_media(listing: Dict[str, Any], kinds: Iterable[str]) -> bool:
    wanted = {k.lower() for k in kinds}
    for img in listing.get("images") or []:
        if isinstance(img, dict):
            mt = str(img.get("type") or img.get("category") or "").lower()
            if mt in wanted:
                return True
    return False


def _match(blob: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, blob, re.I)
    return m.group(0) if m else None


def detect_risks(listing: Dict[str, Any], front: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    front = front or {}
    objective = str(front.get("objective") or listing.get("mode") or "buy").lower()
    ptype = property_type(listing)
    blob = text_blob(listing)
    risks: List[RiskItem] = []

    def add(severity: str, category: str, label: str, reason: str, evidence: str = "", known: bool = True) -> None:
        risks.append(RiskItem(severity, category, label, reason, evidence, known))

    cars_min = front.get("cars_min")
    cars = listing.get("cars")
    if cars_min and (cars is None or float(cars or 0) < float(cars_min)):
        add("dealbreaker", "brief", "Parking shortfall", f"Brief requires {cars_min}+ car space.", str(cars))

    if ev := _match(blob, r"\b(flood|bushfire|fire|contamination|heritage)\s+(zone|overlay|prone|affected)\b"):
        add("dealbreaker", "overlay", "Potential property overlay", "Listing text references a serious overlay that needs external confirmation.", ev)

    if ev := _match(blob, r"\b(main road|busy road|arterial|highway frontage|traffic noise)\b"):
        add("major", "location", "Noise or road exposure", "Road exposure can hurt liveability and resale depth.", ev)

    if ev := _match(blob, r"\b(ground floor|street level)\b"):
        add("minor", "apartment", "Ground-floor exposure", "Ground floor can trade at a discount unless privacy/light are excellent.", ev)

    if ev := _match(blob, r"\b(dark|internal outlook|limited natural light|no outlook|south[- ]?facing)\b"):
        add("major", "apartment", "Light/aspect concern", "Aspect language suggests this needs checking in person.", ev)

    is_apartment = "apartment" in ptype or "unit" in ptype or "studio" in ptype
    if is_apartment:
        if ev := _match(blob, r"\b(no lift|without (a )?lift|walk[- ]?up|stair access only|no elevator)\b"):
            add("minor", "apartment", "No-lift building", "A walk-up matters above the first floor and narrows the resale pool.", ev)
        if ev := _match(blob, r"\b(studio|compact|cosy|cozy|bedsit|low[- ]?maintenance footprint)\b"):
            add("watch", "apartment", "Compact floorplan", "Studio/compact wording can mean a small internal area; confirm sqm on the floorplan.", ev)
        elif not _match(blob, r"\b\d{2,3}\s?(sq ?m|sqm|m2|square met)"):
            add("watch", "missing_data", "Internal area unclear", "No internal area (sqm) stated; true usable size is hard to judge.", known=False)
        if _suburb(listing) in HIGH_DENSITY_SUBURBS:
            add("watch", "market", "High-density suburb", "Apartment-heavy suburb with ongoing supply; capital growth can lag and resale competes with stock.")

    if ev := _match(blob, r"\b(needs work|renovat(or'?s|e)|original condition|tlc|blank canvas|structural|water damage|defect)\b"):
        add("major", "condition", "Condition risk", "Renovation or defect wording can mean extra capital after purchase.", ev)

    if "house" in ptype or "townhouse" in ptype or "terrace" in ptype or "villa" in ptype:
        if ev := _match(blob, r"\b(easement|right of way|right-of-way|shared driveway|battle[- ]?axe|landlocked|no street frontage|access handle)\b"):
            add("major", "title", "Easement/access issue", "Easement or shared-access wording affects usable land, privacy, and resale; confirm on title.", ev)

    if ("apartment" in ptype or "unit" in ptype) and not _has_media(listing, ("floorplan",)):
        add("watch", "missing_data", "No floorplan found", "Layout quality and true usability are harder to judge without a floorplan.", known=False)

    if ("apartment" in ptype or "unit" in ptype) and "strata" not in blob:
        add("watch", "missing_data", "Strata costs unknown", "Quarterly levies and capital works fund need checking before confidence is high.", known=False)

    if not (listing.get("inspections") or listing.get("inspection")):
        add("watch", "missing_data", "No inspection time found", "No visible open time makes urgency and access unclear.", known=False)

    agents = listing.get("agents") or []
    has_contact = any(isinstance(a, dict) and (a.get("mobile") or a.get("email") or a.get("landline")) for a in agents)
    if agents and not has_contact:
        add("watch", "missing_data", "Agent contact incomplete", "Need direct contact details before action is easy.", known=False)
    elif not agents:
        add("watch", "missing_data", "Agent details missing", "Listing was probably not enriched yet.", known=False)

    if objective in ("rent", "both") or listing.get("mode") == "rent":
        if not _match(blob, r"\b(air ?conditioning|air-conditioning|aircon|a/c|heating|heater|reverse cycle)\b"):
            add("watch", "renter", "Heating/cooling unconfirmed", "Comfort systems are not confirmed in the rental listing.", known=False)
        if ev := _match(blob, r"\b(no pets|pets not permitted|not pet friendly)\b"):
            add("major", "renter", "Pet restriction", "Pet rules can kill suitability for renters with animals.", ev)
        if ev := _match(blob, r"\b(shared laundry|communal laundry)\b"):
            add("minor", "renter", "Shared laundry", "A liveability compromise many renters discount.", ev)
        if ev := _match(blob, r"\b(short lease|6 month lease|temporary)\b"):
            add("major", "renter", "Short lease risk", "Short lease terms reduce stability.", ev)
        cars = listing.get("cars")
        if ev := _match(blob, r"\b(no parking|street parking only|permit parking|unallocated parking|no storage|no cage)\b"):
            add("minor", "renter", "Parking/storage shortfall", "Parking or storage looks limited; confirm what is actually included.", ev)
        elif cars is not None and float(cars or 0) == 0 and not front.get("cars_min"):
            add("watch", "renter", "No allocated parking", "Listing shows no car space; check street/permit parking realities.", known=False)
        if not _match(blob, r"\b(available|vacant|move[- ]?in|lease start)\b"):
            add("watch", "renter", "Availability unclear", "The available date is not obvious from the current payload.", known=False)

    top = sorted(risks, key=lambda r: SEVERITY_WEIGHT.get(r.severity, 0), reverse=True)
    penalty = min(1.0, sum(SEVERITY_WEIGHT.get(r.severity, 0.1) for r in risks) / 4.0)
    return {
        "items": [asdict(r) for r in top],
        "top": [asdict(r) for r in top[:3]],
        "penalty": round(penalty, 2),
        "has_dealbreaker": any(r.severity == "dealbreaker" for r in risks),
        "summary": top[0].label if top else "No obvious red flags from available data",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="risk.py")
    ap.add_argument("--listing-json", required=True)
    args = ap.parse_args(argv)
    raw = json.loads(Path(args.listing_json).read_text(encoding="utf-8"))
    print(json.dumps(detect_risks(raw.get("listing", raw)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
