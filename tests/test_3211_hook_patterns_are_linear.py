"""The hook's command patterns run in linear time, with their meaning unchanged (3.21.1).

Found by the class scan after the frame-guard fix (agora_output/strategy/builder_3211_redos.md): every regex in
the package was attacked with growing input, and the ones that read caller text and grew worse than linear were
rewritten. Each replacement keeps the regex as `.pattern`, agrees with it on a fuzzed corpus short enough for the
regex to finish, and is timed in a child process with a timeout, so a regression fails quickly."""
import json
import os
import random
import re
import subprocess
import sys

import pytest

from inspeximus import claude_code as cc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rm():
    return next(p for p in cc._NO_REPLAY if getattr(p, "pattern", "").startswith(r"\brm"))


def test_the_rm_rule_agrees_with_the_regex_it_replaces():
    new = _rm()
    old = re.compile(new.pattern, re.I)
    toks = ["rm", " ", "  ", "\n", "\t", ";", "&", "|", "-r", "-rf", "-f", "-R", "--recursive", "--rec", "x", "-a",
            "RM", "-xr", "/", "arm"]
    rnd = random.Random(32111)
    positives = 0
    for _ in range(30000):
        s = "".join(rnd.choice(toks) for _ in range(rnd.randint(0, 10)))
        want = old.search(s) is not None
        assert bool(new.search(s)) == want, repr(s)
        positives += want
    assert positives > 100, positives


@pytest.mark.parametrize("cmd,hit", [("rm -rf build", True), ("rm  -r\n", True), ("sudo rm -f x -R", True),
                                     ("rm x\n-r", True), ("rm x; -r", False), ("rm -f x", False), ("arm -r", False),
                                     ("RM --Recursive d", True), ("rm --rec d", False)])
def test_the_rm_rule_on_known_commands(cmd, hit):
    assert bool(_rm().search(cmd)) is hit
    assert (re.compile(_rm().pattern, re.I).search(cmd) is not None) is hit


def test_the_command_position_check_agrees_with_the_regex_it_replaces():
    old = re.compile(cc._CMD_POS.pattern)
    toks = ["sudo", "env", "time", "xargs", "x=1", "a=", "=b", "|", ";", "&", "\n", "(", ")", " ", "  ", "\t", "foo",
            "é=", "é", "x=a|b", "ENV", "sudox", "a|b=c", "1=2", "_=", "\x1c", " ", "="]
    rnd = random.Random(32112)
    positives = 0
    for _ in range(30000):
        s = "".join(rnd.choice(toks) for _ in range(rnd.randint(0, 8)))
        want = old.search(s) is not None
        assert bool(cc._CMD_POS.search(s)) == want, repr(s)
        positives += want
    assert positives > 1000, positives


@pytest.mark.parametrize("prefix,hit", [("", True), ("ls; ", True), ("echo x | sudo env A=1 ", True),
                                        ("echo 'gh ", False), ("(", True), ("cd d && FOO=bar time ", True),
                                        ("x=a|b ", True), ("echo ", False), ("a\n", True), ("ENV ", False)])
def test_the_command_position_check_on_known_prefixes(prefix, hit):
    assert bool(cc._CMD_POS.search(prefix)) is hit
    assert (re.compile(cc._CMD_POS.pattern).search(prefix) is not None) is hit


CHILD = r"""
import json, time
from inspeximus import claude_code as cc
rm = next(p for p in cc._NO_REPLAY if getattr(p, "pattern", "").startswith(r"\brm"))
shapes = {"rm_spaces": (rm, "rm" + " " * 20000 + "x"), "rm_many": (rm, "rm " * 20000 + "x"),
          "cmd_newlines": (cc._CMD_POS, "x" + "\n" * 20000 + " y "), "cmd_spaces": (cc._CMD_POS, "|" + " " * 20000 + "y ")}
out = {}
for name, (rx, text) in shapes.items():
    t = time.perf_counter()
    rx.search(text)
    out[name] = time.perf_counter() - t
print(json.dumps(out))
"""


def _child(code, timeout=20):
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=ROOT, INSPEXIMUS_KEY_HOME=os.environ.get("INSPEXIMUS_KEY_HOME", ""), INSPEXIMUS_NO_UPDATE_CHECK="1")
    try:
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                           timeout=timeout, env=env, cwd=ROOT)
    except subprocess.TimeoutExpired:
        pytest.fail("the shapes did not finish in %d s" % timeout)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_the_rm_rule_takes_under_a_tenth_of_a_second_on_20_kb():
    # The regex takes about 3 s on the first shape; the child is stopped after 20 s.
    for name, seconds in _child(CHILD).items():
        if name.startswith("rm_"):
            assert seconds < 0.1, "%s took %.3f s" % (name, seconds)


def test_the_command_position_check_takes_under_a_tenth_of_a_second_on_20_kb():
    # The regex takes about 11 s on 20 KB of newlines; the child is stopped after 20 s.
    for name, seconds in _child(CHILD).items():
        if name.startswith("cmd_"):
            assert seconds < 0.1, "%s took %.3f s" % (name, seconds)


