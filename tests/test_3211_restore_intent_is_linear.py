"""The `restore:` intent is parsed in linear time, with the regex's groups (3.21.1).

The regex ^restore:(.+?)=([^@#]*)(?:@([0-9a-f]*))?#([0-9a-f]+)$ was quadratic on a long intent (about 54 ms at
3 KB, 5 s at 30 KB). It runs after the capability check, so only an authorised caller reaches it, but the intent is
still caller text. `_restore_intent` returns the same four groups; the regex stays as `_RESTORE_INTENT`."""
import json
import os
import random
import re
import subprocess
import sys

import pytest

from inspeximus import core

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = re.compile(core._RESTORE_INTENT)


def _groups(m):
    return None if m is None else tuple(m.group(i) for i in range(1, 5))


def test_it_returns_the_regexs_groups_on_a_fuzzed_corpus():
    rnd = random.Random(32113)

    def part(alpha, k):
        return "".join(rnd.choice(alpha) for _ in range(rnd.randint(0, k)))
    positives = 0
    for _ in range(30000):
        s = ("restore:" + part(["k", "=", "@", "#", "\n", "x"], 4) + "=" + part(["v", "=", "\n", "@", "x", "#"], 4)
             + (("@" + part(["0", "a", "f", "g", "@"], 3)) if rnd.random() < 0.6 else "")
             + "#" + part(["0", "9", "f", "g", "#", "\n"], 3) + ("\n" if rnd.random() < 0.2 else ""))
        want = _groups(OLD.match(s))
        assert _groups(core._restore_intent(s)) == want, repr(s)
        positives += want is not None
    assert positives > 1000, positives


@pytest.mark.parametrize("s", ["restore:topic=value@ab12#ff", "restore:topic=value#0", "restore:a=b=c#1\n",
                               "restore:a@b=c@d#1", "restore:a=b@c#1", "restore:=x#1", "restore:a=#1", "revert:a@b#1",
                               "restore:a\nb=c#1", "restore:k=v@#1", "restore:k=v#"])
def test_known_intents(s):
    assert _groups(core._restore_intent(s)) == _groups(OLD.match(s))


def test_a_non_text_intent_raises_as_before():
    with pytest.raises(TypeError):
        core._restore_intent(b"restore:a=b#1")


CHILD = r"""
import json, time
from inspeximus import core
shapes = {"aa": "restore:a=a@0#0" + "aa=" * 10000 + " !", "eq": "restore:" + "=" * 30000 + "#g"}
out = {}
for name, text in shapes.items():
    t = time.perf_counter()
    core._restore_intent(text)
    out[name] = time.perf_counter() - t
print(json.dumps(out))
"""


def test_a_30_kb_intent_takes_under_a_tenth_of_a_second():
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=ROOT, INSPEXIMUS_KEY_HOME=os.environ.get("INSPEXIMUS_KEY_HOME", ""), INSPEXIMUS_NO_UPDATE_CHECK="1")
    try:
        p = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True, encoding="utf-8",
                           timeout=20, env=env, cwd=ROOT)
    except subprocess.TimeoutExpired:
        pytest.fail("the 30 KB intents did not finish in 20 s")
    assert p.returncode == 0, p.stderr[-2000:]
    for name, seconds in json.loads(p.stdout.strip().splitlines()[-1]).items():
        assert seconds < 0.1, "%s took %.3f s" % (name, seconds)
