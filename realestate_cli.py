#!/usr/bin/env python3
"""realestate.com.au structured-data fetcher — the Domain fallback.

Deliberately a mirror of ``domain_cli.py``: same public surface
(``build_search_url``, ``fetch_html``, ``extract_search_payload``,
``extract_listing_payload``, ``listing_url_for_id``) and the *same normalized
listing dict* that ``db.PropertyDB.upsert_listing`` consumes, so REA listings
drop into the existing pipeline (hunts, DB, viability, report_builder) with no
schema changes. Only two things differ, because the site does:

  1. Transport block. REA sits behind **Kasada** (``KPSDK`` / ``ips.js``), not
     Akamai. We still fetch through the genuine OpenClaw browser over CDP — the
     one transport that has beaten this class of protection — and detect the
     Kasada challenge shell instead of Akamai's sec-cpt page.
  2. Data shape. REA ships its data in ``window.ArgonautExchange`` (a doubly
     stringified urql cache), not Domain's ``__NEXT_DATA__``. ``extract_*``
     unwraps that; ``normalize_*`` maps it onto the shared listing schema.

IDs are namespaced ``rea:<id>`` so REA and Domain listings never collide on the
shared ``listings.id`` primary key. Every REA listing carries its own canonical
``url`` so the Domain ``listing_url_for_id`` fallback is never hit for them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

# Reuse Domain's rate-limit + browser-ensure primitives verbatim so both
# scrapers behave identically on the wire (polite pacing, self-healing browser).
from domain_cli import (
    RateLimitConfig,
    SimpleLeakyBucket,
    build_bucket,
    polite_pause,
    ensure_browser,
    cache_path_for,
    DEFAULT_CDP_URL,
)

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None  # type: ignore


REA_ID_PREFIX = "rea:"
BASE = "https://www.realestate.com.au"
DEFAULT_CACHE_DIR = Path(".cache/realestate/html")

SEARCH_MODES = ("sale", "rent", "sold")
# REA's channel path segment for each mode.
MODE_CHANNEL = {"sale": "buy", "rent": "rent", "sold": "sold"}

# Domain ptype tokens -> REA slug tokens. Passthrough for anything unlisted.
PTYPE_MAP = {
    "apartment": "apartment",
    "apartmentunitflat": "unit+apartment",
    "unit": "unit",
    "house": "house",
    "townhouse": "townhouse",
    "villa": "villa",
    "duplex": "duplex",
    "studio": "studio",
    "penthouse": "apartment",
    "acreage": "acreage",
    "land": "land",
}

# Kasada / WAF fingerprints: if the returned HTML is one of these it is a
# challenge or deny shell, not a real page.
BLOCK_MARKERS = (
    "kpsdk",
    "/ips.js",
    "access to this page has been denied",
    "pardon our interruption",
    "px-captcha",
    "please verify you are a human",
    "request unsuccessful",
)


# --------------------------------------------------------------------------- #
# URL building
# --------------------------------------------------------------------------- #
def _norm_id(listing_id: Any) -> str:
    """Return a bare numeric id (strip our ``rea:`` namespace if present)."""
    s = str(listing_id).strip()
    return s[len(REA_ID_PREFIX):] if s.startswith(REA_ID_PREFIX) else s


def listing_url_for_id(listing_id: str) -> str:
    """Best-effort canonical URL for a REA id.

    REA (unlike Domain) does not redirect a bare ``/<id>`` to the listing, so
    prefer the ``url`` carried on the normalized listing. This is only a
    fallback for callers that have nothing but an id.
    """
    return f"{BASE}/property-{_norm_id(listing_id)}"


def locality_slug(value: str) -> str:
    """'Zetland NSW 2017' -> 'zetland,+nsw+2017' (REA's location slug).

    Accepts already-slugged values. Suburb words join with '+', then a comma,
    then state and postcode.
    """
    v = value.strip()
    if "," in v and "+" in v:  # already a slug
        return v.lower()
    m = re.match(r"^(.*?)\s+([A-Za-z]{2,3})\s+(\d{4})$", v)
    if m:
        suburb, state, postcode = m.group(1), m.group(2), m.group(3)
        suburb_slug = re.sub(r"[^a-z0-9]+", "+", suburb.lower()).strip("+")
        return f"{suburb_slug},+{state.lower()}+{postcode}"
    # No state/postcode: just slug the suburb words.
    return re.sub(r"[^a-z0-9]+", "+", v.lower()).strip("+")


def _ptype_slug(ptypes: Optional[List[str]]) -> Optional[str]:
    toks = []
    for p in ptypes or []:
        key = re.sub(r"[^a-z0-9]+", "", str(p).lower())
        toks.append(PTYPE_MAP.get(key, key))
    toks = [t for t in toks if t]
    if not toks:
        return None
    return "property-" + "+".join(toks)


def build_search_url(
    *,
    mode: str = "sale",
    suburbs: Optional[List[str]] = None,
    region: Optional[str] = None,
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
    beds_min: Optional[int] = None,
    beds_max: Optional[int] = None,   # applied client-side (REA slug is min-only)
    baths_min: Optional[int] = None,  # applied client-side
    cars_min: Optional[int] = None,   # applied client-side
    ptypes: Optional[List[str]] = None,
    exclude_under_offer: bool = False,
    features: Optional[List[str]] = None,
    keywords: Optional[str] = None,
    sort: Optional[str] = None,
    page: Optional[int] = None,
) -> str:
    """Build a realestate.com.au search URL from the shared filter vocabulary.

    REA encodes filters as hyphenated slug segments *before* the location, e.g.
    ``/buy/property-apartment-between-0-1100000-in-zetland,+nsw+2017/list-1``.
    Bedroom/bathroom/car *ranges* are not cleanly expressible in the slug, so
    only the minimum bedroom count goes in the URL; ``beds_max``/``baths_min``/
    ``cars_min`` are enforced client-side by the provider (see ``passes_filters``).
    """
    if mode not in SEARCH_MODES:
        raise ValueError(f"mode must be one of {SEARCH_MODES}")
    channel = MODE_CHANNEL[mode]

    localities = [locality_slug(s) for s in (suburbs or []) if s.strip()]
    if region:
        localities = [locality_slug(region)]
    loc = ";".join(localities) if localities else ""

    segments: List[str] = []
    pt = _ptype_slug(ptypes)
    if pt:
        segments.append(pt)
    if beds_min:
        segments.append(f"with-{beds_min}-bedrooms")
    if price_min is not None or price_max is not None:
        lo = price_min if price_min is not None else 0
        hi = price_max if price_max is not None else "any"
        segments.append(f"between-{lo}-{hi}")
    if keywords:
        segments.append("with-keywords-" + re.sub(r"\s+", "+", keywords.strip()))

    filter_slug = "-".join(segments)
    if loc:
        path = f"{filter_slug}-in-{loc}" if filter_slug else f"in-{loc}"
    else:
        path = filter_slug or "in-australia"

    url = f"{BASE}/{channel}/{path}/list-{page or 1}"

    # REA supports a few query params that have no slug form.
    query: List[str] = []
    if exclude_under_offer:
        query.append("misc=ex-under-contract")
    if sort:
        query.append(f"source=refinement&sort={sort}")
    if query:
        url += "?" + "&".join(query)
    return url


# --------------------------------------------------------------------------- #
# Transport (genuine browser over CDP, Kasada-aware)
# --------------------------------------------------------------------------- #
_CDP = {"pw": None, "browser": None, "page": None}


def has_structured_data(html: str) -> bool:
    """True when REA returned a real page carrying the ArgonautExchange cache."""
    return "argonautexchange" in (html or "").lower()


def detect_blocked(html: str) -> bool:
    lower = (html or "").lower()
    if has_structured_data(html):
        return False
    return True if not html else any(m in lower for m in BLOCK_MARKERS) or len(html) < 20000


def detect_hard_denial(html: str) -> bool:
    """Tiny Kasada/WAF shell with no usable data."""
    if has_structured_data(html):
        return False
    lower = (html or "").lower()
    return (
        not html
        or len(html) < 20000
        or any(m in lower for m in BLOCK_MARKERS)
    )


def classify_block(html: str) -> str:
    lower = (html or "").lower()
    # Real REA pages ship the KPSDK/ips.js sensor too, so the cache is the
    # authoritative "this is a real page" signal and has to be checked first.
    if has_structured_data(html) and not detect_hard_denial(html):
        return "ok"
    if "kpsdk" in lower or "/ips.js" in lower:
        return "kasada_challenge"
    if "access to this page has been denied" in lower or "pardon our interruption" in lower:
        return "access_denied"
    if not html or len(html) < 200:
        return "browser_down"
    if has_structured_data(html):
        return "empty_shell"
    if len(html) < 20000:
        return "kasada_challenge"
    return "ok"


def _teardown_cdp() -> None:
    for key in ("browser", "pw"):
        obj = _CDP.get(key)
        try:
            if key == "browser" and obj is not None:
                obj.close()
            elif key == "pw" and obj is not None:
                obj.stop()
        except Exception:
            pass
    _CDP.update({"pw": None, "browser": None, "page": None})


def _rea_page(cdp_url: str, timeout_s: int):
    """Attach to the running OpenClaw browser and return a reusable page."""
    if _CDP["page"] is not None:
        try:
            _ = _CDP["page"].url
            return _CDP["page"]
        except Exception:
            _teardown_cdp()

    ensure_browser(cdp_url)
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(cdp_url, timeout=timeout_s * 1000)
    contexts = browser.contexts or []
    if not contexts:
        raise RuntimeError(f"No browser context available at {cdp_url}. Is the browser running?")
    # Own a dedicated tab rather than hijacking whatever the browser is showing.
    page = contexts[0].new_page()
    page.set_default_timeout(min(timeout_s, 30) * 1000)
    page.set_default_navigation_timeout(min(timeout_s, 30) * 1000)
    _CDP.update({"pw": pw, "browser": browser, "page": page})
    return page


def _wait_until_ready(page, timeout_s: int, *, challenge_grace_s: float = 8.0) -> bool:
    """Poll until the ArgonautExchange cache is present (Kasada solved).

    Kasada either solves within a few seconds or not at all (a flagged session
    never clears). So we give the challenge a short grace period, then bail —
    otherwise a blocked fetch would burn the full timeout and stall a multi-hunt
    crawl. A genuinely slow-hydrating page still gets the full ``timeout_s``.
    """
    start = time.time()
    deadline = start + timeout_s
    while time.time() < deadline:
        try:
            html = page.content()
        except Exception:
            html = ""
        if has_structured_data(html):
            return True
        if html and len(html) > 20000 and not detect_hard_denial(html):
            return True
        # Persistent small Kasada/deny shell past the grace window -> give up.
        if time.time() - start > challenge_grace_s and detect_hard_denial(html):
            return False
        time.sleep(1.0)
    return False


HOMEPAGE_URL = f"{BASE}/"

# Same-origin XHR issued from an already-trusted REA document.
_IN_SESSION_FETCH = """
async (target) => {
  const res = await fetch(target, {
    credentials: 'include',
    headers: {'Accept': 'text/html,application/xhtml+xml'},
  });
  return {status: res.status, html: await res.text()};
}
"""


def _ensure_on_homepage(page, timeout_s: int) -> None:
    """Park the document on REA's homepage (which loads clean).

    Only navigates when the page isn't already sitting on a healthy
    realestate.com.au document, so a multi-hunt run pays the homepage load once.
    """
    try:
        current = page.url or ""
        if current.startswith(BASE) and page.title():
            return
    except Exception:
        pass
    page.goto(HOMEPAGE_URL, wait_until="domcontentloaded", timeout=min(timeout_s, 30) * 1000)
    deadline = time.time() + min(timeout_s, 20)
    while time.time() < deadline:
        try:
            if page.title():
                return
        except Exception:
            pass
        time.sleep(0.5)


def _fetch_in_session(page, url: str) -> str:
    """Pull ``url`` as an XHR from the parked homepage document."""
    result = page.evaluate(_IN_SESSION_FETCH, url)
    if not isinstance(result, dict) or result.get("status") != 200:
        return ""
    return result.get("html") or ""


def cdp_get(url: str, *, bucket, cfg: RateLimitConfig, timeout_s: int = 60,
            cdp_url: str = DEFAULT_CDP_URL) -> str:
    """Fetch a REA page through the genuine OpenClaw browser.

    Kasada challenges *document navigations* to the search endpoints -- a plain
    ``page.goto('/buy/...')`` comes back as the KPSDK/ips.js shell -- but leaves
    same-origin XHRs from an already-trusted page alone. So we park the document
    on the homepage (which loads clean) and pull each target URL with an
    in-session ``fetch()``: the same trick that beat Domain's Akamai sec-cpt in
    June, verified against REA on 2026-08-13 (200, 1.34MB, full ArgonautExchange).

    Falls back to a direct navigation if the XHR path ever fails, then resets the
    session once before giving up.
    """
    if sync_playwright is None:
        raise RuntimeError("Playwright is not installed. Run: ./venv/bin/pip install playwright")

    polite_pause(bucket, cfg)
    html = ""
    for _attempt in range(2):
        page = _rea_page(cdp_url, timeout_s)
        try:
            _ensure_on_homepage(page, timeout_s)
            html = _fetch_in_session(page, url)
        except Exception:
            html = ""
        if html and not detect_hard_denial(html):
            return html

        # XHR path failed -- try a document navigation before burning the session.
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=min(timeout_s, 30) * 1000)
        except Exception:
            pass
        ready = _wait_until_ready(page, timeout_s)
        try:
            nav_html = page.content()
        except Exception:
            nav_html = ""
        if ready and nav_html and not detect_hard_denial(nav_html):
            return nav_html
        html = nav_html or html
        _teardown_cdp()
    return html


def fetch_html(url: str, *, fetcher: str = "cdp", timeout_s: int = 60,
               cache_dir: Path = DEFAULT_CACHE_DIR, no_cache: bool = False,
               headed: bool = True, cdp_url: str = DEFAULT_CDP_URL,
               rps: float = 0.35, burst: int = 1, **_ignored) -> str:
    """Fetch REA HTML. Only the ``cdp`` fetcher is supported (Kasada needs a
    real browser); the signature mirrors ``domain_cli.fetch_html`` so callers
    and the provider stay symmetric."""
    cache_file = cache_path_for(url, cache_dir)
    if not no_cache and cache_file.exists():
        return cache_file.read_text(encoding="utf-8")

    cfg = RateLimitConfig(rps=rps, burst=burst)
    bucket = build_bucket(cfg)
    if fetcher != "cdp":
        raise ValueError("realestate_cli only supports fetcher='cdp' (Kasada needs a real browser)")

    html = cdp_get(url, bucket=bucket, cfg=cfg, timeout_s=timeout_s, cdp_url=cdp_url)
    if has_structured_data(html):
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(html, encoding="utf-8")
    return html


# --------------------------------------------------------------------------- #
# ArgonautExchange extraction
# --------------------------------------------------------------------------- #
_ARGO_RE = re.compile(r"window\.ArgonautExchange\s*=\s*(\{.+?\});", re.DOTALL)


def soup_for(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _argonaut_blob(html: str) -> Optional[Dict[str, Any]]:
    """Return the parsed top-level ``window.ArgonautExchange`` object."""
    soup = soup_for(html)
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        if "window.ArgonautExchange" not in text:
            continue
        m = re.search(r"window\.ArgonautExchange\s*=\s*(\{.+\})\s*;", text, re.DOTALL)
        if not m:
            continue
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            # Greedy match may have swallowed a trailing statement; retry lazy.
            m2 = _ARGO_RE.search(text)
            if m2:
                try:
                    return json.loads(m2.group(1))
                except json.JSONDecodeError:
                    continue
    return None


def _unwrap_urql(argo: Dict[str, Any], experience_hint: str) -> List[Dict[str, Any]]:
    """Unwrap ``<experience>.urqlClientCache`` into the list of query ``data`` objects.

    REA stringifies twice: the exchange value holds ``urqlClientCache`` (a JSON
    string of ``{queryKey: {data: "<json string>"}}``). Returns every parsed
    ``data`` object so callers can pick the search/listing one robustly.
    """
    exp_key = None
    for key in argo:
        if experience_hint in key and isinstance(argo[key], dict) and "urqlClientCache" in argo[key]:
            exp_key = key
            break
    if exp_key is None:
        # Fall back to any experience carrying a urql cache.
        for key in argo:
            if isinstance(argo[key], dict) and "urqlClientCache" in argo[key]:
                exp_key = key
                break
    if exp_key is None:
        return []
    try:
        cache = json.loads(argo[exp_key]["urqlClientCache"])
    except (json.JSONDecodeError, TypeError):
        return []
    out: List[Dict[str, Any]] = []
    for entry in cache.values():
        if not isinstance(entry, dict) or "data" not in entry:
            continue
        try:
            out.append(json.loads(entry["data"]))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def _search_results_node(data_objs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for data in data_objs:
        for val in data.values():
            if isinstance(val, dict) and isinstance(val.get("results"), dict):
                return val["results"]
    return None


def _listing_node(data_objs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for data in data_objs:
        details = data.get("details") if isinstance(data, dict) else None
        if isinstance(details, dict) and isinstance(details.get("listing"), dict):
            return details["listing"]
        # Some payloads nest under a query key.
        for val in (data.values() if isinstance(data, dict) else []):
            if isinstance(val, dict) and isinstance(val.get("details"), dict):
                d = val["details"]
                if isinstance(d.get("listing"), dict):
                    return d["listing"]
    return None


# --------------------------------------------------------------------------- #
# Normalization -> shared listing schema (what db.upsert_listing consumes)
# --------------------------------------------------------------------------- #
_REA_IMG_SIZE = "1144x888-format=webp"  # concrete size for REA's {size} templates


def _resolve_img(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    return url.replace("{size}", _REA_IMG_SIZE)


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if m:
            f = float(m.group(0))
            return int(f) if f.is_integer() else f
    return None


def _price_range(price_display: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """Best-effort (from, to) integers out of a REA price string."""
    if not price_display:
        return None, None
    nums = [int(n.replace(",", "")) for n in re.findall(r"\$?\s*([\d,]{4,})", price_display)]
    nums = [n for n in nums if n >= 1000]
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums), max(nums)


def _address(raw: Dict[str, Any]) -> Dict[str, Any]:
    addr = raw.get("address") if isinstance(raw.get("address"), dict) else {}
    disp = addr.get("display") if isinstance(addr.get("display"), dict) else {}
    geo = disp.get("geocode") if isinstance(disp.get("geocode"), dict) else {}
    return {
        "display": disp.get("fullAddress"),
        "street": disp.get("shortAddress"),
        "suburb": addr.get("suburb"),
        "state": (addr.get("state") or "").upper() or None,
        "postcode": addr.get("postcode"),
        "lat": geo.get("latitude"),
        "lng": geo.get("longitude"),
    }


def _general(raw: Dict[str, Any], key: str) -> Optional[float]:
    gf = raw.get("generalFeatures") if isinstance(raw.get("generalFeatures"), dict) else {}
    node = gf.get(key) if isinstance(gf.get(key), dict) else {}
    return _num(node.get("value"))


def _land_sqm(raw: Dict[str, Any]) -> Optional[float]:
    sizes = raw.get("propertySizes") if isinstance(raw.get("propertySizes"), dict) else {}
    land = sizes.get("land") if isinstance(sizes.get("land"), dict) else {}
    unit = (land.get("sizeUnit") or {}).get("displayValue") if isinstance(land.get("sizeUnit"), dict) else None
    val = _num(land.get("displayValue"))
    if val is None:
        return None
    if unit and "ha" in str(unit).lower():
        return val * 10000
    return val


def _building_sqm(raw: Dict[str, Any]) -> Optional[float]:
    """Internal (building) floor area in m².

    REA publishes this as structured data; Domain does not, so before this the
    only source was a regex over the ad copy (``risk.py`` flags "Internal area
    unclear" whenever that misses). For an apartment buyer this is the number
    that makes $/m² and like-for-like comparison possible.
    """
    sizes = raw.get("propertySizes") if isinstance(raw.get("propertySizes"), dict) else {}
    building = sizes.get("building") if isinstance(sizes.get("building"), dict) else {}
    val = _num(building.get("displayValue"))
    if val is None:
        # Fall back to the "preferred" size only when it is the building measure.
        preferred = sizes.get("preferred") if isinstance(sizes.get("preferred"), dict) else {}
        if str(preferred.get("sizeType") or "").upper() == "BUILDING":
            size = preferred.get("size") if isinstance(preferred.get("size"), dict) else {}
            val = _num(size.get("displayValue"))
        if val is None:
            return None
        building = size if isinstance(size, dict) else {}
    unit = (building.get("sizeUnit") or {}).get("displayValue") if isinstance(building.get("sizeUnit"), dict) else None
    if unit and "ha" in str(unit).lower():
        return val * 10000
    return val


def _study(raw: Dict[str, Any]) -> Optional[float]:
    """Study count. A 1-bed + study reads very differently to a plain 1-bed."""
    return _general(raw, "studies")


def _auction_at(raw: Dict[str, Any]) -> Optional[str]:
    """ISO auction datetime, so Saturdays can actually be planned."""
    auction = raw.get("auction") if isinstance(raw.get("auction"), dict) else {}
    if not auction:
        return None
    dt = auction.get("dateTime") if isinstance(auction.get("dateTime"), dict) else {}
    return _scalar(dt.get("value") or dt) or _scalar(auction.get("dateTime"))


def _agency(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Agency block including REA's public rating — a read on who we're up against."""
    company = raw.get("listingCompany") if isinstance(raw.get("listingCompany"), dict) else {}
    name = company.get("name")
    if not name:
        return None
    ratings = company.get("ratingsReviews") if isinstance(company.get("ratingsReviews"), dict) else {}
    logo = company.get("media") if isinstance(company.get("media"), dict) else {}
    return {
        "name": name,
        "phone": company.get("businessPhone"),
        "rating": _num(ratings.get("avgRating")),
        "review_count": _num(ratings.get("totalReviews")),
        "logo": _resolve_img((logo.get("logo") or {}).get("templatedUrl") if isinstance(logo.get("logo"), dict) else None),
    }


def _agents(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for lister in raw.get("listers") or []:
        if not isinstance(lister, dict) or not lister.get("name"):
            continue
        phone = lister.get("phoneNumber")
        mobile = phone.get("display") if isinstance(phone, dict) else phone
        photo = lister.get("photo")
        photo_url = None
        if isinstance(photo, dict):
            photo_url = _resolve_img(photo.get("templatedUrl") or photo.get("url"))
        links = lister.get("_links") if isinstance(lister.get("_links"), dict) else {}
        profile = (links.get("canonical") or {}).get("href") if isinstance(links.get("canonical"), dict) else None
        lister_ratings = (
            lister.get("listerRatingsReviews")
            if isinstance(lister.get("listerRatingsReviews"), dict) else {}
        )
        out.append({
            "name": lister.get("name"),
            "email": lister.get("email"),
            "mobile": mobile,
            "landline": None,
            "profile_url": profile,
            "photo": photo_url,
            "agent_id": lister.get("id"),
            "job_title": lister.get("jobTitle"),
            "rating": _num(lister_ratings.get("avgRating")),
            "review_count": _num(lister_ratings.get("totalReviews")),
        })
    return out


def _images(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    media = raw.get("media") if isinstance(raw.get("media"), dict) else {}
    imgs = media.get("images") if isinstance(media.get("images"), list) else (raw.get("images") or [])
    for pos, img in enumerate(imgs):
        url = img.get("templatedUrl") if isinstance(img, dict) else img
        resolved = _resolve_img(url)
        if resolved:
            out.append({"url": resolved, "type": "photo", "category": None, "position": pos})
    base = len(out)
    for pos, fp in enumerate(media.get("floorplans") or []):
        url = fp.get("templatedUrl") if isinstance(fp, dict) else fp
        resolved = _resolve_img(url)
        if resolved:
            out.append({"url": resolved, "type": "floorplan", "category": None, "position": base + pos})

    # Video, 3D walkthroughs and the statement of information are real inspection
    # aids for shortlisting without leaving the house — the images table already
    # carries a media_type, so they belong beside the photos rather than in
    # raw_json where nothing can query them.
    for key, media_type in (("videos", "video"),
                            ("threeDimensionalTours", "tour_3d"),
                            ("threeDimensionalToursCompat", "tour_3d")):
        for item in media.get(key) or []:
            url = None
            if isinstance(item, dict):
                url = item.get("href") or item.get("url") or item.get("templatedUrl")
            elif isinstance(item, str):
                url = item
            if url and not any(o["url"] == url for o in out):
                out.append({"url": url, "type": media_type, "category": None, "position": len(out)})

    soi = media.get("statementOfInformation")
    soi_url = soi.get("href") or soi.get("url") if isinstance(soi, dict) else soi
    if isinstance(soi_url, str) and soi_url:
        out.append({"url": soi_url, "type": "statement_of_information",
                    "category": None, "position": len(out)})
    return out


def _inspections(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    insp = raw.get("inspections")
    items = insp if isinstance(insp, list) else (insp.get("inspections") if isinstance(insp, dict) else None)
    for item in items or []:
        if isinstance(item, dict):
            start = item.get("startTime") or item.get("start") or (item.get("openingDateTime") or {}).get("isoDate")
            end = item.get("endTime") or item.get("end") or (item.get("closingDateTime") or {}).get("isoDate")
            if start:
                out.append({"start": start, "end": end})
    return out


def _scalar(value: Any, *keys: str) -> Optional[str]:
    """Flatten REA's ``{display, __typename}`` wrapper objects to a plain string.

    REA wraps several leaf values (``dateSold``, sale method) in typed objects.
    The DB binds these columns as TEXT, so a dict here is a hard write failure.
    """
    if isinstance(value, dict):
        for key in (*keys, "isoDate", "value", "date", "display"):
            inner = value.get(key)
            if isinstance(inner, str) and inner:
                return inner
        return None
    return value or None


_REA_DATE_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})$")


def _iso_date(value: Any) -> Optional[str]:
    """Normalize REA's display dates (``07 Aug 2026``) to ISO, like Domain's.

    ``sales_report.py --days N`` and every comparable query sort on this column,
    so REA and Domain rows have to share one format.
    """
    text = _scalar(value)
    if not text:
        return None
    text = text.strip()
    match = _REA_DATE_RE.match(text)
    if match:
        day, month, year = match.groups()
        try:
            parsed = datetime.strptime(f"{day} {month[:3]} {year}", "%d %b %Y")
            return parsed.date().isoformat()
        except ValueError:
            return text
    return text


def _sold(raw: Dict[str, Any], price_display: Optional[str]) -> Optional[Dict[str, Any]]:
    sold_date = raw.get("dateSold") or raw.get("soldDate")
    sold_block = raw.get("soldDetails") if isinstance(raw.get("soldDetails"), dict) else {}
    if isinstance(sold_block, dict):
        sold_date = sold_date or sold_block.get("soldDate") or (sold_block.get("date"))
    sold_date = _iso_date(sold_date)
    if not sold_date and not (price_display and "sold" in price_display.lower()):
        return None
    lo, hi = _price_range(price_display)
    return {
        "price": (hi or lo),
        "date": sold_date,
        "method": _scalar(
            raw.get("saleMethod") or (sold_block.get("saleMethod") if isinstance(sold_block, dict) else None),
            "method",
        ),
    }


def normalize_listing_model(raw: Dict[str, Any], *, mode: str = "sale") -> Dict[str, Any]:
    """Map a REA listing object onto the shared normalized schema."""
    raw_id = raw.get("id") or raw.get("listingId")
    links = raw.get("_links") if isinstance(raw.get("_links"), dict) else {}
    canonical = (links.get("canonical") or {}).get("href") if isinstance(links.get("canonical"), dict) else None
    ptype = raw.get("propertyType")
    ptype_display = ptype.get("display") if isinstance(ptype, dict) else ptype
    price = raw.get("price") if isinstance(raw.get("price"), dict) else {}
    price_display = price.get("display") if isinstance(price, dict) else raw.get("price")
    price_from, price_to = _price_range(price_display)

    company = raw.get("listingCompany") if isinstance(raw.get("listingCompany"), dict) else {}

    return {
        "id": f"{REA_ID_PREFIX}{raw_id}",
        "listing_type": raw.get("channel") or MODE_CHANNEL.get(mode),
        "status": raw.get("status") or (raw.get("lifecycleStatus")),
        "url": canonical or listing_url_for_id(str(raw_id)),
        "headline": (raw.get("title") or None),
        "description": raw.get("description"),
        "price": price_display,
        "price_from": price_from,
        "price_to": price_to,
        "address": _address(raw),
        "beds": _general(raw, "bedrooms"),
        "baths": _general(raw, "bathrooms"),
        "cars": _general(raw, "parkingSpaces"),
        "property_type": ptype_display,
        "property_types": [ptype_display] if ptype_display else None,
        "land_area_sqm": _land_sqm(raw),
        # Structured extras REA publishes and Domain does not.
        "building_area_sqm": _building_sqm(raw),
        "study": _study(raw),
        "auction_at": _auction_at(raw),
        # Vendor's advertising tier (premiere/highlight/standard) — a read on
        # campaign spend, and so on vendor motivation and likely competition.
        "product_depth": raw.get("productDepth"),
        "badge": _scalar(raw.get("badge"), "label"),
        "enquiry_url": (links.get("submitEnquiry") or {}).get("href")
            if isinstance(links.get("submitEnquiry"), dict) else None,
        "agency": _agency(raw),
        "agents": _agents(raw),
        "images": _images(raw),
        "inspections": _inspections(raw),
        "auction": raw.get("auction"),
        "sold": _sold(raw, price_display) if mode == "sold" else None,
        "features": [
            f.get("displayLabel") or f.get("featureName")
            for f in (raw.get("propertyFeatures") or [])
            if isinstance(f, dict) and (f.get("displayLabel") or f.get("featureName"))
        ],
        # REA has no Domain-style tags block; leaving it absent means the shared
        # sold_status_from_tags() treats these as live, which is correct for a
        # /buy search (REA separates sold into its own channel).
    }


# A REA detail page carries a richer listing under details.listing but the same
# object shape, so normalization is shared.
normalize_listing_detail = normalize_listing_model


# --------------------------------------------------------------------------- #
# Payload extraction (mirrors domain_cli.extract_*)
# --------------------------------------------------------------------------- #
def extract_search_payload(html: str, *, source_url: Optional[str] = None,
                           limit: Optional[int] = None, mode: str = "sale") -> Dict[str, Any]:
    if detect_hard_denial(html):
        reason = classify_block(html)
        messages = {
            "kasada_challenge": (
                "realestate.com.au served a Kasada bot-challenge shell (KPSDK/ips.js). "
                "The genuine browser session is flagged for the search endpoint."
            ),
            "access_denied": "realestate.com.au returned a hard access-denied page.",
            "empty_shell": "realestate.com.au returned an un-hydrated shell (no listing data).",
            "browser_down": "No usable HTML returned -- the browser/CDP target is likely down.",
        }
        return {
            "url": source_url,
            "blocked_markers": [reason],
            "block_reason": reason,
            "error": messages.get(reason, "realestate.com.au returned an unusable page."),
            "search_result_count": None,
            "count": 0,
            "listing_ids": [],
            "listings": [],
            "events": [],
        }

    argo = _argonaut_blob(html)
    if not argo:
        return {
            "url": source_url,
            "blocked_markers": ["empty_shell"],
            "search_result_count": None,
            "count": 0,
            "listing_ids": [],
            "listings": [],
            "events": [],
        }

    data_objs = _unwrap_urql(argo, "search-experience")
    results = _search_results_node(data_objs) or {}
    items = results.get("exact", {}).get("items") if isinstance(results.get("exact"), dict) else None
    items = items or results.get("items") or []
    pagination = results.get("pagination") if isinstance(results.get("pagination"), dict) else {}
    total = results.get("totalResultsCount") or results.get("total") or pagination.get("totalResultsCount")

    listings: List[Dict[str, Any]] = []
    for item in items:
        raw = item.get("listing") if isinstance(item, dict) else None
        if isinstance(raw, dict) and (raw.get("id") or raw.get("listingId")):
            listings.append(normalize_listing_model(raw, mode=mode))
    if limit:
        listings = listings[:limit]

    ids = [l["id"] for l in listings]
    shell = not listings and total is None
    return {
        "url": source_url,
        "blocked_markers": (["empty_shell"] if shell else []),
        "search_result_count": total if isinstance(total, int) else None,
        "count": len(listings),
        "listing_ids": ids,
        "listings": listings,
        "events": [],
    }


def extract_listing_payload(html: str, *, source_url: Optional[str] = None,
                            listing_id: Optional[str] = None, mode: str = "sale") -> Dict[str, Any]:
    if detect_hard_denial(html):
        return {
            "url": source_url,
            "blocked_markers": [classify_block(html)],
            "error": "realestate.com.au returned a challenge/deny page with no structured data.",
            "listing": None,
            "events": [],
        }
    argo = _argonaut_blob(html)
    if not argo:
        return {"url": source_url, "blocked_markers": ["empty_shell"], "listing": None, "events": []}

    data_objs = _unwrap_urql(argo, "listing-experience")
    raw = _listing_node(data_objs)
    if not raw:
        # Some detail pages reuse the search experience cache.
        raw = _listing_node(_unwrap_urql(argo, "search-experience"))
    return {
        "url": source_url,
        "blocked_markers": [],
        "listing": normalize_listing_detail(raw, mode=mode) if raw else None,
        "events": [],
    }


# --------------------------------------------------------------------------- #
# CLI (mirrors domain_cli's fetch/search/listing verbs)
# --------------------------------------------------------------------------- #
def _html_from_args(args: argparse.Namespace) -> str:
    if getattr(args, "html", None):
        return Path(args.html).read_text(encoding="utf-8")
    url = getattr(args, "url", None)
    if not url and getattr(args, "id", None):
        url = listing_url_for_id(args.id)
    if not url:
        raise SystemExit("--url or --html is required")
    return fetch_html(url, no_cache=getattr(args, "no_cache", False))


def cmd_fetch(args: argparse.Namespace) -> int:
    html = _html_from_args(args)
    if args.out:
        Path(args.out).write_text(html, encoding="utf-8")
    print(f"len={len(html)} structured={has_structured_data(html)} blocked={classify_block(html)}", file=sys.stderr)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    url = args.url or build_search_url(
        mode=args.mode, suburbs=args.suburb, price_min=args.price_min, price_max=args.price_max,
        beds_min=args.beds_min, beds_max=args.beds_max, baths_min=args.baths_min,
        cars_min=args.cars_min, ptypes=args.ptype, sort=args.sort,
    )
    html = fetch_html(url, no_cache=args.no_cache) if not args.html else Path(args.html).read_text(encoding="utf-8")
    payload = extract_search_payload(html, source_url=url, limit=args.limit, mode=args.mode)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_listing(args: argparse.Namespace) -> int:
    url = args.url or listing_url_for_id(args.id)
    html = fetch_html(url, no_cache=args.no_cache) if not args.html else Path(args.html).read_text(encoding="utf-8")
    payload = extract_listing_payload(html, source_url=url, listing_id=args.id, mode=args.mode)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_url(args: argparse.Namespace) -> int:
    print(build_search_url(
        mode=args.mode, suburbs=args.suburb, price_min=args.price_min, price_max=args.price_max,
        beds_min=args.beds_min, beds_max=args.beds_max, baths_min=args.baths_min,
        cars_min=args.cars_min, ptypes=args.ptype, sort=args.sort,
    ))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="realestate_cli.py", description="realestate.com.au fetcher (Domain fallback)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_filters(sp):
        sp.add_argument("--mode", default="sale", choices=SEARCH_MODES)
        sp.add_argument("--suburb", action="append", help="e.g. 'Zetland NSW 2017' (repeatable)")
        sp.add_argument("--price-min", type=int)
        sp.add_argument("--price-max", type=int)
        sp.add_argument("--beds-min", type=int)
        sp.add_argument("--beds-max", type=int)
        sp.add_argument("--baths-min", type=int)
        sp.add_argument("--cars-min", type=int)
        sp.add_argument("--ptype", action="append")
        sp.add_argument("--sort")

    fp = sub.add_parser("fetch"); fp.add_argument("--url"); fp.add_argument("--id"); fp.add_argument("--html"); fp.add_argument("--out"); fp.add_argument("--no-cache", action="store_true"); fp.set_defaults(func=cmd_fetch)
    sp = sub.add_parser("search"); add_filters(sp); sp.add_argument("--url"); sp.add_argument("--html"); sp.add_argument("--limit", type=int); sp.add_argument("--no-cache", action="store_true"); sp.set_defaults(func=cmd_search)
    lp = sub.add_parser("listing"); lp.add_argument("--id"); lp.add_argument("--url"); lp.add_argument("--html"); lp.add_argument("--mode", default="sale", choices=SEARCH_MODES); lp.add_argument("--no-cache", action="store_true"); lp.set_defaults(func=cmd_listing)
    up = sub.add_parser("url"); add_filters(up); up.set_defaults(func=cmd_url)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
