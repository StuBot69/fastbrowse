"""FastBrowse phase 3 — site profiles (EasyList for agents).

First visit: planner dismisses an overlay manually; Recorder saves
site -> overlay_signature -> disposition. Later visits: Matcher finds the
overlay, hashes its NORMALIZED html, looks up the profile; Applier executes
the recorded action and verifies the overlay is gone, reporting a one-liner.

Usage:
  python site_profiles.py record --url https://www.bbc.co.uk --selector 'button[data-testid="reject-button"]' --action reject
  python site_profiles.py match --url https://www.bbc.co.uk
  python site_profiles.py demo            # full end-to-end (record BBC, revisit, UNKNOWN site)
  python -m pytest test_site_profiles.py -q   # unit tests (no network)

Reusable bits (normalize/sha) are imported from phase1/measure.py where
sensible; overlay normalization is stricter (see normalize_overlay).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "phase1"))
try:
    from measure import normalize as _phase1_normalize, sha as _phase1_sha  # noqa: F401
except Exception:  # phase1 harness absent — fall back to local copies
    _phase1_normalize = None
    _phase1_sha = None

STORE_PATH = HERE / "profiles.json"
OVERLAY_HTML_CAP = 12_000

# ---------------------------------------------------------------------------
# site + normalization + hashing
# ---------------------------------------------------------------------------

_TWO_LEVEL_SUFFIXES = {
    "co.uk", "org.uk", "me.uk", "net.uk", "ltd.uk", "plc.uk",
    "co.jp", "or.jp", "ne.jp", "co.au", "com.au", "net.au", "org.au",
    "co.nz", "co.za", "co.in", "com.br", "co.kr",
}


def etld1(url_or_host: str) -> str:
    """Best-effort eTLD+1 without external deps (handles common 2-level suffixes)."""
    host = url_or_host.split("://", 1)[-1].split("/", 1)[0].split("@")[-1].split(":", 1)[0]
    host = host.lower().strip().lstrip(".")
    if host in ("localhost", "") or re.fullmatch(r"[\d.]+", host or ""):
        return host
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


# Volatile bits stripped before hashing: hashed CSS-module class suffixes
# (ssrcss-abc123-X -> ssrcss-X), style attrs, ids with trailing digits
# (sp_message_container_1482252), data-* tracking attrs, timestamps.
_HASHED_CLASS_RE = re.compile(r"\b([A-Za-z][\w-]*?)-[a-z0-9]{5,8}(-[A-Za-z][\w-]*)?\b")
_STYLE_ATTR_RE = re.compile(r'\sstyle="[^"]*"', re.I)
_ID_DIGITS_RE = re.compile(r'\s(id|for|aria-labelledby|aria-describedby)="([^"]*\D)\d{3,}"', re.I)
_VOLATILE_ATTR_RE = re.compile(
    r'\s(?:data-[a-zA-Z-]*(?:timestamp|time|nonce|session|token|csrf|request-id|'
    r'page-id|revid|build|version|client-id)[a-zA-Z-]*|nonce|csrf-token)="[^"]*"', re.I)
_VOLATILE_TEXT_RE = re.compile(
    r"(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AP]M)?|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}[^\"<]*)")
_WS_RE = re.compile(r"\s+")


def normalize_overlay(html: str) -> str:
    """Normalize overlay HTML so repeat visits hash identically."""
    if _phase1_normalize is not None:
        html = _phase1_normalize(html)
    html = _STYLE_ATTR_RE.sub("", html)
    html = _VOLATILE_ATTR_RE.sub("", html)
    html = _ID_DIGITS_RE.sub(r' \1="\2<N>"', html)
    html = _HASHED_CLASS_RE.sub(r"\1\2", html)
    html = _VOLATILE_TEXT_RE.sub("<TIME>", html)
    return _WS_RE.sub(" ", html).strip()


def sha(s: str) -> str:
    if _phase1_sha is not None:
        return _phase1_sha(s)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def overlay_signature(normalized_html: str) -> str:
    return sha(normalized_html)


# ---------------------------------------------------------------------------
# overlay kind classification
# ---------------------------------------------------------------------------

def classify_kind(text: str, html: str = "") -> str:
    blob = f"{text}\n{html[:2000]}".lower()
    if re.search(r"cookie|consent|gdpr|onetrust|sourcepoint|doNotSell|tracking techn|we value your privacy|accept additional cookies|reject additional", blob):
        return "consent"
    if re.search(r"paywall|metered|unlock (full|unlimited)|continue reading.*subscri|articles? (left|remaining)", blob):
        return "paywall"
    if re.search(r"newsletter|subscribe to (our|the).*newsletter|sign up.*(news|updates)", blob):
        return "newsletter"
    if re.search(r"chat|need help\?|talk to (us|an expert)|live (support|agent)", blob):
        return "chat"
    if re.search(r"install (our|the) app|download.*app|open in app", blob):
        return "app-nag"
    return "modal"


# ---------------------------------------------------------------------------
# ProfileStore
# ---------------------------------------------------------------------------

ACTIONS = ("dismiss", "reject", "accept", "close")


class ProfileStore:
    """JSON-file store of site overlay profiles. Dedupe key: (site, signature)."""

    def __init__(self, path: Path | str = STORE_PATH):
        self.path = Path(path)
        self.profiles: list[dict] = []
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.profiles = data.get("profiles", []) if isinstance(data, dict) else []
            except (json.JSONDecodeError, OSError):
                self.profiles = []
        else:
            self.profiles = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"profiles": self.profiles}, indent=1), encoding="utf-8")

    def find(self, site: str, signature: str) -> dict | None:
        for p in self.profiles:
            if p["site"] == site and p["overlay_signature"] == signature:
                return p
        return None

    def for_site(self, site: str) -> list[dict]:
        return [p for p in self.profiles if p["site"] == site]

    def upsert(self, *, site: str, overlay_signature: str, overlay_kind: str,
               selector: str, action: str, overlay_bytes: int = 0,
               frame_url_pattern: str = "",
               frame_inner_selector: str = "") -> tuple[dict, bool]:
        """Insert or refresh a profile. Returns (profile, created)."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        existing = self.find(site, overlay_signature)
        if existing is not None:
            existing.update(selector=selector, action=action, overlay_kind=overlay_kind,
                            last_seen=now, hits=existing.get("hits", 1) + 1)
            if overlay_bytes:
                existing["overlay_bytes"] = overlay_bytes
            if frame_url_pattern:
                existing["frame_url_pattern"] = frame_url_pattern
            if frame_inner_selector:
                existing["frame_inner_selector"] = frame_inner_selector
            self.save()
            return existing, False
        profile = {
            "site": site, "overlay_signature": overlay_signature,
            "overlay_kind": overlay_kind, "selector": selector, "action": action,
            "first_seen": now, "last_seen": now, "hits": 1,
            "overlay_bytes": overlay_bytes,
            "frame_url_pattern": frame_url_pattern,
            "frame_inner_selector": frame_inner_selector,
        }
        self.profiles.append(profile)
        self.save()
        return profile, True


