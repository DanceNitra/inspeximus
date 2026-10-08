"""Where each INSPEXIMUS_* setting may come from (3.18; AUDIT-A, appendix of 2026-10-08).

A project's `.claude/settings.json` `env` reaches every hook and every MCP server Claude Code starts, so an
INSPEXIMUS_* value in the environment can be the repository's choice and not the user's. 3.17.0 moved three settings to
the user's own `<key home>/inspeximus/config.json` (`receipts.tail`, `store.format`, the embed recipe). This module does
the same for the rest, by one rule per variable, and `POLICY` names the rule of every variable the package reads.
`tests/test_every_environment_variable_has_a_policy.py` fails on a variable the package reads that is not in it.

THE THREE RULES
  * CONFIG_ONLY: read from the user's config. A value in the environment is ignored, with one stderr line per
    process naming the config key and the file (`_userconfig.env_ignored`).
  * ENV_GUARD: the environment is honoured only in the direction that cannot harm the user's existing store: it may
    switch a guard on and never off, raise a wait and never lower it, name a key file inside the user's key home and
    nowhere else, name the project the process runs in and no other. Anything else is ignored with the same line.
  * ENV_SAFE: the environment is honoured. Each entry says why: read side only, hook only, opt-in that adds and
    removes nothing, or a guard that already exists (3.16.x) and has its own test.

An explicit argument (a constructor keyword, a command-line flag) always wins: it is the caller's own choice, made in
code or typed by the user, and never arrives through a project's settings.
"""
from __future__ import annotations

import os

CONFIG_ONLY = "config-only"
ENV_GUARD = "env-with-guard"
ENV_SAFE = "env-safe"

