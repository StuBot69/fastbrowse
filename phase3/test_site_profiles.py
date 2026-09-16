"""Unit tests for phase3 site_profiles (no network — fake pages)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "phase1"))

from site_profiles import (  # noqa: E402
    ProfileStore, classify_kind, etld1, match, normalize_overlay,
    overlay_signature, pick_candidate, record, apply,
)

CONSENT_HTML = (
    '<section aria-labelledby="consent-banner-title" class="ssrcss-abc123-ConsentBanner" '
    'style="z-index:2500"><h2>Cookies on the BBC website</h2>'
    '<button data-testid="reject-button">Reject additional cookies</button></section>'
)
CONSENT_HTML_DRIFTED = (
    '<section aria-labelledby="consent-banner-title" class="ssrcss-def456-ConsentBanner">'
    '<h2>Cookies on the BBC website</h2>'
    '<button data-testid="reject-button">Reject additional cookies</button></section>'
)


class FakePage:
    def __init__(self, url, candidates=None):
        self.url = url
        self._cands = candidates or []
        self.clicked = []

    def evaluate(self, _js):
        return self._cands

    def click(self, selector, timeout=None):
        self.clicked.append(selector)

    def wait_for_timeout(self, _ms):
        pass


def _cand(html, kind="consent", visible=True, selector="section", sig_holder=None):
    return {"why": "test", "selector": selector, "click_selector": "",
            "tag": "SECTION", "z": "2500", "position": "fixed",
            "visible": visible, "w": 1300, "h": 200,
            "text": "Cookies on the BBC website", "html": html}


def test_etld1():
    assert etld1("https://www.bbc.co.uk/news") == "bbc.co.uk"
    assert etld1("https://sub.example.com/x") == "example.com"
    assert etld1("https://www.theguardian.com/uk") == "theguardian.com"


def test_normalize_kills_drift():
    a = normalize_overlay(CONSENT_HTML)
    b = normalize_overlay(CONSENT_HTML_DRIFTED)
    assert overlay_signature(a) == overlay_signature(b)  # hashed-class + style drift absorbed


def test_signature_changes_on_real_content_change():
    a = overlay_signature(normalize_overlay(CONSENT_HTML))
    b = overlay_signature(normalize_overlay(CONSENT_HTML.replace("Reject", "Accept")))
    assert a != b


def test_classify_kind():
    assert classify_kind("We use cookies, accept additional cookies") == "consent"
    assert classify_kind("Subscribe to continue reading, paywall") == "paywall"
    assert classify_kind("Sign up for our newsletter") == "newsletter"
    assert classify_kind("Random promo modal!!!") == "modal"


def test_store_dedupe_and_hits(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    p1, created1 = store.upsert(site="bbc.co.uk", overlay_signature="abc",
                                overlay_kind="consent", selector="s", action="reject")
    p2, created2 = store.upsert(site="bbc.co.uk", overlay_signature="abc",
                                overlay_kind="consent", selector="s", action="reject")
    assert created1 and not created2
    assert p2["hits"] == 2 and len(store.profiles) == 1
    # persists
    store2 = ProfileStore(tmp_path / "profiles.json")
    assert store2.find("bbc.co.uk", "abc")["hits"] == 2


def test_record_match_apply_known(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    page = FakePage("https://www.bbc.co.uk/", [_cand(CONSENT_HTML)])
    prof = record(page, selector='button[data-testid="reject-button"]',
                  action="reject", store=store)
    assert prof["site"] == "bbc.co.uk" and prof["action"] == "reject"
    # revisit: overlay gone after click -> need page whose candidates vanish
    live = FakePage("https://www.bbc.co.uk/", [_cand(CONSENT_HTML)])
    disp = match(live, store)
    assert disp["status"] == "KNOWN"
    gone = FakePage("https://www.bbc.co.uk/", [])
    # apply clicks on live page; verify against post-click state by swapping
    res = apply(live, disp)
    live._cands = []  # overlay dismissed for real
    res2 = apply(gone, disp)
    assert res["oneliner"].startswith("handled bbc.co.uk consent")
    assert res2["handled"] is True


def test_match_unknown_and_variant(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    store.upsert(site="bbc.co.uk", overlay_signature="deadbeef",
                 overlay_kind="consent", selector="s", action="reject")
    # unrelated site -> UNKNOWN, no profile
    disp = match(FakePage("https://example.com/", [_cand("<div>promo</div>", kind="modal")]), store)
    assert disp["status"] == "UNKNOWN" and disp["profile"] is None
    # same site, same kind, drifted sig -> VARIANT with suggestion
    other = CONSENT_HTML.replace("Cookies on the BBC website", "Cookies on BBC iPlayer")
    disp2 = match(FakePage("https://www.bbc.co.uk/", [_cand(other)]), store)
    assert disp2["status"] == "VARIANT" and disp2["suggestion"]["overlay_signature"] == "deadbeef"
    # UNKNOWN apply reports one line, handled=False
    res = apply(FakePage("https://example.com/", [_cand("<div>x</div>")]), disp)
    assert res["handled"] is False and res["oneliner"].startswith("unknown overlay on example.com")


def test_pick_candidate_prefers_visible():
    cands = [_cand(CONSENT_HTML, visible=False),
             _cand(CONSENT_HTML_DRIFTED, visible=True)]
    # normalize first (match() path does this)
    for c in cands:
        n = normalize_overlay(c["html"])
        c["signature"] = overlay_signature(n)
        c["normalized_bytes"] = len(n)
    assert pick_candidate(cands)["visible"] is True