# ---------------------------------------------------------------------------
# Candidate discovery (page -> overlays)
# ---------------------------------------------------------------------------

CANDIDATE_JS = """() => {
  const seen = new Set(); const out = [];
  const push = (el, why) => {
    if (!el || seen.has(el) || out.length >= 12) return; seen.add(el);
    const r = el.getBoundingClientRect();
    if (r.width < 50 || r.height < 20) return;
    const cs = getComputedStyle(el);
    let sel = '';
    if (el.id) sel = '#' + CSS.escape(el.id);
    else if (el.getAttribute('role')) sel = el.tagName.toLowerCase() + '[role="' + el.getAttribute('role') + '"]';
    else if (el.getAttribute('data-testid')) sel = '[data-testid="' + el.getAttribute('data-testid') + '"]';
    else if (el.className && typeof el.className === 'string' && el.className.trim()) {
      sel = el.tagName.toLowerCase() + '.' + el.className.trim().split(/\\s+/).slice(0,2).map(c=>CSS.escape(c)).join('.');
    } else sel = el.tagName.toLowerCase();
    // Prefer an actionable child (button/link) as the click target hint.
    let clickSel = '';
    const btn = el.querySelector && el.querySelector('button, [role="button"], input[type="button"], input[type="submit"]');
    if (btn) {
      if (btn.getAttribute('data-testid')) clickSel = 'button[data-testid="' + btn.getAttribute('data-testid') + '"]';
      else if (btn.id) clickSel = '#' + CSS.escape(btn.id);
    }
    const vis = (el.checkVisibility ? el.checkVisibility() : (r.width>0 && r.height>0));
    out.push({
      why, selector: sel, click_selector: clickSel,
      tag: el.tagName, z: cs.zIndex, position: cs.position,
      visible: !!vis, w: Math.round(r.width), h: Math.round(r.height),
      text: (el.innerText || '').slice(0, 600),
      html: (el.outerHTML || '').slice(0, 12000),
      iframe_src: (el.tagName === 'IFRAME') ? (el.src || '').slice(0,300) : ''
    });
  };
  const sels = ['#onetrust-banner-sdk','div[id^=sp_message_container]','.fc-consent-root',
    '.cky-consent-container','#qc-cmp2-container','div[id*=onetrust]','div[id*=consent]',
    'div[id*=cookie]','section[aria-labelledby*=consent]','dialog','[role=dialog]','[role=alertdialog]'];
  try { document.querySelectorAll(sels.join(',')).forEach(el => push(el, 'consent-selector')); } catch(e) {}
  try {
    [...document.querySelectorAll('body *')].forEach(el => {
      const cs = getComputedStyle(el);
      if (cs.position !== 'fixed' && cs.position !== 'sticky') return;
      const r = el.getBoundingClientRect();
      if (r.width < 200 || r.height < 50) return;
      const z = parseInt(cs.zIndex || '0', 10);
      if (z < 10 && cs.position !== 'fixed') return;
      push(el, 'fixed-highz');
    });
  } catch(e) {}
  // Consent iframes (e.g. Sourcepoint) carry no innerText — record the shell.
  try {
    [...document.querySelectorAll('iframe')].forEach(el => {
      const src = (el.src || '').toLowerCase();
      if (/consent|sourcepoint|onetrust|privacy|tcf|__tcfapi|sp_|cookie/i.test(src)) push(el, 'consent-iframe');
    });
  } catch(e) {}
  return out;
}"""


