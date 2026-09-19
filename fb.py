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
import subprocess
import sys
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
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
