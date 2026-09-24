"""Erasure certificate: tests written from the 2026-09-24 mutation run of the evidence modules.

Each test exists because a named mutant of `verify_erasure_certificate` (inspeximus/core.py)
survived the whole suite (audits/2026-09-24/mutation-evidence.md). The branches below were not
merely under-asserted: no test executed them at all, so any edit to them, including one that turns
a refusal into `valid: true`, left the suite green. The mutation is named in each docstring so the
test cannot be "simplified" back into one that passes either way. Every test was checked in both
directions with `python audits/2026-09-24/mutate_evidence.py kill`: green on the original source,
red on the mutant.
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from inspeximus import Inspeximus, erasure_challenge, new_receipt_keypair, new_source_keypair, sign_erasure
from inspeximus.core import _GENESIS, _canon, _sha256_hex, verify_erasure_certificate
from inspeximus.merkle import root as merkle_root


@pytest.fixture()
def signed():
    """A signed store with three erasures under two requests, its certificate and its state."""
    sk, pk = new_receipt_keypair()
    st = Inspeximus(path=None, receipts=True, receipt_key=sk)
    for i in range(5):
        st.remember(f"subject {i} value-{i}", key=f"k{i}", object=f"v{i}")
    st.forget(where=lambda r: r.get("key") == "k0", request_id="DSAR-1")
    st.forget(where=lambda r: r.get("key") == "k1", request_id="DSAR-1")
    st.forget(where=lambda r: r.get("key") == "k2", request_id="DSAR-2")
    cert = st.erasure_certificate()
    items = [dict(r) for r in st.items]
    receipts = [dict(r) for r in st._receipts]
    ok = verify_erasure_certificate(cert, store_items=items, store_receipts=receipts,
                                    expected_pubkey=pk, expected_anchor=copy.deepcopy(cert["anchor"]))
    assert ok["valid"] is True and not ok["problems"], ok["problems"]                  # the control
    return cert, items, receipts, pk


def _store(signed: bool):
    kw = {"path": None, "receipts": True}
    if signed:
        kw["receipt_key"] = new_receipt_keypair()[0]
    st = Inspeximus(**kw)
    for i in range(5):
        st.remember(f"subject {i} value-{i}", key=f"k{i}", object=f"v{i}")
    for i in range(3):
        st.forget(where=lambda r, i=i: r.get("key") == f"k{i}", request_id="DSAR-7")
    return st


def _reanchor(cert):
    """Everything a forger WITHOUT the receipt key can recompute: the anchor's count, tip, Merkle root
    and sth_hash, and the summary fields. This is what leaves the chain check as the only one left."""
    a, toms = cert["anchor"], cert["tombstones"]
    a["n_tombstones"] = len(toms)
    a["tombstones_tip"] = toms[-1]["hash"]
    a["tombstones_root"] = merkle_root([_canon(Inspeximus._chain_core(t, "tombstone")) for t in toms]).hex()
    a["sth_hash"] = _sha256_hex(_canon({k: a[k] for k in ("n_writes", "writes_tip", "n_tombstones",
                                                          "tombstones_tip")}))
    ids = sorted({t["memory_id"] for t in toms})
    cert["erased_memory_ids"], cert["count"] = ids, len(ids)
    return cert


# -- (1) the chain -----------------------------------------------------------------------------------
def test_a_tombstone_edited_in_place_fails_on_the_chain_alone():
    """SURVIVOR core.py:1041 `chain_ok = False` -> `chain_ok = True` (core:1041:23:e24cd658).

    `valid` is computed from `chain_ok`, not from `problems`. Every existing tamper test ALSO broke
    a second check (a signature, the anchor, the summary), so flipping this flag changed nothing
    any test could see. The forgery here needs no key: the tombstone's `memory_id` is rewritten and
    its stored `hash` kept, so the Ed25519 signature over that hash still verifies, and the anchor
    and summary are recomputed to match. The certificate now attests to erasing a record that never
    existed; the hash mismatch is the only thing that says so."""
    st = _store(signed=True)
    items = [dict(r) for r in st.items]
    forged = copy.deepcopy(st.erasure_certificate())
    forged["tombstones"][1]["memory_id"] = "a-record-that-never-existed"
    _reanchor(forged)
    res = verify_erasure_certificate(forged, store_items=items)
    failed = sorted(k for k, v in res["checks"].items() if v is False)
    assert failed == ["chain_intact"], (failed, res["problems"])     # the ONLY check that can see it
    assert res["checks"]["signatures_valid"] is True
    assert res["valid"] is False
    assert any("tombstone 1: hash mismatch" in p for p in res["problems"]), res["problems"]


def test_a_tombstone_removed_from_the_middle_fails_on_the_chain_alone():
    """SURVIVOR core.py:1038 `chain_ok = False` -> `chain_ok = True` (core:1038:23:543714b3).

    The trimmed-certificate test removes the LAST tombstone, which the anchor catches. Removing one
    from the middle of an unsigned chain and recomputing the anchor leaves only the broken `prev`
    link, and with the flag flipped that certificate verified `valid: true` with one erasure fewer
    than the store performed."""
    st = _store(signed=False)
    items = [dict(r) for r in st.items]
    forged = copy.deepcopy(st.erasure_certificate())
    del forged["tombstones"][1]
    _reanchor(forged)
    res = verify_erasure_certificate(forged, store_items=items)
    failed = sorted(k for k, v in res["checks"].items() if v is False and k != "signed")
    assert failed == ["chain_intact"], (failed, res["problems"])
    assert res["valid"] is False
    assert any("tombstone 1: broken chain link" in p for p in res["problems"]), res["problems"]


# -- (2) signatures ----------------------------------------------------------------------------------
def test_a_tombstone_carrying_another_tombstones_signature_does_not_verify(signed):
    """SURVIVORS core.py:1055 `sigs_ok = False` -> `sigs_ok = True` and -> `None`.

    No test ever put a signature on a tombstone that fails to verify, so the branch that answers
    "invalid signature" never ran. `valid` is computed from `sigs_ok`, not from `problems`: with the
    flag left True the certificate reported `valid: true` while its own problem list said
    "tombstone 0: invalid signature". A signature that is well-formed hex, made by the right key,
    over a DIFFERENT tombstone is the realistic forgery: it is what a copy-paste produces."""
    cert, items, _receipts, pk = signed
    forged = copy.deepcopy(cert)
    forged["tombstones"][0]["sig"] = forged["tombstones"][1]["sig"]
    for kw in ({}, {"expected_pubkey": pk}):
        res = verify_erasure_certificate(forged, store_items=items, **kw)
        assert res["checks"]["chain_intact"] is True        # the hashes still chain: only the sig lies
        assert res["checks"]["signatures_valid"] is False, kw
        assert res["valid"] is False, kw
        assert any("tombstone 0: invalid signature" in p for p in res["problems"]), res["problems"]


# -- (7) the witnessed anchor ------------------------------------------------------------------------
@pytest.mark.parametrize("witnessed", [
    {"n_tombstones": "3"},                              # a count that is not an integer
    {"n_tombstones": None},
    {"n_tombstones": 3, "tombstones_tip": None},        # a count with nothing to compare it to
    {},
], ids=["count-is-a-string", "count-is-null", "no-tip", "empty"])
def test_a_witnessed_anchor_that_pins_nothing_is_not_a_witness(signed, witnessed):
    """SURVIVORS core.py:1131 `ok_w = False` -> `True` / `None`, and core.py:1129
    `not isinstance(w_n, int) or w_tip is None` -> `... and ...`.

    The only test that passed `expected_anchor` passed a well-formed one. Handed an anchor that
    names no position and no tip, the mutants reported `anchor_witnessed: True` -- a certificate
    "pinned" to nothing read as operator-adversarially checked."""
    cert, items, _receipts, _pk = signed
    w = dict(witnessed)
    if "tombstones_tip" not in w and w:
        w["tombstones_tip"] = cert["anchor"]["tombstones_tip"]
    res = verify_erasure_certificate(cert, store_items=items, expected_anchor=w)
    assert res["checks"]["anchor_witnessed"] is False, res["problems"]
    assert res["valid"] is False
    assert any("nothing to pin to" in p for p in res["problems"]), res["problems"]


@pytest.mark.parametrize("n", [1, 2, 3])
def test_a_chain_rewritten_at_the_witnessed_position_fails(signed, n):
    """SURVIVORS core.py:1136-1138 (`!= w_tip` -> `== w_tip`, `w_n > 0` -> `w_n > 1`, `w_n - 1` ->
    `w_n + 1`, `ok_w = False` -> `True`, ...). The trimmed-certificate test covers a chain SHORTER
    than the witness saw; none covered a chain of the right length whose tombstone at the witnessed
    position is a different one. Position 1 is included on purpose: `w_n > 1` skips exactly it."""
    cert, items, _receipts, _pk = signed
    toms = cert["tombstones"]
    other = toms[n % len(toms)]["hash"]                             # some tombstone, not number n-1
    witnessed = {"n_tombstones": n, "tombstones_tip": other}
    res = verify_erasure_certificate(cert, store_items=items, expected_anchor=witnessed)
    assert res["checks"]["anchor_witnessed"] is False
    assert res["valid"] is False
    assert any(f"tombstone {n - 1} is not the one the witness saw" in p for p in res["problems"]), res["problems"]

    honest = {"n_tombstones": n, "tombstones_tip": toms[n - 1]["hash"]}               # the control
    ok = verify_erasure_certificate(cert, store_items=items, expected_anchor=honest)
    assert ok["checks"]["anchor_witnessed"] is True and ok["valid"] is True, ok["problems"]


def test_a_witness_of_an_empty_chain_must_hold_the_genesis_tip(signed):
    """SURVIVORS core.py:1140-1142 (`w_n == 0 and w_tip != _GENESIS` -> `or` / `!=` / `==`, and
    `ok_w = False` -> `True`). An empty chain has exactly one honest tip."""
    cert, items, _receipts, _pk = signed
    bad = verify_erasure_certificate(cert, store_items=items,
                                     expected_anchor={"n_tombstones": 0, "tombstones_tip": "ab" * 32})
    assert bad["checks"]["anchor_witnessed"] is False and bad["valid"] is False
    assert any("empty chain with a non-genesis tip" in p for p in bad["problems"]), bad["problems"]

    ok = verify_erasure_certificate(cert, store_items=items,
                                    expected_anchor={"n_tombstones": 0, "tombstones_tip": _GENESIS})
    assert ok["checks"]["anchor_witnessed"] is True and ok["valid"] is True, ok["problems"]


# -- (8) bound to the store the absence was checked against ------------------------------------------
def test_an_anchor_without_a_writes_tip_cannot_be_bound_to_a_store(signed):
    """SURVIVORS core.py:1248 `checks["store_bound"] = False` -> `True` / `None`, core.py:1246
    `is None` -> `is not None`. No test handed `store_receipts` with an anchor lacking `writes_tip`;
    flipped, a certificate that names no store at all read as bound to the one it was checked
    against."""
    cert, items, receipts, _pk = signed
    c = copy.deepcopy(cert)
    del c["anchor"]["writes_tip"]
    res = verify_erasure_certificate(c, store_items=items, store_receipts=receipts)
    assert res["checks"]["store_bound"] is False
    assert res["valid"] is False
    assert any("carries no writes_tip" in p for p in res["problems"]), res["problems"]


def test_a_certificate_from_a_receiptless_store_does_not_bind_to_one_with_receipts(signed):
    """SURVIVORS core.py:1249-1252 (`w_tip == _GENESIS and store_receipts` -> `or`, `==` -> `!=`,
    `checks["store_bound"] = False` -> `True` / `None`). A genesis `writes_tip` says "issued from a
    store that never wrote a receipt"; a store that has receipts is therefore a different store."""
    cert, items, receipts, _pk = signed
    c = copy.deepcopy(cert)
    c["anchor"]["writes_tip"] = _GENESIS
    res = verify_erasure_certificate(c, store_items=items, store_receipts=receipts)
    assert res["checks"]["store_bound"] is False
    assert res["valid"] is False
    assert any("issued from a store with no write receipts" in p for p in res["problems"]), res["problems"]


