#!/usr/bin/env python3
"""FastBrowse Unsplash suite — eyes-first hunt on a harder site.

Learned Sep 2026 (opencode session):
  search: https://unsplash.com/s/photos/<dash-terms> — React-hydrated.
    Broad topics (cyborg-woman) hydrate free grids; niche slugs
    (redhead-cyborg) stall on iStock/Unsplash+ paywalled tiles. So: search
    BROAD, let Jasper's eyes find the redhead (eyes-first ethos).
  grid:   a[href^='/photos/'] with ^/photos/[A-Za-z0-9_-]+$
  photo:  "Download free" anchor + images.unsplash.com/photo-<id> raw URL
    embedded in HTML. Direct GET with ?q=80&w=1600&auto=format&fit=crop.
  download: browser-stack GET via page.context.request (no direct sockets).

Usage:
  python unsplash_suite.py --run 1 --query "cyborg woman" --ask "sexy female cyborg with red hair and visible robotic parts" --out runs/unsplash-red-1
  (--query = search terms for the grid; --ask/--verify = what Jasper looks for.)
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "phase4"))
sys.path.insert(0, str(HERE.parent / "phase3"))

import fb_config  # noqa: E402
RUNS_DIR = fb_config.RUNS_DIR
PICS_DIR = fb_config.PICS_DIR
SITES_DIR = fb_config.SITES_DIR

from blind import (  # noqa: E402
    hybrid_baseline,
    mark_site_stale,
    snapshot_bytes,
    visual_distance,
    visual_hash,
    wait_past_challenge,
    gate_similarity,
    main_region_blocks,
)

SITE = "unsplash.com"
RUN1_QUERY = "cyborg woman"
RUN1_ASK = "sexy female cyborg with red hair and visible robotic body parts"

SEARCH_URL = "https://unsplash.com/s/photos/{slug}"
ITEM_SELECTOR = "figure:has(a[href^='/photos/'])"
HREF_CHILD = "a[href^='/photos/']"
HREF_RE = r"^/photos/[A-Za-z0-9_-]+$"


def now() -> float:
    return time.time()


def slugify(query: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")


def run_flow(query: str, outdir: Path, profile_dir: Path,
             ask: str | None = None,
             blind_mode: bool = False, expect_different_slug: str | None = None,
             blind_href: str | None = None,
             verify_ask: str | None = None) -> dict:
    from camoufox.sync_api import Camoufox

    outdir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    dl_dir = outdir / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    ask = ask or query

    steps: list = []
    learned: list = []
    planner_bytes = 0
    sentinel_bytes = 0
    strikes = 0
    t_start = now()

    def step(name: str, t0: float, **kw) -> dict:
        s = {"name": name, "s": round(now() - t0, 1)}
        s.update(kw)
        steps.append(s)
        print(f"  [{name}] {s['s']}s " + " ".join(f"{k}={v}" for k, v in kw.items()),
              flush=True)
        return s

    with Camoufox(headless=True, persistent_context=True,
                  user_data_dir=str(profile_dir)) as browser:
        try:
            page = browser.new_page()
        except Exception:
            page = browser.new_context().new_page()
        try:
            page.set_viewport_size({"width": 1366, "height": 900})
        except Exception:
            pass

        downloads: list = []

        # --- 1. search page (hydrate wait: React grid needs ~7s) ---
        t0 = now()
        url = SEARCH_URL.format(slug=slugify(query))
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            step("search_load", t0, outcome=f"FAIL-GOTO {e}"[:100])
            raise SystemExit(f"search goto failed: {e}")
        wait_past_challenge(page)
        page.wait_for_timeout(7000)
        # gentle scroll to hydrate lazy tiles
        for _ in range(2):
            try:
                page.evaluate("() => window.scrollBy(0, 900)")
                page.wait_for_timeout(1800)
            except Exception:
                pass
        dom = page.content()
        dom_bytes = len(dom.encode("utf-8"))
        if dom_bytes < 20_000 or "just a moment" in (page.title() or "").lower():
            step("search_load", t0, dom_bytes=dom_bytes, outcome="BOT-WALL")
            raise SystemExit("bot wall on unsplash search — aborting")
        if "loading" in (page.title() or "").lower() and dom_bytes < 250_000:
            step("search_load", t0, dom_bytes=dom_bytes, outcome="NOT-HYDRATED")
            raise SystemExit("unsplash grid did not hydrate (niche slug likely paywalled) — try broader query")
        if not blind_mode:
            planner_bytes += dom_bytes
        learned.append({"label": "unsplash-search-url",
                        "url_template": SEARCH_URL,
                        "note": "dash-slug topic; broad terms hydrate free grid, niche stalls on iStock+"})
        baseline = hybrid_baseline(page)
        sentinel_bytes += 512 + 272
        step("search_load", t0, dom_bytes=dom_bytes,
             title=(page.title() or "")[:60],
             outcome="OK-FULL" if not blind_mode else "OK-BLIND")

        # --- 2. eyes-first pick (or blind replay) ---
        t0 = now()
        pick = None
        hunt_info: dict = {}
        if blind_mode and blind_href:
            pick = {"href": blind_href, "alt": "", "tile_number": -1,
                    "screen_idx": -1, "n_looks": 0, "replay": True}
            step("collect_links", t0, href=pick["href"][:80], outcome="OK-BLIND-REPLAY")
        else:
            try:
                from grid_hunt import grid_hunt
                if fb_config.vision_available():
                    found = grid_hunt(
                        page, ask=ask,
                        item_selector=ITEM_SELECTOR,
                        href_child=HREF_CHILD,
                        href_re=HREF_RE,
                        exclude_substr=(expect_different_slug or None),
                        exclude_card_re=(r"sponsored|unsplash\+|promoted"),
                        min_tiles=3,
                        max_screens=6)
                    how_pick = "OK-EYES"
                    note = (f"jasper tile {found.get('tile_number')} "
                            f"screen {found.get('screen_idx')}, {found.get('n_looks')} looks")
                else:
                    raise RuntimeError("no vision endpoint; abort")
                pick = {"href": found["href"], "alt": found.get("alt", "")}
                hunt_info = {k: found[k] for k in
                             ("tile_number", "screen_idx", "n_looks", "n_tiles_seen") if k in found}
                sentinel_bytes += found.get("n_looks", 0) * 800
                learned.append({"label": "photo-link",
                                "selector": "grid_hunt eyes-first pick :: " + ITEM_SELECTOR,
                                "href": pick["href"], "note": note})
                step("collect_links", t0, href=pick["href"][:80],
                     alt=(pick.get("alt") or "")[:60], **hunt_info, outcome=how_pick)
            except RuntimeError as e:
                full = snapshot_bytes(page)
                planner_bytes += full
                step("collect_links", t0, outcome="FAIL-NO-MATCH", planner_bytes=full)
                raise SystemExit(str(e)[:200])

        # --- 3. gate, then photo page ---
        t0 = now()
        if blind_mode:
            _, live = main_region_blocks(page)
            sim = gate_similarity(live, baseline["blocks"])
            live_vh, _ = visual_hash(page)
            vdist = visual_distance(live_vh, baseline.get("visual", ""))
            sentinel_bytes += 512 + 272
            if sim < 0.95 and vdist > 12:
                strikes += 1
                mark_site_stale(SITE, f"unsplash search gate: sim={sim:.3f} vdist={vdist}")
                full = snapshot_bytes(page)
                planner_bytes += full
                step("gate_before_nav", t0, sim=round(sim, 3), vdist=vdist,
                     outcome="HALT-SNAPSHOT", planner_bytes=full)
            else:
                step("gate_before_nav", t0, sim=round(sim, 3), vdist=vdist, outcome="GATE-PASS")
            t0 = now()
        before_url = page.url
        photo_url = ("https://unsplash.com" + pick["href"]
                     if pick["href"].startswith("/") else pick["href"])
        try:
            page.goto(photo_url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            step("photo_load", t0, outcome=f"FAIL-GOTO {e}"[:100])
            raise SystemExit(f"photo goto failed: {e}")
        page.wait_for_timeout(4000)
        dom2 = page.content()
        dom2_bytes = len(dom2.encode("utf-8"))
        if not blind_mode:
            planner_bytes += dom2_bytes
        ok_nav = page.url != before_url
        if not ok_nav:
            strikes += 1
        baseline = hybrid_baseline(page)
        step("photo_load", t0, dom_bytes=dom2_bytes,
             title=(page.title() or "")[:60],
             outcome="OK-BLIND" if ok_nav else "FAIL-NAV")

        # --- 4. raw image URL (Download-free href or embedded raw) ---
        t0 = now()
        orig = None
        try:
            hrefs = page.eval_on_selector_all(
                "a[href*='images.unsplash.com']",
                "els => els.map(e => e.getAttribute('href'))")
            cands = [h for h in (hrefs or []) if h and "images.unsplash.com/photo-" in h]
            orig = cands[0] if cands else None
        except Exception:
            pass
        if not orig:
            try:
                html = page.content()
                m = re.findall(r"https://images\.unsplash\.com/photo-[^\"'&\s]+", html)
                orig = m[0] if m else None
            except Exception:
                pass
        if not orig:
            full = snapshot_bytes(page)
            planner_bytes += full
            step("find_original", t0, outcome="FAIL-NO-ORIG", planner_bytes=full)
            raise SystemExit("no images.unsplash.com raw URL on photo page")
        if "w=" not in orig:
            orig += ("&" if "?" in orig else "?") + "q=80&w=1600&auto=format&fit=crop"
        learned.append({"label": "original-file",
                        "selector": "a[href*='images.unsplash.com'] or embedded photo-<id> raw",
                        "href": orig[:120],
                        "note": "direct raw GET, no dialog"})
        step("find_original", t0, href_host="images.unsplash.com", outcome="OK-BLIND")

        # --- 5. download via browser stack ---
        t0 = now()
        try:
            resp = page.context.request.get(orig, timeout=60000)
            body = resp.body()
            if len(body) < 20_000:
                raise RuntimeError(f"download too small ({len(body)}B)")
            pid = re.search(r"photo-([A-Za-z0-9-]+)", orig)
            name = f"unsplash-{pid.group(1)[:20] if pid else 'img'}.jpg"
            dest = dl_dir / name
            dest.write_bytes(body)
            downloads.append({"file": dest.name, "bytes": len(body), "url": orig[:120]})
            how = "request-get"
        except Exception as e:
            full = snapshot_bytes(page)
            planner_bytes += full
            step("save", t0, outcome=f"FAIL-DL {e}"[:100], planner_bytes=full)
            raise SystemExit(f"download failed: {e}")
        step("save", t0, via=how, saved=downloads[-1]["file"],
             bytes=downloads[-1]["bytes"], outcome="OK-BLIND")

        # --- 6. vision receipt ---
        if downloads:
            t0 = now()
            if not fb_config.vision_available():
                step("vision_check", t0, outcome="SKIP-NO-VISION")
                report_vision = {"verdict": "SKIP", "detail": "no vision endpoint"}
            else:
                try:
                    from grid_hunt import verify_download
                    ok, txt, vs = verify_download(
                        str(dl_dir / downloads[0]["file"]), verify_ask or ask)
                    step("vision_check", t0, verdict="YES" if ok else "NO",
                         detail=txt[:150], jasper_s=vs, outcome="OK-EYES")
                    report_vision = {"verdict": "YES" if ok else "NO",
                                     "detail": txt[:250], "jasper_s": vs}
                except Exception as e:
                    step("vision_check", t0, outcome=f"SKIP {e}"[:100])
                    report_vision = {"verdict": "SKIP", "detail": str(e)[:120]}
        else:
            report_vision = {"verdict": "SKIP", "detail": "no download"}

        try:
            browser.close()
        except Exception:
            pass

    total_s = round(now() - t_start, 1)
    report = {
        "query": query, "ask": ask, "site": SITE,
        "mode": "blind" if blind_mode else "full-observation",
        "steps": steps, "blockers": [], "downloads": downloads,
        "learned_actions": learned, "total_s": total_s,
        "planner_bytes": planner_bytes, "sentinel_bytes": sentinel_bytes,
        "strikes": strikes,
        "tokens": {"llm_prompt": 0, "llm_completion": 0, "llm_calls": 0,
                   "planner_bytes": planner_bytes,
                   "est_observation_tokens": planner_bytes // 4,
                   "note": "browser loop zero metered LLM calls; jasper local"},
        "vision": report_vision,
    }
    (outdir / "run-report.json").write_text(json.dumps(report, indent=1))
    (outdir / "action_map.json").write_text(json.dumps(learned, indent=1))
    try:
        PICS_DIR.mkdir(parents=True, exist_ok=True)
        import shutil
        for d in downloads:
            shutil.copy2(dl_dir / d["file"], PICS_DIR / d["file"])
        report["pics_sink"] = str(PICS_DIR)
    except Exception as e:
        report["pics_sink_error"] = str(e)[:120]
    try:
        import site_store
        prior = site_store.load_profile(SITE)
        if not prior.get("needs_relearn"):
            site_store.save_profile(
                SITE,
                {"last_verified": time.strftime("%Y-%m-%d")},
                action_map=learned)
        if not blind_mode and downloads:
            site_store.save_profile(
                SITE,
                {"run1_href": pick["href"],
                 "run1_file": downloads[-1].get("file")})
        if downloads and not downloads[0].get("error"):
            site_store.add_pick(SITE, {
                "href": pick["href"],
                "file": downloads[-1].get("file"),
                "vision": report_vision.get("verdict"),
                "query": query,
                "run": outdir.name})
        site_store.index_run(SITE, outdir.name)
    except Exception as e:
        report["site_save_error"] = str(e)[:120]
    (outdir / "run-report.json").write_text(json.dumps(report, indent=1))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, choices=["1", "2"])
    ap.add_argument("--query", default=None)
    ap.add_argument("--ask", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--tape", default=None)
    ap.add_argument("--profile-dir", default=None)
    ap.add_argument("--verify", default=None)
    ap.add_argument("--blind-href", default=None)
    args = ap.parse_args()

    if args.run == "1":
        out = Path(args.out or str(RUNS_DIR / "unsplash-1"))
        rep = run_flow(args.query or RUN1_QUERY, out,
                       Path(args.profile_dir or str(out / "profile")),
                       ask=args.ask or RUN1_ASK, verify_ask=args.verify or args.ask)
    else:
        if not args.tape:
            raise SystemExit("--run 2 needs --tape")
        import site_store
        prior = site_store.load_profile(SITE)
        out = Path(args.out or str(RUNS_DIR / "unsplash-2"))
        rep = run_flow(args.query or RUN1_QUERY, out,
                       Path(args.profile_dir or str(out / "profile")),
                       ask=args.ask or RUN1_ASK,
                       blind_mode=not prior.get("needs_relearn"),
                       blind_href=args.blind_href,
                       verify_ask=args.verify or args.ask,
                       expect_different_slug=(prior.get("run1_href", "").rsplit("-", 1)[-1][:12] or None))

    print(json.dumps({k: rep[k] for k in ("mode", "total_s", "planner_bytes", "strikes")}, indent=1))
    print("VISION:", json.dumps(rep["vision"], indent=1))
    for s in rep["steps"]:
        print(f" - {s['name']}: {s.get('outcome')} {s['s']}s")


if __name__ == "__main__":
    main()
