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

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.core import _GENESIS, verify_erasure_certificate


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
