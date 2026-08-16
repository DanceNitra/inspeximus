"""The operator-adversarial layer, attacked as an operator would attack it.

An adversarial pass on the 2.10.6 candidate found eleven issues. Two were critical and both defeated
the single guarantee in this product that does not come from the party being audited: one by `cp`,
one by 400 unauthenticated HTTP calls. This module pins the fixes, and each test names the shape of
the attack rather than the line that was wrong, because the line will move.

WHY THE ROUND EXISTED AT ALL. Versions 2.10.1 through 2.10.5 shipped on the same day, each because
the adversarial pass ran AFTER publishing and found a hole in the previous one. The pass now runs
against the unreleased candidate. These findings would otherwise have been 2.10.7 through 2.10.11.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.audit_bundle import (_bundle_hash, _derived_store_id, build_bundle,
                                     load_store_items, load_store_receipts, verify_bundle)
from inspeximus.core import new_ed25519_keypair
from conftest import fork_of
from inspeximus.witness_pool import Witness

SK, PK = new_ed25519_keypair()
EVIL_SK, EVIL_PK = new_ed25519_keypair()


def _store(d, sub, n, policy="two"):
    pp = os.path.join(d, sub)
    os.makedirs(pp, exist_ok=True)
    ix = Inspeximus(path=os.path.join(pp, "m.json"), receipts=True, receipt_key=SK)
    ix.remember(f"deployment needs {policy} approvers", key="pol", object=policy)
    for i in range(n - 1):
        ix.remember(f"record {i}", key=f"k{i}", object=str(i))
    ix.flush()
    return ix


def _fork(ix, dest, n, prefix="rewritten"):
    """A real fork of `ix`: same genesis receipt, divergent chain -- see conftest.fork_of.

    Three modules built their "fork" as a second store from scratch, and after the witness moved to
    a derived identity that stopped being a fork of anything. The shared helper is the fix for the
    class; this wrapper is only the local shape.
    """
    return fork_of(ix, dest, [(f"{prefix} {i}", f"f{i}", str(i)) for i in range(n - 1)],
                   receipt_key=SK)


# ─────────────────────────────────────────── F1: identity from the log, not the filename
def test_copying_a_rolled_back_store_does_not_buy_a_fresh_witness():
    """The witness keyed its whole fork memory on the store's FILE PATH -- a value the operator
    chooses. Roll back, copy elsewhere, and every witness co-signed it as a first contact while the
    verdict printed `operator-adversarial`."""
    d = tempfile.mkdtemp()
    w = Witness(state_path=os.path.join(d, "w.json"))
    ix = _store(d, "A", 4)
    build_bundle(ix, witnesses=[w])                       # no store_id: the derived one is used

    # roll the chain back IN PLACE, keeping genesis, then copy the whole store elsewhere
    p = str(ix.path)
    rp = p + ".receipts.json"
    rec = json.load(open(rp, encoding="utf-8"))
    rows = rec if isinstance(rec, list) else rec["receipts"]
    keep = {r["memory_id"] for r in rows[:2]}
    del rows[2:]
    json.dump(rec, open(rp, "w", encoding="utf-8"))
    json.dump([r for r in json.load(open(p, encoding="utf-8")) if r["id"] in keep],
              open(p, "w", encoding="utf-8"))

    B = os.path.join(d, "B")
    shutil.copytree(os.path.join(d, "A"), B)
    moved = Inspeximus(path=os.path.join(B, "m.json"), receipts=True, receipt_key=SK)
    assert _derived_store_id(moved) == _derived_store_id(Inspeximus(path=p, receipts=True))

    b = build_bundle(moved, witnesses=[w])
    assert (b["anchor"].get("cosignatures") or []) == []
    assert b["anchor"].get("witness_refusals")


def test_control_a_genuinely_new_store_is_a_new_identity():
    """The must-not-brick control: two unrelated stores must not share an identity, or the fix has
    made every store look like a fork of every other."""
    d = tempfile.mkdtemp()
    assert _derived_store_id(_store(d, "x", 2)) != _derived_store_id(_store(d, "y", 2))


# ─────────────────────────────────────────── F2: the refusal log, and the extended fork
def test_an_extended_fork_is_reported_even_though_it_is_co_signed():
    """Refusing the fork is not enough on its own: the operator extends the forked chain and the
    rollback guard, which compares heights and tips, has nothing left to object to.

    THE FIRST VERSION OF THIS FIX BLOCKED THE CO-SIGNATURE, and that was wrong -- measured in the
    next round, one unauthenticated POST with a self-consistent conflicting anchor then permanently
    disabled witnessing for any store whose id you knew. Poison is ADVISORY now: a co-signature is a
    FACT ("I saw this head") and the judgement lives in the attestation, which is where the auditor
    reads it and where verify_bundle acts on it. Blocking added a denial channel and bought nothing
    the auditor did not already get.
    """
    d = tempfile.mkdtemp()
    w = Witness(state_path=os.path.join(d, "w.json"))
    a = _store(d, "a", 2)
    wid = build_bundle(a, witnesses=[w])["store_id_derived"]
    forked = _fork(a, os.path.join(d, "b"), 2)
    build_bundle(forked, witnesses=[w])                     # same height, different tip -> refused
    forked.remember("and then it grew", key="g1", object="1")
    forked.remember("and grew again", key="g2", object="2")
    forked.flush()
    b = build_bundle(forked, witnesses=[w])                 # the fork EXTENDED past the real head
    assert b["store_id_derived"] == wid, "the fixture did not build a fork of the witnessed store"
    assert b["anchor"].get("cosignatures"), "advisory poison must still let the head be co-signed"

    # The extended fork IS co-signed -- and the attestation says the witness refused this store.
    att = w.attest(b["store_id_derived"])
    assert att["poisoned"] is True and att["refusals"]
    out = verify_bundle(b, witnesses=[w.public], attestations=[att])
    assert not out["ok"] and any("REFUSED" in x for x in out["problems"]), out["problems"]


def test_flooding_the_refusal_log_cannot_evict_the_fork_evidence():
    """One global FIFO on a caller-chosen `store_id` is floodable, and 200 invented ids pushed the
    real refusal off the front -- after which the witness's own signed attestation reported none."""
    d = tempfile.mkdtemp()
    w = Witness(state_path=os.path.join(d, "w.json"))
    a = _store(d, "a", 2)
    wid = build_bundle(a, witnesses=[w])["store_id_derived"]
    build_bundle(_fork(a, os.path.join(d, "b"), 2), witnesses=[w])       # the evidence to protect

    # TWO POSTS PER INVENTED ID, because a refusal is only recorded for a store the witness has
    # actually seen -- so the flood has to establish each id before it can conflict with it. That is
    # a 2x cost to the attacker and no defence at all: 600 ids is 1200 unauthenticated calls.
    est, con = a.anchor(), _fork(a, os.path.join(d, "c"), 2, "other").anchor()
    assert est["writes_tip"] != con["writes_tip"] and est["n_writes"] == con["n_writes"]
    for i in range(600):
        for anchor in (est, con):
            try:
                w.cosign(f"junk-{i}", anchor)
            except Exception:
                pass
    assert len({r["store_id"] for r in w.refusals()}) > 1, "the flood recorded no refusals at all"

    # The victim is refused FIRST, so under oldest-first eviction it went FIRST -- the opposite of
    # what that code's own comment claimed. A poisoned store is never evicted now, and the rest go
    # newest-first so a flood pushes out its own entries.
    assert w.attest(wid)["refusals"], "the fork evidence was evicted by a flood"
    assert wid in w.poisoned()


