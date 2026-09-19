# Sentinel phase-2 RESULTS (16 Sep 26, morning BST)

Prototype: `sentinel.py` — baseline region map → dumb per-strip pixel/DOM
trigger @2fps → dual classifier (Inception mercury-2 on region TEXT/HTML,
vision model on region screenshot).

## Trigger runs (30s @ 2fps, 60 polls each)

| page | raw trips | deduped | notes |
|---|---|---|---|
| bbc.co.uk (first visit, banner present) | 5 | 5 | pixel=1 dom=4, banner_hint=True |
| wikipedia article (static control) | 1 | 1 | single early pixel trip (load settle), then silent |
| dynamic page | 116 | 112 | near-every-poll firing — the volatility case |

Trigger verdict: clean separation. Static ≈ silent, banner page = handful of
trips, dynamic = storm. Dedup works; dynamic pages need volatility scoring
(phase-1 messy rules) BEFORE classification or the classifier drowns.

## Classifier: mercury-2 (text-only, Inception free tier)

71 trips classified. Sampled parsed verdicts: modal/dismiss x4, other/ignore
x3, navigation/ignore x2, ad/dismiss x2. Latency ~4s per call. Verdict:
**text-only suffices for the common cases** — cookie banners, modals and ads
are all identifiable from region HTML/text without a single pixel.

## Classifier: vision cross-check (OpenRouter free tier)

Rough night for free vision pools:
- thinkingmachines/inkling:free → 403 (restricted to agentic harnesses)
- google/gemma-3-4b-it:free, qwen/qwen2.5-vl-72b-instruct:free → retired (404)
- nex-agi pool → 503 earlier in the night
- **nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free → OK, 9.4s**,
  `{"type":"navigation","disposition":"ignore"}` on the BBC screenshot —
  sensible (news homepage, no blocking overlay in frame).

## Recommended sentinel architecture
1. Dumb per-strip diff trigger (CPU pennies) + volatility gate.
2. mercury-2 text classifier first (~4s, generous free tier).
3. Vision (nemotron omni or successor) ONLY on escalate/ambiguous.
4. trips.json capped at 500KB per write — rotate/compact per run (bit us:
   file truncated mid-run, salvageable records only).

## Local vision
ollama absent on this machine — skipped per instructions. Fits a local
11GB GPU later (7B VL quants).
