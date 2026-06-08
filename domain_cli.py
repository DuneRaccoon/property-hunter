#!/usr/bin/env python3
"""Domain.com.au structured-data fetcher.

The useful bit: we do not scrape rendered cards. Domain ships enough JSON in
blocked HTML to extract IDs, listing URLs, prices, addresses and inspections.
Playwright is only used to fetch browser-shaped HTML.
"""

from __future__ import annotations

import argparse
import atexit
import shutil
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    PlaywrightTimeoutError = None  # type: ignore
    sync_playwright = None  # type: ignore

try:
    from leaky_bucket_py import LeakyBucket  # type: ignore
except Exception:  # pragma: no cover
    LeakyBucket = None  # type: ignore


DEFAULT_UA = os.getenv(
    "DOMAIN_UA",
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
DEFAULT_PROFILE_DIR = Path(os.getenv("DOMAIN_PROFILE_DIR", ".domain-browser-profile"))
DEFAULT_CACHE_DIR = Path(".cache/domain/html")
# CDP endpoint of an already-running real browser (the OpenClaw-managed Chromium).
# Fetching through that genuine session is what reliably loads Domain.
DEFAULT_CDP_URL = os.getenv("DOMAIN_CDP_URL", "http://127.0.0.1:18800")
SYSTEM_PROFILE_DIR = Path(os.getenv("DOMAIN_SYSTEM_PROFILE_DIR", ".domain-browser-profile-system"))
DIGITALDATA_RE = re.compile(r"var\s+digitalData\s*=\s*(\{.*?\});", re.DOTALL)
BLOCK_MARKERS = (
    "are you human",
    "verify you are a human",
    "access denied",
    "reference&#32;&#35;",
    "pardon our interruption",
    "sec-if-cpt-container",
    "powered and protected by akamai",
    "powered and protected by privacy",
)


@dataclass
class RateLimitConfig:
    rps: float = 0.35
    burst: int = 1
    jitter_min_s: float = 0.35
    jitter_max_s: float = 1.25


class SimpleLeakyBucket:
    """Fallback leaky bucket if leaky-bucket-py changes or disappears."""

    def __init__(self, rps: float, burst: int):
        self.rps = max(0.001, rps)
        self.capacity = max(1, int(burst))
        self.tokens = self.capacity
        self.last = time.monotonic()

    def acquire(self):
        while True:
            now = time.monotonic()
            elapsed = now - self.last
            self.last = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rps)
            if self.tokens >= 1:
                self.tokens -= 1
                return
            time.sleep(max(0.05, (1 - self.tokens) / self.rps))


def build_bucket(cfg: RateLimitConfig):
    if LeakyBucket is None:
        return SimpleLeakyBucket(cfg.rps, cfg.burst)
    try:
        return LeakyBucket(max_rate=cfg.burst, time_period=max(1, int(1 / cfg.rps)), capacity=cfg.burst)  # type: ignore
    except TypeError:
        return SimpleLeakyBucket(cfg.rps, cfg.burst)


def polite_pause(bucket, cfg: RateLimitConfig):
    bucket.acquire()
    time.sleep(random.uniform(cfg.jitter_min_s, cfg.jitter_max_s))


