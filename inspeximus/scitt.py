"""SCITT Signed Statements (RFC 9943) over our COSE Receipts — zero dependencies.

WHAT THIS ADDS, AND WHY IT IS THE PIECE THAT MATTERED. `cose.py` already emits a Receipt of Inclusion
that any RFC 9942 implementation can check. A receipt proves a leaf is in a log. It says nothing about
WHO put it there or WHAT it is about, so a third party holding one still has to take our word for the
rest. RFC 9943 closes that: a Signed Statement is a COSE_Sign1 whose protected header names the Issuer
and the Subject, and a Transparent Statement is that statement carrying its Receipt. Those two are the
artifacts an auditor can be handed, because their meaning comes from the RFC rather than from us.

READ FROM THE RFC, NOT REMEMBERED (RFC 9943, sections 6 and 7):

    Signed Statement    COSE_Sign1 [STD96]
      protected   1   : alg                       REQUIRED
                 15   : CWT_Claims                REQUIRED, per RFC 9597 section 2
                          1 : issuer              REQUIRED
                          2 : subject             REQUIRED
      payload         : the Statement
    Transparent Statement
      unprotected 394 : receipts = [ <<Receipt>>, ... ]

    "The protected header of a Signed Statement and a Receipt MUST include the `CWT Claims` header
     parameter ... The `CWT Claims` value MUST include the `Issuer Claim` (Claim label 1) and the
     `Subject Claim` (Claim label 2)."

WHAT GOES IN THE PAYLOAD, and this is a design choice rather than a rule. A Signed Statement travels:
to an auditor, to a regulator, into a transparency log that may be public. So the payload here is a
DIGEST of the record, never its text. A statement that carries the memory itself would leak the
content the store exists to govern, in the one artifact most likely to be copied.

WHAT THIS IS NOT. Emitting conformant Signed Statements does not make inspeximus a Transparency
Service. RFC 9943 section 5.1.1 requires a Transparency Service to publish a Registration Policy, to
apply it at registration time, to maintain trust anchors, and to register before releasing a Receipt.
None of those live here yet, and calling this a Transparency Service before they do would be the exact
overclaim the rest of this package refuses to make.
"""
from __future__ import annotations

import hashlib

from .cose import (ALG_EDDSA, ALG_ES256, COSE_SIGN1_TAG, CBORTag, HDR_ALG,  # noqa: F401
                   _sig_structure, decode, encode)

__all__ = ["signed_statement", "verify_signed_statement", "transparent_statement",
           "verify_transparent_statement",
           "receipts_of", "statement_digest", "without_receipts",
           "HDR_CWT_CLAIMS", "HDR_RECEIPTS", "CWT_ISSUER", "CWT_SUBJECT"]

#: RFC 9597 section 2 registers the CWT Claims header parameter at label 15.
HDR_CWT_CLAIMS = 15
#: RFC 9943 section 7: Receipts ride in the unprotected header at label 394.
HDR_RECEIPTS = 394
CWT_ISSUER = 1
CWT_SUBJECT = 2


def signed_statement(payload: bytes, issuer: str, subject: str, sign,
                     alg: int = ALG_EDDSA, kid: bytes | None = None,
                     content_type=None, extra_claims: dict | None = None,
                     external_aad: bytes = b"") -> bytes:
    """Emit a Signed Statement (RFC 9943 section 6). `sign(to_be_signed) -> bytes` is REQUIRED.

    `issuer` identifies who is making the statement and `subject` identifies what it is about. Both
    are mandatory in the RFC, and both are refused here when empty rather than defaulted: a statement
    whose issuer is a placeholder is worse than an unsigned one, because it looks attributable.

    There is no unsigned path, for the reason `inclusion_receipt` has none. An artifact that is
    byte-identical whether or not it was signed lets the property it exists to carry go missing with
    nothing to notice it.
    """
    if not callable(sign):
        raise TypeError("signed_statement requires a signer; an unsigned statement is not a statement")
    if not isinstance(issuer, str) or not issuer.strip():
        raise ValueError("RFC 9943 requires an Issuer claim; pass the identity making this statement")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("RFC 9943 requires a Subject claim; pass what this statement is about")
    if payload is None:
        raise ValueError("a Signed Statement needs a payload; pass the digest this statement covers")

    claims = {CWT_ISSUER: issuer, CWT_SUBJECT: subject}
    if extra_claims:
        overlap = set(extra_claims) & {CWT_ISSUER, CWT_SUBJECT}
        if overlap:
            raise ValueError("extra_claims may not overwrite the issuer or subject claim: %s" % sorted(overlap))
        claims.update(extra_claims)

    ph = {HDR_ALG: alg, HDR_CWT_CLAIMS: claims}
    if kid:
        ph[4] = kid
    if content_type is not None:
        ph[3] = content_type
    protected = encode(ph)
    sig = sign(_sig_structure(protected, bytes(payload), external_aad))
    if not sig:
        raise RuntimeError("signer returned no signature; refusing to emit an unsigned statement")
    return encode(CBORTag(COSE_SIGN1_TAG, [protected, {}, bytes(payload), bytes(sig)]))


