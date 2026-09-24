"""Transparency log: tests written from the 2026-09-24 mutation run of the evidence modules.

Each test here exists because a named mutant of `inspeximus/merkle.py` or
`inspeximus/transparency.py` survived the whole suite (audits/2026-09-24/mutation-evidence.md).
The mutation is named in each docstring, so the test cannot be "simplified" back into one that
passes either way. Every one was checked in both directions with
`python audits/2026-09-24/mutate_evidence.py kill --ids <id> --tests <this test>`: green on the
original source, red on the mutant.
"""
from __future__ import annotations

import hashlib
import os

import pytest

from inspeximus import new_receipt_keypair, signed_statement
from inspeximus.merkle import (consistency_proof, inclusion_proof, node_hash, root, root_from_inclusion,
                               verify_consistency_proof, verify_inclusion)
from inspeximus.transparency import (RegistrationPolicy, RegistrationRefused, TransparencyService,
                                     verify_registered_statement)

LEAVES = [bytes([i]) * (i + 1) for i in range(11)]
ISSUER = "did:web:issuer.example"


# -- consistency proofs: the two early exits nothing reached --------------------------------------
@pytest.mark.parametrize("n", [2, 3, 5, 8, 11])
def test_an_empty_consistency_proof_proves_nothing(n):
    """SURVIVOR merkle.py:174 `return False` -> `return True` (merkle:174:15:5451bad7).

    No test ever handed the verifier an EMPTY proof for 0 < m < n, so the guard that refuses one was
    never executed. With it flipped, `verify_consistency_proof` accepts ANY pair of roots for any
    sizes -- a rewritten history reads as append-only growth -- and the whole suite stayed green."""
    rn = root(LEAVES[:n])
    for m in range(1, n):
        assert not verify_consistency_proof(m, n, root(LEAVES[:m]), rn, []), (m, n)
        forged = root([b"REWRITTEN"] + LEAVES[1:m])
        assert not verify_consistency_proof(m, n, forged, rn, []), (m, n)


@pytest.mark.parametrize("n", [2, 3, 5, 8, 11])
def test_a_consistency_proof_with_a_hash_to_spare_is_refused(n):
    """SURVIVOR merkle.py:181 `return False` -> `return True` (merkle:181:19:1c53a5a2).

    The walk returns as soon as it runs out of tree with proof hashes left over. Flipped, that exit
    returns True BEFORE either root is compared, so one junk hash appended to any proof of the right
    length makes a forged first root verify. The existing negative controls only ever used proofs of
    the exact length, so the loop never reached this line."""
    rn = root(LEAVES[:n])
    for m in range(1, n):
        good = consistency_proof(LEAVES[:n], m)
        assert verify_consistency_proof(m, n, root(LEAVES[:m]), rn, good), (m, n)   # the control
        assert not verify_consistency_proof(m, n, root(LEAVES[:m]), rn, good + [bytes(32)]), (m, n)
        forged = root([b"REWRITTEN"] + LEAVES[1:m])
        junk = [hashlib.sha256(bytes([k])).digest() for k in range(len(good) + 1)]
        assert not verify_consistency_proof(m, n, forged, rn, junk), (m, n)


@pytest.mark.parametrize("n", [1, 2, 4, 7])
def test_two_different_roots_at_the_same_size_are_a_fork_not_a_consistency(n):
    """SURVIVOR merkle.py:165 `... and not proof` -> `... or not proof` (merkle:165:15:ab4a184d).

    At m == n the only honest answer is "the roots are equal". Every existing test at m == n passed
    the SAME root twice, so the mutant -- which accepts any two roots as long as the proof is empty --
    stayed green. Two different roots for one tree size are exactly what a split view looks like."""
    honest = root(LEAVES[:n])
    fork = root([b"FORKED"] + LEAVES[1:n])
    assert verify_consistency_proof(n, n, honest, honest, [])                     # the control
    assert not verify_consistency_proof(n, n, honest, fork, [])
    assert not verify_consistency_proof(n, n, fork, honest, [])


def test_an_inclusion_proof_for_index_n_is_refused():
    """SURVIVOR merkle.py:106 `0 <= m < n` -> `0 <= m <= n` (merkle:106:17:f7317f2a).

    The bounds tests go through `inclusion_proof()`, which raises before the verifier is reached, and
    the verifier's own negative controls use indexes inside the tree. With the bound widened, leaf 0
    of a two-leaf tree verifies at index 2 -- a position that does not exist -- using leaf 0's honest
    path, so a receipt could place a record anywhere past the end of the log."""
    two = LEAVES[:2]
    r = root(two)
    path = inclusion_proof(two, 0)
    assert verify_inclusion(two[0], 0, 2, path, r)                                  # the control
    assert not verify_inclusion(two[0], 2, 2, path, r)
    assert root_from_inclusion(two[0], 2, 2, path) is None


@pytest.mark.parametrize("claimed", [3, 4, 6])
def test_a_path_too_short_for_the_claimed_tree_size_is_refused(claimed):
    """SURVIVOR merkle.py:122 `r if sn == 0 else None` -> `r if (sn == 0) or True else None`
    (merkle:122:11:aabe62bb).

    A path that ends before it has climbed the whole claimed tree leads to an interior node, not the
    root. Accepted, leaf 0 of a two-leaf log verifies as a member of a larger tree whose root is the
    two-leaf root: the tree size a Receipt states would be whatever the issuer wrote. No test
    presented a path shorter than the size it claimed."""
    two = LEAVES[:2]
    r = root(two)
    path = inclusion_proof(two, 0)
    assert not verify_inclusion(two[0], 0, claimed, path, r)
    assert root_from_inclusion(two[0], 0, claimed, path) is None


@pytest.mark.parametrize("m,n", [(5, 4), (8, 4), (3, 2)])
def test_a_rollback_is_refused_whatever_the_proof_says(m, n):
    """SURVIVOR merkle.py:162 `m < 0 or n < m` -> `m < 0 and n < m` (merkle:162:7:3b54fd68).

    The only rollback test passed an EMPTY proof, which a later guard refuses anyway, so the size
    check itself was never the thing that answered. Here the proof is built so that the rest of the
    walk would accept it: only `n < m` stands between it and True."""
    a, h1, h2 = (hashlib.sha256(bytes([k])).digest() for k in (1, 2, 3))
    proof = [a, h1, h2]
    walked = node_hash(node_hash(a, h1), h2)
    for first, second in ((a, walked), (a, node_hash(a, h1))):
        assert not verify_consistency_proof(m, n, first, second, proof), (m, n)
    assert not verify_consistency_proof(-2, n, a, node_hash(a, h1), [a, h1]), "a negative size"