def find_candidates(page) -> list[dict]:
    """Return candidate overlay dicts; each gets normalized html + signature + kind."""
    raw = page.evaluate(CANDIDATE_JS)
    cands = []
    for c in raw:
        html = c.get("html") or ""
        norm = normalize_overlay(html[:OVERLAY_HTML_CAP])
        c["normalized_bytes"] = len(norm.encode("utf-8"))
        c["signature"] = overlay_signature(norm)
        c["kind"] = classify_kind(c.get("text", ""), html)
        cands.append(c)
    # Biggest visible first (banners before stray fixed chrome).
    cands.sort(key=lambda c: (not c["visible"], -(c["w"] * c["h"])))
    return cands


def pick_candidate(cands: list[dict], selector: str | None = None) -> dict | None:
    """Pick the candidate a human-handled `selector` belongs to (else best visible)."""
    if not cands:
        return None
    if selector:
        for c in cands:
            html = c.get("html", "")
            if selector in html or (c.get("click_selector") and c["click_selector"] == selector):
                return c
        # selector may live inside the overlay without literal match (CSS-module
        # classes differ) — fall through to best visible.
    for c in cands:
        if c["visible"]:
            return c
    return cands[0]


# ---------------------------------------------------------------------------
# Frame-aware overlay support (phase 4 fix — iframe + shadow DOM banners)
#
# Problem (phase 3 limitation #1): Sourcepoint/OneTrust/Quantcast banners
# render the dismiss button INSIDE an <iframe>; the recorded top-level
# selector hits the container, never the button. Fix: at record time
# descend into overlay iframes and store frame_url_pattern (domain+path,
# never exact URL — session tokens rotate) + frame_inner_selector; at
# apply time match the frame by pattern and click inside it via
# frame.locator(inner). Shadow-DOM banners are handled with ">>>"
# shadow-piercing selectors resolved by JS traversal (Playwright has no
# native >>> support).
# ---------------------------------------------------------------------------

