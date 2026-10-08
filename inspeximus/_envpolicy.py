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
    "INSPEXIMUS_RECEIPTS": (ENV_SAFE, None, "opt-in: starts a sidecar on a store that has none; changes no record"),
    "INSPEXIMUS_RECEIPT_KEY": (ENV_SAFE, None, "a flag: the key is the one in the user's key home"),
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
    "INSPEXIMUS_BUSY_TIMEOUT_S": (ENV_GUARD, "store.busy_timeout_s", "the environment may raise it, never lower it"),
    "INSPEXIMUS_SAVE_RETRIES": (ENV_GUARD, "store.save_retries", "the environment may raise it, never lower it"),
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
    "INSPEXIMUS_TRUST_SEEDS": (ENV_SAFE, None, "read side only: narrows what trusted_only returns"),
    # E. hook only, or the hook's own switches
    "INSPEXIMUS_ARCHIVE_AUTO": (ENV_SAFE, None, "hook only; the archive policy is the user's config (3.16.x)"),
    "INSPEXIMUS_DECISION_STORE": (ENV_SAFE, None, "hook only, read-only"),
    "INSPEXIMUS_DECISION_STORE_MAX_MB": (ENV_SAFE, None, "hook only, read side"),
    "INSPEXIMUS_EMBED_HOOKS": (ENV_SAFE, None, "hook only; embedding in hooks is the user's config (embed.hooks)"),
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


def at_least(var: str, default, cast):
    """ENV_GUARD for a number with a safe floor: the user's config sets any value; the environment may raise it above
    `default` and is ignored below it."""
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
        if x >= default:
            return x
        _ignored(var, "%s (the environment may raise it above %s, never lower it)" % (key, default))
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
    """ENV_GUARD for a key file. The user's config may name any file. The environment may name a file that is not inside
    a git work tree and not inside the project of `store_path`: a repository controls the files inside it, so a key it
    ships is a key it chose (the F-13 rule for INSPEXIMUS_KEY_HOME, applied to the file's folder). Returns
    (path or None, source), where source is "config <key>", the variable's name, or None."""
    key = POLICY[var][1]
    v = _cfg(key)
    if isinstance(v, str) and v.strip():
        return v.strip(), "config " + key
    raw = _env(var)
    if not raw:
        return None, None
    from ._keyhome import _git_work_tree, _inside, _norm, _project_of
    folder = _norm(os.path.dirname(os.path.abspath(raw)))
    tree = _git_work_tree(folder)
    proj = _project_of(store_path) if store_path else None
    if tree or (proj and _inside(folder, proj)):
        _ignored(var, "%s (the environment may not name a key file inside %s)" % (key, tree or proj))
        return None, None
    return raw, var


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
