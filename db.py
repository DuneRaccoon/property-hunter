#!/usr/bin/env python3
"""SQLite persistence for Property Hunter.

Stores everything we observe so the buyer's agent can reason over history:
listings (sale/rent/sold), per-observation snapshots (price/status changes),
agents, inspections, hunts and their runs, plus a derived suburb-stats view.

Design notes:
- One row per listing in ``listings`` (latest known state) + an append-only
  ``listing_snapshots`` trail so we never lose a price change or relist.
- ``raw_json`` keeps the full normalized payload for anything not columnised.
- Idempotent upserts keyed on Domain's listing id.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from domain_cli import sold_status_from_tags

HERE = Path(__file__).resolve().parent
DEFAULT_DB_PATH = HERE / "data" / "property_hunter.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id              TEXT PRIMARY KEY,
    mode            TEXT,              -- sale | rent | sold
    listing_type    TEXT,
    status          TEXT,
    url             TEXT,
    headline        TEXT,
    description     TEXT,
    price_display   TEXT,
    price_from      INTEGER,
    price_to        INTEGER,
    beds            REAL,
    baths           REAL,
    cars            REAL,
    property_type   TEXT,
    land_area_sqm   REAL,
    address_display TEXT,
    street          TEXT,
    suburb          TEXT,
    state           TEXT,
    postcode        TEXT,
    lat             REAL,
    lng             REAL,
    agency          TEXT,
    sold_price      INTEGER,
    sold_date       TEXT,
    sale_method     TEXT,
    first_seen      TEXT,
    last_seen       TEXT,
    raw_json        TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_suburb ON listings(suburb, postcode);
CREATE INDEX IF NOT EXISTS idx_listings_mode ON listings(mode);
CREATE INDEX IF NOT EXISTS idx_listings_lastseen ON listings(last_seen);

CREATE TABLE IF NOT EXISTS listing_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id    TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    price_display TEXT,
    price_from    INTEGER,
    price_to      INTEGER,
    status        TEXT,
    FOREIGN KEY (listing_id) REFERENCES listings(id)
);
CREATE INDEX IF NOT EXISTS idx_snap_listing ON listing_snapshots(listing_id, observed_at);

CREATE TABLE IF NOT EXISTS listing_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id    TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    previous_value TEXT,
    current_value  TEXT,
    summary       TEXT,
    FOREIGN KEY (listing_id) REFERENCES listings(id)
);
CREATE INDEX IF NOT EXISTS idx_events_listing ON listing_events(listing_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON listing_events(event_type);

CREATE TABLE IF NOT EXISTS agents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    email       TEXT,
    mobile      TEXT,
    landline    TEXT,
    profile_url TEXT,
    photo_url   TEXT,
    agent_id    TEXT,
    agency      TEXT,
    listings_seen INTEGER DEFAULT 0,
    listings_sold INTEGER DEFAULT 0,
    avg_guide_vs_sold REAL,
    price_drops_observed INTEGER DEFAULT 0,
    underquote_signals INTEGER DEFAULT 0,
    metrics_updated_at TEXT,
    UNIQUE(name, agency)
);

CREATE TABLE IF NOT EXISTS listing_images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  TEXT NOT NULL,
    url         TEXT NOT NULL,
    media_type  TEXT,              -- photo | floorplan | video | virtualtour
    position    INTEGER,
    UNIQUE(listing_id, url)
);
CREATE INDEX IF NOT EXISTS idx_images_listing ON listing_images(listing_id, media_type);

CREATE TABLE IF NOT EXISTS listing_agents (
    listing_id TEXT NOT NULL,
    agent_id   INTEGER NOT NULL,
    PRIMARY KEY (listing_id, agent_id)
);

CREATE TABLE IF NOT EXISTS inspections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  TEXT NOT NULL,
    start_time  TEXT,
    end_time    TEXT,
    UNIQUE(listing_id, start_time)
);

CREATE TABLE IF NOT EXISTS hunts (
    name        TEXT PRIMARY KEY,
    filters_json TEXT,
    enabled     INTEGER DEFAULT 1,
    created_at  TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS hunt_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    hunt_name     TEXT,
    run_at        TEXT,
    url           TEXT,
    total_results INTEGER,
    page_count    INTEGER,
    new_count     INTEGER,
    blocked       INTEGER
);

CREATE TABLE IF NOT EXISTS hunt_run_listings (
    run_id     INTEGER NOT NULL,
    listing_id TEXT NOT NULL,
    is_new     INTEGER DEFAULT 0,
    PRIMARY KEY (run_id, listing_id)
);

CREATE TABLE IF NOT EXISTS external_facts (
    key            TEXT PRIMARY KEY,
    label          TEXT,
    value          TEXT,
    unit           TEXT,
    source_name    TEXT,
    source_url     TEXT,
    observed_at    TEXT,
    published_at   TEXT,
    freshness_days INTEGER,
    status         TEXT,
    notes          TEXT,
    raw_json       TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_price_amount(value: Any) -> Optional[int]:
    """Pull a dollar amount out of a display string like ``$1,050,000``.

    Used to recover a sold price from a sold-search card, whose price is carried
    only as a formatted string. Returns ``None`` for non-price labels
    (``Price Withheld``, ``Contact Agent``) and rejects tiny/ambiguous matches
    (e.g. the ``1`` in ``$1.2m``) so we never persist a bogus figure.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d[\d,]*", str(value))
    if not m:
        return None
    try:
        amount = int(m.group(0).replace(",", ""))
    except ValueError:
        return None
    return amount if amount >= 10_000 else None


