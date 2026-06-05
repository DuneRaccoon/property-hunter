#!/usr/bin/env python3
"""Evidence-backed agent and agency behaviour report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from db import DEFAULT_DB_PATH, PropertyDB


def build_agent_report(db: PropertyDB, *, min_seen: int = 1, limit: int = 20, refresh: bool = True) -> Dict[str, Any]:
    """Return observable agent performance, never inferred reputation."""
    refreshed = db.refresh_all_agent_metrics() if refresh else 0
    rows = db.agent_performance(min_seen=min_seen, limit=limit)
    agents: List[Dict[str, Any]] = []
    for row in rows:
        delta = row.get("avg_guide_vs_sold")
        if delta is None:
            guide_read = "No sold-guide evidence yet"
        elif delta >= 0.10:
            guide_read = f"Sold results average {delta * 100:.1f}% above guide midpoint"
        elif delta <= -0.05:
            guide_read = f"Sold results average {abs(delta) * 100:.1f}% below guide midpoint"
        else:
            guide_read = f"Sold results average {delta * 100:.1f}% from guide midpoint"
        agents.append({**row, "guide_read": guide_read})
    return {
        "agent_count": len(agents),
        "min_seen": min_seen,
        "refreshed_agents": refreshed,
        "agents": agents,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agent_report.py")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--min-seen", type=int, default=1)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with PropertyDB(Path(args.db)) as db:
        report = build_agent_report(db, min_seen=args.min_seen, limit=args.limit, refresh=not args.no_refresh)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    print(f"Agent intelligence ({report['agent_count']} agents with {args.min_seen}+ listings)")
    for agent in report["agents"]:
        agency = f" — {agent['agency']}" if agent.get("agency") else ""
        print(f"\n## {agent['name']}{agency}")
        print(f"Listings seen: {agent['listings_seen']} | sold observed: {agent['listings_sold']}")
        print(f"Price drops observed: {agent['price_drops_observed']} | underquote signals: {agent['underquote_signals']}")
        print(agent["guide_read"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
