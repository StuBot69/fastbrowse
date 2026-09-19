#!/usr/bin/env python3
"""FastBrowse session store — reuse logins without re-verifying.

Problem it kills: every fresh Camoufox profile is a new device, so sites
with 2-step verification challenge EVERY run. Nobody wants a 2SV code per
hunt. Solution: log in ONCE (headed, human types the password — keystrokes
never touch logs), keep the session in a canonical profile dir, and seed
each run's throwaway profile from it with a plain directory copy.

  canonical:  phase5/profiles/<site>/      (gitignored, live cookies)
  per-run:    phase5/runs/<run>/profile/   (gitignored, seeded copy)

Same cookies + same machine + Camoufox fingerprint family = the site sees
a known device, no fresh-device challenge. Copy only while the browser is
closed (suites run sequentially, so this holds); lock files are skipped.

Nothing here ever stores a password — only the session the site issued
after YOU logged in. Credentials stay in your head + the site's form.
"""
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILES_DIR = HERE / "profiles"

# Browser lock / singleton files that must NOT be copied into a seed —
# they belong to a live process and corrupt the copy if carried over.
_LOCK_NAMES = {"SingletonLock", "SingletonSocket", "SingletonCookie",
               "lock", "parent.lock", ".parentlock"}


def canonical(site: str) -> Path:
    return PROFILES_DIR / site


def seed_profile(site: str, target: Path) -> dict:
    """Seed `target` from the canonical login profile for `site`.

    Returns {"seeded": bool, "reason": str}. Never raises — a failed seed
    just means the run starts fresh (caller proceeds logged-out).
    """
    src = canonical(site)
    try:
        target = Path(target)
        if not src.exists() or not src.is_dir():
            return {"seeded": False,
                    "reason": f"no canonical profile at {src}"}
        if target.exists() and any(target.iterdir()):
            return {"seeded": False, "reason": "target not empty, keep it"}

        def _ignore(d, names):
            return [n for n in names
                    if n in _LOCK_NAMES or n.endswith(".lock")]

        shutil.copytree(src, target, ignore=_ignore, dirs_exist_ok=True)
        return {"seeded": True,
                "reason": f"seeded from {src} ({len(list(target.iterdir()))} entries)"}
    except Exception as e:
        return {"seeded": False, "reason": str(e)[:120]}
