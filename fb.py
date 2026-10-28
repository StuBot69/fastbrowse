#!/usr/bin/env python3
"""fb — FastBrowse single entry point. One command shape for humans,
harness bots, and free-model workers.

  fb hunt --ask "white-haired female cyborg" [--site pixabay] [--out NAME]
      Eyes-first grid hunt + download + vision receipt. Writes
      phase5/runs/<NAME>/run-report.json. Exits 0 on download, 1 on
      no-match/bot-wall, 2 on vision NO (file fetched but wrong subject).
      Sites: pixabay | pexels | unsplash | deviantart | commons.
  fb replay --tape runs/<NAME> --href <photo-url> [--ask ...]
      Blind replay of a LEARNED href (no hunt, hybrid gate only).
  fb verify <file> --ask "..."
      Eyes on a file via the vision endpoint. Prints YES/NO + sentence.
  fb sites
      Learned site knowledge (sites/<domain>/): selectors, last_verified,
      needs_relearn flags.
  fb rules
      The 16 hard-won rules (REPORT.md) — required reading for new workers.

Vision degrades gracefully: FASTBROWSE_NO_VISION=1 or unreachable endpoint
-> hunt falls back to first-result, verify prints SKIP. Never crashes.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fb_config  # noqa: E402

PHASE5 = HERE / "phase5"


def cmd_hunt(args) -> int:
    site = (args.site or "pixabay").lower()
    out = args.out or f"hunt-{site}-1"
    if site == "pixabay":
        cmd = [sys.executable, str(PHASE5 / "pixabay_suite.py"),
               "--run", "1", "--query", args.ask, "--out", str(out)]
        if args.verify:
            cmd += ["--verify", args.verify]
    elif site == "pexels":
        cmd = [sys.executable, str(PHASE5 / "pexels_suite.py"),
               "--run", "1", "--query", args.ask, "--out", str(out)]
        if args.verify:
            cmd += ["--verify", args.verify]
    elif site == "unsplash":
        cmd = [sys.executable, str(PHASE5 / "unsplash_suite.py"),
               "--run", "1", "--query", args.ask,
               "--ask", args.verify or args.ask, "--out", str(out)]
        if args.verify:
            cmd += ["--verify", args.verify]
    elif site == "deviantart":
        cmd = [sys.executable, str(PHASE5 / "deviantart_suite.py"),
               "--run", "1", "--query", args.ask, "--out", str(out)]
        if args.verify:
            cmd += ["--verify", args.verify]
    elif site == "commons":
        cmd = [sys.executable, str(PHASE5 / "sydney_suite.py"),
               "--run", "1", "--out", str(out)]
    else:
        print(f"fb hunt: unknown site {site!r} (pixabay|pexels|unsplash|deviantart|commons)", file=sys.stderr)
        return 3
    print(f"fb hunt: {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=str(PHASE5))
    if r.returncode != 0:
        return r.returncode
    # vision verdict gates the exit code: downloaded-but-wrong = 2
    try:
        rep = json.loads((HERE / out / "run-report.json").read_text())
    except Exception:
        try:
            rep = json.loads((PHASE5 / out / "run-report.json").read_text())
        except Exception:
            return 0  # report unreadable — caller inspects stdout
    v = (rep.get("vision") or {}).get("verdict", "SKIP")
    dl = rep.get("downloads") or []
    print(f"fb hunt done: downloads={len(dl)} vision={v} "
          f"total_s={rep.get('total_s')} planner_bytes={rep.get('planner_bytes')}")
    if v == "NO":
        return 2
    return 0 if dl else 1


def cmd_replay(args) -> int:
    cmd = [sys.executable, str(PHASE5 / "pixabay_suite.py"),
           "--run", "2", "--tape", args.tape, "--blind-href", args.href,
           "--out", args.out or "runs/replay-1"]
    if args.ask:
        cmd += ["--query", args.ask, "--verify", args.ask]
    print(f"fb replay: {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=str(PHASE5)).returncode


def cmd_verify(args) -> int:
    sys.path.insert(0, str(PHASE5))
    from grid_hunt import verify_download
    try:
        ok, txt, s = verify_download(args.file, args.ask)
        print(f"{'YES' if ok else 'NO'} ({s}s): {txt}")
        return 0 if ok else 2
    except Exception as e:
        print(f"SKIP: vision unavailable ({e})")
        return 3


def cmd_sites(_args) -> int:
    sys.path.insert(0, str(PHASE5))
    import site_store
    for domain, kind in site_store.all_sites():
        d = site_store.load_profile(domain)
        if not d:
            continue
        stale = "STALE " + d.get("stale_reason", "") if d.get("needs_relearn") else "ok"
        print(f"{domain}: {len(d.get('selectors', []))} selectors, "
              f"verified {d.get('last_verified', '?')} [{stale}] ({kind})")
    return 0


def cmd_doctor(_args) -> int:
    """One-command triage for operators: what works, what's missing."""
    import importlib.util
    ok = lambda name, good, detail="": print(
        f"[{'ok' if good else 'MISSING'}] {name}" + (f" — {detail}" if detail else ""))
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    ok("python", True, f"{sys.version.split()[0]} (venv={in_venv})")
    for mod in ("playwright", "camoufox", "PIL"):
        spec = importlib.util.find_spec(mod)
        if spec:
            try:
                m = importlib.import_module(mod)
                ver = getattr(m, "__version__", "") or "installed"
            except Exception:
                ver = "installed (import check skipped)"
            ok(f"dep {mod}", True, ver if isinstance(ver, str) else "installed")
        else:
            ok(f"dep {mod}", False, "pip install -r requirements.txt")
    pw_cache = Path.home() / ".cache" / "ms-playwright"
    ok("chromium (playwright)", pw_cache.exists(),
       str(pw_cache) if pw_cache.exists()
       else "optional with camofox engine; else: python -m playwright install chromium")
    ok(".env file", (HERE / ".env").exists(),
       "cp .env.example .env" if not (HERE / ".env").exists() else "loaded")
    def _show_model(m: str) -> str:
        # model ids can be local file paths — never print home dirs
        return "model=" + (m.rsplit("/", 1)[-1] if "/" in m else m)
    pv = fb_config.vision_available(timeout=5)
    ok("primary vision", pv,
       _show_model(fb_config.VISION_MODEL) if pv
       else ("FASTBROWSE_VISION_URL unset — see .env.example"
             if not fb_config.VISION_URL else "unreachable (check .env)"))
    fb = fb_config.fallback_available(timeout=5)
    ok("fallback vision", fb,
       _show_model(fb_config.FALLBACK_MODEL) if fb
       else "unset/unreachable — see .env.example")
    if not pv and not fb:
        print("  -> hunts run degraded (first-result, receipt SKIP). "
              "See .env.example.")
    sys.path.insert(0, str(PHASE5))
    import site_store
    sites = site_store.all_sites()
    ok("learned sites", True, f"{len(sites)} ({', '.join(d for d, _ in sites) or 'none yet'})")
    try:
        fb_config.PICS_DIR.mkdir(parents=True, exist_ok=True)
        ok("pics sink", True, str(fb_config.PICS_DIR))
    except Exception as e:
        ok("pics sink", False, str(e)[:80])
    return 0