#: variable -> (rule, user-config key or None, why). Groups A to F are the appendix's.
POLICY = {
    # A. which file it writes
    "INSPEXIMUS_PATH": (ENV_GUARD, None, "a link inside the project is vetted (3.16.5, F-33 to F-39)"),
    "INSPEXIMUS_SCOPE": (ENV_GUARD, None, "`project` and `claude-code` go through the vet"),
    "INSPEXIMUS_CODING_STORE": (ENV_GUARD, None, "outside the project needs the user's config (3.16.5)"),
    "INSPEXIMUS_KEY_HOME": (ENV_GUARD, None, "refused inside a repository (3.16.4, F-13)"),
    "INSPEXIMUS_PROJECT": (ENV_GUARD, "project.names", "only `auto`, the name of the folder the process runs in, or a "
                           "name the user's config lists; a project could otherwise write into another's namespace"),
    # B. how it writes: format, guards, identity
    "INSPEXIMUS_STORE_FORMAT": (CONFIG_ONLY, "store.format", "converts the user's row store (3.17.0)"),
    "INSPEXIMUS_RECEIPTS_TAIL": (CONFIG_ONLY, "receipts.tail", "converts the receipt sidecar (3.17.0)"),
    "INSPEXIMUS_RECEIPTS": (ENV_GUARD, "receipts.enabled", "starts a chain only when the user's config names a signing "
                            "key; an unsigned chain from a project left the user's key unable to sign the store"),
    "INSPEXIMUS_RECEIPT_KEY": (ENV_GUARD, "receipts.key", "a 64-hex key from the environment is ignored (a project "
                               "chose the key that signed a new chain); a path follows the key-file rule"),
    "INSPEXIMUS_RECEIPT_KEY_FILE": (ENV_GUARD, "receipts.key_file", "not a file inside a git work tree or the store's "
                                    "project, and an unreadable one is ignored rather than stopping the server; a "
                                    "project named the key that signed a new store's chain, and stopped the server"),
    "INSPEXIMUS_WRITER_KEY": (CONFIG_ONLY, "writer.key", "the identity stamped as attested_key on the user's records"),
    "INSPEXIMUS_WRITER_KEY_FILE": (ENV_GUARD, "writer.key_file", "not a file inside a git work tree or the store's "
                                   "project"),
    "INSPEXIMUS_ACTOR": (CONFIG_ONLY, "actions.actor", "the actor named in the user's action ledger"),
    "INSPEXIMUS_ACTIONS": (ENV_SAFE, None, "opt-in: records tool calls in the ledger beside the store; adds only"),
    "INSPEXIMUS_SUPERSESSION": (CONFIG_ONLY, "store.supersession", "decides which value of a key is current"),
    "INSPEXIMUS_ECHO_GUARD": (ENV_GUARD, "guards.echo", "the environment may not switch the guard off"),
    "INSPEXIMUS_READ_GUARDS": (ENV_GUARD, "guards.read", "the environment may not switch the guards off"),
    "INSPEXIMUS_HEADS": (ENV_GUARD, "guards.heads", "the environment may not stop the head files"),
    "INSPEXIMUS_PII_DETECT": (CONFIG_ONLY, "pii.detect", "tags records that a later forget_pii sweep deletes"),
    "INSPEXIMUS_OBSERVE_RECALL": (CONFIG_ONLY, "recall.observe", "each recall writes into the user's store"),
    "INSPEXIMUS_KEEP_CONVERSION_BACKUP": (CONFIG_ONLY, "store.keep_conversion_backup",
                                          "keeps a full copy that an erasure does not reach"),
    "INSPEXIMUS_BUSY_TIMEOUT_S": (ENV_GUARD, "store.busy_timeout_s", "the environment may set 10 to 120 s: never lower, "
                                  "never without bound"),
    "INSPEXIMUS_SAVE_RETRIES": (ENV_GUARD, "store.save_retries", "the environment may set 2 to 20: never lower, never "
                                "without bound"),
    # C. which vectors it writes
    "INSPEXIMUS_EMBED_URL": (ENV_GUARD, None, "another host only when the user's config names it (3.16.x, F-12)"),
    "INSPEXIMUS_EMBED_KEY": (ENV_GUARD, None, "sent only to a host the user's config allows (F-12)"),
    "INSPEXIMUS_EMBED_MODEL": (ENV_GUARD, None, "an open embeds nothing (3.17.0); `reembed` replaces vectors of "
                               "another recipe only with --replace-recipe when the recipe came from the environment"),
    "INSPEXIMUS_NOMIC_PREFIX": (ENV_GUARD, None, "part of the recipe: the same rule as INSPEXIMUS_EMBED_MODEL"),
    "INSPEXIMUS_PERSIST_VECTORS": (ENV_SAFE, None, "both directions, from the environment: the approved 3.17.0 "
                                   "CHANGELOG tells users to set =0 in their own MCP entry, and off loses no data, it "
                                   "only stops keeping vectors that `reembed` rebuilds"),
    # D. read side only
    "INSPEXIMUS_MAX_K": (ENV_SAFE, None, "read side only"),
    "INSPEXIMUS_SNIPPET_CHARS": (ENV_SAFE, None, "read side only"),
    "INSPEXIMUS_READ_RESOLVER": (ENV_SAFE, None, "read side only"),
    "INSPEXIMUS_TRUST_SEEDS": (CONFIG_ONLY, "recall.trust_seeds", "a seed adds trust to every record its key signed"),
    # E. hook only, or the hook's own switches
    "INSPEXIMUS_ARCHIVE_AUTO": (ENV_GUARD, "archive.auto", "the environment may switch the archive off, never on, and "
                                "the user's config wins (the 3.16.3 F-9 effect through the environment)"),
    "INSPEXIMUS_DECISION_STORE": (ENV_GUARD, "hook.decision_store", "not a file inside a git work tree or the project, "
                                  "unless the user's config names it: its text reaches the model"),
    "INSPEXIMUS_DECISION_STORE_MAX_MB": (ENV_SAFE, None, "hook only, read side"),
    "INSPEXIMUS_EMBED_HOOKS": (CONFIG_ONLY, "embed.hooks", "sends record text to an embedder and writes vectors under "
                               "its recipe"),
    "INSPEXIMUS_NO_INJECT": (ENV_SAFE, None, "hook only: injects less"),
    "INSPEXIMUS_NO_NUDGE": (ENV_SAFE, None, "hook only: prints less"),
    "INSPEXIMUS_SESSION_DIGEST": (ENV_SAFE, None, "hook only"),
    "INSPEXIMUS_STAMP_AUTO": (ENV_SAFE, None, "hook only; the policy is the user's config"),
    "INSPEXIMUS_HOOK_FAST_EXIT": (ENV_SAFE, None, "hook only: process exit"),
    "INSPEXIMUS_AGENT_ID": (ENV_SAFE, None, "hook only: names the agent"),
    "INSPEXIMUS_RECEIPT_MAX_POINTERS": (ENV_SAFE, None, "hook only: the memory-index receipt text"),
    "INSPEXIMUS_SESSION_MAX_CHARS": (ENV_SAFE, None, "session digest size"),
    "INSPEXIMUS_SESSION_MAX_ITEMS": (ENV_SAFE, None, "session digest size"),
    "INSPEXIMUS_SESSION_MAX_SESSIONS": (ENV_SAFE, None, "session digest size"),
    "INSPEXIMUS_SESSION_SALIENCE": (ENV_SAFE, None, "session digest ranking"),
    # F. other process settings
    "INSPEXIMUS_LLM_URL": (ENV_SAFE, None, "the distiller; the server does not import it"),
    "INSPEXIMUS_LLM_KEY": (ENV_SAFE, None, "the distiller; the server does not import it"),
    "INSPEXIMUS_LLM_MODEL": (ENV_SAFE, None, "the distiller; the server does not import it"),
    "INSPEXIMUS_NO_UPDATE_CHECK": (ENV_SAFE, None, "skips a network call"),
    "INSPEXIMUS_PROBES_DIR": (ENV_SAFE, None, "compliance probes: read only"),
    "INSPEXIMUS_SERVICE_SECRET": (ENV_SAFE, None, "the HTTP service, not the MCP server or the hooks"),
    "INSPEXIMUS_WITNESS_SECRET": (ENV_SAFE, None, "the witness tool, not the MCP server or the hooks"),
    "INSPEXIMUS_RECEIPT_PUBKEY": (ENV_SAFE, None, "a verification pin: can only make verification stricter"),
    "INSPEXIMUS_REALIGN_MAX": (ENV_SAFE, None, "no longer read (3.17.0: an open realigns nothing)"),
}


