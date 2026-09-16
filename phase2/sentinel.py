"""FastBrowse phase-2 sentinel prototype.

Loop per page:
  (1) baseline region map via capture_step-style capture (AX + DOM + regions + strips)
  (2) DUMB trigger: per-strip pixel diff + per-region DOM-hash diff at ~2fps
      for a ~30s window (no ML). Every trip logged with region + changed bytes.
  (3) classify each trip two ways:
      - Inception mercury-2 on the changed region's TEXT/HTML
      - inkling:free (OpenRouter) on the region strip screenshot
      Both answer: {type, disposition} where
        type: cookie-banner|modal|ad|navigation|noise|other
        disposition: dismiss|ignore|escalate

Usage:
  python sentinel.py --page bbc|wiki|dynamic|all [--duration 30] [--fps 2]
      [--out runs] [--classify|--no-classify]

API keys come from env (INCEPTION_API_KEY, OPENROUTER_API_KEY).
Network calls time-boxed to 60s.
"""
import argparse, base64, hashlib, io, json, os, re, sys, time
from pathlib import Path

REGION_SELECTORS = {
    "header": "header, [role=banner]",
    "nav": "nav, [role=navigation]",
    "main": "main, [role=main], #bodyContent, #content",
    "footer": "footer, [role=contentinfo]",
}
N_STRIPS = 6  # horizontal viewport bands for the dumb pixel trigger

VOLATILE_ATTR_RE = re.compile(
    r'\s(?:data-[a-zA-Z-_]*(?:timestamp|time|nonce|session|token|csrf|request-id|page-id|revid)[a-zA-Z-_]*|'
    r'nonce|csrf-token|data-mw-revid|data-page-id|aria-keyshortcuts)="[^"]*"',
    re.I,
)
VOLATILE_TEXT_RE = re.compile(
    r'(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AP]M)?|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}[^"<]*)'
)

CLASSIFY_SCHEMA = "{type: cookie-banner|modal|ad|navigation|noise|other, disposition: dismiss|ignore|escalate}"
MERcury_MODEL = "mercury-2"  # Inception
INKLING_MODEL = "thinkingmachines/inkling:free"  # OpenRouter
CALL_TIMEOUT = 60

BANNER_HINT_RE = re.compile(
    r"cookie|consent|accept|reject|privacy|gdpr|we use cookies|got it|manage preferences",
    re.I,
)


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def normalize(html: str) -> str:
    html = VOLATILE_ATTR_RE.sub("", html)
    html = VOLATILE_TEXT_RE.sub("<TIME>", html)
    return re.sub(r"\s+", " ", html).strip()


# ---------------------------------------------------------------- baseline ---

def capture_baseline(page, name: str, outdir: Path) -> dict:
    """capture_step-style baseline: AX + DOM + per-region hashes + per-strip shots."""
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
        full_shot = page.screenshot(full_page=False)
    except Exception:
        full_shot = b""
    vp = page.viewport_size or {"width": 1366, "height": 900}
    regions = {}
    for rname, sel in REGION_SELECTORS.items():
        try:
            els = page.query_selector_all(sel)
            combined = "\n".join((el.inner_html() or "") for el in els[:4])
            try:
                text = "\n".join((el.inner_text() or "") for el in els[:4])[:4000]
            except Exception:
                text = ""
            norm = normalize(combined)
            regions[rname] = {
                "bytes": len(combined.encode("utf-8")),
                "norm_bytes": len(norm.encode("utf-8")),
                "hash": sha(norm),
                "text": text,
            }
        except Exception as e:
            regions[rname] = {"bytes": 0, "norm_bytes": 0, "hash": "error",
                              "error": str(e), "text": ""}
    strips = strip_capture(page, vp)
    rec = {"step": name, "url": page.url, "title": page.title(),
           "ts": time.time(), "ax_bytes": len(ax_str.encode()),
           "dom_bytes": len(dom.encode()), "screenshot_bytes": len(full_shot),
           "viewport": vp, "regions": regions,
           "strips": {k: {"hash": v["hash"], "bytes": v["bytes"]} for k, v in strips.items()}}
    (outdir / f"{name}.baseline.json").write_text(
        json.dumps({k: (v if k != "regions" else
                   {rk: {kk: vv for kk, vv in rv.items() if kk != "text"}
                    for rk, rv in v.items()}) for k, v in rec.items()}, indent=1),
        encoding="utf-8")
    (outdir / f"{name}.dom.html").write_text(dom, encoding="utf-8")
    if full_shot:
        (outdir / f"{name}.full.png").write_bytes(full_shot)
    for k, v in strips.items():
        if v["png"]:
            (outdir / f"{name}.strip{k}.png").write_bytes(v["png"])
    return rec


