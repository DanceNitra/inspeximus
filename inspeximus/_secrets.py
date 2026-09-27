"""Secret-shaped text, found and masked before an automatic capture stores it.

WHY THIS EXISTS (3.14.2). The Claude Code and Codex hooks store the first 200 characters of every
Bash command and an excerpt of every Edit/Write, and two injection paths print stored text back into
the model's context. So `export OPENAI_API_KEY=sk-...` or a write to `.env` landed in the store
verbatim and was re-injected into later prompts -- and since 3.14.0 `install --all` shares that store
with every agent on the machine. `redact_pii` did not help: it knows SSNs, emails, cards, IPv4 and
phone numbers, and no key shapes at all.

REDACT AT CAPTURE, NEVER AT RECALL. A secret that reached the store is on disk, in every backup of
it and in every peer that merges it; masking it on the way out would leave all of that in place. The
one place that can keep a secret out of the store is the write.

MASK BEFORE YOU CUT. The hook truncates what it stores. Truncating first and masking second leaves the
head of a key at the cut, too short for any pattern to recognise, so every caller here masks the WHOLE
string and only then takes its excerpt.

What it recognises, in order, each replaced by `[REDACTED:<kind>]`:

  * private-key blocks (PEM) and JWTs;
  * provider key prefixes: OpenAI and Anthropic `sk-`, Stripe `sk_live_`/`rk_`, GitHub `ghp_` `gho_`
    `ghu_` `ghs_` `ghr_` `github_pat_`, GitLab `glpat-`, AWS `AKIA`/`ASIA`, Slack `xox?-` and hooks,
    Google `AIza`, Hugging Face `hf_`, npm `npm_`;
  * credentials in a URL (`scheme://user:PASSWORD@host`) and `Bearer <token>`;
  * an assignment or a flag whose NAME says it holds a secret (`*KEY*`, `*TOKEN*`, `*SECRET*`,
    `*PASSWORD*`, `*PASSWD*`, `*PWD*`, `*CREDENTIAL*`, `*AUTH*`), when the value is at least 8
    characters and is not a reference such as `$VAR`, `%VAR%`, `${VAR}` or `<placeholder>`.

It is a HEURISTIC and says so: a secret with no recognisable prefix under an innocent name
(`PORT_B=hunter2hunter2`) passes. The name rule is deliberately generous, because a false positive
costs one masked value in a log of mechanics, and a false negative costs a key in every agent.
"""
from __future__ import annotations

import os
import re

#: (kind, pattern). Order matters: the whole-block shapes first, so a PEM body is not re-matched
#: piecemeal by the assignment rule.
_PATTERNS = (
    ("private_key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("api_key", re.compile(
        r"(?<![A-Za-z0-9])(?:"
        r"sk-[A-Za-z0-9_-]{20,}"                      # OpenAI, Anthropic (sk-ant-), project keys
        r"|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"  # Stripe secret and restricted keys
        r"|gh[pousr]_[A-Za-z0-9]{30,}"                # GitHub tokens
        r"|github_pat_[A-Za-z0-9_]{30,}"
        r"|glpat-[A-Za-z0-9_-]{20,}"                  # GitLab
        r"|(?:AKIA|ASIA)[0-9A-Z]{16}"                 # AWS access key id
        r"|xox[abposr]-[A-Za-z0-9-]{10,}"             # Slack
        r"|AIza[0-9A-Za-z_-]{35}"                     # Google API key
        r"|hf_[A-Za-z0-9]{30,}"                       # Hugging Face
        r"|npm_[A-Za-z0-9]{36}"                       # npm
        r")")),
    ("webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{20,}")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*")),
)

#: A NAME that says its value is a secret. `auth` alone would catch `author`, so it needs a
#: separator or the end of the name after it.
_SECRET_NAME = r"[A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|passw(?:or)?d|pwd|credential|private[_-]?key|access[_-]?key|auth(?![a-z]))[A-Za-z0-9_.-]*"
#: A value that is a reference to a secret rather than the secret itself.
_REFERENCE = r"(?![$%<{]|\[REDACTED)"
_ASSIGN = re.compile(
    r"(?i)(?P<name>\b" + _SECRET_NAME + r")(?P<sep>\s*[:=]\s*)(?P<q>['\"]?)" + _REFERENCE
    + r"(?P<val>[^\s'\"&;|,]{8,})")