# -- the authority an erasure carries ----------------------------------------------------------------
def test_an_erasure_authorization_is_bound_to_its_subject_and_request():
    """SURVIVOR core.py:948 `_canon({"subject": ..., "request_id": ...})` -> `_canon(None)`
    (core:948:34:f91c5908).

    The only test of `sign_erasure` verified the signature against `erasure_challenge()` computed
    by the same function, so a challenge that no longer mentioned the subject or the request -- the
    same message for every erasure -- verified exactly as well. That would make one principal's
    signature over "erase bob, req-1" an authorization for every erasure the store ever performs.
    The auditor's check is that the signature does NOT verify for any other pair."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    psk, ppk = new_source_keypair()
    sig = bytes.fromhex(sign_erasure(psk, "bob", "req-1"))
    pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(ppk))
    pk.verify(sig, erasure_challenge("bob", "req-1").encode())                        # the control
    for subject, request in (("alice", "req-1"), ("bob", "req-2"), ("bob", None)):
        assert erasure_challenge(subject, request) != erasure_challenge("bob", "req-1")
        with pytest.raises(InvalidSignature):
            pk.verify(sig, erasure_challenge(subject, request).encode())


def test_the_erasure_challenge_is_the_documented_message():
    """SURVIVORS core.py:948 `"erase:"` -> `"ERASE:"`, `"subject"` -> `"SUBJECT"`, `"request_id"` ->
    `"REQUEST_ID"` (core:948:11:58a07c3c, core:948:42:5acd9f33, core:948:62:631f57b1).

    The challenge is what a principal signs OFF the store's box, possibly years before an auditor
    checks it. Signer and checker call the same function, so renaming a field breaks nothing a test
    could see and orphans every authorization already issued. Pinned to its definition: "erase:" +
    SHA-256 of the canonical JSON of {subject, request_id}."""
    body = json.dumps({"subject": "bob", "request_id": "req-1"}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert erasure_challenge("bob", "req-1") == "erase:" + hashlib.sha256(body).hexdigest()
    assert erasure_challenge("bob", "req-1") == \
        "erase:6b2e081f0be1f97f71ff7a5179552fe55424d7148ae4b30025b9812e52ff2d76"


# -- (6) the anchor, one field at a time -------------------------------------------------------------
def _resth(anchor):
    anchor["sth_hash"] = _sha256_hex(_canon({k: anchor[k] for k in ("n_writes", "writes_tip", "n_tombstones",
                                                                    "tombstones_tip")}))
    return anchor


def _only_failed(res):
    return sorted(k for k, v in res["checks"].items() if v is False)


def test_an_anchor_count_that_disagrees_with_the_chain_fails_on_its_own(signed):
    """SURVIVORS core.py:1106 `anchor_ok = False` -> `True` / `None` (core:1106:20:299fd9d5,
    core:1106:8:8901a5ef).

    The only tests that reached this line trimmed the chain, which ALSO breaks the Merkle root, so
    the count check never decided a verdict alone. Here only `n_tombstones` lies (and `sth_hash` is
    recomputed over it, which needs no key)."""
    cert, items, _receipts, _pk = signed
    c = copy.deepcopy(cert)
    c["anchor"]["n_tombstones"] += 1
    _resth(c["anchor"])
    res = verify_erasure_certificate(c, store_items=items)
    assert _only_failed(res) == ["anchor_consistent"], (res["checks"], res["problems"])
    assert res["valid"] is False


def test_an_anchor_root_that_disagrees_with_the_chain_fails_on_its_own(signed):
    """SURVIVORS core.py:1113 `anchor_ok = False` -> `True` / `None` (core:1113:28:5ab64a19,
    core:1113:16:0d03a29b). `sth_hash` does not cover `tombstones_root`, so a root that commits to
    a different set of tombstones changes nothing else in the certificate."""
    cert, items, _receipts, _pk = signed
    c = copy.deepcopy(cert)
    c["anchor"]["tombstones_root"] = merkle_root([b"a", b"different", b"history"]).hex()
    res = verify_erasure_certificate(c, store_items=items)
    assert _only_failed(res) == ["anchor_consistent"], (res["checks"], res["problems"])
    assert res["valid"] is False


def test_an_sth_hash_that_is_not_its_fields_fails_on_its_own(signed):
    """SURVIVORS core.py:1117 (the guard: `is not None` -> `is None`, `and` -> `or`, `k in anc` ->
    `k not in anc`, the "sth_hash" key renamed). `sth_hash` is the value a witness signs; every
    existing test that broke it broke something else too, so a guard that skipped the comparison
    entirely was invisible. Here the four fields are honest and only the hash over them is not."""
    cert, items, _receipts, _pk = signed
    c = copy.deepcopy(cert)
    c["anchor"]["sth_hash"] = hashlib.sha256(b"the head a witness was shown").hexdigest()
    res = verify_erasure_certificate(c, store_items=items)
    assert _only_failed(res) == ["anchor_consistent"], (res["checks"], res["problems"])
    assert res["valid"] is False
    assert any("sth_hash does not re-derive" in p for p in res["problems"]), res["problems"]


def test_checks_that_were_not_asked_for_say_none(signed):
    """SURVIVORS core.py:1124 and core.py:1242 (`checks[...] = None` -> `= ""`), core.py:1213. "Not performed" is
    None throughout this verifier -- `store_absent` documents it -- and `valid` reads it with
    `is not False`, so an empty string slipped through every verdict. A consumer that tests
    `checks["store_bound"] is None` to learn whether the binding ran got the wrong answer."""
    cert, items, _receipts, _pk = signed
    res = verify_erasure_certificate(cert, store_items=items)
    assert res["checks"]["anchor_witnessed"] is None
    assert res["checks"]["store_bound"] is None
    assert res["valid"] is True
    # and with no store at all, the absence proof is "not performed" and nothing is reported about a
    # store nobody named (core.py:1213 `store_items is None and store_path` -> `or` read path=None)
    bare = verify_erasure_certificate(cert)
    assert bare["checks"]["store_absent"] is None
    assert bare["problems"] == [] and bare["valid"] is True


# -- the scope statement -----------------------------------------------------------------------------
def test_a_widened_scope_covers_list_is_refused(signed):
    """SURVIVORS core.py:1280 `("scope_covers", ...)` -> `("XXscope_coversXX", ...)` / upper-case
    (core:1280:65:9dcfe18b, core:1280:65:196d46d1).

    The existing scope tests alter `scope` and `scope_excludes`; nothing altered `scope_covers`, so
    a loop that looked the list up under the wrong key -- and therefore never compared it -- passed.
    A certificate that claims to cover the app's vector store is claiming more than it verified."""
    cert, items, _receipts, _pk = signed
    c = copy.deepcopy(cert)
    c["scope_covers"] = list(c["scope_covers"]) + ["the application's vector index"]
    res = verify_erasure_certificate(c, store_items=items)
    assert res["checks"]["scope_intact"] is False
    assert res["valid"] is False
    assert any("`scope_covers` list does not match" in p for p in res["problems"]), res["problems"]


