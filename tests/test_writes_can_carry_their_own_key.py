"""Attestation has to be reachable without every caller doing crypto by hand.

Measured 2026-08-08 across every store this deployment runs -- 111,264 records -- `attested_key`
coverage was **0. Zero. 0.0000%**, while 96,716 of them (86.9%) carried >=2 links. So
`strict_corroboration` (which counts DISTINCT VERIFIED KEYS among corroborating links) could not fire
for a single record, and `credit_requires_warrant` had `good_warranted > 0` on 0 of 60,077. Two
hardening flags, both shipped, both unreachable in practice.

The cause was not the flags. It was that the only way to attest a write was for the CALLER to hold a
keypair and sign each claim -- so nobody ever did, including us, and we dogfood this. Machinery with
no path to it is machinery that does not exist.

`writer_key` closes that: a store configured with a writer identity signs its own writes, so
`attested_key` is populated as a matter of course and the distinct-key rail becomes usable.

HONEST SCOPE, and it is the whole reason this is not called a trust root:
  * it attests AUTHORSHIP, not truth -- a key-holder can sign a false claim (same limit as attest()).
  * it raises manufactured independence from "type two different source strings" to "hold two distinct
    persisted keys". That is exactly what _distinct_verified_keys is for, and it is a real cost, but a
    process that can mint keys freely can still mint witnesses. Pin known writers with `trust_seeds`
    if you need more than authorship.
  * a compromised writer keeps its key. This is an identity anchor, not a integrity oracle.

Paired throughout: every "is now attested" sits next to a "still refuses what it should refuse".
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from inspeximus import Inspeximus

pytest.importorskip("cryptography")
from inspeximus import attest, new_source_keypair as new_ed25519_keypair  # noqa: E402


def _store(tmp, **kw):
    return Inspeximus(path=str(Path(tmp) / "s.json"), **kw)


def test_a_configured_writer_signs_its_own_writes():
    sk, pk = new_ed25519_keypair()
    with tempfile.TemporaryDirectory() as td:
        s = _store(td, writer_key=sk)
        rid = s.remember("the orion pipeline retries three times")
        rec = next(r for r in s.items if r.get("id") == rid)
        assert rec.get("attested_key") == pk, (
            "a store with a writer identity must stamp attested_key -- this is the field whose "
            "coverage was 0 of 111,264 records")


def test_without_a_writer_key_nothing_changes():
    """The other half: unattested writes stay unattested, so this is additive, not a silent rewrite."""
    with tempfile.TemporaryDirectory() as td:
        s = _store(td)
        rid = s.remember("the orion pipeline retries three times")
        rec = next(r for r in s.items if r.get("id") == rid)
        assert not rec.get("attested_key")


def test_an_explicit_attestation_still_wins_over_the_writer_key():
    """A claim signed by its actual source must not be relabelled with the local writer's key."""
    src_sk, src_pk = new_ed25519_keypair()
    w_sk, w_pk = new_ed25519_keypair()
    with tempfile.TemporaryDirectory() as td:
        s = _store(td, writer_key=w_sk)
        text = "the gamma service listens on port 8443"
        sig = attest(text, src_sk, "acme-docs")
        rid = s.remember(text, source={"doc": "acme-docs"}, attestation=(src_pk, sig))
        rec = next(r for r in s.items if r.get("id") == rid)
        assert rec.get("attested_key") == src_pk != w_pk


def test_a_bad_explicit_attestation_is_still_rejected_loudly():
    """Auto-signing must not become a fallback that swallows a forged signature."""
    bad_sk, bad_pk = new_ed25519_keypair()
    w_sk, _ = new_ed25519_keypair()
    with tempfile.TemporaryDirectory() as td:
        s = _store(td, writer_key=w_sk)
        wrong_sig = attest("a different claim entirely", bad_sk, "acme-docs")
        with pytest.raises(ValueError):
            s.remember("the gamma service listens on port 8443",
                       source={"doc": "acme-docs"}, attestation=(bad_pk, wrong_sig))


def test_the_signature_actually_verifies_and_is_bound_to_the_text():
    """Not just a pubkey copied onto the record: the stored signature must verify over this claim."""
    sk, pk = new_ed25519_keypair()
    with tempfile.TemporaryDirectory() as td:
        s = _store(td, writer_key=sk)
        rid = s.remember("the delta cluster uses three replicas")
        rec = next(r for r in s.items if r.get("id") == rid)
        assert rec.get("attested_key") == pk
        # re-signing the SAME message with the same key must reproduce a verifiable signature, and the
        # record must not verify against a different claim
        from inspeximus.core import _attest_message
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        vk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pk))
        vk.verify(bytes.fromhex(rec["attested_sig"]), _attest_message(rec["text"], None))
        with pytest.raises(Exception):
            vk.verify(bytes.fromhex(rec["attested_sig"]), _attest_message("a different claim", None))


