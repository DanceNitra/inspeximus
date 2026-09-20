"""`enable_receipts()` turns receipts on for a store that already holds records, and covers them.

THE GAP IT CLOSES, measured on our own store on 2026-09-20: 2,901 records, receipts on since
part-way, 2,024 of them covered by no receipt, so `verify_writes()` was False on coverage and an
auditor had nothing that vouched for two thirds of the store. Two more records were born under
receipts and superseded by a writer that had receipts off, which the concealment sweep correctly
reported as "hidden". After one call: 0 uncovered, 0 hidden, verify_writes True, 1.7 s.

WHAT A BACKFILL RECEIPT PROVES, and what it must not be read as. It commits to the record AS IT
STANDS at backfill time, and it says so inside its hash (`backfill`). From then on an edit fails
verify_writes like any other. It cannot reach back before the call, and stripping the marker to
make it look like an ordinary receipt breaks the chain link.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.core import new_receipt_keypair

from _store_io import edit_store


def _mk(n=3, **kw):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    ix = Inspeximus(path=p, **kw)
    for i in range(n):
        ix.remember(f"fact {i}: the deadline is day {i}", key=f"k{i}")
    return p, ix


def test_a_store_with_receipts_off_is_covered_and_then_verifies():
    p, ix = _mk(3)
    assert ix.verify_writes()[0] is False           # DISABLED, nothing to verify
    out = ix.enable_receipts(reason="test")
    assert out["status"] == "enabled"
    assert out["anchored_records"] == 3 and out["already_covered"] == 0
    assert len(out["genesis_root"]) == 64 and out["chain_tip"]
    assert ix.verify_writes() == (True, [])
    # the sidecar exists and reopening with receipts on sees the same clean chain
    assert os.path.exists(p + ".receipts.json")
    assert Inspeximus(path=p, receipts=True).verify_writes() == (True, [])


def test_a_second_call_covers_nothing_and_leaves_the_chain_alone():
    p, ix = _mk(3)
    ix.enable_receipts()
    tip = ix._receipts[-1]["hash"]
    out = ix.enable_receipts()
    assert out["anchored_records"] == 0 and out["already_covered"] == 3
    assert out["genesis_root"] is None and out["chain_tip"] == tip
    assert len(ix._receipts) == 3


def test_an_edit_after_the_backfill_fails_verify_writes():
    p, ix = _mk(3)
    ix.enable_receipts()
    victim = ix.items[1]["id"]

    def _edit(items):
        for r in items:
            if r["id"] == victim:
                r["text"] = "fact 1: the deadline is day 99"
        return items
    edit_store(p, _edit)
    ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
    assert ok is False
    assert any(victim in x and "no longer matches its write receipt" in x for x in problems)


def test_the_backfill_marker_is_inside_the_hash():
    p, ix = _mk(2)
    ix.enable_receipts()
    side = p + ".receipts.json"
    chain = json.loads(open(side, encoding="utf-8").read())
    assert all(rc.get("backfill", {}).get("genesis_root") for rc in chain)
    chain[0].pop("backfill")                        # dress it up as an ordinary receipt
    open(side, "w", encoding="utf-8").write(json.dumps(chain))
    ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
    assert ok is False
    assert any("receipt 0" in x and "tampered" in x for x in problems)


def test_a_store_that_enabled_receipts_part_way_gets_only_the_gap_covered():
    p, ix = _mk(4)                                  # 4 records, no chain
    sk, pk = new_receipt_keypair()
    ix = Inspeximus(path=p, receipts=True, receipt_key=sk)
    ix.remember("late 0", key="l0")
    ix.remember("late 1", key="l1")
    ok, problems = ix.verify_writes()
    assert ok is False and any("covered by NO write receipt" in x for x in problems)
    out = ix.enable_receipts(receipt_key=sk)
    assert out["anchored_records"] == 4 and out["already_covered"] == 2 and out["signed"] is True
    ix2 = Inspeximus(path=p, receipts=True, receipt_key=sk)
    assert ix2.verify_writes(expected_pubkey=pk) == (True, [])
    assert all("sig" in rc for rc in ix2._receipts)


def test_a_different_key_is_refused_so_one_chain_never_has_two_signers():
    p, ix = _mk(2)
    sk, _ = new_receipt_keypair()
    ix = Inspeximus(path=p, receipts=True, receipt_key=sk)
    ix.remember("late", key="l")
    sk2, _ = new_receipt_keypair()
    with pytest.raises(ValueError):
        ix.enable_receipts(receipt_key=sk2)


def test_a_retirement_made_with_receipts_off_is_declared_not_hidden():
    p, ix = _mk(0, receipts=True)
    ix.remember("the deadline is Friday", key="deadline")      # born active, under receipts
    born = ix.items[0]["id"]
    off = Inspeximus(path=p)                                    # a writer with receipts off
    off.remember("the deadline is Monday", key="deadline")      # supersedes it, chain sees nothing
    ix = Inspeximus(path=p, receipts=True)
    ok, problems = ix.verify_writes(coverage_strict=False)
    assert ok is False and any(born in x and "hidden" in x for x in problems)   # the control
    out = ix.enable_receipts()
    assert out["anchored_records"] == 1 and out["retirements_declared"] == 1
    assert Inspeximus(path=p, receipts=True).verify_writes() == (True, [])
    decl = [rc for rc in ix._receipts if rc["memory_id"] == born and rc.get("amends")]
    assert decl and decl[0]["backfill"]["genesis_root"] == out["genesis_root"]


def test_the_audit_bundle_and_anchor_walk_a_chain_with_backfill_receipts():
    from inspeximus import audit_bundle as ab
    p, ix = _mk(3)
    ix.enable_receipts()
    ix.remember("after the backfill", key="after")
    assert ab.verify_bundle(ab.build_bundle(ix))["ok"] is True
    assert ix.anchor()["n_writes"] == 4