def _midpoint(low: Any, high: Any) -> Optional[float]:
    lo, hi = _as_int(low), _as_int(high)
    if lo and hi:
        return (lo + hi) / 2.0
    return float(lo or hi) if (lo or hi) else None


class PropertyDB:
    def __init__(self, path: Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a DB was first created (non-destructive)."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(agents)").fetchall()}
        agent_columns = {
            "landline": "TEXT",
            "photo_url": "TEXT",
            "agent_id": "TEXT",
            "listings_seen": "INTEGER DEFAULT 0",
            "listings_sold": "INTEGER DEFAULT 0",
            "avg_guide_vs_sold": "REAL",
            "price_drops_observed": "INTEGER DEFAULT 0",
            "underquote_signals": "INTEGER DEFAULT 0",
            "metrics_updated_at": "TEXT",
        }
        for col, decl in agent_columns.items():
            if col not in cols:
                self.conn.execute(f"ALTER TABLE agents ADD COLUMN {col} {decl}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "PropertyDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- listings -------------------------------------------------------
    def upsert_listing(self, listing: Dict[str, Any], *, mode: str) -> str:
        """Insert/update a listing and append a snapshot if price/status moved."""
        addr = listing.get("address") or {}
        sold = listing.get("sold") or {}
        ptypes = listing.get("property_types") or listing.get("property_type")
        if isinstance(ptypes, list):
            ptypes = ", ".join(str(p) for p in ptypes)
        lid = str(listing.get("id"))
        now = _now()

        price_from = _as_int(listing.get("price_from"))
        price_to = _as_int(listing.get("price_to"))
        price_display = listing.get("price")
        status = listing.get("status")

        # A card carrying an off-market tag (e.g. "Sold by private treaty
        # 12 Jun 2026") is authoritative: force it to its real status regardless
        # of which hunt (sale/rent) surfaced it, so a sold listing never reads as
        # a live buy or trips a "relisted/vendor-blinking" narrative.
        off_market = sold_status_from_tags(listing)
        if off_market:
            status = off_market["status"]
            if off_market["status"] in ("sold", "leased"):
                mode = off_market["status"]

        existing = self.conn.execute(
            "SELECT price_display, price_from, price_to, status FROM listings WHERE id=?", (lid,)
        ).fetchone()

        # Capture the most recent event before we write new ones, so we can tell
        # if this listing is reappearing after having dropped off the market.
        prior_event = None
        if existing is not None:
            ev = self.conn.execute(
                "SELECT event_type FROM listing_events WHERE listing_id=? ORDER BY observed_at DESC LIMIT 1",
                (lid,),
            ).fetchone()
            prior_event = ev["event_type"] if ev else None

        self.conn.execute(
            """
            INSERT INTO listings (id, mode, listing_type, status, url, headline, description,
                price_display, price_from, price_to, beds, baths, cars, property_type,
                land_area_sqm, address_display, street, suburb, state, postcode, lat, lng,
                agency, sold_price, sold_date, sale_method, first_seen, last_seen, raw_json)
            VALUES (:id, :mode, :listing_type, :status, :url, :headline, :description,
                :price_display, :price_from, :price_to, :beds, :baths, :cars, :property_type,
                :land_area_sqm, :address_display, :street, :suburb, :state, :postcode, :lat, :lng,
                :agency, :sold_price, :sold_date, :sale_method, :now, :now, :raw_json)
            ON CONFLICT(id) DO UPDATE SET
                mode=excluded.mode, listing_type=excluded.listing_type, status=excluded.status,
                url=COALESCE(excluded.url, listings.url),
                headline=COALESCE(excluded.headline, listings.headline),
                description=COALESCE(excluded.description, listings.description),
                price_display=excluded.price_display, price_from=excluded.price_from,
                price_to=excluded.price_to, beds=COALESCE(excluded.beds, listings.beds),
                baths=COALESCE(excluded.baths, listings.baths), cars=COALESCE(excluded.cars, listings.cars),
                property_type=COALESCE(excluded.property_type, listings.property_type),
                land_area_sqm=COALESCE(excluded.land_area_sqm, listings.land_area_sqm),
                address_display=COALESCE(excluded.address_display, listings.address_display),
                street=COALESCE(excluded.street, listings.street),
                suburb=COALESCE(excluded.suburb, listings.suburb),
                state=COALESCE(excluded.state, listings.state),
                postcode=COALESCE(excluded.postcode, listings.postcode),
                lat=COALESCE(excluded.lat, listings.lat), lng=COALESCE(excluded.lng, listings.lng),
                agency=COALESCE(excluded.agency, listings.agency),
                sold_price=COALESCE(excluded.sold_price, listings.sold_price),
                sold_date=COALESCE(excluded.sold_date, listings.sold_date),
                sale_method=COALESCE(excluded.sale_method, listings.sale_method),
                last_seen=excluded.last_seen,
                raw_json=excluded.raw_json
            """,
            {
                "id": lid,
                "mode": mode,
                "listing_type": listing.get("listing_type"),
                "status": status,
                "url": listing.get("url"),
                "headline": listing.get("headline"),
                "description": listing.get("description"),
                "price_display": price_display,
                "price_from": price_from,
                "price_to": price_to,
                "beds": listing.get("beds"),
                "baths": listing.get("baths"),
                "cars": listing.get("cars"),
                "property_type": ptypes,
                "land_area_sqm": listing.get("land_area_sqm"),
                "address_display": addr.get("display"),
                "street": addr.get("street"),
                "suburb": addr.get("suburb"),
                "state": addr.get("state"),
                "postcode": addr.get("postcode"),
                "lat": addr.get("lat"),
                "lng": addr.get("lng"),
                "agency": (listing.get("agency") or {}).get("name") if isinstance(listing.get("agency"), dict) else None,
                "sold_price": (
                    (_as_int(sold.get("soldPrice") or sold.get("price")) if isinstance(sold, dict) else None)
                    # Sold cards carry no `sold` block — recover the price from the
                    # card's display string so the column is populated, not just
                    # parsed on the fly by downstream readers.
                    or (_parse_price_amount(price_display) if mode == "sold" else None)
                ),
                "sold_date": ((sold.get("soldDate") or sold.get("date")) if isinstance(sold, dict) else None)
                    or (off_market.get("sold_date") if off_market else None),
                "sale_method": ((sold.get("saleMethod") or sold.get("method")) if isinstance(sold, dict) else None)
                    or (off_market.get("sale_method") if off_market else None),
                "now": now,
                "raw_json": json.dumps(listing, ensure_ascii=False),
            },
        )

        moved = existing is None or (
            existing["price_display"] != price_display
            or existing["price_from"] != price_from
            or existing["price_to"] != price_to
            or existing["status"] != status
        )
        if moved:
            self.conn.execute(
                "INSERT INTO listing_snapshots (listing_id, observed_at, price_display, price_from, price_to, status)"
                " VALUES (?,?,?,?,?,?)",
                (lid, now, price_display, price_from, price_to, status),
            )
            # A card that just went off-market should record the sale, not a
            # "price guide changed" / "relisted" narrative — the price flipping
            # to the sold figure is not a live repricing.
            if off_market:
                if prior_event != "sold":
                    method = off_market.get("sale_method")
                    when = off_market.get("sold_date")
                    detail = off_market.get("tag_text") or off_market["status"]
                    summary = (
                        f"Listing left the market: {detail}"
                        + (f" ({method})" if method else "")
                        + (f" on {when}" if when else "")
                        + ". Not a live opportunity."
                    )
                    self.conn.execute(
                        "INSERT INTO listing_events (listing_id, observed_at, event_type, previous_value, current_value, summary)"
                        " VALUES (?,?,?,?,?,?)",
                        (lid, now, "sold", prior_event or "active", off_market["status"], summary),
                    )
            elif existing is not None:
                self._record_listing_events(
                    lid,
                    observed_at=now,
                    existing=dict(existing),
                    current={
                        "price_display": price_display,
                        "price_from": price_from,
                        "price_to": price_to,
                        "status": status,
                    },
                )

        # A listing whose last recorded event was "withdrawn_or_stale" is now
        # back in the result set -> it has been relisted. Record it once; the new
        # event becomes the latest, so steady-state reruns won't re-trigger.
        # Off-market (sold/leased) reappearances are NOT relists — skip them.
        if prior_event == "withdrawn_or_stale" and not off_market:
            self.conn.execute(
                "INSERT INTO listing_events (listing_id, observed_at, event_type, previous_value, current_value, summary)"
                " VALUES (?,?,?,?,?,?)",
                (
                    lid,
                    now,
                    "relisted",
                    "withdrawn/stale",
                    status or "active",
                    "Listing reappeared after dropping off the market — a relist (often a repricing or campaign reset; treat the new guide sceptically).",
                ),
            )

        agency_name = (listing.get("agency") or {}).get("name") if isinstance(listing.get("agency"), dict) else None
        self._upsert_agents(lid, listing.get("agents") or [], agency_name)
        self._upsert_inspections(lid, listing)
        self._upsert_images(lid, listing.get("images") or [])
        self._refresh_listing_agent_metrics(lid)
        self.conn.commit()
        return lid

    def _record_listing_events(
        self,
        listing_id: str,
        *,
        observed_at: str,
        existing: Dict[str, Any],
        current: Dict[str, Any],
    ) -> None:
        old_mid = _midpoint(existing.get("price_from"), existing.get("price_to"))
        new_mid = _midpoint(current.get("price_from"), current.get("price_to"))
        old_price = existing.get("price_display")
        new_price = current.get("price_display")
        if (old_price, old_mid) != (new_price, new_mid):
            if old_mid and new_mid and new_mid < old_mid:
                event_type = "price_drop"
                direction = "dropped"
            elif old_mid and new_mid and new_mid > old_mid:
                event_type = "price_rise"
                direction = "rose"
            else:
                event_type = "price_change"
                direction = "changed"
            prev = old_price or (f"${old_mid:,.0f}" if old_mid else None)
            curr = new_price or (f"${new_mid:,.0f}" if new_mid else None)
            self.conn.execute(
                "INSERT INTO listing_events (listing_id, observed_at, event_type, previous_value, current_value, summary)"
                " VALUES (?,?,?,?,?,?)",
                (listing_id, observed_at, event_type, str(prev or ""), str(curr or ""), f"Price guide {direction}: {prev or 'unknown'} -> {curr or 'unknown'}"),
            )
        if (existing.get("status") or "") != (current.get("status") or ""):
            self.conn.execute(
                "INSERT INTO listing_events (listing_id, observed_at, event_type, previous_value, current_value, summary)"
                " VALUES (?,?,?,?,?,?)",
                (
                    listing_id,
                    observed_at,
                    "status_change",
                    str(existing.get("status") or ""),
                    str(current.get("status") or ""),
                    f"Status changed: {existing.get('status') or 'unknown'} -> {current.get('status') or 'unknown'}",
                ),
            )

    def _upsert_images(self, listing_id: str, images: Iterable[Any]) -> None:
        """Store listing images, tagged by type. Accepts dicts or bare URLs.

        Detail pages give ``{url, type, category, position}``; search cards
        give bare URL strings (treated as photos).
        """
        for pos, img in enumerate(images):
            if isinstance(img, dict):
                url = img.get("url")
                media_type = (img.get("type") or "photo")
                position = img.get("position") if img.get("position") is not None else pos
            elif isinstance(img, str):
                url, media_type, position = img, "photo", pos
            else:
                continue
            if not url:
                continue
            self.conn.execute(
                "INSERT INTO listing_images (listing_id, url, media_type, position) VALUES (?,?,?,?)"
                " ON CONFLICT(listing_id, url) DO UPDATE SET"
                " media_type=COALESCE(excluded.media_type, listing_images.media_type),"
                " position=COALESCE(excluded.position, listing_images.position)",
                (listing_id, url, media_type, position),
            )

    def _upsert_agents(self, listing_id: str, agents: Iterable[Dict[str, Any]], agency_name: Optional[str] = None) -> None:
        for agent in agents:
            if not isinstance(agent, dict) or not agent.get("name"):
                continue
            # Coalesce agency to '' so UNIQUE(name, agency) dedupes (NULLs are distinct in SQL).
            agency = agent.get("agency") or agency_name or ""
            cur = self.conn.execute(
                "INSERT INTO agents (name, email, mobile, landline, profile_url, photo_url, agent_id, agency)"
                " VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(name, agency) DO UPDATE SET"
                " email=COALESCE(excluded.email, agents.email),"
                " mobile=COALESCE(excluded.mobile, agents.mobile),"
                " landline=COALESCE(excluded.landline, agents.landline),"
                " profile_url=COALESCE(excluded.profile_url, agents.profile_url),"
                " photo_url=COALESCE(excluded.photo_url, agents.photo_url),"
                " agent_id=COALESCE(excluded.agent_id, agents.agent_id)"
                " RETURNING id",
                (
                    agent.get("name"), agent.get("email"), agent.get("mobile"),
                    agent.get("landline"), agent.get("profile_url"), agent.get("photo"),
                    str(agent.get("agent_id")) if agent.get("agent_id") is not None else None,
                    agency,
                ),
            )
            row = cur.fetchone()
            if row:
                self.conn.execute(
                    "INSERT OR IGNORE INTO listing_agents (listing_id, agent_id) VALUES (?,?)",
                    (listing_id, row["id"]),
                )

    def _refresh_listing_agent_metrics(self, listing_id: str) -> None:
        rows = self.conn.execute("SELECT agent_id FROM listing_agents WHERE listing_id=?", (listing_id,)).fetchall()
        for row in rows:
            self.refresh_agent_metrics(int(row["agent_id"]))

    def refresh_agent_metrics(self, agent_pk: int) -> Dict[str, Any]:
        """Update evidence-backed performance counters for one stored agent."""
        linked = self.conn.execute(
            """
            SELECT l.id, l.mode, l.sold_price, l.price_from, l.price_to
            FROM listings l
            JOIN listing_agents la ON la.listing_id = l.id
            WHERE la.agent_id=?
            """,
            (agent_pk,),
        ).fetchall()
        listing_ids = [r["id"] for r in linked]
        sold_rows = [r for r in linked if r["sold_price"]]
        guide_deltas = []
        underquote_signals = 0
        for r in sold_rows:
            mid = _midpoint(r["price_from"], r["price_to"])
            if mid:
                delta = (float(r["sold_price"]) - mid) / mid
                guide_deltas.append(delta)
                if delta >= 0.10:
                    underquote_signals += 1

        price_drops = 0
        if listing_ids:
            placeholders = ",".join("?" for _ in listing_ids)
            price_drops = self.conn.execute(
                f"SELECT COUNT(*) FROM listing_events WHERE event_type='price_drop' AND listing_id IN ({placeholders})",
                listing_ids,
            ).fetchone()[0]

        metrics = {
            "listings_seen": len(set(listing_ids)),
            "listings_sold": len(sold_rows),
            "avg_guide_vs_sold": round(sum(guide_deltas) / len(guide_deltas), 4) if guide_deltas else None,
            "price_drops_observed": int(price_drops),
            "underquote_signals": underquote_signals,
            "metrics_updated_at": _now(),
        }
        self.conn.execute(
            """
            UPDATE agents
            SET listings_seen=?, listings_sold=?, avg_guide_vs_sold=?,
                price_drops_observed=?, underquote_signals=?, metrics_updated_at=?
            WHERE id=?
            """,
            (
                metrics["listings_seen"],
                metrics["listings_sold"],
                metrics["avg_guide_vs_sold"],
                metrics["price_drops_observed"],
                metrics["underquote_signals"],
                metrics["metrics_updated_at"],
                agent_pk,
            ),
        )
        return metrics

    def refresh_all_agent_metrics(self) -> int:
        rows = self.conn.execute("SELECT id FROM agents").fetchall()
        for row in rows:
            self.refresh_agent_metrics(int(row["id"]))
        self.conn.commit()
        return len(rows)

    def _upsert_inspections(self, listing_id: str, listing: Dict[str, Any]) -> None:
        inspections = listing.get("inspections")
        if not inspections and isinstance(listing.get("inspection"), dict):
            insp = listing["inspection"]
            inspections = [{"start": insp.get("openTime"), "end": insp.get("closeTime")}]
        existing = {
            (r["start_time"], r["end_time"])
            for r in self.conn.execute(
                "SELECT start_time, end_time FROM inspections WHERE listing_id=?",
                (listing_id,),
            ).fetchall()
        }
        current = {
            (item.get("start"), item.get("end"))
            for item in inspections or []
            if isinstance(item, dict) and item.get("start")
        }
        if existing and current and existing != current:
            old = "; ".join(" - ".join(str(x or "") for x in item) for item in sorted(existing))
            new = "; ".join(" - ".join(str(x or "") for x in item) for item in sorted(current))
            self.conn.execute(
                "INSERT INTO listing_events (listing_id, observed_at, event_type, previous_value, current_value, summary)"
                " VALUES (?,?,?,?,?,?)",
                (listing_id, _now(), "inspection_change", old, new, "Inspection times changed."),
            )
        for item in inspections or []:
            if isinstance(item, dict) and item.get("start"):
                self.conn.execute(
                    "INSERT OR IGNORE INTO inspections (listing_id, start_time, end_time) VALUES (?,?,?)",
                    (listing_id, item.get("start"), item.get("end")),
                )

    def mark_listings_stale(self, listing_ids: Iterable[str], *, observed_at: Optional[str] = None) -> int:
        """Record that previously seen listings disappeared from the latest provider result."""
        observed_at = observed_at or _now()
        count = 0
        for raw_id in listing_ids:
            listing_id = str(raw_id)
            latest = self.conn.execute(
                "SELECT event_type, observed_at FROM listing_events WHERE listing_id=? ORDER BY observed_at DESC LIMIT 1",
                (listing_id,),
            ).fetchone()
            if latest and latest["event_type"] in {"stale_missing", "withdrawn_or_stale"}:
                continue
            self.conn.execute(
                "INSERT INTO listing_events (listing_id, observed_at, event_type, previous_value, current_value, summary)"
                " VALUES (?,?,?,?,?,?)",
                (
                    listing_id,
                    observed_at,
                    "withdrawn_or_stale",
                    "seen in previous run",
                    "missing from latest run",
                    "Listing disappeared from the latest provider result; check if withdrawn, sold/leased, or missed by the provider.",
                ),
            )
            count += 1
        if count:
            self.conn.commit()
        return count

    # ---- hunts ----------------------------------------------------------
    def upsert_hunt(self, name: str, filters: Dict[str, Any], enabled: bool = True) -> None:
        now = _now()
        self.conn.execute(
            "INSERT INTO hunts (name, filters_json, enabled, created_at, updated_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(name) DO UPDATE SET filters_json=excluded.filters_json,"
            " enabled=excluded.enabled, updated_at=excluded.updated_at",
            (name, json.dumps(filters), int(enabled), now, now),
        )
        self.conn.commit()

    def record_run(
        self,
        hunt_name: str,
        url: str,
        *,
        total_results: Optional[int],
        page_count: Optional[int],
        new_ids: List[str],
        all_ids: List[str],
        blocked: bool,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO hunt_runs (hunt_name, run_at, url, total_results, page_count, new_count, blocked)"
            " VALUES (?,?,?,?,?,?,?)",
            (hunt_name, _now(), url, total_results, page_count, len(new_ids), int(blocked)),
        )
        run_id = cur.lastrowid
        new_set = set(new_ids)
        for lid in all_ids:
            self.conn.execute(
                "INSERT OR IGNORE INTO hunt_run_listings (run_id, listing_id, is_new) VALUES (?,?,?)",
                (run_id, lid, int(lid in new_set)),
            )
        self.conn.commit()
        return run_id

    def seen_ids(self, hunt_name: str) -> set:
        rows = self.conn.execute(
            "SELECT DISTINCT listing_id FROM hunt_run_listings"
            " JOIN hunt_runs ON hunt_runs.id = hunt_run_listings.run_id"
            " WHERE hunt_runs.hunt_name = ?",
            (hunt_name,),
        ).fetchall()
        return {r["listing_id"] for r in rows}

    def supply_trend(self, hunt_name: str, *, history: int = 12) -> Dict[str, Any]:
        """Track market supply for a saved search over time.

        ``total_results`` is the count Domain returns at the top of the search
        results page, captured per run. Reading it as a trend lets the agent
        factor current supply (and whether it is rising/falling) into advice.
        """
        rows = self.conn.execute(
            "SELECT run_at, total_results FROM hunt_runs"
            " WHERE hunt_name=? AND total_results IS NOT NULL"
            " ORDER BY run_at DESC LIMIT ?",
            (hunt_name, history),
        ).fetchall()
        series = [{"run_at": r["run_at"], "total_results": int(r["total_results"])} for r in rows]
        if not series:
            return {"hunt": hunt_name, "current": None, "previous": None, "delta": None,
                    "pct_change": None, "avg_recent": None, "n_runs": 0,
                    "direction": "no-data", "history": []}
        current = series[0]["total_results"]
        previous = series[1]["total_results"] if len(series) > 1 else None
        delta = (current - previous) if previous is not None else None
        pct = round((delta / previous) * 100, 1) if (delta is not None and previous) else None
        vals = [s["total_results"] for s in series]
        avg_recent = round(sum(vals) / len(vals))
        if delta is None:
            direction = "first-reading"
        elif delta > 0:
            direction = "rising"
        elif delta < 0:
            direction = "falling"
        else:
            direction = "flat"
        return {
            "hunt": hunt_name,
            "current": current,
            "previous": previous,
            "delta": delta,
            "pct_change": pct,
            "avg_recent": avg_recent,
            "n_runs": len(series),
            "direction": direction,
            "history": list(reversed(series)),
        }

    def previous_run_at(self, hunt_name: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT run_at FROM hunt_runs WHERE hunt_name=? ORDER BY run_at DESC LIMIT 1",
            (hunt_name,),
        ).fetchone()
        return row["run_at"] if row else None

    def listing_changes(self, listing_id: str, *, since: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        sql = (
            "SELECT event_type, observed_at, previous_value, current_value, summary "
            "FROM listing_events WHERE listing_id=?"
        )
        params: List[Any] = [listing_id]
        if since:
            sql += " AND observed_at > ?"
            params.append(since)
        sql += " ORDER BY observed_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def changed_listing_ids(self, ids: Iterable[str], *, since: Optional[str] = None) -> set:
        ids = [str(i) for i in ids if i]
        if not ids:
            return set()
        placeholders = ",".join("?" for _ in ids)
        sql = f"SELECT DISTINCT listing_id FROM listing_events WHERE listing_id IN ({placeholders})"
        params: List[Any] = ids
        if since:
            sql += " AND observed_at > ?"
            params.append(since)
        return {r["listing_id"] for r in self.conn.execute(sql, params).fetchall()}

    def lifecycle_summary(self, listing_id: str, *, since: Optional[str] = None) -> Dict[str, Any]:
        listing = self.conn.execute(
            "SELECT first_seen, last_seen, status FROM listings WHERE id=?",
            (listing_id,),
        ).fetchone()
        snapshots = self.conn.execute(
            "SELECT COUNT(*) FROM listing_snapshots WHERE listing_id=?",
            (listing_id,),
        ).fetchone()[0]
        events = self.listing_changes(listing_id, since=since)
        price_drops = sum(1 for e in events if e["event_type"] == "price_drop")
        if not listing:
            return {"listing_id": listing_id, "events": events}
        return {
            "listing_id": listing_id,
            "first_seen": listing["first_seen"],
            "last_seen": listing["last_seen"],
            "status": listing["status"],
            "snapshot_count": snapshots,
            "changed": bool(events),
            "price_drops": price_drops,
            "events": events,
            "why_now": self.why_now(listing_id, since=since),
        }

    def why_now(self, listing_id: str, *, since: Optional[str] = None) -> str:
        events = self.listing_changes(listing_id, since=since, limit=3)
        if not events:
            return "New to this hunt or still active; no material change recorded yet."
        sold = next((e for e in events if e["event_type"] == "sold"), None)
        if sold:
            return sold["summary"]
        relisted = next((e for e in events if e["event_type"] == "relisted"), None)
        if relisted:
            drop = next((e for e in events if e["event_type"] == "price_drop"), None)
            msg = "Relisted after dropping off the market"
            if drop:
                msg += f"; {drop['summary'].lower()}"
            return msg + "."
        price_drop = next((e for e in events if e["event_type"] == "price_drop"), None)
        if price_drop:
            return f"Resurfaced because {price_drop['summary'].lower()}."
        return "Resurfaced because " + "; ".join(e["summary"].lower() for e in events) + "."

    def agent_performance(self, *, min_seen: int = 1, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, name, agency, listings_seen, listings_sold, avg_guide_vs_sold,
                   price_drops_observed, underquote_signals, metrics_updated_at
            FROM agents
            WHERE listings_seen >= ?
            ORDER BY underquote_signals DESC, price_drops_observed DESC, listings_seen DESC, name
            LIMIT ?
            """,
            (min_seen, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- external facts -------------------------------------------------
    def upsert_external_fact(self, fact: Dict[str, Any]) -> str:
        """Persist one dated external fact used by report prose."""
        key = str(fact.get("key"))
        now = fact.get("observed_at") or _now()
        self.conn.execute(
            """
            INSERT INTO external_facts
                (key, label, value, unit, source_name, source_url, observed_at,
                 published_at, freshness_days, status, notes, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
                label=excluded.label, value=excluded.value, unit=excluded.unit,
                source_name=excluded.source_name, source_url=excluded.source_url,
                observed_at=excluded.observed_at, published_at=excluded.published_at,
                freshness_days=excluded.freshness_days, status=excluded.status,
                notes=excluded.notes, raw_json=excluded.raw_json
            """,
            (
                key,
                fact.get("label"),
                fact.get("value"),
                fact.get("unit"),
                fact.get("source_name"),
                fact.get("source_url"),
                now,
                fact.get("published_at"),
                fact.get("freshness_days"),
                fact.get("status"),
                fact.get("notes"),
                json.dumps(fact, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return key

    def list_external_facts(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT key, label, value, unit, source_name, source_url, observed_at,
                   published_at, freshness_days, status, notes
            FROM external_facts
            ORDER BY key
            """
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- analysis -------------------------------------------------------
    def suburb_stats(self, mode: str = "sold", min_count: int = 3) -> List[Dict[str, Any]]:
        """Aggregate per-suburb stats from stored listings of a given mode."""
        rows = self.conn.execute(
            """
            SELECT suburb, state, postcode,
                   COUNT(*) AS n,
                   AVG(COALESCE(sold_price, (price_from + price_to)/2, price_from, price_to)) AS avg_price,
                   MIN(COALESCE(sold_price, price_from, price_to)) AS min_price,
                   MAX(COALESCE(sold_price, price_to, price_from)) AS max_price,
                   AVG(beds) AS avg_beds
            FROM listings
            WHERE mode = ? AND suburb IS NOT NULL
            GROUP BY suburb, state, postcode
            HAVING n >= ?
            ORDER BY n DESC
            """,
            (mode, min_count),
        ).fetchall()
        return [dict(r) for r in rows]

    def listing_media(self, listing_id: str) -> Dict[str, List[str]]:
        """Return a listing's image URLs grouped by type (photos/floorplans/other)."""
        rows = self.conn.execute(
            "SELECT url, media_type FROM listing_images WHERE listing_id=? ORDER BY position",
            (listing_id,),
        ).fetchall()
        grouped: Dict[str, List[str]] = {"photos": [], "floorplans": [], "other": []}
        for r in rows:
            mt = (r["media_type"] or "photo").lower()
            if mt == "photo":
                grouped["photos"].append(r["url"])
            elif mt == "floorplan":
                grouped["floorplans"].append(r["url"])
            else:
                grouped["other"].append(r["url"])
        return grouped

    def count_listings(self, mode: Optional[str] = None) -> int:
        if mode:
            return self.conn.execute("SELECT COUNT(*) FROM listings WHERE mode=?", (mode,)).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(prog="db.py")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("cmd", choices=["init", "stats", "count"], nargs="?", default="init")
    ap.add_argument("--mode", default="sold")
    args = ap.parse_args()

    with PropertyDB(Path(args.db)) as db:
        if args.cmd == "init":
            print(f"DB ready at {db.path}")
        elif args.cmd == "count":
            print(f"listings: {db.count_listings()} (sale={db.count_listings('sale')}, rent={db.count_listings('rent')}, sold={db.count_listings('sold')})")
        elif args.cmd == "stats":
            for row in db.suburb_stats(mode=args.mode):
                print(row)