from urllib.parse import urlparse as _urlparse  # noqa: E402

#: Inner dismiss buttons probed inside overlay iframes, most-likely first.
INNER_DISMISS_CANDIDATES = [
    'button:has-text("Reject all")',
    'button:has-text("Reject")',
    'button:has-text("No, thanks")',
    'button:has-text("No thanks")',
    '[aria-label="Close"]',
    '[aria-label="Dismiss"]',
    'button:has-text("Close")',
    'button:has-text("Got it")',
    'button:has-text("Accept")',
    '#sp_message_container_1482252 button',
]

#: JS run inside a frame (or page) that shadow-pierces a ">>>" selector
#: chain and clicks the first match. Returns true on success.
_SHADOW_CLICK_JS = """(sel) => {
  const parts = sel.split('>>>').map(s => s.trim()).filter(Boolean);
  let roots = [document];
  for (const part of parts) {
    const next = [];
    for (const root of roots) {
      const scope = (root.shadowRoot) ? root.shadowRoot : root;
      try { scope.querySelectorAll(part).forEach(el => next.push(el)); } catch(e) {}
      // also descend one shadow level for plain (non->>> ) subtrees
      try {
        scope.querySelectorAll('*').forEach(el => {
          if (el.shadowRoot) {
            try { el.shadowRoot.querySelectorAll(part).forEach(x => next.push(x)); } catch(e2) {}
          }
        });
      } catch(e) {}
    }
    if (!next.length) return 'miss:' + part;
    roots = next;
  }
  const el = roots[0];
  if (el) { el.click(); return 'clicked'; }
  return 'miss';
}"""


def frame_url_pattern(url: str) -> str:
    """Reduce a frame URL to a stable `domain+path` pattern.

    Drops scheme, query, fragment, and trailing numeric session segments
    so per-session tokens never break frame matching.
    """
    try:
        u = _urlparse(url)
    except Exception:
        return url.lower().strip()
    host = (u.hostname or "").lower().strip()
    path = u.path or "/"
    # strip trailing all-digit segments (session ids, timestamps)
    segs = [s for s in path.split("/") if s]
    while segs and re.fullmatch(r"\d{4,}", segs[-1]):
        segs.pop()
    path = "/" + "/".join(segs) if segs else "/"
    return f"{host}{path}"


def frame_matches(pattern: str, url: str) -> bool:
    """True when `url` falls under the stored domain+path `pattern`."""
    if not pattern:
        return False
    try:
        u = _urlparse(url)
    except Exception:
        return False
    host = (u.hostname or "").lower()
    path = u.path or "/"
    if "://" in pattern or pattern.startswith("http"):
        pattern = frame_url_pattern(pattern)
    if "/" in pattern:
        phost, _, ppath = pattern.partition("/")
        ppath = "/" + ppath
    else:
        phost, ppath = pattern, "/"
    if host != phost and not host.endswith("." + phost):
        return False
    # pattern path is a prefix of the live path ("/" matches everything)
    if ppath in ("/", ""):
        return True
    return path == ppath or path.startswith(ppath.rstrip("/") + "/")


def split_shadow_selector(selector: str) -> list[str]:
    """Split a `a >>> b >>> c` shadow-piercing chain into parts."""
    return [p.strip() for p in selector.split(">>>") if p.strip()]


