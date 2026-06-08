"""After a successful gesture, does goto(filtered URL) keep data or revert to shell?
Also: does goto(page2) work for pagination?"""
import json, re, sys, os
sys.path.insert(0, os.getcwd())
from playwright.sync_api import sync_playwright
import domain_cli as d

CDP = "http://127.0.0.1:18800"
TYPEAHEAD_INPUT = "#fe-pa-domain-home-typeahead-input"
TYPEAHEAD_ITEM_PREFIX = "fe-pa-domain-home-typeahead-item-"
SEARCH_BUTTON = "[data-testid='fe-co-search-controls-base-search-button']"


def gesture(page, suburb):
    page.goto("https://www.domain.com.au/", wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(1500)
    page.wait_for_selector(TYPEAHEAD_INPUT, timeout=15000)
    page.click(TYPEAHEAD_INPUT); page.fill(TYPEAHEAD_INPUT, "")
    for ch in suburb:
        page.type(TYPEAHEAD_INPUT, ch, delay=90)
    page.wait_for_timeout(1400)
    opts = page.evaluate(
        "function(p){var o=[];var n=document.querySelectorAll(\"[id^='\"+p+\"']\");"
        "for(var i=0;i<n.length;i++)o.push({id:n[i].id,text:(n[i].innerText||'').trim()});return o;}",
        TYPEAHEAD_ITEM_PREFIX)
    target = next((o["id"] for o in opts if suburb.lower() in o["text"].lower()), opts[0]["id"] if opts else None)
    if target:
        page.click("#" + target); page.wait_for_timeout(900)
    if page.query_selector(SEARCH_BUTTON):
        page.click(SEARCH_BUTTON)
    else:
        page.keyboard.press("Enter")
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    try: page.wait_for_load_state("networkidle", timeout=15000)
    except Exception: pass
    for _ in range(25):
        try:
            n = page.evaluate("document.querySelectorAll(\"[data-testid*='listing-card']\").length")
        except Exception:
            n = 0
        if n: break
        page.wait_for_timeout(1000)


def report(tag, page):
    html = page.content()
    r = d.extract_search_payload(html, source_url=page.url)
    print(f"[{tag}] url={page.url}")
    print(f"      count={r['count']} total={r['search_result_count']} blocked={r['blocked_markers']} htmlLen={len(html)}")
    return html


def main():
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP, timeout=15000)
        page = b.contexts[0].new_page()
        page.set_default_timeout(20000)
        gesture(page, "Zetland")
        report("gesture-land", page)

        # Test 1: goto a filtered URL (price cap + 2 beds) in the warmed tab
        furl = "https://www.domain.com.au/sale/?excludeunderoffer=1&suburb=zetland-nsw-2017&bedrooms=2-2&price=0-1100000&ptype=apartment-unit-flat"
        page.goto(furl, wait_until="domcontentloaded", timeout=30000)
        try: page.wait_for_load_state("networkidle", timeout=12000)
        except Exception: pass
        for _ in range(20):
            try: n=page.evaluate("document.querySelectorAll(\"[data-testid*='listing-card']\").length")
            except Exception: n=0
            if n: break
            page.wait_for_timeout(1000)
        report("goto-filtered", page)

        # Test 2: goto page 2
        p2 = "https://www.domain.com.au/sale/?excludeunderoffer=1&suburb=zetland-nsw-2017&page=2"
        page.goto(p2, wait_until="domcontentloaded", timeout=30000)
        try: page.wait_for_load_state("networkidle", timeout=12000)
        except Exception: pass
        for _ in range(20):
            try: n=page.evaluate("document.querySelectorAll(\"[data-testid*='listing-card']\").length")
            except Exception: n=0
            if n: break
            page.wait_for_timeout(1000)
        report("goto-page2", page)
        page.close()


if __name__ == "__main__":
    main()
