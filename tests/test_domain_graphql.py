"""Tests for the Domain GraphQL transport and its normalizers.

Everything here is offline. The fixtures are trimmed copies of real Aug-18 2026
API responses (listing 2021067641 / 2021077200), so a schema drift that changes
a field name shows up here rather than as a silent blank column in the folio.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import domain_graphql as dg
from db import PropertyDB


SEARCH_NODE = {
    "id": "2021067641",
    "listingId": "2021067641",
    "listingType": "RESIDENTIAL",
    "displayPrice": "For Sale: $900,000 - $950,000",
    "bedrooms": 1, "bathrooms": 1, "carspaces": 1,
    "propertyType": "ApartmentUnitFlat",
    "features": ["AirConditioning", "SecureParking"],
    "tags": [],
    "dateListed": "2026-08-12T04:24:00",
    "headline": "Level 7 with district outlook",
    "summaryDescription": "Study nook, storage cage.",
    "priceDetails": {"displayPrice": "For Sale: $900,000 - $950,000", "canDisplayPrice": True},
    "agency": {"name": "Hudson McHugh", "agencyId": 25178},
    "displayableAddress": {
        "displayAddress": "705/178 Livingstone Road, Marrickville",
        "unitNumber": "705", "streetNumber": "178", "street": "Livingstone Road",
        "state": "NSW", "postcode": "2204", "displayType": "FULL_ADDRESS",
        "suburb": {"name": "Marrickville"},
        "geolocation": {"latitude": -33.9078053, "longitude": 151.1531014},
    },
    "media": [
        {"url": "https://rimh2.domainstatic.com.au/a/2021067641_1-w2048", "rawUrl": "https://b/1", "type": "photo"},
        {"url": "https://rimh2.domainstatic.com.au/a/2021067641_f-w2048", "rawUrl": "https://b/f", "type": "floorplan"},
        {},  # a non-Image union member (video / 3D tour) comes through empty
    ],
    "auctionDetails": None,
    "soldData": None,
    "inspectionDetails": {"inspections": [
        {"openingDateTime": {"isoDate": "2026-08-19T13:15:00", "time": "1:15pm"},
         "closingDateTime": {"isoDate": "2026-08-19T13:45:00", "time": "1:45pm"}},
    ]},
}

DETAIL_NODE = {
    "id": "2021077200", "listingId": "2021077200", "listingType": "RESIDENTIAL",
    "status": "NEW", "headline": "Sophisticated Inner West sanctuary",
    "description": "Full listing copy.",
    "bedrooms": 2, "bathrooms": 2, "carspaces": 1,
    "features": [], "propertyTypes": ["APARTMENT_UNIT_FLAT"],
    "dateListed": "2026-08-17T13:30:13", "landAreaSqm": None,
    "seoUrl": "https://www.domain.com.au/203-250-wardell-road-marrickville-nsw-2204-2021077200",
    "priceDetails": {"displayPrice": "For Sale $950,000", "canDisplayPrice": True},
    "structuredFeatures": [{"name": "Air conditioning", "category": "Indoor"}],
    "agency": {"id": "QWdlbmN5OjMzNzU5", "name": "Adrian William"},
    "agents": [{
        "agentId": "1779519", "fullName": "Norman Tran", "firstName": "Norman",
        "lastName": "Tran", "email": "norman@adrianwilliam.com.au",
        "profileUrl": "https://www.domain.com.au/real-estate-agent/norman-tran-1779519",
        "jobTitle": "Associate Director", "agencyId": 33759,
        "photo": {"url": "https://rimh2.domainstatic.com.au/contact_1779519.jpeg"},
    }],
    "media": [{"url": "https://rimh2.domainstatic.com.au/a/1-w3846", "rawUrl": "https://b/1", "type": "photo"}],
    "auctionDetails": {"auctionSchedule": {"openingDateTime": {"isoDate": "2026-09-06T10:30:00", "time": "10:30am"}}},
    "soldDetails": None,
    "displayableAddress": {
        "displayAddress": "203/250 Wardell Road, Marrickville",
        "unitNumber": "203", "streetNumber": "250", "street": "Wardell Road",
        "state": "NSW", "postcode": "2204", "displayType": "FULL_ADDRESS",
        "suburb": {"name": "Marrickville"},
        "geolocation": {"latitude": -33.9118546, "longitude": 151.14087},
    },
    "inspectionDetails": {"inspections": []},
}

SOLD_NODE = {
    "id": "2021013442", "displayPrice": "$935,000", "tags": [],
    "bedrooms": 2, "bathrooms": 1, "carspaces": 1,
    "soldData": {
        "saleMethod": "SoldByAuction",
        "soldPrice": {"displayPrice": "$935,000", "canDisplayPrice": True},
        "soldDate": {"isoDate": "2026-08-15T00:00:00"},
    },
    "displayableAddress": {"displayAddress": "13/25a George Street, Marrickville",
                           "state": "NSW", "postcode": "2204", "suburb": {"name": "Marrickville"}},
}


class TestSearchParams(unittest.TestCase):
    def test_core_filters_translate(self):
        p = dg.build_search_params({
            "suburbs": ["Zetland NSW 2017"], "mode": "sale", "ptypes": ["apartment"],
            "price_max": 1100000, "beds_min": 1, "beds_max": 2, "baths_min": 1,
            "cars_min": 1, "sort": "dateupdated-desc",
        })
        self.assertEqual(p["listingType"], "Sale")
        self.assertEqual(p["locations"], [{"suburb": "Zetland", "state": "NSW", "postcode": "2017",
                                          "includeSurroundingSuburbs": True}])
        self.assertEqual(p["propertyTypes"], ["ApartmentUnitFlat"])
        self.assertEqual((p["minBedrooms"], p["maxBedrooms"], p["maxPrice"]), (1, 2, 1100000))
        self.assertEqual(p["sort"], {"sortKey": "DateUpdated", "direction": "Descending"})

    def test_features_map_to_enum(self):
        p = dg.build_search_params({"suburbs": ["Zetland NSW 2017"],
                                    "features": ["airconditioning", "pets-allowed"]})
        self.assertEqual(p["propertyFeatures"], ["AirConditioning", "PetsAllowed"])
        self.assertNotIn("_unsupported_features", p)

    def test_unsupported_feature_is_reported_not_dropped(self):
        """A filter the API can't express must be visible, never silently ignored."""
        p = dg.build_search_params({"suburbs": ["Zetland NSW 2017"], "features": ["balcony"]})
        self.assertNotIn("propertyFeatures", p)
        self.assertEqual(p["_unsupported_features"], ["balcony"])

    def test_keywords_and_land_area(self):
        p = dg.build_search_params({"suburbs": ["Zetland NSW 2017"],
                                    "keywords": "north facing", "land_min": 50})
        self.assertEqual(p["keywords"], "north facing")
        self.assertEqual(p["minLandArea"], 50)

    def test_exclude_under_offer_is_never_sent(self):
        """No such enum exists; sending it would fail the whole query."""
        p = dg.build_search_params({"suburbs": ["Zetland NSW 2017"], "exclude_under_offer": True})
        self.assertNotIn("excludeUnderOffer", p)
        self.assertNotIn("listingAttributes", p)


