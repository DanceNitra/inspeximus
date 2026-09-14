"""Two candidate causes of the silent loss, measured against each other under one machine load.

WHY THIS EXISTS. `what_a_concurrent_writer_is_told_against_what_the_store_keeps.py` loses records on
the JSON arms with the lock HELD on every write, so the degraded-lock path this project already
understands does not explain it. Two suspects remained, and both turned out to be real.

SUSPECT 1, THE GUARD'S FIELDS. `_save` refuses to overwrite a file that changed since this handle
loaded it, and it decides "changed" from `(st_mtime_ns, st_size)`. Two writes agreeing on both are
invisible, and a JSON save rewrites the whole file, so the invisible one is erased. Measured here:
mtime_ns advances in steps of about 0.5 to 1.5 ms, and roughly 8 percent of same-length writes land
on a signature that already carries different content.

SUSPECT 2, THE ORDER OF THE READ AND THE STAMP. `_load_from_disk` read the file and stamped the
signature afterwards, with no lock across the two. A writer replacing the file in between left the
handle holding the OLD records under the NEW signature, so the guard compared them, saw no change,
and rewrote the store from a stale view. Fixed 2026-09-12 by stamping before the read, which can
only fail the safe way: a false refusal the caller retries.
`tests/test_a_write_between_the_read_and_the_signature_is_invisible.py` reproduces it exactly.

WHAT THIS PROBE DOES. Three arms, differing by one thing each, with their trials INTERLEAVED so a
change in machine load hits all three equally. Two earlier runs of this file were confounded exactly
that way: one shared the box with a 14-minute test suite and the other with an edit to the code it
was measuring, and they disagreed by an order of magnitude on the same arm.

    old-order   restores the pre-fix stamping, so suspect 2 is present
    fixed       the code as it ships
    widened     the shipped code plus st_ino in the signature, so suspect 1 is removed as well

WHAT IT FOUND (the receipt at 2e8483a, three arms). 30 interleaved rounds, 12 writers, 8 records
each: old-order lost 9 of 2,880 records a writer had been told were stored, fixed lost 0 of 2,864,
and widened lost 0 of 2,840. The tracked receipt was re-recorded with five arms on 2026-09-13 and
reads 5 of 2,864 for old-order. Suspect 2 accounts for the loss; suspect 1 adds nothing on top of
the fix, so `_stat_sig` is left alone. The collision measured below is a real property of the guard
and is NOT a measured loss rate: do not quote it as one. And "adds nothing" is scoped to THIS
workload: every writer appends, so every landed write is larger than the file it was checked
against, and (mtime_ns, size) cannot collide except through the ordering defect itself. Same-size
rewrites inside one mtime tick are the regime where widening would matter, and it is unmeasured.

WHY st_ino AND NOT A CONTENT HASH. A hash of the file is exact and costs a full read of the store on
every save; on this project's own 32,545-record store that is tens of megabytes per write.
`os.replace` points the name at a NEW file, so the inode changes on every atomic write, on NTFS and
on ext4 alike, and it arrives in a stat call the guard already makes. The hole it leaves is inode
REUSE: a freed inode handed straight back to the next temp file restores the collision. The
per-field collision rate is measured on whatever filesystem this runs on, so CI answers that for
ext4 rather than leaving it to an assumption made on Windows.

    python probes/does_a_wider_change_signature_stop_the_silent_loss.py
    python probes/does_a_wider_change_signature_stop_the_silent_loss.py --trials 60 --writers 12
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from _receipt import write_receipt  # noqa: E402

#: Two arms added 2026-09-13, and the reason is the instrument itself. The three arms above retry a
#: refused write by OPENING A NEW HANDLE on a JSON store, so they never enter `_merge_with_disk`,
#: and that is where the defect survived: it re-stamped the signature after its read, one call site
#: over from the 2.27.1 fix. CI caught it (1 of 96, lock held, ordering fixed) while this probe kept
#: reporting 0, because this probe never ran the code that lost the record. The two `reload-*` arms
#: reach `_merge_with_disk` through `reload()`, the remedy the error message names.
#:
#: Two more arms added 2026-09-14, after a verify pass read the CI harness instead of my account of
#: it. The CI test does NOT call `reload()`; it reopens, like the first three arms. What differs is
#: the store: CI runs the default ROW store, whose `_save` merges through `_merge_with_disk` on its
#: own when the signature is refused, so a reopening writer reaches the re-stamp without ever
#: calling `reload()`. The `rows-*` arms run that path: default store format, reopen on refusal.
ARMS = ("old-order", "fixed", "widened", "reload-old", "reload-fixed", "rows-old", "rows-fixed")

#: Records are padded to one length, so in the signature-field measurement below the size field
#: cannot rescue the guard and the question stays "can mtime separate these two writes". In the
#: concurrent arms the file still grows with every landed write, so size does separate states there.
WORKER = '''
import os, sys, time, random
sys.path.insert(0, %(repo)r)
ARM = %(arm)r
if not ARM.startswith("rows"):
    os.environ["INSPEXIMUS_STORE_FORMAT"] = "json"
from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk
if ARM == "old-order":
    # RESTORE THE PRE-FIX ORDERING, and nothing else. Until 2026-09-12 the loader stamped the file
    # signature AFTER reading the file, so a write landing during the read left the handle holding
    # the old records under the new signature.
    _orig_load = Inspeximus._load_from_disk
    def _stamp_after_the_read(self):
        _orig_load(self)
        self._file_sig = self._stat_sig()
    Inspeximus._load_from_disk = _stamp_after_the_read
if ARM in ("reload-old", "rows-old"):
    # RESTORE _merge_with_disk's post-read re-stamp, and nothing else: the tree's _load_from_disk
    # keeps the 2.27.1 ordering, so any loss here is the re-stamp's own. reload-old reaches it
    # through reload(); rows-old reaches it through the row store's own save.
    _orig_merge = Inspeximus._merge_with_disk
    def _merge_then_restamp(self):
        out = _orig_merge(self)
        self._file_sig = self._stat_sig()
        return out
    Inspeximus._merge_with_disk = _merge_then_restamp
if ARM == "widened":
    # ONE EXTRA FIELD, from the stat call the guard already makes: same file, same bytes, same lock.
    def _wide(self):
        try:
            st = self.path.stat()
            return (st.st_mtime_ns, st.st_size, st.st_ino)
        except AttributeError:
            return None
        except OSError:
            return Inspeximus._ABSENT
    Inspeximus._stat_sig = _wide

path, wid, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
m = Inspeximus(path=path)
m._save_min_s = 0
wrote = []
for i in range(n):
    text = "w%%03d r%%03d uniq-%%03d-%%03d" %% (wid, i, wid, i)
    for attempt in range(12):
        try:
            m.remember(text, key="w%%03d::%%03d" %% (wid, i), mtype="fact")
            m.flush()
            wrote.append(text)
            break
        except StoreChangedOnDisk:
            # Take the remedy the product's own error prescribes, so a refusal is never counted as
            # a silent loss. What is left over is only what a writer was TOLD had been stored.
            time.sleep(random.uniform(0.002, 0.02) * (attempt + 1))
            if ARM.startswith("reload"):
                try:
                    m.reload()                # the remedy the error message names
                except StoreChangedOnDisk:
                    pass                      # counted on the next attempt, never as a loss
            else:
                m = Inspeximus(path=path)
                m._save_min_s = 0
        except Exception:
            break
for t in wrote:
    print("WROTE" + chr(9) + t)
'''


def measure_signature_fields(tries: int = 1500) -> dict:
    """Collision rate per candidate signature on THIS filesystem, using the store's own write shape.

    Reported rather than assumed: the answer differs between NTFS and ext4 and this file runs on
    both. A collision is one signature carrying more than one distinct file content.
    """
    d = tempfile.mkdtemp(prefix="sigfields_")
    p = os.path.join(d, "x.json")
    fields = {
        "mtime+size": lambda st, b: (st.st_mtime_ns, st.st_size),
        "mtime+size+ino": lambda st, b: (st.st_mtime_ns, st.st_size, st.st_ino),
        "mtime+size+sha256": lambda st, b: (st.st_mtime_ns, st.st_size,
                                            hashlib.sha256(b).hexdigest()),
    }
    seen: dict = {k: {} for k in fields}
    ticks = []
    for i in range(tries):
        body = json.dumps([{"t": "w%02d r%02d uniq" % (i % 90, (i * 7) % 90)}]).encode("utf-8")
        tmp = p + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(body)
        os.replace(tmp, p)
        st = os.stat(p)
        ticks.append(st.st_mtime_ns)
        for name, fn in fields.items():
            seen[name].setdefault(fn(st, body), set()).add(body)
    gaps = sorted(b - a for a, b in zip(ticks, ticks[1:]) if b > a)
    return {"writes": tries,
            "collisions": {k: sum(1 for v in seen[k].values() if len(v) > 1) for k in fields},
            "mtime_tick_ns_min": gaps[0] if gaps else None,
            "mtime_tick_ns_median": gaps[len(gaps) // 2] if gaps else None}


def run_trial(arm: str, writers: int, per: int) -> dict:
    work = tempfile.mkdtemp(prefix="widesig_")
    src = os.path.join(work, "w.py")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(WORKER % {"repo": REPO, "arm": arm})
    db = os.path.join(work, "s.json")

    t0 = time.time()
    procs = [subprocess.Popen([sys.executable, src, db, str(w), str(per)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                              encoding="utf-8", errors="replace")
             for w in range(writers)]
    claimed = []
    for p in procs:
        out, _ = p.communicate()
        claimed += [ln.split("\t", 1)[1] for ln in (out or "").splitlines()
                    if ln.startswith("WROTE\t")]

    try:
        with open(db, "rb") as fh:
            blob = fh.read()
    except OSError:
        blob = b""
    missing = [t for t in claimed if t.encode("utf-8") not in blob]
    return {"arm": arm, "writers": writers, "per_writer": per,
            "claimed": len(claimed), "missing": len(missing),
            "seconds": round(time.time() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--writers", type=int, default=12)
    ap.add_argument("--per", type=int, default=8)
    # The loss is rare, on the order of one record per several hundred. One round per arm is a smoke
    # test, which is what the suite needs; an investigation passes --trials and gets a sample.
    ap.add_argument("--trials", type=int,
                    default=1 if os.environ.get("PYTEST_CURRENT_TEST") else 24)
    ap.add_argument("--arms", default=None,
                    help="comma separated subset of arms to run; the others are recorded as not run")
    ap.add_argument("--receipt", default=None,
                    help="receipt file name beside this probe (default: <probe>.result.json)")
    a = ap.parse_args()
    arms = tuple(x for x in a.arms.split(",") if x) if a.arms else ARMS
    assert all(x in ARMS for x in arms), arms

    out = {"probe": os.path.basename(__file__),
           "question": "which of the two candidate causes accounts for the silent loss",
           "writers": a.writers, "per_writer": a.per, "trials": a.trials,
           "arms": {arm: {"claimed": 0, "missing": 0, "trials": []} for arm in ARMS}}

    print("  measuring what separates two writes on this filesystem ...", flush=True)
    out["signature_fields"] = measure_signature_fields()
    sf = out["signature_fields"]
    for name, n in sf["collisions"].items():
        print("    %-20s %d of %d writes shared a signature with different content"
              % (name, n, sf["writes"]))
    print("    mtime tick: min %s ns, median %s ns"
          % (sf["mtime_tick_ns_min"], sf["mtime_tick_ns_median"]))
    print()

    # INTERLEAVED, one round at a time. Running each arm to completion in turn lets a background job
    # or a thermal change land on one arm and not the others, and two earlier runs of this file were
    # spoiled that way.
    for t in range(a.trials):
        for arm in arms:
            r = run_trial(arm, a.writers, a.per)
            slot = out["arms"][arm]
            slot["trials"].append(r)
            slot["claimed"] += r["claimed"]
            slot["missing"] += r["missing"]
            print("  round %2d/%d  %-9s claimed %3d, MISSING %d  (%.0fs)"
                  % (t + 1, a.trials, arm, r["claimed"], r["missing"], r["seconds"]), flush=True)

    print()
    out["arms_run"] = list(arms)
    for arm in arms:
        s = out["arms"][arm]
        print("  %-9s lost %2d of %d records a writer was told were stored"
              % (arm, s["missing"], s["claimed"]))

    old, fixed, wide, rold, rfixed, wold, wfixed = (out["arms"][k]["missing"] for k in ARMS)
    out["rows_verdict"] = (
        "row store, reopen on refusal: lost %d with the re-stamp, %d without it" % (wold, wfixed)
        + ("; the re-stamp is a live cause on the CI path" if wold > 0 and wfixed == 0 else
           "; VOID, the arm carrying the re-stamp lost nothing, raise --trials" if wold == 0 else
           "; the fix did not remove all of it, look further"))
    out["reload_verdict"] = (
        "reload() re-stamp: lost %d with the re-stamp, %d without it" % (rold, rfixed)
        + ("; the re-stamp is a live cause" if rold > 0 and rfixed == 0 else
           "; VOID, the arm carrying the re-stamp lost nothing, raise --trials" if rold == 0 else
           "; the fix did not remove all of it, look further"))
    if old == 0:
        out["verdict"] = ("VOID: the arm carrying the known defect lost nothing, so this run was too "
                          "quiet to rank anything. The loss is rare; raise --trials.")
    elif fixed == 0 and wide == 0:
        out["verdict"] = ("the read-and-stamp ordering accounts for the whole loss in this run; the "
                          "signature's fields added nothing on top of it.")
    elif fixed > 0 and wide < fixed:
        out["verdict"] = ("BOTH causes are real: the ordering fix left %d losses and widening the "
                          "signature removed %d more of them." % (fixed, fixed - wide))
    elif fixed > 0 and wide >= fixed:
        out["verdict"] = ("the ordering fix left %d losses and widening the signature did not "
                          "reduce them, so a third mechanism is in play." % fixed)
    else:
        out["verdict"] = "inconclusive: old %d, fixed %d, widened %d" % (old, fixed, wide)
    print("  " + out["verdict"])
    write_receipt(__file__, out, name=a.receipt)

    # A finding goes to stderr too. The suite runs every uncited probe, keeps only the stderr tail on
    # a non-zero exit, and suppresses the receipt, so a stdout-only finding is lost exactly when it
    # matters. The exit code answers "can this run be trusted", never "what did it find": only an arm
    # that failed to reproduce the known defect makes the experiment unusable.
    if old or fixed or wide:
        sys.stderr.write(out["verdict"] + "\n" + json.dumps(
            {k: out["arms"][k]["missing"] for k in ARMS} | {"signature_fields": sf}, indent=1) + "\n")
    # UNDER THE SUITE THIS IS A SMOKE TEST, so a quiet run is not a failure. The default there is one
    # round, and the defect lands about once per 320 records, so demanding that it appear would fail
    # the suite most of the time and would be measuring the sample size rather than the store. Run
    # standalone, a run where the arm carrying the known defect stays clean IS unusable, because
    # every comparison below it is then a comparison of three zeroes.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return 0
    return 2 if old == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
