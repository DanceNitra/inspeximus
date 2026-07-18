"""Deterministic, opt-out "a newer version exists" check — the standard pip/npm/gh courtesy.

`check_for_update()` returns a one-line ASCII notice (or None) when the installed agora-mnemo is behind the
latest on PyPI. It is:
  - throttled to at most once per 24h (cached in <cache_dir>/.update_check.json) so it never nags per-call;
  - fail-open: any network/parse error, or being offline, returns None silently and never blocks;
  - opt-out: MNEMO_NO_UPDATE_CHECK=1 disables it entirely;
  - ASCII-only, so it is safe to print on a non-UTF-8 console.

Callers pick the output stream: the Claude Code plugin prints it to stdout (SessionStart, injected as
context); the MCP stdio server prints to STDERR (stdout is the JSON-RPC channel and must not be polluted).
"""
from __future__ import annotations
import json
import os
import time
import urllib.request

_PYPI_JSON = "https://pypi.org/pypi/agora-mnemo/json"
_TTL_S = 24 * 3600


def _parse(v):
    """Best-effort PEP440-ish tuple: leading numeric release segments only (1.12.1 -> (1,12,1))."""
    out = []
    for part in str(v).split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        out.append(int(num))
    return tuple(out)


def _is_newer(latest, current):
    try:
        return _parse(latest) > _parse(current)
    except Exception:
        return False


def check_for_update(current_version, cache_dir=None, timeout=1.5):
    """Return a one-line notice if a newer agora-mnemo is on PyPI, else None. Fully fail-open."""
    if os.environ.get("MNEMO_NO_UPDATE_CHECK", "").strip().lower() in ("1", "true", "yes"):
        return None
    try:
        cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".mnemo")
        os.makedirs(cache_dir, exist_ok=True)
        cache = os.path.join(cache_dir, ".update_check.json")

        # Throttle: only touch the network once per TTL. Between checks, stay quiet.
        try:
            st = json.load(open(cache, encoding="utf-8"))
        except Exception:
            st = {}
        if (time.time() - float(st.get("checked_at", 0))) < _TTL_S:
            return None

        latest = None
        try:
            req = urllib.request.Request(_PYPI_JSON, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                latest = json.loads(r.read()).get("info", {}).get("version")
        except Exception:
            latest = None

        # Stamp the attempt regardless, so a flaky network doesn't retry every call for a day.
        try:
            json.dump({"checked_at": time.time(), "latest": latest}, open(cache, "w", encoding="utf-8"))
        except Exception:
            pass

        if latest and _is_newer(latest, current_version):
            return (
                f"[mnemo] A new version is available: {latest} (you have {current_version}).\n"
                "        Update:  pip install -U agora-mnemo   |   "
                "changelog: https://github.com/DanceNitra/mnemo/blob/main/CHANGELOG.md\n"
                "        (silence this with MNEMO_NO_UPDATE_CHECK=1)")
    except Exception:
        return None
    return None
