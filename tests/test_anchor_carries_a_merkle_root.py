"""The anchor gains inclusion proofs WITHOUT invalidating anchors already co-signed.

`anchor()` published a hash-chain head. That catches a rewrite, but only for a verifier holding the
whole log, and it cannot answer "prove THIS record is in your log" at all. RFC 6962 roots now ride
alongside the chain tips so it can, in O(log n), for a verifier who has nothing but the record, the
root they witnessed, and log2(n) hashes.

THE CONSTRAINT THAT SHAPED THIS: witnesses have already co-signed anchors under the old shape, over
`sth_hash`, which covers exactly four fields. Widening that hash would silently invalidate every
signature ever made. So the roots are ADDITIVE and `sth_hash` is untouched; a second commitment
`root_hash` binds the roots for witnesses that understand them. Old verifier, new anchor: still
valid. That is the property this file exists to pin.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.merkle import root as merkle_root
from inspeximus.merkle import verify_consistency_proof


def _store(n=9, **kw):
    d = tempfile.mkdtemp()
    s = Inspeximus(os.path.join(d, "s.json"), receipts=True, **kw)
    for i in range(n):
        s.remember("fact %d about agent memory" % i, key="k::%d" % i)
    return s


def test_the_old_anchor_fields_are_byte_for_byte_unchanged():
    """THE BACKWARD-COMPATIBILITY CONTROL. `sth_hash` must still be the hash of exactly the four
    original fields, or every witness signature ever collected stops verifying."""
    import hashlib
    import json
    s = _store()
    a = s.anchor()
    four = {k: a[k] for k in ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip")}
    expect = hashlib.sha256(
        json.dumps(four, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert a["sth_hash"] == expect, "sth_hash now covers different bytes -- old signatures are void"


def test_the_anchor_gained_roots_without_losing_tips():
    a = _store().anchor()
    for old in ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip", "sth_hash", "ts"):
        assert old in a, "dropped pre-existing anchor field %r" % old
    for new in ("writes_root", "tombstones_root", "merkle", "root_hash"):
        assert new in a
    assert a["merkle"] == "rfc6962-sha256"
    assert len(a["writes_root"]) == 64


def test_verify_consistency_still_works_against_a_pre_merkle_anchor():
    """An anchor recorded by an OLDER version has no roots at all. It must still verify."""
    s = _store(5)
    legacy = {k: v for k, v in s.anchor().items()
              if k in ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip", "sth_hash", "ts")}
    for i in range(4):
        s.remember("later fact %d" % i, key="later::%d" % i)
    ok, problems = s.verify_consistency(legacy)
    assert ok, problems


def test_inclusion_proof_verifies_against_a_witnessed_root():
    s = _store(9)
    witnessed = s.anchor()["writes_root"]        # what an auditor recorded out of band
    for i in range(9):
        b = s.inclusion_proof(i)
        assert Inspeximus.verify_inclusion(b, witnessed), "entry %d did not verify" % i
        assert len(b["audit_path"]) <= 4, "proof is not logarithmic"


def test_inclusion_proof_rejects_what_it_must():
    """NEGATIVE CONTROLS. Without these, a verifier that returns True always passes the test above."""
    s = _store(9)
    root_hex = s.anchor()["writes_root"]
    b = s.inclusion_proof(3)
    assert not Inspeximus.verify_inclusion(b, "00" * 32), "foreign root accepted"
    assert not Inspeximus.verify_inclusion(dict(b, leaf=b["leaf"] + "!"), root_hex), "tampered leaf accepted"
    assert not Inspeximus.verify_inclusion(dict(b, index=4), root_hex), "wrong index accepted"
    assert not Inspeximus.verify_inclusion(dict(b, audit_path=[]), root_hex), "empty path accepted"
    assert not Inspeximus.verify_inclusion({}, root_hex), "malformed bundle accepted"


def test_a_record_from_another_store_does_not_verify():
    """The property that makes this worth anything: the proof is about THIS log."""
    a, b = _store(9), _store(9)
    other = b.inclusion_proof(3)
    assert not Inspeximus.verify_inclusion(other, a.anchor()["writes_root"])


def test_the_leaf_covers_exactly_the_chain_preimage():
    """Pin WHAT is committed. The first version of the tamper test below altered `id` and the root
    did not move -- correctly, because `id` is not in the preimage at all. A tamper test aimed at an
    uncommitted field proves nothing and reads as a pass."""
    s = _store(3)
    core = Inspeximus._chain_core(s._receipts[0], "write")
    assert set(core) >= {"seq", "ts", "memory_id", "commit", "prev"}
    assert "id" not in core, "if `id` joined the preimage, the tamper test below must target it too"


@pytest.mark.parametrize("field", ["memory_id", "commit", "seq", "prev"])
def test_the_root_changes_when_a_committed_field_is_rewritten(field):
    """A rewrite that keeps the chain internally valid must still move the root."""
    s = _store(6)
    before = s.anchor()["writes_root"]
    original = s._receipts[2].get(field)
    s._receipts[2] = dict(s._receipts[2], **{field: "tampered-%s" % field})
    assert s.merkle_root("write") != before, "%r (was %r) is not committed by the leaf" % (field, original)


def test_succinct_consistency_needs_no_replica():
    """verify_consistency needs the log; this needs log2(n) hashes and two roots."""
    s = _store(11)
    m_root = merkle_root(s._merkle_leaves("write")[:6])
    cp = s.merkle_consistency_proof(6)
    assert verify_consistency_proof(6, 11, m_root, bytes.fromhex(cp["root"]),
                                    [bytes.fromhex(h) for h in cp["proof"]])
    assert not verify_consistency_proof(6, 11, bytes(32), bytes.fromhex(cp["root"]),
                                        [bytes.fromhex(h) for h in cp["proof"]])


def test_empty_and_single_entry_stores_do_not_crash():
    d = tempfile.mkdtemp()
    s = Inspeximus(os.path.join(d, "e.json"), receipts=True)
    a = s.anchor()
    assert len(a["writes_root"]) == 64
    with pytest.raises(IndexError):
        s.inclusion_proof(0)
    s.remember("one", key="k::1")
    b = s.inclusion_proof(0)
    assert Inspeximus.verify_inclusion(b, s.anchor()["writes_root"])
    assert b["audit_path"] == []