def verify_signed_statement(statement: bytes, verify, expected_issuer: str | None = None,
                            expected_subject: str | None = None, external_aad: bytes = b"") -> dict:
    """Check a Signed Statement and say WHICH part failed.

    Returns {ok, signature_ok, issuer, subject, claims, alg, payload, problems}. A dict rather than a
    bool because "signed by a stranger" and "signed correctly but about something else" are different
    answers, and a caller that cannot tell them apart will act on the wrong one.

    `expected_issuer` and `expected_subject` are checked only when supplied. Reading the issuer out of
    the statement and reporting that it matches itself would be a check that cannot fail, so the
    caller has to name what it expects for the comparison to mean anything.
    """
    out = {"ok": False, "signature_ok": False, "issuer": None, "subject": None,
           "claims": None, "alg": None, "payload": None, "problems": []}
    try:
        tagged = decode(statement)
        if not isinstance(tagged, CBORTag) or tagged.tag != COSE_SIGN1_TAG:
            out["problems"].append("not a COSE_Sign1 (tag 18)")
            return out
        protected, _unprotected, payload, sig = tagged.value
        ph = decode(protected) if protected else {}
        out["alg"] = ph.get(HDR_ALG)

        claims = ph.get(HDR_CWT_CLAIMS)
        if not isinstance(claims, dict):
            out["problems"].append(
                "no CWT Claims in the protected header (label 15): RFC 9943 requires it, so this is a "
                "COSE_Sign1 but not a Signed Statement")
            return out
        out["claims"] = claims
        out["issuer"] = claims.get(CWT_ISSUER)
        out["subject"] = claims.get(CWT_SUBJECT)
        if not out["issuer"]:
            out["problems"].append("CWT Claims carries no Issuer (claim 1), which RFC 9943 requires")
        if not out["subject"]:
            out["problems"].append("CWT Claims carries no Subject (claim 2), which RFC 9943 requires")

        if payload is None:
            out["problems"].append("the payload is detached, so there is nothing to verify the "
                                   "signature over")
            return out
        out["payload"] = bytes(payload)
        out["signature_ok"] = bool(verify(_sig_structure(protected, bytes(payload), external_aad),
                                          bytes(sig)))
        if not out["signature_ok"]:
            out["problems"].append("signature does not verify")

        if expected_issuer is not None and out["issuer"] != expected_issuer:
            out["problems"].append("issuer is %r, not the %r you expected" % (out["issuer"], expected_issuer))
        if expected_subject is not None and out["subject"] != expected_subject:
            out["problems"].append("subject is %r, not the %r you expected" % (out["subject"], expected_subject))

        out["ok"] = bool(out["signature_ok"] and out["issuer"] and out["subject"]
                         and (expected_issuer is None or out["issuer"] == expected_issuer)
                         and (expected_subject is None or out["subject"] == expected_subject))
    except Exception as e:                                    # noqa: BLE001
        out["problems"].append("malformed statement (%s: %s)" % (type(e).__name__, e))
    return out


def transparent_statement(statement: bytes, receipts) -> bytes:
    """Attach Receipts to a Signed Statement, producing a Transparent Statement (RFC 9943 section 7).

    The Receipts go in the UNPROTECTED header at label 394, which is what lets them be added after the
    fact without breaking the Issuer's signature: the statement is signed by whoever made the claim,
    and the receipt is issued by whoever logged it. Two parties, two signatures, one artifact.
    """
    rs = [bytes(r) for r in (receipts or [])]
    if not rs:
        raise ValueError("a Transparent Statement carries at least one Receipt; got none")
    tagged = decode(statement)
    if not isinstance(tagged, CBORTag) or tagged.tag != COSE_SIGN1_TAG:
        raise ValueError("not a COSE_Sign1 (tag 18), so it cannot be made transparent")
    protected, unprotected, payload, sig = tagged.value
    up = dict(unprotected or {})
    up[HDR_RECEIPTS] = list(up.get(HDR_RECEIPTS, [])) + rs
    return encode(CBORTag(COSE_SIGN1_TAG, [protected, up, payload, sig]))


def receipts_of(statement: bytes) -> list:
    """The Receipts carried by a Transparent Statement, or an empty list for a bare Signed Statement."""
    tagged = decode(statement)
    if not isinstance(tagged, CBORTag) or tagged.tag != COSE_SIGN1_TAG:
        return []
    _protected, unprotected, _payload, _sig = tagged.value
    return [bytes(r) for r in (unprotected or {}).get(HDR_RECEIPTS, [])]


