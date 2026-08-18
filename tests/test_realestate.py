"""Tests for the realestate.com.au fallback scraper and provider fallback chain.

Everything here is offline: the REA parser is exercised against a fixture built
to REA's real doubly-stringified ``window.ArgonautExchange`` shape, and the
fallback chain is driven with stub providers.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import realestate_cli as rea
from db import PropertyDB
from hunt_runner import run_hunt
from source_providers import (
    ListingSearchResult,
    RealestateListingProvider,
    _passes_filters,
    build_provider_chain,
)
from buyer_profile import DEFAULT_BUYER, parse_buyer_md

FRONT, _ = parse_buyer_md(DEFAULT_BUYER)


def _argonaut_html(listing: dict, *, experience="resi-property_search-experience-web",
                   total=69, max_pages=5) -> str:
    """Wrap a listing in REA's real nested ArgonautExchange envelope."""
    inner = json.dumps({
        "buySearch": {
            "results": {
                "exact": {"items": [{"listing": listing}]},
                "pagination": {"maxPageNumberAvailable": max_pages, "totalResultsCount": total},
                "totalResultsCount": total,
            }
        }
    })
    urql = json.dumps({"q1": {"data": inner}})
    argo = {experience: {"urqlClientCache": urql}}
    return "<html><body><script>window.ArgonautExchange=" + json.dumps(argo) + ";</script></body></html>"


def _listing(**overrides) -> dict:
    base = {
        "id": "143029712",
        "propertyType": {"display": "Apartment", "id": "apartment"},
        "description": "Set in the heart of Zetland.",
        "_links": {"canonical": {"href": "https://www.realestate.com.au/property-apartment-nsw-zetland-143029712"}},
        "address": {
            "suburb": "Zetland", "state": "NSW", "postcode": "2017",
            "display": {"shortAddress": "101/8 Defries Ave",
                        "fullAddress": "101/8 Defries Ave, Zetland, NSW 2017",
                        "geocode": {"latitude": -33.906, "longitude": 151.209}},
        },
        "price": {"display": "$1,050,000"},
        "generalFeatures": {"bedrooms": {"value": 2}, "bathrooms": {"value": 1}, "parkingSpaces": {"value": 1}},
        "media": {"images": [{"templatedUrl": "https://i2.au.reastatic.net/{size}/abc/image.jpg"}]},
        "listingCompany": {"name": "Ray White Zetland", "phoneNumber": "0298765432"},
        "listers": [{"name": "Jane Smith", "phoneNumber": {"display": "0412345678"}, "id": "ag-1"}],
    }
    base.update(overrides)
    return base


class TestUrlBuilder(unittest.TestCase):
    def test_buy_slug_matches_rea_grammar(self):
        url = rea.build_search_url(
            mode="sale", suburbs=["Zetland NSW 2017"], price_min=0, price_max=1_100_000,
            beds_min=1, ptypes=["apartment"],
        )
        self.assertIn("/buy/property-apartment-with-1-bedrooms-between-0-1100000-in-zetland,+nsw+2017/list-1", url)

    def test_sold_channel(self):
        url = rea.build_search_url(mode="sold", suburbs=["Randwick NSW 2031"], ptypes=["apartment"])
        self.assertTrue(url.startswith("https://www.realestate.com.au/sold/"))

    def test_locality_slug(self):
        self.assertEqual(rea.locality_slug("North Sydney NSW 2060"), "north+sydney,+nsw+2060")

    def test_id_namespacing_roundtrip(self):
        self.assertEqual(rea._norm_id("rea:143029712"), "143029712")
        self.assertEqual(rea._norm_id("143029712"), "143029712")


