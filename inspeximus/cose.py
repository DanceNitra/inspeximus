"""COSE Receipts (draft-ietf-cose-merkle-tree-proofs-11) over our RFC 6962 tree — zero dependencies.

WHAT THIS IS FOR. `inclusion_proof()` already produces a real proof, but in OUR json shape, which only
our verifier reads. IETF SCITT defines a Receipt as "a cryptographic proof that a Signed Statement is
included in the Verifiable Data Structure", and the wire format for that proof is a COSE_Sign1 whose
payload is the Merkle root and whose headers carry the audit path. Emitting that shape is the
difference between "we have inclusion proofs" and "our inclusion proofs are checkable by anyone who
implements the standard".

THE LABELS ARE READ FROM THE DRAFT, NOT REMEMBERED (draft-11, section 8.1 / figure in section 2):

    protected   1   : alg              (-8 EdDSA, -7 ES256)
                395 : vds              = 1  (RFC9162_SHA256)
    unprotected 396 : vdp              = { -1 : [ <<inclusion proof>> ] }
    payload         : the Merkle root bytes
    tag         18  : COSE_Sign1

and the inclusion proof itself is CBOR `[tree-size, leaf-index, [path-element, ...]]`.

ZERO DEPENDENCIES, so the CBOR encoder is here rather than imported. It is deliberately a SUBSET —
unsigned ints, negative ints, byte strings, text strings, arrays, maps, tags, null — which is
everything COSE needs and nothing more. Map keys are emitted in canonical order (RFC 8949 section
4.2.1: sorted by their encoded bytes), because a receipt whose bytes depend on dict iteration order
is not a receipt.

HONEST SCOPE. This produces a Receipt of Inclusion. It does NOT make inspeximus a SCITT Transparency
Service: there is no registration policy, no Signed Statement envelope, and no append-only service
identity beyond the anchor. What a verifier gets is exactly what the proof says — that a record is
in a log with a given root — and no more.
"""
from __future__ import annotations

import hashlib

__all__ = ["encode", "decode", "inclusion_receipt", "verify_receipt",
           "COSE_SIGN1_TAG", "HDR_ALG", "HDR_VDS", "HDR_VDP", "VDS_RFC9162_SHA256",
           "PROOF_INCLUSION", "ALG_EDDSA", "ALG_ES256"]

COSE_SIGN1_TAG = 18
HDR_ALG = 1
HDR_VDS = 395
HDR_VDP = 396
VDS_RFC9162_SHA256 = 1
PROOF_INCLUSION = -1
ALG_EDDSA = -8
ALG_ES256 = -7


# ── minimal deterministic CBOR (RFC 8949) ──────────────────────────────────────────────────────
def _head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([(major << 5) | n])
    for extra, width in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < (1 << (width * 8)):
            return bytes([(major << 5) | extra]) + n.to_bytes(width, "big")
    raise ValueError("integer too large for CBOR: %d" % n)


def encode(obj) -> bytes:
    """Canonical CBOR for the subset COSE needs. Deterministic: map keys sorted by encoded bytes."""
    if obj is None:
        return b"\xf6"
    if obj is True:
        return b"\xf5"
    if obj is False:
        return b"\xf4"
    if isinstance(obj, int):
        return _head(0, obj) if obj >= 0 else _head(1, -obj - 1)
    if isinstance(obj, bytes):
        return _head(2, len(obj)) + obj
    if isinstance(obj, str):
        b = obj.encode("utf-8")
        return _head(3, len(b)) + b
    if isinstance(obj, list):
        return _head(4, len(obj)) + b"".join(encode(x) for x in obj)
    if isinstance(obj, dict):
        items = sorted(((encode(k), encode(v)) for k, v in obj.items()), key=lambda kv: kv[0])
        return _head(5, len(items)) + b"".join(k + v for k, v in items)
    if isinstance(obj, CBORTag):
        return _head(6, obj.tag) + encode(obj.value)
    raise TypeError("cannot CBOR-encode %r" % type(obj).__name__)


class CBORTag:
    __slots__ = ("tag", "value")

    def __init__(self, tag: int, value):
        self.tag, self.value = tag, value

    def __eq__(self, o):
        return isinstance(o, CBORTag) and (self.tag, self.value) == (o.tag, o.value)

    def __repr__(self):
        return "CBORTag(%d, %r)" % (self.tag, self.value)


def _dec(b: bytes, i: int):
    ib = b[i]
    major, ai = ib >> 5, ib & 0x1F
    i += 1
    if ai < 24:
        n = ai
    elif ai in (24, 25, 26, 27):
        w = 1 << (ai - 24)
        n = int.from_bytes(b[i:i + w], "big")
        i += w
    elif major == 7:
        n = ai
    else:
        raise ValueError("indefinite-length CBOR is not accepted (not deterministic)")
    if major == 0:
        return n, i
    if major == 1:
        return -n - 1, i
    if major == 2:
        return b[i:i + n], i + n
    if major == 3:
        return b[i:i + n].decode("utf-8"), i + n
    if major == 4:
        out = []
        for _ in range(n):
            v, i = _dec(b, i)
            out.append(v)
        return out, i
    if major == 5:
        out = {}
        for _ in range(n):
            k, i = _dec(b, i)
            v, i = _dec(b, i)
            out[k] = v
        return out, i
    if major == 6:
        v, i = _dec(b, i)
        return CBORTag(n, v), i
    if major == 7:
        return {20: False, 21: True, 22: None}.get(ai, None), i
    raise ValueError("unsupported CBOR major type %d" % major)


