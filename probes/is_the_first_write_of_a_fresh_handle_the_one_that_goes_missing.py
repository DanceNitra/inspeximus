"""Is the first write of a fresh handle the one that goes missing?

WHY THIS FILE EXISTS. CI lost `w7:r0` at 0a26545 and `w1:r0` at 721c896 on the default row store:
both times a writer's FIRST record, told stored, not on disk. 2.27.5 removed a post-read re-stamp in
`_merge_with_disk`, measured through `reload()` (15 of 7,680 lost with it, 0 without). But the CI
harness never calls `reload()`; it reopens. And the row store's save writes only the ids it touched
and derives deletions from a baseline read on the same open, so a stale signature there cannot
delete a peer's row. Re-running the CI path (row store, reopen on refusal) with the re-stamp
restored lost 0 of 7,548 in 80 Linux rounds. So the CI loss has no demonstrated mechanism yet, and
`r0` twice is the lead: it points at what a handle does before or during its first save.

WHAT THIS MEASURES. N writers, one record each, on the default row store, in two conditions:
`absent` (the store file does not exist when the writers start, so every handle opens on nothing
and the first save creates the file) and `present` (the file exists with one record). Refusals are
retried the way the CI harness retries, by reopening. A record is counted only when `remember`
returned, so anything missing at the end was told stored.

HOW TO READ IT. Loss in `absent` and not in `present` is a creation race. Loss in both is a
first-save defect that has nothing to do with creation. Loss in neither means the lead is dead and
the CI loss needs a different hypothesis.
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

WORKER = '''
import sys, time, random
sys.path.insert(0, %(repo)r)
from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk
path, wid = sys.argv[1], int(sys.argv[2])
text = "w%%d r0 uniq-%%d-0" %% (wid, wid)
told = False
m = Inspeximus(path=path)
for attempt in range(12):
    try:
        m.remember(text, mtype="fact")
        told = True
        break
    except StoreChangedOnDisk:
        time.sleep(random.uniform(0.005, 0.03) * (attempt + 1))
        m = Inspeximus(path=path)
    except Exception as e:
        print("CRASH" + chr(9) + repr(e)[:120])
        break
if told:
    print("WROTE" + chr(9) + text)
'''


def run_round(condition: str, writers: int) -> dict:
    d = tempfile.mkdtemp(prefix="first_")
    db = os.path.join(d, "s.json")
    if condition == "present":
        sys.path.insert(0, REPO)
        from inspeximus import Inspeximus
        m = Inspeximus(path=db)
        m.remember("seed record", mtype="fact")
        del m
    src = os.path.join(d, "w.py")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(WORKER % {"repo": REPO})
    t0 = time.time()
    procs = [subprocess.Popen([sys.executable, src, db, str(w)], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True) for w in range(writers)]
    told, crashes = [], 0
    for p in procs:
        out, _ = p.communicate(timeout=120)
        for ln in out.splitlines():
            if ln.startswith("WROTE\t"):
                told.append(ln.split("\t", 1)[1])
            elif ln.startswith("CRASH\t"):
                crashes += 1
    sys.path.insert(0, REPO)
    from inspeximus import Inspeximus
    kept = {r.get("text") for r in Inspeximus(path=db).items}
    missing = sorted(t for t in told if t not in kept)
    return {"claimed": len(told), "missing": len(missing), "which": missing,
            "crashes": crashes, "seconds": round(time.time() - t0, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--writers", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=1 if os.environ.get("PYTEST_CURRENT_TEST") else 200)
    ap.add_argument("--receipt", default=None)
    a = ap.parse_args()
    out = {"probe": os.path.basename(__file__), "writers": a.writers, "rounds": a.rounds,
           "conditions": {c: {"claimed": 0, "missing": 0, "crashes": 0, "which": []}
                          for c in ("absent", "present")}}
    for r in range(a.rounds):
        for c in ("absent", "present"):
            res = run_round(c, a.writers)
            slot = out["conditions"][c]
            slot["claimed"] += res["claimed"]
            slot["missing"] += res["missing"]
            slot["crashes"] += res["crashes"]
            slot["which"] += ["round %d: %s" % (r + 1, w) for w in res["which"]]
            if res["missing"] or (r + 1) % 20 == 0:
                print("  round %3d/%d  %-7s claimed %2d, MISSING %d, crashes %d  (%.1fs)"
                      % (r + 1, a.rounds, c, res["claimed"], res["missing"], res["crashes"],
                         res["seconds"]), flush=True)
    for c, s in out["conditions"].items():
        print("  %-7s lost %d of %d told-stored first writes (%d crashes)"
              % (c, s["missing"], s["claimed"], s["crashes"]))
    ab, pr = (out["conditions"][c]["missing"] for c in ("absent", "present"))
    out["verdict"] = ("a creation race: the first write is lost only when the file did not exist"
                      if ab and not pr else
                      "a first-save defect independent of creation" if ab and pr else
                      "loss only on a present store: not creation, look at the first merge" if pr else
                      "no first-write loss in this run; the r0 lead did not reproduce here")
    print("  " + out["verdict"])
    write_receipt(__file__, out, name=a.receipt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
