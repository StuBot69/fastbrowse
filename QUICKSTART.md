# FastBrowse — Quick Start

## Setup (one-time)

```bash
git clone <this-repo> && cd fastbrowse
./setup.sh              # venv + deps + chromium; copies .env.example -> .env
cp .env.example .env    # only if setup didn't (fill in vision endpoints)
.venv/bin/python fb.py doctor   # health check: what works, what's missing
```

Eyes-first hunting needs a vision endpoint (see `.env.example`); without
one, hunts run degraded (first-result picks, receipt `SKIP`). Camoufox
fetches its own browser on first launch.

## Hunt

```bash
.venv/bin/python fb.py hunt --ask "what you want" --site pixabay --out runs/my-run
```

## Replay (blind, no vision)

```bash
.venv/bin/python fb.py replay runs/my-run
```

## Verify a download

```bash
.venv/bin/python fb.py verify path/to/image.jpg --ask "what it should show"
```

## List supported sites

```bash
.venv/bin/python fb.py sites
```

## Vision endpoints

| Priority | Endpoint | Model | Cost |
|----------|----------|-------|------|
| Primary ("Jasper" role) | your own box, e.g. `http://127.0.0.1:8080` | Qwen2.5-VL-7B | Free (local electricity) |
| Fallback ("Groq" role) | Groq API | Llama 3.2 11B Vision | Free tier |

Auto-fallback: if primary is down, Groq takes over. Configure both in
`.env` (see `.env.example`) — no code changes needed.

```bash
FASTBROWSE_VISION_URL=http://... FASTBROWSE_VISION_MODEL=... .venv/bin/python fb.py hunt ...
```

## Repo layout

```
fastbrowse/
├── fb.py              # CLI entrypoint (hunt/replay/verify/sites/rules)
├── fb_config.py       # Shared config: paths, vision endpoints, env overrides
├── setup.sh           # One-time venv + deps install
├── .hermes.md         # Auto-loaded context for Hermes sessions
├── requirements.txt
├── phase3/            # Site profiles (selectors, gates, blind replay)
├── phase4/            # Frame-aware overlay + blind mode
├── phase5/            # Suites + runs
│   ├── grid_hunt.py   # Eyes-first image grid picker (primary→fallback vision)
│   ├── pixabay_suite.py
│   ├── sydney_suite.py
│   └── together_signup.py  # Dead — Together AI needs $5 minimum
└── runs/              # Hunt outputs + receipts
```