def test_clearing_poison_needs_a_stated_reason():
    """A poisoned store is a witness saying it saw two histories. The only honest way past that is a
    human deciding which is real and saying why -- so there is no defaulted escape hatch."""
    d = tempfile.mkdtemp()
    w = Witness(state_path=os.path.join(d, "w.json"))
    build_bundle(_store(d, "a", 2), witnesses=[w], store_id="prod")
    build_bundle(_store(d, "b", 2, "ONE"), witnesses=[w], store_id="prod")
    with pytest.raises(ValueError, match="stated reason"):
        w.clear_poison("prod", "")
    w.clear_poison("prod", "reviewed the two histories; the shorter one was a restore from backup")
    assert "prod" not in w.poisoned()


# ─────────────────────────────────────────── F3: verified, not counted
def test_a_chain_re_signed_with_a_foreign_key_fails_when_pinned():
    """`require_signed=True` counted `sig` keys. Rewrite a record, re-sign with a key you minted, and
    the auditor's offline verdict was PASS."""
    d = tempfile.mkdtemp()
    honest = _store(d, "h", 2)
    forged = Inspeximus(path=os.path.join(d, "f.json"), receipts=True, receipt_key=EVIL_SK)
    forged.remember("deployment needs ONE approver", key="pol", object="one")
    forged.flush()
    out = verify_bundle(build_bundle(forged), require_signed=True, expected_pubkey=PK)
    assert not out["ok"] and any("NOT the one pinned" in x for x in out["problems"]), out["problems"]
    assert verify_bundle(build_bundle(honest), require_signed=True, expected_pubkey=PK)["ok"]