def strip_capture(page, vp) -> dict:
    """Screenshot each horizontal strip; return {i: {hash, bytes, png}}."""
    strips = {}
    h = vp["height"] // N_STRIPS
    for i in range(N_STRIPS):
        clip = {"x": 0, "y": i * h,
                "width": vp["width"], "height": h if i < N_STRIPS - 1 else vp["height"] - i * h}
        try:
            png = page.screenshot(clip=clip)
            strips[i] = {"hash": sha_bytes(png), "bytes": len(png), "png": png}
        except Exception as e:
            strips[i] = {"hash": "error", "bytes": 0, "png": b"", "error": str(e)}
    return strips


def region_snapshot(page) -> dict:
    out = {}
    for rname, sel in REGION_SELECTORS.items():
        try:
            els = page.query_selector_all(sel)
            combined = "\n".join((el.inner_html() or "") for el in els[:4])
            try:
                text = "\n".join((el.inner_text() or "") for el in els[:4])[:4000]
            except Exception:
                text = ""
            norm = normalize(combined)
            out[rname] = {"hash": sha(norm), "bytes": len(norm.encode()),
                          "raw_bytes": len(combined.encode()), "text": text,
                          "html": combined[:4000]}
        except Exception as e:
            out[rname] = {"hash": "error", "bytes": 0, "raw_bytes": 0,
                          "text": "", "html": "", "error": str(e)}
    return out


# ---------------------------------------------------------------- trigger ----

def watch(page, baseline: dict, outdir: Path, duration: float, fps: float,
          label: str) -> list:
    """Dumb trigger loop. Returns list of trip dicts."""
    interval = 1.0 / fps
    n_polls = int(duration * fps)
    prev_strips = {int(k): v["hash"] for k, v in baseline["strips"].items()}
    prev_strip_bytes = {int(k): v["bytes"] for k, v in baseline["strips"].items()}
    prev_regions = {r: baseline["regions"][r]["hash"] for r in REGION_SELECTORS}
    prev_region_bytes = {r: baseline["regions"][r]["norm_bytes"] for r in REGION_SELECTORS}
    # keep last region text for classification context
    trips = []
    print(f"[{label}] watching {duration}s @ {fps}fps ({n_polls} polls)...", flush=True)
    for i in range(n_polls):
        t0 = time.time()
        try:
            cur_strips = strip_capture(page, baseline["viewport"])
        except Exception as e:
            print(f"[{label}] poll {i}: strip capture failed: {e}", flush=True)
            page.wait_for_timeout(int(interval * 1000))
            continue
        cur_regions = region_snapshot(page)
        for s, cur in cur_strips.items():
            if cur["hash"] != prev_strips.get(s):
                # changed bytes: png size delta as cheap proxy + note hash flip
                delta = abs(cur["bytes"] - prev_strip_bytes.get(s, 0))
                trips.append({
                    "poll": i, "t": round(time.time() - baseline["ts"], 2),
                    "kind": "pixel", "strip": s,
                    "region": strip_to_region(page, baseline, s),
                    "changed_bytes": delta,
                    "html": cur_regions.get(strip_to_region(page, baseline, s), {}).get("html", ""),
                    "text": cur_regions.get(strip_to_region(page, baseline, s), {}).get("text", ""),
                })
                (outdir / f"{label}.trip-p{i}-strip{s}.png").write_bytes(cur["png"] or b"")
                prev_strips[s] = cur["hash"]
                prev_strip_bytes[s] = cur["bytes"]
        for r, cur in cur_regions.items():
            if cur["hash"] != prev_regions.get(r):
                trips.append({
                    "poll": i, "t": round(time.time() - baseline["ts"], 2),
                    "kind": "dom", "strip": None,
                    "region": r,
                    "changed_bytes": abs(cur["bytes"] - prev_region_bytes.get(r, 0)),
                    "html": cur.get("html", ""),
                    "text": cur.get("text", ""),
                })
                prev_regions[r] = cur["hash"]
                prev_region_bytes[r] = cur["bytes"]
        elapsed = time.time() - t0
        page.wait_for_timeout(max(1, int((interval - elapsed) * 1000)))
    # dedupe: collapse same (kind, strip/region) trips within 2s, keep first
    trips.sort(key=lambda t: t["t"])
    deduped = []
    for t in trips:
        key = (t["kind"], t["strip"] if t["strip"] is not None else t["region"])
        if deduped and deduped[-1]["kind"] == t["kind"] and \
           (deduped[-1]["strip"] if deduped[-1]["strip"] is not None else deduped[-1]["region"]) == key[1] and \
           t["t"] - deduped[-1]["t"] < 2.0:
            deduped[-1]["changed_bytes"] = max(deduped[-1]["changed_bytes"], t["changed_bytes"])
            continue
        deduped.append(t)
    print(f"[{label}] raw trips={len(trips)} deduped={len(deduped)}", flush=True)
    return deduped