class TestNormalizeSearch(unittest.TestCase):
    def setUp(self):
        self.norm = dg.normalize_result(SEARCH_NODE, mode="sale")

    def test_address_is_populated_from_search_card(self):
        addr = self.norm["address"]
        self.assertEqual(addr["display"], "705/178 Livingstone Road, Marrickville NSW 2204")
        self.assertEqual((addr["suburb"], addr["state"], addr["postcode"]), ("Marrickville", "NSW", "2204"))
        self.assertAlmostEqual(addr["lat"], -33.9078053)

    def test_display_gains_missing_state_and_postcode(self):
        """Search returns 'Street, Suburb' with no tail; a report needs the full line."""
        self.assertTrue(self.norm["address"]["display"].endswith("NSW 2204"))

    def test_images_split_by_type_and_skip_non_images(self):
        imgs = self.norm["images"]
        self.assertEqual(len(imgs), 2)
        self.assertEqual([i["type"] for i in imgs], ["photo", "floorplan"])
        self.assertEqual(imgs[0]["position"], 0)

    def test_price_range_parsed(self):
        self.assertEqual((self.norm["price_from"], self.norm["price_to"]), (900000, 950000))

    def test_agency_is_a_dict_so_the_db_stores_it(self):
        """db.upsert_listing only reads agency when it's a mapping."""
        self.assertEqual(self.norm["agency"], {"name": "Hudson McHugh", "id": 25178})

    def test_inspections_normalized(self):
        self.assertEqual(self.norm["inspections"],
                         [{"start": "2026-08-19T13:15:00", "end": "2026-08-19T13:45:00"}])

    def test_project_card_returns_none(self):
        self.assertIsNone(dg.normalize_result({}, mode="sale"))

    def test_sold_card_carries_status_and_sold_block(self):
        norm = dg.normalize_result(SOLD_NODE, mode="sold")
        self.assertEqual(norm["status"], "sold")
        self.assertEqual(norm["sold"]["soldPrice"], 935000)
        self.assertEqual(norm["sold"]["soldDate"], "2026-08-15")
        self.assertEqual(norm["sold"]["saleMethod"], "SoldByAuction")


