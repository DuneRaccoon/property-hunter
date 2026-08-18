"""Tests for hunt bookkeeping: reporting caps, truncation, cross-hunt staleness.

These guard three behaviours that each produced a stream of phantom events in
the digest before they were fixed. All offline — providers are stubs.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from buyer_profile import DEFAULT_BUYER, parse_buyer_md
from db import PropertyDB
from hunt_runner import resolve_stale, run_hunt
from source_providers import ListingSearchResult

FRONT, _ = parse_buyer_md(DEFAULT_BUYER)


def _listing(lid, suburb="Zetland"):
    return {
        "id": str(lid), "url": f"https://www.domain.com.au/{lid}",
        "price": "$900,000", "price_from": 900000, "price_to": 900000,
        "beds": 1, "baths": 1, "cars": 1, "status": "live",
        "address": {"display": f"{lid} Test St, {suburb} NSW 2017", "suburb": suburb},
        "images": [], "agents": [], "inspections": [],
    }


class StubProvider:
    name = "stub"

    def __init__(self, listings, *, total=None, truncated=False):
        self._listings, self._total, self._truncated = listings, total, truncated

    def search(self, filters, *, headed, limit=None):
        return ListingSearchResult(
            provider=self.name, source_url="stub://", total_results=self._total,
            page_count=1, listings=self._listings, blocked_markers=[],
            truncated=self._truncated,
        )

    def listing(self, listing_id, *, headed):
        return None


def _hunt(name, **over):
    hunt = {"name": name, "enabled": True, "enrich": False, "max_items": 2,
            "filters": {"suburbs": ["Zetland NSW 2017"], "mode": "sale"}}
    hunt.update(over)
    return hunt


class TestReportingCap(unittest.TestCase):
    def test_max_items_caps_the_report_not_the_comparison(self):
        """Truncating before the diff made listings rotate in and out of a window."""
        listings = [_listing(i) for i in range(1, 6)]
        with TemporaryDirectory() as tmp, PropertyDB(Path(tmp) / "t.db") as db:
            r = run_hunt(_hunt("h"), headed=False, mark=True, db=db, front=FRONT,
                         provider=StubProvider(listings))
            self.assertEqual(r["new_count"], 5)       # all five compared
            self.assertEqual(r["new_reported"], 2)    # only two reported
            self.assertEqual(len(r["new"]), 2)


class TestTruncation(unittest.TestCase):
    def test_truncated_result_never_marks_anything_stale(self):
        """Absence from a partial window is not evidence a listing is gone."""
        with TemporaryDirectory() as tmp, PropertyDB(Path(tmp) / "t.db") as db:
            run_hunt(_hunt("h"), headed=False, mark=True, db=db, front=FRONT,
                     provider=StubProvider([_listing(1), _listing(2)]))
            r = run_hunt(_hunt("h"), headed=False, mark=True, db=db, front=FRONT,
                         provider=StubProvider([_listing(1)], total=999, truncated=True))
            self.assertTrue(r["stale_truncated"])
            self.assertEqual(r["stale_count"], 0)

    def test_complete_result_does_mark_stale(self):
        with TemporaryDirectory() as tmp, PropertyDB(Path(tmp) / "t.db") as db:
            run_hunt(_hunt("h"), headed=False, mark=True, db=db, front=FRONT,
                     provider=StubProvider([_listing(1), _listing(2)]))
            r = run_hunt(_hunt("h"), headed=False, mark=True, db=db, front=FRONT,
                         provider=StubProvider([_listing(1)]))
            self.assertEqual(r["stale_count"], 1)


class TestCrossHuntStaleness(unittest.TestCase):
    """Hunts overlap once surrounding suburbs are included.

    Staleness is observed per hunt but recorded against the *listing*, so a unit
    present in hunt B and absent from hunt A used to be marked withdrawn by A
    and relisted by B on every single run.
    """

    def test_listing_present_in_a_sibling_hunt_is_not_stale(self):
        with TemporaryDirectory() as tmp, PropertyDB(Path(tmp) / "t.db") as db:
            for name in ("a", "b"):
                run_hunt(_hunt(name), headed=False, mark=True, db=db, front=FRONT,
                         provider=StubProvider([_listing(1), _listing(2)]))
            # Hunt A no longer returns listing 2; hunt B still does.
            results = [
                run_hunt(_hunt("a"), headed=False, mark=True, db=db, front=FRONT,
                         defer_stale=True, provider=StubProvider([_listing(1)])),
                run_hunt(_hunt("b"), headed=False, mark=True, db=db, front=FRONT,
                         defer_stale=True, provider=StubProvider([_listing(1), _listing(2)])),
            ]
            resolve_stale(db, results)
            self.assertEqual([r["stale_count"] for r in results], [0, 0])
            events = db.conn.execute(
                "SELECT count(*) c FROM listing_events WHERE event_type IN"
                " ('withdrawn_or_stale','relisted')").fetchone()["c"]
            self.assertEqual(events, 0)

    def test_listing_absent_everywhere_is_still_marked_stale(self):
        with TemporaryDirectory() as tmp, PropertyDB(Path(tmp) / "t.db") as db:
            for name in ("a", "b"):
                run_hunt(_hunt(name), headed=False, mark=True, db=db, front=FRONT,
                         provider=StubProvider([_listing(1), _listing(2)]))
            results = [
                run_hunt(_hunt(name), headed=False, mark=True, db=db, front=FRONT,
                         defer_stale=True, provider=StubProvider([_listing(1)]))
                for name in ("a", "b")
            ]
            resolve_stale(db, results)
            self.assertEqual([r["stale_count"] for r in results], [1, 1])


if __name__ == "__main__":
    unittest.main()