def _iter_frames(page) -> list:
    """Return live frame objects for real or fake pages (never raises)."""
    try:
        if hasattr(page, "frames"):
            frames = page.frames  # real Playwright page: property -> list
            if callable(frames):
                frames = frames()
            return list(frames)
    except Exception:
        pass
    try:
        if hasattr(page, "_frames"):
            return list(page._frames)
    except Exception:
        pass
    return []


def _frame_url(frame) -> str:
    try:
        url = frame.url
        return url() if callable(url) else (url or "")
    except Exception:
        return ""


def find_frame(page, pattern: str):
    """First live frame whose URL matches `pattern` (domain+path)."""
    for fr in _iter_frames(page):
        if frame_matches(pattern, _frame_url(fr)):
            return fr
    return None


def _locator_count(loc) -> int:
    try:
        c = loc.count
        return c() if callable(c) else int(c)
    except Exception:
        return 1  # fake locators without count(): assume present


def probe_frame_button(frame, candidates: list[str] | None = None) -> str | None:
    """First inner-dismiss selector that resolves inside `frame`."""
    for sel in (candidates or INNER_DISMISS_CANDIDATES):
        try:
            loc = frame.locator(sel)
            if _locator_count(loc) > 0:
                return sel
        except Exception:
            continue
    return None


def shadow_click(target, selector: str, timeout: int = 5000) -> bool:
    """Click `selector` on a page-or-frame, piercing `>>>` shadow chains.

    Plain selectors go through locator().click(); shadow chains go through
    JS shadow traversal + click. Returns True on success, raises on miss
    when the target exposes no JS evaluate path.
    """
    if ">>>" not in selector:
        target.locator(selector).click(timeout=timeout)
        return True
    # shadow chain: prefer in-frame JS traversal (works on real + fake)
    if hasattr(target, "evaluate"):
        try:
            res = target.evaluate(_SHADOW_CLICK_JS, selector)
            if isinstance(res, str) and res == "clicked":
                return True
            raise RuntimeError(f"shadow selector missed: {res}")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"shadow click failed: {e}")
    # fake without evaluate: drill locator chain manually
    loc = None
    for part in split_shadow_selector(selector):
        loc = target.locator(part) if loc is None else loc.locator(part)
    loc.click(timeout=timeout)
    return True


def record_frame_binding(page, candidate: dict,
                         candidates: list[str] | None = None) -> dict:
    """Descend into the overlay iframe for `candidate`; return binding.

    Returns {frame_url_pattern, frame_inner_selector} (empty strings when
    the overlay is not iframe-hosted or no inner button resolves).
    """
    if not candidate or candidate.get("tag") != "IFRAME":
        if (candidate or {}).get("why") != "consent-iframe":
            return {"frame_url_pattern": "", "frame_inner_selector": ""}
    src = (candidate or {}).get("iframe_src", "") or ""
    frames = _iter_frames(page)
    # Prefer the frame whose live URL sits under the shell src's domain+path;
    # fall back to probing every frame for a dismiss button.
    ordered: list = []
    if src:
        pat = frame_url_pattern(src)
        ordered = [fr for fr in frames if frame_matches(pat, _frame_url(fr))]
        ordered += [fr for fr in frames if fr not in ordered]
    else:
        ordered = list(frames)
    pattern_hint = frame_url_pattern(src) if src else ""
    for fr in ordered:
        inner = probe_frame_button(fr, candidates)
        if inner:
            return {"frame_url_pattern": frame_url_pattern(_frame_url(fr)) or pattern_hint,
                    "frame_inner_selector": inner}
    return {"frame_url_pattern": pattern_hint, "frame_inner_selector": ""}


# ---------------------------------------------------------------------------
# Recorder / Matcher / Applier
# ---------------------------------------------------------------------------