# ---- 3.21.1 round 2 (AUDIT-A F-1): every replay rule and warning pattern that is a stand-in ----
FAST = r"""
import json, re, time
from inspeximus import claude_code as cc
from inspeximus import memory_index_receipt as mir
KEYS = {"taskkill": "taskkill ", "stop-process": "kill ", "get-process": "get-process ", "remove-item": "remove-item ",
        "(?:del": "rd ", "git\\s+reset": "git reset ", "git\\s+clean": "git clean ", "git\\s+push\\b": "git push ",
        "git\\s+checkout": "git checkout ", "git\\s+branch": "git branch ", "mkfs": "dd ", "gh\\s+(issue": "gh api ",
        "git\\s+push\\s+.*": "git push "}
objs = [(o.pattern, o.search) for o in cc._NO_REPLAY if not isinstance(o, re.Pattern)]
for p, _s, _w in cc._PRE_PATTERNS:                     # every warning, as the hook calls it: a string through re.search
    objs.append((p, (lambda t, p=p: re.search(p, t, re.I))) if isinstance(p, str) else (p.pattern, p.search))
out = {}
for pattern, search in objs:
    unit = next((u for k, u in KEYS.items() if k in pattern), None)
    if unit:
        text = unit * (40000 // len(unit))
        t = time.perf_counter(); search(text); out["%s %r" % (pattern[:24], unit)] = time.perf_counter() - t
for name, text in (("cmdpos a;", "a;" * 20000 + "y "), ("cmdpos ((", "((" * 20000 + "y "), ("link ](", "](x" * 13000 + ".md)"),
                   ("link ]( no close", "](" * 20000)):
    fn = mir.LINK.findall if name.startswith("link") else cc._CMD_POS.search
    t = time.perf_counter(); fn(text); out[name] = time.perf_counter() - t
print(json.dumps(out))
"""


def test_every_stand_in_takes_under_a_fifth_of_a_second_on_its_keyword_at_40_kb():
    # The regexes take about a second or more here (git checkout: 7.2 s at 104 KB; AUDIT-A F-1), and the old
    # _CmdPos slice 0.3 s on 40 KB of "a;" (F-2). The child is stopped after 60 s.
    took = _child(FAST, timeout=60)
    assert len([k for k in took if not k.startswith(("cmdpos", "link"))]) >= 13, took
    slow = {k: round(v, 3) for k, v in took.items() if v >= 0.2}
    assert not slow, slow
WS = [" ", "\n", "\t", "\r\n", "\u00a0", "\u2028", "\x1c", "\x85", "\x0b", "\x0c", "\u3000", "\u200b"]
COMMON = ["x", "a", "-", "--", "/", "|", ";", "&", "+", "=", "'", "\"", "\u212a", "\u017f", "K", "S"]
EXTRA = ["comments", "Comments", "gh issue comment", "gh pr comment", "GH PR comment", "--force-with-lease", "+x",
         "-xf", "-fd"]


def _words(pattern):
    toks = set()
    flat = pattern.replace("\\b", " ").replace("\\s+", " ").replace("\\s*", " ").replace("\\s", " ")
    for w in re.findall(r"[A-Za-z][A-Za-z.=/-]+|--?[A-Za-z]+|/[A-Za-z]+|\+", flat):
        toks.update({w, w.upper(), w.capitalize()})
    for phrase in re.findall(r"[A-Za-z]+(?:\\s[+*]?(?:\(\?:)?[A-Za-z|]+\)?)+", pattern.replace("\\b", "")):
        parts = [x for x in re.split(r"\\s[+*]?", phrase) if x]
        for alt in parts[-1].strip("()?:").split("|"):
            ws_ = [p.strip("()?:").split("|")[0] for p in parts[:-1]] + [alt]
            toks.update({" ".join(ws_), "  ".join(ws_), "\t".join(ws_)})
    return sorted(toks)


def _small(toks):
    keep = [t for t in toks if " " in t or "\t" in t or t[:1] in "-/+" or t.lower() in
            ("taskkill", "kill", "spps", "remove-item", "del", "rd", "rmdir", "erase", "dd", "rm", "get-process",
             "stop-process", "mkfs", "format")]
    extra = [t for t in EXTRA if t.split()[-1].lower().lstrip("-+") in " ".join(toks).lower()]
    return keep + extra + ["x", "|", "\n"]


def _stand_ins():
    out = [(o, False) for o in cc._NO_REPLAY if not isinstance(o, re.Pattern)]
    out += [(p, sev == "block") for p, sev, _w in cc._PRE_PATTERNS if not isinstance(p, str)]
    return out


def test_the_stand_ins_cover_the_rules_audit_a_named():
    # CONTROL: 11 replay rules of the KEYWORD[^\n]*?\sFLAG shape, get-process, the dd part, rm, and the two warnings
    names = [type(o).__name__ for o, _strict in _stand_ins()]
    assert names.count("_LineFlag") == 9 and "_PipeThen" in names and "_AnyOf" in names, names
    assert "_ForcePush" in names and "_GhComments" in names and "_RmRecursive" in names, names


@pytest.mark.parametrize("structured", [False, True], ids=["keywords", "structured"])
@pytest.mark.parametrize("idx", range(14))
def test_every_stand_in_agrees_with_the_regex_it_replaces(idx, structured):
    obj, strict = _stand_ins()[idx]
    old = re.compile(obj.pattern, re.I)
    toks = _small(_words(obj.pattern)) if structured else _words(obj.pattern)
    rnd = random.Random(3211 + idx + (100 if structured else 0))
    hits = 0
    for _ in range(4000):
        out = []
        for _ in range(rnd.randint(0, 12)):
            x = rnd.random()
            out.append(rnd.choice(toks) if x < 0.45 else (rnd.choice(WS) * rnd.randrange(1, 3) if x < 0.75
                                                          else rnd.choice(COMMON)))
        s = "".join(out)
        a, b = old.search(s), obj.search(s)
        if strict:
            assert (a is None) == (b is None), repr(s)
            if a is not None:
                assert (a.start(), a.group(0)) == (b.start(), b.group(0)), repr(s)
        else:
            assert (a is not None) == bool(b), repr(s)
        hits += a is not None
    if structured:
        assert hits > 20, "the structured corpus must reach the matching case (%d)" % hits
