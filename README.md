# FastBrowse — eyes-first browser automation that sips tokens

A browser loop that **never sends DOM to an LLM**. Full snapshots only
where knowledge is missing, cheap watchers everywhere else. Measured:
9.16x fewer bytes on a Wikipedia flow, 160x on same-page revisit, **0
planner bytes** on blind replays. The browser loop makes **zero metered
LLM calls** — the only "tokens" are the ones you never spend.

## Quick start (any bot, any box)

```bash
git clone https://github.com/StuBot69/fastbrowse.git && cd fastbrowse
./setup.sh                    # venv + playwright/camoufox/pillow + chromium
.venv/bin/python fb.py rules  # the 16 rules — read first
.venv/bin/python fb.py hunt --ask "female cyborg" --site pixabay --out runs/demo
```

That hunts Pixabay's grid with eyes, downloads the verified pick, and
writes `phase5/runs/demo/run-report.json` (times, bytes, strikes, vision
verdict, token log). Exit 0 = downloaded, 1 = no match/bot-wall,
2 = downloaded but eyes say wrong subject.

## Commands (`fb.py` — the only interface you need)

| command | what |
|---|---|
| `fb hunt --ask "..." [--site pixabay\|commons] [--verify "..."] [--out runs/NAME]` | eyes-first hunt + download + vision receipt |
| `fb replay --tape runs/NAME --href <url> [--ask ...]` | blind replay of a learned href (no hunt, hybrid gate) |
| `fb verify <file> --ask "..."` | eyes on a file: YES/NO + sentence |
| `fb sites` | learned site knowledge + stale flags |
| `fb rules` | the 16 hard-won rules |

## Vision (optional, degrades gracefully)

Eyes-first hunting needs an OpenAI-compatible vision endpoint:

```bash
export FASTBROWSE_VISION_URL=http://<host>:8080/v1/chat/completions
export FASTBROWSE_VISION_MODEL=<model-id>
```

No endpoint (or `FASTBROWSE_NO_VISION=1`) → hunt falls back to
first-result, receipt reports `SKIP`. Nothing crashes; the report says
what wasn't verified. Reference setup: Qwen2.5-VL-7B on llama.cpp over
Tailscale (~21s/look, local = electricity, not tokens).

Other knobs: `FASTBROWSE_PICS_DIR` (default `~/Downloads/pictures`),
`FASTBROWSE_ENGINE` (`camofox` walks through Cloudflare; vanilla
chromium doesn't — rule 14).

## How it works (30 seconds)

1. **Learn**: search → consent sweep → `grid_hunt` (badged thumbnails,
   NUMBER-only picks, pre-look overlay sweep + layout-shift tripwire) →
   photo page → tolerant download (rule 15) → `vision_check` receipt.
   Selectors land in `phase5/sites/<domain>.json`.
2. **Replay blind**: learned href + TEXT gate (Jaccard ≥0.95) with dHash
   second opinion. Both fail = drift → `needs_relearn` flag, one
   snapshot, stop. Either passes = noise, carry on.
3. **Report**: every run writes `run-report.json` — step times,
   `planner_bytes`, `est_observation_tokens` (bytes/4, the cost a naive
   agent WOULD have paid), `llm_calls: 0`, vision verdict.

## Layout

- `fb.py`, `fb_config.py`, `setup.sh`, `requirements.txt` — packaging
- `phase1/` measure · `phase2/` sentinel · `phase3/` site profiles
- `phase4/blind.py` — blind runner + hybrid drift gate
- `phase5/` — `grid_hunt.py`, `pixabay_suite.py`, `sydney_suite.py`,
  `learn.py` (headed human drive), `replay.py`, `sites/`, `runs/`
- `phase6/hover.py` — hover physics · `human_mouse.py` — Bezier mouse
- `REPORT.md` — full numbers + rules · `phase*/RESULTS.md` — per-phase

Run bulk (`profile/`, `downloads/`, raw `events.json`, browser
binaries) is gitignored — reports + action maps are the memory.
