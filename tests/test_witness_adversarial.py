"""The witness layer, attacked rather than described.

A co-signature is only worth something if the party being checked cannot produce it. This file asks
that question directly, plus the three ways such a scheme usually leaks: replay, rollback, and Sybil.

All five properties HOLD on the current implementation — this file exists to keep them holding. Each
one is the kind of guarantee a later "simplification" (drop the allowlist and just verify the
signature; bind the signature to the store instead of the head; count keys instead of classes) would
remove while every other test stayed green.

The first assertion is the control: if an honest co-signature from an allowlisted witness does NOT
verify, the four refusals below are vacuous and prove nothing.
"""
import os
import tempfile

import pytest

from inspeximus.core import Inspeximus

cryptography = pytest.importorskip("cryptography", reason="witness co-signing needs cryptography")
from inspeximus.witness_pool import Witness  # noqa: E402

V = Inspeximus.verify_cosigned_anchor


def _witness():
    return Witness(state_path=os.path.join(tempfile.mkdtemp(), "w.json"))


@pytest.fixture()
def store(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"), receipts=True)
    m.remember("alpha fact")
    m.remember("beta fact")
    m.flush()
    return m


def test_control_an_honest_cosignature_verifies(store):
    """Without this, every refusal below could be a verifier that rejects everything."""
    a = store.anchor()
    w = _witness()
    pub, sig = w.cosign("s1", a)
    assert V(a, [(pub, sig)], [pub], 1)["ok"] is True


def test_a_self_minted_witness_does_not_count(store):
    """The core question: can the attested party mint its own witness?

    Anyone can generate a valid Ed25519 key and produce a valid signature. What stops that being
    trust is that `witnesses` is the CLIENT's allowlist — validity is not standing.
    """
    a = store.anchor()
    honest, rogue = _witness(), _witness()
    hpub, _ = honest.cosign("s1", a)
    rpub, rsig = rogue.cosign("s1", a)

    r = V(a, [(rpub, rsig)], [hpub], 1)
    assert r["ok"] is False, (
        "a signature from a key the client never allowlisted was counted as a witness — "
        "then an operator can co-sign its own fork with keys it minted itself")
    assert r["count"] == 0


def test_a_cosignature_cannot_be_replayed_onto_another_anchor(store):
    """The signature must commit to the HEAD, not merely to the store."""
    a1 = store.anchor()
    w = _witness()
    pub, sig = w.cosign("s1", a1)

    store.remember("gamma fact")
    store.flush()
    a2 = store.anchor()
    assert a2.get("sth_hash") != a1.get("sth_hash"), "fixture error: the head did not move"

    assert V(a2, [(pub, sig)], [pub], 1)["ok"] is False, (
        "a co-signature of an earlier head validated against a later one — "
        "an operator could show one witness signature for any history")


def test_a_witness_refuses_to_co_sign_a_rollback(store):
    """The persisted last-head is what makes the guarantee hold ACROSS TIME rather than per-call."""
    a1 = store.anchor()
    w = _witness()
    w.cosign("s1", a1)

    store.remember("gamma fact")
    store.flush()
    a2 = store.anchor()
    w.cosign("s1", a2)

    with pytest.raises(ValueError):
        w.cosign("s1", a1)          # back to the old head = a fork of what it already attested


def test_sybil_keys_declared_to_one_class_collapse_to_one_vote(store):
    """k-of-n is meaningless if one party can mint n keys. Declared classes are the defence.

    Honest scope, which the implementation states outright: the class map is a DECLARED grouping the
    allowlist curator owns. The store enforces the collapse; it cannot prove two classes are
    causally independent. This test pins the enforcement, not the independence.
    """
    a = store.anchor()
    w1, w2 = _witness(), _witness()
    c1 = w1.cosign("s1", a)
    c2 = w2.cosign("s1", a)

    one_class = {c1[0]: "sybilA", c2[0]: "sybilA"}
    r = V(a, [c1, c2], one_class, 2)
    assert r["count"] == 1, f"two keys in one declared class must be one vote, got {r['count']}"
    assert r["ok"] is False

    two_classes = {c1[0]: "orgA", c2[0]: "orgB"}
    assert V(a, [c1, c2], two_classes, 2)["ok"] is True, (
        "genuinely distinct declared classes must still reach the threshold, or the collapse rule "
        "has made k-of-n unusable rather than safe")
