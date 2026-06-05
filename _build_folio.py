"""Build the live folio from this cron-style run.

Card + detail data is pulled from the DB (enriched detail pages). The buyer's-agent
JUDGEMENT prose (verdict, why-it-fits, highlights, caveat, fit_score, financials,
viability, and all of Section 02) is authored here — the deterministic/judgement
split the project is built around. Section 02 macro is grounded in live web
research done at run time (RBA 4.35%, third hike May 2026, rising bias, CPI, and a
spread of named Sydney forecasts).
"""
import json
import sqlite3
from pathlib import Path

from db import DEFAULT_DB_PATH
from buyer_profile import DEFAULT_BUYER, parse_buyer_md
from decision_engine import analyse_listing
from db import PropertyDB
from report_builder import build_report, REPORTS_DIR

DB = Path(DEFAULT_DB_PATH)
BUYER_FRONT, _BUYER_PROSE = parse_buyer_md(DEFAULT_BUYER)


def _conn():
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    return con


def load(lid: str) -> dict:
    """Pull a listing's card + enriched detail from the DB as a folio card dict."""
    con = _conn()
    row = con.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
    if not row:
        raise SystemExit(f"listing {lid} not in DB")
    raw = json.loads(row["raw_json"] or "{}")

    # images grouped by type
    imgs = con.execute(
        "SELECT url, media_type FROM listing_images WHERE listing_id=? ORDER BY position", (lid,)
    ).fetchall()
    photos = [r["url"] for r in imgs if (r["media_type"] or "photo") == "photo"]
    floorplans = [r["url"] for r in imgs if r["media_type"] == "floorplan"]
    if not photos:  # fall back to raw images
        ri = raw.get("images") or []
        photos = [i.get("url") if isinstance(i, dict) else i for i in ri]

    # agent
    ag = con.execute(
        "SELECT a.name,a.mobile,a.email,a.agency FROM agents a "
        "JOIN listing_agents la ON la.agent_id=a.id WHERE la.listing_id=? LIMIT 3", (lid,)
    ).fetchall()
    agents = [{"name": a["name"], "mobile": a["mobile"], "email": a["email"]} for a in ag]
    agency = {"name": (ag[0]["agency"] if ag else row["agency"]) or "", "logo": None}

    # inspections from raw
    inspections = []
    for ins in (raw.get("inspections") or []):
        if ins.get("start"):
            inspections.append({"start": ins["start"], "end": ins.get("end")})

    addr = raw.get("address") or {}
    composed = ", ".join(filter(None, [
        addr.get("street"),
        " ".join(filter(None, [addr.get("suburb"), addr.get("state"), addr.get("postcode")])).strip(),
    ]))
    con.close()
    return {
        "id": lid,
        "_raw_listing": raw,
        "address": addr.get("display") or row["address_display"] or composed or "",
        "suburb": addr.get("suburb") or row["suburb"] or "",
        "price": raw.get("price") or row["price_display"] or "Contact agent",
        "beds": raw.get("beds") if raw.get("beds") is not None else row["beds"],
        "baths": raw.get("baths") if raw.get("baths") is not None else row["baths"],
        "cars": (raw.get("cars") if raw.get("cars") is not None else row["cars"]) or 0,
        "property_type": "Apartment",
        "url": raw.get("url") or row["url"] or f"https://www.domain.com.au/{lid}",
        "description": raw.get("description") or "",
        "features": raw.get("features") or [],
        "images": {
            "hero": photos[0] if photos else None,
            "gallery": photos[1:] if len(photos) > 1 else [],
            "floorplan": floorplans[0] if floorplans else None,
        },
        "agency": agency,
        "agents": agents,
        "inspections": inspections,
    }


def prop(lid: str, **judgement) -> dict:
    base = load(lid)
    with PropertyDB(DB) as db:
        decision = analyse_listing(base["_raw_listing"], BUYER_FRONT, db=db)
    base.update(decision)
    base.pop("_raw_listing", None)
    return {**base, **judgement}


# ---- prose authored after reading enrichment; filled in build step ---------
PROPERTIES: list = []   # populated in _build_folio_data.py
MARKET: dict = {}
META: dict = {}
BRIEF: dict = {}


if __name__ == "__main__":
    from _build_folio_data import META, BRIEF, MARKET, PROPERTIES  # noqa
    payload = {"meta": META, "brief": BRIEF, "market": MARKET, "properties": PROPERTIES}
    out = build_report(payload, REPORTS_DIR / "property_folio_live.pdf", palette="folio")
    print(f"Wrote {out} ({out.stat().st_size/1_000_000:.1f} MB)")
