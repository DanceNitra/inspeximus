"""3.20 (AUDIT-A after F9 and F14): every public text scanner stays inside a time budget on adversarial input.

THE CLASS. Two regex entry points were super-linear on text an agent writes: has_secret("token." * 2000) took 85 s in
3.19.0 (F9, fixed in 3.19.1), and redact_pii("a-" * 64000) took 5.8 s in 3.19.1 (F14, fixed in 3.20). AUDIT-A found
the second by running the F9 ladder over the other entry points (dr/ss3/sec_linear.py). This test runs that check in
CI so the class cannot come back unnoticed.

HOW IT DECIDES. Each scanner is called once on each shape at 64,000 characters, and each call must finish inside 1.5
s. The linear scanners take milliseconds there; the two defects above took 5 to 85 s. An absolute budget is used
rather than a ratio between sizes, because ratios of millisecond timings are noise on a shared CI runner.

THE CONTROL. The e-mail pattern F14 replaced runs through the same harness and must be flagged, so a harness that
cannot see a quadratic scan fails here instead of passing everything.

THE LIST IS DISCOVERED, NOT WRITTEN (3.20, AUDIT-A N-4). The hand-written SCANNERS list below missed two quadratic
functions: derive_key and regex_extractor took 1.5 s on 4,000 characters of "-" and about 23 s on 16,000 (AUDIT-A,
dr/ss5/scan_all_text_fns.py). So every function in the package that takes exactly one required text argument, found
by introspection as AUDIT-A's sweep does, also runs on every shape at 16,000 characters inside the same budget. A
control requires the discovery to find at least 60 functions, including the known scanners, so a discovery that finds
nothing cannot pass.
"""
import importlib
import inspect
import os
import pkgutil
import re
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import _secrets  # noqa: E402
from inspeximus import core  # noqa: E402

N = 64000
BUDGET = 1.5


