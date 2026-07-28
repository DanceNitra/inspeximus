"""The erasure certificate is the document a DPA reads. Two things in it were not checked.

Found by the standing audit for one class: a function whose whole purpose is to REFUSE returning a
clean verdict about input it never structurally examined. Six surfaces had already been fixed that way;
these are the seventh and eighth, and they sit in the artefact `docs/AI_ACT.md` names as the moat.

1. `signatures_valid: true` ON A CERTIFICATE WITH NO SIGNATURES. `sigs_ok` started True and was only
   ever set False by a FAILING signature, so a store without `receipt_key` produced tombstones carrying
   no `sig` at all and the verifier still reported the signatures as valid. Swapping `pubkey` for zeros
   changed nothing, because nothing was verified against it. The same function already models this
   correctly two checks away: `store_absent` is None when the proof was not performed.

2. THE SCOPE STATEMENT WAS FREE TEXT. `scope` carries the certificate's own declaration of what it does
   NOT certify — "Tamper-evident integrity primitive, NOT a compliance certification" — and nothing
   compared it. Replacing it with "Full GDPR compliance certification, all systems." left the
   certificate verifying `valid: true`, because every other field still derived from the chain. The one
   sentence a regulator most needs was the easiest thing in the document to delete.
"""
from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402
from inspeximus.core import _CERT_SCOPE, verify_erasure_certificate  # noqa: E402


def _store(signed: bool, requests: int = 2):
    kw: dict = {"path": None, "receipts": True}
    if signed:
        from inspeximus import new_source_keypair
        kw["receipt_key"] = new_source_keypair()[0]
    st = Inspeximus(**kw)
    for i in range(8):
        st.remember(f"subject {i} value-{i}", key=f"k{i}", object=f"v{i}", source={"doc": f"p{i}"})
    for j in range(requests):
        st.forget(where=lambda r, j=j: r.get("key") == f"k{j}",
                  request_id=f"DSAR-{j + 1}", basis="art17")
    return st, st.erasure_certificate(request_id="DSAR-1"), [dict(r) for r in st.items]


def test_an_honest_certificate_still_verifies_signed_and_unsigned():
    """The control. A verifier that refuses everything is not a fixed verifier."""
    for signed in (False, True):
        st, cert, items = _store(signed)
        res = verify_erasure_certificate(cert, store_items=items)
        assert res["valid"] is True, (signed, res["problems"])
        assert not res["problems"], res["problems"]


def test_an_unsigned_certificate_does_not_claim_its_signatures_are_valid():
    st, cert, items = _store(signed=False)
    res = verify_erasure_certificate(cert, store_items=items)
    assert res["checks"]["signatures_valid"] is None, \
        "an unsigned certificate reported its signatures as valid"
    assert res["checks"]["signed"] is False
    assert any("UNSIGNED" in x for x in res["limits"]), res["limits"]
    # and it must not be a `problem` — not-checked is not failed, or every honest unsigned
    # certificate becomes invalid
    assert not res["problems"], res["problems"]


def test_a_signed_certificate_still_reports_signature_validity():
    """The other direction: the None must come from absence, not from the check being disabled."""
    st, cert, items = _store(signed=True)
    res = verify_erasure_certificate(cert, store_items=items)
    assert res["checks"]["signatures_valid"] is True
    assert res["checks"]["signed"] is True
    assert res["limits"] == []


def test_swapping_the_pubkey_is_refused_on_a_signed_certificate():
    st, cert, items = _store(signed=True)
    bad = copy.deepcopy(cert)
    bad["pubkey"] = "00" * 32
    res = verify_erasure_certificate(bad, store_items=items)
    assert res["valid"] is False
    assert any("unexpected key" in p for p in res["problems"]), res["problems"]


@pytest.mark.parametrize("replacement", [
    "Full GDPR compliance certification, all systems.",
    "",
    _CERT_SCOPE.replace("NOT a compliance certification", "a compliance certification"),
])
def test_editing_the_scope_statement_is_refused(replacement):
    """Including the one-word edit — dropping 'NOT' — which is the whole attack."""
    st, cert, items = _store(signed=False)
    bad = copy.deepcopy(cert)
    bad["scope"] = replacement
    res = verify_erasure_certificate(bad, store_items=items)
    assert res["valid"] is False, f"scope rewritten to {replacement[:40]!r} and still valid"
    assert res["checks"]["scope_intact"] is False
    assert any("scope" in p for p in res["problems"]), res["problems"]


def test_the_producer_and_the_verifier_read_the_same_scope_text():
    """If the producer's text drifts from the constant, every honest certificate fails. One source."""
    st, cert, _ = _store(signed=False)
    assert cert["scope"] == _CERT_SCOPE


def test_a_widened_scope_marker_is_still_refused():
    """The guarantee that already worked must not be traded away for the new ones."""
    st, cert, items = _store(signed=False, requests=2)
    bad = copy.deepcopy(cert)
    bad["scoped_to"] = None
    assert verify_erasure_certificate(bad, store_items=items)["valid"] is False
