#!/usr/bin/env python3
"""FastBrowse site knowledge store — one folder per learned site.

Layout (folder now, DB later):
  phase5/sites/<domain>/
    profile.json     selectors, last_verified, schema_version,
                     run1_href/run1_file, needs_relearn + stale_reason/at
    action_map.json  latest learned action list
    picks.json       verified picks [{href, file, vision, ask, run}]
    runs.json        run dir names that contributed to this site
    notes.json       free-form findings list [{date, text}]

Legacy files (phase5/sites/<domain>.json) are read as fallback and
migrated into the folder on the next save; the legacy file is then
removed so there is exactly one source of truth.

Future DB migration path (when we outgrow folders): one table
  sites(domain TEXT PK, profile JSON, action_map JSON, picks JSON,
        runs JSON, notes JSON, updated_at TEXT)
The function names below map 1:1 to that row — swap the backend without
touching the suites.
"""
import json
import time
from pathlib import Path

try:
    import fb_config  # repo-root import when cwd is a suite dir
    SITES_DIR = fb_config.SITES_DIR
except Exception:
    SITES_DIR = Path(__file__).resolve().parent / "sites"


def _safe(domain: str) -> str:
    return "".join(c for c in (domain or "").lower()
                   if c.isalnum() or c in (".", "-", "_")) or "unknown"


def site_dir(domain: str) -> Path:
    return SITES_DIR / _safe(domain)


def legacy_file(domain: str) -> Path:
    return SITES_DIR / f"{_safe(domain)}.json"


def _read_json(fp: Path, default):
    try:
        if fp.exists():
            return json.loads(fp.read_text())
    except Exception:
        pass
    return default


def load_profile(domain: str) -> dict:
    """New layout first, legacy <domain>.json fallback, else {}."""
    d = _safe(domain)
    prof = _read_json(site_dir(d) / "profile.json", None)
    if isinstance(prof, dict):
        return prof
    leg = _read_json(legacy_file(d), None)
    return leg if isinstance(leg, dict) else {}


def save_profile(domain: str, patch: dict | None = None,
                 action_map: list | None = None) -> dict:
    """Merge patch into profile.json (migrating legacy first)."""
    d = _safe(domain)
    sdir = site_dir(d)
    sdir.mkdir(parents=True, exist_ok=True)
    prof = _read_json(sdir / "profile.json", None)
    if prof is None:
        prof = _read_json(legacy_file(d), None) or {}
        if not isinstance(prof, dict):
            prof = {}
        # legacy pexels profile carried verified picks inline — promote them
        legacy_picks = prof.pop("verified_picks", None)
        if isinstance(legacy_picks, list) and legacy_picks:
            try:
                fpicks = sdir / "picks.json"
                existing = _read_json(fpicks, []) or []
                seen = {(p.get("href"), p.get("file")) for p in existing
                        if isinstance(p, dict)}
                for p in legacy_picks:
                    if isinstance(p, dict) and (p.get("href"), p.get("file")) not in seen:
                        existing.append(p)
                fpicks.write_text(json.dumps(existing, indent=1), encoding="utf-8")
            except Exception:
                pass
    if patch:
        prof.update(patch)
    if action_map is not None:
        prof["selectors"] = action_map
        (sdir / "action_map.json").write_text(
            json.dumps(action_map, indent=1), encoding="utf-8")
    prof.setdefault("schema_version", 1)
    (sdir / "profile.json").write_text(
        json.dumps(prof, indent=1), encoding="utf-8")
    # legacy file served its purpose — remove so one source of truth remains
    try:
        if legacy_file(d).exists():
            legacy_file(d).unlink()
    except Exception:
        pass
    return prof


def add_pick(domain: str, pick: dict) -> list:
    d = _safe(domain)
    sdir = site_dir(d)
    sdir.mkdir(parents=True, exist_ok=True)
    fp = sdir / "picks.json"
    picks = _read_json(fp, []) or []
    if isinstance(picks, dict):
        picks = picks.get("verified_picks", [])
    picks.append(pick)
    fp.write_text(json.dumps(picks, indent=1), encoding="utf-8")
    return picks


def add_note(domain: str, text: str) -> list:
    d = _safe(domain)
    sdir = site_dir(d)
    sdir.mkdir(parents=True, exist_ok=True)
    fp = sdir / "notes.json"
    notes = _read_json(fp, []) or []
    notes.append({"date": time.strftime("%Y-%m-%d %H:%M"), "text": text[:500]})
    fp.write_text(json.dumps(notes, indent=1), encoding="utf-8")
    return notes


def index_run(domain: str, run_name: str) -> list:
    d = _safe(domain)
    sdir = site_dir(d)
    sdir.mkdir(parents=True, exist_ok=True)
    fp = sdir / "runs.json"
    runs = _read_json(fp, []) or []
    if run_name not in runs:
        runs.append(run_name)
    fp.write_text(json.dumps(runs, indent=1), encoding="utf-8")
    return runs


def mark_stale(domain: str, reason: str) -> None:
    if not domain:
        return
    try:
        save_profile(domain, {"needs_relearn": True,
                              "stale_reason": reason[:200],
                              "stale_at": time.strftime("%Y-%m-%d %H:%M")})
    except Exception:
        pass


def clear_stale(domain: str) -> None:
    try:
        d = _safe(domain)
        sdir = site_dir(d)
        sdir.mkdir(parents=True, exist_ok=True)
        fp = sdir / "profile.json"
        prof = _read_json(fp, None)
        if prof is None:
            prof = load_profile(domain)
        for k in ("needs_relearn", "stale_reason", "stale_at"):
            prof.pop(k, None)
        prof["needs_relearn"] = False
        fp.write_text(json.dumps(prof, indent=1), encoding="utf-8")
    except Exception:
        pass


def all_sites() -> list:
    """All known domains, new folders + unmigrated legacy files."""
    out: dict = {}
    try:
        if SITES_DIR.exists():
            for p in sorted(SITES_DIR.iterdir()):
                if p.is_dir():
                    out[p.name] = "folder"
                elif p.suffix == ".json":
                    out.setdefault(p.stem, "legacy")
    except Exception:
        pass
    return sorted(out.items())
