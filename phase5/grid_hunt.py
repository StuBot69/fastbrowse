#!/usr/bin/env python3
"""FastBrowse grid_hunt — general eyes-first picker for any image grid.

Problem it kills: alt-text scorers can't tell hair colour from photo
treatment ("black and white" style vs white hair). So don't guess from
text — LOOK. Screenshot the grid with numbered badges on each visible
tile, ask Jasper which number matches the ask, click that href. No
download happens until eyes have verified.

General by design:
  - any site: item_selector + href allowlist passed in, not hardcoded
  - vision-driven: Jasper owns the navigation verdict each screen —
    PICK <n> (click tile), SCROLL <down|up> (not here yet), or
    STOP <reason> (dead end: paywall/login/trap — abort, don't burn looks)
  - bot-gentle: slow wheel scrolls, 1-1.8s settles, no rapid fire
  - token-cheap: one Jasper look per screen (~640px thumb),
    max_tokens=30. Browser loop still makes zero metered LLM calls.
  - fast: annotated thumb, small payload, early exit on first PICK.

Flow per screen:
  1. eval visible tiles (href + viewport box) for item_selector
  2. screenshot viewport, draw numbered badges at tile centres (PIL)
  3. Jasper verdict on <ask>: PICK n / SCROLL dir / STOP reason
  4. PICK -> return {href, tile_number, screen_idx, n_looks}
  5. SCROLL -> wheel-scroll the reported direction, settle, next screen
  6. STOP -> raise (suite aborts + reports Jasper's reason)
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

# Groq API key — env (or local .env via fb_config) only. Never any vault,
# keychain, or database lookup: operator secrets stay in operator hands.
GROQ_API_KEY = os.environ.get("FASTBROWSE_GROQ_API_KEY", "")


def _vision_call(url: str, model: str, thumb_bytes: bytes, ask: str,
                 max_tokens: int = 30, timeout: int = 300,
                 api_key: str = "") -> tuple[str, float]:
    """Send annotated thumbnail to an OpenAI-compatible vision endpoint.
    Returns (raw_text, seconds) — the caller parses the verdict."""
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
         f"You are driving the browser. Target: {ask}. "
         f"Only a tile that CLEARLY shows the target counts — ordinary "
         f"photos without those features are NOT a match. "
         f"Reply with exactly one verdict: "
         f"PICK <n> (tile n clearly shows the target — badge number), "
         f"SCROLL <down|up> (target not on this screen, scroll that way), "
         f"or STOP <few words> (dead end: the results grid itself is "
         f"blocked by a paywall, login wall, or consent trap covering it, "
         f"or the page has nothing to do with the target — NOT just "
         f"because login buttons exist in the header while results load "
         f"further down).")
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
    return txt, round(time.time() - t0, 1)


def parse_verdict(txt: str) -> dict:
    """Parse a vision verdict into a decision dict.

    Returns {"action": PICK|SCROLL|STOP, "n": int|None,
             "dir": down|up, "reason": str, "raw": str}.
    Unparseable replies degrade to SCROLL down (never strand the loop);
    bare NUMBER-only replies stay backward-compatible as PICK n."""
    raw = (txt or "").strip()
    t = raw.upper()
    m = re.search(r"PICK\s*(\d+)", t)
    if m:
        return {"action": "PICK", "n": int(m.group(1)), "dir": "down",
                "reason": raw[:80], "raw": raw[:120]}
    if "STOP" in t:
        reason = re.sub(r"(?i)^.*?STOP\s*", "", raw).strip()[:80] or raw[:80]
        return {"action": "STOP", "n": None, "dir": "down",
                "reason": reason, "raw": raw[:120]}
    if "SCROLL" in t:
        direction = "up" if re.search(r"\bUP\b", t) else "down"
        return {"action": "SCROLL", "n": None, "dir": direction,
                "reason": raw[:80], "raw": raw[:120]}
    m2 = re.search(r"\d+", t)  # legacy NUMBER-only model reply
    if m2 and int(m2.group()) > 0:
        return {"action": "PICK", "n": int(m2.group()), "dir": "down",
                "reason": "legacy number reply: " + raw[:60],
                "raw": raw[:120]}
    return {"action": "SCROLL", "n": None, "dir": "down",
            "reason": "unparseable, default scroll: " + raw[:60],
            "raw": raw[:120]}


def jasper_verdict(thumb_bytes: bytes, ask: str,
                   timeout: int = 300) -> tuple[dict, float]:
    """Vision-driven navigation verdict. Jasper (Qwen) first, Groq fallback.

    Returns (verdict_dict, seconds). Raises if both endpoints are down."""
    # Try primary (Jasper Qwen)
    try:
        txt, s = _vision_call(JASPER_URL, JASPER_MODEL, thumb_bytes, ask,
                              timeout=timeout)
        return parse_verdict(txt), s
    except Exception as e:
        print(f"  [jasper_verdict] Jasper failed: {e}", flush=True)

    # Try fallback (Groq Llama)
    if GROQ_API_KEY:
        try:
            print(f"  [jasper_verdict] Falling back to Groq {FALLBACK_MODEL}",
                  flush=True)
            txt, s = _vision_call(FALLBACK_URL, FALLBACK_MODEL, thumb_bytes,
                                  ask, timeout=timeout, api_key=GROQ_API_KEY)
            return parse_verdict(txt), s
        except Exception as e:
            print(f"  [jasper_verdict] Groq fallback failed: {e}", flush=True)

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


TILE_JS = """(args) => {
  const sel = args.sel, child = args.child, excard = args.excard;
  const els = [...document.querySelectorAll(sel)].slice(0, 60);
  const out = [];
  for (const e of els) {
    const r = e.getBoundingClientRect();
    const img = e.querySelector('img');
    const link = child ? e.querySelector(child) : null;
    // sponsored/promoted tiles are paywalled traps for eyes-first:
    // drop any tile whose card text matches the exclusion pattern
    // (sponsored rows, Unsplash+ badges, promoted pins).
    if (excard) {
      let blob = '';
      try {
        const card = e.closest('li, figure, [class*="card"], [class*="Card"], [class*="tile"], [class*="Tile"], [class*="promo"], [class*="Promo"], [class*="sponsor"], [class*="Sponsor"]');
        blob = ((e.innerText || '') + ' ' + ((card && card.innerText) || '')).slice(0, 300);
        if (blob.match(new RegExp(excard, 'i'))) continue;
      } catch (x) { /* keep the tile on regex errors */ }
    }
    const t = {href: (link && link.getAttribute('href')) || e.getAttribute('href') || '',
            alt: (img && img.alt) || '',
            x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height)};
    if (t.w > 60 && t.h > 60 && t.y > -50 && t.y < window.innerHeight - 50)
      out.push(t);
  }
  return out;
}"""


class NoMatch(RuntimeError):
    """grid_hunt found nothing. Carries the per-look verdicts so suites
    can write a failure report instead of losing Jasper's reasons."""

    def __init__(self, msg: str, looks: list | None = None):
        super().__init__(msg)
        self.looks = looks or []


