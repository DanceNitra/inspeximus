"""verify_bundle reports `fully_verified` and `checks_not_run` (3.19, G-4 / AUDIT-A).

A check that could not run is a limit, not a problem, so `ok` stays True for a caller verifying on a base install
without a crypto backend. But a limit was the only trace of it: a caller reading `ok` could not tell a bundle whose
every check ran from one whose signatures, witnesses, content or growth were never checked. Each case below removes
one ingredient from a bundle that verifies fully and requires `fully_verified` to fall and the check to be named. The
control first: with every ingredient, nothing is listed and `fully_verified` is True.
"""
from __future__ import annotations

import os

import pytest

from inspeximus import Inspeximus, audit_bundle
from inspeximus.audit_bundle import build_bundle, verify_bundle
from inspeximus.core import new_ed25519_keypair
from inspeximus.witness_pool import Witness

SK, PUB = new_ed25519_keypair()


def _parts(tmp_path, sign=True, witnessed=True, cross_tenant=False):
    ws = [Witness(state_path=str(tmp_path / ("w%d.json" % i))) for i in range(3)]
    kw = {"receipt_key": SK} if sign else {}
    ix = Inspeximus(path=str(tmp_path / "s.json"), receipts=True, **kw)
    ix.remember("deployment needs two approvers", key="pol", object="two")
    ix.remember("the deploy key rotates every 90 days", key="rot", object="90d")
    ix.flush()
    if cross_tenant:                                         # a tenant's export of a chain that spans tenants
        ix.for_tenant("acme").remember("acme keeps its own notes")
        ix.flush()
    b = build_bundle(ix.for_tenant("acme") if cross_tenant else ix, witnesses=ws if witnessed else None,
                     store_id="store-1",
                     expected_pubkey=PUB if sign else None, cross_tenant_chain=cross_tenant)
    full = dict(witnesses=[w.public for w in ws], threshold=2, expected_pubkey=PUB if sign else None,
                store_items=ix.items, store_receipts=ix._receipts)
    return ix, ws, b, full


def test_a_bundle_whose_every_check_ran_is_fully_verified(tmp_path):
    _ix, _ws, b, full = _parts(tmp_path)
    out = verify_bundle(b, **full)
    assert out["ok"] and out["fully_verified"] and out["checks_not_run"] == [], out["checks_not_run"]


def _case(tmp_path, monkeypatch, name):
    if name == "chain_signatures (no crypto backend)":
        _ix, _ws, b, full = _parts(tmp_path)
        monkeypatch.setattr(audit_bundle, "_HAVE_ED", False)
        return b, full
    if name == "chain_signatures (unsigned chain)":
        _ix, _ws, b, full = _parts(tmp_path, sign=False)
        return b, full
    if name == "chain_signatures (no pinned key)":
        _ix, _ws, b, full = _parts(tmp_path)
        return b, dict(full, expected_pubkey=None)
    if name == "external_cosignature":
        _ix, _ws, b, full = _parts(tmp_path, witnessed=False)
        return b, dict(full, witnesses=None, threshold=1)
    if name == "witness_attestations":
        _ix, ws, b, full = _parts(tmp_path)
        return b, dict(full, attestations=[w.attest(b["store_id_derived"]) for w in ws[:2]])
    if name == "record_count_coverage":
        _ix, _ws, b, full = _parts(tmp_path, cross_tenant=True)
        return b, full
    if name == "growth_chain_membership":
        _ix, _ws, b, full = _parts(tmp_path)
        return b, dict(full, store_receipts=None)
    if name == "content":
        _ix, _ws, b, full = _parts(tmp_path)
        return b, dict(full, store_items=None)
    raise AssertionError(name)


CASES = ["chain_signatures (no crypto backend)", "chain_signatures (unsigned chain)", "chain_signatures (no pinned key)",
         "external_cosignature", "witness_attestations", "record_count_coverage", "growth_chain_membership", "content"]


@pytest.mark.parametrize("case", CASES)
def test_a_check_that_did_not_run_is_named_and_the_bundle_is_not_fully_verified(tmp_path, monkeypatch, case):
    b, kw = _case(tmp_path, monkeypatch, case)
    out = verify_bundle(b, **kw)
    check = case.split(" (")[0]
    assert check in out["checks_not_run"], "%s did not run and is not named: %r" % (case, out["checks_not_run"])
    assert out["fully_verified"] is False, "%s did not run and the bundle reads fully verified" % case
    if not out["problems"]:
        assert out["ok"] is True, "a check that did not run changed `ok`; callers without a backend rely on it"