def record(page, selector: str, action: str, store: ProfileStore | None = None,
           overlay_html: str | None = None, kind: str | None = None,
           frame_url_pattern: str | None = None,
           frame_inner_selector: str | None = None) -> dict:
    """Save/update the profile for the overlay `selector`+`action` just dismissed.

    If the overlay lives inside an iframe, descend at record time and store
    frame_url_pattern (domain+path) + frame_inner_selector so apply() can
    click inside the frame. Explicit frame_* args override auto-detection.
    Returns the stored profile dict.
    """
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}, got {action!r}")
    store = store or ProfileStore()
    site = etld1(page.url)
    binding = {"frame_url_pattern": frame_url_pattern or "",
               "frame_inner_selector": frame_inner_selector or ""}
    if overlay_html is None:
        cand = pick_candidate(find_candidates(page), selector)
        overlay_html = ((cand or {}).get("html") or "")
        kind = kind or ((cand or {}).get("kind") or "modal")
        if frame_url_pattern is None and frame_inner_selector is None:
            try:
                binding = record_frame_binding(page, cand or {})
            except Exception:
                binding = {"frame_url_pattern": "", "frame_inner_selector": ""}
    kind = kind or classify_kind("", overlay_html or "")
    norm = normalize_overlay(overlay_html[:OVERLAY_HTML_CAP])
    sig = overlay_signature(norm)
    profile, _ = store.upsert(site=site, overlay_signature=sig, overlay_kind=kind,
                              selector=selector, action=action,
                              overlay_bytes=len(overlay_html.encode("utf-8")),
                              frame_url_pattern=binding.get("frame_url_pattern", ""),
                              frame_inner_selector=binding.get("frame_inner_selector", ""))
    return profile


def match(page, store: ProfileStore | None = None) -> dict:
    """Look up candidate overlays in the store.

    Returns {status, candidate, profile, ...} where status is KNOWN,
    VARIANT (same site+kind, drifted signature) or UNKNOWN.
    """
    store = store or ProfileStore()
    site = etld1(page.url)
    cands = find_candidates(page)
    if not cands:
        return {"status": "UNKNOWN", "site": site, "candidate": None,
                "profile": None, "reason": "no-overlay-found"}
    site_profiles = store.for_site(site)
    for c in cands:
        if not c["visible"]:
            continue
        hit = store.find(site, c["signature"])
        if hit:
            return {"status": "KNOWN", "site": site, "candidate": c, "profile": hit}
    # Same-site, same-kind near miss -> likely signature drift / A/B variant.
    for c in cands:
        if not c["visible"]:
            continue
        same_kind = [p for p in site_profiles if p["overlay_kind"] == c["kind"]]
        if same_kind:
            return {"status": "VARIANT", "site": site, "candidate": c,
                    "profile": None, "suggestion": max(same_kind, key=lambda p: p["hits"]),
                    "reason": "same-site-same-kind-signature-drift"}
    best = next((c for c in cands if c["visible"]), cands[0])
    return {"status": "UNKNOWN", "site": site, "candidate": best,
            "profile": None, "reason": "no-profile-for-site"}


def planner_cost_bytes(page) -> dict:
    """Bytes a planner-style full observation would need (AX snapshot + DOM)."""
    try:
        cdp = page.context.new_cdp_session(page)
        ax = cdp.send("Accessibility.getFullAXTree")
        ax_bytes = len(json.dumps(ax, ensure_ascii=False).encode("utf-8"))
    except Exception:
        ax_bytes = 0
    try:
        dom_bytes = len(page.content().encode("utf-8"))
    except Exception:
        dom_bytes = 0
    return {"ax_bytes": ax_bytes, "dom_bytes": dom_bytes,
            "total": ax_bytes + dom_bytes}


def _click_target(page, selector: str, timeout: int = 5000) -> None:
    """Click `selector` on the page, piercing `>>>` shadow chains."""
    if ">>>" in selector:
        shadow_click(page, selector, timeout=timeout)
        return
    loc = getattr(page, "locator", None)
    if callable(loc):
        loc(selector).click(timeout=timeout)
        return
    page.click(selector, timeout=timeout)  # type: ignore[attr-defined]


