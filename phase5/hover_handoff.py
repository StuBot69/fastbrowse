#!/usr/bin/env python3
"""FastBrowse hover handoff — phase5 recorded hovers -> phase6 hover profiles.

Takes a phase5 run dir (events.json with hover_anchors from the recorder),
and for each long-dwell hover anchor, promotes it to a phase6 hover profile:

  1. load tape, keep anchors with dwell_ms >= threshold (default 800)
  2. for each anchor (deepest-first so nested children record after parents):
       - dedupe by CSS-prefix overlap (drop child anchors under a parent)
       - call hover.record() with the recorder's own dwell
  3. verify each new profile with hover.play() on the fixture
  4. save to hover-profiles.json; report record/play outcomes

Usage:
  python hover_handoff.py --tape runs-fixture --url <fixture-or-site-url> \
      [--engine camofox] [--headless] [--out runs-handoff] [--min-dwell 800]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "phase6"))
sys.path.insert(0, str(HERE.parent / "phase4"))
sys.path.insert(0, str(HERE.parent / "phase3"))

import hover  # phase6
from site_profiles import ProfileStore  # noqa: E402
from camoufox.sync_api import Camoufox
from playwright.sync_api import sync_playwright


def load_anchors(tapedir: Path, min_dwell: int) -> list[dict]:
    ev = tapedir / "events.json"
    if not ev.exists():
        raise SystemExit(f"no events.json in {tapedir}")
    tape = json.loads(ev.read_text())
    hovers = tape.get("hovers", {})
    anchors = [{"selector": s, "dwell_ms": d}
               for s, d in sorted(hovers.items(), key=lambda kv: -kv[1])
               if d >= min_dwell and "__fb_badge" not in s]
    return anchors


def prune_children(anchors: list[dict]) -> list[dict]:
    """Drop an anchor whose selector path is under another anchor's path.
    Long dwell on a lightbox link inside a hovered container: keep the more
    specific (child) one as the anchor — it is what actually reveals."""
    keep: list[dict] = []
    for a in sorted(anchors, key=lambda x: -x["dwell_ms"]):
        s = a["selector"]
        under = [k["selector"] for k in keep if s != k["selector"]
                 and (s.startswith(k["selector"].split(">")[0].strip())
                      or k["selector"].startswith(s.split(">")[0].strip()))]
        if not under:
            keep.append(a)
    return keep


def target_for(anchor: dict, page) -> str:
    """Pick a target inside the revealed menu: first visible clickable
    descendant; the anchor itself is the fallback."""
    sel = anchor["selector"]
    for cand in (f"{sel} a", f"{sel} [role=menuitem]", f"{sel} button",
                 f"{sel} li a"):
        try:
            loc = page.locator(cand)
            if loc.count() > 0 and loc.first.is_visible():
                return cand
        except Exception:
            continue
    return sel


def anchor_exists(page, sel: str) -> bool:
    try:
        loc = page.locator(sel)
        return loc.count() > 0 and loc.first.bounding_box() is not None
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--engine", default="camofox", choices=["chromium", "camofox"])
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--out", default="runs-handoff")
    ap.add_argument("--min-dwell", type=int, default=800)
    ap.add_argument("--store", default="hover-profiles.json")
    args = ap.parse_args()

    anchors = load_anchors(Path(args.tape), args.min_dwell)
    anchors = prune_children(anchors)
    print(f"HANDOFF: {len(anchors)} anchors (dwell>={args.min_dwell})")
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    store = ProfileStore(outdir / args.store)
    report: dict = {"tape": args.tape, "anchors": [], "play": []}

    if args.engine == "camofox":
        ctxman = Camoufox(headless=args.headless)
        browser = ctxman.__enter__()
        page = hover_page_new(browser)
        _run(page, anchors, store, report, args)
        ctxman.__exit__(None, None, None)
    else:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=args.headless)
            page = browser.new_page(viewport={"width": 1366, "height": 900})
            _run(page, anchors, store, report, args)
            browser.close()

    (outdir / "handoff-report.json").write_text(json.dumps(report, indent=1)[:200_000])
    print(f"report -> {outdir / 'handoff-report.json'}")


def hover_page_new(browser):
    try:
        return browser.new_page(viewport={"width": 1366, "height": 900})
    except TypeError:
        return browser.new_page()


def _run(page, anchors: list, store: ProfileStore, report: dict, args) -> None:
    page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)
    for a in anchors:
        sel = a["selector"]
        if not anchor_exists(page, sel):
            # hidden-at-rest anchor (submenu item): it lives inside a parent
            # anchor's reveal — record the PARENT that reveals its container.
            parent = sel.split(" > ")[0] if " > " in sel else None
            if parent and anchor_exists(page, parent):
                print(f"  skip hidden anchor {sel[:50]} — using parent {parent[:50]}")
                a = dict(a, selector=parent)
                sel = parent
            else:
                report["anchors"].append({"selector": sel, "error": "no box at rest"})
                print(f"  skip: {sel[:60]} has no box at rest")
                continue
        target = target_for(a, page)
        try:
            rec = hover.record(page, sel, target, store,
                               dwell_ms=a["dwell_ms"],
                               pre_hover="" if sel == a["selector"] else a["selector"])
            report["anchors"].append({"selector": sel, "target": target,
                                      "created": rec["created"],
                                      "reveals": rec["reveals"]})
            print(f"  record: {sel[:60]} -> reveals={rec['reveals']!r} target={target[:40]!r}", flush=True)
            prof = store.find(hover.etld1(page.url), f"hover:{sel}->{target}")
            res = hover.play(page, prof)
            report["play"].append({"anchor": sel, "ok": res.get("ok"),
                                   "effect": res.get("effect", "")[:80]})
            print(f"  play:   ok={res.get('ok')} effect={res.get('effect', '')[:70]}", flush=True)
        except Exception as e:
            report["anchors"].append({"selector": sel, "error": str(e)[:150]})
            print(f"  ERROR on {sel[:60]}: {e}", flush=True)
    store.save()
    print("HANDOFF DONE", flush=True)


if __name__ == "__main__":
    main()
