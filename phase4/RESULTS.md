# FastBrowse phase 4 — RESULTS (blind-click + tripwire, Camoufox)

Target: starwars.fandom.com/wiki/Luke_Skywalker under Camoufox
(Cloudflare challenge cleared, 3.3MB page, 11 overlay candidates, ad churn).
First-visit planner cost: **3,273,145 bytes**.

## Blind revisit

| action | gate | outcome | effect | planner bytes |
|---|---|---|---|---|
| dispose-overlays | pass sim=1.000 | OK-BLIND | overlay gone | 0 |
| open-nav-link | pass sim=1.000 | OK-BLIND | url=.../Luke_Skywalker/Legends | 0 |

**Totals: 0 strikes, 0 planner bytes, 0 snapshots, 2/2 blind actions.**
Revisit cost: one ~200B line ("handled fandom.com consent ...") vs 3.3MB.

## Design fixes the target forced (all in `blind.py`)
1. **Exact-match gate is wrong for the web.** Reload drift is ~0.02% but
   nonzero; Jaccard similarity (threshold 0.95) passes real revisits and
   still halts on genuine change.
2. **Gate on TEXT, not markup.** Ad slots randomize HTML per load
   (html-line Jaccard 0.71) while visible text is identical (1.0).
3. **Rebase after own success.** Disposal changes the page — refresh the
   baseline or the gate trips on your own work.
4. **Record ONE banner with a UNIQUE button.** Recording page furniture
   (sidebars, header backgrounds) as overlays caused strict-violation
   timeouts. Walk candidates until one yields a count==1 visible button.
5. **Map carries destinations.** Blind nav clicks a learned href
   (`a[href='...']` + first), never a bare prefix selector (4291 matches,
   self-links).
6. **Camoufox clears Cloudflare** where headless Chromium gets
   "Just a moment...". Engine flag: `--engine camofox|chromium`.

## Tripwire log (from the failing runs, kept as proof)
- sim=0.000 MISMATCH → HALT-SNAPSHOT → ABORT-REPROFILE: correct halts on
  challenge-page vs article mismatch (different documents).
- sim=1.000 pass with failed effect → retry-with-snapshot, strike counting,
  abort at 2: exercised and behaving per spec.

## Frame-aware applier (phase3 fix, committed d0def0e)
Iframe + `>>>` shadow-DOM clicking with locator→click fallback.
8/8 phase-3 unit tests pass.

## Limitations
- One page, one engine, one run. Fandom's layout is the exam, not the syllabus.
- Gate threshold 0.95 and text-length floor (>12 chars) are tuned constants,
  not learned. Volatility scoring (phase-1 messy rules) should set them per region.
- Challenge waiting is fixed-time; a proper sentinel would watch for it.
- Human-mouse path in blind runner is wired but untested live (headless run).

## Files
- `phase4/blind.py` — runner, gate, tripwire, fandom demo (`--engine`)
- `phase3/site_profiles.py` — frame-aware applier fix
