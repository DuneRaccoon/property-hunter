#!/usr/bin/env python3
"""Inspection-run planning for shortlisted properties."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class InspectionStop:
    listing_id: str
    address: str
    suburb: str
    start: str
    end: Optional[str]
    time_label: str
    clash: bool
    travel_note: str
    priority: str
    action: str


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%a %d %b %H:%M"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
    return None


def _address(listing: Dict[str, Any]) -> str:
    addr = listing.get("address") or {}
    if isinstance(addr, dict):
        return addr.get("display") or ", ".join(str(x) for x in (addr.get("street"), addr.get("suburb")) if x)
    return str(addr or listing.get("address_display") or "Unknown address")


def _suburb(listing: Dict[str, Any]) -> str:
    addr = listing.get("address") or {}
    if isinstance(addr, dict) and addr.get("suburb"):
        return str(addr["suburb"])
    return str(listing.get("suburb") or "")


def _inspections(listing: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for inspection in listing.get("inspections") or []:
        if isinstance(inspection, dict) and inspection.get("start"):
            yield inspection
    single = listing.get("inspection")
    if isinstance(single, dict) and single.get("start"):
        yield single


def _priority(listing: Dict[str, Any]) -> str:
    action = (listing.get("action_plan") or {}).get("best_next_action") or {}
    if action.get("type") == "pass":
        return "skip"
    score = listing.get("fit_score")
    try:
        if float(score) >= 8:
            return "must inspect"
        if float(score) >= 7:
            return "strong backup"
    except (TypeError, ValueError):
        pass
    return "optional"


def _time_label(start: datetime, end: Optional[datetime]) -> str:
    if end:
        return f"{start.strftime('%-I:%M%p').lower()}-{end.strftime('%-I:%M%p').lower()}"
    return start.strftime("%-I:%M%p").lower()


def _target_day(candidates: List[tuple[datetime, Dict[str, Any], Dict[str, Any]]], target_date: Optional[str]) -> Optional[str]:
    if target_date:
        return target_date
    if not candidates:
        return None
    counts: Dict[str, int] = {}
    saturday_counts: Dict[str, int] = {}
    for start, _end, _listing in candidates:
        key = start.date().isoformat()
        counts[key] = counts.get(key, 0) + 1
        if start.weekday() == 5:
            saturday_counts[key] = saturday_counts.get(key, 0) + 1
    pool = saturday_counts or counts
    return sorted(pool.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def build_inspection_plan(
    listings: List[Dict[str, Any]],
    *,
    target_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an inspection-ready run.

    The route is deliberately conservative: chronological order first, then a
    short suburb hand-off note. Real travel-time routing can come later once a
    maps source is wired in.
    """
    candidates: List[tuple[datetime, Optional[datetime], Dict[str, Any]]] = []
    for listing in listings:
        for inspection in _inspections(listing):
            start = _parse_dt(inspection.get("start"))
            if not start:
                continue
            candidates.append((start, _parse_dt(inspection.get("end")), listing))

    target = _target_day(candidates, target_date)
    stops_raw = [(s, e, l) for s, e, l in candidates if target and s.date().isoformat() == target]
    stops_raw.sort(key=lambda item: (item[0], _suburb(item[2]), _address(item[2])))

    stops: List[InspectionStop] = []
    prev_end: Optional[datetime] = None
    prev_suburb = ""
    for start, end, listing in stops_raw:
        suburb = _suburb(listing)
        clash = bool(prev_end and start < prev_end)
        if not prev_suburb:
            travel_note = "Start here."
        elif suburb == prev_suburb:
            travel_note = f"Stay in {suburb}."
        else:
            travel_note = f"Move from {prev_suburb} to {suburb}; confirm travel time."
        action = (listing.get("action_plan") or {}).get("best_next_action") or {}
        stops.append(
            InspectionStop(
                listing_id=str(listing.get("id") or ""),
                address=_address(listing),
                suburb=suburb,
                start=start.isoformat(),
                end=end.isoformat() if end else None,
                time_label=_time_label(start, end),
                clash=clash,
                travel_note=travel_note,
                priority=_priority(listing),
                action=action.get("label") or "Inspect and verify fundamentals",
            )
        )
        prev_end = end or start
        prev_suburb = suburb

    unscheduled = [
        {
            "listing_id": str(listing.get("id") or ""),
            "address": _address(listing),
            "suburb": _suburb(listing),
            "reason": "No inspection time visible",
            "action": "Call the agent and request access.",
        }
        for listing in listings
        if not list(_inspections(listing))
    ]

    return {
        "target_date": target,
        "summary": (
            f"{len(stops)} scheduled inspections"
            + (f" · {sum(1 for s in stops if s.clash)} clash(es)" if any(s.clash for s in stops) else "")
            + (f" · {len(unscheduled)} need agent follow-up" if unscheduled else "")
        ),
        "stops": [asdict(stop) for stop in stops],
        "unscheduled": unscheduled,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="inspection_plan.py")
    ap.add_argument("--listings-json", required=True)
    ap.add_argument("--target-date")
    args = ap.parse_args(argv)
    raw = json.loads(Path(args.listings_json).read_text(encoding="utf-8"))
    listings = raw.get("properties") or raw.get("listings") or raw
    print(json.dumps(build_inspection_plan(listings, target_date=args.target_date), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
