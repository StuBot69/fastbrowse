# FastBrowse — final report (all phases, 17 Sep 26)

Stu's idea: planner + sentinel split for AI browser agents — full snapshots
only where knowledge is missing, cheap watchers everywhere else.

## Headline numbers (measured, not projected)

| experiment | result |
|---|---|
| Phase 1, Wikipedia flow (5 steps) | **9.16x** fewer bytes via changed-regions (hypothesis was 4x) |
| Same-page revisit | **160x** (main byte-identical, chrome cached) |
| Redirect chains | **6.33x**; mid-chain snapshots worthless, settle-then-snapshot rule |
| Messy page (Guardian, 8 captures) | 0.00 volatility all regions; scroll added 90B on 1.5MB |
| Sentinel trigger (30s @2fps) | wiki 1 trip, BBC-banner 5, dynamic 112 — clean separation |
| mercury-2 text classifier | 71/71 trips classified, ~4s, free tier |
| Phase 4 fandom (Camoufox) | first visit 3,273,145 planner bytes → blind revisit **0**, 0 strikes |
| Phase 6 hover fixture | 3/3 menus (simple, nested submenu, intent-delay) via chain-walk + precise mode |

## Phase status (all shipped)
- [x] 1. Region-map + dirty-strip measurement (phase1/, 3 result files)
- [x] 2. Sentinel loop prototype (phase2/sentinel.py + RESULTS.md)
- [x] 3. Site profiles — store, recorder, matcher, applier; frame-aware
      (iframe + shadow-DOM) applier (phase3/, 8/8 unit tests green)
- [x] 4. Blind-click + staleness tripwire (phase4/blind.py) — Jaccard 0.95
      gate on text lines; single-banner recording; effect verification;
      2-strike reprofile. Fandom run: dispose + nav, zero snapshots.
- [x] 5. Learning mode (phase5/learn.py) — headed Camoufox persistent
      context, human drives; recorder captures clicks/trail/hovers/navs;
      Python-side tape mirror survives cross-origin navs; window-close
      saves partial tape. Live demo (Stu): nav captured, 39 trail pts,
      4 hover anchors with dwell.
- [x] 6. Hover physics (phase6/hover.py) — pointer-is-state; anchor chain
      walk, precise steady-hand mode, deepest-menu-wins recording, gap
      analysis (dead gaps reported NO-SAFE-PATH, no flailing).

## Key rules discovered
1. Settle-then-snapshot: never trust mid-chain/mid-load observations.
2. Gate on URL + full-doc bytes first, regions second (empty-region hashes match across document swaps).
3. Bot-wall detection: dom < 20KB or deny-title ⇒ blocked, log and move on.
4. Gate on TEXT lines, not markup: ads randomize HTML (0.71) while text is identical (1.0).
5. Record ONE banner with a count==1 visible button — page furniture is not an overlay.
6. An exact-match overlay gate breaks on 0.02% per-load drift — Jaccard ≥0.95 or bust.
7. Trail replay beats path computation: the human's proven pointer trail crosses gaps by construction.
8. Clicks that cause navigation die with their document — mirror the tape Python-side every poll.
9. Router links to self are not navigation — learned action maps must carry the destination href.
10. A 12px dead gap kills ANY pointer (human included) — verify hover reachability with elementFromPoint.
11. Pixabay download dialog: the Download button sits under an `overlay--PRoff`
    div that intercepts pointer events — normal Playwright click times out;
    `force=True` sails through. Same hashed-class overlay family as the rest.
12. Record the REC badge as a pinned `div#__fb_badge` selector — a
    hashed-class-stripping recorder will otherwise log it as `html > div`
    and the replay runner will try to click "Finish & Save" as a real step.
13. Camoufox persistent contexts accept downloads by default; a bare
    `page.on("download")` handler + `save_as` into the run dir is enough —
    no `accept_downloads` plumbing needed. Log the file + source URL in profile.json.
14. Cloudflare ("Just a moment...") kills vanilla Chromium headless dead
    (0 links, challenge page only) — Camoufox walks straight through.
    Engine choice IS the bot-wall strategy on hardened sites.
15. Pixabay Original races download+nav to /get/: the click call dies
    with a nav error (EXPECTED) while the download event already fired.
    Fire-and-forget clicks (JS .click(), ignore errors), poll the handler
    for a >50KB file; never expect_download, never re-GET the /get/ URL.
16. Alt text lies ("black and white" photo style matched "white hair"):
    never pick results by alt/slug scoring — show numbered tiles to eyes
    first, download only what eyes verify.

## Site knowledge
- Guardian AI topic page: /technology/artificialintelligenceai (NOT /ai or /artificialintelligence; on-site /search dead from here). Freshest links via /<topic>/2026/sep/<day>/all.
- Guardian consent banner: OneTrust, #onetrust-reject-all-handler / accept — profiled in phase3.
- Pixabay: search `/images/search/<terms>/`; photo pages `/photos|illustrations|vectors/<slug>-<id>/`.
  Photo links are stable `a[href]` (no hash) — record href, click by href. The
  "Free download" button itself has NO stable attrs (positional selector only).
  17 Sep 26: blind download flow green — search → random photo → OneTrust
  dismiss → Free download → dialog Download (force-click) → file saved.

## Repo
github.com/StuBot69/fastbrowse — all phases + REPORT (this file), tree clean.
