#!/usr/bin/env python3
"""FastBrowse shared config — env overrides, sane defaults.

Every suite and helper imports this instead of hardcoding hosts/paths.
A stranger cloning the repo gets working defaults minus vision (which
degrades gracefully); operators add their own endpoints via environment
or a local `.env` file (gitignored — never commit yours).

We call the primary endpoint "Jasper" and the secondary "Groq" in logs
and report keys — those are ROLE names for primary/fallback vision, not
anybody's machine. Point them at whatever you run.

Env vars (or `.env` in the repo root):
  FASTBROWSE_VISION_URL    Primary OpenAI-compatible chat-completions endpoint
                           with vision (default: unset — hunts run eyes-first
                           only when this is reachable)
  FASTBROWSE_VISION_MODEL  model id the primary endpoint serves
                           (default: qwen2.5-vl-7b)
  FASTBROWSE_FALLBACK_URL  Secondary vision endpoint (default: Groq)
  FASTBROWSE_FALLBACK_MODEL model id the fallback endpoint serves
                           (default: Llama 3.2 11B Vision on Groq)
  FASTBROWSE_GROQ_API_KEY  API key for the fallback endpoint (default: unset)
  FASTBROWSE_PICS_DIR      where finished downloads get copied
                           (default: ~/Downloads/pictures)
  FASTBROWSE_ENGINE        camofox | chromium (default: camofox)
  FASTBROWSE_NO_VISION     1 = skip vision looks entirely (scorer fallback)
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PHASE5 = REPO_ROOT / "phase5"
RUNS_DIR = PHASE5 / "runs"
SITES_DIR = PHASE5 / "sites"


def _load_dotenv() -> None:
    """Tiny stdlib `.env` loader: KEY=value lines, # comments, no deps.
    Real environment always wins — .env only fills gaps."""
    fp = REPO_ROOT / ".env"
    try:
        text = fp.read_text(encoding="utf-8")
    except Exception:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("'\"")
        if k and v and k not in os.environ:
            os.environ[k] = v


_load_dotenv()

# Primary vision ("Jasper" role): any OpenAI-compatible endpoint serving
# a vision-capable model — e.g. Qwen2.5-VL on llama.cpp on your own box.
# Unset by default: hunts degrade gracefully until you configure one.
VISION_URL = os.environ.get("FASTBROWSE_VISION_URL", "")
VISION_MODEL = os.environ.get(
    "FASTBROWSE_VISION_MODEL",
    "qwen2.5-vl-7b")

# Fallback vision: Llama 3.2 11B Vision on Groq (free tier, 1M tokens/day)
FALLBACK_URL = os.environ.get(
    "FASTBROWSE_FALLBACK_URL",
    "https://api.groq.com/openai/v1/chat/completions")
FALLBACK_MODEL = os.environ.get(
    "FASTBROWSE_FALLBACK_MODEL",
    "llama-3.2-11b-vision-preview")

PICS_DIR = Path(os.environ.get("FASTBROWSE_PICS_DIR",
                               str(Path.home() / "Downloads" / "pictures")))
ENGINE = os.environ.get("FASTBROWSE_ENGINE", "camofox")
NO_VISION = os.environ.get("FASTBROWSE_NO_VISION", "") == "1"


def _probe(url: str, timeout: int = 8) -> bool:
    """Probe an OpenAI-compatible /models endpoint. Never raises."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(
                url.rsplit("/chat/completions", 1)[0] + "/models",
                timeout=timeout) as r:
            json.load(r)
        return True
    except Exception:
        return False


def vision_available(timeout: int = 8) -> bool:
    """Probe primary vision endpoint. Never raises — returns False."""
    if NO_VISION or not VISION_URL:
        return False
    return _probe(VISION_URL, timeout)


def fallback_available(timeout: int = 8) -> bool:
    """Probe fallback vision endpoint. Never raises — returns False."""
    if NO_VISION:
        return False
    return _probe(FALLBACK_URL, timeout)


def active_vision() -> tuple:
    """Return (url, model) for whichever vision endpoint is live.

    Prefers primary, falls back to Groq.
    """
    if vision_available():
        return (VISION_URL, VISION_MODEL)
    if fallback_available():
        return (FALLBACK_URL, FALLBACK_MODEL)
    return (None, None)