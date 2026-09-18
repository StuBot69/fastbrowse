#!/usr/bin/env python3
"""Probe: what does the Pixabay photo page actually offer? Dumps every
link/button that could yield the file, clicks Original, reports events."""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "phase4"))
sys.path.insert(0, str(HERE.parent))

from blind import wait_past_challenge  # noqa: E402

URL = "https://pixabay.com/photos/girl-black-and-white-light-profile-2806276/"

from camoufox.sync_api import Camoufox

with Camoufox(headless=True, persistent_context=True,
              user_data_dir=str(HERE / "runs" / "probe-profile")) as browser:
    try:
        page = browser.new_page()
    except Exception:
        page = browser.new_context().new_page()

    events = []
    page.on("download", lambda d: events.append(
        {"file": d.suggested_filename, "url": d.url[:150]}))
    page.on("popup", lambda p: events.append({"popup": p.url[:150]}))

    page.goto(URL, wait_until="domcontentloaded", timeout=45000)
    wait_past_challenge(page)
    page.wait_for_timeout(3000)

    print("== all /get/ + canva + download-ish anchors ==")
    for sel in ["a[href*='pixabay.com/get/']",
                "a[href*='canva']",
                "button:has-text('Free download')",
                "[role=menuitem]",
                "a[download]"]:
        try:
            items = page.eval_on_selector_all(
                sel, "els => els.map(e => ({t: (e.innerText||'').slice(0,40), "
                "h: e.getAttribute('href'), vis: e.offsetParent !== null}))")
            print(f"{sel}: {len(items or [])}")
            for it in (items or [])[:10]:
                print("   ", it)
        except Exception as e:
            print(f"{sel}: ERR {e}"[:120])

    print("== click Free download ==")
    try:
        page.locator("button:has-text('Free download')").first.click(timeout=8000)
        page.wait_for_timeout(2500)
        print("menuitems now:",
              page.locator("[role=menuitem]").count())
        print("== click Original ==")
        page.locator("[role=menuitem]:has-text('Original')").first.click(timeout=8000)
    except Exception as e:
        print("click chain ERR:", str(e)[:200])
    for _ in range(20):
        page.wait_for_timeout(1000)
        if events:
            break
    print("events:", events)
    print("final url:", page.url[:150])
    try:
        browser.close()
    except Exception:
        pass
