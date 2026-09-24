"""Write receipts: tests written from the 2026-09-24 mutation run of the evidence modules.

Each test exists because a named mutant of the write-receipt code in `inspeximus/core.py`
(`_write_commit`, `_append_receipt`, `_emit_write_receipt`, `enable_receipts`, `verify_writes`, ...)
survived the whole suite (audits/2026-09-24/mutation-evidence.md). The mutation is named in each
docstring so the test cannot be "simplified" back into one that passes either way. Every test was
checked in both directions with `python audits/2026-09-24/mutate_evidence.py kill`: green on the
original source, red on the mutant.
"""
from __future__ import annotations

import json
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


def test_a_receipt_records_when_its_record_was_written(tmp_path):
    """SURVIVORS core.py:3777 `"ts": rec.get("ts")` -> `rec.get(None)` / key renamed
    (core:3777:19:1caf6dd6, core:3777:27:e0789e69, core:3777:27:fc38149b).

    `ts` is inside the receipt's hash, so a receipt with `ts: None` is a perfectly consistent link --
    and a chain whose links say nothing about WHEN each write happened. No test compared a receipt's
    time with its record's."""
    m = _store(tmp_path)
    ids = [m.remember(f"fact {i}", key=f"k{i}", object=str(i)) for i in range(3)]
    by_id = {r["id"]: r for r in m.items}
    for rid in ids:
        rc = [r for r in m._receipts if r["memory_id"] == rid][0]
        assert isinstance(rc["ts"], float) and rc["ts"] == by_id[rid]["ts"]


# -- enable_receipts: covering what was written before the chain -------------------------------------
def _unreceipted(tmp_path, n=8):
    p = str(tmp_path / "s.json")
    ix = Inspeximus(path=p)
    for i in range(n):
        ix.remember(f"fact {i}: the deadline is day {i}", key=f"k{i}", object=str(i))
    return p, ix


def test_the_backfill_genesis_root_commits_to_the_records_it_covers(tmp_path):
    """SURVIVORS core.py:4111 `_canon(c)` -> `_canon(None)` (core:4111:29:466f261b) and the
    backfill marker's fields (core.py:4112-4114: `now` -> None, the keys `n` / `at` / `reason`
    renamed, the "unstated" default and the 200-character cut; core.py:4046 the `reason=""`
    parameter default).

    `genesis_root` is what the backfill offers an auditor: an RFC 6962 root over the commitments
    it vouches for. The tests check that it exists and is 64 hex characters, which a root over
    eight nulls also is. It is re-derived here from the backfill receipts' own commits."""
    from inspeximus.merkle import root as merkle_root
    _p, ix = _unreceipted(tmp_path)
    before = __import__("time").time()
    out = ix.enable_receipts(reason="  the store predates its chain  ")
    backfill = [rc for rc in ix._receipts if rc.get("backfill")]
    assert len(backfill) == out["anchored_records"] == 8
    assert out["genesis_root"] == merkle_root([_canon(rc["commit"]) for rc in backfill]).hex()
    marker = backfill[0]["backfill"]
    assert all(rc["backfill"] == marker for rc in backfill)
    assert marker["genesis_root"] == out["genesis_root"] and marker["n"] == 8
    assert before <= marker["at"] <= __import__("time").time()
    assert marker["reason"] == "the store predates its chain"
    for i, kw in enumerate(({}, {"reason": "   "})):             # the default, and a blank one
        _p2, ix2 = _unreceipted(tmp_path / f"unstated{i}", n=2)
        ix2.enable_receipts(**kw)
        assert ix2._receipts[0]["backfill"]["reason"] == "unstated", kw
    _p3, ix3 = _unreceipted(tmp_path / "third")
    ix3.enable_receipts(reason="x" * 300)
    assert ix3._receipts[0]["backfill"]["reason"] == "x" * 200


def test_the_backfill_follows_write_order_and_keeps_each_records_time(tmp_path):
    """SURVIVORS core.py:4108 (the sort key: `r.get("ts") or 0` -> `and 0` / `or 1` / key renamed)
    and core.py:4116 (`"ts": rec.get("ts")` -> None / key renamed). A backfill receipt vouches for a
    record AS IT STANDS, but the chain still reads in the order the records were written, and each
    receipt says when its record was. Ids are random, so eight records sort differently by id than
    by time with probability 1 - 1/8!."""
    _p, ix = _unreceipted(tmp_path, n=8)
    ix.enable_receipts()
    by_id = {r["id"]: r for r in ix.items}
    ts = [by_id[rc["memory_id"]]["ts"] for rc in ix._receipts]
    assert ts == sorted(ts), "backfill receipts are not in write order"
    assert [rc["ts"] for rc in ix._receipts] == ts


