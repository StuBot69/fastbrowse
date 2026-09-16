#!/usr/bin/env python3
"""FastBrowse phase 5 — learning mode. Human drives, sentinel watches.

Launches Camoufox (persistent profile: logins/cookies survive across demos)
HEADED — a real window the human clicks through. A recorder injected via
add_init_script captures trusted input events + DOM snapshots per action:

  clicks   -> selector path, coords, button
  trail    -> sampled pointer positions (~10/s, the PROVEN safe path)
  hovers   -> dwell >500ms on an element (hover-anchor candidates)
  navigations -> url transitions (action-map edges)

The human clicks the floating "Finish & Save" badge when done. Output:
events.json (raw tape) + profile.json (site profile, hover anchors,
action map with trail). Replay: trail-first, computed path as fallback.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "phase3"))
sys.path.insert(0, str(HERE.parent))

from site_profiles import ProfileStore, etld1  # noqa: E402

RECORDER_JS = """(() => {
  if (window.__fb_rec) return;
  window.__fb_rec = { clicks: [], trail: [], hovers: {}, navs: [], done: false };
  const R = window.__fb_rec;
  // Restore clicks/trail saved by previous pages in this flow: clicks that
  // CAUSE navigation die with their document — localStorage carries them over.
  try {
    const s = JSON.parse(localStorage.getItem('__fb_tape') || 'null');
    if (s) {
      if (Array.isArray(s.clicks)) R.clicks = s.clicks;
      if (Array.isArray(s.trail)) R.trail = s.trail.slice(-500);
    }
  } catch (e) { /* fresh flow */ }
  const persist = () => {
    try {
      localStorage.setItem('__fb_tape', JSON.stringify(
        { clicks: R.clicks, trail: R.trail.slice(-500) }));
    } catch (e) { /* private mode etc */ }
  };
  const sel = (el) => {
    if (!el || el === document.body) return 'body';
    const parts = [];
    while (el && el !== document.body && parts.length < 5) {
      let s = el.tagName.toLowerCase();
      if (el.id) { s += '#' + el.id; parts.unshift(s); break; }
      if (el.className && typeof el.className === 'string') {
        const c = el.className.trim().split(/\\s+/).slice(0, 2).join('.');
        if (c) s += '.' + c;
      }
      const sibs = el.parentElement ? [...el.parentElement.children].filter(e => e.tagName === el.tagName) : [];
      if (sibs.length > 1) s += `:nth-of-type(${sibs.indexOf(el) + 1})`;
      parts.unshift(s);
      el = el.parentElement;
    }
    return parts.join(' > ');
  };
  let lastTrail = 0;
  document.addEventListener('mousemove', (e) => {
    const now = Date.now();
    if (now - lastTrail < 100) return;  // ~10/s
    lastTrail = now;
    R.trail.push([Math.round(e.clientX), Math.round(e.clientY), now]);
  }, { passive: true, capture: true });
  const hoverStart = {};
  document.addEventListener('mouseover', (e) => {
    hoverStart[e.target.outerHTML?.slice(0, 80)] = Date.now();
  }, { passive: true, capture: true });
  document.addEventListener('mouseout', (e) => {
    const k = e.target.outerHTML?.slice(0, 80);
    if (k && hoverStart[k]) {
      const dwell = Date.now() - hoverStart[k];
      if (dwell > 500) R.hovers[sel(e.target)] = (R.hovers[sel(e.target)] || 0) + dwell;
      delete hoverStart[k];
    }
  }, { passive: true, capture: true });
  document.addEventListener('click', (e) => {
    const r = e.target.getBoundingClientRect();
    R.clicks.push({ selector: sel(e.target), x: Math.round(e.clientX), y: Math.round(e.clientY),
      w: Math.round(r.width), h: Math.round(r.height), t: Date.now(),
      text: (e.target.innerText || '').slice(0, 80) });
    persist();
  }, { passive: true, capture: true });
  window.addEventListener('beforeunload', persist);
  // finish badge (deferred: init-script DOM may not accept appends yet)
  const badge = document.createElement('div');
  badge.id = '__fb_badge';
  badge.textContent = '● REC — click to Finish & Save';
  badge.style.cssText = 'position:fixed;top:8px;right:8px;z-index:2147483647;background:#c00;color:#fff;padding:8px 14px;border-radius:8px;font:14px sans-serif;cursor:pointer;';
  badge.onclick = () => { R.done = true; badge.textContent = '✓ saved — closing…'; };
  const addBadge = () => {
    try {
      if (document.documentElement && !document.getElementById('__fb_badge'))
        document.documentElement.appendChild(badge);
    } catch (e) { /* retry on interval */ }
  };
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', addBadge);
  else addBadge();
  setInterval(addBadge, 3000);
  try {
    new MutationObserver(addBadge).observe(document.documentElement,
      { childList: true, subtree: false });
  } catch (e) { /* observer optional */ }
})();"""


def learn_session(start_url: str, outdir: Path, profile_dir: Path,
                  headless: bool = False) -> dict:
    from camoufox.sync_api import Camoufox
    outdir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    summary: dict = {"start_url": start_url, "events": {}, "profiles": []}
    with Camoufox(headless=headless, persistent_context=True,
                  user_data_dir=str(profile_dir)) as browser:
        try:
            page = browser.new_page()  # persistent returns a context
        except Exception:
            page = browser.new_context().new_page()
        page.add_init_script(RECORDER_JS)
        page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
        try:
            # fresh flow: drop any previous session's mirrored tape
            page.evaluate("() => localStorage.removeItem('__fb_tape')")
        except Exception:
            pass
        print("LEARN: drive the flow in the Camoufox window. Click the red REC badge when done.")
        print("LEARN: closing the window also finishes (partial tape is saved).")
        last_url = page.url
        navs: list = []
        closed_early = False
        # Python-side tape mirror: page documents die on navigation, so every
        # poll merges new clicks/trail here. Survives cross-origin navs where
        # localStorage cannot follow.
        py_clicks: list = []
        py_trail: list = []
        seen_click_keys: set = set()
        while True:
            try:
                page.wait_for_timeout(1000)
            except Exception:
                closed_early = True  # window closed by human — save what we have
                break
            try:
                if page.url != last_url:
                    navs.append({"from": last_url, "to": page.url})
                    last_url = page.url
                    try:
                        page.evaluate(RECORDER_JS)
                    except Exception:
                        pass
                st = page.evaluate(
                    "() => window.__fb_rec ? {done: window.__fb_rec.done, "
                    "clicks: window.__fb_rec.clicks.length, trail: window.__fb_rec.trail.length} : null")
                # mirror the tape Python-side (deduped) — survives navs
                try:
                    bulk = page.evaluate(
                        "() => window.__fb_rec ? {clicks: window.__fb_rec.clicks, "
                        "trail: window.__fb_rec.trail.slice(-200)} : null")
                    if bulk:
                        for c in bulk.get("clicks", []):
                            key = (c.get("t"), c.get("selector"))
                            if key not in seen_click_keys:
                                seen_click_keys.add(key)
                                py_clicks.append(c)
                        py_trail.extend(bulk.get("trail", [])[-50:])
                        py_trail = py_trail[-2000:]
                except Exception:
                    pass
            except Exception:
                st = None
            if st:
                print(f"\rLEARN: {st['clicks']} clicks, {st['trail']} trail pts, url={last_url[:60]}",
                      end="", flush=True)
                if st["done"]:
                    break
        print()
        if closed_early:
            print("LEARN: window closed — saving partial tape.")
        try:
            tape = page.evaluate(
                "() => ({clicks: window.__fb_rec.clicks, trail: window.__fb_rec.trail, "
                "hovers: window.__fb_rec.hovers})")
            tape["navs"] = navs
            tape["final_url"] = page.url
        except Exception:
            tape = {"clicks": [], "trail": [], "hovers": {}, "navs": navs,
                    "final_url": last_url, "partial": True}
        # Python-side mirror wins on clicks/trail: it spans navigations.
        if py_clicks:
            known = {(c.get("t"), c.get("selector")) for c in tape.get("clicks", [])}
            tape["clicks"] = tape.get("clicks", []) + [
                c for c in py_clicks if (c.get("t"), c.get("selector")) not in known]
        if len(py_trail) > len(tape.get("trail", [])):
            tape["trail"] = py_trail
        try:
            dom = page.content()
        except Exception:
            dom = ""
        (outdir / "events.json").write_text(json.dumps(tape, indent=1)[:2_000_000], encoding="utf-8")
        # build profile: clicks become action map, long hovers become anchors
        store = ProfileStore(outdir / "profiles.json")
        actions = []
        for c in tape.get("clicks", []):
            if "__fb_badge" in (c.get("selector") or ""):
                continue
            actions.append({"label": (c.get("text") or c["selector"])[:60],
                            "selector": c["selector"], "at": [c["x"], c["y"]]})
        anchors = [{"selector": s, "dwell_ms": d}
                   for s, d in sorted(tape.get("hovers", {}).items(),
                                      key=lambda kv: -kv[1])[:10]]
        summary.update({
            "clicks": len(actions), "trail_points": len(tape.get("trail", [])),
            "navs": navs, "final_url": tape.get("final_url"),
            "dom_bytes": len(dom.encode("utf-8")),
            "action_map": actions, "hover_anchors": anchors,
            "trail_head": tape.get("trail", [])[:5],
        })
        (outdir / "profile.json").write_text(json.dumps(summary, indent=1)[:500_000], encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help="starting URL for the demo")
    ap.add_argument("--out", default="runs")
    ap.add_argument("--profile-dir", default="learn-profile")
    ap.add_argument("--headless", action="store_true", help="no window (testing only)")
    args = ap.parse_args()
    rep = learn_session(args.site, Path(args.out),
                        Path(args.profile_dir), headless=args.headless)
    print(json.dumps({k: rep[k] for k in
                      ("clicks", "trail_points", "navs", "final_url", "dom_bytes")}, indent=1))
    print(f"actions: {len(rep['action_map'])}, anchors: {len(rep['hover_anchors'])}")


if __name__ == "__main__":
    main()