def _rep(unit, n=N):
    return (unit * (n // max(1, len(unit)) + 1))[:n]


def _shapes(n):
    return {
        "a-": _rep("a-", n), "token.": _rep("token.", n), "token-": _rep("token-", n), "dashes": "-" * n, "dots": "." * n,
        "equals": "=" * n, "colons": ":" * n, "spaces": " " * n, "token=+a": "token=" + "a" * n,
        "token+spaces+x": "token" + " " * n + "x", "eyJ.": _rep("eyJ.", n), "jwt-ish": _rep("eyJaaaaaaaa.eyJaaaaaaaa.", n),
        "BEGIN+A": "-----BEGIN " + "A" * n, "BEGIN X KEY": _rep("-----BEGIN X PRIVATE KEY-----\n", n), "sk-": _rep("sk-", n),
        "bearer": _rep("bearer ", n), "a://b:": _rep("a://b:", n), "letters then ://": "a" * n + "://u:",
        "x://u:p@": _rep("x://u:p@", n), "--token": _rep("--token ", n), "api_key_": _rep("api_key_", n),
        "a. then token": _rep("a.", n) + "token", "e-acute run + token=": "é" * n + "token=abcdefgh",
        "email-ish": _rep("x@ex.io ", n), "at run": _rep("a@", n), "a@b.": _rep("a@b.", n), "digit spaces": _rep("1 ", n),
        "digit dots": _rep("1.", n), "card-ish": _rep("4111 ", n), "plus one": _rep("+1 ", n), "a.-": _rep("a.-", n),
        "ip-ish": _rep("1.1.1.", n), "ssn-ish": _rep("123-45-", n), "parens": _rep("(1) ", n), "ignore previous": _rep(
            "ignore all previous instructions ", n), "you are now": _rep("you are now ", n), "send to": _rep("send it to https://x ", n),
        "do not tell": _rep("do not tell ", n), "as an ai": _rep("as an ai ", n), "i think": _rep("i think ", n),
        "word repeat": _rep("alpha ", n), "no spaces": "a" * n, "unicode digits": _rep("٣", n), "zwj": _rep("tok\u200den= ", n),
    }


SHAPES = _shapes(N)

SCANNERS = {
    "has_secret": _secrets.has_secret,
    "redact_secrets": _secrets.redact_secrets,
    "detect_pii": core.detect_pii,
    "redact_pii": core.redact_pii,
    "_instruction_shape": core._instruction_shape,
    "_stuffing": core._stuffing,
    "_tokens": core._tokens,
    "_token_counts": core._token_counts,
    "check_self_narration": core.Inspeximus.check_self_narration,
    "_value_clash": lambda t: core._value_clash(t, t[::-1]),
    "_negation_clash": lambda t: core._negation_clash(t, "not " + t),
}


def _slowest(fn, shapes):
    worst = (0.0, None)
    for name, text in shapes.items():
        t0 = time.perf_counter()
        fn(text)
        dt = time.perf_counter() - t0
        if dt > worst[0]:
            worst = (dt, name)
        if dt > BUDGET:
            return dt, name
    return worst


@pytest.mark.parametrize("scanner", sorted(SCANNERS))
def test_a_text_scanner_stays_inside_the_budget_on_every_shape(scanner):
    dt, shape = _slowest(SCANNERS[scanner], SHAPES)
    assert dt <= BUDGET, "%s took %.2f s on %r at %d characters (budget %.1f s)" % (scanner, dt, shape, N, BUDGET)


# -- the discovered list (N-4) ------------------------------------------------------------------------------------
DISCOVERED_N = 16000
# Modules that serve, install or launch rather than scan text; their one-argument functions open sockets or files.
SKIP_MODULES = ("cli", "mcp_server", "install", "install_all", "demo", "hookd", "scrapi", "witness_server", "browser",
                "witness_checkpoint", "_launch", "_update", "_serve")
TEXT_PARAMS = {"text", "query", "q", "s", "string", "value", "prompt", "content", "line", "claim", "statement",
               "message", "source", "raw", "sentence", "doc", "body", "subject", "snippet", "blob", "name", "label",
               "x", "t"}
# Methods that write, erase or change state: they are not scanners, and calling them on adversarial text changes
# the store under test rather than measuring a scan.
SKIP_NAMES = ("remember", "forget", "recall", "observe", "retire", "revert", "sleep", "consolidate", "_save",
              "flush", "reload", "refresh", "shred", "vacuum", "close", "open", "set_", "record_", "_append", "_emit",
              "grant", "revoke", "write", "import", "apply", "install", "release", "stamp", "credit", "slash",
              "restore", "ratify", "confirm", "discard", "promote", "supersede", "merge", "subscribe", "publish",
              "enable", "_persist", "_flush")


def _discover(store):
    import inspeximus
    cands = []
    for mi in pkgutil.walk_packages(inspeximus.__path__, "inspeximus."):
        if (mi.name.split(".")[-1] in SKIP_MODULES or ".integrations" in mi.name or ".probes" in mi.name):
            continue
        try:
            mod = importlib.import_module(mi.name)
        except Exception:                                  # noqa: BLE001 - an optional dependency is missing
            continue
        for n, f in inspect.getmembers(mod, inspect.isfunction):
            if f.__module__ == mod.__name__:
                cands.append((mod.__name__ + "." + n, f))
    for n, f in inspect.getmembers(core.Inspeximus):
        if not n.startswith("__") and inspect.isfunction(f):
            cands.append(("Inspeximus." + n, getattr(store, n)))
    found = {}
    for name, f in cands:
        try:
            ps = [p for p in inspect.signature(f).parameters.values() if p.name != "self"]
        except (ValueError, TypeError):
            continue
        req = [p for p in ps if p.default is inspect.Parameter.empty
               and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if len(req) == 1 and req[0].name.lower() in TEXT_PARAMS and not any(k in name.split(".")[-1] for k in SKIP_NAMES):
            found[name] = f
    return found


def _timed(fn, text, limit):
    """Seconds `fn(text)` took, or `limit` when it was still running then. An exception is a result, not a slowness."""
    out = {}

    def run():
        t0 = time.perf_counter()
        try:
            fn(text)
        except Exception:                                  # noqa: BLE001
            pass
        out["t"] = time.perf_counter() - t0
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(limit)
    return out.get("t", limit)


def test_every_discovered_single_text_function_stays_inside_the_budget(tmp_path):
    found = _discover(core.Inspeximus(str(tmp_path / "s.db")))
    for must in ("inspeximus.core.derive_key", "inspeximus.core.regex_extractor", "inspeximus.core.detect_pii",
                 "inspeximus.core.redact_pii", "inspeximus._secrets.has_secret"):
        assert must in found, "CONTROL: the discovery did not find " + must
    assert len(found) >= 60, "CONTROL: the discovery found only %d functions" % len(found)
    shapes = _shapes(DISCOVERED_N)
    slow = []
    for name in sorted(found):
        for shape, text in shapes.items():
            dt = _timed(found[name], text, 4 * BUDGET)
            if dt > BUDGET:
                slow.append("%s %.2f s on %r" % (name, dt, shape))
                break
    assert not slow, "over %.1f s at %d characters:\n  " % (BUDGET, DISCOVERED_N) + "\n  ".join(slow)


def test_control_the_harness_flags_the_email_pattern_f14_replaced():
    old = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
    dt, shape = _slowest(lambda t: list(old.finditer(t)), {"a-": SHAPES["a-"]})
    assert dt > BUDGET, "CONTROL: the harness did not see the quadratic pattern (%.2f s)" % dt
