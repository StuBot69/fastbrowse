"""Sentinel phase-1 measurement harness (reusable for phase 2).

Usage:
  python measure.py [--flow wikipedia] [--out snapshots] [--headless]
  python regions.py <snapshots_dir>   # re-analyze saved snapshots
"""
import argparse, hashlib, json, re, sys, time
from pathlib import Path

REGION_SELECTORS = {
    "header": "header, [role=banner]",
    "nav": "nav, [role=navigation]",
    "main": "main, [role=main], #bodyContent, #content",
    "footer": "footer, [role=contentinfo]",
}

# Volatile attributes / patterns normalized before hashing.
VOLATILE_ATTR_RE = re.compile(
    r'\s(?:data-[a-zA-Z-_]*(?:timestamp|time|nonce|session|token|csrf|request-id|page-id|revid)[a-zA-Z-_]*|'
    r'nonce|csrf-token|data-mw-revid|data-page-id|aria-keyshortcuts)="[^"]*"',
    re.I,
)
VOLATILE_TEXT_RE = re.compile(
    r'(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AP]M)?|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}[^"<]*)'
)


def normalize(html: str) -> str:
    html = VOLATILE_ATTR_RE.sub("", html)
    html = VOLATILE_TEXT_RE.sub("<TIME>", html)
    return re.sub(r"\s+", " ", html).strip()


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def capture_step(page, name: str, outdir: Path) -> dict:
    """Capture AX snapshot + full DOM + per-region hashes. Returns record dict."""
    try:
        cdp = page.context.new_cdp_session(page)
        ax = cdp.send("Accessibility.getFullAXTree")
    except Exception as e:
        ax = {"error": str(e)}
    ax_str = json.dumps(ax, ensure_ascii=False)
    try:
        dom = page.content()
    except Exception as e:
        dom = f"<error>{e}</error>"
    try:
        shot = page.screenshot(full_page=False)
        shot_bytes = len(shot)
    except Exception:
        shot_bytes = 0
    regions = {}
    for rname, sel in REGION_SELECTORS.items():
        try:
            els = page.query_selector_all(sel)
            combined = "\n".join((el.inner_html() or "") for el in els[:4])
            norm = normalize(combined)
            regions[rname] = {
                "bytes": len(combined.encode("utf-8")),
                "norm_bytes": len(norm.encode("utf-8")),
                "hash": sha(norm),
            }
        except Exception as e:
            regions[rname] = {"bytes": 0, "norm_bytes": 0,
                              "hash": "error", "error": str(e)}
    rec = {
        "step": name,
        "url": page.url,
        "title": page.title() if hasattr(page, "title") else "",
        "ts": time.time(),
        "ax_bytes": len(ax_str.encode("utf-8")),
        "dom_bytes": len(dom.encode("utf-8")),
        "screenshot_bytes": shot_bytes,
        "regions": regions,
    }
    (outdir / f"{name}.ax.json").write_text(ax_str, encoding="utf-8")
    (outdir / f"{name}.dom.html").write_text(dom, encoding="utf-8")
    return rec


def diff_regions(prev: dict, cur: dict):
    changed, unchanged = [], []
    for r in REGION_SELECTORS:
        if prev["regions"][r]["hash"] != cur["regions"][r]["hash"]:
            changed.append(r)
        else:
            unchanged.append(r)
    changed_bytes = sum(cur["regions"][r]["norm_bytes"] for r in changed)
    full_bytes = cur["ax_bytes"] + cur["dom_bytes"]
    return changed, unchanged, changed_bytes, full_bytes


WIKI_FLOW = [
    ("01-home", "goto:https://en.wikipedia.org/wiki/Main_Page", None),
    ("02-search", "search:Large language model", "input[name=search]"),
    ("03-article", "goto:https://en.wikipedia.org/wiki/Large_language_model", None),
    ("04-link1", "click:#bodyContent a", None),   # first internal link in article body
    ("05-link2", "click:#bodyContent a", None),   # again on the new article
]


def dismiss_banners(page):
    """Dismiss cookie/consent banners the way a normal agent would."""
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
            els = page.query_selector_all(sel)
            for el in els[:3]:
                try:
                    if el and el.is_visible():
                        el.click(timeout=2000)
                        page.wait_for_timeout(500)
                        break
                except Exception:
                    continue
        except Exception:
            pass


