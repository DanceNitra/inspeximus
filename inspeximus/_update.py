"""Deterministic, opt-out "a newer version exists" check — the standard pip/npm/gh courtesy.

`check_for_update()` returns a one-line ASCII notice (or None) when the installed inspeximus is behind the
latest on PyPI. It is:
  - throttled to at most once per 24h (cached in <cache_dir>/.update_check.json) so it never nags per-call;
    `cached_notice()` answers from that cache with no network call, which is how every agent on a shared
    store sees the notice, not only the process that ran the day's check (3.14.0);
  - fail-open: any network/parse error, or being offline, returns None silently and never blocks;
  - opt-out: INSPEXIMUS_NO_UPDATE_CHECK=1 disables it entirely;
  - ASCII-only, so it is safe to print on a non-UTF-8 console.

Callers pick the output stream: the Claude Code plugin prints it to stdout (SessionStart, injected as
context); the MCP stdio server prints to STDERR (stdout is the JSON-RPC channel and must not be polluted).
"""
from __future__ import annotations
import json
import os
import time
import urllib.request

# The distribution is `inspeximus`. This said `agora-inspeximus`, which 404s — so the notice could
# never fire, and if it had it would have told the user to install a package that does not exist.
_PYPI_JSON = "https://pypi.org/pypi/inspeximus/json"
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
    """Return a one-line notice if a newer inspeximus is on PyPI, else None. Fully fail-open."""
    if os.environ.get("INSPEXIMUS_NO_UPDATE_CHECK", "").strip().lower() in ("1", "true", "yes"):
        return None
    try:
        cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".inspeximus")
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
            return _notice(latest, current_version)
    except Exception:
        return None
    return None


def cached_notice(current_version, cache_dir=None):
    """The notice from the last check's cached answer, with no network call; None when there is none.

    ONE STORE, SEVERAL AGENTS, ONE THROTTLE (3.14.0). `check_for_update` is quiet inside its 24-hour
    window, so the first process of the day saw the notice and every other agent did not: Claude Code's
    hook checked first and Codex, Gemini and Cursor never heard of a new version. The MCP server's
    handshake and the hook read this instead, so the throttle stays on the network, not on telling the
    user."""
    if os.environ.get("INSPEXIMUS_NO_UPDATE_CHECK", "").strip().lower() in ("1", "true", "yes"):
        return None
    try:
        cache = os.path.join(cache_dir or os.path.join(os.path.expanduser("~"), ".inspeximus"),
                             ".update_check.json")
        latest = json.load(open(cache, encoding="utf-8")).get("latest")
        return _notice(latest, current_version) if latest and _is_newer(latest, current_version) else None
    except Exception:
        return None


def _notice(latest, current_version):
    """The notice. Its second command re-pins every agent the installer wired, because each host's MCP
    entry names the exact version that wrote it."""
    return (
        f"[inspeximus] A new version is available: {latest} (you have {current_version}).\n"
        "        Update:  pip install -U \"inspeximus[mcp]\"  then  inspeximus install --all   |   "
        "changelog: https://github.com/DanceNitra/inspeximus/blob/main/CHANGELOG.md\n"
        "        (silence this with INSPEXIMUS_NO_UPDATE_CHECK=1)")
