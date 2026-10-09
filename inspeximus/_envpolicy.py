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
    "INSPEXIMUS_HOOK_DAEMON": (CONFIG_ONLY, "hook.daemon", "holds the user's store in memory between prompts"),
    "INSPEXIMUS_HOOK_DAEMON_NOSTART": (ENV_SAFE, None, "only keeps a daemon from starting"),
    "INSPEXIMUS_HOOK_DAEMON_TRACE": (ENV_SAFE, None, "one stderr line per prompt hook"),
    "INSPEXIMUS_RECALL_INDEX": (ENV_SAFE, None, "the index serves the records a scan serves; off only slows recall"),
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


def host(var: str, entry):
    """A value from a host's own configuration ENTRY (an agent's MCP server entry in the user's host config file, whose
    `env` block the user wrote and a project's settings do not reach). Any rule; never the process environment: an
    entry that is the environment, or whose `env` is the environment or a copy of it, raises (AUDIT-A S-2)."""
    def _is_env(m):
        if m is os.environ or isinstance(m, (type(os.environ), _EnvCopy)):
            return True
        if not isinstance(m, dict) or not m:
            return False
        return len(m) == len(os.environ) and all(os.environ.get(k) == v for k, v in m.items())

    def _mirrors_env(m):
        # A FILTERED COPY (AUDIT-A delta S-2): every INSPEXIMUS_* entry it has is the environment's own value. Its values
        # are refused (read as absent) rather than raised on, because a host entry the user wrote can match the shell it
        # runs from by coincidence, and `install --all` must still run there.
        ours = [(k, v) for k, v in m.items() if _ours(k)]
        return bool(ours) and all(os.environ.get(k) == v for k, v in ours)
    if _is_env(entry):
        raise ValueError("host() reads a host config entry, not the process environment")
    env = (entry or {}).get("env") if isinstance(entry, dict) else None
    if env is None:
        return ""
    if _is_env(env) or not isinstance(env, dict):
        raise ValueError("host() reads the `env` block of a host config entry, not the process environment")
    if _mirrors_env(env):
        return ""
    v = env.get(var)
    return "" if v is None else str(v)


def notice_if_set(var: str, when=None) -> None:
    """For a config-only variable: say once that the environment's value is ignored, when it is set (and `when(value)`
    holds). Reads nothing for the caller."""
    v = _env(var)
    if v and (when is None or when(v.lower())):
        _ignored(var, POLICY[var][1])


class _EnvCopy(dict):
    """A copy of this process's environment, or part of it. host() refuses one (AUDIT-A delta S-2), so a copy cannot
    pass for a host's own config entry."""


def snapshot(env=None, without=()) -> dict:
    """Every INSPEXIMUS_* entry of `env` (default: the process environment) except the names in `without`, for a
    fingerprint. The one prefix scan the package has. The copy is an `_EnvCopy`, which host() refuses."""
    src = os.environ if env is None else env
    return _EnvCopy((k, v) for k, v in src.items() if k.startswith("INSPEXIMUS_") and k not in without)


#: The INSPEXIMUS_* variables whose value names a file or a folder (the prompt daemon compares them with the hook's,
#: AUDIT-A E-1). Every variable the package reads as a path is here; `test_every_path_variable_is_listed` fails when one
#: read with a filesystem call is not.
PATH_VARS = ("INSPEXIMUS_PATH", "INSPEXIMUS_DECISION_STORE", "INSPEXIMUS_KEY_HOME", "INSPEXIMUS_RECEIPT_KEY",
             "INSPEXIMUS_RECEIPT_KEY_FILE", "INSPEXIMUS_WRITER_KEY_FILE", "INSPEXIMUS_PROBES_DIR",
             "INSPEXIMUS_CODING_STORE")


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
        # A signing key is a key file, or a 64-hex key: any other string in receipts.key signs nothing (AUDIT-A S-3).
        if (isinstance(kf, str) and kf.strip()) or (isinstance(kk, str) and _hexkey(kk.strip())):
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


