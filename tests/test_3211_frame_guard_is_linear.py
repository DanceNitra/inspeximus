"""remember()'s frame-markup guard runs in linear time, with its meaning unchanged (3.21.1).

The smuggled-parameters check was a regex whose lazy `.*?` sat inside a repeated group, so it could split one run
of tags in every possible way before failing. Measured 2026-10-19: remember() spent 67 s on a 3,776-character
section of an ordinary tool output with 47 closing tags, and on `"</tr>" + "<td>v</td>" * n + " end"` the time
doubled with every cell (0.027 s at n = 16, 0.457 s at n = 20). Every remember() runs the check, the MCP tool's
included.

The meaning is pinned two ways: tests/test_frame_markup_is_refused.py is unchanged and passes, and the replacement
agrees with the old regex on a fuzzed corpus of short tag-dense strings, short enough for the old regex to finish.
The timing runs in a child process with a timeout, so a regression fails in seconds instead of hanging the suite."""
import json
import os
import random
import re
import subprocess
import sys

import pytest

from inspeximus.core import _SMUGGLED_PARAMS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = re.compile(_SMUGGLED_PARAMS.pattern, re.S)
TOKENS = ["<ab>", "</ab>", "<cd>", "</cd>", "</x>", "<x>", " ", "\n", "\t", "t", "ab>", "</", "<ab", "</decision>",
          "<topic>", "</topic>", '<parameter name="t">', " ", "\x1c"]


def test_it_agrees_with_the_regex_it_replaces():
    rnd = random.Random(3211)
    positives = 0
    for _ in range(20000):
        s = "".join(rnd.choice(TOKENS) for _ in range(rnd.randint(0, 9)))
        old = OLD.search(s.rstrip()) is not None
        assert (_SMUGGLED_PARAMS.search(s.rstrip()) is not None) == old, repr(s)
        positives += old
    assert positives > 100, "the corpus must reach the matching case, or agreement says nothing (%d)" % positives


@pytest.mark.parametrize("text", ["body.</decision>\n<topic>t</topic>", "x</a1>  <ab>q</ab>\n<cd></cd>  ",
                                  "</ab><ab><ab></ab>"])
def test_known_matches_still_match(text):
    assert _SMUGGLED_PARAMS.search(text) and OLD.search(text.rstrip())


@pytest.mark.parametrize("text", ["prose </decision> then <topic>t</topic> mid-sentence.", "<ab>q</ab>",
                                  "</a><b>q</b>", "</ab> <cd>q</ab>", "x</ab>"])
def test_known_non_matches_still_do_not(text):
    assert not _SMUGGLED_PARAMS.search(text) and not OLD.search(text.rstrip())


CHILD = r"""
import json, sys, time
from inspeximus.core import Inspeximus, _reject_frame_markup
shapes = {"cells": "</tr>" + "<td>v</td>" * 30 + " end",
          "lines": "".join("</tr>\n<td>v</td>\n<td>w</td>\n" for _ in range(30)) + "x",
          "long": "</tr>" + "<td>v</td>" * 5000 + " end"}
out = {}
for name, text in shapes.items():
    t = time.perf_counter()
    _reject_frame_markup(text)
    out[name] = time.perf_counter() - t
m = Inspeximus(None)
t = time.perf_counter()
m.remember(shapes["cells"] + " in a memory")
out["remember"] = time.perf_counter() - t
print(json.dumps(out))
"""


def test_the_tag_runs_that_blew_up_take_under_a_tenth_of_a_second():
    # The old regex needs about 8 minutes for the n = 30 cell shape. The child process is stopped after 20 s,
    # so a regression fails here quickly instead of stalling the suite.
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=ROOT, INSPEXIMUS_KEY_HOME=os.environ.get("INSPEXIMUS_KEY_HOME", ""), INSPEXIMUS_NO_UPDATE_CHECK="1")
    try:
        p = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True, encoding="utf-8",
                           timeout=20, env=env, cwd=ROOT)
    except subprocess.TimeoutExpired:
        pytest.fail("the frame guard did not finish the 30-cell shapes in 20 s")
    assert p.returncode == 0, p.stderr[-2000:]
    took = json.loads(p.stdout.strip().splitlines()[-1])
    for name, seconds in took.items():
        assert seconds < 0.1, "%s took %.3f s" % (name, seconds)
