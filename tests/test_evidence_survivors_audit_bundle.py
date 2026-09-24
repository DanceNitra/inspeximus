"""Audit bundle: tests written from the 2026-09-24 mutation run of the evidence modules.

Each test exists because a named mutant of `inspeximus/audit_bundle.py` survived the whole suite
(audits/2026-09-24/mutation-evidence.md). The mutation is named in each docstring so the test cannot
be "simplified" back into one that passes either way. Every test is green on the original source. The
run was stopped before these were checked against their mutants with
`python audits/2026-09-24/mutate_evidence.py kill`; the command is under "Not run yet" in the report.
"""
from __future__ import annotations

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.audit_bundle import _bundle_hash, bind_content, build_bundle, verify_bundle
from inspeximus.core import witness_cosign


def _signed_store(n=3, erase=0):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(path=None, receipts=True, receipt_key=sk)
    for i in range(n):
        m.remember(f"fact {i}", key=f"k{i}", object=str(i))
    for i in range(erase):
        m.forget(where=lambda r, i=i: r.get("key") == f"k{i}", request_id=f"R{i}")
    return m, pk


def _rehash(b):
    b["bundle_hash"] = _bundle_hash(b)
    return b


def _cosign(b, witnesses):
    b["anchor"]["cosignatures"] = [(w.public, witness_cosign(w._secret, b["anchor"])) for w in witnesses]
    return _rehash(b)


# -- witnesses ---------------------------------------------------------------------------------------
def test_the_witness_threshold_is_the_one_the_caller_asked_for():
    """SURVIVOR audit_bundle.py:586 `verify_cosigned_anchor(anchor, cosigs, witnesses,
    threshold=threshold)` -> `threshold` dropped (audit_bundle:586:16:124321ce).

    The only co-signature test uses one witness and threshold 1, which is also the callee's
    default, so a verifier that never passed the caller's threshold on answered the same. k-of-n
    is the whole point of a witness pool: asked for 2, one co-signature is not enough."""
    from inspeximus.witness_pool import Witness
    m, pk = _signed_store()
    ws = [Witness(), Witness(), Witness()]
    b = _cosign(build_bundle(m, expected_pubkey=pk), ws[:1])
    allow = [w.public for w in ws]
    assert verify_bundle(b, witnesses=allow, threshold=1)["ok"] is True                   # the control
    res = verify_bundle(b, witnesses=allow, threshold=2)
    assert res["ok"] is False
    assert any("witness co-signature check FAILED (need 2, got 1)" in p for p in res["problems"]), res
    two = _cosign(build_bundle(m, expected_pubkey=pk), ws[:2])
    assert verify_bundle(two, witnesses=allow, threshold=2)["ok"] is True


def test_one_witness_meets_the_default_threshold():
    """SURVIVOR audit_bundle.py:376 `threshold: int = 1` -> `2` (audit_bundle:376:80:2ee038fb):
    every call passes `threshold=` explicitly."""
    from inspeximus.witness_pool import Witness
    m, pk = _signed_store()
    w = Witness()
    b = _cosign(build_bundle(m, expected_pubkey=pk), [w])
    res = verify_bundle(b, witnesses=[w.public])
    assert res["ok"] is True and res["summary"]["operator_adversarial"] is True, res["problems"]


# -- signatures --------------------------------------------------------------------------------------
def test_an_unpinned_bundle_still_catches_a_signature_that_does_not_verify():
    """SURVIVORS audit_bundle.py:519 `_pk = expected_pubkey or _r.get("pubkey")` -> `and`, the
    "pubkey" key renamed / `.get(None)` (audit_bundle:519:14:0a76916c, :519:33:2c537337,
    :519:40:30ce8f36, :519:40:363bc0fc).

    Without a pinned key each entry's signature is checked against the key it names, which cannot
    prove WHOSE key it is but does catch a signature that is simply wrong. The bad-signature test
    pins the key, so a verifier that skipped every signature when unpinned stayed green."""
    m, _pk = _signed_store(n=3)
    b = build_bundle(m)
    b["write_chain"][1]["sig"] = b["write_chain"][0]["sig"]           # well-formed, wrong
    _rehash(b)
    res = verify_bundle(b)
    assert res["ok"] is False
    assert any("DO NOT VERIFY against the key they name" in p for p in res["problems"]), res["problems"]