def _env(var: str) -> str:
    return (os.environ.get(var) or "").strip()


def _cfg(key: str):
    from . import _userconfig
    return _userconfig.get(*key.split("."))


def _ignored(var: str, key: str) -> None:
    from . import _userconfig
    _userconfig.env_ignored(var, key)


def config_flag(var: str, default: bool = False) -> bool:
    """CONFIG_ONLY on/off: `true` or `false` in the user's config, else `default`. The environment is never read."""
    key = POLICY[var][1]
    v = _cfg(key)
    if isinstance(v, bool):
        return v
    if _env(var):
        _ignored(var, "%s to true or false" % key)
    return default


def config_choice(var: str, default: str, allowed) -> str:
    """CONFIG_ONLY string: one of `allowed` from the user's config, else `default`."""
    key = POLICY[var][1]
    v = _cfg(key)
    if isinstance(v, str) and v.strip().lower() in allowed:
        return v.strip().lower()
    if _env(var) and _env(var).lower() != default:
        _ignored(var, "%s to one of %s" % (key, ", ".join(sorted(allowed))))
    return default


def config_string(var: str):
    """CONFIG_ONLY string, or None."""
    key = POLICY[var][1]
    v = _cfg(key)
    if isinstance(v, str) and v.strip():
        return v.strip()
    if _env(var):
        _ignored(var, key)
    return None


_OFF = ("0", "off", "false", "no")


def guard_on(var: str) -> bool:
    """ENV_GUARD for a guard that is on by default: `false` in the user's config switches it off; the environment
    cannot (its `0` is ignored with the stderr line, any other value changes nothing)."""
    key = POLICY[var][1]
    if _cfg(key) is False:
        return False
    if _env(var).lower() in _OFF:
        _ignored(var, "%s to false" % key)
    return True


def at_least(var: str, default, cast, ceiling=None):
    """ENV_GUARD for a number with a safe floor: the user's config sets any value; the environment may raise it above
    `default`, up to `ceiling` (AUDIT-A EC-4: `inf` and 1e12 were accepted, so a write could wait without limit), and
    is ignored outside that range."""
    key = POLICY[var][1]
    v = _cfg(key)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return cast(v)
    raw = _env(var)
    if raw:
        try:
            x = cast(raw)
        except (TypeError, ValueError):
            return default
        if default <= x <= (x if ceiling is None else ceiling):
            return x
        _ignored(var, "%s (the environment may set it from %s to %s)" % (key, default, ceiling))
    return default