def cmd_rules(_args) -> int:
    in_rules = False
    for line in (HERE / "REPORT.md").read_text().splitlines():
        if line.startswith("## Key rules"):
            in_rules = True
        elif line.startswith("## ") and in_rules:
            break
        if in_rules:
            print(line)
    return 0


FB_VERSION = "0.3.0"


def cmd_shot(args) -> int:
    """Screenshot one or more URLs with camofox/chromium."""
    urls = args.urls
    if len(urls) == 1 and urls[0].startswith("@"):
        # Read URLs from file (one per line)
        with open(urls[0][1:]) as f:
            urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    outdir = args.out or f"shots-{int(time.time())}"
    full = args.full
    headless = not args.headed
    engine = args.engine
    wait = args.wait
    
    from playwright.sync_api import sync_playwright
    import importlib.util
    use_camofox = (engine == "camofox")
    if use_camofox:
        spec = importlib.util.find_spec("camoufox.sync_api")
        if not spec:
            print("camoufox not available; install with: pip install camoufox", file=sys.stderr)
            return 3
    
    PICS_DIR = fb_config.PICS_DIR
    PICS_DIR.mkdir(parents=True, exist_ok=True)
    outdir_path = Path(outdir)
    outdir_path.mkdir(parents=True, exist_ok=True)
    
    results = []
    if use_camofox:
        from camoufox.sync_api import Camoufox
        with Camoufox(headless=headless) as browser:
            for i, url in enumerate(urls):
                res = _shot_one(browser, url, outdir_path, full, wait, i, use_camofox)
                results.append(res)
                if i < len(urls) - 1:
                    time.sleep(args.gap)
    else:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            try:
                for i, url in enumerate(urls):
                    res = _shot_one(browser, url, outdir_path, full, wait, i, use_camofox)
                    results.append(res)
                    if i < len(urls) - 1:
                        time.sleep(args.gap)
            finally:
                browser.close()
    
    # Receipt
    receipt = {
        "command": "shot",
        "engine": engine,
        "headless": headless,
        "full_page": full,
        "results": results,
        "total": len(results),
        "ok": sum(1 for r in results if r.get("ok")),
        "pics_dir": str(PICS_DIR),
        "outdir": str(outdir_path),
    }
    (outdir_path / "shot-receipt.json").write_text(json.dumps(receipt, indent=1))
    print(f"fb shot done: {receipt['ok']}/{receipt['total']} ok -> {outdir_path}")
    return 0 if receipt['ok'] == receipt['total'] else 1