def apply(page, disposition: dict) -> dict:
    """Execute the recorded action, verify the overlay is gone, report one line.

    Frame-aware: when the profile carries frame_url_pattern +
    frame_inner_selector, match the live frame by domain+path (never exact
    URL) and click the inner selector via frame.locator. `>>>`
    shadow-piercing selectors work on both frame and page targets.
    Returns {handled, site, kind, selector, action, oneliner, ...}.
    """
    profile = disposition.get("profile")
    site = disposition.get("site", etld1(page.url))
    if disposition.get("status") != "KNOWN" or not profile:
        reason = disposition.get("reason", "unknown-overlay")
        oneliner = f"unknown overlay on {site} ({reason}) — planner needed"
        return {"handled": False, "site": site, "kind": (disposition.get("candidate") or {}).get("kind", "?"),
                "oneliner": oneliner, "reason": reason}
    selector, action, kind = profile["selector"], profile["action"], profile["overlay_kind"]
    frame_pat = profile.get("frame_url_pattern", "") or ""
    frame_inner = profile.get("frame_inner_selector", "") or ""
    before = pick_candidate(find_candidates(page))
    clicked_via = "page"
    try:
        if frame_pat and frame_inner:
            fr = find_frame(page, frame_pat)
            if fr is None:
                raise RuntimeError(f"no live frame matches {frame_pat}")
            shadow_click(fr, frame_inner, timeout=5000)
            clicked_via = f"frame:{frame_pat}"
        else:
            _click_target(page, selector, timeout=5000)
        try:
            page.wait_for_timeout(900)
        except Exception:
            pass
    except Exception as e:
        # frame path failed but a plain page selector exists -> one fallback
        if clicked_via.startswith("frame:"):
            try:
                _click_target(page, selector, timeout=5000)
                clicked_via = "page-fallback"
                try:
                    page.wait_for_timeout(900)
                except Exception:
                    pass
            except Exception as e2:
                oneliner = f"FAILED {site} {kind} ({action} {selector}): {str(e2)[:100]}"
                return {"handled": False, "site": site, "kind": kind, "selector": selector,
                        "action": action, "oneliner": oneliner, "error": str(e2)[:200],
                        "clicked_via": clicked_via, "frame_error": str(e)[:200]}
        else:
            oneliner = f"FAILED {site} {kind} ({action} {selector}): {str(e)[:100]}"
            return {"handled": False, "site": site, "kind": kind, "selector": selector,
                    "action": action, "oneliner": oneliner, "error": str(e)[:200],
                    "clicked_via": clicked_via}
    after = find_candidates(page)
    gone = not any(c["visible"] and c["signature"] == (before or {}).get("signature") for c in after)
    if not gone and before:
        gone = not any(c["visible"] and c["kind"] == kind and c["w"] * c["h"] >= before["w"] * before["h"] // 2
                       for c in after)
    oneliner = f"handled {site} {kind} ({action} {selector})"
    return {"handled": bool(gone), "site": site, "kind": kind, "selector": selector,
            "action": action, "oneliner": oneliner, "verified_gone": bool(gone),
            "clicked_via": clicked_via}


# ---------------------------------------------------------------------------
# CLI + end-to-end demo
# ---------------------------------------------------------------------------

def _fresh_page(pw, browser, url: str, wait_ms: int = 4000):
    ctx = browser.new_context(viewport={"width": 1366, "height": 900})
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(wait_ms)
    return ctx, page


