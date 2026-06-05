#!/usr/bin/env python3
"""Parse buyer.md and translate the hard criteria into Domain search filters.

buyer.md has a YAML front-matter block (hard, machine-translatable criteria)
followed by prose (soft preferences the buyer's agent reasons over). This
module handles the deterministic half: front-matter -> filter dicts that
``build_search_url`` / the hunt runner understand. The prose is returned as-is
for the agent to use.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_BUYER = HERE / "buyer.md"

FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_buyer_md(path: Path = DEFAULT_BUYER) -> Tuple[Dict[str, Any], str]:
    """Return (front_matter_dict, prose)."""
    text = path.read_text(encoding="utf-8")
    m = FRONT_MATTER_RE.match(text)
    if not m:
        raise ValueError("buyer.md is missing a leading --- YAML front-matter block ---")
    front = yaml.safe_load(m.group(1)) or {}
    prose = m.group(2).strip()
    return front, prose


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def buyer_to_searches(front: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Translate hard criteria into named search definitions.

    Emits a 'buy' (sale) and/or 'rent' search per the objective, plus an
    optional 'sold' comparables search when track_sold is set.
    """
    objective = (front.get("objective") or "buy").lower()
    locations = front.get("locations") or {}
    suburbs = list(locations.get("suburbs") or [])
    region = locations.get("region")  # explicit region slug, optional
    beds_min = front.get("beds_min")
    beds_max = front.get("beds_max")
    baths_min = front.get("baths_min")
    cars_min = front.get("cars_min")
    ptypes = list(front.get("property_types") or [])
    exclude_under_offer = bool(front.get("exclude_under_offer"))
    sort = front.get("sort")
    budget = front.get("budget") or {}

    base = dict(
        suburbs=suburbs,
        region=region,
        beds_min=beds_min,
        beds_max=beds_max,
        baths_min=baths_min,
        cars_min=cars_min,
        ptypes=ptypes,
        exclude_under_offer=exclude_under_offer,
        sort=sort,
    )

    searches: List[Dict[str, Any]] = []
    loc_tag = _slug(suburbs[0]) if len(suburbs) == 1 else (_slug(region) if region else "multi")

    if objective in ("buy", "both"):
        buy_budget = budget.get("buy") or {}
        searches.append({
            "name": f"buy-{loc_tag}",
            "filters": {**base, "mode": "sale", "price_min": buy_budget.get("min"), "price_max": buy_budget.get("max")},
        })
    if objective in ("rent", "both"):
        rent_budget = budget.get("rent") or {}
        searches.append({
            "name": f"rent-{loc_tag}",
            "filters": {**base, "mode": "rent", "price_min": rent_budget.get("min"), "price_max": rent_budget.get("max")},
        })
    if front.get("track_sold"):
        # Comparable sold listings ignore under-offer/sort-by-new and any sale budget.
        searches.append({
            "name": f"sold-{loc_tag}",
            "filters": {**base, "mode": "sold", "exclude_under_offer": False, "sort": "solddate-desc"},
        })

    # Drop None-valued keys so build_search_url stays clean.
    for s in searches:
        s["filters"] = {k: v for k, v in s["filters"].items() if v not in (None, [], "")}
    return searches


def load_buyer_searches(path: Path = DEFAULT_BUYER) -> List[Dict[str, Any]]:
    front, _ = parse_buyer_md(path)
    return buyer_to_searches(front)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="buyer_profile.py")
    ap.add_argument("--buyer", default=str(DEFAULT_BUYER))
    ap.add_argument("--show-prose", action="store_true")
    args = ap.parse_args(argv)

    front, prose = parse_buyer_md(Path(args.buyer))
    searches = buyer_to_searches(front)
    print(json.dumps(searches, indent=2))
    if args.show_prose:
        print("\n--- PROSE ---\n" + prose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
