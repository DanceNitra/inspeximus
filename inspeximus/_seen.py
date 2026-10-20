"""Inject each memory line once per session (3.22).

The prompt hook and the pre-tool hook used to print the same decisions on every prompt. Measured by AUDIT-B over
3,666 transcripts (agora_output/strategy/audit_b_graph_stage0.md, section "Follow-up"): the UserPromptSubmit hook
injected 49.7 MB over 130 sessions, and 92.7 % of it repeated a line already injected earlier in the same session.

This module remembers, per session, the lines a hook has already put into the agent's context, and drops a repeat:

  * the key is the line's text, whitespace collapsed, so a CHANGED line (a superseded decision now reading its new
    value) is new and is injected again, as the correction it is;
  * a compaction drops what the agent saw, so `forget_session` clears the set and the next prompt injects again;
  * without a session id nothing is remembered and nothing is dropped, because two sessions must never share a set;
  * INSPEXIMUS_HOOK_DEDUPE=0 turns it off.

The state is one small JSON file per session under the key home (`<key home>/inspeximus/seen/`), never in the
project and never in a temporary directory. Files untouched for `EXPIRE_S` are removed on the next write. Every
failure is open: a state file that cannot be read or written means the line is printed, never lost.
"""
import hashlib
import json
import os
import time

from . import _envpolicy

#: A session's set is dropped after this long without a write: a week, longer than any session we have measured.
EXPIRE_S = 7 * 24 * 3600

_OFF = ("0", "false", "no", "off")


def enabled() -> bool:
    """False when INSPEXIMUS_HOOK_DEDUPE is set to an off value."""
    return _envpolicy.raw("INSPEXIMUS_HOOK_DEDUPE", "").strip().lower() not in _OFF


def _dir() -> str:
    from ._keyhome import key_home
    return os.path.join(key_home(), "inspeximus", "seen")


def _path(session_id) -> str:
    # The session id is hashed, so a hostile id cannot name a path outside the directory.
    name = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:32]
    return os.path.join(_dir(), name + ".json")


def _key(line) -> str:
    return hashlib.sha256(" ".join(str(line).split()).encode("utf-8")).hexdigest()[:24]


def _load(path) -> set:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return set(data.get("seen") or []) if isinstance(data, dict) else set()
    except (OSError, ValueError):
        return set()


def _expire(now) -> None:
    try:
        for name in os.listdir(_dir()):
            p = os.path.join(_dir(), name)
            if name.endswith(".json") and now - os.path.getmtime(p) > EXPIRE_S:
                os.unlink(p)
    except OSError:
        pass


def once(session_id, lines) -> list:
    """`lines` without those already injected in this session, in their order; the kept ones are remembered.

    A repeat inside `lines` itself is kept once. On any error every line is returned, so nothing is withheld."""
    lines = list(lines)
    if not session_id or not enabled() or not lines:
        return lines
    try:
        path = _path(session_id)
        seen = _load(path)
        keep, added = [], False
        for line in lines:
            k = _key(line)
            if k in seen:
                continue
            seen.add(k)
            keep.append(line)
            added = True
        if added:
            from ._safewrite import write_atomic
            os.makedirs(_dir(), exist_ok=True)
            now = time.time()
            write_atomic(path, json.dumps({"seen": sorted(seen), "at": int(now)}))
            _expire(now)
        return keep
    except Exception:                                   # noqa: BLE001 -- fail open: print, never withhold
        return lines


def forget_session(session_id) -> None:
    """Drop the set of `session_id`: after a compaction the agent no longer holds what it was shown."""
    if not session_id:
        return
    try:
        os.unlink(_path(session_id))
    except OSError:
        pass
