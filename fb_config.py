#!/usr/bin/env python3
"""FastBrowse shared config — env overrides, sane defaults.

Every suite and helper imports this instead of hardcoding hosts/paths.
A stranger cloning the repo gets working defaults minus vision (which
degrades gracefully); Stu's setup comes from the environment.

Env vars:
  FASTBROWSE_VISION_URL    Primary OpenAI-compatible chat-completions endpoint
                           with vision (default: Jasper over Tailscale)
  FASTBROWSE_VISION_MODEL  model id the primary endpoint serves
                           (default: Qwen2.5-VL abliterated on Jasper :8080)
  FASTBROWSE_FALLBACK_URL  Secondary vision endpoint (default: Groq)
  FASTBROWSE_FALLBACK_MODEL model id the fallback endpoint serves
                           (default: Llama 3.2 11B Vision on Groq)
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

# Primary vision: Qwen2.5-VL-7B on Jasper (Tailscale)
VISION_URL = os.environ.get(
    "FASTBROWSE_VISION_URL",
    "http://100.95.162.99:8080/v1/chat/completions")
VISION_MODEL = os.environ.get(
    "FASTBROWSE_VISION_MODEL",
    "/home/jasper/models/Qwen2.5-VL-7B-Abliterated-Q4_K_M.gguf")

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
    if NO_VISION:
        return False
    return _probe(VISION_URL, timeout)


def fallback_available(timeout: int = 8) -> bool:
    """Probe fallback vision endpoint. Never raises — returns False."""
    if NO_VISION:
        return False
    return _probe(FALLBACK_URL, timeout)


def active_vision() -> tuple:
    """Return (url, model) for whichever vision endpoint is live.

    Prefers primary (Jasper Qwen), falls back to Groq Llama.
    """
    if vision_available():
        return (VISION_URL, VISION_MODEL)
    if fallback_available():
        return (FALLBACK_URL, FALLBACK_MODEL)
    return (None, None)