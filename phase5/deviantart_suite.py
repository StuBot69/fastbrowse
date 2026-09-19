#!/usr/bin/env python3
"""FastBrowse DeviantArt suite — the hard site: consent/login nudges,
mature-blur tiles, dynamic grid. Tasteful-sexy cyborg art hunt.

Learned Sep 2026 (opencode session):
  search: https://www.deviantart.com/search?q=<terms> — works logged-out,
    ~9.8K results for "sexy female cyborg", 1MB DOM.
  grid:   a[href*='/art/'] with /art/<slug>-<id>$ — 13 big tiles on screen.
  MATURE-BLUR: logged-out deviations serve blurred wixmp previews
    (blur_16/18/32 in the URL). Full-res + Download button need LOGIN.
    Logged-out hunts therefore record NEEDS-LOGIN instead of fetching
    blurred files as results.
  login path: persistent profile dir keeps the session — log in once via
    the headed learner (phase5/learn.py), headless reuses it afterwards.

Usage:
  python deviantart_suite.py --run 1 --query "sexy female cyborg" --out runs/da-sexy-1
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

SITE = "deviantart.com"
RUN1_QUERY = "sexy female cyborg"

SEARCH_URL = "https://www.deviantart.com/search?q={q}"
ITEM_SELECTOR = "a[href*='/art/']"
HREF_RE = r"/art/.+-\d+$"
CARD_EXCLUDE = r"sponsored|promoted"


def now() -> float:
    return time.time()


def run_flow(query: str, outdir: Path, profile_dir: Path,
             blind_mode: bool = False, expect_different_slug: str | None = None,
             blind_href: str | None = None,
             verify_ask: str | None = None) -> dict:
    from camoufox.sync_api import Camoufox

    outdir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    dl_dir = outdir / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)

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

        # --- 1. search page ---
        t0 = now()
        url = SEARCH_URL.format(q=query.replace(" ", "%20"))
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            step("search_load", t0, outcome=f"FAIL-GOTO {e}"[:100])
            raise SystemExit(f"search goto failed: {e}")
        wait_past_challenge(page)
        page.wait_for_timeout(4000)
        dom = page.content()
        dom_bytes = len(dom.encode("utf-8"))
        if dom_bytes < 20_000 or "just a moment" in (page.title() or "").lower():
            step("search_load", t0, dom_bytes=dom_bytes, outcome="BOT-WALL")
            raise SystemExit("bot wall on deviantart search — aborting")
        if not blind_mode:
            planner_bytes += dom_bytes
        learned.append({"label": "da-search-url",
                        "url_template": SEARCH_URL,
                        "note": "%20-joined terms; deviations /<user>/art/<slug>-<id>"})
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
                if not fb_config.vision_available():
                    raise RuntimeError("no vision endpoint; abort")
                found = grid_hunt(
                    page, ask=query,
                    item_selector=ITEM_SELECTOR,
                    href_re=HREF_RE,
                    exclude_substr=(expect_different_slug or None),
                    exclude_card_re=CARD_EXCLUDE,
                    min_tiles=3,
                    max_screens=6)
                how_pick = "OK-EYES"
                note = (f"jasper tile {found.get('tile_number')} "
                        f"screen {found.get('screen_idx')}, "
                        f"{found.get('n_looks')} looks")
                pick = {"href": found["href"], "alt": found.get("alt", "")}
                hunt_info = {k: found[k] for k in
                             ("tile_number", "screen_idx", "n_looks",
                              "n_tiles_seen") if k in found}
                sentinel_bytes += found.get("n_looks", 0) * 800
                learned.append({"label": "deviation-link",
                                "selector": "grid_hunt eyes-first pick :: " + ITEM_SELECTOR,
                                "href": pick["href"], "note": note})
                step("collect_links", t0, href=pick["href"][:80],
                     alt=(pick.get("alt") or "")[:60], **hunt_info, outcome=how_pick)
            except RuntimeError as e:
                full = snapshot_bytes(page)
                planner_bytes += full
                looks = getattr(e, "looks", [])
                for l in looks:
                    print(f"    look s{l.get('screen')}: {l.get('verdict')} "
                          f"{(l.get('detail') or '')[:70]!r} tiles={l.get('n_tiles')}",
                          flush=True)
                step("collect_links", t0, outcome="FAIL-NO-MATCH",
                     planner_bytes=full, n_looks=len(looks))
                try:
                    browser.close()
                except Exception:
                    pass
                total_s = round(now() - t_start, 1)
                (outdir / "run-report.json").write_text(json.dumps({
                    "query": query, "site": SITE, "mode": "full-observation",
                    "outcome": "FAIL-NO-MATCH", "error": str(e)[:200],
                    "steps": steps, "downloads": [], "total_s": total_s,
                    "planner_bytes": planner_bytes,
                    "sentinel_bytes": sentinel_bytes, "strikes": strikes,
                    "looks": looks,
                    "tokens": {"llm_prompt": 0, "llm_completion": 0,
                               "llm_calls": 0, "planner_bytes": planner_bytes,
                               "est_observation_tokens": planner_bytes // 4},
                }, indent=1))
                raise SystemExit(str(e)[:200])

        # --- 3. deviation page ---
        t0 = now()
        photo_url = (pick["href"] if pick["href"].startswith("http")
                     else "https://www.deviantart.com" + pick["href"])
        try:
            page.goto(photo_url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            step("photo_load", t0, outcome=f"FAIL-GOTO {e}"[:100])
            raise SystemExit(f"deviation goto failed: {e}")
        page.wait_for_timeout(4000)
        dom2 = page.content()
        dom2_bytes = len(dom2.encode("utf-8"))
        if not blind_mode:
            planner_bytes += dom2_bytes
        step("photo_load", t0, dom_bytes=dom2_bytes,
             title=(page.title() or "")[:60], outcome="OK-BLIND")

        # mature-filtered deviations hide behind a reveal button —
        # click it so the full-res URLs enter the DOM.
        try:
            reveal = page.locator("button:has-text('Show Deviation')")
            if reveal.count() >= 1 and reveal.first.is_visible():
                reveal.first.click(timeout=5000)
                page.wait_for_timeout(2500)
                dom2 = page.content()
                dom2_bytes = len(dom2.encode("utf-8"))
                learned.append({"label": "mature-reveal",
                                "selector": "button:has-text('Show Deviation')",
                                "note": "mature filter gate; click to unhide full-res"})
        except Exception:
            pass

        # --- 4. full-res file: unblurred wixmp URL or Download button.
        # Logged-out pages only embed blurred previews (blur_N in URL) —
        # those are NOT results. No clean file = NEEDS-LOGIN, honest stop.
        t0 = now()
        # Learned from the human tape (da-debug): the download is an
        # icon-anchor to /download/<id>/<token>, NOT a "Download" text
        # button. Authenticated GET on that href returns the full file.
        try:
            dll = page.eval_on_selector_all(
                "a[href*='/download/']",
                "els => els.map(e => e.getAttribute('href'))")
            dll = [h for h in (dll or []) if h and "/download/" in h]
        except Exception:
            dll = []
        dl_href = None
        if dll:
            dl_href = dll[0] if dll[0].startswith("http") else \
                "https://www.deviantart.com" + dll[0]
        try:
            urls = re.findall(r"https://images-wixmp[^\"'&\s]+", dom2)
            urls = list(dict.fromkeys(urls))
        except Exception:
            urls = []

        def _width(u: str) -> int:
            m = re.search(r"/w_(\d+)", u)
            return int(m.group(1)) if m else 0

        # widest-first, skipping blurred previews (blur_ in URL or -NW
        # thumbnail filename suffixes). Logged-out pages usually have no
        # clean full-res at all — then this list empties into NEEDS-LOGIN.
        cands = sorted(
            (u for u in urls
             if "blur" not in u and not re.search(r"-\d+w\.", u)),
            key=_width, reverse=True)[:3]
        try:
            dl_btn = page.locator("a:has-text('Download'), button:has-text('Download')")
            has_dl = dl_btn.count() > 0 and dl_btn.first.is_visible()
        except Exception:
            has_dl = False
        learned.append({"label": "deviation-file",
                        "selector": "a[href*='/download/'] icon-anchor (human-tape learned)",
                        "note": f"dl-link={bool(dl_href)}, {len(urls)} wixmp urls, "
                                f"{len(cands)} clean cands, download-btn={has_dl}"})
        if not dl_href and not cands and not has_dl:
            step("find_original", t0, outcome="NEEDS-LOGIN",
                 detail="only blurred previews logged-out")
            raise SystemExit("NEEDS-LOGIN: deviantart serves blurred previews "
                             "logged-out — log in once via the headed learner "
                             "so the persistent profile carries the session")
        step("find_original", t0, href_host="images-wixmp",
             n_cands=len(cands), outcome="OK-BLIND")

        # --- 5. download via browser stack. Order: /download/ link
        # (full file, authed GET), then widest clean wixmp candidate,
        # then the Download button event. First body >200KB wins.
        t0 = now()
        saved = None
        how = None
        try:
            if dl_href:
                try:
                    resp = page.context.request.get(dl_href, timeout=60000)
                    body = resp.body()
                    cd = ""
                    try:
                        cd = resp.headers.get("content-disposition", "")
                    except Exception:
                        pass
                    m = re.search(r'filename="?([^";]+)"?', cd)
                    name = (m.group(1) if m else dl_href.rstrip("/").rsplit("/", 1)[-1][:60])
                    name = name.split("?")[0].split("&")[0][:80] or "deviant.jpg"
                    if not re.search(r"\.(jpg|jpeg|png|webp)$", name, re.I):
                        name += ".jpg"
                    if len(body) >= 200_000:
                        dest = dl_dir / name
                        dest.write_bytes(body)
                        downloads.append({"file": dest.name,
                                          "bytes": len(body),
                                          "url": dl_href[:120]})
                        saved, how = dest, "download-link-get"
                except Exception:
                    pass
            if not saved:
                tried = 0
                for orig in cands:
                    tried += 1
                    try:
                        resp = page.context.request.get(orig, timeout=60000)
                        body = resp.body()
                    except Exception:
                        continue
                    if len(body) < 200_000:
                        continue
                    m = re.search(r"/([^/?]+\.(?:jpg|jpeg|png|webp))", orig)
                    name = (m.group(1) if m else "deviant.bin").split("-375w")[0]
                    if not re.search(r"\.(jpg|jpeg|png|webp)$", name, re.I):
                        name += ".jpg"
                    dest = dl_dir / name
                    dest.write_bytes(body)
                    downloads.append({"file": dest.name, "bytes": len(body),
                                      "url": orig[:120]})
                    saved, how = dest, "request-get"
                    break
            if not saved:
                if has_dl:
                    # logged-in Download button: real file download event.
                    # Fire-and-forget click inside expect_download; the
                    # handler saves whatever arrives (>200KB or it fails).
                    try:
                        with page.expect_download(timeout=30000) as dl_info:
                            try:
                                btn = page.locator(
                                    "a:has-text('Download'), "
                                    "button:has-text('Download')").first
                                btn.click(timeout=8000)
                            except Exception:
                                pass
                        try:
                            dl = dl_info.value
                            dest = dl_dir / (dl.suggested_filename or "deviant.bin")
                            dl.save_as(str(dest))
                            if dest.stat().st_size < 200_000:
                                try:
                                    dest.unlink()
                                except Exception:
                                    pass
                                raise RuntimeError(
                                    f"button file too small ({dest.stat().st_size}B)")
                            downloads.append({"file": dest.name,
                                              "bytes": dest.stat().st_size,
                                              "url": photo_url[:120]})
                            saved, how = dest, "download-event"
                        except Exception as e:
                            raise RuntimeError(f"button download failed: {e}"[:120])
                    except Exception as e:
                        raise RuntimeError(str(e)[:160])
                else:
                    raise RuntimeError(
                        f"no full-res logged-out (tried {tried} clean cands)")
        except Exception as e:
            full = snapshot_bytes(page)
            planner_bytes += full
            step("save", t0, outcome=f"FAIL-DL {e}"[:100], planner_bytes=full)
            try:
                browser.close()
            except Exception:
                pass
            total_s = round(now() - t_start, 1)
            (outdir / "run-report.json").write_text(json.dumps({
                "query": query, "site": SITE,
                "mode": "blind" if blind_mode else "full-observation",
                "outcome": "FAIL-DL", "error": str(e)[:200],
                "pick": pick, "steps": steps, "downloads": downloads,
                "total_s": total_s, "planner_bytes": planner_bytes,
                "sentinel_bytes": sentinel_bytes, "strikes": strikes,
                "tokens": {"llm_prompt": 0, "llm_completion": 0,
                           "llm_calls": 0, "planner_bytes": planner_bytes,
                           "est_observation_tokens": planner_bytes // 4},
            }, indent=1))
            if "no full-res logged-out" in str(e):
                raise SystemExit("NEEDS-LOGIN: " + str(e)[:160])
            raise SystemExit(f"download failed: {e}")
        step("save", t0, via=how, saved=downloads[-1]["file"],
             bytes=downloads[-1]["bytes"], outcome="OK-BLIND")

        # --- 6. vision receipt (tasteful gate holds here too) ---
        t0 = now()
        if not fb_config.vision_available():
            step("vision_check", t0, outcome="SKIP-NO-VISION")
            report_vision = {"verdict": "SKIP", "detail": "no vision endpoint"}
        else:
            try:
                from grid_hunt import verify_download
                ok, txt, vs = verify_download(
                    str(dl_dir / downloads[0]["file"]), verify_ask or query)
                step("vision_check", t0, verdict="YES" if ok else "NO",
                     detail=txt[:150], jasper_s=vs, outcome="OK-EYES")
                report_vision = {"verdict": "YES" if ok else "NO",
                                 "detail": txt[:250], "jasper_s": vs}
            except Exception as e:
                step("vision_check", t0, outcome=f"SKIP {e}"[:100])
                report_vision = {"verdict": "SKIP", "detail": str(e)[:120]}

        try:
            browser.close()
        except Exception:
            pass

    total_s = round(now() - t_start, 1)
    report = {
        "query": query, "site": SITE,
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
        import site_store
        prior = site_store.load_profile(SITE)
        if not prior.get("needs_relearn"):
            site_store.save_profile(SITE, {"last_verified": time.strftime("%Y-%m-%d")},
                                    action_map=learned)
        if not blind_mode and downloads:
            site_store.save_profile(SITE, {"run1_href": pick["href"],
                                           "run1_file": downloads[-1].get("file")})
        if downloads and not downloads[0].get("error"):
            site_store.add_pick(SITE, {"href": pick["href"],
                                       "file": downloads[-1].get("file"),
                                       "vision": report_vision.get("verdict"),
                                       "query": query, "run": outdir.name})
        site_store.index_run(SITE, outdir.name)
    except Exception as e:
        report["site_save_error"] = str(e)[:120]
        (outdir / "run-report.json").write_text(json.dumps(report, indent=1))
    try:
        PICS_DIR.mkdir(parents=True, exist_ok=True)
        import shutil
        for d in downloads:
            if d.get("file") and not d.get("error"):
                shutil.copy2(dl_dir / d["file"], PICS_DIR / d["file"])
        report["pics_sink"] = str(PICS_DIR)
    except Exception as e:
        report["pics_sink_error"] = str(e)[:120]
    (outdir / "run-report.json").write_text(json.dumps(report, indent=1))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, choices=["1", "2"])
    ap.add_argument("--query", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--tape", default=None)
    ap.add_argument("--profile-dir", default=None)
    ap.add_argument("--verify", default=None)
    ap.add_argument("--blind-href", default=None)
    args = ap.parse_args()

    if args.run == "1":
        out = Path(args.out or str(RUNS_DIR / "da-1"))
        prof = Path(args.profile_dir) if args.profile_dir else out / "profile"
        if not args.profile_dir:
            # reuse the one-time login: seed the throwaway run profile
            # from the canonical session so no fresh-device 2SV fires.
            import session_store
            seed = session_store.seed_profile("deviantart", prof)
            print(f"  [session] {seed['reason']}", flush=True)
        rep = run_flow(args.query or RUN1_QUERY, out, prof,
                       verify_ask=args.verify)
    else:
        if not args.tape:
            raise SystemExit("--run 2 needs --tape")
        import site_store
        prior = site_store.load_profile(SITE)
        out = Path(args.out or str(RUNS_DIR / "da-2"))
        prof = Path(args.profile_dir) if args.profile_dir else out / "profile"
        if not args.profile_dir:
            import session_store
            seed = session_store.seed_profile("deviantart", prof)
            print(f"  [session] {seed['reason']}", flush=True)
        rep = run_flow(args.query or RUN1_QUERY, out,
                       prof,
                       blind_mode=not prior.get("needs_relearn"),
                       blind_href=args.blind_href,
                       verify_ask=args.verify,
                       expect_different_slug=(prior.get("run1_href", "").rsplit("-", 1)[-1][:12] or None))

    print(json.dumps({k: rep[k] for k in ("mode", "total_s", "planner_bytes", "strikes")}, indent=1))
    print("VISION:", json.dumps(rep.get("vision", {}), indent=1))
    for s in rep["steps"]:
        print(f" - {s['name']}: {s.get('outcome')} {s['s']}s")


if __name__ == "__main__":
    main()
