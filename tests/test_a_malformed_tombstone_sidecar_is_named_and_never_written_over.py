"""3.16.2, AUDIT-A F-3, the tombstone half and the readers its test file does not cover.

A tombstone sidecar that is not JSON, not a list, or holds an entry that is not a tombstone made
erasure_certificate(), governance_report(), erasure_report(), forget() and verify_writes() raise AttributeError,
TypeError or KeyError. Worse, a sidecar that did not parse was read as an EMPTY chain, and the next erasure wrote
a fresh chain over it. Now each defect is named: the verifiers and reports return it, and an operation that would
extend or certify the chain raises SidecarMalformed and changes nothing. A malformed receipt sidecar makes the
certificate refuse the same way and the governance report say why it was not issued.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus.core import Inspeximus, SidecarMalformed  # noqa: E402

SHAPES = {
    "an entry is a string": lambda c: c.__setitem__(0, "garbage"),
    "an entry is null": lambda c: c.__setitem__(0, None),
    "memory_id is a list": lambda c: c[0].__setitem__("memory_id", ["a"]),
    "memory_id is missing": lambda c: c[0].pop("memory_id"),
    "the file is an object": "object",
    "the file is not JSON": "bytes",
    "the file is not UTF-8": "binary",
}


def _store(tmp_path):
    p = str(tmp_path / "st" / "memory.json")
    os.makedirs(os.path.dirname(p))
    m = Inspeximus(p, receipts=True)
    gone = m.remember("one", key="k1")
    keep = m.remember("two", key="k2")
    m.forget(ids=[gone])
    m.flush()
    assert json.load(open(p + ".tombstones.json"))[0]["memory_id"] == gone, "control: a real chain on disk"
    return p, keep


def _break(p, shape):
    tp = p + ".tombstones.json"
    how = SHAPES[shape]
    if how == "bytes":
        open(tp, "w").write("[{ not json")
    elif how == "binary":
        open(tp, "wb").write(b"\x00\xff junk")
    else:
        chain = json.load(open(tp))
        chain = {"not": "a list"} if how == "object" else (how(chain) or chain)
        json.dump(chain, open(tp, "w"))
    return open(tp, "rb").read()


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_malformed_tombstone_sidecar_gets_a_named_verdict(tmp_path, shape):
    p, keep = _store(tmp_path)
    broken = _break(p, shape)
    ok, problems = Inspeximus(p, receipts=True).verify_writes()
    assert ok is False and any("tombstone" in x for x in problems), problems
    rep = Inspeximus(p, receipts=True).governance_report()
    assert rep["proof"]["verified"] is False and rep["proof"]["problems"], rep
    Inspeximus(p, receipts=True).erasure_report()
    with pytest.raises(SidecarMalformed):
        Inspeximus(p, receipts=True).erasure_certificate()
    with pytest.raises(SidecarMalformed):
        Inspeximus(p, receipts=True).forget(ids=[keep])
    assert open(p + ".tombstones.json", "rb").read() == broken, "the malformed chain was written over"
    assert keep in {r["id"] for r in Inspeximus(p)._items}, "a refused erasure erased nothing"
    Inspeximus(p, receipts=True).remember("ordinary writes go on", key="k3")


def test_a_malformed_receipt_makes_the_certificate_refuse_and_the_report_say_why(tmp_path):
    p, _ = _store(tmp_path)
    rp = p + ".receipts.json"
    chain = json.load(open(rp))
    chain[0] = "garbage"
    json.dump(chain, open(rp, "w"))
    with pytest.raises(SidecarMalformed):
        Inspeximus(p, receipts=True).erasure_certificate()
    rep = Inspeximus(p, receipts=True).governance_report()
    assert rep["proof"]["verified"] is False and any("receipt" in x for x in rep["proof"]["problems"]), rep


def test_a_repaired_sidecar_is_read_again(tmp_path):
    p, keep = _store(tmp_path)
    good = open(p + ".tombstones.json", "rb").read()
    _break(p, "an entry is null")
    h = Inspeximus(p, receipts=True)
    assert h.verify_writes()[0] is False
    open(p + ".tombstones.json", "wb").write(good)
    assert h.forget(ids=[keep])["forgotten"] == 1, "the same handle reads the repaired file and erases"
