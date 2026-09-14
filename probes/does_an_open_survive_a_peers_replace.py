"""Does opening a store survive a peer replacing the file at the same moment?

WHY THIS FILE EXISTS. The hostile re-run of the 2.27.5 race measurement (2026-09-13) left a bill it
did not chase: under twelve contending writers on Windows, about one open in a hundred died at
`Inspeximus(path)` with `PermissionError: [Errno 13]`. The writer side already knows the shape:
`_durable_replace` retries `os.replace` because Windows refuses to replace a file a reader holds
open. The reader side had no such loop. A file that is mid-replace is briefly a name with no
openable target, and Windows reports that as access denied, not as not found.

THE SILENT HALF. `Path.exists()` and `_stat_sig()` both swallow that error. `exists()` answers False
and `_stat_sig()` answers ABSENT, so an open that lands on the same instant does not crash: it loads
an EMPTY store from a file that has records in it, and reports nothing. A write from that handle is
refused by the signature guard, which is the safe direction, but a read-only handle serves an empty
recall and never says why.

WHAT THIS MEASURES. One writer process appends records through the JSON store as fast as the
refusal-and-reopen loop allows, which means one `os.replace` per landed write. K reader processes
open the same path in a loop for the same window and count, per open: raised `PermissionError`,
raised anything else, and returned zero records from a file seeded with one. Every count is taken
from what the reader observed, never from the writer.

HOW TO READ IT. `permission_errors` is the crash the re-run saw. `empty_loads` is the silent case.
Both must read 0 after the fix; a positive count on either is the defect, and the count per thousand
opens is the rate a caller pays. The verdict is computed from the counts.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _receipt import write_receipt  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WRITER = '''
import os, sys, time, random
os.environ["INSPEXIMUS_STORE_FORMAT"] = "json"
sys.path.insert(0, %(repo)r)
from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk
path, seconds = sys.argv[1], float(sys.argv[2])
m = Inspeximus(path=path)
landed = 0
end = time.time() + seconds
i = 0
while time.time() < end:
    i += 1
    try:
        m.remember("w r%%d uniq-%%d" %% (i, i), mtype="fact")
        landed += 1
    except StoreChangedOnDisk:
        m = Inspeximus(path=path)
    except Exception as e:
        print("WRITER-CRASH" + chr(9) + repr(e)[:160])
        break
print("LANDED" + chr(9) + str(landed))
'''

READER = '''
import os, sys, time
os.environ["INSPEXIMUS_STORE_FORMAT"] = "json"
sys.path.insert(0, %(repo)r)
from inspeximus import Inspeximus
path, seconds = sys.argv[1], float(sys.argv[2])
opens = perm = other = empty = 0
kinds = {}
end = time.time() + seconds
while time.time() < end:
    opens += 1
    try:
        m = Inspeximus(path=path)
        if len(m.items) == 0:
            empty += 1
    except PermissionError:
        perm += 1
    except Exception as e:
        other += 1
        k = type(e).__name__
        kinds[k] = kinds.get(k, 0) + 1
print("READ" + chr(9) + "%%d %%d %%d %%d" %% (opens, perm, other, empty) + chr(9) + repr(sorted(kinds.items())))
'''


def run_round(readers: int, seconds: float) -> dict:
    d = tempfile.mkdtemp(prefix="openrace_")
    db = os.path.join(d, "s.json")
    env = dict(os.environ, INSPEXIMUS_STORE_FORMAT="json")
    sys.path.insert(0, REPO)
    os.environ["INSPEXIMUS_STORE_FORMAT"] = "json"
    from inspeximus import Inspeximus
    m = Inspeximus(path=db)
    m.remember("seed record", mtype="fact")
    del m
    assert os.path.getsize(db) > 2, "the seed did not land; an empty load could not be told apart"
    wsrc, rsrc = os.path.join(d, "w.py"), os.path.join(d, "r.py")
    with open(wsrc, "w", encoding="utf-8") as fh:
        fh.write(WRITER % {"repo": REPO})
    with open(rsrc, "w", encoding="utf-8") as fh:
        fh.write(READER % {"repo": REPO})
    w = subprocess.Popen([sys.executable, wsrc, db, str(seconds)], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=env)
    rs = [subprocess.Popen([sys.executable, rsrc, db, str(seconds)], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, env=env) for _ in range(readers)]
    out = {"opens": 0, "permission_errors": 0, "other_errors": 0, "empty_loads": 0, "landed": 0,
           "other_kinds": {}, "reader_crashes": 0}
    wo, we = w.communicate(timeout=seconds + 120)
    for ln in wo.splitlines():
        if ln.startswith("LANDED\t"):
            out["landed"] = int(ln.split("\t")[1])
        elif ln.startswith("WRITER-CRASH\t"):
            out["writer_crash"] = ln.split("\t", 1)[1]
    for p in rs:
        ro, re_ = p.communicate(timeout=seconds + 120)
        got = False
        for ln in ro.splitlines():
            if ln.startswith("READ\t"):
                got = True
                _, nums, kinds = ln.split("\t")
                o, pe, oe, em = (int(x) for x in nums.split())
                out["opens"] += o
                out["permission_errors"] += pe
                out["other_errors"] += oe
                out["empty_loads"] += em
                for k, n in eval(kinds):  # noqa: S307 - our own repr of a sorted list of pairs
                    out["other_kinds"][k] = out["other_kinds"].get(k, 0) + n
        if not got:
            out["reader_crashes"] += 1
            out.setdefault("reader_stderr_tail", []).append(re_.strip().splitlines()[-1:] or [""])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readers", type=int, default=6)
    ap.add_argument("--seconds", type=float, default=2.0 if os.environ.get("PYTEST_CURRENT_TEST") else 20.0)
    ap.add_argument("--rounds", type=int, default=1 if os.environ.get("PYTEST_CURRENT_TEST") else 3)
    ap.add_argument("--receipt", default=None)
    a = ap.parse_args()
    out = {"probe": os.path.basename(__file__), "platform": sys.platform, "readers": a.readers,
           "seconds_per_round": a.seconds, "rounds": a.rounds,
           "totals": {"opens": 0, "permission_errors": 0, "other_errors": 0, "empty_loads": 0,
                      "landed": 0, "reader_crashes": 0, "other_kinds": {}},
           "per_round": []}
    for r in range(a.rounds):
        res = run_round(a.readers, a.seconds)
        out["per_round"].append(res)
        t = out["totals"]
        for k in ("opens", "permission_errors", "other_errors", "empty_loads", "landed", "reader_crashes"):
            t[k] += res[k]
        for k, n in res["other_kinds"].items():
            t["other_kinds"][k] = t["other_kinds"].get(k, 0) + n
        print("  round %d/%d  opens %6d  PermissionError %4d  empty %4d  other %3d  writer landed %5d"
              % (r + 1, a.rounds, res["opens"], res["permission_errors"], res["empty_loads"],
                 res["other_errors"], res["landed"]), flush=True)
    t = out["totals"]
    per_k = (lambda n: round(1000.0 * n / t["opens"], 2) if t["opens"] else None)
    out["per_thousand_opens"] = {"permission_errors": per_k(t["permission_errors"]),
                                 "empty_loads": per_k(t["empty_loads"])}
    bad = t["permission_errors"] + t["empty_loads"] + t["reader_crashes"]
    if t["opens"] == 0 or t["landed"] == 0:
        out["verdict"] = "no measurement: the writer landed nothing or no reader opened; nothing contended"
    elif bad == 0:
        out["verdict"] = ("every open survived: %d opens against %d replaces, 0 PermissionError, 0 empty loads"
                          % (t["opens"], t["landed"]))
    else:
        out["verdict"] = ("an open can fail or read empty while a peer replaces the file: %d PermissionError "
                          "and %d empty loads in %d opens against %d replaces"
                          % (t["permission_errors"], t["empty_loads"], t["opens"], t["landed"]))
    print("  " + out["verdict"])
    write_receipt(__file__, out, name=a.receipt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
