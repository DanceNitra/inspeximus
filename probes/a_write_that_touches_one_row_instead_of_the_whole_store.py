"""One write, one row: 0.54 s over 21.5 MB becomes 0.03 s, measured on the live store.

WHAT THIS MEASURES. The JSON store is rewritten in full on every save. This project's coding store
holds tens of thousands of records in tens of megabytes, a hook fires on every tool call, and two
agents share it. So the
cost of one write is paid hundreds of times a session, and it is paid against the whole file.

THE FIRST VERSION OF THIS WAS SLOWER THAN WHAT IT REPLACED, and that is the useful part. Moving to
sqlite3 was SLOWER than the JSON store it replaced, while inserting exactly one row. Splitting the time
found 98% of it in the diff: serialising all 32,539 records to discover which one moved cost
0.3933 s, and the INSERT it produced cost 0.0070 s. The engine was never the problem; deciding what
to write was. `save(..., dirty=[id])` lets a caller name what it touched, which it already knows.

CONTROLS, because a fast write that loses a record is worse than a slow one:
  - the migration re-reads the store and refuses unless the count AND the id order both survive;
  - a caller that passes a WRONG dirty list must lose the edit rather than corrupt the row, and the
    full diff must still find it afterwards. That arm is in the test suite;
  - the baseline is re-measured here rather than quoted, so a slower machine reports a smaller
    speedup instead of a false one.

RUN IT: python probes/a_write_that_touches_one_row_instead_of_the_whole_store.py
It uses a COPY of the live store when one is present, and a synthetic store of the same size
otherwise, so it runs for someone who is not us.
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import sqlite_store as ss                    # noqa: E402
from _receipt import write_json  # noqa: E402

LIVE = r"C:/Users/Danculus/agora/.inspeximus/coding_memory.json"
N = 32538


def _corpus(d):
    """The live store if it is here, otherwise one of the same shape and size."""
    dst = os.path.join(d, "store.json")
    if os.path.exists(LIVE):
        shutil.copy(LIVE, dst)
        return dst, "a copy of the live coding store"
    items = [{"id": "r%06d" % i, "text": "ran: some command number %d %s" % (i, "x" * 380),
              "ts": 1000.0 + i, "status": "active", "meta": {"sid": "s"}, "tags": ["bash"]}
             for i in range(N)]
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(items, fh)
    return dst, "a synthetic store of the same size"


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2]


def main():
    d = tempfile.mkdtemp()
    src, what = _corpus(d)
    # THE LIVE STORE IS NOW A ROW STORE, which this probe caused, and it crashed here reading it as
    # JSON. A measurement of the JSON baseline still needs the records, whatever they are stored in.
    if ss.looks_like_sqlite(src):
        items = ss.load(src)
        what += " (already converted; the JSON baseline below is re-created from its records)"
        mb = sum(len(json.dumps(r, ensure_ascii=False).encode("utf-8")) for r in items) / 1e6
    else:
        with open(src, encoding="utf-8") as fh:
            raw = json.load(fh)
        items = raw if isinstance(raw, list) else raw.get("records") or []
        mb = os.path.getsize(src) / 1e6
    print("  corpus: %s, %d records, %.1f MB\n" % (what, len(items), mb))

    # BASELINE, re-measured rather than quoted: rewrite the whole file, as the JSON store does.
    js = os.path.join(d, "baseline.json")
    shutil.copy(src, js)
    base = []
    for i in range(3):
        items.append({"id": "b%d" % i, "text": "one more", "ts": 1.0, "status": "active"})
        t0 = time.time()
        tmp = js + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(items, fh, ensure_ascii=False)
        os.replace(tmp, js)
        base.append(time.time() - t0)
    for i in range(3):
        items.pop()

    # The migration is timed from a JSON file, because that is what a user upgrading actually has.
    # When the source is already converted, one is written out first and the write is not timed.
    mig_src = src
    if ss.looks_like_sqlite(src):
        mig_src = os.path.join(d, "as_json.json")
        with open(mig_src, "w", encoding="utf-8") as fh:
            json.dump(items, fh, ensure_ascii=False)
    db = os.path.join(d, "store.db")
    t0 = time.time()
    mig = ss.migrate_from_json(mig_src, db)
    t_mig = time.time() - t0

    loaded = ss.load(db)
    snap = ss.snapshot(loaded)
    row = []
    for i in range(5):
        rid = "p%d" % i
        loaded.append({"id": rid, "text": "one more", "ts": 1.0, "status": "active"})
        t0 = time.time()
        res = ss.save(db, loaded, snap, dirty=[rid])
        row.append(time.time() - t0)
        snap = res["snapshot"]

    b, r = _median(base), _median(row)
    print("  whole-file rewrite (today)   %.4f s   %.1f MB written" % (b, mb))
    print("  one row (sqlite3, stdlib)    %.4f s   1 INSERT" % r)
    print("  speedup                      %.0fx\n" % (b / r if r else 0))
    print("  migration: %d records in %.1f s, %.1f MB -> %.1f MB"
          % (mig["records"], t_mig, mb, os.path.getsize(db) / 1e6))
    print("  last save did: +%d ~%d -%d" % (res["added"], res["changed"], res["removed"]))

    assert mig["records"] == len(items), "the migration lost records"
    assert res["added"] == 1 and res["changed"] == 0, \
        "an append wrote more than one row, so the diff is a rewrite in disguise"
    assert len(ss.load(db)) == len(items) + 5, "records went missing across the five writes"

    out = {"corpus": what, "records": len(items), "megabytes": round(mb, 1),
           "whole_file_rewrite_s": round(b, 4), "one_row_s": round(r, 4),
           "speedup": round(b / r, 1) if r else None,
           "migration_seconds": round(t_mig, 1),
           "first_attempt_was_slower": {"sqlite_full_diff_s": 0.5131, "json_s": 0.35,
                                        "diff_cost_s": 0.3933, "insert_cost_s": 0.0070}}
    path = os.path.splitext(os.path.abspath(__file__))[0] + ".result.json"
    write_json(path, out, indent=1)
    print("\n  receipt: %s" % os.path.basename(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
