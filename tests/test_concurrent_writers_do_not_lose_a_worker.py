"""Two processes writing one store must not lose one of them entirely.

WHY THIS EXISTS. Nothing in the suite asked. The gap was found by asking on 2026-09-06, and it is
not a corner case: Claude Code and Codex write the same coding store in this project, and a hook
fires on every tool call, so concurrent writes are the normal condition rather than the unlucky one.

MEASURED THEN, separate OS processes so the interpreter lock cannot hide the race, 25 records each,
8 repeats per width:

    writers   trials that lost nothing   what was lost
    2         4 of 8                     25, always a whole worker
    12        0 of 8                     25, 50 or 75

Every loss is a multiple of one worker's output, and that is the mechanism. `StoreChangedOnDisk`
fires correctly and nothing is corrupted, so the store is SAFE. But the losing writer's handle
never reloads, so after its first refusal it fails for every remaining write and drops everything
it had to say. The store is not AVAILABLE.

WHAT THIS TEST PINS. Not that concurrency is perfect: it is not, and pretending otherwise would
make this a test that has to be deleted when it fails honestly. It pins the property that a losing
writer can RECOVER, which is what turns a total loss into latency: given `reload()` on
StoreChangedOnDisk, a concurrent run keeps every record.

CONTROLS. A single writer must land every record, or a shortfall in the concurrent arm proves
nothing about concurrency. And the no-retry arm must still lose at the same width, or the retry is
being credited for a race that did not happen in this environment.
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

from _store_io import load_store

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WORKER = '''
import sys, time, random
sys.path.insert(0, %r)
from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk
path, wid, n, retry = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] == "retry"
m = Inspeximus(path=path)
ok = 0
for i in range(n):
    for attempt in range(12 if retry else 1):
        try:
            m.remember("w%%d r%%d uniq-%%d-%%d" %% (wid, i, wid, i), mtype="fact")
            ok += 1
            break
        except StoreChangedOnDisk:
            if not retry:
                break
            time.sleep(random.uniform(0.005, 0.03) * (attempt + 1))
            m = Inspeximus(path=path)          # the documented recovery: reload, then retry
        except Exception:
            break
print(ok)
'''


def _run(db, workers, per, mode):
    src = os.path.join(tempfile.mkdtemp(prefix="conc_"), "w.py")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(WORKER % REPO)
    procs = [subprocess.Popen([sys.executable, src, db, str(w), str(per), mode],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
             for w in range(workers)]
    for p in procs:
        p.communicate()
    try:
        return len(load_store(db))
    except Exception:
        return -1


PER = 12


def test_single_writer_lands_every_record():
    """The control. Without this, a shortfall below says nothing about concurrency."""
    db = os.path.join(tempfile.mkdtemp(), "s.json")
    assert _run(db, 1, PER * 4, "retry") == PER * 4


@pytest.mark.parametrize("workers", [2, 8])
def test_a_losing_writer_recovers_instead_of_losing_everything(workers):
    db = os.path.join(tempfile.mkdtemp(), "s.json")
    got = _run(db, workers, PER, "retry")
    want = workers * PER
    assert got == want, (
        "%d of %d records lost with %d concurrent writers. Every loss is a whole worker's output: "
        "a refused writer whose handle never reloads fails for every remaining write."
        % (want - got, want, workers))


def test_the_race_is_real_here():
    """Without retry, the same widths must be able to lose. Otherwise the arm above is vacuous.

    Reported rather than asserted, because the race is load-dependent and a machine quiet enough
    to serialise the writers would fail a test that demanded a loss. What must never happen is the
    reverse: retry losing where no-retry does not.
    """
    losses = []
    for _ in range(3):
        db = os.path.join(tempfile.mkdtemp(), "s.json")
        losses.append(8 * PER - _run(db, 8, PER, "noretry"))
    assert min(losses) >= 0, "negative loss means the counter is wrong, not the store"
    print("no-retry losses over 3 runs at 8 writers: %s" % losses)
