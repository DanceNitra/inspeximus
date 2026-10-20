"""One rule for every timing verdict of the 3.21.1 tests: no verdict rests on one noisy reading.

CI runs 39372338135 and 39377281135 failed four linear rewrites on single readings of 1 to 90 ms, where one scheduler
stall on a loaded runner decides the verdict. So every timing verdict here follows three rules:

- each reading is the minimum of 3 calls (one call is enough when it is already under the bound, because noise only
  adds time);
- a verdict is judged only on calls of FLOOR or more;
- a case that fails is measured again in a fresh child process, and fails only if it fails there too.

Child scripts get PRELUDE first: `best(fn, arg, bound)`, `ONLY` (the case names to measure, all when empty) and
`ARG` (a JSON value the parent passes, such as the sizes)."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOOR = 0.05

PRELUDE = r'''
import json as _json, sys as _sys, time as _time
ONLY = set(_json.loads(_sys.argv[1]))
ARG = _json.loads(_sys.argv[2])
def best(fn, arg, bound=None):
    """The minimum of 3 calls. One call when it is under `bound`; no repeats when it is over 10x the bound,
    where a regression is seconds and the fresh child confirms it anyway."""
    b = 9e9
    for _ in range(3):
        t = _time.perf_counter()
        fn(arg)
        b = min(b, _time.perf_counter() - t)
        if bound is not None and (b < bound or b > 10 * bound):
            break
    return b
'''


def child(code, only=(), arg=None, timeout=60, what="the shapes"):
    """Run PRELUDE + code in a fresh interpreter and return the JSON object it prints last."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=ROOT, INSPEXIMUS_KEY_HOME=os.environ.get("INSPEXIMUS_KEY_HOME", ""), INSPEXIMUS_NO_UPDATE_CHECK="1")
    try:
        p = subprocess.run([sys.executable, "-c", PRELUDE + code, json.dumps(sorted(only)), json.dumps(arg)],
                           capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env, cwd=ROOT)
    except subprocess.TimeoutExpired:
        pytest.fail("%s did not finish in %d s: a rewrite is slow again" % (what, timeout))
    assert p.returncode == 0, p.stderr[-3000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def over_bound(code, took, bound, seconds=lambda v: v, arg=None, timeout=60, what="the shapes"):
    """{name: [first reading, fresh reading]} of the cases over `bound` twice: once in `took`, once in a fresh child."""
    assert bound >= FLOOR, "a bound under %.2f s judges on clock noise" % FLOOR
    flagged = {name for name, v in took.items() if seconds(v) >= bound}
    if not flagged:
        return {}
    again = child(code, flagged, arg, timeout, what)
    return {name: [took[name], again[name]] for name in sorted(flagged) if seconds(again[name]) >= bound}
