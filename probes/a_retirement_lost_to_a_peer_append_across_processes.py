"""Does a retire() survive a write from another handle, in-process and across processes?

Crew OS section 6: 35 retire() calls, a fresh handle per call, beside a parallel run; 3 keys were
still active afterwards though each retire() returned retired=1. Not reproduced by them in
isolation. Measured here in three arms on a temp store shaped like theirs (.json path):
  A) in-process: handle A retires K, stale handle B writes an unrelated key, reload: is K retired?
  B) the reverse: stale B holds K active, A retires K, B then writes K again (a keyed write).
  C) across processes: 35 retires in 35 subprocesses while a writer process appends 200 keyed
     writes concurrently; count keys still active afterwards.

Result files beside this probe: `.3.5.0.result.json` (before the merge fix: A 0 of 35, B kept,
C 6 of 35 still active with every retire() returning 1) and `.result.json` (3.5.1: C 0 of 35).
"""
import os, sys, json, time, tempfile, subprocess, multiprocessing as mp
PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # this tree
sys.path.insert(0, PKG)
from inspeximus import Inspeximus, __version__


def arm_a(path):
    A = Inspeximus(path); B = Inspeximus(path)          # B opened BEFORE the retire: a stale reader
    for i in range(35):
        A.remember("layer %d" % i, key="crew::L%d" % i, object="v1")
    B2 = Inspeximus(path)                                # opened after the layers exist, before the retires
    for i in range(35):
        A.retire("crew::L%d" % i, reason="bulk")
    B2.remember("unrelated", key="crew::other", object="x")   # a stale handle writes something else
    B.remember("older still", key="crew::other2", object="y")
    fresh = Inspeximus(path)
    active = [r["key"] for r in fresh.items if r.get("key", "").startswith("crew::L") and r["status"] == "active"]
    return {"still_active_after_stale_writes": len(active), "of": 35}


def arm_b(path):
    A = Inspeximus(path)
    A.remember("v1", key="crew::K", object="v1", derived_from=[])
    B = Inspeximus(path)                                 # holds K active
    A.retire("crew::K", reason="ended")
    B.remember("v2", key="crew::K", object="v2")          # stale handle rewrites the ended key
    fresh = Inspeximus(path)
    recs = [(r["object"], r["status"]) for r in fresh.items if r.get("key") == "crew::K"]
    return {"records": recs, "b_last_write": B.last_write}


def _retire_one(args):
    path, i = args
    sys.path.insert(0, PKG)
    from inspeximus import Inspeximus
    m = Inspeximus(path)
    return m.retire("crew::P%d" % i, reason="bulk")["retired"]


def _writer(args):
    path, n = args
    sys.path.insert(0, PKG)
    from inspeximus import Inspeximus
    ok = 0
    for i in range(n):
        m = Inspeximus(path)
        m.remember("w%d" % i, key="crew::W%d" % i, object="v")
        ok += 0 if m.last_write.get("blocked") else 1
    return ok


def arm_c(path):
    seed = Inspeximus(path)
    for i in range(35):
        seed.remember("p %d" % i, key="crew::P%d" % i, object="v1")
    del seed
    with mp.Pool(8) as pool:
        w = pool.apply_async(_writer, ((path, 200),))
        retired = pool.map(_retire_one, [(path, i) for i in range(35)])
        wrote = w.get()
    fresh = Inspeximus(path)
    active = [r["key"] for r in fresh.items if r.get("key", "").startswith("crew::P") and r["status"] == "active"]
    wkeys = {r["key"] for r in fresh.items if r.get("key", "").startswith("crew::W") and r["status"] == "active"}
    return {"retire_returned_1": sum(retired), "still_active": len(active), "still_active_keys": active[:5],
            "writer_landed": wrote, "writer_keys_on_disk": len(wkeys)}


if __name__ == "__main__":
    mp.freeze_support()
    print("inspeximus", __version__)
    out = {"probe": os.path.basename(__file__), "inspeximus": __version__, "arms": {}}
    for name, fn in (("A in-process stale handles", arm_a), ("B stale handle rewrites an ended key", arm_b),
                     ("C 35 retires x 8 processes + 200 concurrent writes", arm_c)):
        d = tempfile.mkdtemp(); p = os.path.join(d, "mcp_memory_chain.json")
        t = time.time()
        res = fn(p)
        out["arms"][name] = res
        print(name, "->", json.dumps(res, default=str), "%.1fs" % (time.time() - t))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _receipt import write_receipt
    write_receipt(__file__, json.loads(json.dumps(out, default=str)))
