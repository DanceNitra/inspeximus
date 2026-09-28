"""AUDIT-B B-25, B25-R1 as a class: every two-phase step of the archive, stopped after each phase.

AUDIT-A found that an erasure stopped after its `amend-intent` and before its tombstones was finished by the
next erasure's recover(): archived rows left their segment with no tombstone. The rule these tests hold for
every path is the one forget() states for the hot store: a stop may leave proof of a deletion that did not
happen, never a deletion without proof, and never a row that is in neither the hot store nor a segment.

Paths and the precondition each destructive step reads from disk:
  erasure, segment rewrite  every erased id has a tombstone on disk (recover and commit), and the segment is
                            still what the temp was built from (commit)
  apply, hot delete         the rows' segment and its `move` entry are on disk; a stranded row is finished
                            only when its segment is present and matches the log
  temp sweep                a temp is removed only when no pending intent names it
  log head                  the head never claims more entries than the log holds
"""
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from test_audit_b_erasure_reaches_archive_segments import (  # noqa: E402,F401
    DAY, T0, _archived, _no_env, _store)
import inspeximus.core as core  # noqa: E402
from inspeximus import archive  # noqa: E402
from inspeximus import sqlite_store  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


class Stop(Exception):
    pass


def _raise(*a, **k):
    raise Stop()


def _segment_rows(p) -> set:
    """Every id held by any segment file beside the store, listed or not."""
    d = os.path.dirname(p)
    out = set()
    for n in os.listdir(d):
        if archive._segment_rx(p).match(n):
            out |= {r.get("id") for r in sqlite_store.load(os.path.join(d, n)) if isinstance(r, dict)}
    return out


def _tombstoned(p) -> set:
    return {t["memory_id"] for t in Inspeximus(p)._tombstones}


def _stop_hot_save(mp, p):
    """Stop the HOT store's save only. A segment is written through its own Inspeximus handle, so patching
    `_save` for every handle stops the segment writer instead, one phase early."""
    real = Inspeximus._save

    def save(self, *a, **k):
        if os.path.abspath(str(self.path)) == os.path.abspath(p):
            raise Stop()
        return real(self, *a, **k)
    mp.setattr(Inspeximus, "_save", save)


def _stop_erasure(mp, where, p):
    if where == "before_tombstones":
        mp.setattr(Inspeximus, "_flush_tombstones", _raise)
    elif where == "tombstones_silently_lost":
        mp.setattr(Inspeximus, "_flush_tombstones", lambda self: None)
    elif where == "before_intent":
        mp.setattr(archive, "prepare_erasure", _raise)
    elif where == "before_hot_save":
        _stop_hot_save(mp, p)
    elif where == "before_commit":
        mp.setattr(archive, "commit_erasure", _raise)
    elif where == "between_replace_and_amend":
        real = archive._append

        def append(target, entry):
            if entry.get("kind") == "amend":
                raise Stop()
            return real(target, entry)
        mp.setattr(archive, "_append", append)


ERASURE_STOPS = ["before_tombstones", "tombstones_silently_lost", "before_intent", "before_hot_save",
                 "before_commit", "between_replace_and_amend"]


@pytest.mark.parametrize("where", ERASURE_STOPS)
def test_an_erasure_stopped_at_any_phase_never_removes_a_row_without_its_tombstone(tmp_path, monkeypatch,
                                                                                    where):
    p, ids, hot = _store(tmp_path, monkeypatch)
    target = {ids[1], ids[5]}
    assert target <= _archived(p) and target <= _segment_rows(p), "control: the targets are archived"
    with monkeypatch.context() as mp:
        _stop_erasure(mp, where, p)
        try:
            Inspeximus(p).forget(ids=sorted(target), request_id="stopped")
        except Stop:
            pass
    Inspeximus(p).forget(ids=[ids[2]], request_id="later")          # runs recover() first
    gone = target - _segment_rows(p) - {r["id"] for r in Inspeximus(p)._items}
    assert not gone - _tombstoned(p), f"{where}: rows left the archive with no tombstone"
    held = target & _segment_rows(p)
    if held & _tombstoned(p):
        cert = Inspeximus(p).erasure_certificate()
        assert not cert["self_check"]["verified"], f"{where}: certified while erased rows remain"
    if where in ("before_hot_save", "before_commit", "between_replace_and_amend"):
        assert not held, f"{where}: the tombstones were on disk, so recover() must finish the erasure"
    assert not archive._pending_intents(archive.read_log(p)), f"{where}: an intent was left pending"