# -- (5) a certificate from before the scope marker --------------------------------------------------
def test_a_pre_marker_certificate_is_scoped_by_its_claimed_requests(signed):
    """SURVIVORS core.py:1163-1165 (`claimed = set(...)` -> `None`, the "request_ids" key renamed,
    `if claimed` forced either way, the None-request clause).

    A certificate written before `scoped_to` existed is scoped by the requests it names, plus any
    tombstone with no request. The one test of that path used a store with a single request, where
    "scoped to it" and "the whole chain" are the same set. With two requests they are not: scoped
    to DSAR-2 the certificate is honest, read as unscoped its summary no longer matches."""
    cert, items, _receipts, _pk = signed
    legacy = copy.deepcopy(cert)
    del legacy["scoped_to"]
    dsar2 = [t for t in legacy["tombstones"] if t["request_id"] == "DSAR-2"]
    legacy["request_ids"] = ["DSAR-2"]
    legacy["erased_memory_ids"] = sorted(t["memory_id"] for t in dsar2)
    legacy["count"] = len(dsar2)
    res = verify_erasure_certificate(legacy, store_items=items)
    assert res["checks"]["summary_derivable"] is True, res["problems"]
    assert res["valid"] is True and res["count"] == 1

    overclaim = copy.deepcopy(legacy)
    overclaim["erased_memory_ids"] = sorted(t["memory_id"] for t in legacy["tombstones"])
    overclaim["count"] = len(overclaim["erased_memory_ids"])
    res = verify_erasure_certificate(overclaim, store_items=items)
    assert res["checks"]["summary_derivable"] is False
    assert res["valid"] is False

    # and a legacy certificate that claims no request at all covers the whole chain
    # (core:1164:21:0f95d0ce forces the claimed-requests filter on an empty claim)
    unscoped = copy.deepcopy(cert)
    del unscoped["scoped_to"]
    del unscoped["request_ids"]
    res = verify_erasure_certificate(unscoped, store_items=items)
    assert res["valid"] is True and res["count"] == 3, res["problems"]