def strip_to_region(page, baseline, strip: int) -> str:
    """Map a strip index to the named DOM region overlapping its vertical band."""
    try:
        vp = baseline["viewport"]
        h = vp["height"] // N_STRIPS
        y = strip * h + h // 2
        # element at strip center, walk up to a landmark
        tag = page.evaluate(
            """(y) => {
                const el = document.elementFromPoint(window.innerWidth/2, y);
                if (!el) return 'main';
                const chain = [el, ...Array.from({length: 5}, (_, i) => null)];
                let n = el, d = 0;
                while (n && d < 6) {
                    const t = (n.tagName || '').toLowerCase();
                    const role = (n.getAttribute && n.getAttribute('role')) || '';
                    if (t === 'header' || role === 'banner') return 'header';
                    if (t === 'nav' || role === 'navigation') return 'nav';
                    if (t === 'footer' || role === 'contentinfo') return 'footer';
                    if (t === 'main' || role === 'main') return 'main';
                    n = n.parentElement; d++;
                }
                return y < window.innerHeight * 0.2 ? 'header' : 'main';
            }""", y)
        return tag if tag in REGION_SELECTORS else "main"
    except Exception:
        return "main"


# -------------------------------------------------------------- classify ----

SYS_PROMPT = (
    "You are a browser sentinel classifier. Given a changed page region "
    "(text/HTML), answer with ONLY a JSON object "
    + CLASSIFY_SCHEMA + ". "
    "cookie-banner: consent/privacy/cookie prompts. modal: popups/paywalls/login walls. "
    "ad: advertising slots. navigation: menus/nav state. noise: timestamps, counters, "
    "live tickers, minor reflows. other: anything else. "
    "disposition: dismiss (blocks content, safe to close), ignore (harmless), "
    "escalate (needs user/agent decision)."
)


def classify_mercury(text: str, html: str) -> dict:
    """Text/HTML classification via Inception mercury-2 (OpenAI-compatible)."""
    from openai import OpenAI
    t0 = time.time()
    try:
        client = OpenAI(base_url="https://api.inceptionlabs.ai/v1",
                        api_key=os.environ["INCEPTION_API_KEY"], timeout=CALL_TIMEOUT)
        content = (f"REGION TEXT:\n{(text or '')[:2500]}\n\nREGION HTML:\n{(html or '')[:2500]}")
        r = client.chat.completions.create(
            model=MERcury_MODEL, temperature=0,
            messages=[{"role": "system", "content": SYS_PROMPT},
                      {"role": "user", "content": content}],
            max_tokens=120, timeout=CALL_TIMEOUT)
        raw = r.choices[0].message.content.strip()
        return {"ok": True, "raw": raw, "parsed": parse_json(raw),
                "latency_s": round(time.time() - t0, 2)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}",
                "latency_s": round(time.time() - t0, 2)}