def grid_hunt(page, ask: str, item_selector: str,
              href_re: str = r"/(photos|illustrations|vectors)/.+-\d+/?$",
              max_screens: int = 5, exclude_substr: str | None = None,
              viewport: dict | None = None, min_tiles: int = 1,
              href_child: str | None = None,
              exclude_card_re: str | None = None) -> dict:
    """Hunt `ask` across grid screens. Returns pick dict or raises.

    min_tiles: skip near-empty screens (lazy grid still loading) without
    burning a Jasper look — scroll and retry, up to 3 times per screen.
    href_child: descendant selector to read the href from (e.g. Unsplash
    grids where the sized tile is a <figure> and the link is a 21px title
    anchor inside it). When None, href comes from the tile element itself.
    exclude_card_re: case-insensitive pattern matched against tile + card
    text; matching tiles (Sponsored rows, Plus badges, promoted pins) are
    dropped before the look so Jasper never spends on paywalled traps."""
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
                TILE_JS, {"sel": item_selector, "child": href_child,
                          "excard": exclude_card_re})
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
                        TILE_JS, {"sel": item_selector, "child": href_child,
                                  "excard": exclude_card_re})
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
            verdict, s = jasper_verdict(thumb, ask)
        except Exception as e:
            looks.append({"screen": screen, "error": str(e)[:100]})
            _scroll(page, vh)
            continue
        looks.append({"screen": screen, "n_tiles": len(tiles),
                      "jasper": verdict["raw"][:60], "verdict": verdict["action"],
                      "detail": verdict["reason"][:60], "s": s,
                      "sweeps": sweeps})
        print(f"  [grid_hunt] screen {screen}: {len(tiles)} tiles, "
              f"jasper={verdict['action']} {verdict['reason'][:40]!r} ({s}s)",
              flush=True)
        if verdict["action"] == "STOP":
            if tiles and not any(l.get("retry") for l in looks[-1:]):
                # One-screen sites (dA's login wall caps logged-out
                # scrolling): don't scroll away from the only good screen.
                # Re-shoot and constrain the model — PICK or SCROLL only.
                try:
                    shot2 = page.screenshot(timeout=8000)
                except Exception:
                    shot2 = None
                if shot2 is not None:
                    try:
                        verdict2, s2 = jasper_verdict(
                            _badged_thumb(shot2, tiles, vw, vh),
                            f"{ask} (STOP is not available on this screen: "
                            f"reply PICK 1-{len(tiles)} or SCROLL down)")
                    except Exception:
                        verdict2, s2 = None, 0.0
                    if verdict2 is not None:
                        looks.append({"screen": screen,
                                      "n_tiles": len(tiles),
                                      "jasper": verdict2["raw"][:60],
                                      "verdict": verdict2["action"],
                                      "detail": verdict2["reason"][:60],
                                      "s": s2, "retry": True,
                                      "overruled": True})
                        print(f"  [grid_hunt] screen {screen}: STOP overruled "
                              f"— re-ask: {verdict2['action']} "
                              f"{verdict2['reason'][:40]!r} ({s2}s)",
                              flush=True)
                        if verdict2["action"] == "PICK":
                            n0 = verdict2["n"] or 0
                            if 1 <= n0 <= len(tiles):
                                win = tiles[n0 - 1]
                                return {"href": win["href"],
                                        "alt": win.get("alt", ""),
                                        "tile_number": n0,
                                        "screen_idx": screen,
                                        "n_looks": len(looks),
                                        "looks": looks,
                                        "n_tiles_seen": len(seen_hrefs)}
                        _scroll(page, vh, verdict2["dir"]
                                if verdict2["action"] == "SCROLL" else "down")
                        continue
            if tiles:
                # Model cried wolf: free tiles are visible and unblocked
                # (login buttons in the header, blurred thumbs in view —
                # not a wall). Downgrade to SCROLL, log it, move on.
                # STOP is only honored when no usable tile is on screen.
                looks[-1]["verdict"] = "SCROLL"
                looks[-1]["overruled"] = True
                looks[-1]["detail"] = (
                    f"STOP overruled ({len(tiles)} tiles visible): "
                    + verdict["reason"][:50])
                print(f"  [grid_hunt] screen {screen}: STOP overruled — "
                      f"{len(tiles)} tiles visible, scrolling down",
                      flush=True)
                _scroll(page, vh, "down")
                continue
            raise NoMatch(
                f"grid_hunt STOP on screen {screen}: {verdict['reason'][:120]} "
                f"({len(looks)} jasper looks)", looks)
        if verdict["action"] == "PICK":
            n = verdict["n"] or 0
            if 1 <= n <= len(tiles):
                win = tiles[n - 1]
                return {"href": win["href"], "alt": win.get("alt", ""),
                        "tile_number": n, "screen_idx": screen,
                        "n_looks": len(looks), "looks": looks,
                        "n_tiles_seen": len(seen_hrefs)}
            # out-of-range PICK: small-model miscount — re-shoot the SAME
            # screen once with a stricter ask before moving on.
            if tiles and not any(l.get("retry") for l in looks[-1:]):
                try:
                    shot = page.screenshot(timeout=8000)
                except Exception:
                    _scroll(page, vh)
                    continue
                try:
                    verdict2, s2 = jasper_verdict(
                        _badged_thumb(shot, tiles, vw, vh),
                        f"{ask} (there are {len(tiles)} numbered tiles; "
                        f"reply PICK 1-{len(tiles)}, SCROLL down, or STOP reason)")
                except Exception as e:
                    looks.append({"screen": screen, "error": str(e)[:100]})
                    _scroll(page, vh)
                    continue
                looks.append({"screen": screen, "n_tiles": len(tiles),
                              "jasper": verdict2["raw"][:60],
                              "verdict": verdict2["action"],
                              "detail": verdict2["reason"][:60], "s": s2,
                              "retry": True})
                print(f"  [grid_hunt] screen {screen} retry: "
                      f"jasper={verdict2['action']} "
                      f"{verdict2['reason'][:40]!r} ({s2}s)", flush=True)
                if verdict2["action"] == "STOP":
                    # retry context always has tiles on screen — same
                    # overrule as the main path (see above).
                    looks[-1]["verdict"] = "SCROLL"
                    looks[-1]["overruled"] = True
                    looks[-1]["detail"] = (
                        f"STOP overruled ({len(tiles)} tiles visible): "
                        + verdict2["reason"][:50])
                    print(f"  [grid_hunt] screen {screen} retry: STOP "
                          f"overruled, scrolling down", flush=True)
                    _scroll(page, vh, "down")
                    continue
                if verdict2["action"] == "PICK":
                    n2 = verdict2["n"] or 0
                    if 1 <= n2 <= len(tiles):
                        win = tiles[n2 - 1]
                        return {"href": win["href"], "alt": win.get("alt", ""),
                                "tile_number": n2, "screen_idx": screen,
                                "n_looks": len(looks), "looks": looks,
                                "n_tiles_seen": len(seen_hrefs)}
                    _scroll(page, vh, verdict2["dir"]
                            if verdict2["action"] == "SCROLL" else "down")
                    continue
                _scroll(page, vh, verdict2["dir"])
                continue
        # SCROLL (or PICK that missed twice): the model drives — scroll
        # the reported direction and re-assess the next screen.
        _scroll(page, vh, verdict["dir"] if verdict["action"] == "SCROLL"
                else "down")

    raise NoMatch(
        f"grid_hunt: no match for {ask!r} in {max_screens} screens "
        f"({len(looks)} jasper looks)", looks)