def run_flow(flow_name: str, outdir: Path, headless: bool = True) -> list:
    from playwright.sync_api import sync_playwright
    outdir.mkdir(parents=True, exist_ok=True)
    records = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        for name, action, _ in WIKI_FLOW:
            if action.startswith("goto:"):
                page.goto(action[5:], wait_until="domcontentloaded",
                          timeout=30000)
                page.wait_for_timeout(1500)
            elif action.startswith("search:"):
                q = action[7:]
                page.goto("https://en.wikipedia.org/wiki/Main_Page",
                          wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1200)
                box = page.query_selector("input[name=search]")
                box.fill(q)
                page.wait_for_timeout(800)
                try:
                    page.query_selector("input[name=search]").press("Enter")
                except Exception:
                    page.keyboard.press("Enter")
                page.wait_for_load_state("domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
            elif action.startswith("click:"):
                sel = action[6:]
                page.wait_for_selector(sel, timeout=15000)
                # first visible http/wiki internal link
                links = page.query_selector_all(sel)
                target = None
                for l in links:
                    try:
                        href = l.get_attribute("href") or ""
                        if "wikipedia.org/wiki/" in href:
                            href = "/" + href.split("wikipedia.org/", 1)[-1]
                        if href.startswith("/wiki/") and ":" not in href.split("/wiki/")[-1] \
                                and l.is_visible():
                            target = l
                            break
                    except Exception:
                        continue
                if target is None:
                    raise RuntimeError(f"{name}: no internal link found")
                href = target.get_attribute("href") or ""
                if "wikipedia.org/wiki/" in href:
                    href = "/" + href.split("wikipedia.org/", 1)[-1]
                page.goto("https://en.wikipedia.org" + href,
                          wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
            dismiss_banners(page)
            rec = capture_step(page, name, outdir)
            records.append(rec)
            print(f"[{name}] url={rec['url'][:70]} ax={rec['ax_bytes']} "
                  f"dom={rec['dom_bytes']}", flush=True)
        # Volatility probe: re-capture last page twice, no action, 3s apart.
        page.wait_for_timeout(3000)
        rec_a = capture_step(page, "06-volatility-a", outdir)
        page.wait_for_timeout(3000)
        rec_b = capture_step(page, "06-volatility-b", outdir)
        records += [rec_a, rec_b]
        print(f"[volatility] a: ax={rec_a['ax_bytes']} dom={rec_a['dom_bytes']} "
              f"b: ax={rec_b['ax_bytes']} dom={rec_b['dom_bytes']}", flush=True)
        browser.close()
    (outdir / "records.json").write_text(
        json.dumps(records, indent=1), encoding="utf-8")
    return records


def report(records: list) -> str:
    lines = []
    lines.append("| step | full snap bytes (ax+dom) | changed regions | "
                 "changed-region bytes | ratio |")
    lines.append("|---|---|---|---|---|")
    total_full, total_changed = 0, 0
    prev = None
    for rec in records:
        if prev is None:
            lines.append(f"| {rec['step']} | {rec['ax_bytes']+rec['dom_bytes']} "
                         f"| — (baseline) | — | — |")
        else:
            changed, unchanged, ch_bytes, full = diff_regions(prev, rec)
            ratio = full / ch_bytes if ch_bytes else float("inf")
            total_full += full
            total_changed += ch_bytes
            lines.append(f"| {rec['step']} | {full} | {','.join(changed) or 'NONE'} "
                         f"| {ch_bytes} | {ratio:.2f}x |")
        prev = rec
    lines.append("")
    if total_changed:
        lines.append(f"Aggregate (steps 2..n): full={total_full} changed={total_changed} "
                     f"savings={total_full/total_changed:.2f}x")
    return "\n".join(lines)


MESSY_CANDIDATES = [
    "https://www.dailymail.co.uk/home/index.html",
    "https://www.thesun.co.uk/",
    "https://www.theguardian.com/uk",
    "https://edition.cnn.com/",
    "https://www.bbc.co.uk/news",
]

META_REFRESH_DATA_URL = (
    "data:text/html,"
    "<html><head>"
    "<meta http-equiv='refresh' content='2;url=https://example.com/'>"
    "</head><body><h1>redirecting\u2026</h1></body></html>"
)


def volatility_score(records: list) -> dict:
    """Per-region churn across a no-action re-capture series.

    Returns {region: {'changes': n, 'probes': m, 'rate': f, 'hashes': [...]}}.
    A region that changes on every probe is chronically noisy.
    """
    score = {}
    for r in REGION_SELECTORS:
        hashes = [rec["regions"][r]["hash"] for rec in records]
        changes = sum(1 for a, b in zip(hashes, hashes[1:]) if a != b)
        probes = max(len(hashes) - 1, 1)
        score[r] = {"changes": changes, "probes": probes,
                    "rate": changes / probes, "hashes": hashes}
    return score


def run_messy(outdir: Path, headless: bool = True) -> tuple:
    """MESSY flow: news homepage -> dismiss banner -> scroll x2 -> 5x5s probe."""
    from playwright.sync_api import sync_playwright
    outdir.mkdir(parents=True, exist_ok=True)
    records, meta = [], {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        landed = None
        for url in MESSY_CANDIDATES:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                # Blocker check: bot walls return HTTP-200 stubs ("Access
                # Denied", consent-only shells). Skip on tiny/denied content.
                try:
                    dom_len = len(page.content() or "")
                    title = page.title() or ""
                except Exception:
                    dom_len, title = 0, ""
                if dom_len < 20000 or title.strip() in (
                        "Access Denied", "Verifying Device") \
                        or "Access Denied" in (page.url or ""):
                    meta.setdefault("blocked", {})[url] = (
                        f"bot-wall stub (dom={dom_len} title={title!r})")
                    continue
                landed = url
                break
            except Exception as e:
                meta.setdefault("goto_errors", {})[url] = str(e)[:200]
                continue
        if landed is None:
            browser.close()
            raise RuntimeError(f"all messy candidates failed: {meta}")
        meta["landed"] = landed
        dismiss_banners(page)
        page.wait_for_timeout(1000)
        rec = capture_step(page, "01-landed", outdir)
        records.append(rec)
        print(f"[01-landed] url={rec['url'][:70]} ax={rec['ax_bytes']} "
              f"dom={rec['dom_bytes']}", flush=True)
        # Scroll twice, capturing after each (infinite/dynamic content).
        for i in (2, 3):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            page.wait_for_timeout(2000)
            dismiss_banners(page)
            rec = capture_step(page, f"0{i}-scroll{i-1}", outdir)
            records.append(rec)
            print(f"[0{i}-scroll{i-1}] url={rec['url'][:70]} "
                  f"ax={rec['ax_bytes']} dom={rec['dom_bytes']}", flush=True)
        # LONG volatility probe: 5 captures, 5s apart, no action.
        vol = []
        for i in range(1, 6):
            page.wait_for_timeout(5000)
            rec = capture_step(page, f"04-vol-{i}", outdir)
            vol.append(rec)
            print(f"[04-vol-{i}] ax={rec['ax_bytes']} dom={rec['dom_bytes']}",
                  flush=True)
        records += vol
        meta["volatility"] = volatility_score(vol)
        browser.close()
    (outdir / "records.json").write_text(
        json.dumps(records, indent=1), encoding="utf-8")
    (outdir / "meta.json").write_text(
        json.dumps(meta, indent=1), encoding="utf-8")
    return records, meta


def run_redirects(outdir: Path, headless: bool = True) -> tuple:
    """REDIRECTS flow: httpbin chain + meta-refresh + control navigation."""
    from playwright.sync_api import sync_playwright
    outdir.mkdir(parents=True, exist_ok=True)
    records, cases = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        targets = [
            ("r1-httpbin-chain", "https://httpbin.org/redirect/3"),
            ("r2-meta-refresh", META_REFRESH_DATA_URL),
            ("r3-control", "https://en.wikipedia.org/wiki/Main_Page"),
        ]
        for name, url in targets:
            case = {"name": name, "start_url": url}
            # Mid-redirect observation attempt: start nav (no wait) + capture.
            try:
                try:
                    page.goto(url, wait_until="commit", timeout=30000)
                except Exception:
                    pass
                page.wait_for_timeout(300)
                mid = capture_step(page, f"{name}-mid", outdir)
                case["mid_url"] = mid["url"]
                case["mid_note"] = "captured during settle window"
                records.append(mid)
            except Exception as e:
                case["mid_url"] = None
                case["mid_note"] = f"mid-capture failed: {str(e)[:200]}"
            # Settled observation.
            try:
                t0 = time.time()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                    case["settle"] = "networkidle reached"
                except Exception:
                    case["settle"] = "networkidle TIMEOUT (page keeps polling)"
                case["elapsed_s"] = round(time.time() - t0, 2)
                rec = capture_step(page, f"{name}-settled", outdir)
                records.append(rec)
                case["final_url"] = rec["url"]
                case["redirected"] = (rec["url"].rstrip("/") !=
                                      url.rstrip("/"))
                case["ax_bytes"] = rec["ax_bytes"]
                case["dom_bytes"] = rec["dom_bytes"]
                print(f"[{name}] start={url[:60]} final={rec['url'][:60]} "
                      f"t={case['elapsed_s']}s settle={case['settle']}",
                      flush=True)
            except Exception as e:
                case["final_url"] = None
                case["settle"] = f"goto failed: {str(e)[:200]}"
                print(f"[{name}] FAILED: {str(e)[:200]}", flush=True)
            cases.append(case)
        browser.close()
    (outdir / "records.json").write_text(
        json.dumps(records, indent=1), encoding="utf-8")
    (outdir / "redirect_cases.json").write_text(
        json.dumps(cases, indent=1), encoding="utf-8")
    return records, cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flow", default="wikipedia",
                    choices=["wikipedia", "messy", "redirects"])
    ap.add_argument("--out", default="snapshots")
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    outdir = Path(args.out)
    headless = not args.headed
    if args.flow == "messy":
        records, meta = run_messy(outdir, headless=headless)
        print(report(records))
        print("\nVolatility (5 probes, 5s apart, no action):")
        for r, s in meta["volatility"].items():
            print(f"  {r}: {s['changes']}/{s['probes']} changed "
                  f"(rate={s['rate']:.2f})")
    elif args.flow == "redirects":
        records, cases = run_redirects(outdir, headless=headless)
        print(report(records))
    else:
        records = run_flow(args.flow, outdir, headless=headless)
        print(report(records))


if __name__ == "__main__":
    main()
