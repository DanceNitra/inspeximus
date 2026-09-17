"""The write receipt of an erased record no longer confirms a guessed text.

Found by a red team on 2026-09-17, on the run published at erasure.html: after the page's exact
erasure flow, `mem.json.receipts.json` still held `sha256(canon({text, key}))` for each erased
record, and a loop over "Alice phone is +%d" for 0..999 recovered "Alice phone is +100". The
tombstone was content-free; the receipt was not, and `actions.py` already called that shape "not
content-free" for its own digests.

Since 2.40.0 every record carries a 128-bit `nonce` that `_write_commit` folds into the three
content-bearing hashes. The nonce lives in the record, so a holder of the live record still
recomputes the receipt, and erasing the record removes the only thing that makes the hash
guessable. Controls: verify_writes still passes on a live store, a record whose nonce is stripped
fails it, and a record written without a nonce (before 2.40.0) hashes exactly as it did.
"""
from __future__ import annotations

import os

import pytest

from inspeximus import Inspeximus
from inspeximus.core import _canon, _sha256_hex

cryptography = pytest.importorskip("cryptography")


def _store(tmp_path):
    return Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=os.urandom(32).hex())


def _naive(text, key):
    return _sha256_hex(_canon({"text": text, "key": key}))


def test_the_receipt_of_an_erased_record_does_not_match_the_guessed_text(tmp_path):
    m = _store(tmp_path)
    rid = m.remember("Alice phone is +100", key="alice::phone", source={"doc": "crm/alice"})
    commit = [r for r in m._receipts if r["memory_id"] == rid][-1]["commit"]
    assert commit["immutable_sha256"] != _naive("Alice phone is +100", "alice::phone")
    assert m.verify_writes()[0] is True                                    # a live record still verifies

    m.forget_subject("crm/alice", request_id="DSAR-1")
    assert all(r["id"] != rid for r in m.items)
    # the red team's loop, against the receipt that survives the erasure
    guessed = any(_naive("Alice phone is +%d" % i, "alice::phone") == commit["immutable_sha256"]
                  for i in range(1000))
    assert guessed is False
    # and nothing content-free carries the nonce onward
    assert all("nonce" not in t for t in m._tombstones)


def test_a_record_whose_nonce_was_stripped_no_longer_matches_its_receipt(tmp_path):
    m = _store(tmp_path)
    rid = m.remember("Alice phone is +100", key="alice::phone")
    assert m.verify_writes()[0] is True
    rec = next(r for r in m.items if r["id"] == rid)
    assert len(rec["nonce"]) == 32
    del rec["nonce"]
    ok, problems = m.verify_writes()
    assert ok is False and any(rid in p for p in problems)


def test_a_record_written_before_the_nonce_hashes_as_it_always_did():
    """An upgrade must not raise a tamper alarm on every honest old store."""
    old = {"id": "abc", "text": "t", "key": "k", "mtype": "fact", "object": {"v": 1}, "status": "active"}
    c = Inspeximus._write_commit(old)
    assert c["immutable_sha256"] == _naive("t", "k")
    assert c["content_sha256"] == _sha256_hex(_canon({"text": "t", "key": "k", "mtype": "fact"}))
    assert c["value_sha256"] == _sha256_hex(_canon({"object": {"v": 1}}))
    # and the same record with a nonce hashes differently in all three
    new = dict(old, nonce="00" * 16)
    n = Inspeximus._write_commit(new)
    assert n["immutable_sha256"] != c["immutable_sha256"]
    assert n["content_sha256"] != c["content_sha256"]
    assert n["value_sha256"] != c["value_sha256"]
