"""recommit() writes the receipt sidecar once, and finds each record's latest receipt in one pass.

Measured 2026-10-01 on a copy of our own MCP store (13,142 records, 8,101 active): recommit(all) took
1,357 s. Every emitted receipt rewrote the whole sidecar (~10 MB, 6,392 times), and every record scanned
the whole chain for its latest receipt. The fix defers the sidecar write to one write per call and builds
the latest-receipt index once. These tests pin the work, not the clock, and check that the result is the
same: every record recommitted, the chain on disk complete, verify_writes clean.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402

N = 60


def _unbound_store(tmp_path):
    p = str(tmp_path / "s.json")
    m0 = Inspeximus(p, receipts=False)
    for i in range(N):
        m0.remember(f"record {i}", source={"doc": f"d{i % 7}"})
    m0.flush()
    return p, Inspeximus(p, receipts=True)


def _count_receipt_writes(monkeypatch):
    writes = []
    real = Inspeximus._atomic_write

    def counted(path, data, *a, **k):
        if str(path).endswith(".receipts.json"):
            writes.append(path)
        return real(path, data, *a, **k)

    monkeypatch.setattr(Inspeximus, "_atomic_write", staticmethod(counted))
    return writes


def test_recommit_writes_the_receipt_sidecar_once(tmp_path, monkeypatch):
    p, m = _unbound_store(tmp_path)
    writes = _count_receipt_writes(monkeypatch)
    res = m.recommit()
    assert len(res["recommitted"]) == N, res
    assert len(writes) == 1, f"the receipt sidecar was written {len(writes)} times for {N} receipts"


def test_the_chain_on_disk_holds_every_recommitted_receipt(tmp_path):
    p, m = _unbound_store(tmp_path)
    res = m.recommit()
    reopened = Inspeximus(p, receipts=True)
    ids = {r["memory_id"] for r in reopened._receipts}
    assert set(res["recommitted"]) <= ids
    assert len(reopened._receipts) == N
    ok, problems = reopened.verify_writes()
    assert ok, problems[:3]


def test_a_second_recommit_skips_everything_and_writes_nothing(tmp_path, monkeypatch):
    p, m = _unbound_store(tmp_path)
    m.recommit()
    writes = _count_receipt_writes(monkeypatch)
    res = m.recommit()
    assert res["recommitted"] == [] and len(res["skipped"]) == N
    assert writes == []


def test_control_a_single_remember_still_writes_its_receipt_at_once(tmp_path, monkeypatch):
    """The deferral is recommit's alone: an ordinary receipted write still persists its receipt."""
    m = Inspeximus(str(tmp_path / "c.json"), receipts=True)
    writes = _count_receipt_writes(monkeypatch)
    m.remember("one fact", source={"doc": "c"})
    assert len(writes) == 1
    assert getattr(m, "_defer_receipt_flush", False) is False


def test_a_chain_that_cannot_be_written_is_put_back(tmp_path, monkeypatch):
    """The deferred write fails: recommit raises, and memory keeps the chain it had, as backfill does."""
    import pytest
    from inspeximus.core import ProofNotWritten
    p, m = _unbound_store(tmp_path)
    before = list(m._receipts)
    real = Inspeximus._atomic_write

    def failing(path, data, *a, **k):
        if str(path).endswith(".receipts.json"):
            raise OSError("disk full (test)")
        return real(path, data, *a, **k)

    monkeypatch.setattr(Inspeximus, "_atomic_write", staticmethod(failing))
    with pytest.raises(ProofNotWritten):
        m.recommit()
    assert m._receipts == before
    assert getattr(m, "_defer_receipt_flush", False) is False