def test_require_signed_refuses_signatures_nobody_verified():
    """SURVIVOR audit_bundle.py:568 `(bad if require_signed else limits.append)` ->
    `(bad if (require_signed) and False else ...)` (audit_bundle:568:13:5c4cc7d8).

    `require_signed` means VERIFIED (the comment above that line says so): with no key pinned the
    signatures are present but unverified, and the flag must refuse that rather than accept a count.
    No test combined `require_signed=True` with an unpinned, signed bundle."""
    m, pk = _signed_store()
    b = build_bundle(m)
    assert verify_bundle(b, require_signed=True, expected_pubkey=pk)["ok"] is True      # the control
    res = verify_bundle(b, require_signed=True)
    assert res["ok"] is False
    assert any("PRESENT BUT UNVERIFIED" in p for p in res["problems"]), res["problems"]
    assert verify_bundle(b)["ok"] is True                    # without the flag it is a stated limit


def test_a_partly_signed_bundle_with_as_many_tombstones_as_writes_is_refused():
    """SURVIVOR audit_bundle.py:531 `_total = len(wc) + len(tc)` -> `len(wc) - len(tc)`
    (audit_bundle:531:13:be36401b).

    With the minus, a bundle holding exactly as many tombstones as write receipts has `_total == 0`
    and skips the whole signature assessment -- including "a chain that is signed in places is not
    signed". No test built one: every bundle in the suite has more writes than erasures."""
    m, pk = _signed_store(n=2, erase=2)
    b = build_bundle(m)
    assert len(b["write_chain"]) == len(b["tombstone_chain"]) == 2
    b["tombstone_chain"][1].pop("sig")
    _rehash(b)
    res = verify_bundle(b)
    assert res["ok"] is False
    assert any("PARTIALLY SIGNED: 3 of 4" in p for p in res["problems"]), res["problems"]


# -- what build_bundle writes ------------------------------------------------------------------------
def test_the_bundle_says_whether_its_baseline_was_complete(tmp_path):
    """SURVIVORS audit_bundle.py:268-270, the `baseline_complete` computation (`not in` -> `in`,
    the "id" / "memory_id" / "_receipts" lookups renamed, `or` -> `and`). The verifier reads it to
    tell a planted record from a baseline that was never clean; no test read it at all."""
    m, _pk = _signed_store()
    assert build_bundle(m)["baseline_complete"] is True
    p = str(tmp_path / "s.json")
    Inspeximus(path=p).remember("written with receipts off", key="off", object="x")
    late = Inspeximus(path=p, receipts=True)
    late.remember("written with receipts on", key="on", object="y")
    assert build_bundle(late)["baseline_complete"] is False


def test_an_unbound_handle_does_not_claim_a_cross_tenant_chain():
    """SURVIVOR audit_bundle.py:257 `cross_tenant_chain and tenant is not None` -> `or`
    (audit_bundle:257:35:25307e01). The flag switches off the verifier's record-count coverage
    check, so it must be True only when the chain really spans tenants."""
    m, _pk = _signed_store()
    b = build_bundle(m, cross_tenant_chain=True)
    assert b["cross_tenant_chain"] is False
    res = verify_bundle(b)
    assert not any("record-count coverage NOT CHECKED" in x for x in res["limits"]), res["limits"]


def test_the_governance_section_is_computed_against_the_pinned_key():
    """SURVIVOR audit_bundle.py:260 `store.governance_report(expected_pubkey)` -> `(None)`
    (audit_bundle:260:22:f8f08a55). Built with the WRONG key pinned, the store's own verification in
    the bundle must say so, and the verifier must refuse the bundle on it."""
    m, pk = _signed_store()
    _other_sk, other_pk = new_receipt_keypair()
    assert build_bundle(m, expected_pubkey=pk)["governance"]["proof"]["verified"] is True     # control
    b = build_bundle(m, expected_pubkey=other_pk)
    assert b["governance"]["proof"]["verified"] is False
    res = verify_bundle(b)
    assert res["ok"] is False
    assert any("write-verification as FAILED" in p for p in res["problems"]), res["problems"]


def test_bind_content_on_an_honest_store_reports_nothing():
    """SURVIVOR audit_bundle.py:363 `compared == 0 and first` -> `or` (audit_bundle:363:7:589b7b8b).
    With `or`, every successful comparison also reported "NOT ONE of the records was found". The
    verdict ignored it because `ok` is computed separately, and no test read the problem list of a
    clean check."""
    m, _pk = _signed_store()
    out = bind_content(build_bundle(m), list(m.items))
    assert out["ok"] is True and out["checked"] == 3
    assert out["problems"] == []