def _scroll(page, vh: int, direction: str = "down") -> None:
    """One bot-gentle wheel scroll + settle. Direction comes from the
    vision verdict (SCROLL up|down) — the model drives, not a constant."""
    sign = -1 if direction == "up" else 1
    try:
        page.mouse.wheel(0, sign * int(vh * random.uniform(0.7, 0.85)))
    except Exception:
        try:
            page.evaluate(f"() => window.scrollBy(0, {sign * int(vh * 0.8)})")
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
    the receipt.)

    Uses inline vision calls with Jasper→Groq fallback, same endpoints
    as jasper_verdict.
    """
    import base64 as _b64
    from PIL import Image as _Im
    t0 = time.time()
    im = _Im.open(path)
    im.thumbnail((768, 768))
    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    b64img = _b64.b64encode(buf.getvalue()).decode()
    q = (f"Does this image show: {ask}? "
         f"Reply NO unless EVERY listed feature is clearly visible — "
         f"a partial match (wrong hair, no robotic parts, not a full "
         f"figure when one is asked for) is a NO. "
         f"Start your answer with YES or NO, then one short sentence.")

    # Try Jasper first, then Groq fallback
    for url, model, key in [
        (JASPER_URL, JASPER_MODEL, ""),
        (FALLBACK_URL, FALLBACK_MODEL, GROQ_API_KEY),
    ]:
        try:
            payload = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": q},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/jpeg;base64," + b64img}}]}],
                "max_tokens": 80,
            }).encode()
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            txt = data["choices"][0]["message"]["content"].strip()
            s = round(time.time() - t0, 1)
            return txt.upper().startswith("YES"), txt, s
        except Exception as e:
            print(f"  [verify_download] {model} failed: {e}", flush=True)
            continue

    raise RuntimeError("verify_download: no vision endpoint available")
