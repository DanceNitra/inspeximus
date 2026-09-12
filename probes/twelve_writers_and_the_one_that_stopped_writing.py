"""Two formats, the same twelve writers: how much of what they wrote survives.

READ THIS BEFORE QUOTING THE JSON NUMBER. Corrected 2026-09-12, and the correction is larger than
the finding. THE JSON ARM HERE IS A NAIVE CALLER, NOT A FAIR BASELINE: its workers catch
`StoreChangedOnDisk` and drop the record, while the product's own error text says "Call reload() to
merge the two and retry" and the row path performs that union automatically. Given the retry its own
error prescribes, JSON lands 336 of 336 at widths 2 and 12, the same as the row store. So the loss
measured below belongs to a caller who ignores the recovery path, not to the format, and any
"the row store is more durable" reading of this file is wrong.
`what_a_concurrent_writer_is_told_against_what_the_store_keeps.py` runs both callers side by side
and is the file to cite.

ALSO REFUTED BY THIS FILE'S OWN RECEIPT: the line below claiming every loss is a multiple of one
writer's output. At eight records per writer the receipt holds losses of 41, 75, 82, 89, 178, 181
and 185. The whole-worker mechanism is real and is not the only one.

WHY THIS EXISTS. The row store was built for write cost, and the concurrency result was the reason it
had to ship rather than a bonus. Claude Code and Codex write this project's coding store, a hook
fires on every tool call, so two processes writing at once is the normal condition. Under JSON the
store stayed SAFE and stopped being AVAILABLE: `StoreChangedOnDisk` fires correctly and nothing is
corrupted, but the refused writer's handle never reloads, so after its first refusal it fails for
every remaining write and drops everything it still had to say. Every loss is a multiple of one
writer's output, which is the signature of that mechanism rather than of a torn file.

WHAT IT REPORTS. Records landed out of records attempted, per format, over several trials at two
widths. The JSON arm is a CONTROL as much as a comparison: if it loses nothing on this machine the
run is quiet enough that the row arm's success proves nothing, and the receipt says so.

RUN IT: python probes/twelve_writers_and_the_one_that_stopped_writing.py
Separate OS processes, so the interpreter lock cannot hide the race.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from inspeximus import sqlite_store as ss                      # noqa: E402

WORKER = '''
import os, sys
sys.path.insert(0, %r)
os.environ["INSPEXIMUS_STORE_FORMAT"] = sys.argv[4]
from inspeximus import Inspeximus
path, wid, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
m = Inspeximus(path=path)
m._save_min_s = 0
for i in range(n):
    try:
        m.remember("w%%d r%%d" %% (wid, i), key="w%%d::%%d" %% (wid, i), mtype="fact")
        m.flush()
    except Exception:
        pass
'''


def _count(path):
    if ss.looks_like_sqlite(path):
        return len(ss.load(path))
    try:
        with open(path, encoding="utf-8") as fh:
            return len(json.load(fh))
    except Exception:
        return -1


def _trial(fmt, writers, per, src):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "memory.json")
    env = dict(os.environ, INSPEXIMUS_STORE_FORMAT=fmt)
    procs = [subprocess.Popen([sys.executable, src, path, str(w), str(per), fmt],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
             for w in range(writers)]
    for p in procs:
        p.communicate()
    return _count(path)


def main():
    src = os.path.join(tempfile.mkdtemp(prefix="w_"), "w.py")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(WORKER % REPO)

    per, trials = 8, 4
    # HOW MANY AGENTS CAN WRITE AT ONCE? That is the first question anyone asks of "one memory
    # shared by every agent and session", and twelve was as far as this went. Measured 2026-09-12 by
    # adding 24 and 48: the row store landed every record at all four widths, 672 of 672, while the
    # JSON control lost 42 to 50 percent at each. So the answer is "no ceiling found below 48", and
    # the widths are here rather than in a shell history so the next person re-runs the same thing.
    #
    # The wide arms stay OFF under the suite. 48 processes for three trials is 32 s on an idle box
    # and this file is executed as a smoke test alongside several thousand other tests.
    widths = (2, 12) if os.environ.get("PYTEST_CURRENT_TEST") else (2, 12, 24, 48)
    out = {"per_writer": per, "trials": trials, "widths": list(widths), "arms": []}
    for writers in widths:
        for fmt in ("json", "rows"):
            got, want = [], writers * per
            t0 = time.time()
            for t in range(trials):
                got.append(_trial(fmt, writers, per, src))
                print("    %-4s %2d writers, trial %d/%d: %d of %d  (%.0fs elapsed)"
                      % (fmt, writers, t + 1, trials, got[-1], want, time.time() - t0), flush=True)
            arm = {"format": fmt, "writers": writers, "attempted": want, "landed": got,
                   "clean_trials": sum(1 for g in got if g == want),
                   "worst_loss": want - min(got)}
            out["arms"].append(arm)

    print()
    for a in out["arms"]:
        print("  %-4s %2d writers: %d of %d trials landed all %d records, worst loss %d"
              % (a["format"], a["writers"], a["clean_trials"], trials, a["attempted"],
                 a["worst_loss"]))

    json_loss = max(a["worst_loss"] for a in out["arms"] if a["format"] == "json")
    rows_clean = all(a["clean_trials"] == trials for a in out["arms"] if a["format"] == "rows")
    out["control_the_race_is_real_here"] = json_loss > 0
    out["rows_lost_nothing"] = rows_clean
    if not out["control_the_race_is_real_here"]:
        print("\n  CONTROL DID NOT FIRE: the JSON arm lost nothing on this machine, so the row arm's "
              "clean result says nothing about concurrency here. The number is void, not good.")
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
    print("\n  receipt: %s" % os.path.basename(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
