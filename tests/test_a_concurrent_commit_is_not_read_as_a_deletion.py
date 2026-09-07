"""A row another writer commits while we are saving must not be deleted as though we had erased it.

WHY THIS EXISTS. The row writer derives its deletions: `removed = baseline - memory`. That is only
sound while the baseline describes the same read that filled memory. It did not. The recovery path
merged with disk, which reads the store, and then read the store a SECOND time to refresh the
baseline. A row committed between those two reads is in the baseline, is absent from memory, and is
therefore deleted, in the same transaction that reports a successful save to both writers.

MEASURED 2026-09-07 with eight concurrent writers, changing nothing but the inter-process lock: with
the lock held, 0 of 96 records lost in 4 of 4 trials; with it degraded, 17, 6, 28 and 47 lost, every
worker reporting all twelve of its writes successful and no exception raised anywhere. The lock
degrades by design after about 27 s of contention, and SQLite's own busy timeout is longer than that
deadline, so a single slow commit unlocks every waiter. The loss this project had recorded as
unexplained, 9 of 96, sits inside that range.

The fix is that the baseline now comes from the read that filled memory, so a row committed after it
is simply not in the baseline, and a row that is not in the baseline can never be computed as a
deletion.

THE CONTROL is the second test. It restores the second read on one handle and requires the committed
row to disappear. Without it, the first test would pass just as happily against a store that deletes
nothing at all, and the fix would be pinned by a test that cannot fail.
"""
import json
import os
import sqlite3
import tempfile

from inspeximus import Inspeximus, sqlite_store as ss


def _commit_a_foreign_row(path, rid, text):
    """What another process's committed write looks like from here: one row this handle never read."""
    rec = {"id": rid, "text": text, "ts": 1.0, "iso": "2026-09-07T00:00:00Z", "status": "active",
           "value": 1.0, "mtype": "fact", "tags": [], "links": [], "meta": {}}
    con = sqlite3.connect(str(path))
    try:
        con.execute("INSERT INTO records(id, ord, doc) VALUES(?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc",
                    (rid, 999, ss._doc(rec, True)))
        con.commit()
    finally:
        con.close()
    return rid


def _ids(path):
    return {r["id"] for r in ss.load(path)}


def _two_handles_and_a_stale_one():
    """A store, a second writer that has moved it on, and a handle whose next save must merge."""
    p = os.path.join(tempfile.mkdtemp(), "m.json")
    stale = Inspeximus(path=p)
    stale._save_min_s = 0
    stale.remember("the first record", key="a")
    stale.flush()
    assert ss.looks_like_sqlite(p), "the fixture is not a row store, so it tests nothing"

    other = Inspeximus(path=p)
    other._save_min_s = 0
    other.remember("a second writer's record", key="b")
    other.flush()                       # the store has moved under `stale`, so its next save merges
    return stale, p


def _inject_during_the_merge(handle, path, rid, also_refresh_baseline):
    """Commit a foreign row at the exact moment the old code took its second read."""
    real = handle._merge_with_disk

    def patched():
        out = real()
        _commit_a_foreign_row(path, rid, "committed by another process mid-merge")
        if also_refresh_baseline:
            # THE LINE THIS TEST EXISTS FOR, restored verbatim: a baseline taken later than the
            # records is what turns another writer's row into a deletion.
            handle._row_snapshot = ss.snapshot(ss.load(path), handle._persist_vectors)
        return out

    handle._merge_with_disk = patched


def test_a_row_committed_mid_save_survives():
    stale, path = _two_handles_and_a_stale_one()
    _inject_during_the_merge(stale, path, "foreign001", also_refresh_baseline=False)

    stale.remember("this handle's own record", key="c")
    stale.flush()

    assert "foreign001" in _ids(path), (
        "a row another writer committed during the merge was deleted by this save. Deletions are "
        "`baseline - memory`, so a baseline read later than the records names that row as erased.")


def test_the_second_read_really_does_delete_it():
    """CONTROL. Restore the removed line on one handle; the same row must be lost."""
    stale, path = _two_handles_and_a_stale_one()
    _inject_during_the_merge(stale, path, "foreign002", also_refresh_baseline=True)

    stale.remember("this handle's own record", key="c")
    stale.flush()

    assert "foreign002" not in _ids(path), (
        "the defect did not reproduce with the second read restored, so the test above is not "
        "measuring the fix and would pass against any store")


def test_nothing_else_was_lost_in_either_arm():
    """Both writers' earlier records must be present either way; only the injected row differs."""
    stale, path = _two_handles_and_a_stale_one()
    before = _ids(path)
    assert len(before) == 2, "the fixture does not hold both writers' records: %r" % (before,)

    _inject_during_the_merge(stale, path, "foreign003", also_refresh_baseline=False)
    stale.remember("this handle's own record", key="c")
    stale.flush()

    after = _ids(path)
    assert before <= after, "the merge lost a record that was on disk before it ran: %r" % (
        sorted(before - after),)
    assert len(after) == 4, "expected both writers' records, the injected row and the new one: %r" % (
        json.dumps(sorted(after)),)


def test_the_lock_outlasts_a_busy_database():
    """The two waits live in different files, and getting them the wrong way round unlocks writers.

    A writer holds the inter-process lock across SQLite's own busy wait, so if a waiter gives up on
    the lock sooner than a holder can legally block, ordinary contention degrades the lock instead of
    a wedged process doing it. Shipped as 20 s against 30 s, which is backwards.

    The wiring is asserted, not just the constants: a connection reports the busy timeout it actually
    got, and `_StoreLock.__enter__` must READ the module constant rather than carry its own literal.
    """
    import sqlite3 as _sq

    from inspeximus.core import LOCK_WAIT_S, _StoreLock

    assert ss.BUSY_TIMEOUT_S < LOCK_WAIT_S, (
        "a writer can block for %ss inside the lock while a waiter gives up after %ss, so contention "
        "makes the waiter write unprotected" % (ss.BUSY_TIMEOUT_S, LOCK_WAIT_S))

    p = os.path.join(tempfile.mkdtemp(), "m.json")
    m = Inspeximus(path=p)
    m.remember("a record", key="a")
    m.flush()
    con = _sq.connect(str(p), timeout=ss.BUSY_TIMEOUT_S)
    try:
        got = con.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        con.close()
    assert got == ss.BUSY_TIMEOUT_S * 1000, (
        "the constant is not what a connection ends up with: %r ms" % got)

    names = _StoreLock.__enter__.__code__.co_names
    assert "LOCK_WAIT_S" in names, (
        "the lock wait is a literal inside __enter__ again, so the ordering above is checking a "
        "number nothing uses")
    assert 20.0 not in _StoreLock.__enter__.__code__.co_consts, "the old 20 s literal is back"


def test_an_unprotected_write_leaves_a_trace():
    """Degrading to unlocked must be recorded. A loss nothing recorded is a loss nobody can explain."""
    from inspeximus.core import _StoreLock

    assert isinstance(_StoreLock.DEGRADED, dict), (
        "there is no record of a write that gave up on the lock, which is why eight writers could "
        "lose 47 of 96 records while every one of them reported success")
