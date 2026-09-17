#!/usr/bin/env python3
"""FastBrowse Pixabay suite — refined ask: white-haired female cyborg.

Test 1 (learn, full observation): search Pixabay, pick a result whose
  alt-text/slug matches WHITE-HAIR + FEMALE + CYBORG (not a random first
  link — that's how sydney-1 fetched a harbour), walk the download dialog,
  save the file. Every working selector is recorded to action_map.json +
  sites/pixabay.com.json.
Test 2 (blind, hybrid gate): a DIFFERENT query, same flow via the learned
  map. TEXT gate first, dHash second opinion; both fail = DRIFT + stale
  flag. Compare times + observation bytes.

Token log per run: the browser loop makes zero LLM calls (llm=0). The
observation cost that WOULD be tokens is logged as planner_bytes plus an
estimated token count (bytes/4, labelled estimate).

Usage:
  python pixabay_suite.py --run 1 [--out runs/cyborg-1]
  python pixabay_suite.py --run 2 --tape runs/cyborg-1 [--out runs/cyborg-2]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS_DIR = HERE / "runs"
PICS_DIR = Path.home() / "Downloads" / "pictures"
SITES_DIR = HERE / "sites"
sys.path.insert(0, str(HERE.parent / "phase4"))
sys.path.insert(0, str(HERE.parent / "phase3"))
sys.path.insert(0, str(HERE.parent))

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

SITE = "pixabay.com"
RUN1_QUERY = "white haired female cyborg"
RUN2_QUERY = "white haired cyborg woman"

WANT_HAIR = re.compile(r"white|silver|grey|gray|blond|platinum|red|redhead|ginger|auburn|copper", re.I)
WANT_WHO = re.compile(r"wom[ae]n|female|girl|lady|cyborg|robot|android", re.I)


def now() -> float:
    return time.time()


def score_candidate(alt: str, href: str) -> int:
    """Refined-ask scorer: +2 hair match, +1 who match (alt and slug)."""
    blob = f"{alt} {href}"
    s = 0
    if WANT_HAIR.search(blob):
        s += 2
    if WANT_WHO.search(blob):
        s += 1
    return s


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

        downloads: list = []

        def _on_download(download):
            try:
                dest = dl_dir / (download.suggested_filename or "download.bin")
                download.save_as(str(dest))
                downloads.append({"file": dest.name,
                                  "bytes": dest.stat().st_size,
                                  "url": download.url[:120]})
                print(f"\n  DL: saved {dest.name} {dest.stat().st_size}B")
            except Exception as e:
                downloads.append({"error": str(e)[:120], "url": download.url[:120]})

        try:
            page.on("download", _on_download)
        except Exception:
            pass

        # --- 1. search page ---
        t0 = now()
        url = "https://pixabay.com/images/search/" + query.replace(" ", "%20") + "/"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            step("search_load", t0, outcome=f"FAIL-GOTO {e}"[:100])
            raise SystemExit(f"search goto failed: {e}")
        wait_past_challenge(page)
        page.wait_for_timeout(2500)
        dom = page.content()
        dom_bytes = len(dom.encode("utf-8"))
        if dom_bytes < 20_000 or "just a moment" in (page.title() or "").lower():
            step("search_load", t0, dom_bytes=dom_bytes, outcome="BOT-WALL")
            raise SystemExit("bot wall on pixabay search — aborting")
        if not blind_mode:
            planner_bytes += dom_bytes
        learned.append({"label": "pixabay-search-url",
                        "url_template": "https://pixabay.com/images/search/<terms>/",
                        "note": "%20-joined terms; photo pages /photos|illustrations|vectors/<slug>-<id>/"})
        baseline = hybrid_baseline(page)
        sentinel_bytes += 512 + 272
        step("search_load", t0, dom_bytes=dom_bytes,
             title=(page.title() or "")[:60],
             outcome="OK-FULL" if not blind_mode else "OK-BLIND")

        # --- 2. consent: OneTrust reject (profiled selector, 0 snapshot) ---
        t0 = now()
        try:
            rej = page.locator("#onetrust-reject-all-handler")
            if rej.count() == 1 and rej.first.is_visible():
                rej.first.click(timeout=5000)
                page.wait_for_timeout(1200)
                learned.append({"label": "consent-reject",
                                "selector": "#onetrust-reject-all-handler",
                                "note": "OneTrust; count==1 visible guard (rule #5)"})
                step("consent_reject", t0, outcome="OK-BLIND")
            else:
                step("consent_reject", t0, outcome="SKIP-NONE-VISIBLE")
        except Exception as e:
            step("consent_reject", t0, outcome=f"SKIP-ERR {e}"[:80])

        # --- 3. EYES-FIRST pick: grid_hunt shows Jasper the results grid
        # with numbered tiles; it picks the tile that matches the ask.
        # No alt-text guessing (rule #16: alt lies — "black and white"
        # style matched "white hair"). Nothing downloads until eyes verify.
        # Blind replays skip the hunt: they replay the learned href.
        t0 = now()
        pick = None
        hunt_info: dict = {}
        if blind_mode and blind_href:
            pick = {"href": blind_href, "alt": "", "tile_number": -1,
                    "screen_idx": -1, "n_looks": 0, "replay": True}
            step("collect_links", t0, href=pick["href"][:80],
                 outcome="OK-BLIND-REPLAY")
        else:
            try:
                from grid_hunt import grid_hunt
                found = grid_hunt(
                    page, ask=query,
                    item_selector=("a[href*='/photos/'], "
                                   "a[href*='/illustrations/'], "
                                   "a[href*='/vectors/']"),
                    exclude_substr=(expect_different_slug or None),
                    max_screens=5)
                pick = {"href": found["href"], "alt": found.get("alt", "")}
                hunt_info = {k: found[k] for k in
                             ("tile_number", "screen_idx", "n_looks",
                              "n_tiles_seen") if k in found}
                sentinel_bytes += found.get("n_looks", 0) * 800  # ~640px thumbs
                learned.append({"label": "photo-link",
                                "selector": "grid_hunt eyes-first pick",
                                "href": pick["href"],
                                "note": f"jasper tile {found.get('tile_number')} "
                                        f"screen {found.get('screen_idx')}, "
                                        f"{found.get('n_looks')} looks"})
                step("collect_links", t0, href=pick["href"][:80],
                     alt=(pick.get("alt") or "")[:60], **hunt_info,
                     outcome="OK-EYES")
            except RuntimeError as e:
                full = snapshot_bytes(page)
                planner_bytes += full
                step("collect_links", t0, outcome="FAIL-NO-MATCH",
                     planner_bytes=full)
                raise SystemExit(str(e)[:200])

        # --- 4. hybrid gate, then open the photo page ---
        t0 = now()
        if blind_mode:
            _, live = main_region_blocks(page)
            sim = gate_similarity(live, baseline["blocks"])
            live_vh, _ = visual_hash(page)
            vdist = visual_distance(live_vh, baseline.get("visual", ""))
            sentinel_bytes += 512 + 272
            if sim < 0.95 and vdist > 12:
                strikes += 1
                mark_site_stale(SITE, f"cyborg search gate: sim={sim:.3f} vdist={vdist}")
                full = snapshot_bytes(page)
                planner_bytes += full
                step("gate_before_nav", t0, sim=round(sim, 3), vdist=vdist,
                     outcome="HALT-SNAPSHOT", planner_bytes=full)
            else:
                step("gate_before_nav", t0, sim=round(sim, 3), vdist=vdist,
                     outcome="GATE-PASS")
            t0 = now()
        before_url = page.url
        try:
            page.goto("https://pixabay.com" + pick["href"],
                      wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            step("photo_load", t0, outcome=f"FAIL-GOTO {e}"[:100])
            raise SystemExit(f"photo goto failed: {e}")
        page.wait_for_timeout(2500)
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

        # --- 5. Free download button (positional, no stable attrs — rule #9
        # site knowledge). Click opens the dialog. ---
        t0 = now()
        try:
            btn = page.locator("button:has-text('Free download')").first
            btn.wait_for(state="visible", timeout=10000)
            btn.click(timeout=8000)
            page.wait_for_timeout(2000)
            learned.append({"label": "free-download-open",
                            "selector": "button:has-text('Free download')",
                            "note": "positional, no stable attrs; opens dialog"})
            step("free_download_click", t0, outcome="OK-BLIND")
        except Exception as e:
            full = snapshot_bytes(page)
            planner_bytes += full
            step("free_download_click", t0, outcome=f"FAIL {e}"[:100],
                 planner_bytes=full)
            raise SystemExit(f"Free download button missing: {e}")

        # --- 6. Free-download opens a radial size menu (role=menuitem),
        # NOT a dialog. Clicking Original fires the download event FIRST,
        # then the page races toward the /get/ file URL — the click call
        # itself usually dies with a nav error, which is EXPECTED. So: open
        # the menu if needed, click tolerantly, poll the download handler
        # for a REAL file (>50KB; canva stubs ~3KB). No expect_download (it
        # loses the race), no /get/ re-GET (single-use URL). Rule #15. ---
        t0 = now()
        how = None
        try:
            for _ in range(3):
                try:
                    n = page.locator("[role=menuitem]").count()
                except Exception:
                    n = 0
                if n and n > 0:
                    break
                page.locator("button:has-text('Free download')").first.click(
                    timeout=8000)
                page.wait_for_timeout(2000)
            menu = page.locator("[role=menuitem]:has-text('Original')")
            # Fire-and-forget clicks: the download fires first, then the
            # page navigates to /get/ and every locator dies. So NEVER wait
            # on the click result — click, ignore everything, poll handler.
            def _poll_real(secs: float) -> list:
                t = now()
                real = [d for d in downloads
                        if not d.get("error") and (d.get("bytes") or 0) > 50_000]
                while now() - t < secs and not real:
                    try:
                        page.wait_for_timeout(1000)
                    except Exception:
                        time.sleep(1)
                    real = [d for d in downloads
                            if not d.get("error")
                            and (d.get("bytes") or 0) > 50_000]
                return real
            real = _poll_real(5)  # maybe it already fired (fast /get/ redirect)
            if not real:
                try:
                    menu.first.wait_for(state="attached", timeout=8000)
                    try:
                        # JS click returns immediately — no nav-wait race
                        menu.first.evaluate("(el) => el.click()")
                    except Exception:
                        pass
                except Exception:
                    pass
                real = _poll_real(20)
            if not real:
                # last resort: coords click, also fire-and-forget
                try:
                    box = menu.first.bounding_box(timeout=5000)
                    if box:
                        try:
                            page.mouse.click(box["x"] + box["width"] / 2,
                                             box["y"] + box["height"] / 2)
                        except Exception:
                            pass
                except Exception:
                    pass
                real = _poll_real(30)
            if not real:
                raise RuntimeError("no download event after Original click")
            # only the real file survives: drop canva HTML stubs etc.
            downloads[:] = real
            # sweep any stub files the handler already saved to disk
            for f in dl_dir.iterdir():
                if f.is_file() and f.stat().st_size < 50_000:
                    try:
                        f.unlink()
                    except Exception:
                        pass
            how = "download-event"
            learned.append({"label": "original-menuitem",
                            "selector": "[role=menuitem]:has-text('Original')",
                            "note": "Original races download+nav — click "
                                    "tolerantly, poll handler (rule #15)"})
        except Exception as e:
            full = snapshot_bytes(page)
            planner_bytes += full
            step("save", t0, outcome=f"FAIL-DL {e}"[:100],
                 planner_bytes=full)
            raise SystemExit(f"dialog download failed: {e}")
        step("save", t0, via=how,
             saved=downloads[-1].get("file") if downloads else None,
             bytes=downloads[-1].get("bytes") if downloads else 0,
             outcome="OK-BLIND")

        # --- 7. receipt: eyes on the DOWNLOADED file (local Jasper,
        # 0 metered tokens). The hunt already matched pre-download; this
        # confirms the file is the ask, not a lookalike.
        if downloads and not downloads[0].get("error"):
            t0 = now()
            try:
                from grid_hunt import verify_download
                ok, txt, vs = verify_download(
                    str(dl_dir / downloads[0]["file"]),
                    verify_ask or query)
                step("vision_check", t0, verdict="YES" if ok else "NO",
                     detail=txt[:120], jasper_s=vs, outcome="OK-EYES")
                report_vision = {"verdict": "YES" if ok else "NO",
                                 "detail": txt[:200], "jasper_s": vs}
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
        "query": query,
        "mode": "blind" if blind_mode else "full-observation",
        "steps": steps,
        "blockers": [],
        "downloads": downloads,
        "learned_actions": learned,
        "total_s": total_s,
        "planner_bytes": planner_bytes,
        "sentinel_bytes": sentinel_bytes,
        "strikes": strikes,
        "tokens": {"llm_prompt": 0, "llm_completion": 0, "llm_calls": 0,
                   "planner_bytes": planner_bytes,
                   "est_observation_tokens": planner_bytes // 4,
                   "note": "browser loop makes zero metered LLM calls; "
                           "observation cost that WOULD be tokens = "
                           "planner_bytes (~bytes/4, estimate); jasper "
                           "vision is local (electricity, not tokens)"},
        "vision": report_vision,
    }
    (outdir / "run-report.json").write_text(json.dumps(report, indent=1))
    (outdir / "action_map.json").write_text(json.dumps(learned, indent=1))
    try:
        PICS_DIR.mkdir(parents=True, exist_ok=True)
        import shutil
        for d in downloads:
            if d.get("file") and not d.get("error"):
                shutil.copy2(dl_dir / d["file"], PICS_DIR / d["file"])
        report["pics_sink"] = str(PICS_DIR)
    except Exception as e:
        report["pics_sink_error"] = str(e)[:120]
    try:
        SITES_DIR.mkdir(parents=True, exist_ok=True)
        fp = SITES_DIR / f"{SITE}.json"
        prior = json.loads(fp.read_text()) if fp.exists() else {}
        if not prior.get("needs_relearn"):
            prior.update({"selectors": learned,
                          "last_verified": time.strftime("%Y-%m-%d"),
                          "schema_version": 1})
        # run-1 pick recorded so run-2 fetches a DIFFERENT picture
        if not blind_mode and downloads:
            prior["run1_href"] = pick["href"]
            prior["run1_file"] = downloads[-1].get("file")
        fp.write_text(json.dumps(prior, indent=1))
    except Exception as e:
        report["site_save_error"] = str(e)[:120]
    (outdir / "run-report.json").write_text(json.dumps(report, indent=1))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, choices=["1", "2"])
    ap.add_argument("--query", default=None,
                    help="override the built-in query (e.g. red-haired ask)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--tape", default=None)
    ap.add_argument("--profile-dir", default=None)
    ap.add_argument("--verify", default=None,
                    help="vision receipt ask (defaults to the query)")
    ap.add_argument("--blind-href", default=None,
                    help="replay a known href blind (skips the hunt)")
    args = ap.parse_args()

    if args.run == "1":
        out = Path(args.out or str(RUNS_DIR / "cyborg-1"))
        rep = run_flow(args.query or RUN1_QUERY, out,
                       Path(args.profile_dir or str(out / "profile")),
                       verify_ask=args.verify)
    else:
        if not args.tape:
            raise SystemExit("--run 2 needs --tape runs/cyborg-1")
        prior = json.loads((Path(SITES_DIR) / f"{SITE}.json").read_text()) \
            if (Path(SITES_DIR) / f"{SITE}.json").exists() else {}
        if prior.get("needs_relearn"):
            print(f"STALE-FLAG: {prior.get('stale_reason')} — "
                  f"re-learning instead of blind replay")
        out = Path(args.out or str(RUNS_DIR / "cyborg-2"))
        rep = run_flow(args.query or RUN2_QUERY, out,
                       Path(args.profile_dir or str(out / "profile")),
                       blind_mode=not prior.get("needs_relearn"),
                       blind_href=args.blind_href,
                       verify_ask=args.verify,
                       expect_different_slug=prior.get("run1_href", "").rsplit("-", 1)[-1][:12] or None)
        r1 = json.loads((Path(args.tape) / "run-report.json").read_text())
        comp = {
            "run1_total_s": r1["total_s"],
            "run2_total_s": rep["total_s"],
            "run1_planner_bytes": r1["planner_bytes"],
            "run2_planner_bytes": rep["planner_bytes"],
            "run1_est_tokens": r1["tokens"]["est_observation_tokens"],
            "run2_est_tokens": rep["tokens"]["est_observation_tokens"],
            "speedup": round(r1["total_s"] / rep["total_s"], 2)
            if rep["total_s"] else None,
            "byte_ratio": round(r1["planner_bytes"] / rep["planner_bytes"], 2)
            if rep["planner_bytes"] else "inf (blind used 0 planner bytes)",
        }
        rep["comparison"] = comp
        (out / "run-report.json").write_text(json.dumps(rep, indent=1))
        print("COMPARISON:", json.dumps(comp, indent=1))

    print(json.dumps({k: rep[k] for k in
                      ("mode", "total_s", "planner_bytes", "strikes")}, indent=1))
    print("TOKENS:", json.dumps(rep["tokens"], indent=1))
    for s in rep["steps"]:
        print(f" - {s['name']}: {s.get('outcome')} {s['s']}s")


if __name__ == "__main__":
    main()
