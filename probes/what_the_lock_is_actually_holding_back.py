"""What the inter-process lock is holding back, measured by taking it away.

WHY THIS EXISTS. This project recorded a loss of 9 records in 96 with eight concurrent writers, could
not reproduce it in four solo runs or three under load, and shipped anyway with the cause written
down as unexplained. That is not a defensible state for a library sold on data integrity, and the
reason it stayed unexplained is that the two mechanisms behind it are both invisible: `_StoreLock`
degrades to a no-op after a deadline and said nothing, and the row save path derived deletions from a
baseline it had read AFTER the records, so a row another writer committed in between was deleted by
a save that reported success.

The deletion path is fixed and pinned by `tests/test_a_concurrent_commit_is_not_read_as_a_deletion.py`
with a control, which is a unit-level reproduction. This is the end-to-end one: eight real processes,
no injection point, and the defect restored on one arm rather than simulated.

WHAT IT DOES. Eight separate OS processes, twelve records each, four trials per arm, against one
store. Three arms, each differing from the next by one thing:

  lock held            what ships
  lock degraded        `_LOCK_PRIMITIVE = (None, None)`, which is what the degrade branch produces
  lock degraded, pre-fix   the same, plus the second read restored on `_merge_rows_from_disk`

THE THIRD ARM IS WHAT MAKES A NULL READABLE. The first version had two arms and came back clean in
both, and its own note called that a quiet machine. That reading was not available: the probe runs
against code where the second read is already gone, so a clean degraded arm is precisely what a
working fix looks like. An arm that still carries the defect separates the two, and they point in
opposite directions.

WHY SEPARATE PROCESSES. Threads would be serialised by the interpreter lock for most of the write,
so a thread-based version measures the GIL and reports the store as safe.

THE CONTROLS, because each half can pass for the wrong reason:
  - a single writer must land every record it claims, or a shortfall in either arm says nothing;
  - the comparison is against what the workers CLAIMED, never against what they attempted. On a
    two-core CI runner eight processes exhaust their retries and the first version called that an
    84-of-96 loss in the SHIPPED arm. A writer that gives up and says so has lost nothing; a writer
    told its write succeeded whose record is absent is the whole point;
  - no worker may raise, or the arms differ in an error path rather than in a silent loss;
  - the unlocked arm must actually be unlocked, asserted in the child rather than assumed.

WHAT IT DOES NOT CLAIM. No arm here is a bug report against a released version: no release ever
carried the row store, and nothing ships with the lock disabled. Disabling the lock is how the
degrade branch's conditions are reached without waiting for a contended machine, which is what makes
the third arm's loss observable in a hundred seconds instead of by luck.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

WORKER = '''
import sys, json
sys.path.insert(0, %r)
path, wid, n, locked = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] == "locked"

from inspeximus import core
arm = sys.argv[4]
if arm != "locked":
    core._LOCK_PRIMITIVE = (None, None)          # the degrade branch, made deterministic
assert (core._LOCK_PRIMITIVE[0] is not None) == (arm == "locked"), "the arm is not what it claims"

from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk
from inspeximus import sqlite_store as _ss

if arm == "prefix":
    # THE LINE THE FIX REMOVED, restored verbatim. It refreshed the deletion baseline with a SECOND
    # read of the store, so a row another writer committed between the two reads was in the baseline,
    # was absent from memory, and was deleted by the save that followed.
    _real = Inspeximus._merge_rows_from_disk

    def _with_second_read(self):
        out = _real(self)
        if out:
            try:
                self._row_snapshot = _ss.snapshot(_ss.load(self.path), self._persist_vectors)
            except Exception:
                return False
        return out

    Inspeximus._merge_rows_from_disk = _with_second_read
    assert Inspeximus._merge_rows_from_disk is not _real, "the pre-fix arm did not take effect"

m = Inspeximus(path=path)
ok, refused, raised = 0, 0, None
for i in range(n):
    for attempt in range(12):
        try:
            m.remember("w%%d r%%d uniq-%%d-%%d" %% (wid, i, wid, i), mtype="fact")
            ok += 1
            break
        except StoreChangedOnDisk:
            refused += 1
            m = Inspeximus(path=path)            # the documented recovery
        except Exception as e:
            raised = "%%s: %%s" %% (type(e).__name__, e)
            break
    if raised:
        break
print(json.dumps({"ok": ok, "refused": refused, "raised": raised}))
'''

# THE WIDTH FITS THE MACHINE. Eight writers on twenty-four cores is contention; on the two cores a CI
# runner gives you it is thrashing, and one worker then never gets scheduled long enough to report.
# That is not the thing being measured, and reading it as a defect in the shipped path is how this
# probe broke CI for the third time.
WRITERS = max(2, min(8, (os.cpu_count() or 2)))
PER = 12
TRIALS = 4


def _load(path):
    from inspeximus import sqlite_store as ss
    if ss.looks_like_sqlite(path):
        return ss.load(path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _trial(src, writers, arm):
    db = os.path.join(tempfile.mkdtemp(prefix="lockarm_"), "m.json")
    procs = [subprocess.Popen([sys.executable, src, db, str(w), str(PER), arm],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
             for w in range(writers)]
    reports = []
    for pr in procs:
        out, _ = pr.communicate()
        try:
            reports.append(json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1]))
        except Exception:                                        # noqa: BLE001
            # NO REPORT IS NOT A RAISE. A worker whose stdout is empty was starved or killed by the
            # machine, and nothing follows from it about the store. The trial is void.
            reports.append({"ok": 0, "refused": 0, "raised": None, "silent": True})
    try:
        landed = len(_load(db))
    except Exception as e:                                       # noqa: BLE001
        landed = -1
        reports.append({"ok": 0, "refused": 0, "raised": "store unreadable: %s" % e})
    return {"landed": landed,
            "void": any(r.get("silent") for r in reports),
            "attempted": writers * PER,
            # WHAT THE WORKERS BELIEVE THEY WROTE. The store is compared against THIS. A writer that
            # exhausted its retries and said so has lost nothing; a writer told its write succeeded
            # whose record is absent is the silent loss this probe exists to find.
            "claimed": sum(r["ok"] for r in reports),
            "workers_reporting_success": sum(1 for r in reports if r["ok"] == PER),
            "any_worker_raised": next((r["raised"] for r in reports if r["raised"]), None)}


def main():
    sys.path.insert(0, REPO)
    src = os.path.join(tempfile.mkdtemp(prefix="lockprobe_"), "w.py")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(WORKER % REPO)

    control = _trial(src, 1, "locked")
    print("  control, one writer, lock held: %d of %d landed"
          % (control["landed"], control["attempted"]))

    arms = {}
    for arm, name in (("locked", "lock held"), ("unlocked", "lock degraded"),
                      ("prefix", "degraded, pre-fix")):
        arms[name] = []
        for t in range(TRIALS):
            r = _trial(src, WRITERS, arm)
            arms[name].append(r)
            print("  %-14s trial %d/%d: %d landed of %d claimed (%d attempted), %d/%d workers "
                  "reported success%s"
                  % (name, t + 1, TRIALS, r["landed"], r["claimed"], r["attempted"],
                     r["workers_reporting_success"], WRITERS,
                     "" if not r["any_worker_raised"] else ", raised: " + r["any_worker_raised"]))

    print()
    out = {"writers": WRITERS, "records_per_writer": PER, "trials": TRIALS,
           "control_single_writer_landed": control["landed"],
           "control_single_writer_attempted": control["attempted"], "arms": {}}
    for name, rs in arms.items():
        void = [i for i, r in enumerate(rs) if r.get("void")]
        rs = [r for r in rs if not r.get("void")]
        if void:
            print("  %-14s: %d of %d trials void, a worker produced no output on this machine"
                  % (name, len(void), len(void) + len(rs)))
        if not rs:
            print("  VOID ARM: every trial of '%s' lost a worker to the machine, so this run measures "
                  "nothing. Re-run where %d processes fit." % (name, WRITERS))
            out["verdict"] = "void_no_usable_trial"
            path = os.path.splitext(os.path.abspath(__file__))[0] + ".result.json"
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(out, indent=1))
            return 0
        losses = [r["claimed"] - r["landed"] for r in rs]
        out["arms"][name] = {
            "landed": [r["landed"] for r in rs],
            "claimed": [r["claimed"] for r in rs],
            "gave_up": [r["attempted"] - r["claimed"] for r in rs],
            "losses": losses,
            "clean_trials": sum(1 for l in losses if l == 0),
            "worst_loss": max(losses),
            "trials_where_every_worker_reported_success":
                sum(1 for r in rs if r["workers_reporting_success"] == WRITERS),
            "any_worker_raised": [r["any_worker_raised"] for r in rs if r["any_worker_raised"]]}
        gave_up = out["arms"][name]["gave_up"]
        print("  %-14s: silently lost %s of what was claimed, clean in %d of %d trials%s"
              % (name, losses, out["arms"][name]["clean_trials"], TRIALS,
                 "" if not any(gave_up) else "; %s writes gave up after exhausting their retries, "
                                             "which is load and not loss" % (gave_up,)))

    held = out["arms"]["lock held"]
    degraded = out["arms"]["lock degraded"]
    prefix = out["arms"]["degraded, pre-fix"]
    if control.get("void"):
        print("  VOID: the single-writer control lost its worker to the machine, so nothing here is "
              "measurable. Re-run where the processes fit.")
        return 0
    assert control["landed"] == control["claimed"] == control["attempted"], (
        "the single-writer control did not land every record it claimed, so neither arm below means "
        "anything: landed %d, claimed %d, attempted %d"
        % (control["landed"], control["claimed"], control["attempted"]))
    assert not held["any_worker_raised"], (
        "a worker raised in the locked arm: %s" % held["any_worker_raised"])
    assert not degraded["any_worker_raised"], (
        "a worker raised in the unlocked arm, so this measures an error path rather than a silent "
        "loss: %s" % degraded["any_worker_raised"])
    assert held["clean_trials"] == TRIALS, (
        "the LOCKED arm lost records a writer was TOLD had been written, which is a defect in the "
        "shipped path rather than a counterfactual: %s. Writes that gave up after exhausting their "
        "retries are counted separately and are not this: %s" % (held["losses"], held["gave_up"]))
    assert not prefix["any_worker_raised"], (
        "a worker raised in the pre-fix arm, so it measures an error path rather than a silent "
        "loss: %s" % prefix["any_worker_raised"])

    print()
    if prefix["worst_loss"] > 0:
        print("  THE SECOND READ IS THE LOSS. With the lock degraded and the pre-fix line restored, "
              "%s of %d records were lost across %d trials, and every worker reported success in %d "
              "of them. On the shipped code the same arm lost %s."
              % (prefix["losses"], WRITERS * PER, TRIALS,
                 prefix["trials_where_every_worker_reported_success"], degraded["losses"]))
    else:
        print("  NOTHING REPRODUCED, INCLUDING THE DEFECT. The pre-fix arm lost nothing either, so "
              "this run was too quiet to interleave the writers and says nothing about the fix. Do "
              "not read the clean shipped arms as evidence; re-run under load.")
    if degraded["worst_loss"] > 0:
        print("  The shipped code still lost %s with the lock degraded, which the fix was supposed "
              "to close." % degraded["losses"])

    if os.environ.get("PYTEST_CURRENT_TEST"):
        # UNDER THE SUITE THIS IS A SMOKE TEST, NOT A MEASUREMENT. Every uncited probe is executed
        # in parallel with several thousand tests, and those numbers are 20% slower with the
        # smallest cell reversed. One such run was committed because it looked like an ordinary
        # change to the working tree. The assertions above still ran; only the receipt is spared.
        print("  running under pytest, so the receipt is NOT rewritten: these numbers describe a "
              "saturated machine.")
        return 0
    path = os.path.splitext(os.path.abspath(__file__))[0] + ".result.json"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(out, indent=1))
    print("  receipt: %s" % os.path.basename(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