# -- the producer: tombstones as the store writes them -----------------------------------------------
def test_tombstones_signed_through_an_external_signer_verify_against_the_pinned_key():
    """SURVIVORS core.py:1048 `t.get("pubkey") or pub or ""` -> `... or pub and ""`
    (core:1048:63:30057560), core.py:7956 `t["sig"] = _sig` -> `None`, core.py:7958
    `t["pubkey"] = self.receipt_pubkey` -> `None` / key renamed.

    `receipt_signer=` keeps the key outside this process -- the deployment that takes the
    write-authority boundary most seriously -- and one test covers it, checking only that the signer
    was called. Nothing verified the tombstones it produced: that they carry the signature, carry the
    public key when the store was given one, and verify from a certificate against the pinned key
    when the store was not."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk_hex, pk_hex = new_receipt_keypair()
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(sk_hex))

    def signer(h):
        return sk.sign(bytes.fromhex(h)).hex()

    for pubkey in (pk_hex, None):
        st = Inspeximus(path=None, receipts=True, receipt_signer=signer, receipt_pubkey=pubkey)
        st.remember("subject value", key="k", object="v")
        st.forget(where=lambda r: r.get("key") == "k", request_id="DSAR-9")
        t = st._tombstones[-1]
        assert t.get("sig"), pubkey
        assert t.get("pubkey") == pubkey
        cert = st.erasure_certificate(expected_pubkey=pk_hex)
        res = verify_erasure_certificate(cert, store_items=[dict(r) for r in st.items], expected_pubkey=pk_hex)
        assert res["checks"]["signatures_valid"] is True, (pubkey, res["problems"])
        assert res["valid"] is True


def test_a_tenant_sees_its_own_erasures_and_not_another_tenants(tmp_path):
    """SURVIVORS core.py:7933 `if self.tenant is not None` -> `is None`, core.py:7934
    `t["tenant"] = self.tenant` -> `None` / key renamed.

    The tenant stamp is the view filter that keeps one tenant's erasure records (request ids and
    free-text bases that name data subjects) away from another. The tests asserted the NEGATIVE --
    globex does not see acme's tombstone -- which an unstamped tombstone also satisfies, because the
    filter fails closed. The positive half, acme seeing its own erasure, was never asserted: with the
    stamp gone acme's report reads zero erasures and one withheld."""
    p = str(tmp_path / "shared.json")
    acme = Inspeximus(path=p, receipts=True, tenant="acme")
    acme.remember("acme customer record", key="a1", object="x")
    globex = Inspeximus(path=p, receipts=True, tenant="globex")
    globex.remember("globex customer record", key="g1", object="y")
    acme.forget(where=lambda r: r.get("key") == "a1", request_id="ACME-DSAR-1")
    assert acme._tombstones[-1]["tenant"] == "acme"
    rep = Inspeximus(path=p, receipts=True, tenant="acme").erasure_report()
    assert rep["tombstoned_total"] == 1
    assert [e["request_id"] for e in rep["erasures"]] == ["ACME-DSAR-1"]
    other = Inspeximus(path=p, receipts=True, tenant="globex").erasure_report()
    assert other["tombstoned_total"] == 0 and other["erasures"] == []