def cmd_demo(out: Path | None = None) -> dict:
    from playwright.sync_api import sync_playwright

    store = ProfileStore()
    results: dict = {"steps": []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # Step 1 — first visit (BBC, fresh context): planner observes + rejects.
        ctx1, page1 = _fresh_page(pw, browser, "https://www.bbc.co.uk")
        try:
            cost1 = planner_cost_bytes(page1)
            cands = find_candidates(page1)
            vis = [c for c in cands if c["visible"]]
            sel = 'button[data-testid="reject-button"]'
            el = page1.query_selector(sel)
            if el and el.is_visible() and vis:
                page1.click(sel, timeout=5000)
                page1.wait_for_timeout(900)
                profile = record(page1, selector=sel, action="reject", store=store,
                                 overlay_html=vis[0]["html"], kind=vis[0]["kind"])
                dismissed = True
            else:
                profile, dismissed = None, False
            results["steps"].append({
                "step": "record-bbc-first-visit",
                "candidate_kinds": [c["kind"] for c in vis],
                "planner_cost": cost1, "dismissed": dismissed, "profile": profile,
            })
        finally:
            ctx1.close()

        # Step 2 — revisit (new clean context): match + auto-dispose, no AX/DOM read.
        ctx2, page2 = _fresh_page(pw, browser, "https://www.bbc.co.uk")
        try:
            disp = match(page2, store)
            res = apply(page2, disp) if disp["status"] == "KNOWN" else apply(page2, disp)
            revisit_cost = len(res["oneliner"].encode("utf-8"))
            first_total = results["steps"][0]["planner_cost"]["total"]
            results["steps"].append({
                "step": "revisit-bbc-auto-dispose",
                "match_status": disp["status"], "apply": res,
                "revisit_cost_bytes": revisit_cost,
                "saving_vs_first_visit": first_total - revisit_cost,
                "saving_ratio": round(first_total / max(revisit_cost, 1), 1),
            })
        finally:
            ctx2.close()

        # Step 3 — UNKNOWN path: site with no profile.
        ctx3, page3 = _fresh_page(pw, browser, "https://example.com")
        try:
            disp3 = match(page3, store)
            res3 = apply(page3, disp3)
            results["steps"].append({
                "step": "unknown-site-example",
                "match_status": disp3["status"], "apply": res3,
            })
        finally:
            ctx3.close()
        browser.close()

    results["profiles"] = store.profiles
    if out:
        out.write_text(json.dumps(results, indent=1)[:200_000], encoding="utf-8")
    # Console one-liners (this is the whole point: revisit costs one line).
    for s in results["steps"]:
        if "apply" in s:
            print(f"[{s['step']}] match={s['match_status']} :: {s['apply']['oneliner']}", flush=True)
        else:
            print(f"[{s['step']}] planner_cost={s['planner_cost']['total']} "
                  f"dismissed={s['dismissed']} kinds={s['candidate_kinds']}", flush=True)
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FastBrowse phase 3 site profiles")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="record a dismissal as a profile")
    r.add_argument("--url", required=True)
    r.add_argument("--selector", required=True)
    r.add_argument("--action", required=True, choices=ACTIONS)
    r.add_argument("--store", default=str(STORE_PATH))
    m = sub.add_parser("match", help="match current overlays against profiles")
    m.add_argument("--url", required=True)
    m.add_argument("--store", default=str(STORE_PATH))
    d = sub.add_parser("demo", help="end-to-end demo (record BBC, revisit, UNKNOWN)")
    d.add_argument("--out", default=str(HERE / "demo_results.json"))
    a = ap.parse_args(argv)

    if a.cmd == "demo":
        cmd_demo(Path(a.out))
        return 0

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx, page = _fresh_page(pw, browser, a.url)
        try:
            store = ProfileStore(a.store)
            if a.cmd == "record":
                page.click(a.selector, timeout=5000)
                page.wait_for_timeout(900)
                prof = record(page, selector=a.selector, action=a.action, store=store)
                print(json.dumps(prof, indent=1))
            elif a.cmd == "match":
                disp = match(page, store)
                print(json.dumps({k: (v if k != "candidate" else
                                      {kk: (vv[:500] if kk == "html" else vv) for kk, vv in v.items()} if v else None)
                                  for k, v in disp.items() if k != "profile"},
                                 indent=1, default=str))
                if disp.get("profile"):
                    print(json.dumps(disp["profile"], indent=1))
        finally:
            ctx.close()
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
