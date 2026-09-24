"""Write receipts: tests written from the 2026-09-24 mutation run of the evidence modules.

Each test exists because a named mutant of the write-receipt code in `inspeximus/core.py`
(`_write_commit`, `_append_receipt`, `_emit_write_receipt`, `enable_receipts`, `verify_writes`, ...)
survived the whole suite (audits/2026-09-24/mutation-evidence.md). The mutation is named in each
docstring so the test cannot be "simplified" back into one that passes either way. Every test was
checked in both directions with `python audits/2026-09-24/mutate_evidence.py kill`: green on the
original source, red on the mutant.
"""
from __future__ import annotations

import os

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.core import _canon, _sha256_hex


def _store(tmp_path, **kw):
    return Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=new_receipt_keypair()[0], **kw)


def _commit(m, rid):
    return [r for r in m._receipts if r["memory_id"] == rid][-1]["commit"]


# -- what a receipt commits to -----------------------------------------------------------------------
def test_the_nonce_is_what_makes_an_erased_records_receipt_unguessable(tmp_path):
    """SURVIVORS core.py:3586 `... = _n` -> `... = None`, and each `"nonce"` key upper-cased
    (core:3586:12:3d0c1533, core:3586:17:0ac304b6, core:3586:33:d9b279d7, core:3586:49:4a2137a9).

    The guessing test checks the receipt against sha256({text, key}) -- the pre-2.40 formula. A
    preimage of {text, key, nonce: null} fails that comparison too, so a mutant that committed a
    null nonce kept the suite green while every receipt went back to being a dictionary-attackable
    fingerprint: the attacker only has to add `"nonce": null` to the guess. The property is that the
    commitment is over the record's OWN random nonce, which erasure removes."""
    m = _store(tmp_path)
    rid = m.remember("Alice phone is +100", key="alice::phone", source={"doc": "crm/alice"})
    rec = next(r for r in m.items if r["id"] == rid)
    nonce = rec["nonce"]
    assert isinstance(nonce, str) and len(nonce) >= 32
    commit = _commit(m, rid)
    text, key = "Alice phone is +100", "alice::phone"
    assert commit["immutable_sha256"] == _sha256_hex(_canon({"text": text, "key": key, "nonce": nonce}))
    assert commit["content_sha256"] == _sha256_hex(_canon({"text": text, "key": key, "mtype": rec.get("mtype"),
                                                           "nonce": nonce}))
    for guess_nonce in (None, "", "0" * len(nonce)):
        guess = _sha256_hex(_canon({"text": text, "key": key, "nonce": guess_nonce}))
        assert commit["immutable_sha256"] != guess, guess_nonce
    assert m.verify_writes()[0] is True


GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "golden_store_3.9.1")


@pytest.fixture()
def golden(tmp_path):
    """The store 3.9.1 wrote (audits/2026-09-24/make_golden_store.py), copied so opening it can
    never touch the committed files."""
    import json
    import shutil
    dst = tmp_path / "golden"
    shutil.copytree(GOLDEN, dst)
    meta = json.load(open(dst / "golden.json", encoding="utf-8"))
    return Inspeximus(str(dst / "mem.json"), receipts=True), meta


def test_a_store_written_by_3_9_1_still_verifies(golden):
    """SURVIVORS core.py:3628-3659 -- every key inside `status_sha256` and `time_sha256`
    (`"serving"`, `"confirmed_by"`, `"valid_from"`, `"valid_from_source"`), the `"active"` default of
    `born_status` -- and core.py:3586's nonce keys.

    Producer and verifier share `_write_commit`, so renaming a field inside a commitment is invisible
    to any test that writes a store and verifies it in the same run. What it breaks is every receipt
    already on disk: the recomputed hash no longer matches, and `verify_writes()` raises a tamper
    alarm on every honest store after an upgrade -- the outcome the comments in `_write_commit` go
    out of their way to prevent. This store was written once by 3.9.1: a backfilled record, a
    correction that retires it, a declared `valid_from`, a confirmation and a slash (both
    amendments, with reasons), and an erasure."""
    from inspeximus.core import verify_erasure_certificate
    m, meta = golden
    assert meta["inspeximus_version"] == "3.9.1"
    assert (len(m.items), len(m._receipts), len(m._tombstones)) == (meta["records"], meta["receipts"],
                                                                    meta["tombstones"])
    kinds = {tuple(r.get("amends") or ()) for r in m._receipts}
    assert ("mtype",) in kinds and ("status_sha256",) in kinds and any(r.get("backfill") for r in m._receipts)
    ok, problems = m.verify_writes(meta["receipt_pubkey"])
    assert ok, problems
    cert = m.erasure_certificate(expected_pubkey=meta["receipt_pubkey"])
    res = verify_erasure_certificate(cert, store_items=list(m.items), expected_pubkey=meta["receipt_pubkey"])
    assert res["valid"] is True, res["problems"]


# -- a receipt moved after a peer's (the rechain path) -----------------------------------------------
def test_a_rechained_amendment_keeps_what_it_amends_why_and_when(tmp_path, monkeypatch):
    """SURVIVORS core.py:3742-3745 -- the keys `_append_receipt` copies onto a rechained receipt
    (`"ts"` -> `"XXtsXX"` / `"TS"`, `"amends"` / `"amend_reason"` renamed, `r[k] = old[k]` ->
    `r[k] = None`).

    The one rechain test moves an ORDINARY write, which carries neither `amends` nor `amend_reason`,
    and never compares the moved receipt's time with the original's. Here the receipt that has to
    move is a confirmation -- an amendment of `status_sha256` with a stated reason. Dropped on the
    move, the chain no longer forgives the provisional record's first receipt, and a fresh handle
    reports an honest store as edited after write."""
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "rows")
    p = str(tmp_path / "s.json")
    a = Inspeximus(path=p, receipts=True)
    a.remember("a one", key="a1", object="1")
    prov = a.remember("the rota is in PagerDuty", key="rota", object="pd", provisional=True)
    b = Inspeximus(path=p, receipts=True)
    monkeypatch.setattr(Inspeximus, "_reconcile_receipts_with_disk", lambda self: 0)
    b.confirm(prov, by="ops-lead")                       # b's chain: [a1, prov, amend]
    amend = dict(b._receipts[-1])
    assert amend.get("amends") == ["status_sha256"], "the fixture did not produce an amendment"
    a.remember("a two", key="a2", object="2")            # a's chain [a1, prov, a2] lands on disk
    monkeypatch.undo()
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "rows")
    b.refresh()                                          # b adopts disk and moves its amendment after a2
    moved = b._receipts[-1]
    assert moved.get("rechained_from") == amend["hash"]
    assert moved["amends"] == amend["amends"]
    assert moved["amend_reason"] == amend["amend_reason"]
    assert moved["ts"] == amend["ts"] and moved["commit"] == amend["commit"]
    b.remember("b two", key="b2", object="2")            # the next emit persists the merged chain
    ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
    assert ok, problems