def classify_inkling(png_path: str, text_hint: str = "") -> dict:
    """Vision classification via inkling:free on OpenRouter with region screenshot."""
    import base64 as b64
    from openai import OpenAI
    t0 = time.time()
    try:
        data = Path(png_path).read_bytes()
        if not data:
            return {"ok": False, "error": "empty-screenshot", "latency_s": 0.0}
        # downscale big strips to keep payload small
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            if im.width > 700:
                im = im.resize((700, int(im.height * 700 / im.width)))
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            data = buf.getvalue()
        except Exception:
            pass
        client = OpenAI(base_url="https://openrouter.ai/api/v1",
                        api_key=os.environ["OPENROUTER_API_KEY"], timeout=CALL_TIMEOUT)
        img_url = "data:image/png;base64," + b64.b64encode(data).decode()
        msgs = [{"role": "system", "content": SYS_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "Classify the changed region in this screenshot. "
                     f"Text hint: {(text_hint or '')[:500]}. Reply ONLY JSON {CLASSIFY_SCHEMA}."},
                    {"type": "image_url", "image_url": {"url": img_url}}]}]
        r = client.chat.completions.create(model=INKLING_MODEL, temperature=0,
                                           messages=msgs, max_tokens=150,
                                           timeout=CALL_TIMEOUT)
        raw = (r.choices[0].message.content or "").strip()
        return {"ok": True, "raw": raw, "parsed": parse_json(raw),
                "latency_s": round(time.time() - t0, 2)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}",
                "latency_s": round(time.time() - t0, 2)}


def parse_json(raw: str):
    m = re.search(r"\{[^}]*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ------------------------------------------------------------------ pages ----

PAGES = {
    "bbc": "https://www.bbc.co.uk",
    "wiki": "https://en.wikipedia.org/wiki/Large_language_model",
    "dynamic": "https://edition.cnn.com",
}


def run_page(pw, label: str, url: str, outdir: Path, duration: float,
             fps: float, do_classify: bool) -> dict:
    from playwright.sync_api import sync_playwright  # noqa (pw passed in)
    ctx = pw.browser.new_context(viewport={"width": 1366, "height": 900},
                                 user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    baseline = capture_baseline(page, label, outdir)
    trips = watch(page, baseline, outdir, duration, fps, label)
    # ground-truth hint: banner-like text present anywhere?
    try:
        body_text = (page.evaluate("() => document.body ? document.body.innerText.slice(0,3000) : ''") or "")
    except Exception:
        body_text = ""
    banner_present = bool(BANNER_HINT_RE.search(body_text))
    result = {"label": label, "url": url, "final_url": page.url,
              "banner_hint_present": banner_present, "trips": trips}
    if do_classify:
        for t in trips:
            t["mercury"] = classify_mercury(t.get("text", ""), t.get("html", ""))
            # vision only for pixel trips (have a screenshot); dom trips get nearest strip shot
            shot = (outdir / f"{label}.trip-p{t['poll']}-strip{t['strip']}.png"
                    if t["strip"] is not None else None)
            if shot is None or not shot.exists():
                # fallback: baseline full screenshot
                shot = outdir / f"{label}.full.png"
            t["inkling"] = classify_inkling(str(shot), t.get("text", ""))
            time.sleep(1)  # be gentle on free-tier rate limits
    page.close()
    ctx.close()
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", default="all", choices=["bbc", "wiki", "dynamic", "all"])
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--fps", type=float, default=2)
    ap.add_argument("--out", default="runs")
    ap.add_argument("--classify", dest="classify", action="store_true", default=True)
    ap.add_argument("--no-classify", dest="classify", action="store_false")
    args = ap.parse_args()
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    labels = ["bbc", "wiki", "dynamic"] if args.page == "all" else [args.page]
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # attach browser to the pw-like namespace run_page expects
        ns = type("NS", (), {"browser": browser})()
        for label in labels:
            try:
                results.append(run_page(ns, label, PAGES[label], outdir,
                                        args.duration, args.fps, args.classify))
            except Exception as e:
                print(f"[{label}] FAILED: {type(e).__name__}: {e}", flush=True)
                results.append({"label": label, "url": PAGES[label], "error": str(e)[:300], "trips": []})
        browser.close()
    (outdir / "trips.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    # console summary
    for r in results:
        trips = r.get("trips", [])
        print(f"== {r['label']}: {len(trips)} trips "
              f"(pixel={sum(1 for t in trips if t['kind']=='pixel')} "
              f"dom={sum(1 for t in trips if t['kind']=='dom')}) "
              f"banner_hint={r.get('banner_hint_present')} url={r.get('final_url', r['url'])[:60]}")
        for t in trips[:12]:
            m = (t.get("mercury") or {}).get("parsed")
            v = (t.get("inkling") or {}).get("parsed")
            print(f"   t={t['t']:5.1f}s {t['kind']:5s} {t['region']:6s} Δ={t['changed_bytes']:6d}B "
                  f"mercury={m} inkling={v}")


if __name__ == "__main__":
    main()
