#!/usr/bin/env python3
"""Together AI signup — Camoufox headed, stays open for Stu to click through."""
import sys, time, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from camoufox.sync_api import Camoufox


def main():
    print("Starting Camoufox headed for Together AI signup...")
    with Camoufox(headless=False) as browser:
        ctx = browser.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()

        print("Navigating to api.together.ai...")
        page.goto("https://api.together.ai", wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        # Check if we're already on a signin page
        url = page.url
        title = page.title()
        print(f"\n=== START === url={url} title={title}")

        # If already on signin, look for Google button
        if "signin" in url.lower():
            try:
                btn = page.locator("button:has-text('Continue with Google')")
                if btn.count() > 0 and btn.first.is_visible():
                    print("Clicking 'Continue with Google'...")
                    btn.first.click(timeout=5000)
                    time.sleep(3)
            except Exception as e:
                print(f"Google click error: {e}")

        # Wait for Google OAuth / 2FA — poll up to 5 min
        print("\nWaiting for Google OAuth / 2FA (up to 5 min)...")
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(3)
            try:
                url = page.url
                title = page.title()
                if "accounts.google.com" not in url and "signin" not in url and "oauth" not in url:
                    print(f"\n=== LANDED === url={url} title={title}")
                    break
                if time.time() % 15 < 3:
                    print(f"  [{int(time.time()-deadline+300)}s] url={url[:80]} title={title[:60]}")
            except Exception:
                pass

        # After OAuth, navigate to API keys
        print("\nNavigating to API keys page...")
        page.goto("https://api.together.ai/api-keys", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        print(f"\n=== API KEYS PAGE ===")
        print(f"url={page.url} title={page.title()}")

        # Look for create/mint key button
        try:
            btns = page.eval_on_selector_all(
                "button, a",
                """els => els.slice(0, 30).map(e => ({
                    tag: e.tagName,
                    text: (e.textContent || '').trim().slice(0, 80),
                    href: e.getAttribute('href') || ''
                }))"""
            )
            for b in btns:
                t = b['text'].lower()
                if any(kw in t for kw in ['create', 'generate', 'mint', 'new key', 'add key', 'api key', 'token']):
                    print(f"  {b['tag']} text='{b['text']}' href='{b['href']}'")
        except Exception as e:
            print(f"  scan error: {e}")

        print("\n=== INSTRUCTIONS ===")
        print("1. Click 'Create API Key' / 'New Key' / whatever mint button appears")
        print("2. Copy the key that appears (it's shown ONCE)")
        print("3. Paste it here when ready")
        print("\nWaiting 120s for you to mint the key...")
        time.sleep(120)

        # Final state
        print(f"\n=== FINAL === url={page.url} title={page.title()}")
        try:
            all_text = page.inner_text("body")
            # Look for key-like patterns
            import re
            keys = re.findall(r'[A-Za-z0-9_\-]{20,60}', all_text)
            for k in keys[:10]:
                print(f"  potential key: {k}")
        except Exception:
            pass

        print("\nDone. Copy the key from the Together AI dashboard and paste it to me.")


if __name__ == "__main__":
    main()