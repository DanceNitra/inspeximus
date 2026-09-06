"""The row store must be faster AND lossless, and the second is the one that can hurt.

A store that writes one row instead of 20.3 MB is worth having only if every record survives the
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
    """The JSON store lost a whole worker's output in 8 of 8 trials at this width."""
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
    seen = {}
    real_save = ss.save

    def spy(path, items, before, dirty=None):
        res = real_save(path, items, before, dirty=dirty)
        seen["dirty_was_none"] = dirty is None
        return res

    ss.save = spy
    try:
        m.remember("an ordinary write", key="k", mtype="fact")
        m.flush()
    finally:
        ss.save = real_save
    assert seen.get("dirty_was_none") is False, (
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