class TestParser(unittest.TestCase):
    def test_search_payload_shape(self):
        payload = rea.extract_search_payload(_argonaut_html(_listing()), source_url="http://x", mode="sale")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["search_result_count"], 69)
        self.assertEqual(payload["blocked_markers"], [])
        l = payload["listings"][0]
        self.assertEqual(l["id"], "rea:143029712")
        self.assertEqual(l["beds"], 2)
        self.assertEqual(l["price_from"], 1_050_000)
        self.assertEqual(l["address"]["suburb"], "Zetland")
        self.assertEqual(l["agents"][0]["mobile"], "0412345678")
        self.assertTrue(l["url"].startswith("https://www.realestate.com.au/"))

    def test_kasada_challenge_detected(self):
        html = '<html><body><script>window.KPSDK={}</script><script src="/x/ips.js"></script></body></html>'
        payload = rea.extract_search_payload(html, source_url="http://x")
        self.assertEqual(payload["block_reason"], "kasada_challenge")
        self.assertEqual(payload["listings"], [])

    def test_persists_through_db_contract(self):
        norm = rea.normalize_listing_model(_listing(), mode="sale")
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "p.db") as db:
                lid = db.upsert_listing(norm, mode="sale")
                row = db.conn.execute(
                    "SELECT suburb, price_from, beds, agency, url FROM listings WHERE id=?", (lid,)
                ).fetchone()
        self.assertEqual(lid, "rea:143029712")
        self.assertEqual(row["suburb"], "Zetland")
        self.assertEqual(row["price_from"], 1_050_000)
        self.assertEqual(row["agency"], "Ray White Zetland")

    def test_sold_date_object_flattened_to_iso(self):
        """REA ships ``dateSold`` as ``{display, __typename}`` with no ISO value.

        Binding that dict straight into the TEXT column is a hard write failure
        (caught live on 2026-08-13), and Domain rows store ISO, so both sources
        have to agree on format for comparable queries to sort correctly.
        """
        raw = {**_listing(), "dateSold": {"display": "07 Aug 2026", "__typename": "DateSold"}}
        norm = rea.normalize_listing_model(raw, mode="sold")
        self.assertEqual(norm["sold"]["date"], "2026-08-07")
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "p.db") as db:
                lid = db.upsert_listing(norm, mode="sold")
                row = db.conn.execute(
                    "SELECT sold_date FROM listings WHERE id=?", (lid,)
                ).fetchone()
        self.assertEqual(row["sold_date"], "2026-08-07")

    def test_sale_method_object_flattened(self):
        raw = {**_listing(), "dateSold": {"display": "07 Aug 2026"},
               "saleMethod": {"display": "Private treaty", "__typename": "SaleMethod"}}
        norm = rea.normalize_listing_model(raw, mode="sold")
        self.assertEqual(norm["sold"]["method"], "Private treaty")


