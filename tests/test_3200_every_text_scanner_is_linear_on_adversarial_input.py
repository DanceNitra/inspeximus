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
"""
import os
import re
import sys
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


SHAPES = {
    "a-": _rep("a-"), "token.": _rep("token."), "token-": _rep("token-"), "dashes": "-" * N, "dots": "." * N,
    "equals": "=" * N, "colons": ":" * N, "spaces": " " * N, "token=+a": "token=" + "a" * N,
    "token+spaces+x": "token" + " " * N + "x", "eyJ.": _rep("eyJ."), "jwt-ish": _rep("eyJaaaaaaaa.eyJaaaaaaaa."),
    "BEGIN+A": "-----BEGIN " + "A" * N, "BEGIN X KEY": _rep("-----BEGIN X PRIVATE KEY-----\n"), "sk-": _rep("sk-"),
    "bearer": _rep("bearer "), "a://b:": _rep("a://b:"), "letters then ://": "a" * N + "://u:",
    "x://u:p@": _rep("x://u:p@"), "--token": _rep("--token "), "api_key_": _rep("api_key_"),
    "a. then token": _rep("a.") + "token", "e-acute run + token=": "é" * N + "token=abcdefgh",
    "email-ish": _rep("x@ex.io "), "at run": _rep("a@"), "a@b.": _rep("a@b."), "digit spaces": _rep("1 "),
    "digit dots": _rep("1."), "card-ish": _rep("4111 "), "plus one": _rep("+1 "), "a.-": _rep("a.-"),
    "ip-ish": _rep("1.1.1."), "ssn-ish": _rep("123-45-"), "parens": _rep("(1) "), "ignore previous": _rep(
        "ignore all previous instructions "), "you are now": _rep("you are now "), "send to": _rep("send it to https://x "),
    "do not tell": _rep("do not tell "), "as an ai": _rep("as an ai "), "i think": _rep("i think "),
    "word repeat": _rep("alpha "), "no spaces": "a" * N, "unicode digits": _rep("٣"), "zwj": _rep("tok\u200den= "),
}

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


def test_control_the_harness_flags_the_email_pattern_f14_replaced():
    old = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
    dt, shape = _slowest(lambda t: list(old.finditer(t)), {"a-": SHAPES["a-"]})
    assert dt > BUDGET, "CONTROL: the harness did not see the quadratic pattern (%.2f s)" % dt