class TestNormalizeDetail(unittest.TestCase):
    def setUp(self):
        self.norm = dg._detail_from_node(DETAIL_NODE)

    def test_address_from_displayable_not_property(self):
        self.assertEqual(self.norm["address"]["display"], "203/250 Wardell Road, Marrickville NSW 2204")

    def test_agents_normalized_for_the_db(self):
        agent = self.norm["agents"][0]
        self.assertEqual(agent["name"], "Norman Tran")
        self.assertEqual(agent["email"], "norman@adrianwilliam.com.au")
        self.assertEqual(agent["agency"], "Adrian William")
        self.assertTrue(agent["photo"].endswith(".jpeg"))

    def test_agents_have_no_phone_because_the_api_has_none(self):
        """Guards the folio against promising a contact channel that never arrives."""
        agent = self.norm["agents"][0]
        self.assertIsNone(agent["mobile"])
        self.assertIsNone(agent["landline"])

    def test_structured_features_folded_into_features(self):
        self.assertIn("Air conditioning", self.norm["features"])

    def test_features_deduped_across_the_two_sources(self):
        """`features` and `structuredFeatures` overlap and disagree on case."""
        node = {**DETAIL_NODE, "features": ["Air Conditioning", "Balcony"]}
        feats = dg._detail_from_node(node)["features"]
        self.assertEqual(feats, ["Air Conditioning", "Balcony"])

    def test_auction_datetime_extracted(self):
        self.assertEqual(self.norm["auction_at"], "2026-09-06T10:30:00")


class TestStatus(unittest.TestCase):
    """Status must be stable across a search-only and an enriched observation.

    Otherwise every run records unknown -> live -> unknown and the digest
    reports a "status changed" event that never happened.
    """

    def test_live_search_card_and_enriched_detail_agree(self):
        card = dg.normalize_result(SEARCH_NODE, mode="sale")
        detail = dg._detail_from_node(DETAIL_NODE)
        self.assertEqual(card["status"], "live")
        self.assertEqual(detail["status"], "live")

    def test_domain_lifecycle_enum_mapped(self):
        self.assertEqual(dg.normalize_status("NEW"), "live")
        self.assertEqual(dg.normalize_status("LIVE"), "live")
        self.assertEqual(dg.normalize_status("SOLD"), "sold")
        self.assertEqual(dg.normalize_status("UnderContract"), "off_market")
        self.assertEqual(dg.normalize_status("something-else", default="live"), "live")

    def test_sold_card_beats_the_live_default(self):
        self.assertEqual(dg.normalize_result(SOLD_NODE, mode="sold")["status"], "sold")


