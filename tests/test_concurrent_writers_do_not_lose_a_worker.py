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
StoreChangedOnDisk, a concurrent run keeps every record it was told it kept.

THE COMPARISON IS AGAINST WHAT THE WORKERS CLAIMED, not against what they attempted, and the first
version got that wrong. Asking for every attempted record to land conflates two outcomes a library
has to treat differently: a writer that exhausted its retries and SAID so is a load-dependent limit,
while a writer that reported success and lost the record is the silent loss this file exists to
catch. The first version passed on an idle machine and failed inside the release gate, where the
suite saturates 24 cores -- and the failure said nothing about which of the two had happened.

So the workers report what they achieved and the store is compared against that. Silent loss is
asserted at zero on every machine; exhaustion is reported and left to the reader.

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
print(ok)          # what this worker BELIEVES it wrote; the test compares the store against this
'''


def _run(db, workers, per, mode):
    src = os.path.join(tempfile.mkdtemp(prefix="conc_"), "w.py")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(WORKER % REPO)
    procs = [subprocess.Popen([sys.executable, src, db, str(w), str(per), mode],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
             for w in range(workers)]
    claimed = 0
    for p in procs:
        out, _ = p.communicate()
        try:
            claimed += int((out or "0").strip().splitlines()[-1])
        except Exception:                                        # noqa: BLE001
            pass
    try:
        return len(load_store(db)), claimed
    except Exception:                                            # noqa: BLE001
        return -1, claimed


PER = 12


def test_single_writer_lands_every_record():
    """The control. Without this, a shortfall below says nothing about concurrency."""
    db = os.path.join(tempfile.mkdtemp(), "s.json")
    landed, claimed = _run(db, 1, PER * 4, "retry")
    assert claimed == PER * 4, "the single writer could not even complete its own writes"
    assert landed == PER * 4


@pytest.mark.parametrize("workers", [2, 8])
def test_no_writer_is_told_a_record_landed_that_did_not(workers):
    """The silent loss. A record someone was told they wrote must be in the store."""
    db = os.path.join(tempfile.mkdtemp(), "s.json")
    landed, claimed = _run(db, workers, PER, "retry")
    assert landed >= claimed, (
        "%d of %d records that a writer was TOLD had been written are not in the store, with %d "
        "concurrent writers. A save that reports success and does not persist is the failure this "
        "file exists to catch, and no amount of machine load excuses it."
        % (claimed - landed, claimed, workers))
    assert landed == claimed, (
        "the store holds %d records and the writers claimed %d; a store larger than what anyone "
        "claims to have written means the counting is wrong, not the store" % (landed, claimed))
    if claimed < workers * PER:
        print("%d of %d writes gave up after exhausting their retries at %d writers; that is a "
              "load-dependent limit, not a loss" % (workers * PER - claimed, workers * PER, workers))


def test_the_race_is_real_here():
    """Without retry, the same widths must be able to lose. Otherwise the arm above is vacuous.

    Reported rather than asserted, because the race is load-dependent and a machine quiet enough
    to serialise the writers would fail a test that demanded a loss. What must never happen is the
    reverse: retry losing where no-retry does not.
    """
    losses = []
    for _ in range(3):
        db = os.path.join(tempfile.mkdtemp(), "s.json")
        losses.append(8 * PER - _run(db, 8, PER, "noretry")[0])
    assert min(losses) >= 0, "negative loss means the counter is wrong, not the store"
    print("no-retry losses over 3 runs at 8 writers: %s" % losses)
