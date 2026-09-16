# FastBrowse phase 3 — Site Profiles (EasyList for agents)

**Date:** 16 Sep 26  
**Scope:** ProfileStore + Recorder + Matcher + Applier + live demos

---

## What works (measured)

| scenario | result |
|---|---|
| Record first visit (Guardian consent) | ✅ `site=theguardian.com`, `kind=consent`, `selector=#sp_message_container_…` |
| Match on revisit (fresh context) | ✅ `status=KNOWN`, signature match `e75b4dc3…` |
| Match on unknown site | ✅ `status=UNKNOWN` |
| Variant detection (drifted HTML, same structure) | ✅ `status=VARIANT`, suggests re-record |
| Store dedupe + persistence | ✅ same site+sig → hits=2, 1 profile, survives reload |
| Native consent banner demo (injected) | ✅ record `#demo-reject`, match KNOWN on revisit |

---

## What the apply step needs

The `apply()` click works **when the selector is a clickable dismiss button**.
- Guardian banner → button lives **inside an iframe** (`#sp_message_iframe_…`), so the selector is the iframe container, `click_selector` is empty → click doesn't reach the inner button.
- Injected native banner → `#demo-reject` works as `click_selector` → click fires, but test doesn't wire the button to hide the banner (so `verified_gone=False`).
- **Fix:** frame-aware click (`frame.locator(...).click()`) for iframe overlays; or extract the inner button selector during recording.

---

## Profile JSON example (stored in `profiles.json`)

```json
{
  "profiles": [
    {
      "site": "theguardian.com",
      "overlay_signature": "e75b4dc33a668191",
      "overlay_kind": "consent",
      "selector": "#sp_message_container_1482252",
      "action": "dismiss",
      "first_seen": "2026-09-16T10:55:47+0100",
      "last_seen": "2026-09-16T10:55:47+0100",
      "hits": 1,
      "overlay_bytes": 448
    }
  ]
}
```

---

## Savings math (revisit vs first visit)

| metric | first visit (planner) | revisit (profile) |
|---|---|---|
| Observation bytes | ~4 MB full snapshot | ~200 B one-liner |
| LLM tokens | thousands | zero (planner gets one-liner) |
| Latency | 2–5 s | <50 ms |
| Human effort | manual dismiss | zero |

---

## Limitations (honest)

1. **iframe overlays** — Sourcepoint/OneTrust/Quantcast load the real banner inside an iframe; our selector hits the container, but the dismiss button is inside. Need frame-aware click or inner-selector extraction.
2. **A/B variants / signature drift** — class names rotate (`ssrcss-abc123-ConsentBanner` → `ssrcss-def456`). Normalization kills style/class churn, but structural changes (new wrapper, re-ordered buttons) change the signature → `VARIANT` (correctly flags re-record).
3. **Geographic variants** — EU sees consent, US may not. Profiles are per-site; a geo-aware layer would store `region` alongside `site`.
4. **Dynamic lazy overlays** — some banners inject after scroll/timer. Candidate JS runs once at match time; may miss late arrivals.
5. **Selector brittleness** — `#sp_message_container_1482252` has a random ID per session. Recording captures it, but next session gets a new ID → signature matches (normalization kills the ID), but selector fails. Should prefer stable `click_selector` (e.g., `[data-testid="reject-button"]`) or role-based locator.

---

## Files

| file | purpose |
|---|---|
| `phase3/site_profiles.py` | ProfileStore, recorder, matcher, applier, candidate finder |
| `phase3/test_site_profiles.py` | 8 unit tests (all pass) |
| `phase3/RESULTS.md` | this file |

---

## Commits

```
b7c9a2f  phase3: site_profiles.py + tests (store, record, match, apply)
```

---

## Next (phase 4)

Blind-click protocol + staleness tripwire — act from learned map without snapshots, verify by effect, re-profile on mismatch.