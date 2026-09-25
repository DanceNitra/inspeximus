"""After erasure, nothing on disk confirms a guessed subject.

Session T, item 1. A write receipt commits a record's attribution as
`attrib_sha256 = sha256(canon(sorted(sources)))`, with no salt. The content hashes carry the record's
nonce since 2.40.0 (tests/test_an_erased_records_receipt_cannot_be_guessed.py); the attribution hash
did not. Reproduced on 3.9.4: after `forget_subject("user:alice")`, hashing the guess `user:alice`
matched 3 of 4 receipts (all of hers), `user:bob` 1, `user:carol` 0. So a holder of the receipts
file could confirm who had been erased, from the evidence of the erasure.

The test scans every file in the store directory for the guessed subject's digest in each encoding
a guesser would try, so a second unsalted copy anywhere (a tombstone, a sidecar, the database) fails
it too. Controls: the scanner finds a planted digest, the receipts are on disk, the erasure removed
her records, and the chain verifies before and after.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from inspeximus import Inspeximus
from inspeximus.core import _canon, _sha256_hex

pytest.importorskip("cryptography")


def _guesses(subject: str) -> set:
    c = Inspeximus._canon_source(subject)
    return {_sha256_hex(_canon(sorted({c}))), _sha256_hex(_canon(c)), _sha256_hex(_canon([c])),
            hashlib.sha256(c.encode()).hexdigest(), hashlib.sha256(subject.encode()).hexdigest()}


def _files_holding(root, digests) -> list:
    hits = []
    for d, _, names in os.walk(root):
        for n in names:
            with open(os.path.join(d, n), "rb") as fh:
                blob = fh.read()
            hits += [(n, h) for h in digests if h.encode() in blob]
    return hits


@pytest.mark.parametrize("fmt", ["json", "default"])
def test_an_erased_subject_matches_no_commitment_on_disk(tmp_path, monkeypatch, fmt):
    if fmt == "json":
        monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    else:
        monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)
    root = tmp_path / "store"
    root.mkdir()
    m = Inspeximus(str(root / "mem.json"), receipts=True, receipt_key=os.urandom(32).hex())
    alice = [m.remember(f"alice fact {i}", key=f"alice::f{i}", source={"doc": "user:alice"})
             for i in range(3)]
    m.remember("bob fact", key="bob::f", source={"doc": "user:bob"})
    assert m.verify_writes()[0], "control: the store verifies before the erasure"

    m.forget_subject("user:alice", request_id="DSAR-1")
    assert not any(r["id"] in alice for r in m.items), "control: her records are gone"
    assert m.verify_writes()[0], "control: the chain verifies after the erasure"

    receipts = [p for p in root.rglob("*receipts*") if p.is_file() and p.stat().st_size]
    assert receipts, "control: the receipts are on disk, so the scan reads them"
    planted = root / "planted.txt"
    planted.write_text(sorted(_guesses("user:carol"))[0])
    assert _files_holding(root, _guesses("user:carol")) == [("planted.txt", sorted(_guesses("user:carol"))[0])], \
        "control: the scanner finds a digest that is there"
    planted.unlink()

    assert _files_holding(root, _guesses("user:alice")) == [], \
        "a guess of the erased subject is confirmed by a hash on disk"


def test_a_receipt_written_before_the_fix_still_verifies(tmp_path, monkeypatch):
    """Old chains stay verifiable: a nonced record whose receipt carries the unsalted attribution hash
    (written by 2.40.0 to 3.11.x) passes verify_writes and verify_attribution, and a relabel of it
    still fails both."""
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    path = tmp_path / "mem.json"
    real = Inspeximus._write_commit

    def old(rec, retires=()):
        c = real(rec, retires)
        c.pop("attrib_nonced_sha256", None)
        c["attrib_sha256"] = _sha256_hex(_canon(sorted(Inspeximus._rec_sources(rec))))
        return c
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(old))
    key = os.urandom(32).hex()
    rid = Inspeximus(str(path), receipts=True, receipt_key=key).remember(
        "alice fact", key="alice::f", source={"doc": "user:alice"})
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(real))

    m = Inspeximus(str(path), receipts=True, receipt_key=key)
    commit = [r for r in m._receipts if r["memory_id"] == rid][-1]["commit"]
    assert "attrib_sha256" in commit and "attrib_nonced_sha256" not in commit, "control: an old receipt"
    assert m.verify_writes()[0]
    assert m.verify_attribution()["ok"]

    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["items"] if isinstance(data, dict) else data
    next(r for r in rows if r["id"] == rid)["source"] = {"doc": "user:mallory"}
    path.write_text(json.dumps(data), encoding="utf-8")
    m = Inspeximus(str(path), receipts=True, receipt_key=key)
    assert not m.verify_writes()[0]
    assert rid in m.verify_attribution()["relabeled"]


def test_a_relabel_under_the_nonced_commitment_is_caught(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    path = tmp_path / "mem.json"
    key = os.urandom(32).hex()
    rid = Inspeximus(str(path), receipts=True, receipt_key=key).remember(
        "alice fact", key="alice::f", source={"doc": "user:alice"})
    m = Inspeximus(str(path), receipts=True, receipt_key=key)
    commit = [r for r in m._receipts if r["memory_id"] == rid][-1]["commit"]
    assert "attrib_nonced_sha256" in commit and "attrib_sha256" not in commit
    assert m.verify_attribution()["ok"]
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["items"] if isinstance(data, dict) else data
    next(r for r in rows if r["id"] == rid)["source"] = {"doc": "user:mallory"}
    path.write_text(json.dumps(data), encoding="utf-8")
    m = Inspeximus(str(path), receipts=True, receipt_key=key)
    ok, problems = m.verify_writes()
    assert not ok, problems
    assert rid in m.verify_attribution()["relabeled"]
    assert m.provenance(id=rid)["integrity"]["attribution_matches_receipt"] is False