def test_the_certificates_self_check_honours_the_pinned_key():
    """SURVIVOR core.py:10479 `self.verify_writes(expected_pubkey)` -> `self.verify_writes(None)`
    (core:10479:23:b2fe9891).

    `erasure_certificate(expected_pubkey=...)` records the store's own verification in
    `self_check`. Every test that pinned a key pinned the right one, so a self-check that dropped
    the pin reported `verified: True` either way. Pinned to a stranger's key it must not."""
    sk, pk = new_receipt_keypair()
    _other_sk, other_pk = new_receipt_keypair()
    st = Inspeximus(path=None, receipts=True, receipt_key=sk)
    st.remember("subject value", key="k", object="v")
    st.forget(where=lambda r: r.get("key") == "k", request_id="DSAR-3")
    assert st.erasure_certificate(expected_pubkey=pk)["self_check"]["verified"] is True     # control
    wrong = st.erasure_certificate(expected_pubkey=other_pk)["self_check"]
    assert wrong["verified"] is False
    assert any("unexpected key" in p for p in wrong["problems"]), wrong["problems"]


def test_a_tenant_bound_handle_refuses_to_issue_a_certificate(tmp_path):
    """SURVIVORS core.py:10470-10475 (the refusal's message). No test ever asked a tenant-bound
    handle for a certificate, so the refusal -- which exists because the chain carries every
    tenant's request ids and free-text bases -- had never run."""
    st = Inspeximus(path=str(tmp_path / "m.json"), receipts=True, tenant="acme")
    st.remember("acme record", key="a", object="x")
    st.forget(where=lambda r: r.get("key") == "a", request_id="ACME-1")
    with pytest.raises(AttributeError, match="operator-only and is not available on a tenant-bound handle"):
        st.erasure_certificate()