def _detect_stub(page) -> str:
    """Return a reason string if the loaded page is an error/challenge stub.

    A Google Play 404 still returns HTTP 200 with a tiny shell, so the status
    code is not enough — check the rendered title, body text and DOM size.
    """
    try:
        title = (page.title() or "").strip()
    except Exception:
        title = ""
    try:
        body = (page.inner_text("body") or "").strip()
    except Exception:
        body = ""
    try:
        dom_bytes = len(page.content() or "")
    except Exception:
        dom_bytes = 0

    low_title = title.lower()
    low_body = body.lower()[:2000]

    if "just a moment" in low_title or "attention required" in low_title:
        return f"bot challenge (title={title!r})"
    if "not found" in low_title or title.lower() == "404":
        return f"404 page (title={title!r})"
    if "requested url was not found" in low_body:
        return "404 shell (body says requested URL was not found)"
    if "page not found" in low_body and dom_bytes < 60000:
        return f"404 shell (dom={dom_bytes})"
    if "access denied" in low_title or "access denied" in low_body[:500]:
        return f"access denied (title={title!r})"
    # Tiny DOM with no meaningful text is a stub shell.
    if dom_bytes < 25000 and len(body) < 200:
        return f"empty shell (dom={dom_bytes}, text={len(body)} chars)"
    return ""


