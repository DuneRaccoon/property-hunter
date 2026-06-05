#!/usr/bin/env python3
"""Advice-first report and digest helpers.

Phase 7 is about making the output read like a buyer's agent's recommendation,
not a scraper transcript. These helpers keep the PDF and Telegram digest aligned
on the same ranking, confidence and uncertainty language.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def _score(prop: Dict[str, Any]) -> float:
    via = (prop.get("viability") or {}).get("score")
    fit = prop.get("fit_score")
    try:
        return float(via if via is not None else fit if fit is not None else 0)
    except (TypeError, ValueError):
        return 0.0


def _confidence(prop: Dict[str, Any]) -> str:
    valuation = prop.get("valuation") or {}
    explicit = valuation.get("confidence")
    if explicit in {"high", "medium", "low", "none"}:
        return "low" if explicit == "none" else explicit
    if _score(prop) >= 8.5:
        return "high"
    if _score(prop) >= 7.0:
        return "medium"
    return "low"


def ranked_properties(properties: Iterable[Dict[str, Any]], *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    ranked = sorted(list(properties), key=_score, reverse=True)
    return ranked[:limit] if limit else ranked


def why_this_might_be_wrong(prop: Dict[str, Any]) -> List[str]:
    """Return short, concrete uncertainty notes for a recommendation."""
    notes: List[str] = []
    valuation = prop.get("valuation") or {}
    diligence = prop.get("due_diligence") or {}
    risks = prop.get("risks") or {}
    action = prop.get("action_plan") or {}

    confidence = valuation.get("confidence")
    comp_count = valuation.get("comparable_count")
    if not valuation:
        notes.append("No comparable valuation panel is attached to this payload.")
    elif confidence in (None, "none", "low") or (comp_count is not None and comp_count < 4):
        notes.append("Comparable evidence is thin, so the price call can move quickly.")

    unresolved = diligence.get("critical_count")
    if unresolved:
        notes.append("Critical due-diligence items still need human verification.")

    top_risks = risks.get("top") or []
    if top_risks:
        first = top_risks[0]
        severity = str(first.get("severity") or "").lower()
        if severity in {"dealbreaker", "major"}:
            notes.append(f"Top risk may dominate the recommendation: {first.get('label')}.")
    elif not risks:
        notes.append("Risk scan is missing from the payload.")

    best = action.get("best_next_action") or {}
    if best and str(best.get("type") or "").lower() == "pass":
        notes.append("The action plan says pass; only revisit if the missing facts change.")

    if not notes and _confidence(prop) != "high":
        notes.append("Recommendation is useful, but not yet high-confidence.")
    return notes[:3]


def executive_recommendation(properties: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    ranked = ranked_properties(properties)
    if not ranked:
        return {
            "standout": None,
            "summary": "No properties cleared the current shortlist.",
            "confidence": "low",
            "reasons": [],
            "watch": [],
            "next_actions": [],
            "why_wrong": ["The run may simply have found no stock worth Ben's time."],
        }

    standout = ranked[0]
    val = standout.get("valuation") or {}
    risks = standout.get("risks") or {}
    action = standout.get("action_plan") or {}
    best = action.get("best_next_action") or {}

    reasons = []
    if standout.get("verdict"):
        reasons.append(str(standout["verdict"]))
    fairness = val.get("fairness")
    if fairness:
        reasons.append(f"Price read: {fairness}.")
    risk_summary = risks.get("summary")
    if risk_summary:
        reasons.append(f"Risk read: {risk_summary}.")

    watch = []
    if standout.get("caveat"):
        watch.append(str(standout["caveat"]))
    risks = standout.get("risks") or {}
    top_risks = risks.get("top") or []
    if top_risks:
        first = top_risks[0]
        if str(first.get("severity") or "").lower() in {"dealbreaker", "major"}:
            watch.append(f"{first.get('severity')}: {first.get('label')}")

    next_actions = []
    if best:
        label = best.get("label") or "Next action"
        deadline = best.get("deadline")
        detail = best.get("detail")
        next_actions.append(" · ".join(str(x) for x in (label, deadline, detail) if x))
    else:
        next_actions.append("Inspect the standout first, then request contract and strata documents if it still feels right.")

    for prop in ranked[1:3]:
        if prop.get("address"):
            next_actions.append(f"Keep warm: {prop['address']} if the top pick fails due diligence.")

    score = _score(standout)
    address = standout.get("address") or standout.get("headline") or "the standout property"
    return {
        "standout": standout,
        "summary": f"Lead with {address}. It is the strongest current fit at {score:.1f}/10.",
        "confidence": _confidence(standout),
        "reasons": reasons[:3],
        "watch": watch[:4],
        "next_actions": next_actions[:4],
        "why_wrong": why_this_might_be_wrong(standout),
    }


def _listing_line(item: Dict[str, Any], prefix: str = "-") -> str:
    decision = item.get("decision") or {}
    bits = [
        item.get("address") or item.get("id") or "Unknown listing",
        item.get("price"),
    ]
    headline = " — ".join(str(x) for x in bits if x)
    value = decision.get("price_fairness")
    risk = decision.get("top_risk")
    action = decision.get("next_action")
    detail = " | ".join(str(x) for x in (value, risk, action) if x)
    return f"{prefix} {headline}" + (f"\n  {detail}" if detail else "")


def format_daily_digest(report: Dict[str, Any], *, top_limit: int = 3) -> str:
    """Advice-first plain-text digest for Telegram."""
    hunts = report.get("hunts") or []
    new_items = [item for hunt in hunts for item in (hunt.get("new") or [])]
    changed_items = [item for hunt in hunts for item in (hunt.get("changed") or [])]
    stale_items = [item for hunt in hunts for item in (hunt.get("stale") or [])]

    def item_score(item: Dict[str, Any]) -> float:
        decision = item.get("decision") or {}
        try:
            return float(decision.get("viability_score") or 0)
        except (TypeError, ValueError):
            return 0.0

    new_items.sort(key=item_score, reverse=True)
    standout = new_items[0] if new_items else None

    lines = [f"Property hunt — {report.get('generated_at', '')}"]
    if standout:
        lines.append("")
        lines.append("Standout pick")
        lines.append(_listing_line(standout, prefix="*"))
        if standout.get("url"):
            lines.append(f"  {standout['url']}")

    if changed_items:
        lines.append("")
        lines.append("Changed / newly interesting")
        for item in changed_items[:top_limit]:
            life = item.get("lifecycle") or {}
            lines.append(f"- {item.get('address') or item.get('id')} — {life.get('why_now') or 'material change recorded'}")
            if item.get("url"):
                lines.append(f"  {item['url']}")

    if new_items:
        lines.append("")
        lines.append(f"Top new stock ({min(len(new_items), top_limit)} of {len(new_items)})")
        for item in new_items[:top_limit]:
            lines.append(_listing_line(item))
            if item.get("url"):
                lines.append(f"  {item['url']}")

    if stale_items:
        lines.append("")
        lines.append("Stale / missing from latest run")
        for item in stale_items[:top_limit]:
            life = item.get("lifecycle") or {}
            why = life.get("why_now") or "check if withdrawn, sold/leased, or provider missed it."
            lines.append(f"- {item.get('address') or item.get('id')} — {why}")
            if item.get("url"):
                lines.append(f"  {item['url']}")

    if not (standout or changed_items or stale_items):
        lines.append("")
        lines.append("No property is worth interrupting Ben for in this run.")

    lines.append("")
    lines.append("Counts: " + ", ".join(
        f"{h.get('name')}: {h.get('new_count', 0)} new, {h.get('changed_count', 0)} changed, {h.get('stale_count', 0)} stale"
        for h in hunts
    ))
    return "\n".join(lines).strip()
