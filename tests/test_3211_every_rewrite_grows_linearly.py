"""Every 3.21.1 rewrite grows linearly: 4x the input never takes more than 8x the time (AUDIT-A R2-3).

An absolute bound cannot see a slowdown that only copies memory. A variant of _PipeThen that copies the text's tail at
every pipe gives the same answers and stays under every bound at 160 KB (0.055 s), yet is quadratic: 0.139 s at 320 KB,
up to 1.1 s at 640 KB, and it passed all 85 tests of round 2. So each rewrite is timed at a size n and at 4n, and the
step must not take more than 8x the time. A linear rewrite takes about 4x, a quadratic one 16x.

THE SIZE IS CHOSEN PER CASE (3.21.1, CI runs 39372338135 and 39377281135). Fixed sizes of 40 to 320 KB left most cases
at 1 to 90 ms, where one stall on a loaded runner decided the verdict: _LineFlag 'git push ' read 0.011, 0.0108,
0.0217 and 0.0938 s there. So n starts at 40 KB and doubles until a call takes FLOOR (50 ms) or n reaches CAP, every
reading is the minimum of 3, and a flagged case is timed again at the same n and 4n in a fresh child before it fails
(tests/_timing.py). The measurements run in child processes with a timeout."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _timing  # noqa: E402

MAX_RATIO = 8.0
FLOOR = _timing.FLOOR
START = 40000
CAP = START * 2 ** 8                                   # 10.24 MB, so 4n is at most 41 MB
FAMILIES = ("frame", "cmdpos", "intent", "link", "_LineFlag", "_PipeThen", "_AnyOf", "_ForcePush", "_GhComments",
            "_RmRecursive")

CHILD = r"""
import json, re
from inspeximus import core, claude_code as cc, memory_index_receipt as mir
START, CAP, FLOOR = ARG["start"], ARG["cap"], ARG["floor"]
FIXED = ARG.get("fixed") or {}
def rep(unit, n, tail=""):
    return unit * -(-n // len(unit)) + tail
cases = {
    "frame cells": (core._SMUGGLED_PARAMS.search, lambda n: "</tr>" + rep("<td>v</td>", n, " end")),
    "frame openers": (core._SMUGGLED_PARAMS.search, lambda n: rep("<aa>", n, "</aa>")),
    "cmdpos a;": (cc._CMD_POS.search, lambda n: rep("a;", n, "y ")),
    "cmdpos sudo env": (cc._CMD_POS.search, lambda n: rep("sudo env ", n)),
    "cmdpos newlines": (cc._CMD_POS.search, lambda n: "x" + rep("\n", n, " y ")),
    "intent =": (core._restore_intent, lambda n: "restore:" + rep("=", n, "#a")),
    "intent @a": (core._restore_intent, lambda n: "restore:k=" + rep("@a", n, "#a")),
    # A judged intent case with margin: "intent =" reads about 0.06 s at 41 MB, on the floor; this one 0.13 s at 2.56 MB.
    "intent hex tail": (core._restore_intent, lambda n: "restore:k=v#" + rep("a", n)),
    "link ](x": (mir.LINK.findall, lambda n: rep("](x", n, ".md)")),
    "link ](": (mir.LINK.findall, lambda n: rep("](", n)),
    "link ](b.md)": (mir.LINK.findall, lambda n: rep("](b.md)", n)),        # every unit a match: a call over the floor
    "link [t](x.md) lines": (mir.LINK.findall, lambda n: rep("[t](x.md)\n", n)),
}
KEYS = {"taskkill": "taskkill ", "get-process": "get-process ", "stop-process": "kill ", "remove-item": "remove-item ",
        "(?:del": "rd ", "git\\s+reset": "git reset ", "git\\s+clean": "git clean ", "git\\s+push\\b": "git push ",
        "git\\s+checkout": "git checkout ", "git\\s+branch": "git branch ", "mkfs": "dd ", "\\brm\\s": "rm ",
        "gh\\s+(issue": "gh api ", "git\\s+push\\s+.*": "git push "}
stand_ins = [o for o in cc._NO_REPLAY if not isinstance(o, re.Pattern)] + [p for p, _s, _w in cc._PRE_PATTERNS if not isinstance(p, str)]
for o in stand_ins:
    unit = next((u for k, u in KEYS.items() if k in o.pattern), None)
    if unit is None:
        continue
    name = "%s %r" % (type(o).__name__, unit)
    cases[name] = (o.search, lambda n, u=unit: rep(u, n))
    cases[name + " |"] = (o.search, lambda n, u=unit: rep(u + "|", n))                 # AUDIT-A's copy-per-pipe shape
    cases[name + " x|"] = (o.search, lambda n, u=unit: rep(u + "x|", n))
    cases[name + " newline"] = (o.search, lambda n, u=unit: rep(u + "\n", n))
out = {}
for name, (fn, mk) in cases.items():
    if ONLY and name not in ONLY:
        continue
    n = FIXED.get(name, START)
    t = best(fn, mk(n))
    while name not in FIXED and t < FLOOR and n * 2 <= CAP:
        n *= 2
        t = best(fn, mk(n))
    out[name] = [n, t, best(fn, mk(4 * n))]
print(json.dumps(out))
"""


def _measure(only=(), fixed=None):
    arg = {"start": START, "cap": CAP, "floor": FLOOR, "fixed": fixed}
    return _timing.child(CHILD, only, arg, timeout=900, what="the growth measurements")


@pytest.fixture(scope="module")
def timings():
    return _measure()


def test_the_cases_reach_every_rewrite(timings):
    # CONTROL: a rewrite with no case would pass unexamined.
    names = " ".join(timings)
    for part in FAMILIES:
        assert part in names, part
    assert len(timings) >= 50, len(timings)


def test_every_rewrite_is_judged_above_the_floor(timings):
    # CONTROL: a floor nothing crosses makes the ratio test vacuous. Every case's larger call is over FLOOR, or its
    # n reached CAP: 41 MB in under 50 ms is linear time on its own (frame cells and cmdpos newlines return at once,
    # on an early rejection). And every rewrite has at least one case judged over the floor.
    under = {name: v for name, v in timings.items() if v[2] < FLOOR and v[0] < CAP}
    assert not under, under
    judged = " ".join(name for name, v in timings.items() if v[2] >= FLOOR)
    assert all(part in judged for part in FAMILIES), [part for part in FAMILIES if part not in judged]


def _flags(timings):
    return {name: v for name, v in timings.items() if v[2] >= FLOOR and v[2] / max(v[1], 1e-9) > MAX_RATIO}


def verdict(timings, remeasure):
    """{name: [first reading, fresh reading]} of the cases over 8x for 4x twice: once here, once in `remeasure`
    (a fresh child at the same n, given {name: n}). Each reading is [n, seconds at n, seconds at 4n]."""
    flagged = _flags(timings)
    if not flagged:
        return {}
    again = _flags(remeasure({name: v[0] for name, v in flagged.items()}))
    return {name: [flagged[name], again[name]] for name in flagged if name in again}


def test_no_rewrite_takes_more_than_8x_the_time_for_4x_the_input(timings):
    worse = verdict(timings, lambda fixed: _measure(fixed, fixed))
    assert not worse, "grows faster than linear twice ([n, seconds at n, seconds at 4n]): %s" % worse


def test_one_noisy_step_is_not_a_verdict():
    # A reading of the shape CI run 39377281135 failed on: a linear rewrite with one stalled call.
    noisy = {"_LineFlag 'git push '": [80000, 0.0108, 0.0938], "_PipeThen 'get-process ' |": [640000, 0.05, 0.21]}
    asked = []
    linear = {"_LineFlag 'git push '": [80000, 0.0108, 0.044]}            # the fresh child: about 4x for 4x
    assert verdict(noisy, lambda fixed: asked.append(fixed) or linear) == {}
    assert asked == [{"_LineFlag 'git push '": 80000}], "the first pass no longer flags the stalled reading: " + repr(asked)
    # CONTROL: a step the fresh child confirms still fails.
    quadratic = {"_LineFlag 'git push '": [80000, 0.0108, 0.17]}
    assert list(verdict(noisy, lambda fixed: quadratic)) == ["_LineFlag 'git push '"]
