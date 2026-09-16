# FastBrowse — final report (night build, 16 Sep 26)

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
| Vision cross-check (nemotron omni) | OK, 9.4s, sensible verdict |

## Phase status
- [x] 1. Region-map + dirty-strip measurement (phase1/, 3 result files)
- [x] 2. Sentinel loop prototype (phase2/sentinel.py + RESULTS.md)
- [ ] 3. Site profiles (EasyList-for-agents) — designed in plan, unbuilt
- [ ] 4. Blind-click protocol + staleness tripwire — designed, unbuilt
- [ ] 5. Learning mode (human demo → profile) — designed, unbuilt
- [ ] 6. Hover physics (pointer-as-state) — designed, unbuilt
- [x] Extras: messy pages, redirects, cyborg art (assets/cyborg-0{1..4}.png)

## Key rules discovered
1. Settle-then-snapshot: never trust mid-chain/mid-load observations.
2. Gate on URL + full-doc bytes first, regions second (empty-region hashes match across document swaps).
3. Bot-wall detection: dom < 20KB or deny-title ⇒ blocked, log and move on.
4. Volatility-gate before classify or dynamic pages drown the classifier.
5. trips.json: rotate per run (500KB write cap truncates).
6. Free vision pools churn nightly — mercury-text first, vision only on escalate.

## Push status
6 local commits. GitHub repo creation blocked on `gh auth login`
(StusAIprojects) — then: `gh repo create StusAIprojects/fastbrowse --public --source=. --push`.