def switch_on_only(var: str, default: bool) -> bool:
    """ENV_GUARD for a setting whose `off` is the harmful direction: the user's config sets either; the environment may
    switch it on and never off."""
    key = POLICY[var][1]
    v = _cfg(key)
    if isinstance(v, bool):
        return v
    raw = _env(var).lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in _OFF and default:
        _ignored(var, "%s to false" % key)
    return default


def key_file(var: str, store_path=None):
    """ENV_GUARD for a key file. The user's config may name any file. The environment may name a file whose folder passes
    the F-13 rule for the key home (`_keyhome.refusal`): not inside a git work tree, not inside the project of
    `store_path`, and not inside the project the process runs in (a project delivered without .git, AUDIT-A EC-3). A
    repository controls the files inside it, so a key it ships is a key it chose. Returns (path or None, source)."""
    key = POLICY[var][1]
    v = _cfg(key)
    if isinstance(v, str) and v.strip():
        return v.strip(), "config " + key
    raw_v = _env(var)
    if not raw_v:
        return None, None
    from ._keyhome import refusal
    why = refusal(os.path.dirname(os.path.abspath(raw_v)), store_path)
    if why:
        _ignored(var, "%s (the environment may not name this key file: %s)" % (key, why))
        return None, None
    return raw_v, var


def project_name(raw: str, cwd=None):
    """ENV_GUARD for INSPEXIMUS_PROJECT: `auto`, the name of the folder the process runs in, or a name listed in the
    user's config `project.names` is honoured; anything else is ignored (the store is then unscoped, as when the
    variable is unset). Returns the value to use, or None."""
    from pathlib import Path
    here = Path(cwd or os.getcwd()).resolve().name
    names = _cfg("project.names")
    names = [str(n) for n in names] if isinstance(names, list) else []
    if raw == "auto" or raw == here or raw in names:
        return raw
    _ignored("INSPEXIMUS_PROJECT", "project.names to a list that includes %r (or run from a folder named %r)"
             % (raw, raw))
    return None


# ── THE ONE READ (AUDIT-A EC-5) ─────────────────────────────────────────────────────────────────────────────────
# Every INSPEXIMUS_* value the package reads comes through this module. `tests/test_every_environment_variable_has_a_
# policy.py` fails on a direct read of such a name anywhere else (os.environ, os.getenv, a mapping's `.get`), on a name
# built at run time, and on a scan of the environment by prefix, so a new reader cannot skip the rule by its shape.

def raw(var: str, default=None, env=None):
    """The value of an env-safe or env-with-guard variable, as the environment holds it, or `default` when it is unset.
    The caller applies the guard its POLICY entry names (the vetted resolver for INSPEXIMUS_PATH, F-13 for the key home).
    A config-only variable is never read here: it raises, because the read would be the defect."""
    if not var.startswith("INSPEXIMUS_"):
        rule = ENV_SAFE                                 # another program's variable; an unlisted name of ours raises
    else:
        rule = POLICY[var][0]
    if rule == CONFIG_ONLY:
        raise ValueError("%s is config-only; read %s from the user's config" % (var, POLICY[var][1]))
    src = os.environ if env is None else env
    v = src.get(var)
    return default if v is None else v


def host(var: str, mapping):
    """A value from a host's own configuration entry (an agent's MCP `env` block in the user's host config file), which
    the user wrote and a project's settings do not reach. Any rule; never the process environment."""
    if mapping is os.environ:
        raise ValueError("host() reads a host config entry, not the process environment")
    v = (mapping or {}).get(var)
    return "" if v is None else str(v)


def notice_if_set(var: str, when=None) -> None:
    """For a config-only variable: say once that the environment's value is ignored, when it is set (and `when(value)`
    holds). Reads nothing for the caller."""
    v = _env(var)
    if v and (when is None or when(v.lower())):
        _ignored(var, POLICY[var][1])


def snapshot(env=None) -> dict:
    """Every INSPEXIMUS_* entry of `env` (default: the process environment), for a fingerprint or a child's environment.
    The one prefix scan the package has."""
    src = os.environ if env is None else env
    return {k: v for k, v in src.items() if k.startswith("INSPEXIMUS_")}


