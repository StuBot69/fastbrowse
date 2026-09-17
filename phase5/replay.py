#!/usr/bin/env python3
"""FastBrowse replay — glue phase5 tapes into the phase4 blind runner.

Converts a phase5 learning tape (events.json/profile.json) into a phase4
action map, then runs it BLIND with the sentinel gate + tripwire:

  1. load tape from runs dir (events.json preferred, profile.json fallback)
  2. convert each recorded click -> action {label, selector, expect}
     - "expect" inferred from recorded nav: click caused a nav -> expect
       url_contains on the recorded destination slug
  3. install a per-step trail gate: before each action, first REPLAY the
     recorded pointer trail from current position (precise human-move),
     so the pointer approaches along the HUMAN's proven path
  4. run via phase4 run_blind() unchanged: gate, click, verify, strikes

Usage:
  python replay.py --tape runs-stu-demo --url https://www.theguardian.com/uk \
      [--engine camofox] [--headless] [--out outdir]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "phase4"))
sys.path.insert(0, str(HERE.parent / "phase3"))
sys.path.insert(0, str(HERE.parent / "phase6"))
sys.path.insert(0, str(HERE.parent))

import blind  # phase4  # noqa: E402
from site_profiles import ProfileStore  # noqa: E402

HAS_HUMAN_MOUSE = False
try:
    import human_mouse  # noqa: E402
    HAS_HUMAN_MOUSE = True
except Exception:
    human_mouse = None


def load_tape(tapedir: Path) -> dict:
    """Load a phase5 run dir: events.json has the raw tape."""
    ev_path = tapedir / "events.json"
    prof_path = tapedir / "profile.json"
    if ev_path.exists():
        tape = json.loads(ev_path.read_text())
    elif prof_path.exists():
        tape = json.loads(prof_path.read_text()).get("events", {})
    else:
        raise SystemExit(f"no tape in {tapedir} (need events.json or profile.json)")
    return tape


def tape_to_actions(tape: dict, navs: list[dict] | None = None) -> list[dict]:
    """Recorded clicks -> phase4 action map, with nav-informed expects.

    Trail IS the approach: kept per-step in `trail_to` for the runner to
    walk before clicking (see trail_replay_runner)."""
    navs = navs or tape.get("navs") or []
    dests = [n["to"] for n in navs if n.get("to")]
    actions: list[dict] = []
    clicks = [c for c in tape.get("clicks", []) if "__fb_badge" not in (c.get("selector") or "")]
    for i, c in enumerate(clicks):
        a = {"label": (c.get("text") or c["selector"])[:60],
             "selector": c["selector"],
             "trail_to": [c.get("x"), c.get("y")]}
        # if this click's timestamp precedes a nav, expect that destination
        for d in dests:
            if c.get("t") and _nav_after(navs, c["t"]):
                a["expect"] = {"url_contains": d.rsplit("/", 1)[-1][:60]}
                break
        actions.append(a)
    return actions


def _nav_after(navs: list[dict], t_ms: int) -> bool:
    """True if any nav happened after this click time (approx — phase5 only
    records nav at page granularity, so use click position in the sequence)."""
    return len(navs) > 0


def trail_replay_runner(page, actions: list[dict], baseline, store=None,
                        use_human_mouse: bool = True, **kw) -> dict:
    """Wrap run_blind: before each selector action, walk the recorded trail
    approach for that step using precise human-mouse moves.

    Trail points are viewport-relative from the LEARN session; replayed at
    the same viewport size they land on the same elements."""
    if not (HAS_HUMAN_MOUSE and use_human_mouse):
        return blind.run_blind(page, actions, baseline, store=store,
                               use_human_mouse=False, **kw)
    orig_actions = actions
    # build per-step approach trails: use recorded global trail, slice the
    # segment leading to each click's coords (last 6 points before it)
    full_trail = _tape_trail.get("trail", []) if isinstance(_tape_trail, dict) else []
    prepared = []
    for a in actions:
        na = dict(a)
        tt = a.get("trail_to")
        if tt and full_trail:
            seg = _approach_segment(full_trail, tt)
            if seg:
                na["approach"] = seg
        prepared.append(na)
    # monkey-patch: run each action with approach moves then delegate
    return _run_with_approach(page, prepared, baseline, store, **kw)


_tape_trail: dict = {}


def _approach_segment(trail: list, to_xy: list) -> list[tuple[float, float]]:
    """Last N trail points ending near the click target."""
    tx, ty = to_xy
    scored = []
    for idx, (x, y, _t) in enumerate(trail):
        d2 = (x - tx) ** 2 + (y - ty) ** 2
        scored.append((d2, idx))
    scored.sort()
    if not scored or scored[0][0] > 80 ** 2:
        return []  # recorded trail never got near this target
    end_idx = scored[0][1] + 1
    start_idx = max(0, end_idx - 6)
    return [(x, y) for x, y, _t in trail[start_idx:end_idx]]


def _run_with_approach(page, prepared: list, baseline, store, **kw) -> dict:
    """Re-implementation of run_blind loop with trail approaches injected.
    Delegates to blind.run_blind by pre-moving the pointer before each call
    via a wrapper page proxy that human-moves on locator resolution."""
    # simplest robust route: do the approach moves ourselves, then call
    # run_blind with modified actions that skip mouse (it clicks by locator)
    results = {"log": [], "approach_stats": []}
    from blind import run_blind  # already imported but be explicit

    for a in prepared:
        seg = a.pop("approach", None)
        if seg:
            moved, failed = 0, 0
            for (x, y) in seg:
                try:
                    human_mouse.human_move(page, x, y, precise=True)
                    moved += 1
                except Exception:
                    failed += 1
            results["approach_stats"].append(
                {"label": a.get("label"), "approach_pts": moved, "approach_fail": failed})
    # remaining fields clean — run the standard blind runner on all actions
    res = run_blind(page, prepared, baseline, store=store, use_human_mouse=True, **kw)
    results.update(res)
    return results


def main() -> dict | None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True, help="phase5 runs dir (events.json)")
    ap.add_argument("--url", required=True, help="start URL (recorded start_url by default if omitted here — must pass explicitly)")
    ap.add_argument("--engine", default="camofox", choices=["chromium", "camofox"])
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--out", default="runs-replay")
    args = ap.parse_args()

    tapedir = Path(args.tape)
    tape = load_tape(tapedir)
    _tape_trail.update(tape)  # trail for approach computation
    actions = tape_to_actions(tape, tape.get("navs"))
    print(f"REPLAY: {len(actions)} actions from tape, "
          f"trail {len(tape.get('trail', []))} pts")

    from playwright.sync_api import sync_playwright
    from camoufox.sync_api import Camoufox
    store = ProfileStore(HERE / "runs-camofox" / "profiles.json")
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    report: dict = {"tape": str(tapedir), "url": args.url, "n_actions": len(actions)}
    if args.engine == "camofox":
        with Camoufox(headless=args.headless) as browser:
            return _replay_flow(browser, store, report, actions)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        try:
            return _replay_flow(browser, store, report, actions)
        finally:
            browser.close()


def _replay_flow(browser, store: ProfileStore, report: dict, actions: list) -> dict:
    page = blind.fresh_page(browser)
    page.goto(report["url"], wait_until="domcontentloaded", timeout=45000)
    blind.wait_past_challenge(page)
    page.wait_for_timeout(3000)
    baseline = blind.main_region_blocks(page)
    res = trail_replay_runner(page, actions, baseline, store=store,
                              use_human_mouse=True)
    report["replay"] = res
    print(json.dumps({k: res[k] for k in
                      ("strikes", "planner_bytes", "snapshots_used",
                       "blind_actions")}, indent=1))
    for e in res.get("log", []):
        print(" ", e.get("action"), "->", e.get("outcome"),
              "|", e.get("effect", e.get("gate", ""))[:80])
    for s in res.get("approach_stats", []):
        print("  approach:", s["label"][:40], f"{s['approach_pts']}pts/{s['approach_fail']}fail")
    outdir = Path(report.get("_out", "runs-replay"))
    (outdir / "replay-report.json").write_text(json.dumps(report, indent=1)[:500_000])
    print(f"report -> {outdir / 'replay-report.json'}")
    return report


if __name__ == "__main__":
    main()
