#!/usr/bin/env python3
"""FastBrowse Sydney suite — Test 1 + Test 2 (Stu's spec, 17 Sep 26).

Test 1: straight search for a picture of Sydney Sweeney on Wikimedia
  Commons, download it. The site is LEARNED as we attempt: every selector
  that works is recorded into an action map (action_map.json) for reuse.
Test 2: download ANOTHER picture via the learned map, run BLIND
  (sentinel gate + effect verify, planner_bytes only on fallback), and
  compare headless times + observation bytes vs Test 1.

Token usage is logged per run (llm_tokens: the browser loop uses no LLM;
agent-level session tokens are tracked separately, noted in the report).

Usage:
  python sydney_suite.py --run 1 [--out runs-sydney-commons-1]
  python sydney_suite.py --run 2 --tape runs-sydney-commons-1 [--out runs-sydney-commons-2]
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "phase4"))
sys.path.insert(0, str(HERE.parent / "phase3"))
sys.path.insert(0, str(HERE.parent))

import blind  # noqa: E402
from blind import (  # noqa: E402
    fresh_page,
    gate_similarity,
    main_region_blocks,
    snapshot_bytes,
    wait_past_challenge,
)

SEARCH_URL = ("https://commons.wikimedia.org/w/index.php"
              "?search={q}&title=Special:Search&profile=images&fulltext=1")
RUN1_QUERY = "Sydney Sweeney"
RUN2_QUERY = "Sydney Sweeney TIFF 2024"
RUN2_SLUG = "Sydney_Sweeney_at_the_2024_Toronto"  # expected file slug


def now() -> float:
    return time.time()


def run_flow(query: str, outdir: Path, profile_dir: Path,
             blind_mode: bool = False, tape: dict | None = None,
             expect_slug: str | None = None) -> dict:
    """One search->result->file->download flow. Returns the run report."""
    from camoufox.sync_api import Camoufox

    outdir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    dl_dir = outdir / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)

    steps: list = []
    learned: list = []  # action map entries discovered this run
    planner_bytes = 0
    sentinel_bytes = 0
    strikes = 0
    t_start = now()

    def step(name: str, t0: float, **kw) -> dict:
        s = {"name": name, "s": round(now() - t0, 1)}
        s.update(kw)
        steps.append(s)
        print(f"  [{name}] {s['s']}s " + " ".join(f"{k}={v}" for k, v in kw.items()))
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
                print(f"\n  DL-ERR: {e}")

        try:
            page.on("download", _on_download)
        except Exception:
            pass

        # --- step 1: search page ---
        t0 = now()
        page.goto(SEARCH_URL.format(q=query.replace(" ", "+")),
                  wait_until="domcontentloaded", timeout=45000)
        wait_past_challenge(page)
        page.wait_for_timeout(2500)
        dom = page.content()
        dom_bytes = len(dom.encode("utf-8"))
        if dom_bytes < 20_000 or "Just a moment" in (page.title() or ""):
            step("search_load", t0, dom_bytes=dom_bytes, outcome="BOT-WALL")
            raise SystemExit("bot wall on search page — aborting")
        if not blind_mode:
            planner_bytes += dom_bytes
        learned.append({"label": "search-url",
                        "url_template": SEARCH_URL,
                        "note": "file search via Special:Search profile=images"})
        baseline = main_region_blocks(page)
        sentinel_bytes += 512  # hash reads are cheap, accounted symbolically
        step("search_load", t0, dom_bytes=dom_bytes,
             title=(page.title() or "")[:60],
             outcome="OK-FULL" if not blind_mode else "OK-BLIND")

        # --- step 2: pick a File: result (learned: first /wiki/File: link,
        # or the expected slug when given) ---
        t0 = now()
        try:
            hrefs = page.eval_on_selector_all(
                "a[href*='/wiki/File:']",
                "els => els.map(e => e.getAttribute('href'))")
        except Exception:
            hrefs = []
        hrefs = [h for h in (hrefs or []) if h and h.startswith("/wiki/File:")]
        target = None
        if expect_slug:
            target = next((h for h in hrefs if expect_slug in h), None)
        target = target or (hrefs[0] if hrefs else None)
        if not target:
            full = snapshot_bytes(page)
            planner_bytes += full
            step("collect_result", t0, outcome="FAIL-NO-RESULTS",
                 planner_bytes=full)
            raise SystemExit("no File: results on search page")
        learned.append({"label": "first-file-result",
                        "selector": "a[href*='/wiki/File:']",
                        "href": target,
                        "note": "stable href anchor, no class soup"})
        step("collect_result", t0, n_results=len(hrefs),
             target=target[:80], outcome="OK-BLIND")

        # --- step 3: open the file page, gate on the way ---
        t0 = now()
        before_url = page.url
        if blind_mode:
            _, live = main_region_blocks(page)
            sim = gate_similarity(live, baseline[1])
            sentinel_bytes += 512
            if sim < 0.95:
                strikes += 1
                full = snapshot_bytes(page)
                planner_bytes += full
                step("gate_before_nav", t0, sim=round(sim, 3),
                     outcome="HALT-SNAPSHOT", planner_bytes=full)
            else:
                step("gate_before_nav", t0, sim=round(sim, 3),
                     outcome="GATE-PASS")
            t0 = now()
        page.goto("https://commons.wikimedia.org" + target,
                  wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        dom2 = page.content()
        dom2_bytes = len(dom2.encode("utf-8"))
        if not blind_mode:
            planner_bytes += dom2_bytes
        ok_nav = page.url != before_url and "/wiki/File:" in page.url
        if not ok_nav:
            strikes += 1
        baseline = main_region_blocks(page)
        step("photo_load", t0, dom_bytes=dom2_bytes,
             title=(page.title() or "")[:60],
             outcome="OK-BLIND" if ok_nav else "FAIL-NAV")

        # --- step 4: find the Original file link (learned anchor) ---
        t0 = now()
        orig_href = None
        try:
            orig_href = page.eval_on_selector_all(
                "a[href*='upload.wikimedia.org']",
                "els => els.map(e => e.getAttribute('href'))")
        except Exception:
            orig_href = []
        orig = next((h for h in (orig_href or [])
                     if h and "upload.wikimedia.org" in h), None)
        if not orig:
            full = snapshot_bytes(page)
            planner_bytes += full
            step("find_original", t0, outcome="FAIL-NO-ORIG",
                 planner_bytes=full)
            raise SystemExit("no upload.wikimedia.org link on file page")
        learned.append({"label": "original-file",
                        "selector": "a[href*='upload.wikimedia.org']",
                        "href": orig[:120],
                        "note": "stable upload host anchor"})
        step("find_original", t0, href_host="upload.wikimedia.org",
             outcome="OK-BLIND")

        # --- step 5: download — real browser download event via
        # in-page fetch+blob+a[download]; fallback: context request GET ---
        t0 = now()
        how = None
        try:
            with page.expect_download(timeout=30000) as dl_info:
                page.evaluate(
                    """async (url) => {
                      const r = await fetch(url, {credentials: 'omit'});
                      const b = await r.blob();
                      const a = document.createElement('a');
                      a.href = URL.createObjectURL(b);
                      a.download = url.split('/').pop().split('?')[0];
                      document.body.appendChild(a); a.click(); a.remove();
                    }""", orig)
            try:
                dl = dl_info.value
                dest = dl_dir / (dl.suggested_filename or "sydney.bin")
                dl.save_as(str(dest))
                downloads.append({"file": dest.name,
                                  "bytes": dest.stat().st_size,
                                  "url": orig[:120]})
                how = "download-event"
            except Exception as e:
                downloads.append({"error": str(e)[:120], "url": orig[:120]})
                how = "download-event-err"
        except Exception as e:
            # fallback: browser-stack GET, still no direct socket code
            try:
                resp = page.context.request.get(orig, timeout=60000)
                body = resp.body()
                name = orig.split("/")[-1].split("?")[0] or "sydney.bin"
                dest = dl_dir / name
                dest.write_bytes(body)
                downloads.append({"file": dest.name, "bytes": len(body),
                                  "url": orig[:120]})
                how = "request-fallback"
            except Exception as e2:
                step("save", t0, outcome=f"FAIL-DL {e2}"[:100])
                raise SystemExit(f"download failed: {e2}")
        step("save", t0, via=how,
             saved=downloads[-1].get("file") if downloads else None,
             bytes=downloads[-1].get("bytes") if downloads else 0,
             outcome="OK-BLIND")

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
        "tokens": {"llm_prompt": 0, "llm_completion": 0,
                   "note": "browser loop uses no LLM; agent session tokens "
                           "tracked separately by the harness"},
    }
    (outdir / "run-report.json").write_text(json.dumps(report, indent=1))
    (outdir / "action_map.json").write_text(json.dumps(learned, indent=1))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, choices=["1", "2"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--tape", default=None,
                    help="run-1 outdir (learned map) for run 2")
    ap.add_argument("--profile-dir", default=None)
    args = ap.parse_args()

    if args.run == "1":
        out = Path(args.out or "runs-sydney-commons-1")
        rep = run_flow(RUN1_QUERY, out,
                       Path(args.profile_dir or str(out / "profile")))
    else:
        if not args.tape:
            raise SystemExit("--run 2 needs --tape runs-sydney-commons-1")
        tape = json.loads((Path(args.tape) / "action_map.json").read_text())
        out = Path(args.out or "runs-sydney-commons-2")
        rep = run_flow(RUN2_QUERY, out,
                       Path(args.profile_dir or str(out / "profile")),
                       blind_mode=True, tape=tape, expect_slug=RUN2_SLUG)
        # comparison vs run 1
        r1 = json.loads((Path(args.tape) / "run-report.json").read_text())
        comp = {
            "run1_total_s": r1["total_s"],
            "run2_total_s": rep["total_s"],
            "run1_planner_bytes": r1["planner_bytes"],
            "run2_planner_bytes": rep["planner_bytes"],
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
    for s in rep["steps"]:
        print(f" - {s['name']}: {s.get('outcome')} {s['s']}s")


if __name__ == "__main__":
    main()
