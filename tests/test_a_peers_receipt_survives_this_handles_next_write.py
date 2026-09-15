"""The receipt chain is part of what a peer wrote: a handle adopts it instead of writing over it.

WHY. The store had a merge for peers since 2.10; the receipts sidecar never did. Every emit wrote
this handle's whole in-memory chain over the file, so a long-lived handle whose peer had appended
a receipt overwrote that receipt on its next write, and the peer's record, still in the store, read
forever as "inserted out of band". Measured 2026-09-15 on 2.28.0: two handles, one write each,
sidecar 2; the first refreshes and writes again, sidecar 2, records 3, verify_writes False on a
fresh handle. On the JSON path the mirror image: a refused save emitted no receipt and reload()
re-added the record with none.

A red-team pass of a public reply found it. The reply had described the chain as refusing a
peer's entry; the evidence behind that sentence was this defect.

Each test fails on 2.28.0. The control pins that a single handle's chain is untouched.
"""
from __future__ import annotations

import json
import os

import pytest

from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk, new_receipt_keypair


def _sidecar(p):
    return json.load(open(p + ".receipts.json", encoding="utf-8"))


def _keypair():
    kp = new_receipt_keypair()
    return (kp[0], kp[1]) if isinstance(kp, (tuple, list)) else (kp["private"], kp["public"])


@pytest.mark.parametrize("fmt", ["rows", "json"])
def test_a_long_lived_handle_keeps_the_peers_receipt_on_its_next_write(tmp_path, monkeypatch, fmt):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", fmt)
    p = str(tmp_path / "s.json")
    a = Inspeximus(path=p, receipts=True)
    a.remember("a one", key="a1", object="1")
    b = Inspeximus(path=p, receipts=True)
    b.remember("b one", key="b1", object="1")
    assert len(_sidecar(p)) == 2, "the fixture did not produce two receipts; nothing below measures"
    a.refresh()
    a.remember("a two", key="a2", object="2")
    rc = _sidecar(p)
    items = Inspeximus(path=p).items
    assert len(rc) == 3 and len(items) == 3
    assert {r["memory_id"] for r in rc} == {i["id"] for i in items}
    ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
    assert ok, problems
    assert a.verify_writes()[0], "the long-lived handle must verify its own merged chain"


def test_a_stale_handle_that_never_refreshed_still_adopts_before_it_emits(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "rows")
    p = str(tmp_path / "s.json")
    a = Inspeximus(path=p, receipts=True)
    a.remember("a one", key="a1", object="1")
    Inspeximus(path=p, receipts=True).remember("b one", key="b1", object="1")
    a.remember("a two", key="a2", object="2")            # no refresh, no reload: the emit itself adopts
    assert len(_sidecar(p)) == 3
    assert Inspeximus(path=p, receipts=True).verify_writes()[0]


def test_a_refused_json_write_gets_its_receipt_when_reload_re_adds_it(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    p = str(tmp_path / "s.json")
    a = Inspeximus(path=p, receipts=True)
    a.remember("a one", key="a1", object="1")
    Inspeximus(path=p, receipts=True).remember("b one", key="b1", object="1")
    with pytest.raises(StoreChangedOnDisk):
        a.remember("a two", key="a2", object="2")
    out = a.reload()
    assert out["readded"] == 1, "the fixture did not re-add anything; nothing below measures"
    rc = _sidecar(p)
    items = Inspeximus(path=p).items
    assert len(items) == 3 and {r["memory_id"] for r in rc} == {i["id"] for i in items}
    assert Inspeximus(path=p, receipts=True).verify_writes()[0]


def test_two_signed_handles_with_one_key_leave_one_verifiable_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "rows")
    priv, pub = _keypair()
    p = str(tmp_path / "s.json")
    a = Inspeximus(path=p, receipts=True, receipt_key=priv)
    a.remember("a one", key="a1", object="1")
    Inspeximus(path=p, receipts=True, receipt_key=priv).remember("b one", key="b1", object="1")
    a.refresh()
    a.remember("a two", key="a2", object="2")
    ok, problems = Inspeximus(path=p, receipts=True).verify_writes(expected_pubkey=pub, require_signed=True)
    assert ok, problems


def test_a_rechained_receipt_keeps_its_binding_and_names_its_old_hash(tmp_path, monkeypatch):
    """The divergent case: both handles chained on the same prev. The disk chain wins the common part;
    this handle's entry is moved after it with a fresh seq, prev, hash, and the old hash recorded."""
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "rows")
    p = str(tmp_path / "s.json")
    a = Inspeximus(path=p, receipts=True)
    a.remember("a one", key="a1", object="1")
    b = Inspeximus(path=p, receipts=True)
    # make the two chains diverge: stop a's emit from adopting, then let the reconcile run on refresh
    monkeypatch.setattr(Inspeximus, "_reconcile_receipts_with_disk", lambda self: 0)
    b.remember("b one", key="b1", object="1")
    a.remember("a two", key="a2", object="2")            # a's chain: [a1, a2]; disk now holds a's view
    monkeypatch.undo()
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "rows")
    b.refresh()                                          # b's chain [a1, b1] meets disk [a1, a2]
    rc = b._receipts
    assert [r["memory_id"] for r in rc][:2] == [a._receipts[0]["memory_id"], a._receipts[1]["memory_id"]]
    moved = rc[2]
    assert moved["memory_id"] == b._receipts[2]["memory_id"] and "rechained_from" in moved
    assert moved["prev"] == rc[1]["hash"] and moved["seq"] == 2
    b.remember("b two", key="b2", object="2")
    assert Inspeximus(path=p, receipts=True).verify_writes()[0]


def test_control_a_single_handle_chain_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "rows")
    p = str(tmp_path / "s.json")
    a = Inspeximus(path=p, receipts=True)
    for i in range(6):
        a.remember("r%d" % i, key="k%d" % i, object=str(i))
    rc = _sidecar(p)
    assert len(rc) == 6 and not any("rechained_from" in r for r in rc)
    assert [r["seq"] for r in rc] == list(range(6))
    assert a.verify_writes()[0]
