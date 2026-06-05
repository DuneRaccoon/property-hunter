#!/usr/bin/env python3
"""Open normal headed Chromium for manual Domain/Akamai clearance.

This intentionally does not use Playwright or CDP. It starts a regular Chromium
window with the same system profile directory that the hunter later uses for
headed Domain fetches. Close the browser when Domain search pages load normally.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from domain_cli import SYSTEM_PROFILE_DIR, build_search_url


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="domain_clearance.py")
    parser.add_argument("--url", help="Domain URL to open")
    parser.add_argument("--profile-dir", default=str(SYSTEM_PROFILE_DIR))
    args = parser.parse_args(argv)

    chrome = shutil.which("chromium") or shutil.which("google-chrome")
    if not chrome:
        raise SystemExit("No Chromium/Chrome executable found")

    url = args.url or build_search_url(
        mode="sale",
        suburbs=["Zetland NSW 2017"],
        price_min=0,
        price_max=1_100_000,
        beds_min=1,
        beds_max=2,
        baths_min=1,
        cars_min=1,
        ptypes=["apartment"],
        exclude_under_offer=True,
        sort="dateupdated-desc",
    )
    profile_dir = Path(args.profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"Opening {url}")
    print(f"Profile: {profile_dir}")
    print("Complete any Domain/Akamai prompt in the browser, confirm listings load, then close the browser window.")
    return subprocess.call([
        chrome,
        f"--user-data-dir={profile_dir.resolve()}",
        "--new-window",
        "--lang=en-AU,en",
        url,
    ])


if __name__ == "__main__":
    raise SystemExit(main())