def test_a_garbage_signature_does_not_verify():
    d = tempfile.mkdtemp()
    b = build_bundle(_store(d, "h", 2))
    for e in b["write_chain"]:
        e["sig"] = "deadbeef"
    b.pop("bundle_hash", None)
    b["bundle_hash"] = _bundle_hash(b)
    out = verify_bundle(b, require_signed=True, expected_pubkey=PK)
    assert not out["ok"]


def test_unpinned_reads_as_present_but_unverified():
    """Verifying a signature against the key carried NEXT TO IT proves only that whoever wrote the
    bundle owned a keypair. With no out-of-band key the honest report is not "signed"."""
    d = tempfile.mkdtemp()
    out = verify_bundle(build_bundle(_store(d, "h", 2)))
    assert any("PRESENT BUT UNVERIFIED" in x for x in out["limits"]), out["limits"]


# ─────────────────────────────────────────── F4/F5: what the witness actually saw
def test_rewrite_then_grow_is_a_fork_not_staleness():
    """A taller bundle is not automatically honest growth, and the bundle carries the chain to prove
    it: if the witness signed tip T at height n, entry n-1 must hash to T."""
    d = tempfile.mkdtemp()
    w = Witness(state_path=os.path.join(d, "w.json"))
    a = _store(d, "a", 3)
    b0 = build_bundle(a, witnesses=[w])
    att = w.attest(b0["store_id_derived"])
    # The forged store must share a genesis with the witnessed one, or this measures "a different
    # store" rather than a fork. Roll the real one back on disk and grow it instead.
    p = str(a.path)
    rp = p + ".receipts.json"
    rec = json.load(open(rp, encoding="utf-8"))
    rows = rec if isinstance(rec, list) else rec["receipts"]
    keep = {r["memory_id"] for r in rows[:1]}
    del rows[1:]
    json.dump(rec, open(rp, "w", encoding="utf-8"))
    json.dump([r for r in json.load(open(p, encoding="utf-8")) if r["id"] in keep],
              open(p, "w", encoding="utf-8"))
    forged = Inspeximus(path=p, receipts=True, receipt_key=SK)
    for i in range(4):
        forged.remember(f"rewritten {i}", key=f"z{i}", object=str(i))
    forged.flush()
    out = verify_bundle(build_bundle(forged), attestations=[att])
    assert not out["ok"] and any("FORK" in x for x in out["problems"]), out["problems"]


def test_control_honest_growth_past_the_witness_stays_a_note():
    d = tempfile.mkdtemp()
    w = Witness(state_path=os.path.join(d, "w.json"))
    ix = _store(d, "a", 3)
    b0 = build_bundle(ix, witnesses=[w])
    att = w.attest(b0["store_id_derived"])
    ix.remember("an honest later write", key="z", object="9")
    ix.flush()
    out = verify_bundle(build_bundle(ix), attestations=[att])
    assert out["ok"], out["problems"]


def test_a_stranger_cannot_vouch():
    d = tempfile.mkdtemp()
    w = Witness(state_path=os.path.join(d, "w.json"))
    stranger = Witness(state_path=os.path.join(d, "x.json"))
    ix = _store(d, "a", 3)
    b = build_bundle(ix, witnesses=[w], store_id="prod")
    stranger.bootstrap("prod")
    stranger.cosign("prod", ix.anchor())
    out = verify_bundle(b, witnesses=[w.public], attestations=[stranger.attest("prod")])
    assert any("not on the witness allowlist" in x for x in out["problems"]), out["problems"]


