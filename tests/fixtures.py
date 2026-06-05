"""Offline listing fixtures for decision-engine tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


def listing(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "id": "x1",
        "mode": "sale",
        "price": "$950,000 - $1,050,000",
        "price_from": 950_000,
        "price_to": 1_050_000,
        "beds": 2,
        "baths": 2,
        "cars": 1,
        "property_type": "Apartment",
        "address": {"display": "1 Test Street, Zetland NSW 2017", "suburb": "Zetland"},
        "description": "Light-filled apartment with balcony, secure parking, gym, lift and transport nearby.",
        "features": ["Air Conditioning", "Secure Parking", "Balcony"],
        "images": [{"url": "photo.jpg", "type": "photo"}, {"url": "floorplan.jpg", "type": "floorplan"}],
        "agents": [{"name": "Agent One", "mobile": "0400000000"}],
        "inspections": [{"start": "2026-06-06T10:00:00", "end": "2026-06-06T10:30:00"}],
    }
    base.update(overrides)
    return base


def strong_buyer_candidate() -> Dict[str, Any]:
    return listing(
        id="buyer-strong",
        price_from=920_000,
        price_to=990_000,
        description="North-facing apartment with lift, air conditioning, balcony and secure parking.",
    )


def strong_renter_candidate() -> Dict[str, Any]:
    return listing(
        id="renter-strong",
        mode="rent",
        price="$850 per week",
        price_from=850,
        price_to=850,
        description="Available now. Pet friendly apartment with air conditioning, heating, balcony and secure parking.",
    )


def attractive_but_risky_apartment() -> Dict[str, Any]:
    return listing(
        id="risky-apt",
        cars=0,
        description="Beautiful ground floor apartment on a busy main road with no parking and limited natural light.",
    )


def overpriced_property() -> Dict[str, Any]:
    return listing(id="overpriced", price="$1,350,000", price_from=1_350_000, price_to=1_350_000)


def underquoted_property() -> Dict[str, Any]:
    return listing(id="underquoted", price="$760,000", price_from=760_000, price_to=760_000)


def missing_data_property() -> Dict[str, Any]:
    raw = deepcopy(listing(id="missing-data", price=None, agents=[], inspections=[], images=[{"url": "photo.jpg", "type": "photo"}]))
    raw.pop("price_from", None)
    raw.pop("price_to", None)
    return raw
