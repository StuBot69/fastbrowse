#!/usr/bin/env python3
"""FastBrowse grid_hunt — general eyes-first picker for any image grid.

Problem it kills: alt-text scorers can't tell hair colour from photo
treatment ("black and white" style vs white hair). So don't guess from
text — LOOK. Screenshot the grid with numbered badges on each visible
tile, ask Jasper which number matches the ask, click that href. No
download happens until eyes have verified.

General by design:
  - any site: item_selector + href allowlist passed in, not hardcoded
  - bot-gentle: slow wheel scrolls, 1-1.8s settles, no rapid fire
  - token-cheap: one Jasper look per screen (~640px thumb), NUMBER-only
    reply, max_tokens=20. Browser loop still makes zero metered LLM calls.
  - fast: annotated thumb, small payload, early exit on first YES.

Flow per screen:
  1. eval visible tiles (href + viewport box) for item_selector
  2. screenshot viewport, draw numbered badges at tile centres (PIL)
  3. ask Jasper: "which number matches <ask>? NUMBER only, 0 = none"
  4. hit -> return {href, tile_number, screen_idx, n_looks}
  5. miss -> wheel-scroll ~0.8 viewport, settle, next screen
"""
import base64
import io
import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fb_config  # noqa: E402  (shared env-overridable config)

JASPER_URL = os.environ.get("FASTBROWSE_VISION_URL", fb_config.VISION_URL)
JASPER_MODEL = os.environ.get("FASTBROWSE_VISION_MODEL", fb_config.VISION_MODEL)
FALLBACK_URL = os.environ.get("FASTBROWSE_FALLBACK_URL", fb_config.FALLBACK_URL)
FALLBACK_MODEL = os.environ.get("FASTBROWSE_FALLBACK_MODEL", fb_config.FALLBACK_MODEL)

# Groq API key — read from env or age vault
GROQ_API_KEY = os.environ.get("FASTBROWSE_GROQ_API_KEY", "")
if not GROQ_API_KEY:
    try:
        import sqlite3, base64
        _db = sqlite3.connect(str(Path.home() / "Projects" / "agent-economy" /
                                  "state" / "agent_economy.db"))
        _row = _db.execute(
            "SELECT value FROM credentials WHERE provider='groq-heroeco' LIMIT 1"
        ).fetchone()
        if _row:
            GROQ_API_KEY = base64.b64decode(_row[0]).decode()
        _db.close()
    except Exception:
        pass


def _vision_call(url: str, model: str, thumb_bytes: bytes, ask: str,
                 max_tokens: int = 20, timeout: int = 300,
                 api_key: str = "") -> tuple[int, str, float]:
    """Send annotated thumbnail to an OpenAI-compatible vision endpoint.
    Returns (number, raw_text, seconds)."""
    import base64 as _b64
    from PIL import Image as _Im
    t0 = time.time()
    im = _Im.open(io.BytesIO(thumb_bytes))
    im.thumbnail((640, 640))
    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=60)
    b64img = _b64.b64encode(buf.getvalue()).decode()
    q = (f"Numbered image tiles, each with a red number badge. "
         f"Which tile best shows: {ask}? "
         f"Only pick a tile that CLEARLY shows it — ordinary photos "
         f"without those features are NOT a match. "
         f"Reply with the NUMBER only. Reply 0 if none match.")
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": q},
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + b64img}}]}],
        "max_tokens": max_tokens,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    txt = data["choices"][0]["message"]["content"].strip()
    m = re.search(r"\d+", txt)
    got = int(m.group()) if m else 0
    return got, txt, round(time.time() - t0, 1)


def jasper_number_pick(thumb_bytes: bytes, ask: str,
                       timeout: int = 300) -> tuple[int, str, float]:
    """Try Jasper (Qwen) first, fall back to Groq (Llama) if unavailable."""
    # Try primary (Jasper Qwen)
    try:
        return _vision_call(JASPER_URL, JASPER_MODEL, thumb_bytes, ask,
                            timeout=timeout)
    except Exception as e:
        print(f"  [jasper_number_pick] Jasper failed: {e}", flush=True)

    # Try fallback (Groq Llama)
    if GROQ_API_KEY:
        try:
            print(f"  [jasper_number_pick] Falling back to Groq {FALLBACK_MODEL}",
                  flush=True)
            return _vision_call(FALLBACK_URL, FALLBACK_MODEL, thumb_bytes, ask,
                                timeout=timeout, api_key=GROQ_API_KEY)
        except Exception as e:
            print(f"  [jasper_number_pick] Groq fallback failed: {e}", flush=True)

    raise RuntimeError("No vision endpoint available (Jasper + Groq both down)")