def test_two_distinct_writers_make_strict_corroboration_reachable():
    """THE POINT. With strict_corroboration ON, corroboration counts distinct verified keys -- which was
    unreachable for every record in the deployment. Two key-holding writers must now clear it."""
    sk_a, pk_a = new_ed25519_keypair()
    sk_b, pk_b = new_ed25519_keypair()
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "shared.json")
        a = Inspeximus(path=path, writer_key=sk_a)
        id_a = a.remember("the epsilon endpoint accepts uploads", source={"doc": "alice"})
        b = Inspeximus(path=path, writer_key=sk_b)
        id_b = b.remember("epsilon endpoint accepts uploads", source={"doc": "bob"},
                          derived_from=[id_a])
        b.strict_corroboration = True
        by_id = {r.get("id"): r for r in b.items}
        keys = Inspeximus._distinct_verified_keys([id_a, id_b], by_id)
        assert keys == 2, f"distinct verified keys should be 2, got {keys} (pk_a={pk_a[:8]}, pk_b={pk_b[:8]})"


def test_one_writer_cannot_manufacture_two_witnesses_by_renaming_its_source():
    """The attack the distinct-key rail exists to price: same key, two source strings, one witness."""
    sk, _ = new_ed25519_keypair()
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "s.json")
        s = Inspeximus(path=path, writer_key=sk)
        i1 = s.remember("the zeta job runs at midnight", source={"doc": "wikipedia"})
        i2 = s.remember("zeta job runs at midnight", source={"doc": "wikipedia.org"},
                        derived_from=[i1])
        by_id = {r.get("id"): r for r in s.items}
        assert Inspeximus._distinct_verified_keys([i1, i2], by_id) == 1, (
            "two source strings from ONE key must collapse to one witness")


# ── 2.4.0: the tenant is part of the binding, and the binding is re-checkable ──────────────────────
#
# Raised externally (yun520-1, NousResearch/hermes-agent#34352): we signed text+source and not the
# tenant, so a signature stayed valid after a record was moved into another tenant's rows -- the one
# fact tenant isolation most needs to be non-repudiable was the one the signature did not cover.
#
# And the deeper half, found while fixing it: `attested_sig` was WRITTEN and NEVER READ. There was no
# API to re-verify a stored signature anywhere in the library; the only check was hand-rolled in this
# file. 2.3.0's changelog says "a non-repudiable identity you cannot re-verify is not one" and then
# shipped no verifier. Binding the tenant without one would have been unenforceable.

def test_a_record_moved_between_tenants_fails_verification(tmp_path):
    """THE REGRESSION. Move a row into another tenant and the signature must stop verifying."""
    sk, _pk = new_ed25519_keypair()
    s = Inspeximus(path=str(tmp_path / "s.json"), embed=None, tenant="acme", writer_key=sk)
    s.remember("the release cadence is fortnightly", source={"doc": "runbook"})
    ok, problems = s.verify_attestations()
    assert ok, "a freshly signed record must verify: %s" % problems

    rec = next(r for r in s._items if r.get("attested_sig"))
    rec["tenant"] = "globex"                      # the out-of-band edit an isolation bug would make
    ok, problems = s.verify_attestations()
    assert not ok, "a record relocated to another tenant still verified -- the binding is not covered"
    assert any("tenant" in p for p in problems), problems


def test_an_unbound_store_still_verifies(tmp_path):
    """THE CONTROL, and the back-compatibility guarantee: tenant=None is omitted from the message, so
    an unbound store signs exactly what every earlier version signed."""
    sk, _pk = new_ed25519_keypair()
    s = Inspeximus(path=str(tmp_path / "u.json"), embed=None, writer_key=sk)
    s.remember("an unscoped fact about the schedule", source={"doc": "runbook"})
    ok, problems = s.verify_attestations()
    assert ok, "an unbound store must verify unchanged: %s" % problems


def test_tampered_text_fails_verification(tmp_path):
    """The verifier must catch the ordinary case too, or the tenant test above proves only one path."""
    sk, _pk = new_ed25519_keypair()
    s = Inspeximus(path=str(tmp_path / "t.json"), embed=None, tenant="acme", writer_key=sk)
    s.remember("the original claim", source={"doc": "runbook"})
    rec = next(r for r in s._items if r.get("attested_sig"))
    rec["text"] = "a claim someone substituted later"
    ok, problems = s.verify_attestations()
    assert not ok and problems