def test_an_intent_whose_tombstones_are_not_on_disk_is_withdrawn(tmp_path, monkeypatch):
    """The recover() check on its own, independent of forget's order: an intent logged with no tombstones
    behind it is aborted, its temp removed and the segment left byte-identical."""
    p, ids, hot = _store(tmp_path, monkeypatch)
    by_seg = archive.locate(Inspeximus(p), [ids[1]])
    seg = next(iter(by_seg))
    before = open(os.path.join(tmp_path, seg), "rb").read()
    prepared = archive.prepare_erasure(Inspeximus(p), by_seg)
    assert os.path.exists(os.path.join(tmp_path, prepared[0]["temp"])), "control: the temp was written"
    done = archive.recover(Inspeximus(p))
    assert (seg, "aborted") in done
    assert open(os.path.join(tmp_path, seg), "rb").read() == before
    assert not os.path.exists(os.path.join(tmp_path, prepared[0]["temp"]))
    kinds = [e["kind"] for e in archive.read_log(p)]
    assert kinds[-1] == "amend-abort" and not archive._pending_intents(archive.read_log(p))
    assert archive.verify_log(archive.read_log(p))[0]


def test_a_commit_without_tombstones_on_disk_leaves_the_segment_unchanged(tmp_path, monkeypatch):
    """A tombstone write that fails is recorded, not raised, so the erasure reaches commit_erasure without
    its proof. The commit reads the chain on disk and refuses."""
    p, ids, hot = _store(tmp_path, monkeypatch)
    by_seg = archive.locate(Inspeximus(p), [ids[1]])
    seg = next(iter(by_seg))
    before = open(os.path.join(tmp_path, seg), "rb").read()
    m = Inspeximus(p)
    states = archive.commit_erasure(m, archive.prepare_erasure(m, by_seg))
    assert states[0]["erased"] == 0 and "tombstones" in states[0]["state"]
    assert open(os.path.join(tmp_path, seg), "rb").read() == before
    assert ids[1] in _segment_rows(p)


