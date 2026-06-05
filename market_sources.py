#!/usr/bin/env python3
"""Market evidence and freshness helpers.

The folio prose is still written by the agent, but the claims it leans on should
carry dated evidence. This module keeps those facts small, serialisable, and
auditable so a stale macro view is visible instead of being typeset as truth.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_FRESHNESS_DAYS = 45


@dataclass
class SourceFact:
    key: str
    label: str
    value: Optional[str]
    source_name: str
    source_url: str
    observed_at: str
    published_at: Optional[str] = None
    unit: Optional[str] = None
    freshness_days: int = DEFAULT_FRESHNESS_DAYS
    status: str = "unknown"
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                parsed = datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def assess_freshness(fact: Dict[str, Any], *, now: Optional[datetime] = None) -> str:
    """Return fresh, stale, or unknown for one source fact."""
    if not fact.get("value"):
        return "unknown"
    now = now or datetime.now(timezone.utc)
    anchor = parse_dt(fact.get("published_at")) or parse_dt(fact.get("observed_at"))
    if not anchor:
        return "unknown"
    max_days = int(fact.get("freshness_days") or DEFAULT_FRESHNESS_DAYS)
    return "stale" if (now - anchor).days > max_days else "fresh"


def normalise_facts(facts: Iterable[Dict[str, Any]], *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for fact in facts:
        item = dict(fact)
        item["status"] = assess_freshness(item, now=now)
        out.append(item)
    return out


def validate_market_sources(market: Dict[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Summarise source coverage for a report market section.

    Expected payload shape:
        market["sources"] = [{key,label,value,source_name,source_url,observed_at,...}]
    """
    facts = normalise_facts(market.get("sources") or [], now=now)
    warnings: List[str] = []
    if not facts:
        warnings.append("No dated market sources supplied; treat Section 02 as unverified commentary.")
        status = "missing"
    else:
        stale = [f for f in facts if f["status"] == "stale"]
        unknown = [f for f in facts if f["status"] == "unknown"]
        if stale:
            warnings.append(f"{len(stale)} market source(s) are stale.")
        if unknown:
            warnings.append(f"{len(unknown)} market source(s) could not be freshness-checked.")
        status = "fresh" if not stale and not unknown else "needs_review"

    citations = []
    for fact in facts:
        bits = [fact.get("label") or fact.get("key"), fact.get("value")]
        source = fact.get("source_name")
        date = fact.get("published_at") or fact.get("observed_at")
        tail = " · ".join(str(x) for x in (source, date) if x)
        citations.append(" — ".join(str(x) for x in (" ".join(str(b) for b in bits if b), tail) if x))

    return {"status": status, "facts": facts, "warnings": warnings, "citations": citations}


def fetch_text(url: str, *, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "PropertyHunter/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fact_from_regex(
    *,
    key: str,
    label: str,
    source_name: str,
    source_url: str,
    pattern: str,
    text: str,
    unit: Optional[str] = None,
    freshness_days: int = DEFAULT_FRESHNESS_DAYS,
    observed_at: Optional[str] = None,
) -> SourceFact:
    match = re.search(pattern, text, re.I | re.S)
    value = match.group(1).strip() if match else None
    fact = SourceFact(
        key=key,
        label=label,
        value=value,
        unit=unit,
        source_name=source_name,
        source_url=source_url,
        observed_at=observed_at or utc_now(),
        freshness_days=freshness_days,
        notes=None if value else "Fetch succeeded but the expected value was not found.",
    )
    fact.status = assess_freshness(fact.to_dict())
    return fact


def default_market_source_urls() -> Dict[str, str]:
    return {
        "rba_cash_rate": "https://www.rba.gov.au/statistics/cash-rate/",
        "abs_cpi": "https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia/latest-release",
    }


def fetch_default_market_facts() -> List[Dict[str, Any]]:
    """Best-effort official-source facts.

    The regexes are deliberately conservative. If a page changes, the fact comes
    back as unknown rather than making up a number.
    """
    urls = default_market_source_urls()
    facts: List[SourceFact] = []
    try:
        rba_text = fetch_text(urls["rba_cash_rate"])
        match = re.search(
            r"<tr>\s*<th[^>]*>([^<]+)</th>\s*<td>[^<]+</td>\s*<td>([0-9]+(?:\.[0-9]+)?)</td>",
            rba_text,
            re.I | re.S,
        )
        fact = SourceFact(
            key="rba_cash_rate",
            label="RBA cash rate target",
            value=(f"{match.group(2)}%" if match else None),
            source_name="Reserve Bank of Australia",
            source_url=urls["rba_cash_rate"],
            observed_at=utc_now(),
            published_at=match.group(1) if match else None,
            unit="percent",
            freshness_days=45,
            notes=None if match else "Fetch succeeded but the expected table row was not found.",
        )
        fact.status = assess_freshness(fact.to_dict())
        facts.append(fact)
    except Exception as exc:
        facts.append(SourceFact(
            key="rba_cash_rate", label="RBA cash rate target", value=None,
            source_name="Reserve Bank of Australia", source_url=urls["rba_cash_rate"],
            observed_at=utc_now(), freshness_days=45, status="unknown",
            notes=f"Fetch failed: {exc}",
        ))
    try:
        cpi_text = fetch_text(urls["abs_cpi"])
        facts.append(fact_from_regex(
            key="abs_cpi",
            label="Latest Australian CPI",
            source_name="Australian Bureau of Statistics",
            source_url=urls["abs_cpi"],
            pattern=r"rose\s+([0-9]+(?:\.[0-9]+)?\s*%)",
            text=cpi_text,
            unit="percent",
            freshness_days=120,
        ))
    except Exception as exc:
        facts.append(SourceFact(
            key="abs_cpi", label="Latest Australian CPI", value=None,
            source_name="Australian Bureau of Statistics", source_url=urls["abs_cpi"],
            observed_at=utc_now(), freshness_days=120, status="unknown",
            notes=f"Fetch failed: {exc}",
        ))
    return [f.to_dict() for f in facts]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="market_sources.py")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    facts = fetch_default_market_facts()
    result = {"generated_at": utc_now(), "sources": facts, "freshness": validate_market_sources({"sources": facts})}
    print(json.dumps(result, indent=2) if args.json else "\n".join(result["freshness"]["citations"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
