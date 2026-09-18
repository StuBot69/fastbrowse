# FastBrowse — Quick Start

## Setup (one-time)

```bash
cd ~/Projects/fastbrowse
./setup.sh
```

Installs venv, deps, Chromium. Camoufox fetches its own browser on first launch.

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

| Priority | Where | Model | Cost |
|----------|-------|-------|------|
| Primary | Jasper :8080 (Tailscale 100.95.162.99) | Qwen2.5-VL-7B | Free (local) |
| Fallback | Groq API | Llama 3.2 11B Vision | Free (1M tokens/day) |

Auto-fallback: if Jasper is down, Groq takes over. No config needed — the Groq key is auto-loaded from the age vault.

Override with env vars:
```bash
FASTBROWSE_VISION_URL=http://... FASTBROWSE_VISION_MODEL=... .venv/bin/python fb.py hunt ...
FASTBROWSE_FALLBACK_URL=https://api.groq.com/openai/v1/chat/completions .venv/bin/python fb.py hunt ...
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
│   ├── grid_hunt.py   # Eyes-first image grid picker (Jasper→Groq fallback)
│   ├── pixabay_suite.py
│   ├── sydney_suite.py
│   └── together_signup.py  # Dead — Together AI needs $5 minimum
└── runs/              # Hunt outputs + receipts
```