def test_without_an_ed25519_backend_signatures_are_not_reported_valid(signed, monkeypatch):
    """SURVIVORS core.py:1045 `sigs_ok = False` -> `True` / `None` (core:1045:26:d20c71e0,
    core:1045:16:83770c00), and the message on core.py:1044.

    The base package has no dependencies, so a verifier running WITHOUT `cryptography` is an
    ordinary deployment, not an edge case -- and no test ran one. There the signatures cannot be
    checked at all; with the flag flipped the certificate said `signatures_valid: True, valid: true`
    about signatures nothing had looked at."""
    import inspeximus.core as core
    cert, items, _receipts, _pk = signed
    monkeypatch.setattr(core, "_HAVE_ED", False)
    res = verify_erasure_certificate(cert, store_items=items)
    assert res["checks"]["signatures_valid"] is False
    assert res["valid"] is False
    assert any("cannot verify signatures (cryptography not installed)" in p for p in res["problems"])


def test_the_time_of_an_erasure_is_committed_in_its_tombstone(signed):
    """SURVIVORS core.py:7916 `"ts": ts` -> `"TS": ts` / `"XXtsXX": ts` (core:7916:67:71d8a3ad,
    core:7916:67:47da4962).

    `_tombstone_core` hashes `t.get("ts")`, so a tombstone that stored its time under another key
    hashed `ts: None` -- consistently, so every chain still verified -- and the WHEN of the erasure
    act fell out of the commitment. No test read a tombstone's time or edited it."""
    cert, items, _receipts, _pk = signed
    t = cert["tombstones"][0]
    assert isinstance(t["ts"], float) and t["ts"] > 1.7e9
    moved = copy.deepcopy(cert)
    moved["tombstones"][0]["ts"] = t["ts"] - 86400 * 30          # "it was erased a month earlier"
    res = verify_erasure_certificate(moved, store_items=items)
    assert res["checks"]["chain_intact"] is False and res["valid"] is False


