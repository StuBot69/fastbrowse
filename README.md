# Fastbrowse — Sentinel Browser

**Vision:** a browser that observes like an agent, not like a screenshot tool.
Today's agents re-snapshot the entire page on every step — megabytes of
accessibility tree + DOM — when usually only one region changed. The Sentinel
Browser flips this: it watches the page continuously (per-region dumb diffs at
2–4 fps), classifies what changed with a small local model, and emits only the
delta the agent needs. Goal: **~4x+ fewer observation bytes** with zero missed
state changes, running locally on commodity hardware with no paid services.

**How we get there:**
1. **Phase 1 — measure (this repo, `phase1/`):** quantify the headroom.
   Capture full AX snapshots + DOM per step on real sites, diff per-region
   (header/nav/main/footer), and score volatility. Proves region-diffing wins
   and identifies chronically-noisy regions (ads, tickers, infinite feeds) the
   sentinel must quarantine.
2. **Phase 2 — sentinel loop:** per-region diff at 2–4fps + small-model
   classifier (changed / noisy / settled) on top of the phase-1 harness
   (`capture_step` / `diff_regions`).
3. **Phase 3 — browser integration:** ship as the observation layer for a
   headless Chromium driving real agent tasks; benchmark task success vs
   observation bytes.

**Repo layout:**
- `phase1/measure.py` — reusable harness (`capture_step`, `diff_regions`,
  `dismiss_banners`, flows: wikipedia / messy / redirects)
- `phase1/RESULTS*.md` — measured numbers, volatility verdicts, redirect rules
- `phase1/snapshots-*` — per-step AX + DOM artifacts (local only, gitignored)

**Reproduce:** `pip install -r requirements.txt && python -m playwright install chromium && cd phase1 && python measure.py --flow wikipedia --out snapshots-wiki`
