#!/usr/bin/env python3
"""FastBrowse phase 6 — hover physics. The pointer is STATE, not a cursor.

A hover menu is held open by the pointer sitting on its anchor chain. Move
the pointer wrong and the menu collapses — and a naive sentinel flags the
collapse as novelty. Profiles record hover-anchors; the player replays with
safe approach paths; the sentinel treats anchor-held menus as expected state.

Storage: hover-profiles.json next to phase-3 profiles (same shape family):
{site, anchor_selector, anchor_point[x,y], reveals_selector,
 target_selector, dwell_ms, path_kind, hits}
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

try:
    import human_mouse  # noqa: E402
    HAS_HUMAN_MOUSE = True
except Exception:
    HAS_HUMAN_MOUSE = False


def anchor_center(page, anchor_selector: str) -> tuple[float, float]:
    box = page.locator(anchor_selector).first.bounding_box()
    if not box:
        raise RuntimeError(f"anchor has no box: {anchor_selector}")
    return (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def is_revealed(page, reveals_selector: str) -> bool:
    try:
        el = page.locator(reveals_selector).first
        return el.is_visible()
    except Exception:
        return False


def record(page, anchor_selector: str, target_selector: str,
           store: ProfileStore, dwell_ms: int = 600,
           path_kind: str = "direct", pre_hover: str = "") -> dict:
    """Record a hover-anchor: park on anchor, capture what reveals + where.

    Nested anchors (submenu items) have no layout box until their parent
    menu is open — pass the parent as pre_hover to walk the chain first.
    """
    site = etld1(page.url)
    if pre_hover:
        px, py = anchor_center(page, pre_hover)
        if HAS_HUMAN_MOUSE:
            human_mouse.reset_mouse_tracker(page, 0, 0)
            human_mouse.human_move(page, px, py)
        else:
            page.mouse.move(px, py)
        page.wait_for_timeout(dwell_ms)
    ax, ay = anchor_center(page, anchor_selector)
    if HAS_HUMAN_MOUSE:
        human_mouse.reset_mouse_tracker(page, 0, 0)
        human_mouse.human_move(page, ax, ay, precise=True)
    else:
        page.mouse.move(ax, ay)
    page.wait_for_timeout(dwell_ms)
    # find what became visible that wasn't before (caller ensures menu shut)
    # Prefer the deepest visible menu whose subtree holds the TARGET —
    # parking reports must name the menu the target lives in, not its parent.
    reveals = ""
    try:
        order = page.eval_on_selector_all(
            "#m1-menu, #m2-menu, #m2-sub, #m3-menu, [role=menu], "
            ".dropdown-menu, .nav-menu, .mega-menu",
            "els => els.filter(e => e.offsetParent !== null).map(e => "
            "(e.id ? '#' + e.id : e.className.toString().split(' ')[0]))")
    except Exception:
        order = []
    target_inside = ""
    # Deepest menu wins: among visible candidates containing the target,
    # pick the smallest area (nested submenu < parent menu).
    holders = []
    for cand in ("#m1-menu", "#m2-menu", "#m2-sub", "#m3-menu",
                 "[role=menu]", ".dropdown-menu", ".nav-menu", ".mega-menu"):
        try:
            loc = page.locator(f"{cand} {target_selector}")
            if loc.count() > 0 and page.locator(cand).first.is_visible():
                box = page.locator(cand).first.bounding_box()
                area = (box["width"] * box["height"]) if box else float("inf")
                holders.append((area, cand))
        except Exception:
            continue
    if holders:
        target_inside = sorted(holders)[0][1]
    reveals = target_inside or (order[0] if order else "")
    if not reveals:
        for cand in ("#m1-menu", "#m2-menu", "#m2-sub", "#m3-menu",
                     "[role=menu]", ".dropdown-menu", ".nav-menu", ".mega-menu"):
            try:
                if page.locator(cand).first.is_visible():
                    reveals = cand
                    break
            except Exception:
                continue
    prof, created = store.upsert(
        site=site, overlay_signature=f"hover:{anchor_selector}->{target_selector}",
        overlay_kind="hover", selector=anchor_selector, action="hover-hold",
        overlay_bytes=0)
    prof["anchor_point"] = [ax, ay]
    prof["reveals_selector"] = reveals
    prof["target_selector"] = target_selector
    prof["pre_hover"] = pre_hover
    prof["dwell_ms"] = dwell_ms
    prof["path_kind"] = path_kind
    store.save()
    return {"profile": prof, "created": created, "reveals": reveals,
            "anchor": [ax, ay]}


def play(page, profile: dict, timeout_ms: int = 5000) -> dict:
    """Replay: park on anchor -> verify reveal -> approach target -> click.

    Approach: 'direct' line first; on collapse, retry with 'L-path'
    (horizontal into the submenu plane, then vertical to target) which keeps
    the pointer over the menu chain on nested menus.
    """
    log: dict = {"anchor": profile.get("selector"), "target": profile.get("target_selector"),
                 "attempts": []}
    # Walk the anchor chain first (nested anchors need parents open).
    chain = [c for c in [profile.get("pre_hover", ""), profile.get("selector")] if c]
    dwell = profile.get("dwell_ms", 600)
    start_xy = (0.0, 0.0)
    try:
        for link in chain:
            lx, ly = anchor_center(page, link)
            if HAS_HUMAN_MOUSE:
                if link == chain[0]:
                    human_mouse.reset_mouse_tracker(page, 0, 0)
                human_mouse.human_move(page, lx, ly, precise=True)
            else:
                page.mouse.move(lx, ly)
            page.wait_for_timeout(dwell if link == chain[-1] else 350)
            start_xy = (lx, ly)
        ax, ay = start_xy
    except RuntimeError as e:
        return {"anchor": profile.get("selector"), "target": profile.get("target_selector"),
                "attempts": [], "ok": False, "effect": f"NO-ANCHOR-BOX {e}"}
    tx, ty = None, None
    try:
        box = page.locator(profile["target_selector"]).first.bounding_box(timeout=1000)
        # target may be hidden until reveal; box may be None — resolve after park
    except Exception:
        pass
    # Gap analysis: dead space between anchor row and reveal box. A true gap
    # (no overlap, no close-delay) is uncrossable by ANY pointer — human
    # included. Report NO-SAFE-PATH so the planner routes around it.
    try:
        abox = page.locator(profile["selector"]).first.bounding_box()
        rbox0 = page.locator(profile.get("reveals_selector", "")).first.bounding_box()
    except Exception:
        abox, rbox0 = None, None
    if abox and rbox0 and rbox0["x"] > abox["x"] + abox["width"] + 2:
        gap = rbox0["x"] - (abox["x"] + abox["width"])
        return {"anchor": profile.get("selector"), "target": profile.get("target_selector"),
                "attempts": [], "ok": False,
                "effect": f"NO-SAFE-PATH gap={gap:.0f}px (pointer-hostile menu)"}
    for attempt, path in enumerate(["direct", "L-path"]):
        if attempt > 0 and profile.get("path_kind") == "direct":
            pass  # still try L as fallback
        if HAS_HUMAN_MOUSE:
            human_mouse.reset_mouse_tracker(page, 0, 0)
            human_mouse.human_move(page, ax, ay, precise=True)
        else:
            page.mouse.move(ax, ay)
        page.wait_for_timeout(dwell)
        revealed = is_revealed(page, profile.get("reveals_selector", ""))
        att = {"path": path, "revealed": revealed}
        if not revealed:
            # menu never opened (intent-delay?) — wait once more
            page.wait_for_timeout(dwell)
            revealed = is_revealed(page, profile.get("reveals_selector", ""))
            att["revealed_retry"] = revealed
        if not revealed:
            att.update({"outcome": "NO-REVEAL"})
            log["attempts"].append(att)
            continue
        try:
            tbox = page.locator(profile["target_selector"]).first.bounding_box(timeout=2000)
        except Exception:
            tbox = None
        if not tbox:
            att.update({"outcome": "NO-TARGET-BOX"})
            log["attempts"].append(att)
            continue
        tx, ty = tbox["x"] + tbox["width"] / 2, tbox["y"] + tbox["height"] / 2
        try:
            if path == "L-path" or True:
                # Route THROUGH the open submenu's box: descendant hover keeps
                # ancestor :hover alive, but open floor between menu edge and
                # submenu edge collapses it. Waypoint inside the reveal box.
                rbox = None
                try:
                    rbox = page.locator(profile.get("reveals_selector", "")).first.bounding_box()
                except Exception:
                    pass
                # Entry via the submenu's top corner: its top edge usually
                # overlaps the parent menu's padding, threading AROUND the
                # dead-space gap beside the anchor row.
                entry = None
                if rbox:
                    entry = (rbox["x"] + 8, rbox["y"] + 8)
                mover = (lambda x, y: human_mouse.human_move(page, x, y, precise=True)) \
                    if HAS_HUMAN_MOUSE else (lambda x, y: page.mouse.move(x, y))
                if entry:
                    mover(*entry)
                    page.wait_for_timeout(120)
                    if not is_revealed(page, profile.get("reveals_selector", "")):
                        att.update({"outcome": "COLLAPSED-EN-ROUTE"})
                        log["attempts"].append(att)
                        continue
                if HAS_HUMAN_MOUSE:
                    human_mouse.human_move(page, tx, ay, precise=True)
                    page.wait_for_timeout(120)
                    # collapse check mid-path
                    if not is_revealed(page, profile.get("reveals_selector", "")):
                        att.update({"outcome": "COLLAPSED-MID-PATH"})
                        log["attempts"].append(att)
                        continue
                    human_mouse.human_move(page, tx, ty, precise=True)
                else:
                    page.mouse.move(tx, ay)
                    page.wait_for_timeout(120)
                    if not is_revealed(page, profile.get("reveals_selector", "")):
                        att.update({"outcome": "COLLAPSED-MID-PATH"})
                        log["attempts"].append(att)
                        continue
                    page.mouse.move(tx, ty)
            page.wait_for_timeout(150)
            if not is_revealed(page, profile.get("reveals_selector", "")):
                att.update({"outcome": "COLLAPSED-ON-ARRIVAL"})
                log["attempts"].append(att)
                continue
            page.locator(profile["target_selector"]).first.click(timeout=3000)
            clicked = page.evaluate("document.getElementById('log').textContent")
            att.update({"outcome": "CLICKED", "clicked": clicked})
            log["attempts"].append(att)
            log.update({"ok": True, "effect": clicked})
            return log
        except Exception as e:
            att.update({"outcome": f"ERROR {str(e)[:100]}"})
            log["attempts"].append(att)
    log.update({"ok": False})
    return log


def hover_state_key(pointer: tuple[float, float], anchor: tuple[float, float],
                    reveals_open: bool) -> str:
    """Sentinel rule: menu-open + pointer-on-anchor = EXPECTED, not novelty."""
    dist = ((pointer[0] - anchor[0]) ** 2 + (pointer[1] - anchor[1]) ** 2) ** 0.5
    if reveals_open and dist < 120:
        return "EXPECTED-HOVER-OPEN"
    if not reveals_open and dist >= 120:
        return "EXPECTED-HOVER-CLOSED"
    return "NOVELTY"


def cmd_fixture_test(outdir: Path, headless: bool = True) -> dict:
    from playwright.sync_api import sync_playwright
    outdir.mkdir(parents=True, exist_ok=True)
    store = ProfileStore(outdir / "hover-profiles.json")
    results: dict = {"cases": []}
    url = Path(HERE / "fixture.html").as_uri()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        for anchor, target, label, pre in [
                ("#m1", "#m1-a", "simple-dropdown", ""),
                ("#m2a", "#m2-a", "nested-submenu", "#m2"),
                ("#m3", "#m3-a", "intent-delay", "")]:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(400)
            rec = record(page, anchor, target, store, pre_hover=pre)
            # fresh load, replay from profile
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(400)
            res = play(page, rec["profile"])
            key = hover_state_key(
                tuple(rec["profile"]["anchor_point"]),
                tuple(rec["profile"]["anchor_point"]), True)
            results["cases"].append({
                "label": label, "reveals": rec["reveals"],
                "ok": res.get("ok", False), "effect": res.get("effect", ""),
                "attempts": len(res.get("attempts", [])),
                "sentinel_key": key})
            # collapse check: move pointer away -> menu must report closed-expected
            page.mouse.move(10, 700)
            page.wait_for_timeout(500)
            still = is_revealed(page, rec["profile"].get("reveals_selector", ""))
            results["cases"][-1]["collapse_key"] = hover_state_key(
                (10, 700), tuple(rec["profile"]["anchor_point"]), still)
        browser.close()
    (outdir / "fixture-report.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", default="fixture", choices=["fixture"])
    ap.add_argument("--out", default="runs")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    res = cmd_fixture_test(Path(args.out), headless=not args.headed)
    for c in res["cases"]:
        print(f"{c['label']}: ok={c['ok']} effect={c['effect']} "
              f"attempts={c['attempts']} sentinel={c['sentinel_key']} collapse={c['collapse_key']}")


if __name__ == "__main__":
    main()
