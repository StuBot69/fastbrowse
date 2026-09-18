#!/usr/bin/env python3
"""fb — FastBrowse single entry point. One command shape for humans,
harness bots, and free-model workers.

  fb hunt --ask "white-haired female cyborg" [--site pixabay] [--out NAME]
      Eyes-first grid hunt + download + vision receipt. Writes
      phase5/runs/<NAME>/run-report.json. Exits 0 on download, 1 on
      no-match/bot-wall, 2 on vision NO (file fetched but wrong subject).
  fb replay --tape runs/<NAME> --href <photo-url> [--ask ...]
      Blind replay of a LEARNED href (no hunt, hybrid gate only).
  fb verify <file> --ask "..."
      Eyes on a file via the vision endpoint. Prints YES/NO + sentence.
  fb sites
      Learned site knowledge (sites/*.json): selectors, last_verified,
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
    elif site == "commons":
        cmd = [sys.executable, str(PHASE5 / "sydney_suite.py"),
               "--run", "1", "--out", str(out)]
    else:
        print(f"fb hunt: unknown site {site!r} (pixabay|commons)", file=sys.stderr)
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
    for fp in sorted(fb_config.SITES_DIR.glob("*.json")):
        try:
            d = json.loads(fp.read_text())
        except Exception:
            continue
        stale = "STALE " + d.get("stale_reason", "") if d.get("needs_relearn") else "ok"
        print(f"{fp.stem}: {len(d.get('selectors', []))} selectors, "
              f"verified {d.get('last_verified', '?')} [{stale}]")
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


def main() -> None:
    ap = argparse.ArgumentParser(prog="fb", description="FastBrowse entry point")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hunt", help="eyes-first hunt + download + receipt")
    h.add_argument("--ask", required=True, help="what to find (search terms)")
    h.add_argument("--site", default="pixabay", help="pixabay|commons")
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
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
