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
  FASTBROWSE_LOCAL_URL     Third-choice local vision endpoint (default: unset)
  FASTBROWSE_LOCAL_MODEL   model id the local endpoint serves
  FASTBROWSE_LOCAL_API_KEY optional bearer token for the local endpoint
  FASTBROWSE_LOCAL_MAX_TOKENS
                           max_tokens for the local endpoint (default: 1200).
                           Reasoning VL models spend most of the budget on
                           reasoning_content — too low a cap returns
                           content=None and reads as a false negative.
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

# Cloudflare fronts several vision endpoints and 1010-blocks Python-urllib's
# default UA. Identify as a browser everywhere we call out.
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/126.0 Safari/537.36")


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

# Third-choice local vision endpoint (e.g. a local VL model on this Mac).
# Used when the remote primary is down AND the cloud fallback has no vision
# model available. Reasoning VL models need a generous max_tokens.
LOCAL_URL = os.environ.get("FASTBROWSE_LOCAL_URL", "")
LOCAL_MODEL = os.environ.get("FASTBROWSE_LOCAL_MODEL", "")
LOCAL_API_KEY = os.environ.get("FASTBROWSE_LOCAL_API_KEY", "")
LOCAL_MAX_TOKENS = int(os.environ.get("FASTBROWSE_LOCAL_MAX_TOKENS", "1200"))

PICS_DIR = Path(os.environ.get("FASTBROWSE_PICS_DIR",
                               str(Path.home() / "Downloads" / "pictures")))
ENGINE = os.environ.get("FASTBROWSE_ENGINE", "camofox")
NO_VISION = os.environ.get("FASTBROWSE_NO_VISION", "") == "1"


def _probe(url: str, timeout: int = 8, api_key: str = "") -> bool:
    """Probe an OpenAI-compatible /models endpoint. Never raises."""
    import json
    import urllib.request
    try:
        headers = {"User-Agent": _BROWSER_UA}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(
            url.rsplit("/chat/completions", 1)[0] + "/models",
            headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
    """Probe fallback vision endpoint (with key — Groq 401s without).

    A reachable /models endpoint is NOT proof of vision: Groq accounts
    without a vision-capable model pass this probe but fail every
    image request with HTTP 400. Check the model list for a vision model.
    Never raises — returns False.
    """
    if NO_VISION:
        return False
    if not _probe(FALLBACK_URL, timeout,
                  api_key=os.environ.get("FASTBROWSE_GROQ_API_KEY", "")):
        return False
    return _probe_has_vision_model(FALLBACK_URL, timeout,
                                   os.environ.get("FASTBROWSE_GROQ_API_KEY", ""))


def local_available(timeout: int = 8) -> bool:
    """Probe the local third-choice vision endpoint. Never raises."""
    if NO_VISION or not LOCAL_URL:
        return False
    return _probe(LOCAL_URL, timeout, api_key=LOCAL_API_KEY)


def _probe_has_vision_model(url: str, timeout: int, api_key: str = "") -> bool:
    """True if the endpoint's model list contains a vision-capable model.

    Vision model names carry 'vision', 'vl', 'llava', or 'llama-4'.
    """
    import json as _json
    import urllib.request as _req
    try:
        headers = {"User-Agent": _BROWSER_UA}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        r = _req.Request(
            url.rsplit("/chat/completions", 1)[0] + "/models", headers=headers)
        with _req.urlopen(r, timeout=timeout) as resp:
            data = _json.load(resp)
        ids = [m.get("id", "").lower() for m in data.get("data", [])]
        return any(("vision" in i or "llava" in i or "vl" in i
                    or "llama-4" in i) for i in ids)
    except Exception:
        return False


def active_vision() -> tuple:
    """Return (url, model) for whichever vision endpoint is live.

    Order: primary, cloud fallback (only if it has a vision model),
    local endpoint.
    """
    if vision_available():
        return (VISION_URL, VISION_MODEL)
    if fallback_available():
        return (FALLBACK_URL, FALLBACK_MODEL)
    if local_available():
        return (LOCAL_URL, LOCAL_MODEL)
    return (None, None)