class TestSurroundingSuburbs(unittest.TestCase):
    def test_included_by_default(self):
        """Domain's page search includes nearby suburbs; the API must be asked."""
        p = dg.build_search_params({"suburbs": ["Crows Nest NSW 2065"]})
        self.assertTrue(p["locations"][0]["includeSurroundingSuburbs"])

    def test_can_be_turned_off_per_hunt(self):
        p = dg.build_search_params({"suburbs": ["Crows Nest NSW 2065"], "include_surrounding": False})
        self.assertFalse(p["locations"][0]["includeSurroundingSuburbs"])


class TestTruncationSignal(unittest.TestCase):
    """`more_pages`, not a count comparison — see the comment in `search`."""

    def test_project_cards_do_not_imply_truncation(self):
        from unittest import mock
        block = {"totalResults": 181, "totalPages": 1, "page": 1,
                 "results": [SEARCH_NODE, {}, {}]}   # two project cards
        with mock.patch.object(dg, "graphql", return_value={"searchListings": block}):
            out = dg.search({"suburbs": ["Zetland NSW 2017"]}, max_pages=5)
        self.assertEqual(len(out["listings"]), 1)
        self.assertFalse(out["more_pages"])

    def test_hitting_max_pages_is_truncation(self):
        from unittest import mock
        block = {"totalResults": 500, "totalPages": 10, "page": 1, "results": [SEARCH_NODE]}
        with mock.patch.object(dg, "graphql", return_value={"searchListings": block}):
            out = dg.search({"suburbs": ["Zetland NSW 2017"]}, max_pages=2)
        self.assertTrue(out["more_pages"])


class TestOffMarketTags(unittest.TestCase):
    def test_flat_string_tags_detected(self):
        """GraphQL tags are a [String!] of slugs, not the scraper's dict."""
        self.assertEqual(dg.off_market_from_tags(["undercontract"]), "off_market")
        self.assertEqual(dg.off_market_from_tags(["sold"]), "sold")

    def test_live_tags_are_none(self):
        self.assertIsNone(dg.off_market_from_tags(["newdevelopment"]))
        self.assertIsNone(dg.off_market_from_tags([]))
        self.assertIsNone(dg.off_market_from_tags(None))


class TestPersistence(unittest.TestCase):
    """The normalized shapes must actually land in the DB's columns."""

    def test_search_card_alone_fills_address_images_and_agency(self):
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "t.db") as db:
                db.upsert_listing(dg.normalize_result(SEARCH_NODE, mode="sale"), mode="sale")
                row = db.conn.execute(
                    "SELECT address_display, suburb, postcode, agency, lat FROM listings").fetchone()
                self.assertEqual(row["address_display"], "705/178 Livingstone Road, Marrickville NSW 2204")
                self.assertEqual(row["suburb"], "Marrickville")
                self.assertEqual(row["agency"], "Hudson McHugh")
                self.assertIsNotNone(row["lat"])
                self.assertEqual(
                    db.conn.execute("SELECT count(*) c FROM listing_images").fetchone()["c"], 2)

    def test_detail_fills_agents(self):
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "t.db") as db:
                db.upsert_listing(dg._detail_from_node(DETAIL_NODE), mode="sale")
                row = db.conn.execute("SELECT name, email, agency FROM agents").fetchone()
                self.assertEqual((row["name"], row["agency"]), ("Norman Tran", "Adrian William"))

    def test_sold_card_persists_sale_figures(self):
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "t.db") as db:
                db.upsert_listing(dg.normalize_result(SOLD_NODE, mode="sold"), mode="sold")
                row = db.conn.execute(
                    "SELECT sold_price, sold_date, sale_method, status FROM listings").fetchone()
                self.assertEqual(row["sold_price"], 935000)
                self.assertEqual(row["sold_date"], "2026-08-15")
                self.assertEqual(row["status"], "sold")


if __name__ == "__main__":
    unittest.main()
