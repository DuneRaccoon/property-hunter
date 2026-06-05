#!/usr/bin/env python3
"""Compose due diligence, valuation, risk and action planning for one listing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

from action_plan import build_action_plan
from buyer_profile import DEFAULT_BUYER, parse_buyer_md
from db import DEFAULT_DB_PATH, PropertyDB
from due_diligence import build_due_diligence
from risk import detect_risks
from valuation import value_listing
from viability import score_listing


def analyse_listing(
    listing: Dict[str, Any],
    front: Optional[Dict[str, Any]] = None,
    *,
    db: Optional[PropertyDB] = None,
) -> Dict[str, Any]:
    front = front or {}
    risks = detect_risks(listing, front)
    diligence = build_due_diligence(listing, front)
    objective = front.get("objective") or "buy"
    valuation = value_listing(listing, db, front=front) if objective in ("buy", "rent", "renter", "both") else None
    viability = score_listing(listing, front, db=db, risk_result=risks)
    actions = build_action_plan(listing, front, valuation=valuation, risks=risks, due_diligence=diligence)
    return {
        "viability": viability,
        "due_diligence": diligence,
        "valuation": valuation,
        "risks": risks,
        "action_plan": actions,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="decision_engine.py")
    ap.add_argument("--listing-json", required=True)
    ap.add_argument("--buyer", default=str(DEFAULT_BUYER))
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = ap.parse_args(argv)

    raw = json.loads(Path(args.listing_json).read_text(encoding="utf-8"))
    listing = raw.get("listing", raw)
    front, _ = parse_buyer_md(Path(args.buyer))
    with PropertyDB(Path(args.db)) as db:
        print(json.dumps(analyse_listing(listing, front, db=db), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
