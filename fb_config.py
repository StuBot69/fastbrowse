#!/usr/bin/env python3
"""FastBrowse shared config — env overrides, sane defaults.

Every suite and helper imports this instead of hardcoding hosts/paths.
A stranger cloning the repo gets working defaults minus vision (which
degrades gracefully); Stu's setup comes from the environment.

Env vars:
  FASTBROWSE_VISION_URL    OpenAI-compatible chat-completions endpoint
                           with vision (default: Jasper over Tailscale)
  FASTBROWSE_VISION_MODEL  model id the endpoint serves
                           (default: Qwen2.5-VL abl... on Jasper :8080)
  FASTBROWSE_PICS_DIR      where finished downloads get copied
                           (default: ~/Downloads/pictures)
  FASTBROWSE_ENGINE        camofox | chromium (default: camofox)
  FASTBROWSE_NO_VISION     1 = skip Jasper looks entirely (scorer fallback)
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PHASE5 = REPO_ROOT / "phase5"
RUNS_DIR = PHASE5 / "runs"
SITES_DIR = PHASE5 / "sites"

VISION_URL = os.environ.get(
    "FASTBROWSE_VISION_URL", "http://100.95.162.99:8080/v1/chat/completions")
VISION_MODEL = os.environ.get(
    "FASTBROWSE_VISION_MODEL",
    "/home/jasper/models/Qwen2.5-VL-7B-Abliterated-Q4_K_M.gguf")
PICS_DIR = Path(os.environ.get("FASTBROWSE_PICS_DIR",
                               str(Path.home() / "Downloads" / "pictures")))
ENGINE = os.environ.get("FASTBROWSE_ENGINE", "camofox")
NO_VISION = os.environ.get("FASTBROWSE_NO_VISION", "") == "1"


def vision_available(timeout: int = 8) -> bool:
    """Probe the vision endpoint. Never raises — returns False."""
    if NO_VISION:
        return False
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(VISION_URL.rsplit("/chat/completions", 1)[0]
                                    + "/models", timeout=timeout) as r:
            json.load(r)
        return True
    except Exception:
        return False