# -- the producer: what erasure_certificate() writes into the document ---------------------------------
def test_a_tombstone_signed_by_another_key_is_refused_by_the_key_the_certificate_names(signed):
    """SURVIVORS core.py:10492 `"pubkey": self.receipt_pubkey` -> key renamed (core:10492:12:f27e8d8f,
    core:10492:12:d99c9f93).

    Unpinned, the verifier compares each tombstone's embedded key with the certificate's `pubkey`.
    No test put a tombstone signed by ANOTHER key into a chain, so a producer that stopped writing
    the field passed. Without it the comparison has nothing to compare against: see
    audits/2026-09-24/mutation-evidence.md, finding F1 -- a party without the operator's key can
    append a tombstone signed with its own key, delete `pubkey`, and the unpinned verifier returns
    `valid: true`. This test pins the half the producer owns."""
    import time
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    cert, items, _receipts, pk = signed
    assert cert["pubkey"] == pk
    forged = copy.deepcopy(cert)
    ask, apk = new_receipt_keypair()
    live = next(r["id"] for r in items)
    t = {"seq": len(forged["tombstones"]), "memory_id": live, "ts": time.time(),
         "request_id": "DSAR-1", "prev": forged["tombstones"][-1]["hash"]}
    t["hash"] = _sha256_hex(_canon(Inspeximus._tombstone_core(t)))
    t["pubkey"] = apk
    t["sig"] = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(ask)).sign(bytes.fromhex(t["hash"])).hex()
    forged["tombstones"].append(t)
    _reanchor(forged)
    res = verify_erasure_certificate(forged, store_items=[r for r in items if r["id"] != live])
    assert res["checks"]["signatures_valid"] is False
    assert res["valid"] is False
    assert any("signed by an unexpected key" in p for p in res["problems"]), res["problems"]


