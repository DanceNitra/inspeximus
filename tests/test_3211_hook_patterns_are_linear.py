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


CHILD = r"""
import json, time
from inspeximus import claude_code as cc
rm = next(p for p in cc._NO_REPLAY if getattr(p, "pattern", "").startswith(r"\brm"))
shapes = {"rm_spaces": "rm" + " " * 20000 + "x", "rm_many": "rm " * 20000 + "x"}
out = {}
for name, text in shapes.items():
    t = time.perf_counter()
    rm.search(text)
    out[name] = time.perf_counter() - t
print(json.dumps(out))
"""


def _child(code):
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=ROOT, INSPEXIMUS_KEY_HOME=os.environ.get("INSPEXIMUS_KEY_HOME", ""), INSPEXIMUS_NO_UPDATE_CHECK="1")
    try:
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                           timeout=20, env=env, cwd=ROOT)
    except subprocess.TimeoutExpired:
        pytest.fail("the shapes did not finish in 20 s")
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_the_rm_rule_takes_under_a_tenth_of_a_second_on_20_kb():
    # The regex takes about 3 s on the first shape; the child is stopped after 20 s.
    for name, seconds in _child(CHILD).items():
        assert seconds < 0.1, "%s took %.3f s" % (name, seconds)
