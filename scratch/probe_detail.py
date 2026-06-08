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
    for _ in range(20):
        try: n=page.evaluate("document.querySelectorAll(\"[data-testid*='listing-card']\").length")
        except Exception: n=0
        if n: break
        page.wait_for_timeout(1000)


def main():
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP, timeout=15000)
        page = b.contexts[0].new_page()
        page.set_default_timeout(20000)
        gesture(page, "Zetland")
        # grab a real listing id from the landed page
        html = page.content()
        r = d.extract_search_payload(html, source_url=page.url)
        lid = next((i for i in r["listing_ids"] if int(i) > 1000000), None)
        print("picked listing id:", lid)
        durl = f"https://www.domain.com.au/{lid}"
        page.goto(durl, wait_until="domcontentloaded", timeout=30000)
        try: page.wait_for_load_state("networkidle", timeout=12000)
        except Exception: pass
        page.wait_for_timeout(2000)
        dhtml = page.content()
        print("detail url:", page.url, "htmlLen:", len(dhtml), "denial:", d.detect_hard_denial(dhtml))
        open("scratch/_detail.html","w",encoding="utf-8").write(dhtml)
        # try the detail parser
        try:
            det = d.extract_listing_payload(dhtml, source_url=page.url, listing_id=str(lid))
        except Exception as e:
            det = f"ERR {e}"
        if isinstance(det, dict):
            print("blocked:", det.get("blocked_markers"), "error:", det.get("error"))
            ls = det.get("listing")
            if ls:
                print("listing keys:", list(ls.keys())[:18])
                print("price:", ls.get("price"), "beds:", ls.get("beds"), "agents:", len(ls.get("agents") or []), "desc?:", bool(ls.get("description")))
            else:
                print("listing: None")
        else:
            print("detail parse result:", det)
        page.close()


if __name__ == "__main__":
    main()
