"""The change guard compares (mtime_ns, size). Two writes that agree on both are invisible to it.

WHY THIS EXISTS. `tests/test_concurrent_writers_do_not_lose_a_worker.py` fails intermittently on CI
and has never failed on the machine this was written on. Its own message names the suspect: a
"scattered singles" loss shape means the change guard missed a write, and the guard's signature is
`(st_mtime_ns, st_size)`.

A guard keyed on mtime can only separate two writes that the filesystem timestamps apart. Two writes
inside one tick share a timestamp, and if they also share a size the guard sees no change at all and
the second write replaces the first. A writer is told `remember()` succeeded and its record is not
in the store.

CORRECTED 2026-09-12, AND THE CORRECTION VOIDS THIS FILE'S CONTROL. This paragraph used to say NTFS
"gives every write its own mtime, so the collision essentially never happens here", and treated the
defect as something only a GitHub runner could show. Measured on this machine, 1,500 same-length
atomic writes to a temp file: mtime_ns advances in steps of 0.50 to 1.52 ms, and 165 of those writes
landed on a signature that already carried different content. So the collision happens here at
roughly one write in nine, and the `--granularity-ns 1` arm below, offered as the control, rounds a
clock that already ticks at 500,000 ns. It changes nothing, which makes it a no-op rather than a
control: at native granularity both arms run in the same environment.

The paired arm that IS a control lives in `does_a_wider_change_signature_stop_the_silent_loss.py`,
which holds the environment fixed and changes the guard instead.

WHAT THIS PROBE DOES. It stops waiting for the coincidence and produces it. `--granularity-ns`
models the filesystem's timestamp resolution by rounding mtime down, exactly as a coarse-mtime
filesystem does. At 1 ns it is this machine. At 1 s it is a filesystem that cannot separate any two
writes in the same second, which is the worst case the guard has to survive.

WHAT THE 1 ns ARM IS WORTH. It is a floor, not a control: it says the harness does not lose records
on its own at whatever resolution the filesystem happens to offer. Read a loss at 1 s as the
mechanism amplified, and read a clean 1 ns arm as "this workload is too quiet to show it", never as
"the guard is sound here".

    python probes/a_same_size_write_inside_one_mtime_tick_is_invisible.py
    python probes/a_same_size_write_inside_one_mtime_tick_is_invisible.py --granularity-ns 1000000000
"""
from __future__ import annotations

import argparse
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

#: Every record is padded to the same rendered length, so two writes differ in mtime or in nothing.
#: A size that moves would let the guard notice the change through its other field and the probe
#: would be measuring the padding rather than the clock.
WORKER = '''
import os, sys, time, random
sys.path.insert(0, %(repo)r)
GRAN = %(gran)d
if GRAN > 1:
    # Model a filesystem whose mtime advances in ticks of GRAN ns. This replaces the RESOLUTION of
    # the timestamp the guard reads and nothing else: same file, same bytes, same lock, same code.
    import pathlib
    _real = pathlib.Path.stat
    class _Coarse:
        def __init__(self, st): self._st = st
        def __getattr__(self, n): return getattr(self._st, n)
        @property
        def st_mtime_ns(self): return (self._st.st_mtime_ns // GRAN) * GRAN
    def _stat(self, *a, **k): return _Coarse(_real(self, *a, **k))
    pathlib.Path.stat = _stat

from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk
path, wid, n, = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
m = Inspeximus(path=path)
wrote = []
for i in range(n):
    for attempt in range(12):
        try:
            text = "w%%03d r%%03d uniq-%%03d-%%03d" %% (wid, i, wid, i)
            m.remember(text, mtype="fact")
            wrote.append(text)
            break
        except StoreChangedOnDisk:
            time.sleep(random.uniform(0.005, 0.03) * (attempt + 1))
            m = Inspeximus(path=path)
        except Exception:
            break
for t in wrote:
    print("WROTE\\t" + t)
'''


