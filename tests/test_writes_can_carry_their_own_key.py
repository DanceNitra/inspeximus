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
