"""The row store must be faster AND lossless, and the second is the one that can hurt.

A store that writes one row instead of the whole file is worth having only if every record survives the
trip. This project's coding store has been corrupted three times in ten days, so the migration
checks its own count and identity rather than trusting that it worked.

The speed number is deliberately NOT asserted here. It is measured in the probe, where a slow
machine produces a slow number rather than a red build.
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

from inspeximus import sqlite_store as ss

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rec(i, text=None):
    return {"id": "r%03d" % i, "text": text or ("record %d" % i), "ts": 1000.0 + i,
            "status": "active", "meta": {"sid": "s1"}, "tags": ["t"]}


#: Held in a separate PROCESS: a sqlite connection cannot be used from another thread, so a releaser
#: thread dies silently and the lock is never let go.
HOLDER_SRC = """
import sqlite3, sys, time
c = sqlite3.connect(sys.argv[1], timeout=30, isolation_level=None)
c.execute('BEGIN IMMEDIATE')
print('locked', flush=True)
time.sleep(0.6)
c.execute('COMMIT')
"""


def _db():
    return os.path.join(tempfile.mkdtemp(), "s.db")


def test_a_round_trip_keeps_every_record_and_its_order():
    db = _db()
    items = [_rec(i) for i in range(50)]
    ss.save(db, items, {})
    back = ss.load(db)
    assert [r["id"] for r in back] == [r["id"] for r in items]
    assert back == items, "a record changed shape on the way to disk and back"


def test_an_append_writes_one_row_not_the_store():
    """The whole point. If this reads 50, the diff is doing a rewrite in disguise."""
    db = _db()
    items = [_rec(i) for i in range(50)]
    snap = ss.save(db, items, {})["snapshot"]
    items.append(_rec(999))
    res = ss.save(db, items, snap, dirty=["r999"])
    assert (res["added"], res["changed"], res["removed"]) == (1, 0, 0)
    assert len(ss.load(db)) == 51


def test_an_edit_updates_in_place():
    db = _db()
    items = [_rec(i) for i in range(10)]
    snap = ss.save(db, items, {})["snapshot"]
    items[3]["text"] = "corrected"
    res = ss.save(db, items, snap, dirty=["r003"])
    assert (res["added"], res["changed"]) == (0, 1)
    assert [r for r in ss.load(db) if r["id"] == "r003"][0]["text"] == "corrected"
    assert len(ss.load(db)) == 10, "an edit changed the record count"


def test_a_removal_reaches_disk():
    db = _db()
    items = [_rec(i) for i in range(10)]
    snap = ss.save(db, items, {})["snapshot"]
    gone = items.pop(4)
    res = ss.save(db, items, snap, dirty=[])
    assert res["removed"] == 1
    assert gone["id"] not in {r["id"] for r in ss.load(db)}


def test_the_full_diff_finds_what_a_wrong_dirty_list_would_miss():
    """CONTROL on the optimisation itself. `dirty` is a promise from the caller, and a caller that
    forgets an id must not silently lose the edit for ever: the slow path is the correct one, and
    it has to still be correct."""
    db = _db()
    items = [_rec(i) for i in range(10)]
    snap = ss.save(db, items, {})["snapshot"]
    items[7]["text"] = "changed but not declared"
    missed = ss.save(db, items, snap, dirty=["r000"])          # the caller lied
    assert missed["changed"] == 0
    assert [r for r in ss.load(db) if r["id"] == "r007"][0]["text"] == "record 7"
    recovered = ss.save(db, items, snap)                        # no dirty list -> full diff
    assert recovered["changed"] == 1
    assert [r for r in ss.load(db) if r["id"] == "r007"][0]["text"] == "changed but not declared"


def test_migration_refuses_rather_than_losing_records():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "store.json")
    items = [_rec(i) for i in range(200)]
    with open(src, "w", encoding="utf-8") as fh:
        json.dump(items, fh)
    res = ss.migrate_from_json(src, os.path.join(d, "s.db"))
    assert res["records"] == 200
    back = ss.load(os.path.join(d, "s.db"))
    assert [r["id"] for r in back] == [r["id"] for r in items]


def test_a_header_read_beats_the_file_extension():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "not_obviously_a_db.json")            # a sqlite store under a json name
    ss.save(db, [_rec(1)], {})
    assert ss.looks_like_sqlite(db)
    plain = os.path.join(d, "plain.db")                        # json under a db name
    with open(plain, "w", encoding="utf-8") as fh:
        fh.write("[]")
    assert not ss.looks_like_sqlite(plain)


WORKER = '''
import sys, json
sys.path.insert(0, %r)
from inspeximus import sqlite_store as ss
db, wid, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
items, snap = ss.load(db), {}
snap = ss.snapshot(items)
for i in range(n):
    rid = "w%%d-%%d" %% (wid, i)
    items.append({"id": rid, "text": "from %%d" %% wid, "ts": 1.0, "status": "active"})
    snap = ss.save(db, items, snap, dirty=[rid])["snapshot"]
'''


@pytest.mark.parametrize("workers", [2, 8])
def test_concurrent_processes_lose_nothing(workers):
    """This exercises `sqlite_store.save` DIRECTLY, which is not how an application writes.

    Read `probes/twelve_writers_and_the_one_that_stopped_writing.py` for the number that means
    something: measured through the library, the row store lost MORE than JSON until the save path
    learned to merge, because both writers were refused by the same guard. This test pins that the
    row layer itself does not lose a write; it does not pin the product's concurrency.
    """
    db = _db()
    ss.save(db, [], {})
    src = os.path.join(tempfile.mkdtemp(prefix="sqlw_"), "w.py")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(WORKER % REPO)
    per = 10
    procs = [subprocess.Popen([sys.executable, src, db, str(w), str(per)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for w in range(workers)]
    for p in procs:
        p.communicate()
    got = len(ss.load(db))
    assert got == workers * per, "%d of %d records lost with %d concurrent writers" % (
        workers * per - got, workers * per, workers)


def test_an_undeclared_record_forces_the_complete_diff():
    """A record nobody marked must not be able to hide, and this check must cost no serialising.

    `_touch` is called from 16 places in core.py and that set goes stale as the file grows, so the
    design makes an omission COST rather than CORRUPT. The cheap half of the net is a set
    difference: any live id that is neither in the on-disk baseline nor in the touched set means
    something was added without declaring it, and the next save takes the complete path.

    The first version of this check compared record COUNTS, which fires on every append because an
    append changes the count by definition. The full diff then ran on every write and the row store
    measured 1.0x against JSON. Set membership answers the question that was meant.
    """
    from inspeximus import Inspeximus
    d = tempfile.mkdtemp()
    db = os.path.join(d, "s.db")
    ss.save(db, [], {})
    m = Inspeximus(path=db)
    m._save_min_s = 0
    m.remember("first", key="k1", mtype="fact")
    m.flush()

    # A record that appeared without anything marking it.
    m._items.append({"id": "ghost", "text": "nobody declared me", "ts": 1.0, "status": "active"})
    m._touched.clear()
    m._dirty = True
    m._save(force=False)

    ids = {r["id"] for r in Inspeximus(path=db)._items}
    assert "ghost" in ids, "an undeclared record never reached disk, so the set check does not fire"


def test_an_ordinary_append_does_not_trigger_the_complete_diff():
    """CONTROL. If a normal write reconciled too, the optimisation would be decorative.

    Measured through the counts the row store returns: a declared append writes exactly one row.
    """
    from inspeximus import Inspeximus
    d = tempfile.mkdtemp()
    db = os.path.join(d, "s.db")
    ss.save(db, [_rec(i) for i in range(200)], {})
    m = Inspeximus(path=db)
    m._save_min_s = 0
    calls = []
    real_save = ss.save

    def spy(path, items, before, dirty=None, **kw):
        calls.append(dirty is None)
        return real_save(path, items, before, dirty=dirty, **kw)

    ss.save = spy
    try:
        m.remember("an ordinary write", key="k", mtype="fact")   # the write under test
        m.flush()                                                # reconciles on purpose
    finally:
        ss.save = real_save
    # RECORD EVERY CALL, NOT THE LAST ONE. `flush()` asks for the complete diff deliberately: it is
    # the durability barrier, and a record edited in place that nothing declared is invisible to the
    # fast path. Keeping only the last call measured the flush and then reported that an ordinary
    # append pays for the safety net.
    assert calls, "no save happened at all, so this measures nothing"
    assert calls[0] is False, (
        "a declared append still took the complete diff, so every write pays for the safety net")


def test_close_session_asks_for_the_complete_diff():
    """The other half of the net: an in-place edit nothing marked is invisible to a set check,
    so the one full reconcile a session pays for happens when the session ends."""
    from inspeximus import Inspeximus
    d = tempfile.mkdtemp()
    db = os.path.join(d, "s.db")
    ss.save(db, [], {})
    m = Inspeximus(path=db)
    m._save_min_s = 0
    m.remember("original", key="k", mtype="fact")
    m.flush()
    assert m._full_reconcile is False
    m.open_session("s1")
    m.close_session("s1")
    assert m._full_reconcile is True or m._touched == set(), (
        "close_session neither asked for a reconcile nor had already saved one")


def test_a_format_rewrite_still_deletes_what_is_gone():
    """Rewriting every row must not cost the store its deletions.

    Moving a store to a new row encoding needs every row written again. The first attempt did that by
    emptying the diff baseline, which forces every record to look new -- and `removed` is computed
    from that same baseline, so nothing looked deleted. Measured through the CLI: `forget-subject`
    printed "erased 1 record(s)" and the record was still in the file afterwards. `rewrite_all` says
    what it means and leaves the baseline alone.
    """
    db = _db()
    items = [_rec(i) for i in range(5)]
    snap = ss.save(db, items, {})["snapshot"]
    gone = items.pop(2)

    res = ss.save(db, items, snap, rewrite_all=True)
    assert res["removed"] == 1, "the rewrite did not notice the deletion: %r" % res
    assert res["changed"] == 4, "the rewrite did not rewrite the surviving rows: %r" % res
    left = {r["id"] for r in ss.load(db)}
    assert gone["id"] not in left, "the deleted record survived the rewrite"
    assert len(left) == 4


def test_a_new_store_is_marked_current_however_it_was_written():
    """CONTROL on the marker. An unmarked store is treated as out of date, so a store this version
    creates must be marked whichever writer created it -- otherwise every open triggers a rewrite,
    and the rewrite path is the one that used to lose deletions."""
    a = _db()
    ss.save(a, [_rec(1)], {})                                  # the full-diff writer
    assert not ss.needs_rewrite(a)

    b = _db()
    ss.save(b, [], {})
    snap = ss.snapshot([])
    ss.save(b, [_rec(1)], snap, dirty=["r001"])                # the declared-ids writer
    assert not ss.needs_rewrite(b), (
        "a store created by a declared write is not marked, so every open thinks it is out of date")


def test_a_busy_database_is_waited_out_not_lost():
    """A write that finds the database locked waits for it instead of losing the record.

    THIS TEST REFUTED TWO FIXES AND FOUND NEITHER A CAUSE. A full-suite run lost 9 of 96 records
    with eight concurrent writers, once, and has never reproduced. The first response was a retry
    loop around the write; written, it blocked for six attempts times sqlite's own thirty-second
    busy timeout, which is the "a retry turns a problem into a hang" failure its own docstring
    warned about. The second was to stop re-declaring `journal_mode` on every connect, with a story
    about exclusive locks -- and this test passes with that change reverted, so the story is not
    established and the comment in `_connect` now says so. What this test does pin is the property
    itself: a locked database costs the writer latency, not its record.

    THE LOCK IS HELD BY ANOTHER PROCESS, not another thread: a sqlite connection cannot be used from
    a thread other than the one that made it, and the first version of this test released the lock
    from a thread that had therefore already died. The write then waited the full timeout and the
    test reported a defect that was its own.
    """
    import subprocess
    import time as _t

    db = _db()
    snap = ss.save(db, [_rec(1)], {})["snapshot"]

    holder_src = os.path.join(tempfile.mkdtemp(), "hold.py")
    with open(holder_src, "w", encoding="utf-8") as fh:
        fh.write(HOLDER_SRC)
    holder = subprocess.Popen([sys.executable, holder_src, db], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "locked", "the holder never took the lock"

    t0 = _t.time()
    res = ss.save(db, [_rec(1), _rec(2)], snap, dirty=["r002"])
    waited = _t.time() - t0
    holder.wait(timeout=30)

    assert waited >= 0.2, "the write did not wait at all, so the lock was not real: %.2f s" % waited
    assert waited < 10, (
        "the write waited far longer than the lock was held (%.2f s). A busy database must cost "
        "latency, not a retry loop multiplied by sqlite's own timeout." % waited)
    assert res["added"] == 1
    assert {r["id"] for r in ss.load(db)} == {"r001", "r002"}