def verify_transparent_statement(statement: bytes, verify_statement, verify_receipt_sig,
                                 leaf: bytes, expected_root: bytes,
                                 expected_issuer: str | None = None,
                                 expected_subject: str | None = None) -> dict:
    """Check a Signed Statement AND its Receipt AND that the two are about the same record.

    That third check is the one worth writing down. A statement and a receipt verify independently:
    the first says an Issuer vouched for a digest, the second says a leaf sits in a log. Nothing in
    either connects them, so a pair assembled from two unrelated records passes both checks and means
    nothing. The link is that the statement's payload MUST be the SHA-256 of the leaf the receipt
    covers, and this is where it is enforced.

    `leaf` and `expected_root` come from the verifier, not from the artifact. A receipt checked
    against the root it carries proves nothing, and the same is true of a binding checked against a
    leaf the artifact supplied.

    Returns {ok, statement, receipt, bound, problems}.
    """
    from . import cose
    out = {"ok": False, "statement": None, "receipt": None, "bound": None, "problems": []}

    st = verify_signed_statement(statement, verify_statement,
                                 expected_issuer=expected_issuer, expected_subject=expected_subject)
    out["statement"] = st
    out["problems"] += ["statement: " + p for p in st["problems"]]

    receipts = receipts_of(statement)
    if not receipts:
        out["problems"].append("no Receipt: this is a Signed Statement, not a Transparent one")
        return out
    rc = cose.verify_receipt(receipts[0], verify_receipt_sig, leaf_data=bytes(leaf),
                             expected_root=bytes(expected_root))
    out["receipt"] = rc
    out["problems"] += ["receipt: " + p for p in rc["problems"]]

    expected_payload = hashlib.sha256(bytes(leaf)).digest()
    out["bound"] = st.get("payload") == expected_payload
    if not out["bound"]:
        # NAME THE LIKELY CAUSE BEFORE ACCUSING THE ARTIFACT. There are two bindings in this package,
        # and a caller who reaches for the wrong one used to be told the pair was "about different
        # records" -- which can simply be untrue. A service-issued pair IS about one record; its leaf
        # is a registration entry rather than the record, so this rule asks the wrong question of it.
        # Asserting a falsehood is worse than refusing, so look before saying it.
        if _looks_like_a_registration_entry(leaf):
            out["problems"].append(
                "this leaf is a Transparency Service registration entry, not the record itself, so "
                "the store-issued binding does not apply. Use "
                "inspeximus.transparency.verify_registered_statement for a service-issued pair.")
        else:
            out["problems"].append(
                "the statement's payload is not the SHA-256 of the leaf this receipt covers, so the "
                "two are about different records and neither one supports the other")

    out["ok"] = bool(st["ok"] and rc["ok"] and out["bound"])
    return out


def _looks_like_a_registration_entry(leaf: bytes) -> bool:
    """Is this leaf a Transparency Service log entry rather than a record?

    Used only to improve a refusal message, never to decide whether something verifies. A shape test
    that changed a VERDICT would be a way to talk the verifier into the answer you wanted; changing
    only the explanation cannot be exploited into a pass.
    """
    try:
        import json as _json
        entry = _json.loads(bytes(leaf).decode("utf-8"))
    except Exception:
        return False
    return isinstance(entry, dict) and "statement_sha256" in entry and "kind" in entry


def statement_digest(statement: bytes) -> str:
    """SHA-256 of the statement bytes — a stable id for an audit bundle or a log line."""
    return hashlib.sha256(bytes(statement)).hexdigest()


def without_receipts(statement: bytes) -> bytes:
    """The Signed Statement as the Issuer made it, with any Receipts removed.

    A Transparency Service registers the ISSUER'S statement and records its digest. Receipts are then
    attached to the unprotected header by other parties, which changes the bytes. So the artifact that
    travels never hashes to the value the log recorded, and a verifier comparing the two finds a
    mismatch that means nothing.

    Stripping is the right direction rather than recording the digest of the transparent form: a
    statement can collect a SECOND receipt from another log, and any identity that changes when
    somebody else countersigns is not an identity.
    """
    tagged = decode(statement)
    if not isinstance(tagged, CBORTag) or tagged.tag != COSE_SIGN1_TAG:
        return bytes(statement)
    protected, unprotected, payload, sig = tagged.value
    up = {k: v for k, v in (unprotected or {}).items() if k != HDR_RECEIPTS}
    return encode(CBORTag(COSE_SIGN1_TAG, [protected, up, payload, sig]))
