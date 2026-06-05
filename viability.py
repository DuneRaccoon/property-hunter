#!/usr/bin/env python3
"""Viability scoring — turn a listing's data into a purchase-worthiness read.

This is the analytical core the buyer's agent leans on. The scraper gives us a
rich, normalized listing (price, beds/baths/cars, type, features, the agent's
description, geo); this module *consumes* all of that and scores it against the
buyer's brief (`buyer.md`) to answer: is this actually worth Ben's time, and how
strongly does it fit?

Crucially, the read is **lensed by `buyer_type`**:

- **owner_occupier** — weights lifestyle: light/aspect, outdoor space, low
  maintenance, building amenity, walkability, finishes. Price matters as "fits
  the budget", not as a yield input.
- **investor** — weights the numbers: gross rental yield (from live rent comps
  in the DB), low strata, rentability/depreciation signals, transport. Lifestyle
  niceties are downweighted.

Division of labour stays consistent with the rest of the project:
- **Deterministic (here):** extract signals from the listing text/fields, match
  against hard criteria + soft prefs + deal-breakers, compute yield from comps,
  produce a 0–10 score with a component breakdown and a list of plain-language
  signals.
- **Judgement (the agent/LLM):** read this structured result and write the
  *prose* (verdict, why-it-fits) for the report. The score scaffolds the
  narrative; it doesn't replace it.

The result dict is shaped to drop straight into a `report_builder` property
payload (`fit_score`, `highlights`, `caveat`, `financials`).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Signal vocabularies — matched against features + the agent's description.
# Each maps a preference -> regex of phrases that evidence it.
# --------------------------------------------------------------------------- #
PREF_PATTERNS: Dict[str, str] = {
    "secure_parking": r"\b(secure (car|park)|lock[- ]?up garage|basement park|car space|undercover park|garage)\b",
    "natural_light": r"\b(north[- ]?facing|north aspect|sun[- ]?drench|light[- ]?filled|abundant (natural )?light|bright|sun[- ]?filled)\b",
    "outdoor_space": r"\b(balcony|courtyard|terrace|garden|alfresco|entertain\w* (balcony|terrace|deck)|private outdoor)\b",
    "building_amenity": r"\b(pool|gym|fitness|sauna|spa|concierge|rooftop|residents['’]? lounge)\b",
    "transport": r"\b(station|metro|light rail|train|tram|bus|walk to transport|close to transport)\b",
    "cafes_lifestyle": r"\b(caf[eé]|dining|restaurant|village|shopping|vibrant|lifestyle)\b",
    "modern_finishes": r"\b(stone (kitchen|bench)|quality (appliances|finish)|renovat|brand new|near new|designer|contemporary|gas cook)\b",
    "period_charm": r"\b(period|art deco|federation|character|original feature|heritage|victorian|edwardian)\b",
    "low_maintenance": r"\b(low[- ]?maintenance|lock up and leave|easy[- ]?care|effortless)\b",
    "storage": r"\b(storage cage|built[- ]?in (robe|wardrobe)|ample storage|internal laundry)\b",
}

# Deal-breaker detectors. Some need a *combination* (ground floor AND main road).
DEALBREAKER_PATTERNS: Dict[str, str] = {
    "flood_fire_overlay": r"\b(flood (zone|overlay|prone)|bushfire (zone|overlay|prone)|fire overlay)\b",
    "main_road": r"\b(main road|busy road|arterial|highway frontage|traffic noise)\b",
    "ground_floor": r"\b(ground floor|street level)\b",
}


def _text_blob(listing: Dict[str, Any]) -> str:
    parts: List[str] = [str(listing.get("description") or ""),
                        str(listing.get("headline") or "")]
    for f in (listing.get("features") or []):
        parts.append(str(f))
    for sf in (listing.get("structured_features") or []):
        if isinstance(sf, dict):
            parts.append(str(sf.get("name") or ""))
    return "  ".join(parts).lower()


def _matched(blob: str, patterns: Dict[str, str]) -> Dict[str, bool]:
    return {k: bool(re.search(p, blob, re.I)) for k, p in patterns.items()}


def _price_point(listing: Dict[str, Any]) -> Optional[float]:
    if listing.get("price_exact"):
        return float(listing["price_exact"])
    lo, hi = listing.get("price_from"), listing.get("price_to")
    if lo and hi:
        return (float(lo) + float(hi)) / 2.0
    return float(lo or hi) if (lo or hi) else None


# --------------------------------------------------------------------------- #
# Rent comps -> yield (investor lens). Uses live rent rows already in the DB.
# --------------------------------------------------------------------------- #
def estimate_weekly_rent(db, suburb: Optional[str], beds: Optional[int]) -> Optional[float]:
    """Median advertised weekly rent for the same suburb + bed count, from the
    DB's stored rent listings. Returns None if we have nothing comparable."""
    if db is None or not suburb:
        return None
    try:
        from suburb_analyzer import parse_price_display
    except Exception:
        parse_price_display = lambda x: None  # noqa: E731
    rows = db.conn.execute(
        "SELECT price_from, price_to, price_display, beds FROM listings"
        " WHERE mode='rent' AND lower(suburb)=lower(?)", (suburb,)
    ).fetchall()
    weekly: List[float] = []
    for r in rows:
        r = dict(r)
        if beds and r.get("beds") and abs(int(r["beds"]) - int(beds)) > 1:
            continue
        val = r.get("price_from") or r.get("price_to") or parse_price_display(r.get("price_display"))
        # Weekly rent figures are small (<$5k); ignore anything that looks like a sale price.
        if val and 100 <= float(val) <= 5000:
            weekly.append(float(val))
    return round(statistics.median(weekly)) if weekly else None


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _band(score: float) -> str:
    if score >= 8.5:
        return "Exceptional fit"
    if score >= 7.0:
        return "Strong fit"
    if score >= 5.5:
        return "Worth a look"
    if score >= 4.0:
        return "Marginal"
    return "Off-brief"


