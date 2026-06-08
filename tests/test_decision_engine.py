import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from action_plan import build_action_plan
from agent_report import build_agent_report
from db import PropertyDB
from due_diligence import build_due_diligence
from inspection_plan import build_inspection_plan
from hunt_runner import run_hunt
from market_sources import assess_freshness, validate_market_sources
from report_ux import format_daily_digest
from report_builder import render_html, sample_payload
from source_providers import ListingSearchResult
from risk import detect_risks
from valuation import _fairness, _rental_comps, _sold_comps, value_listing
from valuation_engine import build_valuation_case
from value_providers import CachedAvmProvider, PropertyValueProvider, ValueEstimate, ValueProvider
from viability import score_listing

from fixtures import (
    attractive_but_risky_apartment,
    listing,
    missing_data_property,
    overpriced_property,
    strong_renter_candidate,
    underquoted_property,
)


FRONT = {
    "objective": "buy",
    "buyer_type": "owner_occupier",
    "beds_min": 1,
    "baths_min": 1,
    "cars_min": 1,
    "budget": {"buy": {"max": 1_100_000}},
}


class DecisionEngineTests(unittest.TestCase):
    def test_due_diligence_marks_apartment_contract_and_strata(self):
        result = build_due_diligence(listing(), FRONT)
        cats = {item["category"] for item in result["items"]}
        self.assertIn("legal", cats)
        self.assertIn("strata", cats)
        self.assertEqual(result["critical_count"], 2)

    def test_risk_dealbreaker_for_missing_required_parking(self):
        result = detect_risks(attractive_but_risky_apartment(), FRONT)
        self.assertTrue(result["has_dealbreaker"])
        self.assertEqual(result["top"][0]["label"], "Parking shortfall")

    def test_viability_hard_rejects_dealbreaker_risk(self):
        bad = listing(cars=0, description="Ground floor apartment on a busy main road with no parking.")
        result = score_listing(bad, FRONT)
        self.assertLessEqual(result["score"], 2.0)
        self.assertTrue(result["risks"]["has_dealbreaker"])

    def test_action_plan_passes_on_dealbreaker(self):
        bad = listing(cars=0, description="No parking.")
        result = build_action_plan(bad, FRONT)
        self.assertEqual(result["best_next_action"]["type"], "pass")

    def test_risk_flags_no_lift_walkup_apartment(self):
        result = detect_risks(listing(description="Charming top-floor walk-up, no lift, with leafy outlook."), FRONT)
        labels = {r["label"] for r in result["items"]}
        self.assertIn("No-lift building", labels)

    def test_risk_flags_easement_on_house(self):
        house = listing(property_type="House", description="Family home with a shared driveway and an easement at the rear.")
        result = detect_risks(house, FRONT)
        labels = {r["label"] for r in result["items"]}
        self.assertIn("Easement/access issue", labels)

    def test_risk_flags_high_density_suburb(self):
        result = detect_risks(listing(), FRONT)  # base fixture is a Zetland apartment
        labels = {r["label"] for r in result["items"]}
        self.assertIn("High-density suburb", labels)

    def test_risk_flags_renter_parking_storage(self):
        renter_front = {**FRONT, "objective": "rent"}
        renter = listing(mode="rent", cars=0, description="Available now. Air conditioning. Street parking only, no storage.")
        result = detect_risks(renter, renter_front)
        labels = {r["label"] for r in result["items"]}
        self.assertIn("Parking/storage shortfall", labels)

    def test_valuation_flags_underquoting(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                for idx, price in enumerate((1_000_000, 1_020_000, 1_050_000, 1_080_000), start=1):
                    db.upsert_listing(listing(id=f"comp-{idx}", price_from=price, price_to=price, sold={"price": price, "soldDate": f"2026-05-0{idx}"}), mode="sold")
                result = value_listing(listing(id="target", price_from=820_000, price_to=820_000), db)
        self.assertIsNotNone(result["underquoting_risk"])
        self.assertGreaterEqual(result["underquoting_risk"]["gap_pct"], 12.0)

    def test_valuation_no_underquoting_when_fairly_priced(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                for idx, price in enumerate((1_000_000, 1_020_000, 1_050_000, 1_080_000), start=1):
                    db.upsert_listing(listing(id=f"comp-{idx}", price_from=price, price_to=price, sold={"price": price, "soldDate": f"2026-05-0{idx}"}), mode="sold")
                result = value_listing(listing(id="target", price_from=1_030_000, price_to=1_030_000), db)
        self.assertIsNone(result["underquoting_risk"])

    def test_valuation_fairness_labels(self):
        values = [900_000, 950_000, 1_000_000]
        self.assertEqual(_fairness(920_000, 950_000, values), "good value")
        self.assertEqual(_fairness(980_000, 950_000, values), "fair")
        self.assertEqual(_fairness(1_100_000, 950_000, values), "overpriced")
        self.assertEqual(_fairness(underquoted_property()["price_from"], 950_000, values), "cheap or underquoted")
        self.assertEqual(_fairness(overpriced_property()["price_from"], 950_000, values), "overpriced")

    def test_comparable_selection_prefers_same_suburb_type_and_nearby_bed_count(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                db.upsert_listing(listing(id="exact", price_from=920_000, price_to=920_000, beds=2, property_type="Apartment", sold={"price": 920_000, "soldDate": "2026-06-01"}), mode="sold")
                db.upsert_listing(listing(id="adjacent-bed", price_from=880_000, price_to=880_000, beds=1, property_type="Apartment", sold={"price": 880_000, "soldDate": "2026-05-01"}), mode="sold")
                db.upsert_listing(listing(id="house", price_from=1_400_000, price_to=1_400_000, property_type="House", sold={"price": 1_400_000, "soldDate": "2026-06-02"}), mode="sold")
                db.upsert_listing(listing(id="other-suburb", address={"display": "3 Other Street, Randwick NSW 2031", "suburb": "Randwick"}, sold={"price": 900_000, "soldDate": "2026-06-03"}), mode="sold")
                comps = _sold_comps(db, listing(id="target"))

        self.assertEqual([c["id"] for c in comps], ["exact", "adjacent-bed"])

    def test_comparable_selection_uses_nearby_suburbs_when_local_evidence_is_thin(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                db.upsert_listing(listing(id="nearby", address={"display": "2 Test Street, Waterloo NSW 2017", "suburb": "Waterloo"}, price_from=930_000, price_to=930_000, sold={"price": 930_000, "soldDate": "2026-06-01"}), mode="sold")
                db.upsert_listing(listing(id="far-away", address={"display": "3 Test Street, Manly NSW 2095", "suburb": "Manly"}, price_from=940_000, price_to=940_000, sold={"price": 940_000, "soldDate": "2026-06-02"}), mode="sold")
                result = value_listing(listing(id="target", price_from=950_000, price_to=950_000), db)

        self.assertEqual(result["comparable_count"], 1)
        self.assertEqual(result["comps"][0]["id"], "nearby")
        self.assertEqual(result["price_per_bed"], 475_000)

    def test_comparable_matching_tolerates_enum_vs_display_property_type(self):
        # Sold rows are stored with the Domain display label while an enriched
        # subject carries the enum form. Both must match or the whole valuation
        # silently returns zero comps (regression: real DB produced no signals).
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                for idx, price in enumerate((1_000_000, 1_030_000, 1_060_000), start=1):
                    db.upsert_listing(
                        listing(
                            id=f"disp-{idx}",
                            property_type="Apartment / Unit / Flat",
                            price_from=price,
                            price_to=price,
                            sold={"price": price, "soldDate": f"2026-05-0{idx}"},
                        ),
                        mode="sold",
                    )
                target = listing(id="target", property_type=None, property_types=["APARTMENT_UNIT_FLAT"])
                comps = _sold_comps(db, target)

        self.assertEqual(len(comps), 3)

    def test_value_listing_uses_comps_without_domain(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                for idx, price in enumerate((900_000, 950_000, 1_000_000), start=1):
                    db.upsert_listing(listing(id=f"comp-{idx}", price_from=price, price_to=price, sold={"price": price, "soldDate": f"2026-05-0{idx}"}), mode="sold")
                result = value_listing(listing(id="target", price_from=960_000, price_to=960_000), db)

        self.assertEqual(result["comparable_count"], 3)
        self.assertEqual(result["fairness"], "fair")

    def test_renter_valuation_uses_rental_comps_and_budget_metrics(self):
        renter_front = {**FRONT, "objective": "rent", "budget": {"rent": {"max": 900}}}
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                for idx, rent in enumerate((820, 850, 880, 900), start=1):
                    db.upsert_listing(strong_renter_candidate() | {"id": f"rent-{idx}", "price": f"${rent} per week", "price_from": rent, "price_to": rent}, mode="rent")
                comps = _rental_comps(db, strong_renter_candidate())
                result = value_listing(strong_renter_candidate(), db, front=renter_front)

        self.assertEqual(len(comps), 4)
        self.assertEqual(result["mode"], "rent")
        self.assertEqual(result["fairness"], "fair")
        self.assertEqual(result["weekly_affordability"], "within budget")
        self.assertEqual(result["annual_rent_burden"], 44_200)
        self.assertEqual(result["rent_per_bed"], 425)

    def test_inspection_plan_sorts_flags_clashes_and_unscheduled(self):
        first = listing(id="a", fit_score=8.5)
        second = listing(
            id="b",
            fit_score=7.4,
            address={"display": "2 Test Street, Randwick NSW 2031", "suburb": "Randwick"},
            inspections=[{"start": "2026-06-06T10:15:00", "end": "2026-06-06T10:45:00"}],
        )
        no_time = listing(id="c", inspections=[])
        result = build_inspection_plan([second, no_time, first])
        self.assertEqual(result["target_date"], "2026-06-06")
        self.assertEqual([stop["listing_id"] for stop in result["stops"]], ["a", "b"])
        self.assertFalse(result["stops"][0]["clash"])
        self.assertTrue(result["stops"][1]["clash"])
        self.assertEqual(result["unscheduled"][0]["listing_id"], "c")

    def test_db_records_lifecycle_events_and_why_now(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                db.upsert_listing(listing(id="life1", price_from=1_000_000, price_to=1_100_000, status="active"), mode="sale")
                since = db.previous_run_at("buy-test")
                db.upsert_hunt("buy-test", {"mode": "sale"})
                db.record_run("buy-test", "https://example.test", total_results=1, page_count=1, new_ids=["life1"], all_ids=["life1"], blocked=False)
                since = db.previous_run_at("buy-test")
                db.upsert_listing(listing(id="life1", price_from=950_000, price_to=1_000_000, status="active"), mode="sale")
                changes = db.listing_changes("life1", since=since)
                summary = db.lifecycle_summary("life1", since=since)

        self.assertEqual(changes[0]["event_type"], "price_drop")
        self.assertTrue(summary["changed"])
        self.assertIn("price guide dropped", summary["why_now"])

    def test_db_records_inspection_changes_and_stale_missing_events(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                db.upsert_listing(listing(id="inspect1", inspections=[{"start": "2026-06-06T10:00:00", "end": "2026-06-06T10:30:00"}]), mode="sale")
                db.upsert_listing(listing(id="inspect1", inspections=[{"start": "2026-06-06T11:00:00", "end": "2026-06-06T11:30:00"}]), mode="sale")
                db.mark_listings_stale(["inspect1"])
                changes = db.listing_changes("inspect1")

        event_types = {event["event_type"] for event in changes}
        self.assertIn("inspection_change", event_types)
        self.assertIn("withdrawn_or_stale", event_types)

    def test_db_detects_relist_after_stale(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                db.upsert_listing(listing(id="relist1", status="active"), mode="sale")
                db.mark_listings_stale(["relist1"])
                db.upsert_listing(listing(id="relist1", status="active"), mode="sale")
                changes = db.listing_changes("relist1")
                summary = db.lifecycle_summary("relist1")

        event_types = [e["event_type"] for e in changes]
        self.assertIn("relisted", event_types)
        # A steady-state rerun must not record a second relist.
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                db.upsert_listing(listing(id="relist2", status="active"), mode="sale")
                db.mark_listings_stale(["relist2"])
                db.upsert_listing(listing(id="relist2", status="active"), mode="sale")
                db.upsert_listing(listing(id="relist2", status="active"), mode="sale")
                relists = [e for e in db.listing_changes("relist2", limit=20) if e["event_type"] == "relisted"]
        self.assertEqual(len(relists), 1)
        self.assertIn("Relisted", summary["why_now"])

    def test_supply_trend_tracks_total_results_over_runs(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                db.upsert_hunt("buy-apts", {"mode": "sale"})
                db.record_run("buy-apts", "https://example.test", total_results=120, page_count=6, new_ids=[], all_ids=[], blocked=False)
                db.record_run("buy-apts", "https://example.test", total_results=95, page_count=5, new_ids=[], all_ids=[], blocked=False)
                trend = db.supply_trend("buy-apts")

        self.assertEqual(trend["current"], 95)
        self.assertEqual(trend["previous"], 120)
        self.assertEqual(trend["delta"], -25)
        self.assertEqual(trend["direction"], "falling")
        self.assertEqual(trend["n_runs"], 2)

    def test_blocked_hunt_does_not_mark_seen_listings_stale(self):
        class BlockedProvider:
            name = "domain"

            def search(self, filters, *, headed, limit=None):
                return ListingSearchResult(
                    provider="domain",
                    source_url="https://www.domain.com.au/sale/",
                    total_results=None,
                    page_count=0,
                    listings=[],
                    blocked_markers=["blocked"],
                )

            def listing(self, listing_id, *, headed):
                return None

        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                db.upsert_hunt("buy-apts", {"mode": "sale"})
                db.upsert_listing(listing(id="existing"), mode="sale")
                db.record_run("buy-apts", "https://example.test", total_results=1, page_count=1, new_ids=["existing"], all_ids=["existing"], blocked=False)
                result = run_hunt(
                    {"name": "buy-apts", "filters": {"mode": "sale"}},
                    headed=True,
                    mark=True,
                    db=db,
                    front=FRONT,
                    provider=BlockedProvider(),
                )
                changes = db.listing_changes("existing")

        self.assertEqual(result["blocked"], ["blocked"])
        self.assertEqual(result["stale_count"], 0)
        self.assertNotIn("withdrawn_or_stale", {event["event_type"] for event in changes})

    def test_agent_report_tracks_observed_performance_fields(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                agent_listing = listing(
                    id="agent1",
                    price_from=900_000,
                    price_to=1_000_000,
                    agents=[{"name": "Agent One", "mobile": "0400000000", "agency": "Test Realty"}],
                )
                db.upsert_listing(agent_listing, mode="sale")
                db.upsert_listing({**agent_listing, "price_from": 850_000, "price_to": 900_000}, mode="sale")
                db.upsert_listing({**agent_listing, "sold": {"price": 1_100_000, "soldDate": "2026-06-01"}}, mode="sold")
                report = build_agent_report(db)

        self.assertEqual(report["agent_count"], 1)
        agent = report["agents"][0]
        self.assertEqual(agent["listings_seen"], 1)
        self.assertEqual(agent["listings_sold"], 1)
        self.assertEqual(agent["price_drops_observed"], 1)
        self.assertEqual(agent["underquote_signals"], 1)

    def test_market_sources_flag_stale_and_missing_evidence(self):
        now = datetime(2026, 6, 5, tzinfo=timezone.utc)
        fresh = {
            "key": "rba",
            "label": "Cash rate",
            "value": "4.35%",
            "source_name": "RBA",
            "source_url": "https://example.test/rba",
            "observed_at": "2026-06-01T00:00:00+00:00",
            "freshness_days": 21,
        }
        stale = {**fresh, "key": "forecast", "observed_at": "2026-01-01T00:00:00+00:00"}
        self.assertEqual(assess_freshness(fresh, now=now), "fresh")
        self.assertEqual(assess_freshness(stale, now=now), "stale")

        result = validate_market_sources({"sources": [fresh, stale]}, now=now)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("stale", result["warnings"][0])

    def test_report_renders_market_source_warning_when_evidence_missing(self):
        html = render_html({
            "meta": {"title": "Test", "date": "2026-06-05"},
            "brief": {},
            "market": {"standfirst": "Uncited market claim."},
            "properties": [],
        })
        self.assertIn("No dated market sources supplied", html)
        self.assertIn("source-status-missing", html)

    def test_report_renders_executive_recommendation_and_uncertainty(self):
        payload = {
            "meta": {"title": "Test", "date": "2026-06-05"},
            "brief": {},
            "market": {"standfirst": "Market."},
            "properties": [{
                "address": "1 Test Street, Zetland NSW 2017",
                "price": "$950,000",
                "fit_score": 8.0,
                "verdict": "Best fit.",
                "viability": {"score": 8.8, "band": "Strong fit", "components": {}},
                "valuation": {"fairness": "fair", "confidence": "low", "comparable_count": 2},
                "risks": {"summary": "No major risk", "top": []},
                "action_plan": {"best_next_action": {"label": "Inspect", "deadline": "Saturday", "detail": "Go first."}},
            }],
        }
        html = render_html(payload)
        self.assertIn("What I Would Do", html)
        self.assertIn("Why this might be wrong", html)
        self.assertIn("Comparable evidence is thin", html)

    def test_folio_sample_payload_smoke_renders_core_sections(self):
        payload = sample_payload()
        html = render_html(payload)
        self.assertIn("The Inner-South Dossier", html)
        self.assertIn("What I Would Do", html)
        self.assertIn("Financials &amp; yield", html)
        self.assertIn("Gallery &amp; floorplan", html)

    def test_digest_puts_standout_changed_and_stale_sections_first(self):
        report = {
            "generated_at": "2026-06-05T00:00:00+00:00",
            "hunts": [{
                "name": "buy",
                "new_count": 2,
                "changed_count": 1,
                "stale_count": 1,
                "new": [
                    {"address": "Weak", "price": "$1.1M", "url": "https://weak", "decision": {"viability_score": 6.0}},
                    {"address": "Strong", "price": "$950K", "url": "https://strong", "decision": {"viability_score": 9.0, "price_fairness": "fair", "top_risk": "No major risk", "next_action": "Inspect"}},
                ],
                "changed": [{"address": "Changed", "url": "https://changed", "lifecycle": {"why_now": "price guide dropped"}}],
                "stale": [{"address": "Missing", "url": "https://missing"}],
            }],
        }
        digest = format_daily_digest(report)
        self.assertLess(digest.index("Standout pick"), digest.index("Changed / newly interesting"))
        self.assertLess(digest.index("Strong"), digest.index("Weak"))
        self.assertIn("Stale / missing from latest run", digest)

    def test_db_persists_external_fact_attribution(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "property.sqlite3"
            with PropertyDB(db_path) as db:
                db.upsert_external_fact({
                    "key": "rba_cash_rate",
                    "label": "RBA cash rate target",
                    "value": "4.35%",
                    "source_name": "Reserve Bank of Australia",
                    "source_url": "https://www.rba.gov.au/statistics/cash-rate/",
                    "observed_at": "2026-06-03T00:00:00+00:00",
                    "freshness_days": 21,
                    "status": "fresh",
                })
                facts = db.list_external_facts()

        self.assertEqual(facts[0]["key"], "rba_cash_rate")
        self.assertEqual(facts[0]["source_name"], "Reserve Bank of Australia")

    def test_sold_card_price_persists_to_sold_price_column(self):
        # Sold-search cards carry the price only as a display string and no `sold`
        # block. The column must still be populated (and "Price Withheld" left NULL).
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "property.sqlite3") as db:
                priced = listing(id="priced")
                priced["price"] = "$1,050,000"
                priced.pop("sold", None)
                db.upsert_listing(priced, mode="sold")
                withheld = listing(id="withheld")
                withheld["price"] = "Price Withheld"
                withheld.pop("sold", None)
                db.upsert_listing(withheld, mode="sold")
                rows = {
                    r["id"]: r["sold_price"]
                    for r in db.conn.execute("SELECT id, sold_price FROM listings WHERE mode='sold'")
                }
        self.assertEqual(rows["priced"], 1_050_000)
        self.assertIsNone(rows["withheld"])

    def test_buyer_and_renter_modes_produce_separate_advice(self):
        renter_front = {**FRONT, "objective": "rent", "budget": {"rent": {"max": 900}}}
        buyer_due = build_due_diligence(listing(), FRONT)
        renter_due = build_due_diligence(strong_renter_candidate(), renter_front)
        buyer_actions = build_action_plan(listing(), FRONT)
        renter_actions = build_action_plan(strong_renter_candidate(), renter_front)

        self.assertIn("strata", {item["category"] for item in buyer_due["items"]})
        self.assertNotIn("strata", {item["category"] for item in renter_due["items"]})
        self.assertIn("application", {item["category"] for item in renter_due["items"]})
        self.assertIn("prepare_application", {action["type"] for action in renter_actions["actions"]})
        self.assertNotIn("request_contract", {action["type"] for action in renter_actions["actions"]})
        self.assertIn("conveyancer", " ".join(buyer_actions["documents_to_prepare"]))
        self.assertIn("rental references", " ".join(renter_actions["documents_to_prepare"]))

    def test_missing_data_fixture_does_not_crash_report_logic(self):
        sparse = missing_data_property()
        due = build_due_diligence(sparse, FRONT)
        risks = detect_risks(sparse, FRONT)
        actions = build_action_plan(sparse, FRONT, due_diligence=due, risks=risks)

        self.assertGreater(due["unknown_count"], 0)
        self.assertIn("No inspection time found", {item["label"] for item in risks["items"]})
        self.assertEqual(actions["best_next_action"]["type"], "contact_agent")

    def test_db_migrates_legacy_agent_table_non_destructively(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.sqlite3"
            import sqlite3
            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE agents (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, agency TEXT, UNIQUE(name, agency))")
            con.execute("INSERT INTO agents (name, agency) VALUES (?, ?)", ("Legacy Agent", "Old Realty"))
            con.commit()
            con.close()

            with PropertyDB(db_path) as db:
                cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(agents)").fetchall()}
                row = db.conn.execute("SELECT name, agency FROM agents").fetchone()

        self.assertIn("underquote_signals", cols)
        self.assertIn("metrics_updated_at", cols)
        self.assertEqual(dict(row), {"name": "Legacy Agent", "agency": "Old Realty"})


class _StubAvmProvider(ValueProvider):
    name = "propertyvalue"

    def __init__(self, point, low, high, confidence="medium"):
        self._est = ValueEstimate(
            provider="propertyvalue",
            method="avm",
            point=point,
            low=low,
            high=high,
            confidence=confidence,
            source="propertyvalue.com.au",
            note="CoreLogic AVM (stub).",
        )

    def available(self):
        return True

    def estimate(self, listing, *, db=None, front=None):
        return self._est


class ValuationEngineTests(unittest.TestCase):
    def _seed_comps(self, db, prices=(1_000_000, 1_020_000, 1_050_000, 1_080_000)):
        for idx, price in enumerate(prices, start=1):
            db.upsert_listing(
                listing(id=f"comp-{idx}", price_from=price, price_to=price, beds=2,
                        sold={"price": price, "soldDate": f"2026-05-0{idx}"}),
                mode="sold",
            )

    def test_independent_estimate_without_asking_price(self):
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "p.sqlite3") as db:
                self._seed_comps(db)
                subject = listing(id="target")
                subject.pop("price_from", None)
                subject.pop("price_to", None)
                subject["price"] = None
                case = build_valuation_case(subject, db, providers=[])
        self.assertIsNotNone(case["independent_estimate"])
        self.assertGreater(case["independent_estimate"]["point"], 0)
        self.assertIsNone(case["asking_comparison"])
        self.assertTrue(any("independent estimate" in line.lower() for line in case["case"]))

    def test_positions_asking_below_independent_range(self):
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "p.sqlite3") as db:
                self._seed_comps(db)
                case = build_valuation_case(
                    listing(id="target", price_from=820_000, price_to=820_000), db, providers=[]
                )
        comp = case["asking_comparison"]
        self.assertEqual(comp["position"], "below independent range")
        self.assertGreater(comp["negotiation_headroom"], 0)

    def test_multiple_signals_are_reconciled(self):
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "p.sqlite3") as db:
                self._seed_comps(db)
                case = build_valuation_case(
                    listing(id="target", price_from=1_030_000, price_to=1_030_000), db, providers=[]
                )
        methods = {s["method"] for s in case["signals"]}
        self.assertIn("comparable_median", methods)
        self.assertIn("per_bed", methods)
        self.assertGreaterEqual(case["independent_estimate"]["signal_count"], 2)

    def test_external_avm_provider_contributes_a_signal(self):
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "p.sqlite3") as db:
                self._seed_comps(db)
                provider = _StubAvmProvider(point=1_100_000, low=1_060_000, high=1_140_000)
                case = build_valuation_case(
                    listing(id="target", price_from=1_030_000, price_to=1_030_000),
                    db, providers=[provider],
                )
        avm = [s for s in case["signals"] if s["provider"] == "propertyvalue"]
        self.assertEqual(len(avm), 1)
        self.assertEqual(avm[0]["method"], "avm")
        # An authoritative high AVM should drag the blended estimate above the bare comp median.
        self.assertGreater(case["independent_estimate"]["point"], 1_037_000)

    def test_cached_avm_provider_round_trips(self):
        with TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "avm_cache.json"
            writer = CachedAvmProvider(cache_path)
            writer.put("target", {"point": 990_000, "low": 950_000, "high": 1_030_000, "confidence": "medium"})
            reader = CachedAvmProvider(cache_path)
            est = reader.estimate(listing(id="target"))
        self.assertIsNotNone(est)
        self.assertEqual(est.point, 990_000)
        self.assertEqual(est.confidence, "medium")

    def test_live_provider_disabled_by_default_returns_none(self):
        provider = PropertyValueProvider(enabled=False, cache=CachedAvmProvider(Path("/nonexistent/avm.json")))
        self.assertIsNone(provider.estimate(listing(id="target")))

    def test_comparable_median_anchors_to_same_bedroom_cluster(self):
        # A 2-bed subject must not have its comparable median dragged down by
        # cheaper 1-bed sales that also match suburb/type. The strongest signal
        # should reflect the 2-bed cluster when a usable cluster exists.
        with TemporaryDirectory() as tmp:
            with PropertyDB(Path(tmp) / "p.sqlite3") as db:
                for idx, price in enumerate((980_000, 1_040_000), start=1):
                    db.upsert_listing(
                        listing(id=f"two-{idx}", beds=2, price_from=price, price_to=price,
                                sold={"price": price, "soldDate": f"2026-05-0{idx}"}),
                        mode="sold",
                    )
                for idx, price in enumerate((600_000, 640_000), start=1):
                    db.upsert_listing(
                        listing(id=f"one-{idx}", beds=1, price_from=price, price_to=price,
                                sold={"price": price, "soldDate": f"2026-05-1{idx}"}),
                        mode="sold",
                    )
                case = build_valuation_case(listing(id="target", beds=2), db, providers=[])
        comp = next(s for s in case["signals"] if s["method"] == "comparable_median")
        self.assertEqual(comp["point"], 1_010_000)
        self.assertIn("same-bedroom", comp["note"])


if __name__ == "__main__":
    unittest.main()
