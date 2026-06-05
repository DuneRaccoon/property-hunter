import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from action_plan import build_action_plan
from agent_report import build_agent_report
from db import PropertyDB
from due_diligence import build_due_diligence
from inspection_plan import build_inspection_plan
from market_sources import assess_freshness, validate_market_sources
from report_ux import format_daily_digest
from report_builder import render_html, sample_payload
from risk import detect_risks
from valuation import _fairness, _rental_comps, _sold_comps, value_listing
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


if __name__ == "__main__":
    unittest.main()
