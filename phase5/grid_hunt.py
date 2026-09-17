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
import random
import re
import time
import urllib.request

JASPER_URL = "http://100.95.162.99:8080/v1/chat/completions"
JASPER_MODEL = "/home/jasper/models/Qwen2.5-VL-7B-Abliterated-Q4_K_M.gguf"


def jasper_number_pick(thumb_bytes: bytes, ask: str,
                       timeout: int = 300) -> tuple[int, str, float]:
    """Send annotated thumbnail, get back (number, raw_text, seconds)."""
    from PIL import Image
    t0 = time.time()
    # keep it small: hunting doesn't need detail, position does
    im = Image.open(io.BytesIO(thumb_bytes))
    im.thumbnail((640, 640))
    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=60)
    b64 = base64.b64encode(buf.getvalue()).decode()
    q = (f"Numbered image tiles, each with a red number badge. "
         f"Which tile best shows: {ask}? "
         f"Only pick a tile that CLEARLY shows it — ordinary photos "
         f"without those features are NOT a match. "
         f"Reply with the NUMBER only. Reply 0 if none match.")
    payload = json.dumps({
        "model": JASPER_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": q},
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + b64}}]}],
        "max_tokens": 20,
    }).encode()
    req = urllib.request.Request(JASPER_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    txt = data["choices"][0]["message"]["content"].strip()
    m = re.search(r"\d+", txt)
    got = int(m.group()) if m else 0
    # guard: small-model miscounts (tile numbers exceed visible count).
    # Caller passes n_tiles via the validated range — clamp here by
    # returning raw and letting grid_hunt re-ask on out-of-range.
    return got, txt, round(time.time() - t0, 1)


def grid_hunt(page, ask: str, item_selector: str,
              href_re: str = r"/(photos|illustrations|vectors)/.+-\d+/?$",
              max_screens: int = 5, exclude_substr: str | None = None,
              viewport: dict | None = None) -> dict:
    """Hunt `ask` across grid screens. Returns pick dict or raises."""
    from PIL import Image, ImageDraw
    looks: list = []
    seen_hrefs: set = set()
    vw = (viewport or {}).get("width", 1366)
    vh = (viewport or {}).get("height", 900)

    for screen in range(max_screens):
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
        if not tiles:
            _scroll(page, vh)
            continue
        for t in tiles:
            seen_hrefs.add(t["href"])

        # screenshot + badges
        try:
            shot = page.screenshot(timeout=8000)
        except Exception:
            _scroll(page, vh)
            continue
        im = Image.open(io.BytesIO(shot)).convert("RGB")
        iw, ih = im.size
        sx, sy = iw / vw, ih / vh
        dr = ImageDraw.Draw(im)
        for i, t in enumerate(tiles, 1):
            cx = int((t["x"] + t["w"] / 2) * sx)
            cy = int((t["y"] + 12) * sy)
            r = max(10, int(12 * sx))
            dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(220, 0, 0))
            dr.text((cx - 5, cy - 8), str(i), fill=(255, 255, 255))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=70)
        try:
            n, raw, s = jasper_number_pick(buf.getvalue(), ask)
        except Exception as e:
            looks.append({"screen": screen, "error": str(e)[:100]})
            _scroll(page, vh)
            continue
        looks.append({"screen": screen, "n_tiles": len(tiles),
                      "jasper": raw[:60], "pick": n, "s": s})
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
            im = Image.open(io.BytesIO(shot)).convert("RGB")
            iw, ih = im.size
            sx, sy = iw / vw, ih / vh
            dr = ImageDraw.Draw(im)
            for i, t in enumerate(tiles, 1):
                cx = int((t["x"] + t["w"] / 2) * sx)
                cy = int((t["y"] + 12) * sy)
                r = max(10, int(12 * sx))
                dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(220, 0, 0))
                dr.text((cx - 5, cy - 8), str(i), fill=(255, 255, 255))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=70)
            try:
                n2, raw2, s2 = jasper_number_pick(
                    buf.getvalue(),
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