class TestRicherFields(unittest.TestCase):
    """REA publishes structured data Domain doesn't; it has to reach the DB."""

    def _rich(self):
        return {
            **_listing(),
            "propertySizes": {
                "building": {"displayValue": "78", "sizeUnit": {"displayValue": "m²"}},
                "land": {"displayValue": "0", "sizeUnit": {"displayValue": "m²"}},
            },
            "generalFeatures": {
                "bedrooms": {"value": 2}, "bathrooms": {"value": 1},
                "parkingSpaces": {"value": 1}, "studies": {"value": 1},
            },
            "productDepth": "premiere",
            "auction": {"dateTime": {"value": "2026-08-29T13:30:00+10:00"}},
            "listingCompany": {
                "name": "Ray White Touma Taylor",
                "businessPhone": "02 8322 0750",
                "ratingsReviews": {"avgRating": 5, "totalReviews": 428},
            },
            "_links": {
                "canonical": {"href": "https://www.realestate.com.au/property-apartment-nsw-zetland-1"},
                "submitEnquiry": {"href": "https://agent-contact.realestate.com.au/contact-agent/listing/1"},
            },
        }

    def test_normalizes_richer_fields(self):
        n = rea.normalize_listing_model(self._rich(), mode="sale")
        self.assertEqual(n["building_area_sqm"], 78)
        self.assertEqual(n["study"], 1)
        self.assertEqual(n["product_depth"], "premiere")
        self.assertEqual(n["auction_at"], "2026-08-29T13:30:00+10:00")
        self.assertEqual(n["agency"]["rating"], 5)
        self.assertEqual(n["agency"]["review_count"], 428)
        self.assertEqual(n["agency"]["phone"], "02 8322 0750")
        self.assertTrue(n["enquiry_url"].startswith("https://agent-contact"))

    def test_richer_fields_persist(self):
        n = {**rea.normalize_listing_model(self._rich(), mode="sale"), "source_provider": "realestate"}
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "p.db") as db:
                lid = db.upsert_listing(n, mode="sale")
                row = db.conn.execute(
                    "SELECT building_area_sqm, study, auction_at, product_depth, enquiry_url,"
                    " agency, agency_phone, agency_rating, agency_review_count, source_provider"
                    " FROM listings WHERE id=?", (lid,)
                ).fetchone()
        self.assertEqual(row["building_area_sqm"], 78)
        self.assertEqual(row["study"], 1)
        self.assertEqual(row["auction_at"], "2026-08-29T13:30:00+10:00")
        self.assertEqual(row["product_depth"], "premiere")
        self.assertEqual(row["agency"], "Ray White Touma Taylor")
        self.assertEqual(row["agency_phone"], "02 8322 0750")
        self.assertEqual(row["agency_rating"], 5)
        self.assertEqual(row["agency_review_count"], 428)
        self.assertEqual(row["source_provider"], "realestate")

    def test_sparse_card_does_not_wipe_enriched_values(self):
        """A later search card lacking detail data must not null out what we have."""
        rich = {**rea.normalize_listing_model(self._rich(), mode="sale"), "source_provider": "realestate"}
        sparse = {**rich, "building_area_sqm": None, "product_depth": None, "auction_at": None}
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "p.db") as db:
                db.upsert_listing(rich, mode="sale")
                lid = db.upsert_listing(sparse, mode="sale")
                row = db.conn.execute(
                    "SELECT building_area_sqm, product_depth, auction_at FROM listings WHERE id=?", (lid,)
                ).fetchone()
        self.assertEqual(row["building_area_sqm"], 78)
        self.assertEqual(row["product_depth"], "premiere")
        self.assertEqual(row["auction_at"], "2026-08-29T13:30:00+10:00")

    def test_agent_job_title_and_rating_persist(self):
        raw = {**self._rich(), "listers": [{
            "name": "Roger Wardy", "id": "2333530",
            "jobTitle": "Director | Licensed Real Estate Agent | Auctioneer",
            "phoneNumber": {"display": "0412 345 678"},
            "listerRatingsReviews": {"avgRating": 4.9, "totalReviews": 120},
        }]}
        n = rea.normalize_listing_model(raw, mode="sale")
        self.assertEqual(n["agents"][0]["job_title"], "Director | Licensed Real Estate Agent | Auctioneer")
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "p.db") as db:
                db.upsert_listing(n, mode="sale")
                row = db.conn.execute(
                    "SELECT job_title, rating, review_count FROM agents WHERE name=?", ("Roger Wardy",)
                ).fetchone()
        self.assertEqual(row["rating"], 4.9)
        self.assertEqual(row["review_count"], 120)

    def test_video_and_tour_media_captured(self):
        raw = {**self._rich(), "media": {
            "images": [{"templatedUrl": "https://i2.au.reastatic.net/{size}/a/image.jpg"}],
            "floorplans": [{"templatedUrl": "https://i2.au.reastatic.net/{size}/b/image.jpg"}],
            "videos": [{"href": "https://youtu.be/abc123"}],
            "threeDimensionalTours": [{"href": "https://my.matterport.com/show/?m=xyz"}],
        }}
        media = rea.normalize_listing_model(raw, mode="sale")["images"]
        types = {m["type"] for m in media}
        self.assertIn("video", types)
        self.assertIn("tour_3d", types)
        self.assertIn("floorplan", types)

    def test_small_internal_area_flagged(self):
        from risk import detect_risks
        listing = {"property_type": "Apartment", "beds": 2, "building_area_sqm": 55,
                   "description": "Bright two bedroom apartment.", "suburb": "Zetland"}
        labels = [i["label"] for i in detect_risks(listing)["items"]]
        self.assertIn("Small internal area", labels)
        self.assertNotIn("Internal area unclear", labels)

    def test_adequate_internal_area_not_flagged(self):
        """With a real number in hand, neither the guess nor the warning fires."""
        from risk import detect_risks
        listing = {"property_type": "Apartment", "beds": 2, "building_area_sqm": 88,
                   "description": "Bright two bedroom apartment.", "suburb": "Zetland"}
        labels = [i["label"] for i in detect_risks(listing)["items"]]
        self.assertNotIn("Small internal area", labels)
        self.assertNotIn("Internal area unclear", labels)

    def test_area_unclear_still_flagged_without_data(self):
        """Domain rows have no structured area — the old fallback must survive."""
        from risk import detect_risks
        listing = {"property_type": "Apartment", "beds": 2,
                   "description": "Bright two bedroom apartment.", "suburb": "Zetland"}
        labels = [i["label"] for i in detect_risks(listing)["items"]]
        self.assertIn("Internal area unclear", labels)


