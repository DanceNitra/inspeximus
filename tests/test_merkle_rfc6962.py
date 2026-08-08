"""The Merkle tree must be RFC 6962, not merely 'a Merkle tree'.

A homemade tree gives inclusion proofs only WE can check. The point of matching RFC 6962 byte for
byte is that any CT / Sigstore / SCITT verifier can check ours, and that the 0x00/0x01 domain
separation closes the classic second-preimage attack where a leaf is forged as an interior node.

Three kinds of test here, in increasing order of how much they prove:

  1. STRUCTURAL round-trips (every index, every split) -- self-validating, no external truth needed.
  2. NEGATIVE controls -- a tampered leaf, a shifted index, a foreign root must FAIL. Without these
     a verifier that returns True unconditionally passes every round-trip test above.
  3. INTEROP vectors -- the eight canonical Certificate Transparency leaves. These pin us to the
     wider ecosystem; a change that keeps 1 and 2 green but breaks 3 has silently forked the format.
"""
from __future__ import annotations

import hashlib

import pytest

from inspeximus.merkle import (consistency_proof, inclusion_proof, leaf_hash, node_hash, root,
                               verify_consistency_proof, verify_inclusion)

# The eight canonical CT test leaves (certificate-transparency reference data).
CT_LEAVES = [bytes.fromhex(h) for h in
             ["", "00", "10", "2021", "3031", "40414243", "5051525354555657",
              "606162636465666768696a6b6c6d6e6f"]]


def test_the_empty_tree_is_sha256_of_nothing():
    """MTH({}) = SHA-256(). Independently computable, so it needs no external vector."""
    assert root([]) == hashlib.sha256(b"").digest()


def test_a_single_leaf_is_the_leaf_hash():
    assert root([b"hello"]) == leaf_hash(b"hello")
    assert leaf_hash(b"hello") == hashlib.sha256(b"\x00hello").digest()


def test_domain_separation_is_real():
    """THE SECOND-PREIMAGE CONTROL. Without the 0x00/0x01 prefixes an attacker can present an
    interior node's preimage as a leaf. These must never collide."""
    a, b = leaf_hash(b"x"), leaf_hash(b"y")
    assert node_hash(a, b) != leaf_hash(a + b)
    assert leaf_hash(b"") != hashlib.sha256(b"").digest()


@pytest.mark.parametrize("n", list(range(1, 17)))
def test_inclusion_round_trips_for_every_index(n):
    leaves = [bytes([i]) * (i + 1) for i in range(n)]
    r = root(leaves)
    for m in range(n):
        proof = inclusion_proof(leaves, m)
        assert verify_inclusion(leaves[m], m, n, proof, r), "index %d of %d failed" % (m, n)
        assert len(proof) <= max(1, (n - 1).bit_length()), "proof is not O(log n)"


@pytest.mark.parametrize("n", list(range(1, 13)))
def test_inclusion_rejects_tampering(n):
    """NEGATIVE CONTROL. If these pass, the round-trip test above proves nothing."""
    leaves = [bytes([i]) * (i + 1) for i in range(n)]
    r = root(leaves)
    for m in range(n):
        proof = inclusion_proof(leaves, m)
        assert not verify_inclusion(leaves[m] + b"!", m, n, proof, r), "tampered leaf accepted"
        assert not verify_inclusion(leaves[m], m, n, proof, bytes(32)), "foreign root accepted"
        if n > 1:
            wrong = (m + 1) % n
            assert not verify_inclusion(leaves[m], wrong, n, proof, r), "wrong index accepted"
        if proof:
            bad = list(proof)
            bad[0] = bytes(32)
            assert not verify_inclusion(leaves[m], m, n, bad, r), "corrupted path accepted"


@pytest.mark.parametrize("n", list(range(1, 13)))
def test_consistency_round_trips_for_every_prefix(n):
    leaves = [bytes([i]) * (i + 1) for i in range(n)]
    rn = root(leaves)
    for m in range(0, n + 1):
        rm = root(leaves[:m])
        proof = consistency_proof(leaves, m)
        assert verify_consistency_proof(m, n, rm, rn, proof), "prefix %d of %d failed" % (m, n)


@pytest.mark.parametrize("n", [4, 5, 7, 8, 11])
def test_consistency_rejects_a_rewritten_prefix(n):
    """NEGATIVE CONTROL, and the one that matters: an append-only violation must be caught."""
    leaves = [bytes([i]) * (i + 1) for i in range(n)]
    rn = root(leaves)
    for m in range(1, n):
        proof = consistency_proof(leaves, m)
        forged = leaves[:m - 1] + [b"REWRITTEN"]
        assert not verify_consistency_proof(m, n, root(forged), rn, proof), \
            "a rewritten prefix verified as consistent (m=%d n=%d)" % (m, n)
        assert not verify_consistency_proof(m, n, root(leaves[:m]), bytes(32), proof), \
            "a foreign second root verified"


def test_a_truncated_log_is_not_consistent():
    """Rollback, not rewrite: the log shrank. m > n must be refused outright."""
    leaves = [bytes([i]) for i in range(8)]
    assert not verify_consistency_proof(8, 4, root(leaves), root(leaves[:4]), [])


def test_interop_vectors_against_certificate_transparency():
    """INTEROP PIN. The eight canonical CT leaves, roots for every prefix size.

    These are not asserted from memory as ground truth -- they are recorded from this
    implementation AND cross-checked by the structural tests above. What this test protects is
    STABILITY: if a future change alters any of these, our proofs stop being checkable by an
    existing CT verifier, which is the only reason to implement RFC 6962 rather than a nice tree.
    """
    roots = [root(CT_LEAVES[:i]).hex() for i in range(len(CT_LEAVES) + 1)]
    assert roots[0] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert roots[1] == "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d"
    assert roots[2] == "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125"
    assert roots[3] == "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77"
    assert roots[4] == "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7"
    assert roots[5] == "4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4"
    assert roots[6] == "76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef"
    assert roots[7] == "ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c"
    assert roots[8] == "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328"


def test_index_bounds_are_refused_not_silently_clamped():
    leaves = [b"a", b"b"]
    with pytest.raises(IndexError):
        inclusion_proof(leaves, 2)
    with pytest.raises(IndexError):
        inclusion_proof(leaves, -1)
    with pytest.raises(IndexError):
        consistency_proof(leaves, 3)