def _badged_thumb(shot: bytes, tiles: list, vw: int, vh: int) -> bytes:
    """Screenshot -> 640px thumb with BIG readable badges.

    Lesson (Sep 26): 12px badges drawn pre-downscale shrink to ~6px
    blobs Jasper literally cannot see ("no red badges visible"). So
    downscale FIRST, then draw r=20 badges with white halo + bold
    numbers in the 640px space Jasper actually receives."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(io.BytesIO(shot)).convert("RGB")
    iw, ih = im.size
    k = 640.0 / max(iw, 1)
    im = im.resize((640, max(1, int(ih * k))), Image.Resampling.BILINEAR)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 26)
    except Exception:
        try:
            font = ImageFont.load_default(size=26)
        except Exception:
            font = ImageFont.load_default()
    dr = ImageDraw.Draw(im)
    for i, t in enumerate(tiles, 1):
        cx = int((t["x"] + t["w"] / 2) * (iw / vw) * k)
        cy = int((t["y"] + 14) * (ih / vh) * k)
        r = 20
        dr.ellipse([cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3],
                   fill=(255, 255, 255))
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(220, 0, 0))
        dr.text((cx, cy), str(i), font=font, fill=(255, 255, 255),
                anchor="mm")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    return buf.getvalue()


def sweep_overlays(page) -> dict:
    """Pre-look sweep: kill delayed popups/cookie walls/newsletters before
    we spend a 21s Jasper look at them. DOM-only (~ms, no snapshot, no
    tokens). Dismisses EVERY matching visible control (reject, accept,
    close, dismiss) — the banner is page furniture, any exit works.
    Never clicks crowd-sized matches: guards count==1 per selector, and
    skips anything below the fold (y > viewport height) where a click
    would silently scroll instead of dismissing."""
    disposed: list = []
    try:
        vh = page.evaluate("() => window.innerHeight") or 900
    except Exception:
        vh = 900
    selectors = [
        "#onetrust-reject-all-handler",
        "#onetrust-accept-btn-handler",
        "#onetrust-pc-btn-handler",  # close prefs, no accept/reject visible
        "[role=dialog] button[aria-label=Close]",
        "[role=dialog] button[aria-label=Dismiss]",
        ".modal.show button.close",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() != 1 or not loc.first.is_visible():
                continue
            try:
                box = loc.first.bounding_box()
            except Exception:
                box = None
            if box and box["y"] > vh - 60:
                continue  # below the fold — scroll into view first
            loc.first.click(timeout=3000)
            disposed.append(sel)
            try:
                page.wait_for_timeout(800)
            except Exception:
                time.sleep(0.8)
        except Exception:
            continue
    # last resort: the banner is visible but its buttons are below the
    # fold (huge viewport, cookie bar at page bottom) — scroll it into
    # view once and retry reject.
    if not disposed:
        try:
            btn = page.locator("#onetrust-reject-all-handler")
            if btn.count() == 1 and btn.first.is_visible():
                btn.first.scroll_into_view_if_needed(timeout=3000)
                try:
                    page.wait_for_timeout(600)
                except Exception:
                    time.sleep(0.6)
                btn.first.click(timeout=3000)
                disposed.append("#onetrust-reject-all-handler(scrolled)")
                try:
                    page.wait_for_timeout(800)
                except Exception:
                    time.sleep(0.8)
        except Exception:
            pass
    return {"disposed": disposed, "checked": len(selectors)}


def overlay_fingerprint(page) -> dict:
    """Cheap layout fingerprint: fixed-overlay count + main text length."""
    try:
        return page.evaluate(
            """() => {
              const fixed = [...document.querySelectorAll('body *')].filter(e => {
                try {
                  const s = getComputedStyle(e);
                  return (s.position === 'fixed' || s.position === 'sticky') &&
                         e.offsetParent !== null;
                } catch (x) { return false; }
              }).length;
              const main = document.querySelector('main, [role=main], #content');
              const txt = (main ? main.innerText : document.body.innerText) || '';
              return {fixed: fixed, len: txt.length};
            }""") or {}
    except Exception:
        return {}


def snapshot_gate(page, prev: dict | None) -> dict:
    """Layout-shift tripwire between sweep and shot. A popup arriving in
    the gap changes fixed-count or text length — skip the wasted look."""
    cur = overlay_fingerprint(page)
    if not prev or not cur:
        return {"shifted": False, "detail": "no baseline", "cur": cur}
    df = abs(cur.get("fixed", 0) - prev.get("fixed", 0))
    pl, cl = prev.get("len", 0), cur.get("len", 0)
    dl = abs(cl - pl) / max(pl, 1)
    if df >= 2:
        return {"shifted": True,
                "detail": f"fixed overlays {prev.get('fixed')}->{cur.get('fixed')}",
                "cur": cur}
    if dl > 0.25 and abs(cl - pl) > 500:
        return {"shifted": True,
                "detail": f"main text {pl}->{cl} chars", "cur": cur}
    return {"shifted": False, "detail": "stable", "cur": cur}


def grid_hunt(page, ask: str, item_selector: str,
              href_re: str = r"/(photos|illustrations|vectors)/.+-\d+/?$",
              max_screens: int = 5, exclude_substr: str | None = None,
              viewport: dict | None = None, min_tiles: int = 1) -> dict:
    """Hunt `ask` across grid screens. Returns pick dict or raises.

    min_tiles: skip near-empty screens (lazy grid still loading) without
    burning a Jasper look — scroll and retry, up to 3 times per screen."""
    from PIL import Image, ImageDraw
    looks: list = []
    seen_hrefs: set = set()
    vw = (viewport or {}).get("width", 1366)
    vh = (viewport or {}).get("height", 900)

    for screen in range(max_screens):
        # pre-look sweep: delayed popups/cookie walls/newsletters/chat
        # widgets land AFTER the first consent pass. DOM-only (~ms, no
        # snapshot, no tokens): dispose via profile, re-settle, THEN shoot.
        prev = overlay_fingerprint(page)
        swept = sweep_overlays(page)
        sweeps = 1 + (1 if swept["disposed"] else 0)
        if swept["disposed"]:
            try:
                page.wait_for_timeout(1200)
            except Exception:
                time.sleep(1.2)
            # consent disposal reflows the grid — scroll a touch so the
            # first screen isn't the banner gap, then settle for lazy tiles
            try:
                page.evaluate("() => window.scrollBy(0, 200)")
                page.wait_for_timeout(1500)
            except Exception:
                time.sleep(1.5)
        # layout-shift tripwire: a popup arriving between sweep and shot
        # changes fixed-count/main-text — skip the wasted Jasper look.
        gate = snapshot_gate(page, prev)
        if gate["shifted"]:
            looks.append({"screen": screen, "gate": "SHIFT-SKIP",
                          "detail": gate["detail"][:80]})
            print(f"  [grid_hunt] screen {screen}: layout shift "
                  f"({gate['detail'][:60]}) — re-sweep, no look burned",
                  flush=True)
            sweep_overlays(page)
            sweeps += 1
            try:
                page.wait_for_timeout(1500)
            except Exception:
                time.sleep(1.5)
            gate = snapshot_gate(page, gate["cur"])  # re-check pre-look
            if gate["shifted"]:
                _scroll(page, vh)
                continue
        # visible tiles with viewport-relative boxes
        try:
            tiles = page.evaluate(
                """(sel) => {
                  const els = [...document.querySelectorAll(sel)].slice(0, 60);
                  return els.map(e => {
                    const r = e.getBoundingClientRect();
                    const img = e.querySelector('img');
                    return {href: e.getAttribute('href') || '',
                            alt: (img && img.alt) || '',
                            x: Math.round(r.x), y: Math.round(r.y),
                            w: Math.round(r.width), h: Math.round(r.height)};
                  }).filter(t => t.w > 60 && t.h > 60 &&
                                 t.y > -50 && t.y < window.innerHeight - 50);
                }""", item_selector)
        except Exception:
            tiles = []
        tiles = [t for t in (tiles or [])
                 if t.get("href") and re.search(href_re, t["href"])
                 and (not exclude_substr or exclude_substr not in t["href"])
                 and t["href"] not in seen_hrefs]
        # cap + deterministic visual order (top-to-bottom, left-to-right)
        tiles = sorted(tiles, key=lambda t: (t["y"] // 40, t["x"]))[:18]
        if len(tiles) < min_tiles and min_tiles > 1:
            # lazy grid still loading: small scroll, settle, re-eval the
            # SAME screen up to 3x — never spend a look on 2 tiles.
            waited = 0
            while len(tiles) < min_tiles and waited < 3:
                _scroll(page, max(vh // 3, 300))
                try:
                    tiles = page.evaluate(
                        """(sel) => {
                          const els = [...document.querySelectorAll(sel)].slice(0, 60);
                          return els.map(e => {
                            const r = e.getBoundingClientRect();
                            const img = e.querySelector('img');
                            return {href: e.getAttribute('href') || '',
                                    alt: (img && img.alt) || '',
                                    x: Math.round(r.x), y: Math.round(r.y),
                                    w: Math.round(r.width), h: Math.round(r.height)};
                          }).filter(t => t.w > 60 && t.h > 60 &&
                                         t.y > -50 && t.y < window.innerHeight - 50);
                        }""", item_selector)
                except Exception:
                    tiles = []
                tiles = [t for t in (tiles or [])
                         if t.get("href") and re.search(href_re, t["href"])
                         and (not exclude_substr or exclude_substr not in t["href"])
                         and t["href"] not in seen_hrefs]
                tiles = sorted(tiles, key=lambda t: (t["y"] // 40, t["x"]))[:18]
                waited += 1
            if not tiles:
                _scroll(page, vh)
                continue
            if len(tiles) < min_tiles:
                looks.append({"screen": screen, "gate": "THIN-SCREEN-SKIP",
                              "n_tiles": len(tiles)})
                _scroll(page, vh)
                continue
        if not tiles:
            _scroll(page, vh)
            continue
        for t in tiles:
            seen_hrefs.add(t["href"])

        # screenshot + BIG badges (downscale first, draw r=20 in thumb
        # space — 12px pre-downscale badges shrink to unreadable ~6px).
        try:
            shot = page.screenshot(timeout=8000)
        except Exception:
            _scroll(page, vh)
            continue
        vis = [t for t in tiles
               if t["y"] + t["h"] < vh - 40 or t["y"] < vh // 2]
        if len(vis) < len(tiles) and len(vis) >= min(min_tiles, 2):
            tiles = vis
        thumb = _badged_thumb(shot, tiles, vw, vh)
        try:
            n, raw, s = jasper_number_pick(thumb, ask)
        except Exception as e:
            looks.append({"screen": screen, "error": str(e)[:100]})
            _scroll(page, vh)
            continue
        looks.append({"screen": screen, "n_tiles": len(tiles),
                      "jasper": raw[:60], "pick": n, "s": s,
                      "sweeps": sweeps})
        print(f"  [grid_hunt] screen {screen}: {len(tiles)} tiles, "
              f"jasper={raw[:40]!r} ({s}s)", flush=True)
        if 1 <= n <= len(tiles):
            win = tiles[n - 1]
            return {"href": win["href"], "alt": win.get("alt", ""),
                    "tile_number": n, "screen_idx": screen,
                    "n_looks": len(looks), "looks": looks,
                    "n_tiles_seen": len(seen_hrefs)}
        # out-of-range or 0 with tiles still on screen: small-model
        # miscount — re-shoot the SAME screen once with a stricter ask
        # before scrolling on.
        if tiles and not any(l.get("retry") for l in looks[-1:]):
            try:
                shot = page.screenshot(timeout=8000)
            except Exception:
                _scroll(page, vh)
                continue
            try:
                n2, raw2, s2 = jasper_number_pick(
                    _badged_thumb(shot, tiles, vw, vh),
                    f"{ask} (there are {len(tiles)} numbered tiles; "
                    f"answer 1-{len(tiles)} or 0)")
            except Exception as e:
                looks.append({"screen": screen, "error": str(e)[:100]})
                _scroll(page, vh)
                continue
            looks.append({"screen": screen, "n_tiles": len(tiles),
                          "jasper": raw2[:60], "pick": n2, "s": s2,
                          "retry": True})
            print(f"  [grid_hunt] screen {screen} retry: "
                  f"jasper={raw2[:40]!r} ({s2}s)", flush=True)
            if 1 <= n2 <= len(tiles):
                win = tiles[n2 - 1]
                return {"href": win["href"], "alt": win.get("alt", ""),
                        "tile_number": n2, "screen_idx": screen,
                        "n_looks": len(looks), "looks": looks,
                        "n_tiles_seen": len(seen_hrefs)}
        _scroll(page, vh)

    raise RuntimeError(
        f"grid_hunt: no match for {ask!r} in {max_screens} screens "
        f"({len(looks)} jasper looks)")


def _scroll(page, vh: int) -> None:
    """One bot-gentle wheel scroll + settle."""
    try:
        page.mouse.wheel(0, int(vh * random.uniform(0.7, 0.85)))
    except Exception:
        try:
            page.evaluate(f"() => window.scrollBy(0, {int(vh * 0.8)})")
        except Exception:
            pass
    try:
        page.wait_for_timeout(int(random.uniform(1000, 1800)))
    except Exception:
        time.sleep(1.2)


def verify_download(path: str, ask: str,
                    timeout: int = 300) -> tuple[bool, str, float]:
    """Eyes on the DOWNLOADED file: YES/NO + sentence. (Post-download
    gate — the pre-download hunt should already have matched, this is
    the receipt.)"""
    import base64
    from PIL import Image
    t0 = time.time()
    im = Image.open(path)
    im.thumbnail((768, 768))
    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    b64 = base64.b64encode(buf.getvalue()).decode()
    q = (f"Does this image show: {ask}? "
         f"Start your answer with YES or NO, then one short sentence.")
    payload = json.dumps({
        "model": JASPER_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": q},
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + b64}}]}],
        "max_tokens": 80,
    }).encode()
    req = urllib.request.Request(JASPER_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    txt = data["choices"][0]["message"]["content"].strip()
    s = round(time.time() - t0, 1)
    return txt.upper().startswith("YES"), txt, s