def test_a_store_with_no_attestations_reports_that_it_checked_nothing(tmp_path):
    """An empty check must not read as a pass -- the defect this whole file is about, applied to the
    verifier itself."""
    s = Inspeximus(path=str(tmp_path / "n.json"), embed=None)
    s.remember("a plain unsigned record")
    ok, problems = s.verify_attestations()
    assert not ok, "a store with nothing to verify reported OK"
    assert any("verified NOTHING" in p for p in problems), problems


# ── the compatibility choice was a downgrade channel (caught pre-release, folded into 2.4.0) ──────────────────────────────────────
# Found by red-teaming the 2.4.0 note before publishing it, not by a user. An unbound store OMITS the
# tenant from the signed message -- that omission is exactly what keeps pre-2.4.0 signatures valid --
# and the verifier tried the record's tenant and THEN no-tenant for every key alike. So a row signed
# while unbound and later GIVEN a tenant verified: an unsigned promotion into a tenant it was never
# signed for. The fallback now belongs only to keys that had an excuse.

def test_an_unbound_signed_row_cannot_be_promoted_into_a_tenant(tmp_path):
    """THE FIX. Sign with no tenant, then write a tenant onto the row: it must stop verifying."""
    sk, _pk = new_ed25519_keypair()
    s = Inspeximus(path=str(tmp_path / "d.json"), embed=None, writer_key=sk)
    s.remember("a fact signed while the store was unbound", source={"doc": "runbook"})
    ok, problems = s.verify_attestations()
    assert ok, "the unbound baseline must verify first, or the negative below proves nothing: %s" % problems

    rec = next(r for r in s._items if r.get("attested_sig"))
    rec["tenant"] = "beta"                        # the promotion
    ok, problems = s.verify_attestations()
    assert not ok, ("an unbound-signed row was accepted after being placed into a tenant -- the "
                    "no-tenant fallback is a downgrade channel")


def test_a_foreign_signer_keeps_the_no_tenant_fallback(tmp_path):
    """THE CONTROL that the fix did not over-reach. An outside signer cannot bind a tenant it never
    saw, so an externally-attested row must still verify wherever it legitimately sits. Without this,
    closing the downgrade would silently start failing every third-party attestation."""
    src_sk, src_pk = new_ed25519_keypair()
    w_sk, _w_pk = new_ed25519_keypair()
    s = Inspeximus(path=str(tmp_path / "f.json"), embed=None, tenant="acme", writer_key=w_sk)
    text = "a claim signed by its actual source"
    s.remember(text, source={"doc": "acme-docs"}, attestation=(src_pk, attest(text, src_sk, "acme-docs")))
    ok, problems = s.verify_attestations()
    assert ok, "a foreign attestation inside a tenant-bound store must still verify: %s" % problems


def test_the_signature_cannot_see_a_row_that_is_gone(tmp_path):
    """THE HONEST LIMIT, asserted so it cannot quietly change into a claim we do not have.

    Tenant binding was built in answer to a data-LOSS incident, and it does not address it: a deleted
    row carries no failing signature. This pins that, and pins the counterpart -- verify_writes, which
    reads the receipt chain, DOES name the vanished ids. Signature for placement, receipts for
    cardinality.
    """
    sk, _pk = new_ed25519_keypair()
    p = tmp_path / "g.json"
    s = Inspeximus(path=str(p), embed=None, writer_key=sk, receipts=True)
    a, b = s.for_tenant("acme"), s.for_tenant("beta")
    for i in range(3):
        a.remember(f"acme fact {i}")
    b.remember("beta fact")
    s.flush()
    gone = [r["id"] for r in s._items if r.get("tenant") == "acme"]
    assert len(gone) == 3

    s._items[:] = [r for r in s._items if r.get("tenant") != "acme"]   # the loss, out of band
    ok, problems = s.verify_attestations()
    assert ok, ("attestation now reports the deletion (%s) -- if that is a real improvement, rewrite "
                "the HONEST LIMITS note; until then this pins what the check actually covers" % problems)

    ok_w, prob_w = s.verify_writes()
    assert not ok_w, "the receipt chain did not notice three deleted records"
    assert all(any(g in str(p_) for p_ in prob_w) for g in gone), (
        "verify_writes failed, but not about the rows that vanished: %s" % prob_w)