def cache_path_for(url: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.html"


def listing_url_for_id(listing_id: str) -> str:
    """Domain redirects /<id> straight to the canonical listing page."""
    return f"https://www.domain.com.au/{str(listing_id).strip()}"


SEARCH_MODES = ("sale", "rent", "sold")
# Domain uses a different path segment for sold listings.
MODE_PATH = {"sale": "sale", "rent": "rent", "sold": "sold-listings"}


def slugify_locality(value: str) -> str:
    """Turn 'Zetland NSW 2017' into Domain's 'zetland-nsw-2017' slug.

    Already-slugged values pass through unchanged.
    """
    value = value.strip().lower()
    value = re.sub(r"[,]", " ", value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value


def _range_param(low: Optional[int], high: Optional[int]) -> Optional[str]:
    if low is None and high is None:
        return None
    return f"{low if low is not None else 'any'}-{high if high is not None else 'any'}"


def build_search_url(
    *,
    mode: str = "sale",
    suburbs: Optional[List[str]] = None,
    region: Optional[str] = None,
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
    beds_min: Optional[int] = None,
    beds_max: Optional[int] = None,
    baths_min: Optional[int] = None,
    cars_min: Optional[int] = None,
    ptypes: Optional[List[str]] = None,
    exclude_under_offer: bool = False,
    features: Optional[List[str]] = None,
    keywords: Optional[str] = None,
    sort: Optional[str] = None,
    page: Optional[int] = None,
) -> str:
    """Build a Domain search URL from filters.

    A single locality goes in the path (Domain's canonical form); multiple
    localities go in a ``suburb=a,b,c`` query param.
    """
    if mode not in SEARCH_MODES:
        raise ValueError(f"mode must be one of {SEARCH_MODES}")

    path_mode = MODE_PATH[mode]
    slugs = [slugify_locality(s) for s in (suburbs or []) if s.strip()]
    base = f"https://www.domain.com.au/{path_mode}/"
    params: List[str] = []

    if region:
        base = f"https://www.domain.com.au/{path_mode}/{slugify_locality(region)}/"
    elif len(slugs) == 1:
        base = f"https://www.domain.com.au/{path_mode}/{slugs[0]}/"
    elif len(slugs) > 1:
        params.append("suburb=" + ",".join(slugs))

    price = _range_param(price_min, price_max)
    if price:
        params.append(f"price={price}")
    beds = _range_param(beds_min, beds_max)
    if beds:
        params.append(f"bedrooms={beds}")
    if baths_min is not None:
        params.append(f"bathrooms={baths_min}-any")
    if cars_min is not None:
        params.append(f"carspaces={cars_min}-any")
    if ptypes:
        params.append("ptype=" + ",".join(p.strip() for p in ptypes if p.strip()))
    if exclude_under_offer:
        params.append("excludeunderoffer=1")
    if features:
        params.append("features=" + ",".join(f.strip() for f in features if f.strip()))
    if keywords:
        params.append("keywords=" + keywords.replace(" ", "+"))
    if sort:
        params.append(f"sort={sort}")
    if page and page > 1:
        params.append(f"page={page}")

    return base + ("?" + "&".join(params) if params else "")


def parse_proxy(proxy: Optional[str]) -> Optional[Dict[str, str]]:
    """Split a proxy URL into Playwright's server/username/password form.

    Accepts the standard ``scheme://user:pass@host:port`` shape. ``requests``
    takes the URL as-is, but Playwright wants credentials separated out.
    """
    if not proxy:
        return None
    parts = urlsplit(proxy)
    server = urlunsplit((parts.scheme or "http", parts.hostname + (f":{parts.port}" if parts.port else ""), "", "", ""))
    out: Dict[str, str] = {"server": server}
    if parts.username:
        out["username"] = parts.username
    if parts.password:
        out["password"] = parts.password
    return out


def http_get(url: str, *, ua: str, bucket, cfg: RateLimitConfig, timeout_s: int = 45, proxy: Optional[str] = None) -> str:
    polite_pause(bucket, cfg)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-AU,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None
    r = requests.get(url, headers=headers, timeout=timeout_s, proxies=proxies)
    r.raise_for_status()
    return r.text


def playwright_get(
    url: str,
    *,
    ua: str,
    bucket,
    cfg: RateLimitConfig,
    timeout_s: int = 60,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    headed: bool = False,
    block_assets: Optional[bool] = None,
    proxy: Optional[str] = None,
) -> str:
    if sync_playwright is None:
        raise RuntimeError("Playwright is not installed. Run: ./venv/bin/pip install playwright")

    # Headed Chromium needs an X/Wayland display. Over SSH, $DISPLAY is usually
    # unset, which makes the browser exit immediately. Default to the Pi's local
    # session (:0) so headed fetches work regardless of how the script is invoked.
    if headed and not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = os.getenv("DOMAIN_DISPLAY", ":0")

    polite_pause(bucket, cfg)
    block_assets = (not headed) if block_assets is None else block_assets
    executable_candidates = _chrome_candidates()
    last_error: Optional[Exception] = None

    with sync_playwright() as p:
        for executable_path, candidate_profile in executable_candidates:
            candidate_profile.mkdir(parents=True, exist_ok=True)
            launch_kwargs: Dict[str, Any] = dict(
                user_data_dir=str(candidate_profile),
                headless=not headed,
                locale="en-AU",
                timezone_id="Australia/Sydney",
                viewport={"width": 1365, "height": 900},
                slow_mo=80 if headed else 0,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--lang=en-AU,en",
                    "--start-maximized",
                ],
            )
            if ua:
                launch_kwargs["user_agent"] = ua
            if executable_path:
                launch_kwargs["executable_path"] = executable_path
            proxy_cfg = parse_proxy(proxy)
            if proxy_cfg:
                launch_kwargs["proxy"] = proxy_cfg
            try:
                return _playwright_get_once(p, launch_kwargs, url, timeout_s, headed, block_assets)
            except Exception as exc:
                last_error = exc
                continue
    if last_error:
        raise last_error
    raise RuntimeError("No Chromium executable candidates available")


def _chrome_candidates() -> List[tuple[Optional[str], Path]]:
    forced = os.getenv("DOMAIN_CHROME_EXECUTABLE")
    if forced:
        return [(forced, SYSTEM_PROFILE_DIR)]
    candidates: List[tuple[Optional[str], Path]] = []
    system = shutil.which("chromium") or shutil.which("google-chrome")
    if system:
        candidates.append((system, SYSTEM_PROFILE_DIR))
    candidates.append((None, DEFAULT_PROFILE_DIR))
    return candidates


def _playwright_get_once(p, launch_kwargs: Dict[str, Any], url: str, timeout_s: int, headed: bool, block_assets: bool) -> str:
    context = p.chromium.launch_persistent_context(**launch_kwargs)
    try:
        context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = window.chrome || { runtime: {} };
            Object.defineProperty(navigator, 'languages', { get: () => ['en-AU', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            """
        )
        if block_assets:
            context.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in {"image", "font", "media"}
                else route.continue_(),
            )
        page = context.new_page()
        if headed:
            try:
                page.goto("https://www.domain.com.au/", wait_until="domcontentloaded", timeout=timeout_s * 1000)
                _human_dwell(page, min_s=2.0, max_s=4.0)
            except Exception:
                pass
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
        if headed:
            _human_dwell(page, min_s=8.0, max_s=14.0)
            deadline = time.monotonic() + max(20, min(timeout_s, 90))
            html = page.content()
            while detect_hard_denial(html) and time.monotonic() < deadline:
                _human_dwell(page, min_s=4.0, max_s=8.0)
                try:
                    page.reload(wait_until="domcontentloaded", timeout=timeout_s * 1000)
                except Exception:
                    pass
                html = page.content()
            return html
        page.wait_for_timeout(random.randint(500, 1300))
        return page.content()
    except Exception as exc:
        if PlaywrightTimeoutError is not None and isinstance(exc, PlaywrightTimeoutError):
            return page.content()
        raise
    finally:
        context.close()


def _human_dwell(page, *, min_s: float, max_s: float) -> None:
    total_ms = int(random.uniform(min_s, max_s) * 1000)
    end_at = time.monotonic() + total_ms / 1000
    while time.monotonic() < end_at:
        try:
            page.mouse.move(random.randint(120, 1200), random.randint(140, 760), steps=random.randint(8, 22))
            if random.random() < 0.55:
                page.mouse.wheel(0, random.randint(120, 520))
        except Exception:
            pass
        page.wait_for_timeout(random.randint(550, 1400))


def _cdp_reachable(cdp_url: str, timeout_s: float = 3.0) -> bool:
    try:
        requests.get(cdp_url.rstrip("/") + "/json/version", timeout=timeout_s)
        return True
    except Exception:
        return False


def ensure_browser(cdp_url: str = DEFAULT_CDP_URL, *, wait_s: int = 40) -> None:
    """Best-effort: make sure the OpenClaw-managed browser is up for CDP.

    If the CDP endpoint is already reachable, do nothing. Otherwise, for a local
    endpoint, run ``openclaw browser start`` (a no-op if already running) and poll
    until the endpoint answers. Remote endpoints are left untouched.
    """
    if _cdp_reachable(cdp_url):
        return
    host = (urlsplit(cdp_url).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return
    openclaw = shutil.which("openclaw")
    if not openclaw:
        return
    try:
        subprocess.run([openclaw, "browser", "start"], timeout=90,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if _cdp_reachable(cdp_url):
            return
        time.sleep(2)


STATE_CODES = {"nsw", "vic", "qld", "wa", "sa", "tas", "act", "nt"}
TYPEAHEAD_INPUT = "#fe-pa-domain-home-typeahead-input"
TYPEAHEAD_ITEM_PREFIX = "fe-pa-domain-home-typeahead-item-"
SEARCH_BUTTON = "button[data-testid='search-button']"
DEFAULT_WARMUP = ("Sydney", "NSW")

# Akamai validates the session only after a genuine in-site search *gesture*; a
# cold goto() to a /sale/, /rent/, /sold/ or listing URL trips the bot challenge.
# Once a tab has done one gesture-driven search, plain goto() to any filtered/
# paginated/detail URL passes -- but only in that same tab (see cdp_get).


def slug_to_typeahead(slug: str) -> tuple[str, Optional[str]]:
    """'zetland-nsw-2017' -> ('Zetland', 'NSW'). Drops trailing state/postcode."""
    tokens = [t for t in slug.strip().lower().split("-") if t]
    state: Optional[str] = None
    if tokens and re.fullmatch(r"\d{3,4}", tokens[-1]):
        tokens.pop()
    if tokens and tokens[-1] in STATE_CODES:
        state = tokens.pop().upper()
    suburb = " ".join(t.capitalize() for t in tokens) or slug
    return suburb, state


def warmup_locality_from_url(url: str) -> tuple[str, Optional[str]]:
    """Pick a suburb to type into the typeahead so the warm-up search is relevant.

    Derives it from the target search URL's path slug or ?suburb= param; falls
    back to a generic locality for listing-detail URLs (no locality in them).
    """
    parts = urlsplit(url)
    query = dict(
        kv.split("=", 1) if "=" in kv else (kv, "")
        for kv in parts.query.split("&")
        if kv
    )
    if query.get("suburb"):
        first = query["suburb"].split(",")[0]
        if first:
            return slug_to_typeahead(first)
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) >= 2 and segs[0] in {"sale", "rent", "sold", "sold-listings"}:
        return slug_to_typeahead(segs[1])
    return DEFAULT_WARMUP


SEARCH_BUTTON_SELECTORS = (
    "button[data-testid='search-button']",
    "[data-testid='fe-co-search-controls-base-search-button']",
    "button[type='submit']",
)
LISTING_CARD_SELECTOR = "[data-testid*='listing-card']"


def _count_listing_cards(page) -> int:
    try:
        return int(page.evaluate(
            "document.querySelectorAll(\"%s\").length" % LISTING_CARD_SELECTOR
        ))
    except Exception:
        return 0


def _gesture_warmup(page, suburb: str, state: Optional[str], timeout_s: int) -> bool:
    """Drive the homepage search UI like a human to validate the Akamai session.

    Akamai serves the real (hydrated) results only to a session that performed a
    genuine in-site search: homepage -> type a suburb -> pick the typeahead
    suggestion -> submit. A cold ``goto()`` to a /sale/ URL gets the empty SPA
    shell. Returns True only once real listing cards have rendered on the landed
    results page (the signal that the session is trusted and hydrated).
    """
    try:
        page.goto("https://www.domain.com.au/", wait_until="domcontentloaded", timeout=min(timeout_s, 25) * 1000)
        page.wait_for_timeout(random.randint(1000, 2000))
        page.wait_for_selector(TYPEAHEAD_INPUT, timeout=min(timeout_s, 15) * 1000)
        page.click(TYPEAHEAD_INPUT)
        page.fill(TYPEAHEAD_INPUT, "")
        for ch in suburb:
            page.type(TYPEAHEAD_INPUT, ch, delay=random.randint(70, 150))

        # Wait for the autocomplete suggestions to actually render. Landing via a
        # real suggestion click (-> /sale/?suburb=...) is what Akamai trusts; the
        # free-text Enter fallback (-> /sale/?terms=...) is far more often
        # challenged, so we only fall back if suggestions never appear.
        options: List[Dict[str, str]] = []
        sug_deadline = time.monotonic() + min(timeout_s, 12)
        while time.monotonic() < sug_deadline:
            options = page.evaluate(
                "function(p){var o=[];var n=document.querySelectorAll(\"[id^='\"+p+\"']\");"
                "for(var i=0;i<n.length;i++)o.push({id:n[i].id,text:(n[i].innerText||'').replace(/\\n/g,' ').trim()});"
                "return o;}",
                TYPEAHEAD_ITEM_PREFIX,
            )
            if options:
                break
            page.wait_for_timeout(400)

        target = None
        sub_l = suburb.lower()
        st_l = (state or "").lower()
        for opt in options:
            text = opt.get("text", "").lower()
            if sub_l in text and (not st_l or st_l in text):
                target = opt.get("id")
                break
        if not target and options:
            target = options[0].get("id")
        if target:
            page.wait_for_timeout(random.randint(300, 700))
            page.click("#" + target)
            page.wait_for_timeout(random.randint(600, 1200))

        clicked = False
        for sel in SEARCH_BUTTON_SELECTORS:
            if page.query_selector(sel):
                page.click(sel)
                clicked = True
                break
        if not clicked:
            page.keyboard.press("Enter")
        page.wait_for_load_state("domcontentloaded", timeout=min(timeout_s, 30) * 1000)
        # The landed page is the real results page; wait for cards to hydrate.
        return _wait_until_ready(page, "https://www.domain.com.au/sale/", timeout_s) and not detect_hard_denial(page.content())
    except Exception:
        return False


def _is_search_url(url: str) -> bool:
    path = urlsplit(url).path.lower().rstrip("/")
    return any(path == "/" + m or path.startswith("/" + m + "/")
               for m in ("sale", "rent", "sold", "sold-listings"))


def _detail_ready(html: str) -> bool:
    """A listing detail page is hydrated once its GraphQL listing block exists."""
    try:
        component = component_props(extract_next_data(html))
    except Exception:
        return False
    if not isinstance(component, dict):
        return False
    if component.get("listingId") and isinstance(component.get("rootGraphQuery"), dict):
        return isinstance(component["rootGraphQuery"].get("listingByIdV2"), dict)
    return bool(find_listing_models(component))


def _wait_until_ready(page, url: str, timeout_s: int) -> bool:
    """Block until the warmed page has actually hydrated its listing data.

    For search pages that means rendered listing cards (the SSR ``listingsMap``
    is populated at the same time); for detail pages it means the GraphQL listing
    block is present. ``has_structured_data`` is useless here because the empty
    SPA shell already carries ``__NEXT_DATA__`` -- only real hydrated content
    counts. Returns False on timeout or a hard denial.
    """
    search = _is_search_url(url)
    deadline = time.monotonic() + max(8, min(timeout_s, 40))
    while time.monotonic() < deadline:
        try:
            html = page.content()
        except Exception:
            page.wait_for_timeout(400)
            continue
        if detect_hard_denial(html):
            return False
        if search:
            if _count_listing_cards(page) > 0:
                return True
        else:
            if _detail_ready(html):
                return True
        page.wait_for_timeout(700)
    return False


# Persistent warmed CDP session, reused across all fetches in one process. The
# winning Akamai bypass needs the *same* tab that performed the search gesture:
# a fresh page/context (even sharing cookies) gets re-challenged, while plain
# goto() inside the warmed tab loads any filtered/paginated/detail URL cleanly.
_CDP_PW = None
_CDP_BROWSER = None
_CDP_PAGE = None
_CDP_WARMED = False


def _teardown_cdp() -> None:
    global _CDP_PW, _CDP_BROWSER, _CDP_PAGE, _CDP_WARMED
    for closer in (
        lambda: _CDP_PAGE.close() if _CDP_PAGE else None,
        lambda: _CDP_PW.stop() if _CDP_PW else None,
    ):
        try:
            closer()
        except Exception:
            pass
    _CDP_PW = _CDP_BROWSER = _CDP_PAGE = None
    _CDP_WARMED = False


def _warmed_page(cdp_url: str, timeout_s: int):
    """Return the persistent, gesture-warmed CDP page, (re)creating it as needed."""
    global _CDP_PW, _CDP_BROWSER, _CDP_PAGE, _CDP_WARMED
    if _CDP_PAGE is not None:
        try:
            if not _CDP_PAGE.is_closed():
                return _CDP_PAGE
        except Exception:
            pass
        _CDP_PAGE = None
        _CDP_WARMED = False

    ensure_browser(cdp_url)
    if _CDP_PW is None:
        _CDP_PW = sync_playwright().start()
        atexit.register(_teardown_cdp)
    _CDP_BROWSER = _CDP_PW.chromium.connect_over_cdp(cdp_url, timeout=min(timeout_s, 15) * 1000)
    contexts = _CDP_BROWSER.contexts or []
    if not contexts:
        raise RuntimeError(f"No browser context available at {cdp_url}. Is the browser running?")
    page = contexts[0].new_page()
    page.set_default_timeout(min(timeout_s, 20) * 1000)
    page.set_default_navigation_timeout(min(timeout_s, 30) * 1000)
    _CDP_PAGE = page
    _CDP_WARMED = False
    return page


def cdp_get(
    url: str,
    *,
    bucket,
    cfg: RateLimitConfig,
    timeout_s: int = 60,
    cdp_url: str = DEFAULT_CDP_URL,
) -> str:
    """Fetch a Domain page through the genuine OpenClaw-managed Chromium via CDP.

    Akamai only trusts a session after a real in-site search *gesture* (homepage
    -> typeahead -> submit), and only in the very tab that performed it. So we
    keep one persistent warmed tab for the whole process: warm it once with the
    gesture, then plain ``goto()`` every target (filtered/paginated/detail) URL
    in that same tab and wait for the data to actually hydrate before reading.
    """
    global _CDP_WARMED
    if sync_playwright is None:
        raise RuntimeError("Playwright is not installed. Run: ./venv/bin/pip install playwright")

    polite_pause(bucket, cfg)
    warm_suburb, warm_state = warmup_locality_from_url(url)

    html = ""
    for attempt in range(2):
        page = _warmed_page(cdp_url, timeout_s)
        if not _CDP_WARMED:
            for _ in range(3):
                if _gesture_warmup(page, warm_suburb, warm_state, timeout_s):
                    _CDP_WARMED = True
                    break
                page.wait_for_timeout(random.randint(800, 1600))
            if not _CDP_WARMED:
                # Could not establish a trusted session this round; surface the
                # last (challenged) HTML so callers treat it as blocked.
                try:
                    return page.content()
                except Exception:
                    return ""

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=min(timeout_s, 30) * 1000)
        except Exception:
            pass
        if _wait_until_ready(page, url, timeout_s):
            return page.content()

        html = page.content()
        if not detect_hard_denial(html) and (has_structured_data(html) and not _is_search_url(url)):
            # Detail page that genuinely lacks a GraphQL block but isn't blocked.
            return html
        # Session lapsed / got challenged -> drop the tab and re-warm once.
        _CDP_WARMED = False
        _teardown_cdp()
    return html


def fetch_html(
    url: str,
    *,
    fetcher: str = "playwright",
    ua: str = DEFAULT_UA,
    rps: float = 0.35,
    burst: int = 1,
    timeout_s: int = 60,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    no_cache: bool = False,
    headed: bool = False,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    proxy: Optional[str] = None,
    cdp_url: str = DEFAULT_CDP_URL,
    retries: int = 3,
    retry_backoff_s: float = 4.0,
) -> str:
    cache_file = cache_path_for(url, cache_dir)
    if not no_cache and cache_file.exists():
        return cache_file.read_text(encoding="utf-8")

    cfg = RateLimitConfig(rps=rps, burst=burst)
    bucket = build_bucket(cfg)

    def _one_attempt() -> str:
        if fetcher == "http":
            return http_get(url, ua=ua, bucket=bucket, cfg=cfg, timeout_s=timeout_s, proxy=proxy)
        if fetcher == "cdp":
            return cdp_get(url, bucket=bucket, cfg=cfg, timeout_s=timeout_s, cdp_url=cdp_url)
        if fetcher == "playwright":
            return playwright_get(
                url,
                ua=ua,
                bucket=bucket,
                cfg=cfg,
                timeout_s=timeout_s,
                profile_dir=profile_dir,
                headed=headed,
                proxy=proxy,
            )
        raise ValueError(f"Unknown fetcher: {fetcher}")

    html = ""
    attempts = 1 if headed else max(1, retries)
    for attempt in range(attempts):
        html = _one_attempt()
        if has_structured_data(html):
            break
        # Blocked/challenge page: back off (escalating) before retrying.
        if attempt < attempts - 1:
            time.sleep(retry_backoff_s * (attempt + 1) + random.uniform(0, 1.5))

    if has_structured_data(html):
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(html, encoding="utf-8")
    return html


def has_structured_data(html: str) -> bool:
    """True when Domain returned a real page (parseable), not a block/challenge."""
    lower = html.lower()
    return "__next_data__" in lower or "digitaldata" in lower


def detect_blocked(html: str) -> bool:
    lower = html.lower()
    return any(marker in lower for marker in BLOCK_MARKERS)


def detect_hard_denial(html: str) -> bool:
    """Tiny block/challenge page with no usable structured data."""
    if has_structured_data(html):
        return False
    lower = html.lower()
    return (
        "access denied" in lower
        or "sec-if-cpt-container" in lower
        or "powered and protected by akamai" in lower
        or "powered and protected by privacy" in lower
        or len(html) < 20000
        or any(marker in lower for marker in BLOCK_MARKERS)
    )


def soup_for(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def extract_digital_data(html: str) -> Dict[str, Any]:
    m = DIGITALDATA_RE.search(html)
    if m:
        return json.loads(m.group(1))

    next_data = extract_next_data(html) or {}
    for path in (
        ("props", "pageProps", "componentProps", "digitalData"),
        ("props", "pageProps", "layoutProps", "digitalData"),
    ):
        node: Any = next_data
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            return node

    raise ValueError("Could not find digitalData in HTML")


def extract_next_data(html: str) -> Optional[Dict[str, Any]]:
    script = soup_for(html).find("script", id="__NEXT_DATA__")
    if not script:
        return None
    text = script.string or script.get_text()
    return json.loads(text)


def extract_json_ld(html: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for script in soup_for(html).find_all("script", type="application/ld+json"):
        text = script.string or script.get_text()
        if not text.strip():
            continue
        parsed = json.loads(text)
        if isinstance(parsed, list):
            items.extend(x for x in parsed if isinstance(x, dict))
        elif isinstance(parsed, dict):
            items.append(parsed)
    return items


def extract_listing_ids(digital_data: Dict[str, Any], next_data: Optional[Dict[str, Any]] = None) -> List[str]:
    ids: List[str] = []
    page = digital_data.get("page") or {}
    page_info = page.get("pageInfo") or {}
    search = page_info.get("search") or {}

    for key, val in search.items():
        if isinstance(key, str) and key.startswith("resultsRecords") and isinstance(val, str):
            ids.extend(p.strip() for p in val.split(",") if p.strip())

    component = component_props(next_data)
    result_ids = component.get("listingSearchResultIds") if isinstance(component, dict) else None
    if isinstance(result_ids, list):
        ids.extend(str(x) for x in result_ids)

    return dedupe_ids(ids)


def dedupe_ids(ids: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in ids:
        listing_id = str(item)
        if listing_id in seen or not re.fullmatch(r"\d{4,}", listing_id):
            continue
        seen.add(listing_id)
        out.append(listing_id)
    return out


def component_props(next_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(next_data, dict):
        return {}
    node: Any = next_data
    for key in ("props", "pageProps", "componentProps"):
        node = node.get(key) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def search_result_count(digital_data: Dict[str, Any], next_data: Optional[Dict[str, Any]]) -> Optional[int]:
    search = (((digital_data.get("page") or {}).get("pageInfo") or {}).get("search") or {})
    count = search.get("searchResultCount")
    if isinstance(count, int):
        return count
    total = component_props(next_data).get("totalListings")
    return total if isinstance(total, int) else None


def absolute_domain_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    return urljoin("https://www.domain.com.au", url)


def normalize_listing_model(listing_id: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    model = raw.get("listingModel") if "listingModel" in raw else raw
    if not isinstance(model, dict):
        model = {}
    address = model.get("address") if isinstance(model.get("address"), dict) else {}
    features = model.get("features") if isinstance(model.get("features"), dict) else {}
    branding = model.get("branding") if isinstance(model.get("branding"), dict) else {}
    agents = []
    for agent in branding.get("agents") or []:
        if isinstance(agent, dict):
            agents.append(
                {
                    "name": agent.get("agentName") or agent.get("name"),
                    "photo": agent.get("agentPhoto"),
                }
            )

    return {
        "id": str(raw.get("id") or listing_id),
        "listing_type": raw.get("listingType") or model.get("listingType"),
        "url": absolute_domain_url(model.get("url")),
        "price": model.get("price") or model.get("displayPrice") or model.get("displaySearchPriceRange"),
        "address": address,
        "beds": features.get("beds"),
        "baths": features.get("baths"),
        "cars": features.get("parking") or features.get("cars"),
        "property_type": features.get("propertyTypeFormatted") or features.get("propertyType"),
        "land_size": features.get("landSize"),
        "land_unit": features.get("landUnit"),
        "inspection": model.get("inspection"),
        "auction": model.get("auction"),
        "agents": agents,
        "agency_id": branding.get("agencyId"),
        "promo_type": model.get("promoType"),
        "tags": model.get("tags"),
        "images": (model.get("images") or [])[:5],
    }


def listing_events_from_json_ld(json_ld: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    events = []
    for item in json_ld:
        if item.get("@type") != "Event":
            continue
        loc = item.get("location") if isinstance(item.get("location"), dict) else {}
        events.append(
            {
                "type": item.get("name"),
                "description": item.get("description"),
                "url": item.get("url"),
                "start_date": item.get("startDate"),
                "address": loc.get("address"),
                "geo": loc.get("geo"),
                "image": item.get("image"),
            }
        )
    return events


def extract_search_payload(html: str, *, source_url: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
    if detect_hard_denial(html):
        return {
            "url": source_url,
            "blocked_markers": True,
            "error": "Domain returned a hard access-denied page with no embedded structured data.",
            "search_result_count": None,
            "count": 0,
            "listing_ids": [],
            "listings": [],
            "events": [],
        }

    next_data = extract_next_data(html)
    # Sale/rent pages carry a top-level digitalData blob; sold-listings pages do
    # not — they only expose componentProps. Both downstream helpers already
    # fall back to componentProps, so an absent digitalData is non-fatal.
    try:
        digital = extract_digital_data(html)
    except ValueError:
        digital = {}
    ids = extract_listing_ids(digital, next_data)
    if limit:
        ids = ids[:limit]

    component = component_props(next_data)
    listings_map = component.get("listingsMap") if isinstance(component.get("listingsMap"), dict) else {}
    listings = []
    for listing_id in ids:
        raw = listings_map.get(str(listing_id))
        if isinstance(raw, dict):
            listings.append(normalize_listing_model(listing_id, raw))

    json_ld = extract_json_ld(html)
    result_count = search_result_count(digital, next_data)
    # The empty SPA shell (a soft Akamai challenge / un-hydrated goto) still
    # carries __NEXT_DATA__ but no listingsMap and no result count. Treat that as
    # blocked so callers (hunt_runner) never mistake it for a genuine 0 results.
    shell = not ids and not listings_map and not result_count
    return {
        "url": source_url,
        "blocked_markers": detect_blocked(html) or shell,
        "search_result_count": result_count,
        "count": len(ids),
        "listing_ids": ids,
        "listings": listings,
        "events": listing_events_from_json_ld(json_ld),
    }


def find_listing_models(obj: Any, found: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    if found is None:
        found = []
    if isinstance(obj, dict):
        if isinstance(obj.get("listingModel"), dict):
            found.append(obj)
        for val in obj.values():
            find_listing_models(val, found)
    elif isinstance(obj, list):
        for val in obj:
            find_listing_models(val, found)
    return found


def extract_media(v2: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull every image off a detail-page listing, tagged by type.

    Domain carries the gallery at several resolutions (small/medium/large/
    thumbnail Media). We prefer ``largeMedia`` (fit-in 1920x1080) and fall back
    through the others if a tier is missing. Each entry is tagged with its
    ``type`` ("photo", "floorplan", and occasionally "video"/"virtualtour") so
    callers can split property shots from floorplans. Order is preserved.
    """
    tiers = ("largeMedia", "mediumMedia", "smallMedia", "thumbnailMedia")
    media: List[Dict[str, Any]] = []
    for tier in tiers:
        items = v2.get(tier)
        if isinstance(items, list) and items:
            for pos, m in enumerate(items):
                if isinstance(m, dict) and m.get("url"):
                    media.append(
                        {
                            "url": m.get("url"),
                            "type": (m.get("type") or "photo").lower(),
                            "category": m.get("mediaCategory"),
                            "position": pos,
                        }
                    )
            break  # first tier with content wins
    return media


def _card_images(card_model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize a search card's flat image-URL list into tagged dicts.

    Cards only give bare photo URLs (no type), so everything is "photo".
    """
    out: List[Dict[str, Any]] = []
    for pos, url in enumerate(card_model.get("images") or []):
        if isinstance(url, str) and url:
            out.append({"url": url, "type": "photo", "category": None, "position": pos})
    return out


def normalize_listing_detail(listing_id: str, v2: Dict[str, Any], card: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a detail page from rootGraphQuery.listingByIdV2 (rich source).

    Falls back to the search-card model (listingsMap entry) for any field the
    GraphQL block does not carry.
    """
    card_model = normalize_listing_model(listing_id, card) if card else {}
    addr = v2.get("displayableAddress") if isinstance(v2.get("displayableAddress"), dict) else {}
    geo = addr.get("geolocation") if isinstance(addr.get("geolocation"), dict) else {}
    price = v2.get("priceDetails") if isinstance(v2.get("priceDetails"), dict) else {}
    raw_price = price.get("rawValues") if isinstance(price.get("rawValues"), dict) else {}

    agents = []
    for agent in v2.get("agents") or []:
        if isinstance(agent, dict):
            photo = agent.get("photo") if isinstance(agent.get("photo"), dict) else {}
            agents.append(
                {
                    "name": agent.get("fullName"),
                    "email": agent.get("email"),
                    "mobile": agent.get("mobileNumber"),
                    "landline": agent.get("landlineNumber"),
                    "profile_url": agent.get("profileUrl"),
                    "photo": photo.get("url"),
                    "agent_id": agent.get("agentId"),
                }
            )

    agency = v2.get("agency") if isinstance(v2.get("agency"), dict) else {}
    agency_logo = (((agency.get("branding") or {}).get("logo")) or {}) if isinstance(agency.get("branding"), dict) else {}

    images = extract_media(v2)

    sold_raw = v2.get("soldDetails") if isinstance(v2.get("soldDetails"), dict) else None
    sold = None
    if sold_raw:
        sold_price = (((sold_raw.get("soldPrice") or {}).get("rawValues") or {}).get("exactPrice"))
        sold_date = (sold_raw.get("soldDate") or {}).get("isoDate")
        sold = {
            "price": sold_price,
            "date": sold_date,
            "method": sold_raw.get("saleMethod"),
            "price_source": sold_raw.get("priceSource"),
            "government_recorded_price": sold_raw.get("governmentRecordedSoldPrice"),
        }

    inspections = []
    insp = v2.get("inspectionDetails") if isinstance(v2.get("inspectionDetails"), dict) else {}
    for item in insp.get("inspections") or []:
        if isinstance(item, dict):
            opening = item.get("openingDateTime") if isinstance(item.get("openingDateTime"), dict) else {}
            closing = item.get("closingDateTime") if isinstance(item.get("closingDateTime"), dict) else {}
            inspections.append({"start": opening.get("isoDate"), "end": closing.get("isoDate")})

    detail = {
        "id": str(v2.get("listingId") or listing_id),
        "listing_type": v2.get("listingType") or card_model.get("listing_type"),
        "status": v2.get("status"),
        "url": v2.get("seoUrl") or card_model.get("url"),
        "headline": v2.get("headline"),
        "description": v2.get("description"),
        "price": price.get("displayPrice") or card_model.get("price"),
        "price_from": raw_price.get("from"),
        "price_to": raw_price.get("to"),
        "price_exact": raw_price.get("exactPriceV2"),
        "address": {
            "display": addr.get("displayAddress"),
            "unit": addr.get("unitNumber"),
            "street_number": addr.get("streetNumber"),
            "street": addr.get("street"),
            "suburb": addr.get("suburbName"),
            "state": addr.get("state"),
            "postcode": addr.get("postcode"),
            "lat": geo.get("latitude"),
            "lng": geo.get("longitude"),
        }
        if addr
        else card_model.get("address"),
        "beds": v2.get("bedrooms") if v2.get("bedrooms") is not None else card_model.get("beds"),
        "baths": v2.get("bathrooms") if v2.get("bathrooms") is not None else card_model.get("baths"),
        "cars": v2.get("carspaces") if v2.get("carspaces") is not None else card_model.get("cars"),
        "property_types": v2.get("propertyTypes") or card_model.get("property_type"),
        "land_area_sqm": v2.get("landAreaSqm"),
        "building_area": v2.get("buildingArea"),
        "energy_rating": v2.get("energyEfficiencyRating"),
        "features": v2.get("features") or [],
        "structured_features": [
            {"category": f.get("category"), "name": f.get("name")}
            for f in (v2.get("structuredFeatures") or [])
            if isinstance(f, dict)
        ],
        "agents": agents or card_model.get("agents"),
        "agency": {"name": agency.get("name"), "logo": agency_logo.get("url")} if agency else None,
        "inspections": inspections,
        "auction": v2.get("auctionDetails"),
        "sold": sold,
        "date_listed": v2.get("dateListedV2"),
        "date_updated": v2.get("dateUpdated"),
        "virtual_tour_url": v2.get("virtualTourUrl"),
        "images": images or _card_images(card_model),
    }
    return detail


def extract_listing_payload(html: str, *, source_url: Optional[str] = None, listing_id: Optional[str] = None) -> Dict[str, Any]:
    if detect_hard_denial(html):
        return {
            "url": source_url,
            "blocked_markers": True,
            "error": "Domain returned a hard access-denied page with no embedded structured data.",
            "listing": None,
            "events": [],
        }

    next_data = extract_next_data(html)
    component = component_props(next_data)

    # Detail-page path: a canonical listingId + rich GraphQL block.
    canonical_id = component.get("listingId")
    v2 = (component.get("rootGraphQuery") or {}).get("listingByIdV2") if isinstance(component.get("rootGraphQuery"), dict) else None
    if canonical_id and isinstance(v2, dict):
        cid = str(canonical_id)
        card = (component.get("listingsMap") or {}).get(cid)
        card = card if isinstance(card, dict) else {}
        return {
            "url": source_url,
            "blocked_markers": detect_blocked(html),
            "listing": normalize_listing_detail(listing_id or cid, v2, card),
            "events": listing_events_from_json_ld(extract_json_ld(html)),
        }

    # Fallback: search-card model shape.
    models = find_listing_models(next_data)
    chosen: Optional[Dict[str, Any]] = None
    if listing_id:
        for item in models:
            if str(item.get("id")) == str(listing_id):
                chosen = item
                break
    if chosen is None and models:
        chosen = models[0]

    return {
        "url": source_url,
        "blocked_markers": detect_blocked(html),
        "listing": normalize_listing_model(listing_id or "", chosen or {}) if chosen else None,
        "events": listing_events_from_json_ld(extract_json_ld(html)),
    }


def html_from_args(args: argparse.Namespace) -> str:
    if getattr(args, "html", None):
        return Path(args.html).read_text(encoding="utf-8")
    url = getattr(args, "url", None)
    if not url and getattr(args, "id", None):
        url = listing_url_for_id(args.id)
    if not url:
        raise SystemExit("--url or --html is required")
    return fetch_html(
        url,
        fetcher=args.fetcher,
        ua=args.ua,
        rps=args.rps,
        burst=args.burst,
        timeout_s=args.timeout,
        cache_dir=Path(args.cache_dir),
        no_cache=args.no_cache,
        headed=args.headed,
        profile_dir=Path(args.profile_dir),
        proxy=args.proxy,
        cdp_url=getattr(args, "cdp_url", DEFAULT_CDP_URL),
    )


def cmd_fetch(args: argparse.Namespace) -> int:
    if not args.url:
        raise SystemExit("--url is required")
    html = fetch_html(
        args.url,
        fetcher=args.fetcher,
        ua=args.ua,
        rps=args.rps,
        burst=args.burst,
        timeout_s=args.timeout,
        cache_dir=Path(args.cache_dir),
        no_cache=args.no_cache,
        headed=args.headed,
        profile_dir=Path(args.profile_dir),
        proxy=args.proxy,
        cdp_url=getattr(args, "cdp_url", DEFAULT_CDP_URL),
    )
    if args.out:
        Path(args.out).write_text(html, encoding="utf-8")
        print(args.out)
    else:
        print(html)
    return 0


def cmd_ids(args: argparse.Namespace) -> int:
    html = html_from_args(args)
    payload = extract_search_payload(html, source_url=args.url, limit=args.limit)
    small = {
        "url": payload["url"],
        "blocked_markers": payload["blocked_markers"],
        "search_result_count": payload["search_result_count"],
        "count": payload["count"],
        "listing_ids": payload["listing_ids"],
    }
    print(json.dumps(small, indent=2) if args.json else "\n".join(small["listing_ids"]))
    return 0


def search_url_from_args(args: argparse.Namespace) -> Optional[str]:
    if getattr(args, "url", None) or getattr(args, "html", None):
        return getattr(args, "url", None)
    if not (args.suburb or args.region):
        return None
    return build_search_url(
        mode=args.mode,
        suburbs=args.suburb,
        region=args.region,
        price_min=args.price_min,
        price_max=args.price_max,
        beds_min=args.beds_min,
        beds_max=args.beds_max,
        baths_min=args.baths_min,
        cars_min=args.cars_min,
        ptypes=args.ptype,
        exclude_under_offer=args.exclude_under_offer,
        features=getattr(args, "features", None),
        keywords=args.keywords,
        sort=args.sort,
        page=args.page,
    )


def cmd_search(args: argparse.Namespace) -> int:
    built = search_url_from_args(args)
    if built and not args.html:
        args.url = built
    html = html_from_args(args)
    payload = extract_search_payload(html, source_url=args.url, limit=args.limit)
    print(json.dumps(payload, indent=2))
    return 0


def cmd_listing(args: argparse.Namespace) -> int:
    html = html_from_args(args)
    payload = extract_listing_payload(html, source_url=args.url, listing_id=args.id)
    print(json.dumps(payload, indent=2))
    return 0


def add_fetch_args(parser: argparse.ArgumentParser):
    parser.add_argument("--url", help="Domain URL")
    parser.add_argument("--html", help="Read a saved HTML file instead of fetching")
    parser.add_argument("--fetcher", choices=["playwright", "http", "cdp"], default="playwright")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help="CDP endpoint of a running browser (for --fetcher cdp)")
    parser.add_argument("--ua", default=DEFAULT_UA)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--rps", type=float, default=0.35, help="Steady-state request rate")
    parser.add_argument("--burst", type=int, default=1, help="Burst tokens")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR))
    parser.add_argument("--headed", action="store_true", help="Run browser headed")
    parser.add_argument(
        "--proxy",
        default=os.environ.get("DOMAIN_PROXY"),
        help="Proxy URL (scheme://user:pass@host:port). Defaults to $DOMAIN_PROXY.",
    )


def add_search_filter_args(parser: argparse.ArgumentParser):
    parser.add_argument("--mode", choices=list(SEARCH_MODES), default="sale")
    parser.add_argument("--suburb", action="append", help="Suburb slug or 'Name STATE postcode'. Repeatable.")
    parser.add_argument("--region", help="Broader locality (suburb/region/state slug) for the path")
    parser.add_argument("--price-min", type=int)
    parser.add_argument("--price-max", type=int)
    parser.add_argument("--beds-min", type=int)
    parser.add_argument("--beds-max", type=int)
    parser.add_argument("--baths-min", type=int)
    parser.add_argument("--cars-min", type=int)
    parser.add_argument("--ptype", action="append", help="Property type (e.g. apartment, house). Repeatable.")
    parser.add_argument("--feature", action="append", dest="features", help="Domain feature filter (e.g. airconditioning, petsallowed). Repeatable.")
    parser.add_argument("--exclude-under-offer", action="store_true")
    parser.add_argument("--keywords")
    parser.add_argument("--sort", help="e.g. price-asc, price-desc, dateupdated-desc, suburb-asc")
    parser.add_argument("--page", type=int)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="domain_cli.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="Fetch a Domain page with Playwright or HTTP")
    add_fetch_args(p_fetch)
    p_fetch.add_argument("--out", help="Write HTML to this path")
    p_fetch.set_defaults(func=cmd_fetch)

    p_ids = sub.add_parser("ids", help="Extract listing IDs from a Domain search page")
    add_fetch_args(p_ids)
    p_ids.add_argument("--limit", type=int, default=50)
    p_ids.add_argument("--json", action="store_true")
    p_ids.set_defaults(func=cmd_ids)

    p_search = sub.add_parser("search", help="Extract normalized search payload")
    add_fetch_args(p_search)
    p_search.add_argument("--limit", type=int)
    add_search_filter_args(p_search)
    p_search.set_defaults(func=cmd_search)

    p_listing = sub.add_parser("listing", help="Extract a normalized listing payload")
    add_fetch_args(p_listing)
    p_listing.add_argument(
        "--id",
        help="Listing ID. With no --url/--html, fetches domain.com.au/<id> directly.",
    )
    p_listing.set_defaults(func=cmd_listing)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
