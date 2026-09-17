#!/usr/bin/env python3
"""FastBrowse phase 4 — blind-click runner + staleness tripwire.

Acts from a learned action map with NO snapshots. A cheap sentinel gate
(main-region DOM hash vs baseline) runs before every action; every action is
verified BY EFFECT afterwards. Two strikes -> abort blind mode, full snapshot,
flag page for re-profile.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "phase1"))
sys.path.insert(0, str(HERE.parent / "phase3"))
sys.path.insert(0, str(HERE.parent))

from measure import normalize  # noqa: E402
from site_profiles import (  # noqa: E402
    ProfileStore, etld1, find_candidates, match, apply, record,
)

try:
    import human_mouse  # noqa: E402
    HAS_HUMAN_MOUSE = True
except Exception:
    HAS_HUMAN_MOUSE = False


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def main_region_hash(page) -> tuple[str, int]:
    """Cheap sentinel gate signal: normalized main-region hash + bytes."""
    norm = main_region_norm(page)
    return sha(norm), len(norm.encode("utf-8"))


def main_region_norm(page) -> str:
    try:
        els = page.query_selector_all("main, [role=main], #content, #bodyContent")
        combined = "\n".join((el.inner_html() or "") for el in els[:2])
    except Exception:
        combined = ""
    return normalize(combined)


def main_region_blocks(page) -> tuple[str, frozenset]:
    """Baseline as (hash, block-hash set) for similarity gating.

    Blocks are TEXT lines, not HTML: ad slots randomize markup per load
    (html-line Jaccard ~0.71 across reloads) while visible text is stable
    (text-line Jaccard 1.0). The gate watches what the user sees.
    """
    import re
    norm = main_region_norm(page)
    text = re.sub(r"<[^>]+>", "", norm)
    blocks = frozenset(sha(l.strip()) for l in text.split("\n") if len(l.strip()) > 12)
    return sha(norm), blocks


def gate_similarity(live_blocks: frozenset, base_blocks: frozenset) -> float:
    """Jaccard similarity of region block sets. 1.0 = identical."""
    if not base_blocks and not live_blocks:
        return 1.0
    union = base_blocks | live_blocks
    if not union:
        return 1.0
    return len(base_blocks & live_blocks) / len(union)


def verify_effect(page, expect: dict, before_url: str) -> tuple[bool, str]:
    """Verify an action by its effect, never by looking. Returns (ok, detail)."""
    if not expect:
        return True, "no expectation"
    if "url_contains" in expect:
        ok = expect["url_contains"] in page.url and page.url != before_url
        return ok, f"url={page.url[:80]}"
    if "url_changed" in expect and expect["url_changed"]:
        ok = page.url != before_url
        return ok, f"url={page.url[:80]}"
    if "present" in expect:
        try:
            el = page.query_selector(expect["present"])
            ok = el is not None and el.is_visible()
            return ok, f"present({expect['present']})={ok}"
        except Exception as e:
            return False, f"present() error: {e}"[:120]
    if "overlay_gone" in expect:
        try:
            cands = find_candidates(page)
            left = [c for c in cands if c["visible"] and c["kind"] == expect["overlay_gone"]]
            return (len(left) == 0), f"{expect['overlay_gone']} remaining={len(left)}"
        except Exception as e:
            return False, f"overlay check error: {e}"[:120]
    return True, "unknown expectation type (pass)"


def snapshot_bytes(page) -> int:
    """Full observation cost, used only on fallback. Returns byte count."""
    try:
        dom = page.content()
    except Exception:
        dom = ""
    return len(dom.encode("utf-8"))


def run_blind(page, actions: list, baseline, store: ProfileStore | None = None,
              use_human_mouse: bool = False, max_strikes: int = 2,
              gate_threshold: float = 0.95) -> dict:
    """Execute actions blind. `baseline` is (hash, block-set) or a bare hash (exact mode).
    Returns a full event log + totals."""
    if isinstance(baseline, str):
        baseline_hash, baseline_blocks, exact_mode = baseline, frozenset(), True
    else:
        baseline_hash, baseline_blocks = baseline
        exact_mode = False
    log: list[dict] = []
    strikes = 0
    planner_bytes = 0
    snapshots_used = 0

    for i, a in enumerate(actions):
        label = a.get("label", f"step{i}")
        # --- sentinel gate (similarity, not equality) ---
        if exact_mode:
            h, hb = main_region_hash(page)
            sim, gate_ok = 1.0 if h == baseline_hash else 0.0, h == baseline_hash
        else:
            _, live_blocks = main_region_blocks(page)
            sim = gate_similarity(live_blocks, baseline_blocks)
            gate_ok = sim >= gate_threshold
            hb = len(live_blocks)
        if not gate_ok:
            strikes += 1
            ev = {"action": label, "gate": f"MISMATCH sim={sim:.3f}", "strikes": strikes,
                  "gate_bytes": hb}
            if strikes >= max_strikes:
                full = snapshot_bytes(page)
                planner_bytes += full
                snapshots_used += 1
                ev.update({"outcome": "ABORT-REPROFILE", "planner_bytes": full})
                log.append(ev)
                break
            full = snapshot_bytes(page)  # one look, then halt this action
            planner_bytes += full
            snapshots_used += 1
            ev.update({"outcome": "HALT-SNAPSHOT", "planner_bytes": full})
            log.append(ev)
            continue
        ev = {"action": label, "gate": f"pass sim={sim:.3f}", "strikes": strikes}
        # --- act ---
        before_url = page.url
        try:
            if a.get("profile_action"):
                # overlay disposal via site profile (no observation)
                disp = match(page, store) if store else {"status": "NOSTORE"}
                if disp.get("status") == "KNOWN":
                    res = apply(page, disp)
                    ev.update({"via": "profile", "oneliner": res.get("oneliner"),
                               "effect_ok": res.get("handled", False),
                               "effect": "overlay gone" if res.get("handled") else "still present"})
                    if not res.get("handled"):
                        strikes += 1
                else:
                    ev.update({"via": "profile", "effect_ok": False,
                               "effect": f"no profile ({disp.get('status')})"})
                    strikes += 1
            elif "selector" in a:
                sel = a["selector"]
                # href anchor (recorded closest a[href]) beats generated class
                # soup: stable across deploys. Falls back to recorded selector.
                href = a.get("href")
                if href:
                    try:
                        hloc = page.locator(f'a[href="{href}"]').first
                        if hloc.count() > 0:
                            sel = f'a[href="{href}"]'
                            ev["href_anchor"] = True
                    except Exception:
                        pass
                try:
                    if a.get("first"):
                        loc = page.locator(sel).first
                        if use_human_mouse and HAS_HUMAN_MOUSE:
                            box = loc.bounding_box()
                            if box:
                                human_mouse.human_click(
                                    page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                            else:
                                loc.click(timeout=8000)
                        else:
                            loc.click(timeout=8000)
                    elif use_human_mouse and HAS_HUMAN_MOUSE:
                        box = page.query_selector(sel).bounding_box()
                        if box:
                            human_mouse.human_click(
                                page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        else:
                            page.click(sel, timeout=8000)
                    else:
                        page.click(sel, timeout=8000)
                except AttributeError:
                    page.click(sel, timeout=8000)
                page.wait_for_timeout(a.get("settle_ms", 1500))
                ok, detail = verify_effect(page, a.get("expect", {}), before_url)
                ev.update({"via": "click", "effect_ok": ok, "effect": detail})
                if not ok:
                    strikes += 1
            else:
                ev.update({"via": "none", "effect_ok": True, "effect": "no-op"})
        except Exception as e:
            strikes += 1
            ev.update({"via": "error", "effect_ok": False, "effect": str(e)[:150]})
        ev["strikes"] = strikes
        # --- retry once with a snapshot on failure ---
        if not ev.get("effect_ok", True):
            full = snapshot_bytes(page)
            planner_bytes += full
            snapshots_used += 1
            ev.update({"retry_snapshot_bytes": full})
            if strikes >= max_strikes:
                ev["outcome"] = "ABORT-REPROFILE"
                log.append(ev)
                break
            ev["outcome"] = "RETRY-WITH-SNAPSHOT"
        else:
            ev["outcome"] = "OK-BLIND"
        # Successful overlay disposal legitimately changes the page:
        # refresh the gate baseline so we don't trip on our own success.
        if ev.get("outcome") == "OK-BLIND" and ev.get("via") == "profile":
            try:
                baseline_hash, baseline_blocks = main_region_blocks(page)
                ev["rebased"] = True
            except Exception:
                pass
        log.append(ev)

    return {"log": log, "strikes": strikes, "planner_bytes": planner_bytes,
            "snapshots_used": snapshots_used,
            "blind_actions": sum(1 for e in log if e.get("outcome") == "OK-BLIND")}


def launch_browser(pw, engine: str, headless: bool):
    """Launch chromium (Playwright) or camofox (Firefox anti-detect).

    Camoufox is a context manager — enter it and return the live
    Browser/BrowserContext it yields (has new_context OR new_page)."""
    if engine == "camofox":
        from camoufox.sync_api import Camoufox
        cm = Camoufox(headless=headless)
        return cm.__enter__()
    return pw.chromium.launch(headless=headless)


def wait_past_challenge(page, timeout_s: int = 25) -> bool:
    """Wait out 'Just a moment...' style bot challenges. Returns True if clear."""
    for _ in range(timeout_s * 2):
        try:
            title = page.title() or ""
        except Exception:
            title = ""
        if "just a moment" not in title.lower():
            return True
        page.wait_for_timeout(500)
    return False


def fresh_page(browser, width: int = 1366, height: int = 900):
    """New page on either a Playwright Browser or a Camoufox context/browser."""
    if hasattr(browser, "new_page"):
        try:
            return browser.new_page(viewport={"width": width, "height": height})
        except TypeError:
            return browser.new_page()
    try:
        ctx = browser.new_context(viewport={"width": width, "height": height})
        return ctx.new_page()
    except Exception:
        return browser.new_page()  # camoufox context: pages only


def cmd_demo_fandom(outdir: Path, headless: bool = True, engine: str = "chromium") -> dict:
    """Stress test: fandom wiki overlay gauntlet, record -> revisit blind."""
    outdir.mkdir(parents=True, exist_ok=True)
    store = ProfileStore(outdir / "profiles.json")
    if engine == "camofox":
        from camoufox.sync_api import Camoufox
        with Camoufox(headless=headless) as browser:
            return _demo_flow(browser, outdir, store, engine)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            return _demo_flow(browser, outdir, store, engine)
        finally:
            browser.close()


def _demo_flow(browser, outdir: Path, store: ProfileStore, engine: str) -> dict:
    url = "https://starwars.fandom.com/wiki/Luke_Skywalker"
    # --- first visit: full observation, record overlays ---
    page = fresh_page(browser)
    t0 = time.time()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    cleared = wait_past_challenge(page)
    report: dict = {"site": None, "engine": engine, "challenge_cleared": cleared,
                    "first_visit": {}, "revisit": {}}
    page.wait_for_timeout(4000)
    first_bytes = snapshot_bytes(page)
    cands = find_candidates(page)
    report["site"] = etld1(page.url)
    report["first_visit"] = {
        "url": page.url, "load_s": round(time.time() - t0, 1),
        "planner_bytes": first_bytes,
        "candidates": [{"kind": c["kind"], "visible": c["visible"],
                        "sig": c["signature"][:12], "bytes": c["normalized_bytes"]}
                       for c in cands],
    }
    recorded = []
    link_href = ""
    # Record ONE banner: prefer the OneTrust SDK root; fall back to the
    # biggest visible consent/modal candidate. Page furniture (sidebars,
    # header backgrounds) must NOT become profiles.
    ranked = [c for c in cands if c["visible"] and c["kind"] in ("consent", "modal", "banner")]
    ranked.sort(key=lambda c: (
        0 if "onetrust-banner-sdk" in (c.get("selector") or "") else
        1 if "onetrust" in (c.get("selector") or "") else 2,
        -(c.get("normalized_bytes") or 0)))
    if ranked:
        # Walk candidates until one yields a UNIQUE dismiss button.
        # Generic selectors matching N elements are strict-violations at
        # apply time — never record them.
        for c in ranked:
            btn = ""
            try:
                scope = c.get("selector", "") or ""
                for bs in ("#onetrust-reject-all-handler", "#onetrust-accept-btn-handler"):
                    loc = page.locator(bs)
                    if loc.count() == 1 and loc.first.is_visible():
                        btn = bs
                        break
                if not btn and scope:
                    try:
                        inner = page.locator(f"{scope} button")
                        if inner.count() == 1 and inner.first.is_visible():
                            btn = f"{scope} button"
                    except Exception:
                        pass
            except Exception:
                pass
            sel = btn or c.get("click_selector", "") or ""
            # container selectors alone can't dismiss — need the button
            if not sel or (sel == c.get("selector", "") and c.get("kind") != "consent"):
                continue
            try:
                prof, _ = store.upsert(
                    site=etld1(page.url), overlay_signature=c["signature"],
                    overlay_kind=c["kind"], selector=sel, action="dismiss",
                    overlay_bytes=c["normalized_bytes"])
                recorded.append({"kind": c["kind"], "selector": sel})
                break
            except Exception as e:
                recorded.append({"kind": c["kind"], "error": str(e)[:100]})
                break
    report["first_visit"]["recorded"] = recorded
    # Learn ONE nav destination into the map (first /wiki/ link, other slug).
    try:
        for h in page.eval_on_selector_all(
                ".mw-parser-output a[href^='/wiki/']",
                "els => els.map(e => e.getAttribute('href'))"):
            if h and h.startswith("/wiki/") and ":" not in h and h.rstrip("/") != "/wiki/Luke_Skywalker":
                link_href = h
                break
    except Exception:
        pass
    report["first_visit"]["map_link"] = link_href
    # baseline for blind gate = block set (similarity-gated, not exact)
    baseline = main_region_blocks(page)
    try:
        page.close()
    except Exception:
        pass
    # --- revisit: blind ---
    page2 = fresh_page(browser)
    page2.goto(url, wait_until="domcontentloaded", timeout=30000)
    wait_past_challenge(page2)
    page2.wait_for_timeout(4000)
    actions = [{"label": "dispose-overlays", "profile_action": True}]
    if link_href:
        actions.append(
            {"label": "open-nav-link", "selector": f".mw-parser-output a[href='{link_href}']",
             "first": True,
             "expect": {"url_contains": link_href}, "settle_ms": 2000})
    res = run_blind(page2, actions, baseline, store=store)
    report["revisit"] = res
    browser.close()
    (outdir / "fandom-report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", default="fandom", choices=["fandom"])
    ap.add_argument("--out", default="runs")
    ap.add_argument("--engine", default="chromium", choices=["chromium", "camofox"])
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    outdir = Path(args.out)
    if args.demo == "fandom":
        rep = cmd_demo_fandom(outdir, headless=not args.headed, engine=args.engine)
        print(json.dumps({"site": rep["site"],
                          "first_visit_bytes": rep["first_visit"].get("planner_bytes"),
                          "candidates": len(rep["first_visit"].get("candidates", [])),
                          "recorded": len(rep["first_visit"].get("recorded", [])),
                          "revisit": {k: rep["revisit"][k] for k in
                                      ("strikes", "planner_bytes", "snapshots_used", "blind_actions")}},
                         indent=1))
        for e in rep["revisit"].get("log", []):
            print(f" - {e['action']}: gate={e['gate']} outcome={e.get('outcome')} "
                  f"effect={e.get('effect', '')[:70]}")


if __name__ == "__main__":
    main()