def decode(b: bytes):
    v, i = _dec(bytes(b), 0)
    if i != len(b):
        raise ValueError("trailing bytes after CBOR item (%d of %d consumed)" % (i, len(b)))
    return v


# ── the receipt ────────────────────────────────────────────────────────────────────────────────
def _sig_structure(protected: bytes, payload: bytes, external_aad: bytes = b"") -> bytes:
    """RFC 9052 section 4.4 Sig_structure for COSE_Sign1 — the exact bytes that get signed."""
    return encode(["Signature1", protected, external_aad, payload])


def inclusion_receipt(tree_size: int, leaf_index: int, path: list[bytes], root: bytes,
                      sign, alg: int = ALG_EDDSA, kid: bytes | None = None,
                      external_aad: bytes = b"") -> bytes:
    """Emit a COSE Receipt of Inclusion. `sign(to_be_signed: bytes) -> bytes` is REQUIRED.

    No unsigned fallback, on purpose and for the same reason `anchor(sign=...)` has none: a receipt
    that is byte-identical whether or not it was signed lets the one property it exists to carry go
    missing without anyone noticing.
    """
    if not callable(sign):
        raise TypeError("inclusion_receipt requires a signer; an unsigned receipt is not a receipt")
    if not 0 <= leaf_index < tree_size:
        raise ValueError("leaf_index %d out of range for tree_size %d" % (leaf_index, tree_size))
    ph = {HDR_ALG: alg, HDR_VDS: VDS_RFC9162_SHA256}
    if kid:
        ph[4] = kid
    protected = encode(ph)
    proof = encode([tree_size, leaf_index, [bytes(p) for p in path]])
    unprotected = {HDR_VDP: {PROOF_INCLUSION: [proof]}}
    sig = sign(_sig_structure(protected, bytes(root), external_aad))
    if not sig:
        raise RuntimeError("signer returned no signature; refusing to emit an unsigned receipt")
    return encode(CBORTag(COSE_SIGN1_TAG, [protected, unprotected, bytes(root), bytes(sig)]))


def verify_receipt(receipt: bytes, verify, leaf_data: bytes | None = None,
                   expected_root: bytes | None = None, external_aad: bytes = b"") -> dict:
    """Check a receipt end to end and say WHICH part failed.

    `verify(to_be_signed, signature) -> bool`. Returns a dict rather than a bool because "the
    signature is good but the proof does not lead to the root you trust" and "the proof is fine but
    signed by a stranger" are different answers and a caller must be able to tell them apart.
    """
    from inspeximus.merkle import verify_inclusion
    out = {"ok": False, "signature_ok": False, "inclusion_ok": False, "root_matches": None,
           "problems": []}
    try:
        tagged = decode(receipt)
        if not isinstance(tagged, CBORTag) or tagged.tag != COSE_SIGN1_TAG:
            out["problems"].append("not a COSE_Sign1 (tag 18)")
            return out
        protected, unprotected, payload, sig = tagged.value
        ph = decode(protected) if protected else {}
        if ph.get(HDR_VDS) != VDS_RFC9162_SHA256:
            out["problems"].append("vds is %r, expected 1 (RFC9162_SHA256)" % ph.get(HDR_VDS))
        proofs = (unprotected or {}).get(HDR_VDP, {}).get(PROOF_INCLUSION) or []
        if not proofs:
            out["problems"].append("no inclusion proof in vdp[-1]")
            return out
        size, index, path = decode(proofs[0])
        out.update(tree_size=size, leaf_index=index, alg=ph.get(HDR_ALG), root=bytes(payload).hex())

        out["signature_ok"] = bool(verify(_sig_structure(protected, bytes(payload), external_aad),
                                          bytes(sig)))
        if not out["signature_ok"]:
            out["problems"].append("signature does not verify")

        if leaf_data is not None:
            out["inclusion_ok"] = verify_inclusion(leaf_data, index, size,
                                                   [bytes(p) for p in path], bytes(payload))
            if not out["inclusion_ok"]:
                out["problems"].append("audit path does not lead to the receipt's root")
        else:
            out["problems"].append("no leaf supplied: inclusion NOT checked")

        if expected_root is not None:
            out["root_matches"] = (bytes(payload) == bytes(expected_root))
            if not out["root_matches"]:
                out["problems"].append("receipt root is not the root you trust")

        out["ok"] = bool(out["signature_ok"] and out["inclusion_ok"]
                         and (expected_root is None or out["root_matches"]))
    except Exception as e:                                    # noqa: BLE001
        out["problems"].append("malformed receipt (%s: %s)" % (type(e).__name__, e))
    return out


def receipt_digest(receipt: bytes) -> str:
    """SHA-256 of the receipt bytes — a stable id to put in a span attribute or an audit bundle."""
    return hashlib.sha256(bytes(receipt)).hexdigest()
