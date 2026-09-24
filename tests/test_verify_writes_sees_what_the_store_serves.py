"""verify_writes reports four store states it used to pass (verifier review from session E, 2026-09-24).

1. A copy of a record, with the record's id and a different value, inserted ahead of the original.
   current() served the copy while verify_writes compared the original and returned (True, []).
2. A record deleted out of band and covered by a tombstone signed with a second key: clean unpinned.
4. A record erased with forget_subject() and put back from a copy of the store file: its receipt still
   matched, so it verified.
11. A chain written by two handles with two keys: the docstring of receipt_key_for() said verify_writes
   reports it, and it did not.

Plus the message for a signed chain on a Python with no Ed25519 backend: it said "unsigned", which is
false. It now says the signature cannot be verified here, and it is still a problem.

Every attack has a control: the untouched store verifies, and the edit is on disk before the check.
"""
from __future__ import annotations

import copy
import json
import os
import shutil

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import inspeximus.core as core  # noqa: E402
from inspeximus import Inspeximus, new_receipt_keypair  # noqa: E402
from inspeximus.core import _canon, _sha256_hex  # noqa: E402

SK, PK = new_receipt_keypair()
OTHER_SK, OTHER_PK = new_receipt_keypair()


@pytest.fixture(autouse=True)
def _json_store(monkeypatch, tmp_path):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keys"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")


def _load(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _save(p, v):
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(v, fh)


def _open(p, key=SK):
    return Inspeximus(p, receipts=True, receipt_key=key)


def _clean(p):
    ok, problems = _open(p).verify_writes(expected_pubkey=PK)
    assert ok, "CONTROL: the untouched store must verify: %r" % problems


def test_a_copy_with_the_same_id_inserted_ahead_is_reported(tmp_path):
    p = str(tmp_path / "s.json")
    m = _open(p)
    m.remember("region is ohio", key="r", object="ohio")
    m.remember("unrelated", key="u", object="x")
    _clean(p)
    rows = _load(p)
    orig = next(r for r in rows if r.get("key") == "r")
    fake = copy.deepcopy(orig)
    fake["text"], fake["object"] = "region is oslo", "oslo"
    rows.insert(rows.index(orig), fake)
    _save(p, rows)
    m2 = _open(p)
    assert (m2.current("r") or {}).get("text") == "region is oslo", "CONTROL: the store serves the copy"
    ok, problems = m2.verify_writes(expected_pubkey=PK)
    assert not ok
    assert any(x.startswith(f"memory {orig['id']}: 2 records share this id") for x in problems), problems
    assert not _open(p).verify_writes()[0], "unpinned as well"


def test_an_erased_record_put_back_from_a_copy_is_reported(tmp_path):
    p = str(tmp_path / "s.json")
    m = _open(p)
    m.remember("keep", key="k", object="1")
    gone = m.remember("personal data of X", source={"doc": "subj:x"})
    shutil.copy(p, p + ".bak")
    m.forget_subject("subj:x", request_id="dsar")
    _clean(p)
    shutil.copy(p + ".bak", p)                  # the store file only; receipts and tombstones stay
    assert any(r["id"] == gone for r in _load(p)), "CONTROL: the erased record is on disk again"
    ok, problems = _open(p).verify_writes(expected_pubkey=PK)
    assert not ok
    assert any(x.startswith(f"memory {gone}: a deletion tombstone says it was erased") for x in problems), \
        problems


def _tombstone_signed_by_other_key(p, memory_id):
    tp = p + ".tombstones.json"
    toms = _load(tp) if os.path.exists(tp) else []
    t = {"seq": len(toms), "memory_id": memory_id, "ts": 1.0, "request_id": None,
         "prev": toms[-1]["hash"] if toms else core._GENESIS}
    t["hash"] = _sha256_hex(_canon(Inspeximus._tombstone_core(t)))
    t["pubkey"] = OTHER_PK
    t["sig"] = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(OTHER_SK)).sign(
        bytes.fromhex(t["hash"])).hex()
    toms.append(t)
    _save(tp, toms)


def test_a_deletion_covered_by_another_keys_tombstone_is_reported_unpinned(tmp_path):
    p = str(tmp_path / "s.json")
    m = _open(p)
    m.remember("keep me", key="k", object="1")
    gone = m.remember("delete me out of band", key="g", object="2")
    _clean(p)
    _save(p, [r for r in _load(p) if r["id"] != gone])
    _tombstone_signed_by_other_key(p, gone)
    ok, problems = _open(p).verify_writes()
    assert not ok
    assert any("the chain is signed by 2 different keys" in x for x in problems), problems
    # Pinned, the entry is named by the check that already existed, and the new line stays quiet.
    ok, problems = _open(p).verify_writes(expected_pubkey=PK)
    assert any("signed by an unexpected key" in x for x in problems), problems
    assert not any("different keys" in x for x in problems), problems


def test_a_chain_written_with_two_keys_is_reported(tmp_path):
    p = str(tmp_path / "s.json")
    _open(p).remember("a", key="a", object="1")
    _open(p, key=OTHER_SK).remember("b", key="b", object="2")
    keys = {r.get("pubkey") for r in _load(p + ".receipts.json") if r.get("sig")}
    assert keys == {PK, OTHER_PK}, "CONTROL: the chain carries both keys"
    ok, problems = _open(p, key=OTHER_SK).verify_writes()
    assert not ok
    assert any("the chain is signed by 2 different keys" in x for x in problems), problems


def test_CONTROL_one_key_throughout_is_not_reported(tmp_path):
    p = str(tmp_path / "s.json")
    m = _open(p)
    m.remember("a", key="a", object="1")
    m.remember("x", source={"doc": "subj:x"})
    m.forget_subject("subj:x", request_id="r1")
    ok, problems = _open(p).verify_writes()
    assert ok, problems


def test_without_a_backend_a_signed_chain_says_cannot_verify_not_unsigned(tmp_path, monkeypatch):
    p = str(tmp_path / "s.json")
    m = _open(p)
    m.remember("a", key="a", object="1")
    m.remember("x", source={"doc": "subj:x"})
    m.forget_subject("subj:x", request_id="r1")
    _clean(p)
    monkeypatch.setattr(core, "_HAVE_ED", False)
    ok, problems = Inspeximus(p, receipts=True).verify_writes(expected_pubkey=PK)
    assert not ok, "nothing was checked, so it must not pass"
    assert any(x.startswith("receipt 0: signed, but the signature cannot be verified here") for x in problems)
    assert any(x.startswith("tombstone 0: signed, but the signature cannot be verified here")
               for x in problems), problems
    assert not any("unsigned" in x for x in problems), problems
