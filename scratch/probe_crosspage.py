import sys, os
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
    for _ in range(20):
        try: n=page.evaluate("document.querySelectorAll(\"[data-testid*='listing-card']\").length")
        except Exception: n=0
        if n: break
        page.wait_for_timeout(1000)


def wait_cards(page, t=30):
    for _ in range(t):
        try: n=page.evaluate("document.querySelectorAll(\"[data-testid*='listing-card']\").length")
        except Exception: n=0
        if n: return n
        page.wait_for_timeout(1000)
    return 0


def main():
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP, timeout=15000)
        ctx = b.contexts[0]
        page1 = ctx.new_page(); page1.set_default_timeout(20000)
        gesture(page1, "Zetland")
        r1 = d.extract_search_payload(page1.content(), source_url=page1.url)
        print("page1 (warmed):", r1["count"], "total", r1["search_result_count"])
        page1.close()

        # NEW page, NO gesture, goto a DIFFERENT suburb search directly
        page2 = ctx.new_page(); page2.set_default_timeout(20000)
        url = "https://www.domain.com.au/sale/?excludeunderoffer=1&suburb=randwick-nsw-2031"
        page2.goto(url, wait_until="domcontentloaded", timeout=30000)
        wait_cards(page2)
        r2 = d.extract_search_payload(page2.content(), source_url=url)
        print("page2 (fresh page, cookie-only goto):", r2["count"], "total", r2["search_result_count"], "blocked", r2["blocked_markers"])
        page2.close()


if __name__ == "__main__":
    main()
