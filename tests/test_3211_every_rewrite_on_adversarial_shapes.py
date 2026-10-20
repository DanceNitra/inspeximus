"""Every 3.21.1 rewrite on many adversarial shapes at 160 KB or more (AUDIT-A F-2, F-4).

One shape per rewrite was not enough: `_CmdPos` passed its whitespace test at 20 KB and still took 1.4 s on 160 KB of
"a;" (AUDIT-A F-2). So each rewrite runs here on AUDIT-A's shapes and on its own keywords repeated, at 160 KB or more,
and each call must take under 0.5 s. The rewrites take 0.2 s at most there on this machine; the regexes they replace
take seconds at a third of that size, so the bound leaves a wide margin both ways. The shapes run in one child
process with a timeout, so a quadratic regression fails here in minutes at most instead of stalling the suite."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOUND = 0.5

CHILD = r"""
import json, re, sys, time
from inspeximus import core, claude_code as cc
N = 160000
def rep(unit, tail=""):
    return unit * -(-N // max(1, len(unit))) + tail
fr, cp, ri = core._SMUGGLED_PARAMS, cc._CMD_POS, core._restore_intent
rm = next(o for o in cc._NO_REPLAY if getattr(o, "pattern", "").startswith(r"\brm"))
shapes = {
    # the frame guard: AUDIT-A's shapes
    "frame cells": (fr.search, "</tr>" + rep("<td>v</td>", " end")),
    "frame openers": (fr.search, rep("<aa>", "</aa>")),
    "frame closers": (fr.search, rep("</aa>")),
    "frame closer then openers": (fr.search, "</bb>" + rep("<aa>", "</aa>")),
    "frame spaces before openers": (fr.search, "<aa>" + rep(" " * 50 + "<aa>", "</aa>")),
    "frame newline run": (fr.search, "</bb>" + rep("\n", "<aa>x</aa>")),
    "frame nested names": (fr.search, "".join("</n%d><n%d>" % (i % 7, i % 7) for i in range(N // 8)) + "</zz>"),
    "frame space tail": (fr.search, "</aa><bb>x</bb>" + rep(" ")),
    "frame attributes": (fr.search, "</aa>" + rep("<bb k=v>x</bb>")),
    "frame 1-char names": (fr.search, rep("</a>")),
    "frame open fragments": (fr.search, rep("<aa", "<aa>x</aa>")),
    # the command position check: AUDIT-A's shapes, F-2's among them
    "cmdpos ;": (cp.search, rep(";", " ")), "cmdpos a;": (cp.search, rep("a;", "y ")), "cmdpos |": (cp.search, rep("|", " ")),
    "cmdpos ((": (cp.search, rep("((", "y ")), "cmdpos sudo": (cp.search, rep("sudo ")), "cmdpos A=1": (cp.search, rep("A=1 ")),
    "cmdpos newlines": (cp.search, rep("\n")), "cmdpos spaces": (cp.search, rep(" ")), "cmdpos x spaces": (cp.search, "x" + rep(" ")),
    "cmdpos long word": (cp.search, rep("a", " ")), "cmdpos long value": (cp.search, "a=" + rep("b", " ")),
    "cmdpos a=;": (cp.search, rep("a=;", " ")), "cmdpos &&": (cp.search, rep("x&&", " ")),
    "cmdpos x; after spaces": (cp.search, " " * 10 + rep("x;", " ")), "cmdpos ;sudo": (cp.search, rep(";sudo", " ")),
    "cmdpos a;b=1": (cp.search, rep("a;b=1", " ")), "cmdpos x|": (cp.search, rep("x|", "y ")),
    # the restore intent: AUDIT-A's shapes
    "intent #": (ri, "restore:" + rep("#", "=#a")), "intent @": (ri, "restore:k=" + rep("@", "#a")),
    "intent =": (ri, "restore:" + rep("=", "#a")), "intent hex tail": (ri, "restore:k=v#" + rep("a")),
    "intent newlines": (ri, "restore:" + rep("k\n", "=#a")), "intent @a": (ri, "restore:k=" + rep("@a", "#a")),
    "intent no hash": (ri, "restore:k=" + rep("v")), "intent bad nonce": (ri, "restore:k=v#" + rep("a", "g")),
    "intent aa=": (ri, "restore:a=a@0#0" + rep("aa=", " !")),
    # the rm -r rule: AUDIT-A's shapes
    "rm repeated": (rm.search, rep("rm ")), "rm spaces": (rm.search, "rm" + rep(" ", "x")), "rm a ;": (rm.search, rep("rm a ; ")),
    "rm newlines": (rm.search, rep("rm \n")), "rm -aaaa": (rm.search, "rm " + rep("-aaaa ")), "rm long flag": (rm.search, "rm -" + rep("a")),
    "rm dashes": (rm.search, "rm " + rep("- ")), "rm x": (rm.search, rep("rm x ")), "rm RM tab": (rm.search, rep("RM \t")),
    "rm nbsp": (rm.search, rep("rm ", "-x")),
}
from inspeximus import memory_index_receipt as mir
shapes.update({
    # the memory index link reader, on the MEMORY.md the SessionStart hook reads
    "link ](": (mir.LINK.findall, rep("](")), "link ](x": (mir.LINK.findall, rep("](x", ".md)")),
    "link ](a.md": (mir.LINK.findall, rep("](a.md")), "link ](.md)": (mir.LINK.findall, rep("](.md)")),
    "link ]( newline": (mir.LINK.findall, rep("](\n")), "link [t](": (mir.LINK.findall, rep("[t](", ")")),
})
# every replay rule and warning stand-in, on its own keyword repeated, alone and before a newline or a pipe
KEYS = {"taskkill": "taskkill ", "get-process": "get-process ", "stop-process": "kill ", "remove-item": "remove-item ",
        "(?:del": "rd ", "git\\s+reset": "git reset ", "git\\s+clean": "git clean ", "git\\s+push\\b": "git push ",
        "git\\s+checkout": "git checkout ", "git\\s+branch": "git branch ", "mkfs": "dd ", "gh\\s+(issue": "gh api ",
        "git\\s+push\\s+.*": "git push "}
stand_ins = [o for o in cc._NO_REPLAY if not isinstance(o, re.Pattern)] + [p for p, _s, _w in cc._PRE_PATTERNS if not isinstance(p, str)]
for o in stand_ins:
    unit = next((u for k, u in KEYS.items() if k in o.pattern), None)
    if unit is None:
        continue
    for tail_name, tail in (("", ""), (" then newline", "\n"), (" then pipe", "|")):
        shapes["%s %r%s" % (type(o).__name__, unit, tail_name)] = (o.search, rep(unit) + tail)
        shapes["%s %r%s, no spaces" % (type(o).__name__, unit, tail_name)] = (o.search, rep(unit.replace(" ", "\t")) + tail)
out = {}
for name, (fn, text) in shapes.items():
    t = time.perf_counter()
    fn(text)
    out[name] = [len(text), time.perf_counter() - t]
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def timings():
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=ROOT, INSPEXIMUS_KEY_HOME=os.environ.get("INSPEXIMUS_KEY_HOME", ""), INSPEXIMUS_NO_UPDATE_CHECK="1")
    try:
        p = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True, encoding="utf-8",
                           timeout=300, env=env, cwd=ROOT)
    except subprocess.TimeoutExpired:
        pytest.fail("the adversarial shapes did not finish in 300 s: a rewrite is quadratic again")
    assert p.returncode == 0, p.stderr[-3000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_the_shapes_cover_every_rewrite(timings):
    # CONTROL: the shape list must reach every rewrite, or a rule it never reaches passes unexamined.
    names = " ".join(timings)
    for part in ("frame", "cmdpos", "intent", "rm ", "_LineFlag", "_PipeThen", "_AnyOf", "_ForcePush", "_GhComments",
                 "link "):
        assert part in names, part
    assert min(chars for chars, _s in timings.values()) >= 160000


def test_every_rewrite_takes_under_half_a_second_at_160_kb(timings):
    slow = {k: round(s, 3) for k, (chars, s) in timings.items() if s >= BOUND}
    assert not slow, "over %.1f s at 160 KB or more: %s" % (BOUND, slow)


def test_the_link_reader_returns_the_regexs_findall():
    import random
    import re
    from inspeximus import memory_index_receipt as mir
    old = re.compile(mir.LINK.pattern)
    toks = ["]", "(", ")", "](", ".md", ".md)", "a", "x.md", "\n", " ", "[t]", "md", ".", "](a.md)", ".mD"]
    rnd = random.Random(32114)
    with_links = 0
    for _ in range(30000):
        s = "".join(rnd.choice(toks) for _ in range(rnd.randint(0, 12)))
        want = old.findall(s)
        assert mir.LINK.findall(s) == want, repr(s)
        with_links += bool(want)
    assert with_links > 1000, with_links