class TestAgentMerge(unittest.TestCase):
    """Card-only passes create agency-less agent rows that split an agent's history."""

    def _db(self, tmp):
        return PropertyDB(Path(tmp) / "p.db")

    def test_orphan_agent_merged_into_real_agency_row(self):
        with TemporaryDirectory() as tmp:
            with self._db(tmp) as db:
                db.upsert_listing({"id": "a1", "address": {"suburb": "Zetland"},
                                   "agents": [{"name": "Roger Wardy"}]}, mode="sale")
                db.upsert_listing({"id": "a2", "address": {"suburb": "Zetland"},
                                   "agency": {"name": "Ray White"},
                                   "agents": [{"name": "Roger Wardy", "mobile": "0400000000",
                                               "job_title": "Director"}]}, mode="sale")
                before = db.conn.execute("SELECT COUNT(*) FROM agents WHERE name='Roger Wardy'").fetchone()[0]
                self.assertEqual(before, 2)

                result = db.merge_orphan_agents()
                rows = db.conn.execute(
                    "SELECT agency, job_title, mobile FROM agents WHERE name='Roger Wardy'"
                ).fetchall()
                links = db.conn.execute(
                    "SELECT COUNT(DISTINCT listing_id) FROM listing_agents"
                ).fetchone()[0]

        self.assertEqual(result["merged"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agency"], "Ray White")
        self.assertEqual(rows[0]["job_title"], "Director")
        # Both listings must still be attributed to the surviving agent.
        self.assertEqual(links, 2)

    def test_ambiguous_name_left_alone(self):
        """One name at two agencies could be two different people — don't guess."""
        with TemporaryDirectory() as tmp:
            with self._db(tmp) as db:
                db.upsert_listing({"id": "b1", "agents": [{"name": "John Smith"}]}, mode="sale")
                db.upsert_listing({"id": "b2", "agency": {"name": "Agency A"},
                                   "agents": [{"name": "John Smith"}]}, mode="sale")
                db.upsert_listing({"id": "b3", "agency": {"name": "Agency B"},
                                   "agents": [{"name": "John Smith"}]}, mode="sale")
                result = db.merge_orphan_agents()
                n = db.conn.execute("SELECT COUNT(*) FROM agents WHERE name='John Smith'").fetchone()[0]
        self.assertEqual(result["merged"], 0)
        self.assertEqual(result["skipped_ambiguous"], 1)
        self.assertEqual(n, 3)


class TestProjectProfiles(unittest.TestCase):
    """REA mixes whole-development ``/project/`` profiles into search results."""

    def test_project_profile_dropped(self):
        from source_providers import _is_project_profile
        project = {
            "url": "https://www.realestate.com.au/project/vertex-zetland-600045768",
            "price_from": None, "beds": None, "baths": None, "cars": None,
        }
        self.assertTrue(_is_project_profile(project))

    def test_real_listing_kept(self):
        from source_providers import _is_project_profile
        real = {
            "url": "https://www.realestate.com.au/property-apartment-nsw-zetland-151322976",
            "price_from": 850_000, "beds": 1, "baths": 1, "cars": 1,
        }
        self.assertFalse(_is_project_profile(real))

    def test_project_url_with_real_data_kept(self):
        """A project entry that does carry unit data is still a usable listing."""
        from source_providers import _is_project_profile
        priced = {
            "url": "https://www.realestate.com.au/project/vsq-1-zetland-600048516",
            "price_from": 990_000, "beds": 2, "baths": 1, "cars": 1,
        }
        self.assertFalse(_is_project_profile(priced))


class TestClientSideFilters(unittest.TestCase):
    def test_beds_max_enforced(self):
        three_bed = rea.normalize_listing_model(
            _listing(generalFeatures={"bedrooms": {"value": 3}, "bathrooms": {"value": 1}}), mode="sale")
        self.assertFalse(_passes_filters(three_bed, {"beds_max": 2}))
        self.assertTrue(_passes_filters(three_bed, {"beds_max": 3}))

    def test_cars_min_enforced(self):
        no_car = rea.normalize_listing_model(
            _listing(generalFeatures={"bedrooms": {"value": 2}, "bathrooms": {"value": 1}, "parkingSpaces": {"value": 0}}),
            mode="sale")
        self.assertFalse(_passes_filters(no_car, {"cars_min": 1}))

    def test_missing_value_passes(self):
        no_baths = rea.normalize_listing_model(
            _listing(generalFeatures={"bedrooms": {"value": 2}}), mode="sale")
        self.assertTrue(_passes_filters(no_baths, {"baths_min": 1}))


class _StubProvider:
    def __init__(self, name, listings, blocked=False):
        self.name = name
        self._listings = listings
        self._blocked = blocked

    def search(self, filters, *, headed, limit=None):
        return ListingSearchResult(
            provider=self.name, source_url=f"https://{self.name}/x",
            total_results=len(self._listings), page_count=len(self._listings),
            listings=self._listings, blocked_markers=["blocked"] if self._blocked else [],
        )

    def listing(self, listing_id, *, headed):
        return None


class TestFallbackChain(unittest.TestCase):
    def test_default_chain_order(self):
        chain = build_provider_chain()
        # The HTML scraper is deliberately absent: it 403s on every route that
        # matters, so keeping it in only bought a guaranteed-failing hop.
        self.assertEqual([p.name for p in chain], ["domain_graphql", "realestate"])

    def test_falls_back_to_rea_when_domain_blocked(self):
        rea_listing = rea.normalize_listing_model(_listing(), mode="sale")
        rea_listing["mode"] = "sale"
        chain = [_StubProvider("domain", [], blocked=True),
                 _StubProvider("realestate", [rea_listing])]
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "p.db") as db:
                result = run_hunt(
                    {"name": "buy-zetland", "filters": {"mode": "sale"}},
                    headed=True, mark=True, db=db, front=FRONT, providers=chain,
                )
        self.assertEqual(result["provider"], "realestate")
        self.assertEqual(result["new_count"], 1)
        self.assertFalse(result["blocked"])

    def test_reports_block_when_all_providers_blocked(self):
        chain = [_StubProvider("domain", [], blocked=True),
                 _StubProvider("realestate", [], blocked=True)]
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "p.db") as db:
                result = run_hunt(
                    {"name": "buy-zetland", "filters": {"mode": "sale"}},
                    headed=True, mark=True, db=db, front=FRONT, providers=chain,
                )
        self.assertTrue(result["blocked"])
        self.assertEqual(result["new_count"], 0)


if __name__ == "__main__":
    unittest.main()