# ── THE ONLY MODULE THAT TOUCHES THE ENVIRONMENT (AUDIT-A S-1) ───────────────────────────────────────────────────
# A scan of read shapes cannot be complete (an alias `E = os.environ`, a loop over `items()`, a copy, `expandvars`,
# `environb`, a name built from pieces). So the rule is inverted: outside this module no shipped module touches
# os.environ, os.environb, os.getenv, os.putenv, os.unsetenv or os.path.expandvars at all, under any name, and
# `tests/_env_read_scan.py` checks exactly that. Other programs' variables, a child process's environment and the few
# writes go through the functions below, which refuse an INSPEXIMUS_* name where it would bypass its rule.

def _ours(name: str) -> bool:
    return str(name).upper().startswith("INSPEXIMUS_")


def other(var: str, default=None):
    """Another program's variable (APPDATA, PATH, CODEX_HOME, PYTHONIOENCODING, ...). An INSPEXIMUS_* name raises:
    read it through `raw` or the rule helper its POLICY entry names."""
    if _ours(var):
        raise ValueError("%s is ours; read it through _envpolicy.raw or its rule helper" % var)
    v = os.environ.get(var)
    return default if v is None else v


def other_names(prefix: str) -> list:
    """The names in the environment that start with `prefix`, for a prefix that is not ours (CLAUDE_CODE_)."""
    if _ours(prefix) or "INSPEXIMUS_".startswith(str(prefix).upper()):
        raise ValueError("a scan by %r would include INSPEXIMUS_* names" % prefix)
    return [k for k in os.environ if k.startswith(prefix)]


def child_env(keep=None) -> dict:
    """A copy of this process's environment for a child process that is OURS (an inspeximus process that applies its
    own rule to the INSPEXIMUS_* entries it inherits), with only the names `keep(name)` accepts when it is given. A
    `keep` that accepts an INSPEXIMUS_* name raises (AUDIT-A delta S-2): selecting our names is a read of them. Another
    program gets `tool_env`, which carries none of them."""
    out = _EnvCopy((k, v) for k, v in os.environ.items() if keep is None or keep(k))
    if keep is not None and any(_ours(k) for k in out):
        raise ValueError("child_env(keep=...) selected INSPEXIMUS_* names; a child that needs them inherits them all")
    return out


#: What another program needs from this process's environment to start and find its files (AUDIT-A delta, children).
TOOL_ENV_KEEP = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "HOME", "USERPROFILE", "TEMP",
                 "TMP", "TMPDIR", "LANG", "LC_ALL")
#: What a package installer (uv, uvx, pip) also needs: its caches and settings, and the network's proxies and CAs.
INSTALLER_ENV_KEEP = ("APPDATA", "LOCALAPPDATA", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "HTTP_PROXY",
                      "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy",
                      "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
INSTALLER_ENV_PREFIXES = ("UV_", "PIP_")


def tool_env(keep=(), prefixes=()) -> dict:
    """The environment for another program (git, openssl, uv, pip, Hermes' interpreter): TOOL_ENV_KEEP, the names in
    `keep`, and the names that start with one of `prefixes`, from this process's environment. Never an INSPEXIMUS_*
    name: a key or a secret (INSPEXIMUS_EMBED_KEY, INSPEXIMUS_SERVICE_SECRET) is ours, and another program has no use
    for it."""
    names = {n.upper() for n in TOOL_ENV_KEEP + tuple(keep)}
    out = _EnvCopy()
    for k, v in os.environ.items():
        if _ours(k):
            continue
        if k.upper() in names or any(k.startswith(p) for p in prefixes):
            out[k] = v
    return out


def set_for_this_process(var: str, value) -> None:
    """Set a variable for this process and its children (a write, never a read). `None` removes it."""
    if value is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = str(value)
