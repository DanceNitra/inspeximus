"""The tombstone sidecar is written once per erasure batch, not once per tombstone.

`_emit_tombstone` rewrote the ENTIRE chain to disk every time it appended one link. A batch erasure emits
one tombstone per record, so erasing k records rewrote a chain growing to k, k times: O(k^2) serialization
plus k atomic file replaces. MEASURED on forget_subject (k subject records, n filler), median of 3:

    k=50/n=2000   0.111s -> 0.051s   2.2x
    k=200/n=4000  0.539s -> 0.174s   3.1x
    k=400/n=8000  1.639s -> 0.467s   3.5x
    k=800/n=8000  5.388s -> 0.965s   5.6x

and the growth in k at fixed n fell from 3.29x per doubling to 2.07x -- quadratic to linear.

WHAT MUST NOT CHANGE, which is what this file pins:
  - the bytes on disk (verified identical to one-at-a-time emission, chain head included),
  - a SINGLE emit is still durable the instant it returns (`defer` defaults to False),
  - the sidecar is still written BEFORE the store save,
  - a sidecar write failure is still captured in `_sidecar_errors` rather than raised or lost.

The last two are the crash-safety contract. Deferring is strictly safer here, not merely faster: the old
order left j-of-k tombstones on disk claiming erasures the store save had not yet performed -- a deletion
proof for records still present. All-or-nothing can only lose the proof of a deletion that did not happen.
"""
import json
import os

import pytest

from inspeximus.core import Inspeximus


FROZEN = 1785300500.0


@pytest.fixture()
def frozen_time(monkeypatch):
    """The chain commits to ts, so real time makes any two runs differ and an equivalence check vacuous."""
    import inspeximus.core as core
    monkeypatch.setattr(core.time, "time", lambda: FROZEN)


def _store(tmp_path, name, n):
    m = Inspeximus(str(tmp_path / name), receipts=True)
    for i in range(n):
        m.remember(f"record {i} for the subject", tags=["pii"], source={"doc": "hr/alice"})
    m.flush()
    return m


def _sidecar(m):
    return open(str(m._tombstones_path), encoding="utf-8").read()


def test_control_a_single_emit_is_still_written_immediately(tmp_path, frozen_time):
    """Without this, "fewer writes" could simply mean the single-record path stopped persisting at all."""
    m = _store(tmp_path, "one.json", 1)
    t = m._emit_tombstone("deadbeef01", FROZEN, "req-1")
    assert os.path.exists(str(m._tombstones_path)), "a non-deferred emit must be on disk when it returns"
    chain = json.loads(_sidecar(m))
    assert [x["hash"] for x in chain] == [t["hash"]]


def test_a_batch_erasure_writes_the_sidecar_once_not_once_per_record(tmp_path, frozen_time, monkeypatch):
    """The defect: k tombstones cost k full rewrites of a chain growing to k."""
    m = _store(tmp_path, "batch.json", 25)
    writes = []
    real = Inspeximus._atomic_write

    def counting(path, data):
        if str(path).endswith(".tombstones.json"):
            writes.append(len(data))
        return real(path, data)

    monkeypatch.setattr(Inspeximus, "_atomic_write", staticmethod(counting))
    out = m.forget_subject("hr/alice")

    assert out["erased"] == 25, out
    assert len(writes) == 1, (
        f"erasing 25 records rewrote the tombstone chain {len(writes)} times. Each rewrite serializes the "
        f"whole chain, so this is O(k^2) in the size of the erasure.")
    assert len(json.loads(_sidecar(m))) == 25, "all 25 tombstones must still be on disk"


def test_the_batched_chain_is_byte_identical_to_emitting_one_at_a_time(tmp_path, frozen_time):
    """The equivalence pin: faster must mean the same file, not a differently-shaped one."""
    batched = _store(tmp_path, "b.json", 12)
    ids = sorted(r["id"] for r in batched.items)
    batched.forget_subject("hr/alice")
    got = _sidecar(batched)

    # the same links, appended the old way: one immediate write each
    one_by_one = Inspeximus(str(tmp_path / "s.json"), receipts=True)
    one_by_one._receipt_sk = batched._receipt_sk          # same key -> same signatures
    for tid in ids:
        one_by_one._emit_tombstone(tid, FROZEN, None, basis="forget",
                                   authorized_by=None, authorization=None)
    expected = _sidecar(one_by_one)

    assert got == expected, "the batched sidecar differs from the incrementally written one"
    assert json.loads(got)[-1]["hash"] == json.loads(expected)[-1]["hash"]


def test_the_equivalence_check_can_actually_fail(tmp_path, frozen_time):
    """POSITIVE CONTROL. Without it the assertion above could be comparing two identically-empty files."""
    a = _store(tmp_path, "a.json", 5)
    a.forget_subject("hr/alice")
    b = _store(tmp_path, "b2.json", 6)                    # one more record -> one more link
    b.forget_subject("hr/alice")
    assert _sidecar(a) != _sidecar(b), "the comparison cannot distinguish two different chains"


def test_the_sidecar_is_written_before_the_store_save(tmp_path, frozen_time, monkeypatch):
    """Crash order is the contract: never a store that has dropped records with no proof on disk."""
    m = _store(tmp_path, "order.json", 6)
    order = []
    real_atomic, real_save = Inspeximus._atomic_write, Inspeximus._save

    def note_write(path, data):
        if str(path).endswith(".tombstones.json"):
            order.append("tombstones")
        return real_atomic(path, data)

    def note_save(self, *a, **k):
        order.append("save")
        return real_save(self, *a, **k)

    monkeypatch.setattr(Inspeximus, "_atomic_write", staticmethod(note_write))
    monkeypatch.setattr(Inspeximus, "_save", note_save)
    m.forget_subject("hr/alice")

    assert "tombstones" in order and "save" in order, order
    assert order.index("tombstones") < order.index("save"), (
        f"the store was saved before the deletion proof was persisted: {order}")


def test_a_failing_sidecar_write_is_still_reported_after_a_batch(tmp_path, frozen_time, monkeypatch):
    """The error capture moved with the write. If it were lost, forget_subject would report tombstones:k
    while a reload showed erasures_total: 0 -- silently, which is the bug the original comment records."""
    m = _store(tmp_path, "fail.json", 4)

    def boom(path, data):
        if str(path).endswith(".tombstones.json"):
            raise OSError("disk full")
        return None

    monkeypatch.setattr(Inspeximus, "_atomic_write", staticmethod(boom))
    m.forget_subject("hr/alice")                          # must not raise
    assert "tombstones" in m._sidecar_errors, m._sidecar_errors
    assert "disk full" in m._sidecar_errors["tombstones"]


def test_a_pathless_store_does_not_try_to_flush(frozen_time):
    """An in-memory store has no sidecar path; the flush must be a no-op, not an AttributeError."""
    m = Inspeximus(None, receipts=True)
    m.remember("held only in memory", source={"doc": "hr/alice"})
    out = m.forget_subject("hr/alice")
    assert out["erased"] == 1
    assert len(m._tombstones) == 1