def test_the_certificate_carries_the_scope_it_is_checked_against(signed):
    """SURVIVORS core.py:10502 `"scope_covers": ...` -> key renamed (core:10502:12:9a8ad1d7,
    core:10502:12:39646dcd). The verifier compares `scope_covers` only when the certificate carries
    it, so a producer that dropped the list issued certificates whose coverage statement nothing
    could check. Its siblings are pinned the same way."""
    from inspeximus.core import _CERT_SCOPE, _CERT_SCOPE_COVERS, _CERT_SCOPE_EXCLUDES
    cert, _items, _receipts, _pk = signed
    assert cert["scope"] == _CERT_SCOPE
    assert cert["scope_covers"] == list(_CERT_SCOPE_COVERS)
    assert cert["scope_excludes"] == list(_CERT_SCOPE_EXCLUDES)


def test_the_certificate_says_when_it_was_issued_in_utc(signed):
    """SURVIVORS core.py:10482-10483 (`issued_ts` / `issued_iso` keys renamed, the strftime format
    mangled or its time argument dropped). No test read either field; an auditor reading "issued"
    off a DSAR response reads exactly these."""
    import calendar
    import time
    cert, _items, _receipts, _pk = signed
    parsed = calendar.timegm(time.strptime(cert["issued_iso"], "%Y-%m-%dT%H:%M:%SZ"))
    assert abs(parsed - cert["issued_ts"]) < 2
    assert abs(time.time() - cert["issued_ts"]) < 60
    assert cert["inspeximus_erasure_certificate"] == "1.0"


@pytest.mark.skipif(not hasattr(__import__("time"), "tzset"), reason="time.tzset is POSIX-only")
def test_the_issue_time_is_utc_on_a_server_that_is_not(monkeypatch):
    """SURVIVOR core.py:10483 `time.strftime(fmt, time.gmtime())` -> `time.strftime(fmt, )`
    (core:10483:26:a28a1dd5). Without the argument strftime formats LOCAL time, and the format
    still ends in `Z`. The suite runs where local time is UTC, so it could not see the difference;
    a server in any other zone would stamp its certificates hours off."""
    import calendar
    import time
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        st = _store(signed=True)
        cert = st.erasure_certificate()
        parsed = calendar.timegm(time.strptime(cert["issued_iso"], "%Y-%m-%dT%H:%M:%SZ"))
        assert abs(parsed - cert["issued_ts"]) < 2, (cert["issued_iso"], cert["issued_ts"])
    finally:
        monkeypatch.undo()
        time.tzset()
