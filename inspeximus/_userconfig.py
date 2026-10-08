"""Settings that only the user's own config may set (3.17.0, AUDIT-A inventory).

A project's `.claude/settings.json` `env` reaches every hook and every MCP server that Claude Code starts, so an
`INSPEXIMUS_*` variable in the environment can come from whoever wrote the repository. The variables below change what
a server writes to the user's store: the receipt format and the store format. They are read from
`<key home>/inspeximus/config.json`, the file `archive`, `embed.hooks` and `embed.key` already come from, and never from
the environment and never from the merged `claude_code._cfg(cwd)`, which includes the repository's own
`.inspeximus/config.json`. An environment value is ignored, with one stderr line per variable and process that names
the config key and the full path.
"""
from __future__ import annotations

import json
import os
import sys

_SAID: set = set()


def path() -> str:
    from ._storelink import user_config_file
    return user_config_file()


_CACHE: dict = {}


def read() -> dict:
    """The user's config as a dict; `{}` when the file is absent, unreadable, or not an object.

    Cached by the file's path, size and modification time, so a caller on a hot path (`_rows_available` asks once per
    save) pays one `stat` and not one open and parse."""
    p = path()
    try:
        st = os.stat(p)
        key = (p, st.st_size, st.st_mtime_ns)
    except OSError:
        return {}
    hit = _CACHE.get(p)
    if hit is not None and hit[0] == key:
        return hit[1]
    try:
        with open(p, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}
    _CACHE[p] = (key, cfg)
    return cfg


def get(*keys, default=None):
    """`config[keys[0]][keys[1]]...` from the user's config, or `default`."""
    cur = read()
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def env_ignored(var: str, key: str) -> None:
    """Say once per process that `var` was set in the environment and is not read. Never raises."""
    if var in _SAID:
        return
    _SAID.add(var)
    try:
        sys.stderr.write("[inspeximus] %s is set in the environment and is ignored: a project's settings can set it. "
                         "Set %s in %s.%s" % (var, key, path(), chr(10)))
    except Exception:                                           # noqa: BLE001
        pass
