"""AUDIT-B 3.17.0 candidate, AUDIT-A required change 4: a receipted write stopped after each step, then read.

The receipt chain is a snapshot (`<store>.receipts.json`, an object) plus an append-only tail
(`<store>.receipts.tail.jsonl`). A write has these steps, and the rule each stop must keep is the one the array
format kept: a stop may leave a record without its receipt, which `verify_writes` names, and never a receipt that
is not in the chain, a chain that reads as complete while a line is missing, or a next write that cannot go on.

  row save -> tail append (header on a new tail) -> fsync -> outside head -> compaction: snapshot, then tail

Each test stops one step, opens a new handle and checks what a reader sees, then writes again and checks that the
chain goes on. The cut line is made the way a crash makes it: half of the bytes are written and the process stops.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inspeximus.core as core  # noqa: E402
from inspeximus import receipts_tail as rt  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402
from conftest import tail_config  # noqa: E402


class Stop(Exception):
    pass


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path_factory):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path_factory.mktemp("key-home")))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    tail_config(True)


def _store(tmp_path, n=5):
    p = str(tmp_path / "s.json")
    m = Inspeximus(p, receipts=True)
    for i in range(n):
        m.remember(f"fact number {i}", key=f"k{i}")
    m.flush()
    return p, m


def _pair(p):
    return rt.read(p + ".receipts.json", rt.tail_path(p + ".receipts.json"), core._GENESIS)


def _bytes(p):
    out = {}
    for suffix in (".receipts.json", ".receipts.tail.jsonl"):
        try:
            out[suffix] = open(p + suffix, "rb").read()
        except FileNotFoundError:
            out[suffix] = None
    return out


def test_the_converted_store_is_an_object_and_a_tail(tmp_path):
    p, m = _store(tmp_path)
    snap = json.load(open(p + ".receipts.json"))
    assert isinstance(snap, dict) and snap["kind"] == rt.SNAPSHOT_KIND, "an array is what old writers extend"
    res = _pair(p)
    assert res["mode"] == "tail" and len(res["entries"]) == 5 and not res["problems"] and not res["torn"]
    assert res["snap_n"] == 1, "the snapshot holds the receipt written at conversion; the rest are tail lines"
    head = open(p + ".receipts.tail.jsonl", encoding="utf-8").readline()
    assert json.loads(head) == {"kind": rt.TAIL_KIND, "base_pos": 1, "base_hash": res["entries"][0]["hash"]}


def test_a_stop_before_the_append_leaves_a_record_without_a_receipt_and_the_chain_goes_on(tmp_path, monkeypatch):
    p, m = _store(tmp_path)
    before = _bytes(p)
    with monkeypatch.context() as mp:
        mp.setattr(rt, "append", lambda *a, **k: (_ for _ in ()).throw(Stop()))
        with pytest.raises((Stop, core.ProofNotWritten)):
            m.remember("written, receipt stopped", key="k-stop")
    assert _bytes(p) == before, "no byte of either receipt file moved"
    m2 = Inspeximus(p, receipts=True)
    ok, problems = m2.verify_writes()
    assert not ok and any("k-stop" in x or "no write receipt" in x or "covered" in x for x in problems), problems
    m2.remember("the next write", key="k-next")
    m2.flush()
    res = _pair(p)
    assert not res["problems"] and len(res["entries"]) == 6


def test_a_cut_last_line_is_information_while_the_head_is_not_ahead_and_the_next_write_removes_it(tmp_path, monkeypatch):
    p, m = _store(tmp_path)
    real_write = os.write

    def half(fd, data):
        return real_write(fd, bytes(data)[: max(1, len(data) // 2)]) and (_ for _ in ()).throw(Stop())

    with monkeypatch.context() as mp:
        mp.setattr(core._rtail.os, "write", half)
        with pytest.raises((Stop, core.ProofNotWritten)):
            m.remember("cut while appending", key="k-cut")
    res = _pair(p)
    assert res["torn"] and not res["problems"] and len(res["entries"]) == 5, "a cut line is not a bad line"
    # The head is written after the fsync, so a cut write never reached it.
    m2 = Inspeximus(p, receipts=True)
    assert m2.read_head()["n_writes"] == 5
    ok, problems = m2.verify_writes()
    assert m2.receipts_torn_tail is True
    assert not any("cut line" in x for x in problems), "the cut line alone is information, not a problem"
    m2.remember("the next write", key="k-next")
    m2.flush()
    res = _pair(p)
    assert not res["torn"] and not res["problems"] and len(res["entries"]) == 6, "the next writer cut the torn bytes"
    assert [e["seq"] for e in res["entries"]] == list(range(6))


def test_a_cut_line_with_the_head_ahead_is_a_problem_not_information(tmp_path):
    """A line the head had seen that is now cut off is a truncated chain, not a crash."""
    p, m = _store(tmp_path)
    m.remember("one more", key="k-more")
    m.flush()
    tp = p + ".receipts.tail.jsonl"
    data = open(tp, "rb").read()
    open(tp, "wb").write(data[: -(len(data.splitlines()[-1]) // 2) - 1])         # cut the last line in half
    m2 = Inspeximus(p, receipts=True)
    ok, problems = m2.verify_writes()
    assert not ok
    assert any("head" in x for x in problems), problems
    assert any("cut line" in x for x in problems), "the cut line is named as not a crash"


def test_a_stop_after_the_fsync_and_before_the_head_leaves_the_head_one_behind_and_valid(tmp_path, monkeypatch):
    p, m = _store(tmp_path)
    with monkeypatch.context() as mp:
        mp.setattr(Inspeximus, "_record_head", lambda self, force=False: (_ for _ in ()).throw(Stop()))
        with pytest.raises(Stop):
            m.remember("durable, head not moved", key="k-late")
    m2 = Inspeximus(p, receipts=True)
    assert len(m2._receipts) == 6 and m2.read_head()["n_writes"] == 5
    ok, problems = m2.verify_writes()
    assert ok, problems
    m2.remember("the next write", key="k-next")
    m2.flush()
    assert m2.read_head()["n_writes"] == 7


def _fill_to_compaction(tmp_path, monkeypatch, n_before):
    p = str(tmp_path / "s.json")
    monkeypatch.setattr(rt, "COMPACT_AT", 4)
    m = Inspeximus(p, receipts=True)
    for i in range(n_before):
        m.remember(f"fact number {i}", key=f"k{i}")
    m.flush()
    return p, m


def test_a_stop_between_the_snapshot_and_the_empty_tail_loses_and_doubles_nothing(tmp_path, monkeypatch):
    p, m = _fill_to_compaction(tmp_path, monkeypatch, 4)          # conversion + 3 tail lines: one below the trigger
    assert _pair(p)["disk_n"] == 4 and _pair(p)["snap_n"] == 1
    real = core._durable_replace
    calls = []

    def second_replace_stops(path, payload, *a, **k):
        calls.append(str(path))
        if str(path).endswith(".receipts.tail.jsonl"):
            raise Stop()
        return real(path, payload, *a, **k)

    with monkeypatch.context() as mp:
        mp.setattr(core, "_durable_replace", second_replace_stops)
        with pytest.raises((Stop, core.ProofNotWritten)):
            m.remember("triggers the compaction", key="k-trigger")
    res = _pair(p)
    assert res["snap_n"] == 5, "the snapshot was replaced first and holds every receipt"
    assert not res["problems"] and len(res["entries"]) == 5, "the tail's lines below the snapshot are skipped, not doubled"
    assert [e["seq"] for e in res["entries"]] == list(range(5))
    m2 = Inspeximus(p, receipts=True)
    assert m2.verify_writes()[0]
    m2.remember("the next write", key="k-next")
    m2.flush()
    res = _pair(p)
    assert not res["problems"] and len(res["entries"]) == 6
    assert [e["seq"] for e in res["entries"]] == list(range(6))


def test_a_stop_before_the_snapshot_replace_leaves_the_pair_as_it_was(tmp_path, monkeypatch):
    p, m = _fill_to_compaction(tmp_path, monkeypatch, 4)
    before = _bytes(p)
    real = core._durable_replace

    def first_replace_stops(path, payload, *a, **k):
        if str(path).endswith(".receipts.json"):
            raise Stop()
        return real(path, payload, *a, **k)

    with monkeypatch.context() as mp:
        mp.setattr(core, "_durable_replace", first_replace_stops)
        with pytest.raises((Stop, core.ProofNotWritten)):
            m.remember("triggers the compaction", key="k-trigger")
    after = _bytes(p)
    assert after[".receipts.json"] == before[".receipts.json"], "the snapshot did not move"
    res = _pair(p)
    assert not res["problems"] and len(res["entries"]) == 5, "the line appended before the compaction is in the tail"


def test_a_stop_while_converting_leaves_a_readable_array_or_a_readable_pair(tmp_path, monkeypatch):
    """Conversion writes the snapshot object, then the tail header. Stopped between the two, the object has no tail
    beside it, which `read` takes as an empty tail."""
    p = str(tmp_path / "s.json")
    tail_config(False)
    m = Inspeximus(p, receipts=True)
    for i in range(3):
        m.remember(f"fact number {i}", key=f"k{i}")
    m.flush()
    assert isinstance(json.load(open(p + ".receipts.json")), list)
    tail_config(True)
    real = core._durable_replace

    def tail_header_stops(path, payload, *a, **k):
        if str(path).endswith(".receipts.tail.jsonl"):
            raise Stop()
        return real(path, payload, *a, **k)

    with monkeypatch.context() as mp:
        mp.setattr(core, "_durable_replace", tail_header_stops)
        with pytest.raises((Stop, core.ProofNotWritten)):
            m.remember("first write after the switch", key="k-conv")
    res = _pair(p)
    assert res["mode"] == "tail" and not res["problems"] and len(res["entries"]) == 4
    m2 = Inspeximus(p, receipts=True)
    m2.remember("the next write", key="k-next")
    m2.flush()
    res = _pair(p)
    assert not res["problems"] and len(res["entries"]) == 5 and res["good_off"] > 0


@pytest.mark.parametrize("damage", ["line_removed", "header_base_hash", "snapshot_older_than_tail", "unknown_line"])
def test_a_damaged_pair_is_named_and_a_writer_refuses_to_write_over_it(tmp_path, damage):
    p, m = _store(tmp_path, n=6)
    tp = p + ".receipts.tail.jsonl"
    lines = open(tp, "rb").read().split(b"\n")
    if damage == "middle_line_edited":
        doc = json.loads(lines[2])
        doc["entry"]["memory_id"] = "forged"
        lines[2] = json.dumps(doc).encode()
    elif damage == "line_removed":
        del lines[2]
    elif damage == "header_base_hash":
        h = json.loads(lines[0])
        h["base_hash"] = "0" * 64
        lines[0] = json.dumps(h).encode()
    elif damage == "snapshot_older_than_tail":
        h = json.loads(lines[0])
        h["base_pos"] = 3
        lines[0] = json.dumps(h).encode()
    elif damage == "unknown_line":
        lines[2] = b"not json at all"
    open(tp, "wb").write(b"\n".join(lines))
    before = _bytes(p)
    m2 = Inspeximus(p, receipts=True)
    ok, problems = m2.verify_writes()
    assert not ok and problems, "a damaged pair is never a passing verify_writes"
    res = _pair(p)
    assert len(res["problems"]) == 1, "the read stops at the first line that does not fit and names only it"
    with pytest.raises((core.SidecarMalformed, core.ProofNotWritten)):
        m2.remember("must not write over the damage", key="k-refused")
    assert _bytes(p) == before, "the damaged files are left as they were: nothing healed the evidence away"


def test_old_chain_read_through_the_new_reader_equals_the_array(tmp_path, monkeypatch):
    """Differential: the same chain as an array and as a pair is the same list, and verify_writes, anchor and
    verify_consistency say the same about both."""
    p, m = _store(tmp_path, n=12)
    pair = _pair(p)["entries"]
    q = str(tmp_path / "copy.json")
    import shutil
    shutil.copy(p, q)
    json.dump(pair, open(q + ".receipts.json", "w"))                  # the array form of the same chain
    tail_config(False)
    a, b = Inspeximus(p, receipts=True), Inspeximus(q, receipts=True)
    assert [r["hash"] for r in a._receipts] == [r["hash"] for r in b._receipts]
    assert a.verify_writes()[0] and b.verify_writes()[0]
    anc_a, anc_b = a.anchor(), b.anchor()
    assert anc_a["n_writes"] == anc_b["n_writes"] and anc_a["writes_tip"] == anc_b["writes_tip"]
    assert a.verify_consistency(anc_a)[0] and b.verify_consistency(anc_a)[0]


def test_an_edited_line_stays_flagged_after_a_later_write_because_the_tail_is_not_rewritten(tmp_path):
    """The array format rewrote every receipt from the handle's memory on the next write, so an edit to the file was
    overwritten by the handle's own copy. The tail is appended to, so the edit stays on disk and verify_writes keeps
    naming it."""
    p, m = _store(tmp_path, n=6)
    tp = p + ".receipts.tail.jsonl"
    lines = open(tp, "rb").read().split(b"\n")
    doc = json.loads(lines[2])
    doc["entry"]["memory_id"] = "forged"
    lines[2] = json.dumps(doc).encode()
    open(tp, "wb").write(b"\n".join(lines))
    m2 = Inspeximus(p, receipts=True)
    assert not m2.verify_writes()[0]
    m2.remember("a later write", key="k-later")
    m2.flush()
    m3 = Inspeximus(p, receipts=True)
    ok, problems = m3.verify_writes()
    assert not ok and any("tampered" in x or "hash mismatch" in x for x in problems), problems


def _edit_tail(p, fn):
    tp = p + ".receipts.tail.jsonl"
    lines = open(tp, "rb").read().split(b"\n")
    fn(lines)
    open(tp, "wb").write(b"\n".join(lines))


def test_a_tail_line_below_the_snapshot_that_is_not_the_snapshots_receipt_is_named(tmp_path, monkeypatch):
    """After a compaction cut between its two steps the tail repeats receipts the snapshot already holds. They are
    skipped only when they are the same receipts."""
    p, m = _fill_to_compaction(tmp_path, monkeypatch, 4)
    real = core._durable_replace

    def tail_stops(path, payload, *a, **k):
        if str(path).endswith(".receipts.tail.jsonl"):
            raise Stop()
        return real(path, payload, *a, **k)

    with monkeypatch.context() as mp:
        mp.setattr(core, "_durable_replace", tail_stops)
        with pytest.raises((Stop, core.ProofNotWritten)):
            m.remember("triggers the compaction", key="k-trigger")
    assert not _pair(p)["problems"]

    def forge(lines):
        doc = json.loads(lines[2])
        doc["entry"]["hash"] = "f" * 64
        lines[2] = json.dumps(doc).encode()
    _edit_tail(p, forge)
    assert _pair(p)["problems"], "a repeated line that differs from the snapshot's receipt is a problem"


def test_a_line_whose_position_fits_but_whose_link_does_not_is_named(tmp_path):
    p, m = _store(tmp_path, n=6)

    def relink(lines):
        doc = json.loads(lines[3])
        doc["entry"]["prev"] = "a" * 64
        lines[3] = json.dumps(doc).encode()
    _edit_tail(p, relink)
    res = _pair(p)
    assert res["problems"] and len(res["entries"]) < 6, "the chain stops at the line that does not link"


def test_a_peer_that_appends_between_the_receipt_being_built_and_the_append_is_not_lost(tmp_path):
    """The reconcile before a receipt is built is not under the lock. The append re-reads the pair under it."""
    p, a = _store(tmp_path, n=4)
    b = Inspeximus(p, receipts=True)
    real = Inspeximus._flush_receipts
    fired = []

    def peer_writes_first(self):
        if not fired:
            fired.append(1)
            a.remember("the peer's write", key="k-peer")
            a.flush()
        return real(self)

    Inspeximus._flush_receipts = peer_writes_first
    try:
        b.remember("this handle's write", key="k-mine")
        b.flush()
    finally:
        Inspeximus._flush_receipts = real
    m = Inspeximus(p, receipts=True)
    ids = {r["id"] for r in m.items}
    assert ids <= {r["memory_id"] for r in m._receipts}, "a record has no receipt: one was lost"
    assert [r["seq"] for r in m._receipts] == list(range(len(m._receipts)))
    assert not _pair(p)["problems"]
    assert m.verify_writes()[0]


def test_the_tail_is_fsynced_before_the_head_outside_the_store_moves(tmp_path, monkeypatch):
    p, m = _store(tmp_path)
    order = []
    real_fsync, real_head = os.fsync, Inspeximus._record_head
    monkeypatch.setattr(rt.os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(Inspeximus, "_record_head",
                        lambda self, force=False: (order.append("head"), real_head(self, force))[1])
    m.remember("ordered", key="k-order")
    assert "fsync" in order and "head" in order, order
    assert order.index("fsync") < order.index("head"), order


def test_a_store_in_the_tail_format_stays_in_it_when_the_switch_is_off(tmp_path, monkeypatch):
    """The sidecar is no longer an array, so a writer without the switch must append to the tail. Writing the array
    would drop every receipt the tail holds."""
    p, m = _store(tmp_path, n=5)
    tail_config(False)
    m2 = Inspeximus(p, receipts=True)
    m2.remember("written without the switch", key="k-off")
    m2.flush()
    assert isinstance(json.load(open(p + ".receipts.json")), dict)
    res = _pair(p)
    assert len(res["entries"]) == 6 and not res["problems"]