def score_listing(
    listing: Dict[str, Any],
    front: Dict[str, Any],
    *,
    db=None,
    weekly_rent: Optional[float] = None,
    risk_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Score one listing against the buyer profile. Returns a structured read."""
    lens = (front.get("buyer_type") or "owner_occupier").lower()
    blob = _text_blob(listing)
    prefs = _matched(blob, PREF_PATTERNS)
    breakers = _matched(blob, DEALBREAKER_PATTERNS)

    addr = listing.get("address") or {}
    suburb = addr.get("suburb") if isinstance(addr, dict) else None
    beds = listing.get("beds")
    baths = listing.get("baths")
    cars = listing.get("cars")
    price = _price_point(listing)

    signals: List[str] = []
    violations: List[str] = []
    matched_prefs: List[str] = []
    missing_prefs: List[str] = []

    # ---- 1. Hard criteria (gate-ish; missing mins penalise hard) ----
    hard = 1.0
    if front.get("beds_min") and (beds or 0) < front["beds_min"]:
        hard -= 0.5
        violations.append(f"{beds or 0} bed vs {front['beds_min']}+ required")
    if front.get("baths_min") and (baths or 0) < front["baths_min"]:
        hard -= 0.2
        violations.append(f"{baths or 0} bath vs {front['baths_min']}+ required")
    if front.get("cars_min") and (cars or 0) < front["cars_min"]:
        hard -= 0.3
        violations.append(f"no/low parking vs {front['cars_min']}+ required")
    hard = max(0.0, hard)

    # ---- 2. Budget fit ----
    budget = front.get("budget") or {}
    buy_max = (budget.get("buy") or {}).get("max")
    budget_fit = 0.6
    if price and buy_max:
        ratio = price / buy_max
        if ratio <= 0.85:
            budget_fit = 1.0
            signals.append(f"Priced ~{(1-ratio)*100:.0f}% under the buy ceiling — room to move.")
        elif ratio <= 1.0:
            budget_fit = 0.8
            signals.append("Sits in the upper end of budget — the outgoings will decide it.")
        elif ratio <= 1.08:
            budget_fit = 0.45
            signals.append("Just over budget on the midpoint guide — only if it's special.")
        else:
            budget_fit = 0.15
            violations.append(f"~{(ratio-1)*100:.0f}% over the buy budget")

    # ---- 3. Deal-breakers (hard penalty + flag) ----
    breaker_penalty = 0.0
    if breakers.get("flood_fire_overlay"):
        breaker_penalty += 0.6
        violations.append("Listing text mentions a flood/fire overlay — verify.")
    if breakers.get("ground_floor") and breakers.get("main_road"):
        breaker_penalty += 0.5
        violations.append("Reads as ground floor on a main road — a stated deal-breaker.")
    elif breakers.get("main_road"):
        breaker_penalty += 0.2
        signals.append("Possible main-road exposure — check noise on inspection.")
    no_parking = (cars or 0) == 0 and not prefs.get("secure_parking")
    if no_parking and front.get("cars_min"):
        breaker_penalty += 0.4
        violations.append("No parking evident — a stated deal-breaker.")

    if risk_result is None:
        try:
            from risk import detect_risks
            risk_result = detect_risks(listing, front)
        except Exception:
            risk_result = None
    if risk_result:
        if risk_result.get("has_dealbreaker"):
            breaker_penalty += 0.7
            top = (risk_result.get("top") or [{}])[0]
            violations.append(f"Deal-breaker risk: {top.get('label', 'unresolved risk')}")
        else:
            penalty = float(risk_result.get("penalty") or 0)
            if penalty:
                breaker_penalty += min(0.25, penalty * 0.5)

    # ---- 4. Soft preferences (the lifestyle/quality layer) ----
    # Weight prefs differently per lens.
    OWNER_WEIGHTS = {
        "secure_parking": 1.4, "natural_light": 1.3, "outdoor_space": 1.2,
        "building_amenity": 0.8, "transport": 1.1, "cafes_lifestyle": 0.9,
        "modern_finishes": 1.0, "period_charm": 0.8, "low_maintenance": 1.1,
        "storage": 0.7,
    }
    INVESTOR_WEIGHTS = {
        "secure_parking": 1.3, "natural_light": 0.7, "outdoor_space": 0.7,
        "building_amenity": 1.0, "transport": 1.5, "cafes_lifestyle": 1.1,
        "modern_finishes": 1.2, "period_charm": 0.4, "low_maintenance": 1.2,
        "storage": 0.6,
    }
    weights = INVESTOR_WEIGHTS if lens == "investor" else OWNER_WEIGHTS
    got = sum(weights[k] for k, v in prefs.items() if v)
    possible = sum(weights.values())
    pref_fit = got / possible if possible else 0.0
    for k, v in prefs.items():
        label = k.replace("_", " ")
        (matched_prefs if v else missing_prefs).append(label)

    # ---- 5. Lens-specific: yield (investor) / amenity texture (owner) ----
    yield_block: Dict[str, Any] = {}
    lens_score = pref_fit  # default
    if lens == "investor":
        wr = weekly_rent if weekly_rent is not None else estimate_weekly_rent(db, suburb, beds)
        gross = None
        if wr and price:
            gross = round(wr * 52 / price * 100, 2)
            yield_block = {
                "est_rent_weekly": wr,
                "gross_yield_pct": gross,
                "basis": "median advertised rent, same suburb + beds (DB comps)" if weekly_rent is None
                         else "provided",
            }
            # Map yield to a 0..1 score: 3% poor, 5%+ excellent for Sydney apartments.
            yscore = max(0.0, min(1.0, (gross - 3.0) / 2.0))
            signals.append(f"Gross yield ~{gross:.1f}% on a ${wr:,}/wk rent estimate.")
            lens_score = 0.55 * yscore + 0.45 * pref_fit
        else:
            yield_block = {"est_rent_weekly": None, "gross_yield_pct": None,
                           "basis": "no rent comps stored yet — run a rent hunt for this suburb"}
            signals.append("No rent comps in the DB yet for a yield read — pull rent listings here.")
            lens_score = pref_fit

    # ---- Composite ----
    if lens == "investor":
        composite = 0.28 * hard + 0.22 * budget_fit + 0.50 * lens_score
    else:
        composite = 0.30 * hard + 0.25 * budget_fit + 0.45 * lens_score
    composite = max(0.0, composite - breaker_penalty)
    score10 = round(composite * 10, 1)

    return {
        "score": score10,
        "band": _band(score10),
        "lens": lens,
        "components": {
            "hard_criteria": round(hard, 2),
            "budget_fit": round(budget_fit, 2),
            ("yield_or_lifestyle"): round(lens_score, 2),
            "preference_match": round(pref_fit, 2),
            "dealbreaker_penalty": round(breaker_penalty, 2),
        },
        "price_used": price,
        "violations": violations,
        "matched_prefs": matched_prefs,
        "missing_prefs": missing_prefs,
        "yield": yield_block,
        "risks": risk_result or {},
        "signals": signals,
    }


# --------------------------------------------------------------------------- #
# CLI — score a cached/enriched listing JSON or a live id (for spot checks)
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    from buyer_profile import parse_buyer_md, DEFAULT_BUYER

    ap = argparse.ArgumentParser(prog="viability.py")
    ap.add_argument("--listing-json", help="Path to a normalized listing JSON (the 'listing' object)")
    ap.add_argument("--buyer", default=str(DEFAULT_BUYER))
    ap.add_argument("--weekly-rent", type=float, help="Override the rent estimate")
    ap.add_argument("--lens", choices=["owner_occupier", "investor"], help="Override buyer_type")
    args = ap.parse_args(argv)

    front, _ = parse_buyer_md(Path(args.buyer))
    if args.lens:
        front["buyer_type"] = args.lens

    if not args.listing_json:
        ap.error("provide --listing-json")
    raw = json.loads(Path(args.listing_json).read_text())
    listing = raw.get("listing", raw)

    db = None
    try:
        from db import PropertyDB, DEFAULT_DB_PATH
        if Path(DEFAULT_DB_PATH).exists():
            db = PropertyDB(Path(DEFAULT_DB_PATH))
    except Exception:
        db = None

    result = score_listing(listing, front, db=db, weekly_rent=args.weekly_rent)
    if db:
        db.close()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