def switch_off_only(var: str, config_value):
    """ENV_GUARD for a switch whose `on` is the harmful direction (AUDIT-A EC-1). The user's config wins when it says
    either; otherwise the environment may switch it off and is ignored, with the stderr line, when it asks for on.
    Returns True, False, or None (nothing said)."""
    if isinstance(config_value, bool):
        return config_value
    v = _env(var).lower()
    if v in _OFF:
        return False
    if v in ("1", "true", "yes", "on"):
        _ignored(var, POLICY[var][1] + " to true")
    return None


def receipts_from_env() -> bool:
    """INSPEXIMUS_RECEIPTS=1 starts a receipt chain only when the user's config names a signing key (`receipts.key_file`
    or `receipts.key`); `receipts.enabled: true` starts one in any case (AUDIT-A EC-2). A project started an unsigned
    chain on a store with none, and the user's key then could not sign that store without the chain reading as
    tampered."""
    if _cfg("receipts.enabled") is True:
        return True
    if _env("INSPEXIMUS_RECEIPTS").lower() in ("1", "true", "yes", "on"):
        kf, kk = _cfg("receipts.key_file"), _cfg("receipts.key")
        if (isinstance(kf, str) and kf.strip()) or (isinstance(kk, str) and kk.strip()):
            return True
        _ignored("INSPEXIMUS_RECEIPTS", "receipts.enabled to true, or receipts.key_file (the environment starts a chain "
                 "only when your config names a signing key)")
    return False


def trust_seeds() -> set:
    """The trust root, from the user's config `recall.trust_seeds` (a list, or a comma-separated string) only (AUDIT-A
    EC-6): a seed adds trust to every record its key signed, so a project must not name one."""
    v = _cfg("recall.trust_seeds")
    if isinstance(v, str):
        v = v.split(",")
    if isinstance(v, list):
        return {str(s).strip() for s in v if str(s).strip()}
    if _env("INSPEXIMUS_TRUST_SEEDS"):
        _ignored("INSPEXIMUS_TRUST_SEEDS", "recall.trust_seeds")
    return set()


def receipt_key_value():
    """The receipt key the user gave as a value: the user's config `receipts.key` (64 hex), else None. A 64-hex
    INSPEXIMUS_RECEIPT_KEY in the environment is ignored with the stderr line (Builder EC-7): it signed a new store's
    chain with a key a project chose. A path in that variable is a key file and follows `key_file`'s rule."""
    v = _cfg("receipts.key")
    if isinstance(v, str) and _hexkey(v.strip()):
        return v.strip().lower()
    e = _env("INSPEXIMUS_RECEIPT_KEY")
    if e and _hexkey(e):
        _ignored("INSPEXIMUS_RECEIPT_KEY", "receipts.key")
    return None


def decision_store() -> str:
    """INSPEXIMUS_DECISION_STORE (AUDIT-A EC-6): the user's config `hook.decision_store` names any file; the environment
    a file that is not inside a git work tree or the project the hook runs in, because the hook puts its text into the
    model's context. Returns the path or ''."""
    v = _cfg("hook.decision_store")
    if isinstance(v, str) and v.strip():
        return v.strip()
    raw_v = _env("INSPEXIMUS_DECISION_STORE")
    if not raw_v:
        return ""
    from ._keyhome import refusal
    why = refusal(os.path.dirname(os.path.abspath(raw_v)))
    if why:
        _ignored("INSPEXIMUS_DECISION_STORE", "hook.decision_store (the environment may not name this file: %s)" % why)
        return ""
    return raw_v


def _hexkey(v: str) -> bool:
    return len(v) == 64 and all(c in "0123456789abcdefABCDEF" for c in v)


def receipt_key_path(store_path=None):
    """INSPEXIMUS_RECEIPT_KEY when it holds a path rather than a 64-hex key: the path when its folder passes the
    key-file rule (`key_file`), else None with the stderr line. A path that does not exist is ignored the same way
    (Builder EC-7): a project named a missing file to stop every write. Returns None for a hex value or no value."""
    e = _env("INSPEXIMUS_RECEIPT_KEY")
    if not e or _hexkey(e):
        return None
    from ._keyhome import refusal
    why = refusal(os.path.dirname(os.path.abspath(e)), store_path)
    if not why and not os.path.isfile(e):
        why = "no such file"
    if why:
        _ignored("INSPEXIMUS_RECEIPT_KEY", "receipts.key_file (the environment may not name this key file: %s)" % why)
        return None
    return e
