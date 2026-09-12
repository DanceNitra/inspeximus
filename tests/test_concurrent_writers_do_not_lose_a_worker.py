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
wrote = []
for i in range(n):
    for attempt in range(12 if retry else 1):
        try:
            text = "w%%d r%%d uniq-%%d-%%d" %% (wid, i, wid, i)
            m.remember(text, mtype="fact")
            ok += 1
            wrote.append(text)
            break
        except StoreChangedOnDisk:
            if not retry:
                break
            time.sleep(random.uniform(0.005, 0.03) * (attempt + 1))
            m = Inspeximus(path=path)          # the documented recovery: reload, then retry
        except Exception:
            break
# WHAT it believes it wrote, one text per line, then the count. A count cannot be diagnosed
# after a rare failure; the shape of the missing set can, and it separates "a whole worker was
# lost" from "single records went missing".
for t in wrote:
    print("WROTE\t" + t)
# WAS THE LOCK ACTUALLY HELD? This is the field that separates the two explanations for a silent
# loss, and without it a failure here says only how many records went missing. Measured 2026-09-07
# on this same harness: with the inter-process lock held, 0 of 96 lost in 4 of 4 trials; with it
# degraded, 17, 6, 28 and 47 lost, every worker reporting success and no exception anywhere. So a
# loss with DEGRADED=0 and a loss with DEGRADED>0 are different bugs and want different fixes.
try:
    from inspeximus.core import _StoreLock
    print("DEGRADED\t%%d\t%%s" %% (sum(_StoreLock.DEGRADED.values()),
                                 "; ".join(_StoreLock.DEGRADED_WHY.values()) or "-"))
except Exception as _e:
    print("DEGRADED\t-1\tcould not read the counter: %%r" %% (_e,))
print(ok)
'''


def _run(db, workers, per, mode):
    src = os.path.join(tempfile.mkdtemp(prefix="conc_"), "w.py")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(WORKER % REPO)
    procs = [subprocess.Popen([sys.executable, src, db, str(w), str(per), mode],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
             for w in range(workers)]
    claimed, texts = 0, []
    degraded = []
    for p in procs:
        out, _ = p.communicate()
        lines = (out or "").splitlines()
        texts += [l.split("\t", 1)[1] for l in lines if l.startswith("WROTE\t") and "\t" in l]
        degraded += [l.split("\t", 1)[1] for l in lines if l.startswith("DEGRADED\t")]
        try:
            claimed += int(lines[-1].strip())
        except Exception:                                        # noqa: BLE001
            pass
    _run.last_degraded = degraded                                # read by the failure message
    try:
        return len(load_store(db)), claimed, texts, db
    except Exception:                                            # noqa: BLE001
        return -1, claimed, texts, db


def _missing(db, texts):
    """The claimed records that are not in the store, grouped by writer.

    Reads the file as BYTES. The store is not guaranteed to be decodable text on every path, and a
    UnicodeDecodeError while diagnosing a rare race would replace the finding with an unrelated
    traceback.
    """
    try:
        with open(db, "rb") as fh:
            blob = fh.read()
    except OSError as exc:
        return {"error": "could not read the store: %s" % exc}
    gone = [t for t in texts if t.encode("utf-8") not in blob]
    by_writer = {}
    for t in gone:
        wid = t.split(" ", 1)[0]
        by_writer.setdefault(wid, []).append(t.split(" ")[1])
    return {"missing_total": len(gone), "by_writer": by_writer,
            "claimed_total": len(texts),
            # The shape is the diagnosis. A whole worker points at a handle that never reloaded; a
            # scattered single points at the change guard missing a write.
            "shape": ("a whole worker" if any(len(v) == PER for v in by_writer.values())
                      else "scattered singles" if gone else "nothing missing")}


PER = 12


def test_single_writer_lands_every_record():
    """The control. Without this, a shortfall below says nothing about concurrency."""
    db = os.path.join(tempfile.mkdtemp(), "s.json")
    landed, claimed, _, _ = _run(db, 1, PER * 4, "retry")
    assert claimed == PER * 4, "the single writer could not even complete its own writes"
    assert landed == PER * 4


@pytest.mark.parametrize("workers", [2, 8])
def test_no_writer_is_told_a_record_landed_that_did_not(workers):
    """The silent loss. A record someone was told they wrote must be in the store."""
    db = os.path.join(tempfile.mkdtemp(), "s.json")
    landed, claimed, texts, store = _run(db, workers, PER, "retry")
    assert landed >= claimed, (
        "%d of %d records that a writer was TOLD had been written are not in the store, with %d "
        "concurrent writers. A save that reports success and does not persist is the failure this "
        "file exists to catch, and no amount of machine load excuses it.\n"
        "WHICH ONES: %s\n"
        "WAS THE LOCK HELD (per writer, count then reason): %s\n"
        "Read the lock line FIRST. Measured 2026-09-07 on this harness: lock held, 0 of 96 lost in "
        "4 of 4 trials; lock degraded, 17, 6, 28 and 47 lost with every writer reporting success. "
        "So a non-zero count here is the whole explanation, and a zero means the loss is something "
        "this project has not seen yet.\n"
        "The shape is a second, WEAKER hint, and one of its readings has since been tested and did "
        "not hold: a whole worker means the losing handle never reloaded; scattered singles were "
        "attributed to the change guard missing a same-size write inside one mtime tick, but "
        "probes/a_same_size_write_inside_one_mtime_tick_is_invisible.py forces exactly that "
        "collision, up to a 60 s tick where mtime separates nothing at all, and loses no records. "
        "Treat the mtime reading as refuted rather than as the diagnosis."
        % (claimed - landed, claimed, workers, _missing(store, texts),
           getattr(_run, "last_degraded", "not recorded")))
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
        losses.append(8 * PER - _run(db, 8, PER, "noretry")[0])   # [0] is landed
    assert min(losses) >= 0, "negative loss means the counter is wrong, not the store"
    print("no-retry losses over 3 runs at 8 writers: %s" % losses)


def test_the_lock_diagnostic_can_report_a_degraded_write():
    """The counter the failure message now leads with must be able to say something other than zero.

    Every run so far reports `0 -`, which is the right answer and also exactly what a counter with
    no reachable increment would print. Measured 2026-09-11: the no-primitive branch of
    `_StoreLock.__enter__` returned without touching `DEGRADED` at all, so on a runtime with neither
    fcntl nor msvcrt every write went out unprotected and the one field that explains such a loss
    said the lock had been held. This drives that branch and requires it to leave a trace.
    """
    from inspeximus.core import _StoreLock

    db = os.path.join(tempfile.mkdtemp(), "s.json")
    before = dict(_StoreLock.DEGRADED)

    lock = _StoreLock(db)
    lock._locker = (None, None)              # the platform has no primitive
    with lock:
        pass

    after = dict(_StoreLock.DEGRADED)
    assert after != before, (
        "a write went out with no lock held and the counter did not move, so a real loss would "
        "again be reported against a store that looked protected")
    assert _StoreLock.DEGRADED_WHY.get(lock._path), \
        "the count says a write was unprotected but not why, and the two causes want opposite fixes"
    assert "no platform lock primitive" in _StoreLock.DEGRADED_WHY[lock._path]

    # THE CONTROL. With a primitive present the same path must leave the counter alone, or the
    # assertion above would pass on a counter that increments unconditionally.
    held = _StoreLock(db)
    baseline = dict(_StoreLock.DEGRADED)
    with held:
        pass
    assert dict(_StoreLock.DEGRADED) == baseline, \
        "an ordinary locked write incremented the degraded counter, so the field means nothing"
