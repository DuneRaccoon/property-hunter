#!/usr/bin/env python3
"""Pluggable independent property-value providers.

The valuation engine reconciles several *independent* value signals. Intrinsic
signals (comparable sold evidence, $/bed, $/sqm, hedonic, yield) are computed by
``valuation_engine`` itself. This module adds *external* providers — third-party
automated valuation models (AVMs) such as CoreLogic's propertyvalue.com.au — behind
a single interface so they can be added, swapped, or disabled without touching the
engine.

propertyvalue.com.au is CoreLogic's consumer AVM ("the same insights used by the big
4 banks"). It exposes no structured-data block and is hardened with Google reCAPTCHA,
so live capture must drive the real browser like a human and is fragile + rate
limited. The live provider is therefore disabled by default and writes any captured
estimate through to a local cache so it is reused without re-fetching.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

HERE = Path(__file__).resolve().parent
DEFAULT_AVM_CACHE = HERE / "data" / "avm_cache.json"


@dataclass
class ValueEstimate:
    """One independent value signal, in dollars (sale) or dollars/week (rent)."""

    provider: str
    method: str
    point: Optional[float] = None
    low: Optional[float] = None
    high: Optional[float] = None
    confidence: str = "low"  # none | low | medium | high
    as_at: Optional[str] = None
    source: Optional[str] = None
    note: Optional[str] = None

    def has_value(self) -> bool:
        return any(v is not None for v in (self.point, self.low, self.high))

    def best_point(self) -> Optional[float]:
        if self.point is not None:
            return float(self.point)
        if self.low is not None and self.high is not None:
            return (float(self.low) + float(self.high)) / 2.0
        if self.low is not None or self.high is not None:
            return float(self.low if self.low is not None else self.high)
        return None

    def half_spread(self) -> Optional[float]:
        if self.low is not None and self.high is not None:
            return (float(self.high) - float(self.low)) / 2.0
        return None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_address(listing: Dict[str, Any]) -> str:
    addr = listing.get("address")
    disp = addr.get("display") if isinstance(addr, dict) else None
    disp = disp or listing.get("address_display") or ""
    return re.sub(r"\s+", " ", str(disp)).strip().lower()


class ValueProvider:
    """Base interface. Providers return a ``ValueEstimate`` or ``None``."""

    name = "base"

    def available(self) -> bool:
        return False

    def estimate(
        self,
        listing: Dict[str, Any],
        *,
        db: Any = None,
        front: Optional[Dict[str, Any]] = None,
    ) -> Optional[ValueEstimate]:
        return None


class CachedAvmProvider(ValueProvider):
    """Serves AVM estimates previously captured to a local JSON cache.

    Keyed by listing id and by normalized address so a captured CoreLogic /
    propertyvalue estimate is reused across runs without re-fetching (and re-tripping
    the captcha). Records look like::

        {"point": 1000000, "low": 950000, "high": 1050000, "confidence": "medium",
         "provider": "propertyvalue", "method": "...", "as_at": "...", "source": "..."}
    """

    name = "avm-cache"

    def __init__(self, path: Path = DEFAULT_AVM_CACHE):
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def available(self) -> bool:
        return bool(self._data)

    def _lookup(self, listing: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        rec = self._data.get(str(listing.get("id")))
        if not rec:
            rec = self._data.get(normalize_address(listing))
        return rec

    def estimate(self, listing, *, db=None, front=None) -> Optional[ValueEstimate]:
        rec = self._lookup(listing)
        if not isinstance(rec, dict):
            return None
        est = ValueEstimate(
            provider=rec.get("provider", "propertyvalue"),
            method=rec.get("method", "Automated valuation model (CoreLogic)"),
            point=rec.get("point"),
            low=rec.get("low"),
            high=rec.get("high"),
            confidence=rec.get("confidence", "medium"),
            as_at=rec.get("as_at"),
            source=rec.get("source", "propertyvalue.com.au"),
            note=rec.get("note"),
        )
        return est if est.has_value() else None

    def put(self, key: str, record: Dict[str, Any]) -> None:
        """Persist a captured estimate under a listing id or normalized address."""
        self._data[str(key)] = record
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")


class PropertyValueProvider(ValueProvider):
    """Best-effort live CoreLogic AVM via propertyvalue.com.au.

    The site is captcha-hardened with no structured-data leak, so a live capture must
    drive the real (OpenClaw-managed) browser like a human and is fragile and rate
    limited. Disabled by default — enable explicitly (``enabled=True`` or env
    ``PROPERTY_HUNTER_ENABLE_AVM=1``) and only for a small shortlist. On any failure it
    returns ``None`` so the engine degrades to intrinsic signals. Cached captures are
    served first to avoid re-fetching.
    """

    name = "propertyvalue"

    def __init__(self, *, enabled: Optional[bool] = None, cache: Optional[CachedAvmProvider] = None):
        if enabled is None:
            enabled = os.getenv("PROPERTY_HUNTER_ENABLE_AVM") == "1"
        self.enabled = bool(enabled)
        self.cache = cache or CachedAvmProvider()

    def available(self) -> bool:
        return self.enabled or self.cache.available()

    def estimate(self, listing, *, db=None, front=None) -> Optional[ValueEstimate]:
        # Always prefer a cached capture (cheap, no captcha).
        cached = self.cache.estimate(listing, db=db, front=front)
        if cached is not None:
            return cached
        if not self.enabled:
            return None
        # Live capture is intentionally not wired into the automated pipeline: it
        # requires interactive captcha clearance and would jeopardise the crawler's
        # rate budget. Capture is done out-of-band and written through ``cache.put``.
        return None


def default_providers() -> list:
    """Standard external-provider stack for the engine."""
    return [PropertyValueProvider()]
