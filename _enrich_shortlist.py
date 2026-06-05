"""One-off: enrich a curated shortlist by listing-id, persist detail to DB."""
import sys, time, json
from pathlib import Path
from db import DEFAULT_DB_PATH, PropertyDB
from domain_cli import (
    fetch_html, extract_listing_payload, listing_url_for_id,
)

IDS = [
    "2020887612",  # Zetland 2/2/1 $1.0-1.1M
    "2020884546",  # Zetland 2/2/1 $950k-1.045M
    "2020878443",  # Zetland 2/2/1 $950k-1.045M
    "2020745119",  # Zetland 2/2/1 $900k-990k
    "2020774371",  # Randwick 2/1/1 $1.05M
    "2020553114",  # Randwick 2/2/1 $1.0-1.1M
    "2020846625",  # Randwick 2/1/1 ~$1.0M auction
    "2020760507",  # Bondi Junction 1/1/1
    "2020768035",  # Crows Nest 1/1/1 $919k
    "2020795729",  # North Sydney 1/1/1 $900k
    "2020834480",  # Marrickville 2/1/1 $950k
    "2020834582",  # Marrickville 2/2/1
]

def main() -> int:
    out = {}
    with PropertyDB(Path(DEFAULT_DB_PATH)) as db:
        for i, lid in enumerate(IDS):
            url = listing_url_for_id(lid)
            try:
                html = fetch_html(url, fetcher="playwright", headed=True, no_cache=True)
                detail = extract_listing_payload(html, source_url=url, listing_id=lid)
                listing = detail.get("listing")
                if listing:
                    db.upsert_listing(listing, mode="sale")
                    out[lid] = {
                        "ok": True,
                        "address": (listing.get("address") or {}).get("display"),
                        "price": listing.get("price"),
                        "beds": listing.get("beds"),
                        "baths": listing.get("baths"),
                        "cars": listing.get("cars"),
                        "agents": [a.get("name") for a in (listing.get("agents") or [])],
                        "inspections": listing.get("inspections"),
                        "n_images": len(listing.get("images") or []),
                        "desc_len": len(listing.get("description") or ""),
                    }
                else:
                    out[lid] = {"ok": False, "err": "no listing"}
            except Exception as exc:
                out[lid] = {"ok": False, "err": str(exc)}
            print(f"[{i+1}/{len(IDS)}] {lid}: {out[lid].get('ok')} {out[lid].get('address') or out[lid].get('err')}", flush=True)
            time.sleep(2.0)  # be polite
    Path("_enrich_out.json").write_text(json.dumps(out, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