def test_enable_receipts_says_whether_it_signed(tmp_path):
    """SURVIVORS core.py:4103 (`self._receipt_signer is not None` -> `is None`, `and _HAVE_ED` ->
    `or _HAVE_ED`). Only the signed case was asserted, so a result that reported `signed: True`
    for a chain nobody signed passed."""
    _p, ix = _unreceipted(tmp_path)
    assert ix.enable_receipts()["signed"] is False
    assert not any(rc.get("sig") for rc in ix._receipts)
    sk, _pk = new_receipt_keypair()
    _p2, ix2 = _unreceipted(tmp_path / "signed")
    assert ix2.enable_receipts(receipt_key=sk)["signed"] is True


def test_a_second_call_reports_nothing_declared(tmp_path):
    """SURVIVORS core.py:4099 (`"retirements_declared": 0` -> `1`, the key renamed): the idempotent
    second call's result was only read for `anchored_records`."""
    _p, ix = _unreceipted(tmp_path, n=3)
    ix.enable_receipts()
    again = ix.enable_receipts()
    assert again["anchored_records"] == 0 and again["retirements_declared"] == 0


def test_enabling_receipts_on_an_empty_store_creates_the_sidecar(tmp_path):
    """SURVIVORS core.py:4105 (`self._receipts_path and not ... .exists()` -> `or`, `not` dropped).
    With nothing to cover, `enable_receipts()` still has to leave the sidecar on disk, or a reopened
    handle cannot tell a store whose chain is empty from one that never had receipts on."""
    p = str(tmp_path / "empty.json")
    ix = Inspeximus(path=p)
    out = ix.enable_receipts()
    assert out["anchored_records"] == 0
    assert os.path.exists(p + ".receipts.json")
    assert json.load(open(p + ".receipts.json", encoding="utf-8")) == []


def test_a_retirement_declared_at_backfill_is_a_committed_amendment(tmp_path):
    """SURVIVORS core.py:4129-4132 -- the declaration receipt's keys (`"ts"`, `"commit"`, `"amends"`,
    `"amend_reason"`) and values. The existing test only asks that the store verifies afterwards,
    and a declaration with no `commit` is checked against nothing, so it verified too. The
    declaration must commit to the record as it stands, amend exactly `status_sha256`, and say why."""
    p = str(tmp_path / "s.json")
    ix = Inspeximus(path=p, receipts=True)
    ix.remember("the deadline is Friday", key="deadline", object="friday")
    born = ix.items[0]["id"]
    Inspeximus(path=p).remember("the deadline is Monday", key="deadline", object="monday")   # receipts off
    ix = Inspeximus(path=p, receipts=True)
    out = ix.enable_receipts()
    assert out["retirements_declared"] == 1
    decl = [rc for rc in ix._receipts if rc["memory_id"] == born and rc.get("amends")]
    assert len(decl) == 1
    d = decl[0]
    rec = next(r for r in ix.items if r["id"] == born)
    assert d["amends"] == ["status_sha256"]
    assert d["amend_reason"] == "retired while receipts were off; declared at backfill"
    assert d["commit"] == ix._write_commit(rec)
    assert d["ts"] == rec["ts"]
    assert Inspeximus(path=p, receipts=True).verify_writes() == (True, [])


def test_an_unsigned_tombstone_appended_to_a_signed_store_fails_verify_writes(tmp_path):
    """SURVIVOR core.py:6549 `list(self._tombstones or ())` -> `list(self._tombstones and ())`
    (core:6549:51:57081ac5).

    Unpinned, `verify_writes()` checks tombstone signatures only where a tombstone has one, so the
    rule "a chain signed in places is not signed" is the ONLY thing that sees an unsigned tombstone
    appended by someone without the key. The mutant dropped tombstones from that rule, and no test
    appended one: the partial-signing tests all appended write receipts. Here a forged, correctly
    chained, unsigned tombstone claims a live record was erased."""
    import time
    from inspeximus.core import _canon, _sha256_hex
    m = _store(tmp_path)
    for i in range(3):
        m.remember(f"fact {i}", key=f"k{i}", object=str(i))
    m.forget(where=lambda r: r.get("key") == "k0", request_id="R1")
    assert m.verify_writes() == (True, [])                                              # the control
    victim = next(r["id"] for r in m.items if r.get("key") == "k1")
    t = {"seq": len(m._tombstones), "memory_id": victim, "ts": time.time(), "request_id": "FORGED",
         "prev": m._tombstones[-1]["hash"]}
    t["hash"] = _sha256_hex(_canon(Inspeximus._tombstone_core(t)))
    m._tombstones.append(t)
    ok, problems = m.verify_writes()
    assert ok is False
    assert any("carry NO signature" in p for p in problems), problems
