"""RFC 6962 Merkle Hash Tree — inclusion and consistency proofs, zero dependencies.

WHY THIS EXISTS. `anchor()` publishes a hash-CHAIN head. That detects a rewrite, but only for a
verifier who HOLDS the whole log: `verify_consistency` re-derives the tip over the full prefix. It
cannot answer the question an auditor actually asks — "prove THIS record is in your log" — without
being handed the log itself. A hash chain has no inclusion proof at all.

A Merkle tree does, in O(log n), and it is the structure every transparency system in production
already speaks: Certificate Transparency (RFC 6962), Go's checksum database, Sigstore/Rekor, and
IETF SCITT, whose `Receipt` is defined as "a cryptographic proof that a Signed Statement is included
in the Verifiable Data Structure" and MUST support inclusion proofs. Without a tree we cannot emit
one; with this module we can.

DELIBERATELY RFC 6962 AND NOT A HOMEMADE TREE. The domain-separation prefixes (0x00 for leaves,
0x01 for interior nodes) are not decoration -- without them a leaf can be forged as an interior node
and vice versa (the classic second-preimage attack on naive Merkle trees). Matching the RFC byte for
byte is also what makes our proofs checkable by any existing CT/Sigstore verifier instead of only by
us, which is the whole point of speaking a standard.

    MTH({})     = SHA-256()
    MTH({d0})   = SHA-256(0x00 || d0)
    MTH(D[n])   = SHA-256(0x01 || MTH(D[0:k]) || MTH(D[k:n])),  k = largest power of two < n

This module is pure stdlib on purpose: the base package has no dependencies and that is a product
promise, not an accident.
"""
from __future__ import annotations

import hashlib

__all__ = [
    "leaf_hash", "node_hash", "root", "inclusion_proof", "verify_inclusion",
    "consistency_proof", "verify_consistency_proof",
]


def _sha(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def leaf_hash(data: bytes) -> bytes:
    """MTL(d) = SHA-256(0x00 || d). The 0x00 prefix is the leaf/node domain separation."""
    return _sha(b"\x00" + data)


def node_hash(left: bytes, right: bytes) -> bytes:
    """MTH(node) = SHA-256(0x01 || left || right)."""
    return _sha(b"\x01" + left + right)


def _k(n: int) -> int:
    """The largest power of two STRICTLY less than n (RFC 6962 splits there). n must be >= 2."""
    k = 1
    while k << 1 < n:
        k <<= 1
    return k


def root(leaves: list[bytes]) -> bytes:
    """Merkle Tree Hash over already-hashed leaves? NO -- over RAW leaf DATA. Pass the record bytes;
    this applies MTL itself, so a caller cannot accidentally skip the 0x00 prefix."""
    return _root_hashed([leaf_hash(d) for d in leaves])


def _root_hashed(mtl: list[bytes]) -> bytes:
    n = len(mtl)
    if n == 0:
        return _sha(b"")
    if n == 1:
        return mtl[0]
    k = _k(n)
    return node_hash(_root_hashed(mtl[:k]), _root_hashed(mtl[k:]))


def inclusion_proof(leaves: list[bytes], m: int) -> list[bytes]:
    """PATH(m, D[n]) -- the audit path proving leaf index `m` (0-based) is in a tree of n leaves."""
    n = len(leaves)
    if not 0 <= m < n:
        raise IndexError("leaf index %d out of range for %d leaves" % (m, n))
    return _path(m, [leaf_hash(d) for d in leaves])


def _path(m: int, mtl: list[bytes]) -> list[bytes]:
    n = len(mtl)
    if n == 1:
        return []
    k = _k(n)
    if m < k:
        return _path(m, mtl[:k]) + [_root_hashed(mtl[k:])]
    return _path(m - k, mtl[k:]) + [_root_hashed(mtl[:k])]


def verify_inclusion(leaf_data: bytes, m: int, n: int, proof: list[bytes], expected_root: bytes) -> bool:
    """Check an audit path WITHOUT the log — the whole point. RFC 6962 section 2.1.1.

    A verifier needs only: the record, its index, the tree size, log2(n) hashes, and a root it trusts
    (one it witnessed, or one co-signed by independent witnesses).
    """
    if not 0 <= m < n:
        return False
    fn, sn = m, n - 1
    r = leaf_hash(leaf_data)
    for p in proof:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = node_hash(p, r)
            while fn & 1 == 0 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == expected_root


def consistency_proof(leaves: list[bytes], m: int) -> list[bytes]:
    """PROOF(m, D[n]) -- proves the tree of n leaves is an APPEND-ONLY extension of the first m.

    This is what a witness checks against a root it recorded earlier, and unlike our chain version it
    is O(log n) and needs no copy of the log.
    """
    n = len(leaves)
    if not 0 <= m <= n:
        raise IndexError("m=%d out of range for %d leaves" % (m, n))
    if m == 0:
        return []
    return _subproof(m, [leaf_hash(d) for d in leaves], True)


def _subproof(m: int, mtl: list[bytes], b: bool) -> list[bytes]:
    n = len(mtl)
    if m == n:
        return [] if b else [_root_hashed(mtl)]
    k = _k(n)
    if m <= k:
        return _subproof(m, mtl[:k], b) + [_root_hashed(mtl[k:])]
    return _subproof(m - k, mtl[k:], False) + [_root_hashed(mtl[:k])]


def verify_consistency_proof(m: int, n: int, first_root: bytes, second_root: bytes,
                             proof: list[bytes]) -> bool:
    """RFC 6962 section 2.1.2. True iff the size-n tree provably contains the size-m tree as a prefix."""
    if m < 0 or n < m:
        return False
    if m == n:
        return first_root == second_root and not proof
    if m == 0:
        return True                      # the empty tree is a prefix of everything
    node, last = m - 1, n - 1
    while node & 1:
        node >>= 1
        last >>= 1
    p = list(proof)
    if not p:
        return False
    if m == 1 << (m.bit_length() - 1):   # m is an exact power of two: first_root is the seed
        fr = sr = first_root
    else:
        fr = sr = p.pop(0)
    for h in p:
        if last == 0:
            return False
        if node & 1 or node == last:
            fr = node_hash(h, fr)
            sr = node_hash(h, sr)
            while node & 1 == 0 and node != 0:
                node >>= 1
                last >>= 1
        else:
            sr = node_hash(sr, h)
        node >>= 1
        last >>= 1
    return last == 0 and fr == first_root and sr == second_root
