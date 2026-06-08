"""Paced end-to-end verification of the cdp_get fix.

Tries a real hunt-style search with generous spacing so we don't re-trip the
Akamai behavioural throttle. Stops at first clean success. Reports each attempt.
"""
import sys, os, time
sys.path.insert(0, os.getcwd())
import domain_cli as d

ATTEMPTS = 6
SPACING = 90  # seconds between attempts


def one(suburb):
    url = d.build_search_url(mode="sale", suburbs=[suburb], exclude_under_offer=True)
    # force a fresh fetch through the persistent warmed CDP page
    html = d.fetch_html(url, fetcher="cdp", no_cache=True, timeout_s=90)
    r = d.extract_search_payload(html, source_url=url)
    return url, r, len(html)


def main():
    for i in range(1, ATTEMPTS + 1):
        try:
            url, r, n = one("zetland")
            ok = (not r["blocked_markers"]) and r["count"] > 0
            print(f"[attempt {i}] {url}", flush=True)
            print(f"   count={r['count']} total={r['search_result_count']} "
                  f"blocked={r['blocked_markers']} htmlLen={n} -> {'OK' if ok else 'blocked'}", flush=True)
            if ok:
                for l in r["listings"][:5]:
                    addr = l.get("address") or {}
                    print(f"     {l.get('id')} | {l.get('price')} | "
                          f"{addr.get('street')}, {addr.get('suburb')} | "
                          f"{l.get('beds')}b {l.get('baths')}ba {l.get('cars')}c | {l.get('property_type')}",
                          flush=True)
                print("RESULT: SUCCESS", flush=True)
                return 0
        except Exception as e:
            print(f"[attempt {i}] EXC {type(e).__name__}: {e}", flush=True)
        # reset the warmed singleton between attempts so each is a clean warm
        try:
            d._teardown_cdp()
        except Exception:
            pass
        if i < ATTEMPTS:
            time.sleep(SPACING)
    print("RESULT: STILL BLOCKED after all attempts", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
