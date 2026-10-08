"""Tests that used to set a 3.18 config-only or guarded INSPEXIMUS_* variable in the environment write the same setting
into the user's config instead, in the test's own key home (inspeximus/_envpolicy.py names the key of each)."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import _envpolicy  # noqa: E402

_ON = ("1", "true", "yes", "on")


def _value(var: str, raw: str):
    if var == "INSPEXIMUS_PROJECT":
        return [raw]
    if var in ("INSPEXIMUS_ECHO_GUARD", "INSPEXIMUS_READ_GUARDS", "INSPEXIMUS_HEADS"):
        return raw.strip().lower() not in ("0", "off", "false", "no")
    if var in ("INSPEXIMUS_OBSERVE_RECALL", "INSPEXIMUS_PII_DETECT", "INSPEXIMUS_KEEP_CONVERSION_BACKUP"):
        return raw.strip().lower() in _ON
    if var == "INSPEXIMUS_BUSY_TIMEOUT_S":
        return float(raw)
    if var == "INSPEXIMUS_SAVE_RETRIES":
        return int(raw)
    return raw


def config_settings(env: dict) -> tuple:
    """(the user-config dict for the variables that have a config key, the rest of `env`)."""
    cfg, rest = {}, {}
    for var, raw in env.items():
        rule = _envpolicy.POLICY.get(var)
        if rule and rule[1] and var not in ("INSPEXIMUS_STORE_FORMAT", "INSPEXIMUS_RECEIPTS_TAIL",
                                            "INSPEXIMUS_RECEIPT_KEY_FILE", "INSPEXIMUS_WRITER_KEY_FILE"):
            cur = cfg
            parts = rule[1].split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = _value(var, raw)
            if var == "INSPEXIMUS_PROJECT":
                rest[var] = raw                  # the config only allows the name; the environment still sets it
        else:
            rest[var] = raw
    return cfg, rest


def write_user_config(key_home: str, cfg: dict) -> None:
    """Merge `cfg` into `<key_home>/inspeximus/config.json`."""
    from inspeximus import _userconfig
    p = os.path.join(key_home, "inspeximus", "config.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        with open(p, encoding="utf-8") as fh:
            cur = json.load(fh)
    except (OSError, ValueError):
        cur = {}

    def merge(a, b):
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                merge(a[k], v)
            else:
                a[k] = v
    merge(cur, cfg)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(cur, fh)
    _userconfig._CACHE.clear()