def test_a_missing_temp_is_rebuilt_when_the_tombstones_are_on_disk(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    with monkeypatch.context() as mp:
        mp.setattr(archive, "commit_erasure", _raise)
        with pytest.raises(Stop):
            Inspeximus(p).forget(ids=[ids[1]], request_id="r")
    (it,) = archive._pending_intents(archive.read_log(p))
    os.unlink(os.path.join(tmp_path, it["temp"]))
    assert ids[1] in _segment_rows(p), "control: the row is still in its segment"
    Inspeximus(p).forget(ids=[ids[2]], request_id="later")
    assert ids[1] not in _segment_rows(p)
    assert not archive._pending_intents(archive.read_log(p))
    assert archive.check_segments(p) == []


def test_two_erasures_prepared_from_one_segment_do_not_restore_each_others_rows(tmp_path, monkeypatch):
    """Both prepare from the same segment content before either commits. The second commit used to put
    back the rows the first had removed; it now rebuilds from the segment as it is."""
    p, ids, hot = _store(tmp_path, monkeypatch)
    x, y = ids[1], ids[3]
    assert len(archive.locate(Inspeximus(p), [x, y])) == 1, "control: one segment holds both"
    captured = {}

    def cap(key):
        def commit(store, prepared):
            captured[key] = (store, prepared)
            return []
        return commit
    with monkeypatch.context() as mp:
        mp.setattr(archive, "commit_erasure", cap("a"))
        Inspeximus(p).forget(ids=[x], request_id="a")
        mp.setattr(archive, "recover", lambda *a, **k: [])      # b located before a's intent existed
        mp.setattr(archive, "commit_erasure", cap("b"))
        Inspeximus(p).forget(ids=[y], request_id="b")
    archive.commit_erasure(*captured["a"])
    archive.commit_erasure(*captured["b"])
    rows = _segment_rows(p)
    assert x not in rows and y not in rows, "an erased row came back through a stale temp"
    assert archive.check_segments(p) == [] and not archive._pending_intents(archive.read_log(p))


# ── apply ─────────────────────────────────────────────────────────────────────────────────────────────

def _unarchived(d, monkeypatch, n=16):
    p = str(d / "coding_memory.json")
    m = Inspeximus(p)
    ids = []
    for i in range(n):
        monkeypatch.setattr(core.time, "time", lambda i=i: T0 - 40 * DAY + i)
        ids.append(m.remember(f"ran: build step {i}", key=f"cmd:b{i:03d}", tags=["bash"], mtype="episodic"))
    m.flush()
    monkeypatch.setattr(core.time, "time", lambda: T0)
    return p, ids


def _accounted(p) -> tuple:
    hot = {r["id"] for r in Inspeximus(p)._items}
    listed = _archived(p) if os.path.exists(archive.log_path(p)) else set()
    return hot, listed


@pytest.mark.parametrize("where", ["before_log", "before_hot_save"])
def test_an_apply_stopped_at_any_phase_loses_no_row_and_the_next_run_finishes_it(tmp_path, monkeypatch,
                                                                                   where):
    p, ids = _unarchived(tmp_path, monkeypatch)
    with monkeypatch.context() as mp:
        if where == "before_log":
            mp.setattr(archive, "_write_log", _raise)
        else:
            _stop_hot_save(mp, p)
        with pytest.raises(Stop):
            archive.apply(Inspeximus(p), 7, now=T0)
    hot, listed = _accounted(p)
    assert set(ids) <= hot | listed, f"{where}: a row is in neither the hot store nor a listed segment"
    assert archive.apply(Inspeximus(p), 7, now=T0)["applied"]
    hot, listed = _accounted(p)
    assert set(ids) <= listed and not (set(ids) & hot), f"{where}: the next run did not finish the move"
    assert archive.check_segments(p) == []


def test_a_stranded_row_is_not_dropped_when_its_segment_no_longer_matches(tmp_path, monkeypatch):
    p, ids = _unarchived(tmp_path, monkeypatch)
    with monkeypatch.context() as mp:
        _stop_hot_save(mp, p)
        with pytest.raises(Stop):
            archive.apply(Inspeximus(p), 7, now=T0)
    seg = next(iter(archive.listed_segments(p)))
    con = sqlite3.connect(os.path.join(tmp_path, seg))
    con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('tampered', '1')")
    con.commit()
    con.close()
    with pytest.raises(archive.ArchiveRefused):
        archive.apply(Inspeximus(p), 7, now=T0)
    assert set(ids) <= {r["id"] for r in Inspeximus(p)._items}, "the hot rows were dropped anyway"


# ── temp sweep and log head ───────────────────────────────────────────────────────────────────────────

def test_the_sweep_keeps_a_temp_a_pending_intent_names_and_removes_one_it_does_not(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    with monkeypatch.context() as mp:
        mp.setattr(archive, "commit_erasure", _raise)
        with pytest.raises(Stop):
            Inspeximus(p).forget(ids=[ids[1]], request_id="r")
    (it,) = archive._pending_intents(archive.read_log(p))
    seg = it["segment"]
    stray = seg + ".tmp.999999"
    open(os.path.join(tmp_path, stray), "wb").write(b"x")
    swept = archive.sweep_temps(p)
    assert swept == [stray]
    assert os.path.exists(os.path.join(tmp_path, it["temp"]))


def test_a_stop_between_the_log_and_its_head_leaves_a_log_that_verifies(tmp_path, monkeypatch):
    p, ids = _unarchived(tmp_path, monkeypatch)
    with monkeypatch.context() as mp:
        mp.setattr(archive, "_write_head", _raise)
        with pytest.raises(Stop):
            archive.apply(Inspeximus(p), 7, now=T0)
    assert [e["kind"] for e in archive.read_log(p)].count("move") >= 1, "the log is written before its head"
    assert archive.log_head_problems(p) == []
    assert set(ids) <= {r["id"] for r in Inspeximus(p)._items}, "control: the hot rows were not dropped"
    assert archive.apply(Inspeximus(p), 7, now=T0)["applied"]
    assert archive.recorded_head(p)["count"] == len(archive.read_log(p))


def test_a_withdrawn_erasure_is_listed_in_the_certificate_and_does_not_fail_it(tmp_path, monkeypatch):
    """AUDIT-A's re-review: an aborted intent claims nothing, so it is not a certificate problem, but an
    auditor sees that an erasure was attempted there and withdrawn."""
    p, ids, hot = _store(tmp_path, monkeypatch)
    by_seg = archive.locate(Inspeximus(p), [ids[1]])
    archive.prepare_erasure(Inspeximus(p), by_seg)
    assert (next(iter(by_seg)), "aborted") in archive.recover(Inspeximus(p)), "control: the intent was aborted"
    Inspeximus(p).forget(ids=[ids[2]], request_id="later")
    cert = Inspeximus(p).erasure_certificate()
    aborted = cert["archive"]["aborted"]
    assert aborted == [{"segment": next(iter(by_seg)), "ids": 1, "reason": "its tombstones are not on disk"}]
    # The fixture store runs without write receipts, so self_check is never verified here; the archive
    # block is what an abort could fail, and it must report nothing.
    assert cert["archive"]["problems"] == [], cert["archive"]["problems"]