def _shot_one(browser, url: str, outdir: Path, full: bool, wait: float, idx: int, use_camofox: bool) -> dict:
    """Screenshot a single URL."""
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", url.split("://", 1)[-1].split("/")[0])[:60]
    out_png = outdir / f"{idx:02d}-{safe_name}.png"
    out_json = outdir / f"{idx:02d}-{safe_name}.json"
    
    start = time.time()
    page = None
    try:
        if hasattr(browser, "new_page"):
            page = browser.new_page(viewport={"width": 1366, "height": 900})
        else:
            ctx = browser.new_context(viewport={"width": 1366, "height": 900})
            page = ctx.new_page()
        
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        
        # Dismiss common consent banners.
        # Guard: a generic text selector ("OK") can match a real page button
        # that navigates away (Google Play "OK" -> Books store). Record the
        # URL and go back if the click changed the page.
        for sel in [
            "button:has-text('Accept')", "button:has-text('Got it')",
            "button:has-text('Dismiss')", "button:has-text('Close')",
            "button:has-text('Reject')", "button:has-text('Continue')",
            "button:has-text('I agree')", "button:has-text('Agree')",
            "button:has-text('Consent')", "button:has-text('OK')",
            "[aria-label='Close']", ".cky-btn-accept",
            "#onetrust-accept-btn-handler", "#onetrust-reject-all-handler",
            ".fc-cta-consent", "[data-testid='accept-button']",
        ]:
            try:
                loc = page.locator(sel)
                if loc.count() == 1 and loc.first.is_visible():
                    pre_url = page.url
                    loc.first.click(timeout=2000)
                    page.wait_for_timeout(800)
                    if page.url != pre_url:
                        # The click navigated — undo it and keep the real page.
                        page.goto(pre_url, wait_until="domcontentloaded",
                                  timeout=30000)
                        page.wait_for_timeout(1000)
                    break
            except Exception:
                pass
        
        page.wait_for_timeout(int(wait * 1000))
        
        # Detect error/bot-wall stubs — a 200-status page can still be a
        # "not found" shell or a challenge. Report these as failures, not ok.
        stub_reason = _detect_stub(page)
        
        shot = page.screenshot(full_page=full, path=str(out_png))
        shot_bytes = len(shot) if shot else out_png.stat().st_size
        
        # Also copy to PICS_DIR
        import shutil
        pics_copy = fb_config.PICS_DIR / out_png.name
        shutil.copy2(out_png, pics_copy)
        
        result = {
            "idx": idx,
            "url": url,
            "final_url": page.url,
            "title": page.title() if hasattr(page, "title") else "",
            "png": str(out_png),
            "pics_copy": str(pics_copy),
            "bytes": shot_bytes,
            "ok": not stub_reason,
            "stub": stub_reason,
            "elapsed_s": round(time.time() - start, 1),
        }
        out_json.write_text(json.dumps(result, indent=1))
        return result
    except Exception as e:
        return {
            "idx": idx,
            "url": url,
            "ok": False,
            "error": str(e)[:200],
            "elapsed_s": round(time.time() - start, 1),
        }
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass


def main() -> None:
    ap = argparse.ArgumentParser(prog="fb", description="FastBrowse entry point")
    ap.add_argument("--version", action="version", version=f"fb {FB_VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hunt", help="eyes-first hunt + download + receipt")
    h.add_argument("--ask", required=True, help="what to find (search terms)")
    h.add_argument("--site", default="pixabay",
                   help="pixabay|pexels|unsplash|deviantart|commons")
    h.add_argument("--verify", default=None, help="vision receipt ask")
    h.add_argument("--out", default=None, help="runs/<NAME>")
    h.set_defaults(fn=cmd_hunt)
    r = sub.add_parser("replay", help="blind replay of a learned href")
    r.add_argument("--tape", required=True)
    r.add_argument("--href", required=True)
    r.add_argument("--ask", default=None)
    r.add_argument("--out", default=None)
    r.set_defaults(fn=cmd_replay)
    v = sub.add_parser("verify", help="eyes on a file")
    v.add_argument("file")
    v.add_argument("--ask", required=True)
    v.set_defaults(fn=cmd_verify)
    s = sub.add_parser("sites", help="learned site knowledge")
    s.set_defaults(fn=cmd_sites)
    u = sub.add_parser("rules", help="the 16 rules — read first")
    u.set_defaults(fn=cmd_rules)
    d = sub.add_parser("doctor", help="health check: venv, browsers, vision")
    d.set_defaults(fn=cmd_doctor)
    sh = sub.add_parser("shot", help="screenshot one or more URLs with camofox/chromium")
    sh.add_argument("urls", nargs="+", help="URL(s) to screenshot, or @file to read list from file")
    sh.add_argument("--out", default=None, help="output directory name under phase5/runs/")
    sh.add_argument("--engine", default="camofox", choices=["camofox", "chromium"])
    sh.add_argument("--full", action="store_true", default=True, help="full page screenshot (default)")
    sh.add_argument("--no-full", dest="full", action="store_false", help="viewport only")
    sh.add_argument("--wait", type=float, default=3.0, help="seconds to wait after load before shot")
    sh.add_argument("--gap", type=float, default=1.0, help="seconds between URLs")
    sh.add_argument("--headed", action="store_true", help="run headed (visible browser)")
    sh.set_defaults(fn=cmd_shot)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