def run_arm(workers: int, per: int, gran: int) -> dict:
    """Start `workers` writers on one store and report what was claimed against what landed."""
    work = tempfile.mkdtemp(prefix="mtick_")
    src = os.path.join(work, "w.py")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(WORKER % {"repo": REPO, "gran": gran})
    db = os.path.join(work, "s.json")

    t0 = time.time()
    procs = [subprocess.Popen([sys.executable, src, db, str(w), str(per)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                              encoding="utf-8", errors="replace")
             for w in range(workers)]
    claimed = []
    for p in procs:
        out, _ = p.communicate()
        claimed += [l.split("\t", 1)[1] for l in (out or "").splitlines()
                    if l.startswith("WROTE\t") and "\t" in l]

    try:
        with open(db, "rb") as fh:
            blob = fh.read()
    except OSError:
        blob = b""
    missing = [t for t in claimed if t.encode("utf-8") not in blob]
    by_writer: dict[str, list[str]] = {}
    for t in missing:
        by_writer.setdefault(t.split(" ", 1)[0], []).append(t.split(" ")[1])

    return {"granularity_ns": gran,
            "workers": workers,
            "per_writer": per,
            "claimed": len(claimed),
            "missing": len(missing),
            "by_writer": by_writer,
            "shape": ("a whole worker" if any(len(v) == per for v in by_writer.values())
                      else "scattered singles" if missing else "nothing missing"),
            "seconds": round(time.time() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--per", type=int, default=12)
    # Under the suite this runs as a smoke test on a machine already saturated by several thousand
    # other tests, so six four-process trials is a cost the suite pays on every run for a question
    # nobody asked it. One trial per arm still exercises both paths; a person investigating passes
    # --trials and gets the sample size they need.
    ap.add_argument("--trials", type=int,
                    default=1 if os.environ.get("PYTEST_CURRENT_TEST") else 3)
    ap.add_argument("--granularity-ns", type=int, default=None,
                    help="run ONE arm at this mtime resolution instead of the paired comparison")
    a = ap.parse_args()

    if a.granularity_ns is not None:
        r = run_arm(a.workers, a.per, a.granularity_ns)
        print(json.dumps(r, indent=1))
        return 0 if r["missing"] == 0 else 1

    # THE PAIRED COMPARISON. 1 ns is this machine's own resolution and is the control; 1 s is a
    # filesystem that cannot separate two writes in the same second.
    out = {"probe": os.path.basename(__file__),
           "question": "can the change guard see a same-size write inside one mtime tick",
           "control_1ns": [], "coarse_1s": []}
    for i in range(a.trials):
        c = run_arm(a.workers, a.per, 1)
        out["control_1ns"].append(c)
        print("  control  1ns  trial %d: claimed %d, missing %d (%s, %ss)"
              % (i + 1, c["claimed"], c["missing"], c["shape"], c["seconds"]), flush=True)
    for i in range(a.trials):
        c = run_arm(a.workers, a.per, 1_000_000_000)
        out["coarse_1s"].append(c)
        print("  coarse   1s   trial %d: claimed %d, missing %d (%s, %ss)"
              % (i + 1, c["claimed"], c["missing"], c["shape"], c["seconds"]), flush=True)

    ctl_lost = sum(t["missing"] for t in out["control_1ns"])
    coarse_lost = sum(t["missing"] for t in out["coarse_1s"])
    out["control_lost_total"] = ctl_lost
    out["coarse_lost_total"] = coarse_lost
    out["verdict"] = ("REPRODUCED: the guard loses a claimed write when mtime cannot separate two "
                      "same-size writes" if coarse_lost > 0 and ctl_lost == 0 else
                      "NOT REPRODUCED" if coarse_lost == 0 else
                      "VOID: the control lost records too, so the coarse arm proves nothing about mtime")

    summary = ("control (1ns)  lost %d of %d\ncoarse  (1s)   lost %d of %d\n%s"
               % (ctl_lost, sum(t["claimed"] for t in out["control_1ns"]),
                  coarse_lost, sum(t["claimed"] for t in out["coarse_1s"]), out["verdict"]))
    print()
    print(summary)
    write_receipt(__file__, out)

    # A LOSS GOES TO STDERR AS WELL, because that is the only channel that survives.
    # Measured 2026-09-12: this probe exited 1 on CI, which means it saw a loss neither this machine
    # nor WSL/ext4 could produce in 1,284 records. The finding was unreadable. The suite runs every
    # uncited probe and, on a non-zero exit, quotes the STDERR tail; stdout is dropped, and the
    # receipt that holds the detail is suppressed under the suite by design. So the one run that
    # reproduced the thing this probe exists to catch left nothing behind.
    if ctl_lost or coarse_lost:
        sys.stderr.write(summary + "\n" + json.dumps(
            {"control_1ns": out["control_1ns"], "coarse_1s": out["coarse_1s"]}, indent=1) + "\n")

    # THE EXIT CODE ANSWERS "could this experiment be trusted", not "what did it find". A verdict of
    # REPRODUCED is this probe succeeding, and exiting non-zero on it made the suite report a
    # finding as a broken probe. Only a control that lost records voids the run.
    return 2 if ctl_lost else 0


if __name__ == "__main__":
    sys.exit(main())