_FLAG = re.compile(
    r"(?i)(?P<name>--?" + _SECRET_NAME + r")(?P<sep>[= ]\s*)(?P<q>['\"]?)" + _REFERENCE
    + r"(?P<val>[^\s'\"&;|,]{8,})")
_URL_CRED = re.compile(r"(?P<pre>[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:)" + _REFERENCE + r"(?P<val>[^/\s@]{3,})(?P<post>@)")


def _mask(kind: str) -> str:
    return f"[REDACTED:{kind}]"


def redact_secrets(text):
    """Return (masked_text, {kind: count}). Non-strings come back unchanged with an empty count."""
    if not isinstance(text, str) or not text:
        return text, {}
    counts: dict = {}

    def _count(kind, n):
        if n:
            counts[kind] = counts.get(kind, 0) + n

    for kind, pat in _PATTERNS:
        text, n = pat.subn(_mask(kind), text)
        _count(kind, n)
    text, n = _URL_CRED.subn(lambda m: m.group("pre") + _mask("url_password") + m.group("post"), text)
    _count("url_password", n)
    for pat in (_FLAG, _ASSIGN):
        text, n = pat.subn(lambda m: m.group("name") + m.group("sep") + m.group("q") + _mask("assignment"),
                           text)
        _count("assignment", n)
    return text, counts


def redact_value(value):
    """Mask every string inside `value` (str, list, tuple or dict, any depth). Returns (value, counts)."""
    counts: dict = {}

    def _add(c):
        for k, v in c.items():
            counts[k] = counts.get(k, 0) + v

    def _walk(v):
        if isinstance(v, str):
            out, c = redact_secrets(v)
            _add(c)
            return out
        if isinstance(v, dict):
            return {_walk(k) if isinstance(k, str) else k: _walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return type(v)(_walk(x) for x in v)
        return v

    return _walk(value), counts


_DETECT = tuple(p for _, p in _PATTERNS) + (_URL_CRED, _FLAG, _ASSIGN)
#: Every pattern above needs one of these substrings (lowercased), so a string with none of them
#: cannot match and skips the regexes. Kept beside the patterns: a new pattern needs its trigger.
_TRIGGERS = ("sk-", "sk_", "rk_", "gh", "glpat", "akia", "asia", "xox", "aiza", "hf_", "npm_",
             "hooks.slack", "bearer", "://", "begin", "eyj", "key", "secret", "token", "passw", "pwd",
             "credential", "auth")


def has_secret(value) -> bool:
    """True when `value` (any nesting) holds at least one secret-shaped string.

    Searches and stops at the first hit rather than masking: the one-time scan of a store calls
    this once per record, and on 30,000 records the masking version cost 1.5 to 1.9 s."""
    if isinstance(value, str):
        low = value.lower()
        if not any(t in low for t in _TRIGGERS):
            return False
        return any(p.search(value) for p in _DETECT)
    if isinstance(value, dict):
        return any(has_secret(k) or has_secret(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(has_secret(x) for x in value)
    return False


#: Files whose purpose is holding credentials. Their content is never excerpted, and nothing derived
#: from it is stored: a hash would let whoever holds the store confirm a guessed file offline.
_SECRET_FILE_NAMES = (".netrc", ".pgpass", ".pypirc", ".npmrc")
_SECRET_FILE_PREFIXES = (".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials")
_SECRET_FILE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


def is_secrets_file(path) -> bool:
    """True for `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, SSH private keys, `credentials*` and the
    usual per-user credential files. Matched on the file name only, case-insensitively."""
    if not path:
        return False
    name = os.path.basename(str(path).replace("\\", "/")).lower()
    if name.endswith(".pub"):                  # the public half of an SSH key is meant to be shared
        return False
    return (name in _SECRET_FILE_NAMES or name.startswith(_SECRET_FILE_PREFIXES)
            or name.endswith(_SECRET_FILE_SUFFIXES))

