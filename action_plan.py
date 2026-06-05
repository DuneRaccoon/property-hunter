#!/usr/bin/env python3
"""Operational next steps for each shortlisted property."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from due_diligence import build_due_diligence
from risk import detect_risks


@dataclass(frozen=True)
class Action:
    type: str
    priority: str
    deadline: str
    label: str
    detail: str


def _agent(listing: Dict[str, Any]) -> Dict[str, Any]:
    for agent in listing.get("agents") or []:
        if isinstance(agent, dict) and agent.get("name"):
            return agent
    return {}


def _address(listing: Dict[str, Any]) -> str:
    addr = listing.get("address") or {}
    if isinstance(addr, dict):
        return addr.get("display") or ", ".join(str(x) for x in (addr.get("street"), addr.get("suburb")) if x)
    return str(addr or listing.get("address_display") or "the property")


def _template(listing: Dict[str, Any], need_strata: bool, *, renter: bool = False) -> str:
    agent = _agent(listing)
    name = agent.get("name") or "there"
    if renter:
        return (
            f"Hi {name}, I'm interested in renting {_address(listing)}. Could you please confirm "
            "the next inspection time, available date, lease length, pet policy, included parking/storage, "
            "and whether heating or air-conditioning is installed? Thanks, Ben."
        )
    asks = ["current price guide", "next inspection time", "contract"]
    if need_strata:
        asks.append("strata report or strata documents")
    asks.append("whether air-conditioning is installed or strata-approved")
    return (
        f"Hi {name}, I'm interested in {_address(listing)}. Could you please send the "
        f"{', '.join(asks[:-1])}, and {asks[-1]}? Thanks, Ben."
    )


def build_action_plan(
    listing: Dict[str, Any],
    front: Optional[Dict[str, Any]] = None,
    *,
    valuation: Optional[Dict[str, Any]] = None,
    risks: Optional[Dict[str, Any]] = None,
    due_diligence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    front = front or {}
    objective = str(front.get("objective") or listing.get("mode") or "buy").lower()
    renter = objective in ("rent", "renter") or listing.get("mode") == "rent"
    risks = risks or detect_risks(listing, front)
    due_diligence = due_diligence or build_due_diligence(listing, front)
    items = due_diligence.get("items") or []
    actions: List[Action] = []

    if risks.get("has_dealbreaker"):
        actions.append(Action("pass", "critical", "now", "Pass unless the deal-breaker is disproved", risks["top"][0]["reason"]))

    if not (listing.get("inspections") or listing.get("inspection")):
        actions.append(Action("contact_agent", "high", "today", "Ask for inspection access", "No open time is visible in the current listing data."))
    else:
        actions.append(Action("inspect", "high", "next open", "Inspect in person", "Check light, noise, building condition, floorplan feel and air-conditioning."))

    need_contract = any(i["category"] == "legal" for i in items)
    need_strata = any(i["category"] == "strata" for i in items)
    if renter:
        actions.append(Action("prepare_application", "high", "before inspection", "Prepare rental application", "Have ID, payslips, references and pet profile ready before the open."))
        actions.append(Action("ask_property_manager", "high", "today", "Ask renter suitability questions", "Confirm lease term, available date, pet policy, parking/storage and heating/cooling."))
    if need_contract and not renter:
        actions.append(Action("request_contract", "high", "before offer", "Request contract", "Have conveyancer review before signing, bidding or waiving cooling-off."))
    if need_strata and not renter:
        actions.append(Action("request_strata", "high", "before offer", "Request strata documents", "Check levies, defects, capital works fund, insurance and meeting minutes."))
    if valuation and valuation.get("confidence") in ("none", "low") and not renter:
        actions.append(Action("run_comps", "medium", "before offer", "Run deeper comparable review", "Current comparable evidence is thin."))
    elif valuation and valuation.get("fairness") in ("stretched", "overpriced") and not renter:
        actions.append(Action("price_discipline", "high", "before offer", "Set hard ceiling", valuation.get("negotiation_posture", "")))

    if renter:
        questions = [
            "What is the available date and preferred lease length?",
            "Are pets permitted, and what approval is required?",
            "Is air-conditioning or heating installed and working?",
            "Is parking or storage included in the rent?",
            "How many applications are already in?",
        ]
        documents = ["photo ID", "recent payslips", "rental references", "pet profile", "bank statement if required"]
    else:
        questions = [
            "Is air-conditioning installed? If not, is strata approval straightforward?",
            "What are the quarterly strata levies and any special levies?",
            "Any known defects, water ingress, cladding, lift or capital works issues?",
            "Why is the owner selling, and what price feedback has the campaign had?",
            "How many contracts are out and are there offers already?",
        ]
        documents = ["finance pre-approval", "deposit funds", "photo ID", "conveyancer details"]

    if not actions:
        actions.append(Action("monitor", "medium", "this week", "Monitor and compare", "No urgent next step from available data."))

    primary = actions[0]
    return {
        "best_next_action": asdict(primary),
        "actions": [asdict(a) for a in actions],
        "agent_message": _template(listing, need_strata, renter=renter),
        "inspection_questions": questions,
        "documents_to_prepare": documents,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="action_plan.py")
    ap.add_argument("--listing-json", required=True)
    args = ap.parse_args(argv)
    raw = json.loads(Path(args.listing_json).read_text(encoding="utf-8"))
    print(json.dumps(build_action_plan(raw.get("listing", raw)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
