"""Probe: do the gesture, land on results, find WHERE listings live."""
import json, re, sys, time
sys.path.insert(0, "..")
from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:18800"
SUBURB = "Zetland"

TYPEAHEAD_INPUT = "#fe-pa-domain-home-typeahead-input"
TYPEAHEAD_ITEM_PREFIX = "fe-pa-domain-home-typeahead-item-"
SEARCH_BUTTON = "[data-testid='fe-co-search-controls-base-search-button']"


def main():
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(CDP, timeout=15000)
        ctx = b.contexts[0]
        page = ctx.new_page()
        page.set_default_timeout(20000)
        page.goto("https://www.domain.com.au/", wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(1500)
        page.wait_for_selector(TYPEAHEAD_INPUT, timeout=15000)
        page.click(TYPEAHEAD_INPUT)
        page.fill(TYPEAHEAD_INPUT, "")
        for ch in SUBURB:
            page.type(TYPEAHEAD_INPUT, ch, delay=90)
        page.wait_for_timeout(1400)
        opts = page.evaluate(
            "function(p){var o=[];var n=document.querySelectorAll(\"[id^='\"+p+\"']\");"
            "for(var i=0;i<n.length;i++)o.push({id:n[i].id,text:(n[i].innerText||'').trim()});return o;}",
            TYPEAHEAD_ITEM_PREFIX,
        )
        print("OPTIONS:", [o["text"][:40] for o in opts])
        target = None
        for o in opts:
            if "zetland" in o["text"].lower():
                target = o["id"]; break
        if not target and opts:
            target = opts[0]["id"]
        if target:
            page.click("#" + target)
            page.wait_for_timeout(900)
        if page.query_selector(SEARCH_BUTTON):
            page.click(SEARCH_BUTTON)
        else:
            page.keyboard.press("Enter")
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        # readiness wait: listing cards (resilient to in-SPA navigation)
        def count_cards():
            try:
                return page.evaluate("document.querySelectorAll(\"[data-testid*='listing-card']\").length")
            except Exception:
                return 0
        for _ in range(30):
            n = count_cards()
            if n and n > 0:
                break
            page.wait_for_timeout(1000)
        url = page.url
        h1 = page.evaluate("(document.querySelector('h1')||{}).innerText||''")
        ncards = page.evaluate("document.querySelectorAll(\"[data-testid*='listing-card']\").length")
        print("LANDED URL:", url)
        print("H1:", h1)
        print("CARD NODES:", ncards)

        html = page.content()
        open("scratch/_landed.html", "w", encoding="utf-8").write(html)
        print("htmlLen:", len(html))

        # find __NEXT_DATA__
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            data = json.loads(m.group(1))
            open("scratch/_next.json", "w").write(json.dumps(data)[:50])
            pp = data.get("props", {}).get("pageProps", {})
            print("pageProps keys:", list(pp.keys()))
            comp = pp.get("componentProps", {})
            print("componentProps keys:", list(comp.keys()) if isinstance(comp, dict) else type(comp))
            # search for any list of dicts with 'id' that looks like listings
            def walk(o, path="", depth=0):
                if depth > 6:
                    return
                if isinstance(o, list):
                    if len(o) > 5 and isinstance(o[0], dict):
                        keys = set(o[0].keys())
                        if keys & {"listingType", "id", "listingId", "propertyId", "listingModel"}:
                            print(f"  CANDIDATE {path} len={len(o)} keys={sorted(list(keys))[:12]}")
                    for i, v in enumerate(o[:3]):
                        walk(v, f"{path}[{i}]", depth+1)
                elif isinstance(o, dict):
                    for k, v in o.items():
                        walk(v, f"{path}.{k}", depth+1)
            walk(data, "", 0)
            # save componentProps json for manual inspect
            json.dump(comp, open("scratch/_comp.json", "w"))
        else:
            print("NO __NEXT_DATA__ in landed html")

        # also extract DOM cards directly
        cards = page.evaluate("""
        () => {
          const out=[];
          document.querySelectorAll("[data-testid*='listing-card']").forEach(c=>{
            const a=c.querySelector("a[href*='/']");
            const href=a?a.href:'';
            const price=(c.querySelector("[data-testid='listing-card-price']")||{}).innerText||'';
            const addr=(c.querySelector("[data-testid='address-line1']")||{}).innerText||'';
            const addr2=(c.querySelector("[data-testid='address-line2']")||{}).innerText||'';
            out.push({href,price,addr,addr2});
          });
          return out.slice(0,5);
        }
        """)
        print("SAMPLE DOM CARDS:")
        for c in cards:
            print("  ", c)
        page.close()


if __name__ == "__main__":
    main()