def test_a_witness_that_never_answered_is_reported():
    """An auditor who asked three and was handed two has been told nothing about the third, and that
    absence was invisible."""
    d = tempfile.mkdtemp()
    w, silent = Witness(state_path=os.path.join(d, "w.json")), Witness(state_path=os.path.join(d, "s.json"))
    b = build_bundle(_store(d, "a", 3), witnesses=[w], store_id="prod")
    out = verify_bundle(b, witnesses=[w.public, silent.public], attestations=[w.attest("prod")])
    assert any("produced no attestation" in x for x in out["limits"]), out["limits"]


# ─────────────────────────────────────────── F6/F7: the witness's own memory
def test_a_crafted_state_file_cannot_make_a_witness_co_sign_a_rollback():
    d = tempfile.mkdtemp()
    sp = os.path.join(d, "w.json")
    w = Witness(state_path=sp)
    w.bootstrap("s")
    w.cosign("s", _store(d, "a", 3).anchor())
    raw = json.load(open(sp, encoding="utf-8"))
    raw["heads"]["s"]["n_writes"] = 1
    json.dump(raw, open(sp, "w", encoding="utf-8"))
    with pytest.raises(ValueError, match="does not authenticate"):
        Witness(w._secret, state_path=sp)


def test_control_a_pre_2_10_6_state_file_still_starts_the_witness():
    """Refusing an unMACed file would take every existing witness offline on upgrade, and a witness
    that will not start co-signs nothing -- worse than the gap it was closing."""
    d = tempfile.mkdtemp()
    sp = os.path.join(d, "w.json")
    json.dump({"s": {"n_writes": 1, "writes_tip": "cc" * 32}}, open(sp, "w", encoding="utf-8"))
    assert Witness(state_path=sp).last_head("s")


def test_a_stale_second_witness_cannot_clobber_the_fork_memory():
    """Building a second Witness over a running server's state file was the ONLY route to attest(),
    and it silently destroyed the first's memory for an entire store."""
    d = tempfile.mkdtemp()
    sp = os.path.join(d, "w.json")
    w1 = Witness(state_path=sp)
    w1.bootstrap("A")
    w1.cosign("A", _store(d, "a", 3).anchor())
    w2 = Witness(w1._secret, state_path=sp)
    w2.bootstrap("C")
    w2.cosign("C", _store(d, "c", 4).anchor())
    with pytest.raises(RuntimeError, match="changed since this Witness loaded it"):
        w1.bootstrap("B")
        w1.cosign("B", _store(d, "b", 2).anchor())
    assert sorted(json.load(open(sp, encoding="utf-8"))["heads"]) == ["A", "C"]


# ─────────────────────────────────────────── F8/F9: what the bundle may say
def test_a_tenant_bound_store_refuses_to_export():
    """The chains are store-wide while `n_records` is tenant-scoped, so the artifact carried other
    tenants' memory ids and GDPR request_ids while calling itself content-free."""
    d = tempfile.mkdtemp()
    s = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    s.for_tenant("acme").remember("acme", key="a")
    s.for_tenant("globex").remember("globex", key="g")
    s.flush()
    with pytest.raises(ValueError, match="chains are STORE-WIDE"):
        build_bundle(s.for_tenant("acme"))
    b = build_bundle(s.for_tenant("acme"), cross_tenant_chain=True)
    assert b["cross_tenant_chain"] is True
    assert build_bundle(s)["cross_tenant_chain"] is False


def test_an_incomplete_baseline_is_not_an_accusation_of_insertion():
    """A store that turned receipts on part-way was told, in text identical to a genuine plant, that
    its records were inserted out of band -- which makes the genuine signal unreadable."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    Inspeximus(path=p).remember("early, receipts off", key="e1")
    later = Inspeximus(path=p, receipts=True)
    later.remember("later", key="e2")
    later.flush()
    out = verify_bundle(build_bundle(later), store_items=load_store_items(p),
                        store_receipts=load_store_receipts(p))
    msg = [x for x in out["problems"] if "record(s):" in x]
    assert msg and "baseline was ALREADY incomplete" in msg[0], out["problems"]
