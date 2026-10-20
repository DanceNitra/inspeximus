"""Every 3.21.1 rewrite grows linearly from 40 KB to 320 KB (AUDIT-A R2-3).

An absolute bound cannot see a slowdown that only copies memory. A variant of _PipeThen that copies the text's tail at
every pipe gives the same answers and stays under every bound at 160 KB (0.055 s), yet is quadratic: 0.139 s at 320 KB,
up to 1.1 s at 640 KB, and it passed all 85 tests of round 2. So each rewrite is timed here at 40, 80, 160 and 320 KB,
as the minimum of 3 runs, and a 4x step in size must not take more than 8x the time. A linear rewrite takes about 4x;
a quadratic one 16x. Steps whose larger call is under 10 ms are not judged: there the clock is the noise. The
measurements run in a child process with a timeout."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZES = (40000, 80000, 160000, 320000)
MAX_RATIO = 8.0
FLOOR = 0.01

CHILD = r"""
import json, re, time
from inspeximus import core, claude_code as cc, memory_index_receipt as mir
SIZES = %r
def rep(unit, n, tail=""):
    return unit * -(-n // len(unit)) + tail
def best(fn, s):
    b = 9e9
    for _ in range(3):
        t = time.perf_counter(); fn(s); b = min(b, time.perf_counter() - t)
    return b
cases = {
    "frame cells": (core._SMUGGLED_PARAMS.search, lambda n: "</tr>" + rep("<td>v</td>", n, " end")),
    "frame openers": (core._SMUGGLED_PARAMS.search, lambda n: rep("<aa>", n, "</aa>")),
    "cmdpos a;": (cc._CMD_POS.search, lambda n: rep("a;", n, "y ")),
    "cmdpos sudo env": (cc._CMD_POS.search, lambda n: rep("sudo env ", n)),
    "cmdpos newlines": (cc._CMD_POS.search, lambda n: "x" + rep("\n", n, " y ")),
    "intent =": (core._restore_intent, lambda n: "restore:" + rep("=", n, "#a")),
    "intent @a": (core._restore_intent, lambda n: "restore:k=" + rep("@a", n, "#a")),
    "link ](x": (mir.LINK.findall, lambda n: rep("](x", n, ".md)")),
    "link ](": (mir.LINK.findall, lambda n: rep("](", n)),
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
    name = "%%s %%r" %% (type(o).__name__, unit)
    cases[name] = (o.search, lambda n, u=unit: rep(u, n))
    cases[name + " |"] = (o.search, lambda n, u=unit: rep(u + "|", n))                 # AUDIT-A's copy-per-pipe shape
    cases[name + " x|"] = (o.search, lambda n, u=unit: rep(u + "x|", n))
    cases[name + " newline"] = (o.search, lambda n, u=unit: rep(u + "\n", n))
out = {name: [best(fn, mk(n)) for n in SIZES] for name, (fn, mk) in cases.items()}
print(json.dumps(out))
""" % (SIZES,)


@pytest.fixture(scope="module")
def timings():
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=ROOT, INSPEXIMUS_KEY_HOME=os.environ.get("INSPEXIMUS_KEY_HOME", ""), INSPEXIMUS_NO_UPDATE_CHECK="1")
    try:
        p = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True, encoding="utf-8",
                           timeout=600, env=env, cwd=ROOT)
    except subprocess.TimeoutExpired:
        pytest.fail("the growth measurements did not finish in 600 s: a rewrite is quadratic again")
    assert p.returncode == 0, p.stderr[-3000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_the_cases_reach_every_rewrite(timings):
    # CONTROL: a rewrite with no case would pass unexamined.
    names = " ".join(timings)
    for part in ("frame", "cmdpos", "intent", "link", "_LineFlag", "_PipeThen", "_AnyOf", "_ForcePush", "_GhComments",
                 "_RmRecursive"):
        assert part in names, part
    assert len(timings) >= 50, len(timings)


def test_no_rewrite_takes_more_than_8x_the_time_for_4x_the_input(timings):
    worse = {}
    for name, ts in timings.items():
        for small, big in ((0, 2), (1, 3)):                  # 40 -> 160 KB and 80 -> 320 KB
            if ts[big] >= FLOOR and ts[big] / max(ts[small], 1e-6) > MAX_RATIO:
                worse[name] = [round(t, 4) for t in ts]
    assert not worse, "grows faster than linear (times at %s bytes): %s" % (list(SIZES), worse)
