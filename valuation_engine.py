#!/usr/bin/env python3
"""Independent valuation-case engine.

Sits *on top of* the comparable-led ``valuation.value_listing`` read (which is kept
intact) and builds a multi-signal valuation case for a property — an independent
estimate of what it is worth, derived whether or not an asking price is stated.

Signals reconciled (each yields a point + range + confidence):
  1. Comparable sold median   — direct, from the standard comp read
  2. $ per bedroom            — median $/bed of comps x subject beds
  3. $ per internal sqm       — median $/sqm of comps x subject building area, when known
  4. Hedonic-adjusted comps   — comp median nudged by the property's own quality signals
  5. Yield-implied value      — market rent / target gross yield band (a cross-check)
  6. External AVM             — CoreLogic / propertyvalue.com.au, via a value provider

The engine weights the signals by reliability and agreement into one independent
estimate, writes a plain-English case, then — only if a guide exists — positions the
asking price against that independent estimate (over / fair / under, with headroom).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from db import DEFAULT_DB_PATH, PropertyDB
from valuation import (
    _addr_suburb,
    _price_per_bed,
    _rental_comps,
    _sold_comps,
    price_point,
    value_listing,
)
from value_providers import ValueEstimate, ValueProvider, default_providers

CONF_WEIGHT = {"high": 1.0, "medium": 0.65, "low": 0.35, "none": 0.0}
# Per-method reliability multipliers — how much we trust each method in principle,
# before scaling by the evidence's own confidence.
METHOD_WEIGHT = {
    "comparable_median": 1.0,
    "per_sqm": 0.85,
    "avm": 1.1,
    "hedonic": 0.7,
    "per_bed": 0.55,
    "yield_implied": 0.4,
}
# Default Sydney-unit gross-yield band used to imply a capital value from market rent.
DEFAULT_YIELD_BAND = (0.035, 0.045)


def _bed_match(target: Any, candidate: Any, tol: float = 0.0) -> bool:
    try:
        return abs(float(candidate) - float(target)) <= tol
    except (TypeError, ValueError):
        return False


def _conf_from_n(n: int, hi: int = 8, med: int = 4) -> str:
    if n >= hi:
        return "high"
    if n >= med:
        return "medium"
    if n >= 1:
        return "low"
    return "none"


def _as_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "size", "sqm", "area"):
            if value.get(key) is not None:
                return _as_number(value.get(key))
        return None
    m = re.search(r"[\d,]+(?:\.\d+)?", str(value))
    return float(m.group(0).replace(",", "")) if m else None


def _building_area(listing: Dict[str, Any]) -> Optional[float]:
    for key in ("building_area", "building_area_sqm", "internal_area", "internalArea"):
        v = _as_number(listing.get(key))
        if v and v > 0:
            return v
    return None


def _comp_internal_areas(db: PropertyDB, comp_ids: List[str]) -> Dict[str, float]:
    """Pull internal building areas for comps out of their stored raw_json."""
    areas: Dict[str, float] = {}
    if not comp_ids:
        return areas
    placeholders = ",".join("?" for _ in comp_ids)
    rows = db.conn.execute(
        f"SELECT id, raw_json FROM listings WHERE id IN ({placeholders})",
        comp_ids,
    ).fetchall()
    for row in rows:
        raw = row["raw_json"] if isinstance(row, dict) or hasattr(row, "keys") else None
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        listing = data.get("listing") if isinstance(data, dict) and "listing" in data else data
        if not isinstance(listing, dict):
            continue
        area = _building_area(listing)
        if area:
            areas[str(row["id"])] = area
    return areas


# --------------------------------------------------------------------------- #
# Individual value signals
# --------------------------------------------------------------------------- #


def _comparable_signal(
    comp_values: List[float], median: Optional[float], *, same_bed: bool = False
) -> Optional[ValueEstimate]:
    if not comp_values or median is None:
        return None
    conf = _conf_from_n(len(comp_values))
    scope = "same-bedroom" if same_bed else "matched"
    return ValueEstimate(
        provider="internal",
        method="comparable_median",
        point=round(median),
        low=round(min(comp_values)),
        high=round(max(comp_values)),
        confidence=conf,
        as_at=datetime.now(timezone.utc).isoformat(),
        source="sold comparables",
        note=f"Median of {len(comp_values)} {scope} sold comparables.",
    )


def _per_bed_signal(comps: List[Dict[str, Any]], beds: Any) -> Optional[ValueEstimate]:
    try:
        b = float(beds)
    except (TypeError, ValueError):
        return None
    if b <= 0:
        return None
    per_bed = [float(c["price_per_bed"]) for c in comps if c.get("price_per_bed")]
    if not per_bed:
        return None
    median_pb = statistics.median(per_bed)
    point = median_pb * b
    spread = (max(per_bed) - min(per_bed)) / 2.0 * b if len(per_bed) > 1 else point * 0.05
    return ValueEstimate(
        provider="internal",
        method="per_bed",
        point=round(point),
        low=round(point - spread),
        high=round(point + spread),
        confidence=_conf_from_n(len(per_bed)),
        as_at=datetime.now(timezone.utc).isoformat(),
        source="$/bedroom of comparables",
        note=f"Median ${round(median_pb):,}/bed across {len(per_bed)} comps x {b:g} beds.",
    )


def _per_sqm_signal(
    db: Optional[PropertyDB],
    comps: List[Dict[str, Any]],
    subject_area: Optional[float],
) -> Optional[ValueEstimate]:
    if not subject_area or db is None:
        return None
    areas = _comp_internal_areas(db, [str(c["id"]) for c in comps])
    rates: List[float] = []
    for c in comps:
        area = areas.get(str(c["id"]))
        price = c.get("sold_price")
        if area and price and area > 0:
            rates.append(float(price) / area)
    if not rates:
        return None
    median_rate = statistics.median(rates)
    point = median_rate * subject_area
    spread = (max(rates) - min(rates)) / 2.0 * subject_area if len(rates) > 1 else point * 0.05
    return ValueEstimate(
        provider="internal",
        method="per_sqm",
        point=round(point),
        low=round(point - spread),
        high=round(point + spread),
        confidence=_conf_from_n(len(rates), hi=6, med=3),
        as_at=datetime.now(timezone.utc).isoformat(),
        source="$/internal sqm of comparables",
        note=f"Median ${round(median_rate):,}/sqm across {len(rates)} sized comps x {subject_area:g} sqm.",
    )


# Hedonic quality adjustments applied to the comparable base. Each is a fractional
# nudge; the total is capped so the model never strays far from comparable evidence.
_HEDONIC_POSITIVE = [
    (r"north[- ]?east|north[- ]?facing|due north", 0.02, "north aspect"),
    (r"\bwater\b|harbour|ocean|district view|city view|skyline|panoramic", 0.03, "view"),
    (r"penthouse|top floor|high floor|upper level", 0.02, "elevated floor"),
    (r"renovated|brand new|architect|designer|fully rebuilt|as new", 0.03, "renovated/near-new"),
    (r"\blift\b|elevator", 0.01, "lift"),
    (r"courtyard|garden|terrace|large balcony|entertain", 0.01, "outdoor space"),
    (r"study|home office|extra room", 0.01, "study/extra room"),
    (r"double lock[- ]?up|2 car|two car|double garage|tandem", 0.02, "extra parking"),
]
_HEDONIC_NEGATIVE = [
    (r"ground floor|street level", -0.01, "ground floor"),
    (r"main road|busy road|busy street|highway|traffic", -0.02, "busy-road exposure"),
    (r"needs work|renovator|original condition|potential to|deceased estate|handyman", -0.03, "needs work"),
    (r"no parking|street parking only|unparked", -0.02, "no parking"),
    (r"walk[- ]?up|no lift", -0.01, "walk-up"),
]
_HEDONIC_CAP = 0.12


def _hedonic_signal(listing: Dict[str, Any], median: Optional[float], comp_conf: str) -> Optional[ValueEstimate]:
    if median is None:
        return None
    text = " ".join(
        str(x)
        for x in (
            listing.get("description") or "",
            " ".join(listing.get("features") or []) if isinstance(listing.get("features"), list) else listing.get("features") or "",
            listing.get("headline") or "",
        )
    ).lower()
    adj = 0.0
    reasons: List[str] = []
    for pattern, weight, label in _HEDONIC_POSITIVE + _HEDONIC_NEGATIVE:
        if re.search(pattern, text):
            adj += weight
            reasons.append(("+" if weight > 0 else "") + f"{round(weight * 100)}% {label}")
    adj = max(-_HEDONIC_CAP, min(_HEDONIC_CAP, adj))
    if abs(adj) < 0.005:
        return None
    point = median * (1 + adj)
    # Hedonic leans on the comp base, so it is never more confident than the comps,
    # and capped at medium because the adjustments are heuristic.
    conf = "medium" if comp_conf in ("high", "medium") else "low"
    return ValueEstimate(
        provider="internal",
        method="hedonic",
        point=round(point),
        low=round(median * (1 + min(adj, 0))),
        high=round(median * (1 + max(adj, 0))),
        confidence=conf,
        as_at=datetime.now(timezone.utc).isoformat(),
        source="hedonic adjustment of comps",
        note=f"Comp median adjusted {round(adj * 100):+d}% for: " + ", ".join(reasons) + ".",
    )


def _yield_signal(
    db: Optional[PropertyDB],
    listing: Dict[str, Any],
    front: Optional[Dict[str, Any]],
) -> Optional[ValueEstimate]:
    if db is None:
        return None
    rent_comps = _rental_comps(db, listing)
    rents = [float(c["weekly_rent"]) for c in rent_comps if c.get("weekly_rent")]
    if not rents:
        return None
    median_rent = statistics.median(rents)
    annual = median_rent * 52
    lo_yield, hi_yield = _yield_band(front)
    # Lower yield -> higher implied value, so invert the band bounds.
    high_val = annual / lo_yield
    low_val = annual / hi_yield
    point = (high_val + low_val) / 2.0
    return ValueEstimate(
        provider="internal",
        method="yield_implied",
        point=round(point),
        low=round(low_val),
        high=round(high_val),
        confidence="low" if len(rents) < 4 else "medium",
        as_at=datetime.now(timezone.utc).isoformat(),
        source="rental yield cross-check",
        note=(
            f"Market rent ~${round(median_rent):,}/wk ({len(rents)} comps) capitalised at "
            f"{round(lo_yield * 100, 1)}-{round(hi_yield * 100, 1)}% gross yield."
        ),
    )


def _yield_band(front: Optional[Dict[str, Any]]) -> Tuple[float, float]:
    market = (front or {}).get("market") if isinstance((front or {}).get("market"), dict) else {}
    band = market.get("gross_yield_band") if isinstance(market, dict) else None
    if isinstance(band, (list, tuple)) and len(band) == 2:
        try:
            lo, hi = float(band[0]), float(band[1])
            if 0 < lo < hi < 1:
                return lo, hi
        except (TypeError, ValueError):
            pass
    return DEFAULT_YIELD_BAND


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #


def _signal_weight(est: ValueEstimate) -> float:
    return METHOD_WEIGHT.get(est.method, 0.5) * CONF_WEIGHT.get(est.confidence, 0.0)


def _reconcile(signals: List[ValueEstimate]) -> Optional[Dict[str, Any]]:
    usable = [s for s in signals if s.best_point() and _signal_weight(s) > 0]
    if not usable:
        return None
    points = [s.best_point() for s in usable]
    weights = [_signal_weight(s) for s in usable]
    total_w = sum(weights)
    point = sum(p * w for p, w in zip(points, weights)) / total_w

    # Blended half-spread from each signal's own range (fallback 4% of its point).
    spreads = [s.half_spread() if s.half_spread() is not None else (s.best_point() * 0.04) for s in usable]
    blended_spread = sum(sp * w for sp, w in zip(spreads, weights)) / total_w
    # Disagreement between signals widens the band.
    dispersion = statistics.pstdev(points) if len(points) > 1 else 0.0
    half = blended_spread + dispersion * 0.5
    low, high = point - half, point + half

    cov = (statistics.pstdev(points) / point) if len(points) > 1 and point else 0.0
    if len(usable) >= 3 and cov < 0.06:
        confidence = "high"
    elif len(usable) >= 2 and cov < 0.12:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "point": round(point),
        "low": round(low),
        "high": round(high),
        "confidence": confidence,
        "signal_count": len(usable),
        "agreement": round(1 - cov, 3),
    }


def _assess_asking(ask: Optional[float], reconciled: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not ask:
        return None
    low, high, point = reconciled["low"], reconciled["high"], reconciled["point"]
    headroom = round(point - ask)
    if ask < low:
        gap = (point - ask) / point if point else 0
        position = "below independent range"
        verdict = "under-quoted opportunity" if gap >= 0.07 else "keenly priced"
        posture = (
            "Guide sits under our independent estimate — expect competition and a clearing "
            "price near or above the range. Treat the quote as a floor, not the budget."
        )
    elif ask <= high:
        position = "within independent range"
        verdict = "fairly guided"
        posture = "Guide is consistent with the evidence. Anchor offers to the strongest comparables."
    else:
        position = "above independent range"
        verdict = "over-quoted"
        posture = "Guide exceeds the evidence. Only chase if the property is materially superior to comps."
    return {
        "asking": round(ask),
        "position": position,
        "verdict": verdict,
        "negotiation_headroom": headroom,
        "posture": posture,
    }


def _build_case_narrative(
    reconciled: Dict[str, Any],
    signals: List[ValueEstimate],
    asking: Optional[Dict[str, Any]],
) -> List[str]:
    lines = [
        f"Independent estimate ${reconciled['point']:,} "
        f"(range ${reconciled['low']:,}–${reconciled['high']:,}), "
        f"{reconciled['confidence']} confidence from {reconciled['signal_count']} signal(s)."
    ]
    for s in sorted(signals, key=_signal_weight, reverse=True):
        if not s.best_point() or _signal_weight(s) <= 0:
            continue
        lines.append(f"· {s.method.replace('_', ' ')}: ${round(s.best_point()):,} — {s.note}")
    if asking:
        lines.append(
            f"Asking ${asking['asking']:,} is {asking['position']} "
            f"({asking['verdict']}); headroom to estimate ${asking['negotiation_headroom']:,}."
        )
    else:
        lines.append("No asking price stated — figure above is our independent estimate of worth.")
    return lines


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def build_valuation_case(
    listing: Dict[str, Any],
    db: Optional[PropertyDB] = None,
    *,
    front: Optional[Dict[str, Any]] = None,
    providers: Optional[List[ValueProvider]] = None,
) -> Dict[str, Any]:
    """Build a reconciled, asking-price-independent valuation case for one listing."""
    close_db = False
    if db is None:
        db = PropertyDB(Path(DEFAULT_DB_PATH))
        close_db = True
    if providers is None:
        providers = default_providers()
    try:
        objective = "rent" if (listing.get("mode") == "rent" or (front or {}).get("objective") in ("rent", "renter")) else "buy"
        # Keep the standard comp read attached for continuity / backward-compatible callers.
        standard = value_listing(listing, db, front=front)
        if objective == "rent":
            return {
                "mode": "rent",
                "standard_valuation": standard,
                "independent_estimate": None,
                "signals": [],
                "asking_comparison": None,
                "case": ["Rent mode uses the standard rental-comparable read; see standard_valuation."],
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

        comps = _sold_comps(db, listing)
        # The comparable median is bed-sensitive: blending 1-bed and 2-bed raw
        # prices drags the strongest signal. Anchor it to same-bedroom comps when a
        # usable cluster exists, else fall back to all matched comps. ($/bed and
        # $/sqm normalise away bed/size, so they keep the full comp set.)
        same_bed = [c for c in comps if _bed_match(listing.get("beds"), c.get("beds"))]
        use_same_bed = len(same_bed) >= 2
        comp_basis = same_bed if use_same_bed else comps
        comp_values = [float(c["sold_price"]) for c in comp_basis if c.get("sold_price")]
        median = statistics.median(comp_values) if comp_values else None
        comp_signal = _comparable_signal(comp_values, median, same_bed=use_same_bed)
        comp_conf = comp_signal.confidence if comp_signal else "none"

        signals: List[ValueEstimate] = []
        for sig in (
            comp_signal,
            _per_bed_signal(comps, listing.get("beds")),
            _per_sqm_signal(db, comps, _building_area(listing)),
            _hedonic_signal(listing, median, comp_conf),
            _yield_signal(db, listing, front),
        ):
            if sig is not None:
                signals.append(sig)

        for provider in providers:
            try:
                est = provider.estimate(listing, db=db, front=front)
            except Exception:
                est = None
            if est is not None and est.has_value():
                if est.method not in METHOD_WEIGHT:
                    est.method = "avm"
                signals.append(est)

        reconciled = _reconcile(signals)
        asking = price_point(listing)
        asking_comparison = _assess_asking(asking, reconciled) if reconciled else None
        case = _build_case_narrative(reconciled, signals, asking_comparison) if reconciled else [
            "Insufficient evidence for an independent estimate — gather more comparable sales."
        ]

        return {
            "mode": "buy",
            "independent_estimate": reconciled,
            "signals": [s.to_dict() for s in signals],
            "asking_comparison": asking_comparison,
            "standard_valuation": standard,
            "case": case,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if close_db:
            db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="valuation_engine.py")
    ap.add_argument("--listing-json", required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = ap.parse_args(argv)
    raw = json.loads(Path(args.listing_json).read_text(encoding="utf-8"))
    with PropertyDB(Path(args.db)) as db:
        print(json.dumps(build_valuation_case(raw.get("listing", raw), db), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
