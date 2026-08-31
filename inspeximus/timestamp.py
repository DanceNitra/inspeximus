"""RFC 3161 timestamps over a log root — stdlib only, with an honest boundary.

WHY A TIMESTAMP AT ALL. Everything else here proves ORDER: this entry is in the log, this log extends
that one. None of it proves WHEN, because every clock in the system belongs to the operator being
audited. A Time-Stamping Authority is a third party whose whole business is saying "these bytes
existed at this moment", and under eIDAS Article 41 a QUALIFIED timestamp from an EU Trusted List
provider carries a rebuttable presumption of the time it shows. That is the cheapest credibility an
auditor already recognises, and we cannot manufacture it ourselves at any price.

WHAT THIS DOES, EXACTLY:

    request_bytes(digest)     a DER TimeStampReq over your hash, with a nonce         (complete)
    stamp(url, digest)        POST it, return the raw TimeStampResp token             (complete)
    read_status(token)        the PKIStatus, so a rejection is not stored as proof    (complete)
    verify_with_openssl(...)  shells out to `openssl ts -verify`                      (delegated)

WHERE THIS STOPS, AND WHY IT SAYS SO INSTEAD OF PRETENDING. Verifying a TimeStampResp means verifying
a CMS SignedData over a TSTInfo, which means an X.509 chain, a trust store and a full ASN.1 stack.
This package has zero dependencies, and a hand-rolled partial CMS parser that reported "valid" would
be worse than no verifier: it would be a check that passes on tokens a real verifier rejects.

So the token is stored VERBATIM and handed to the tool an auditor already uses. `openssl ts -verify`
is not a shortcut around the work; it is the work, done by an implementation that is maintained,
audited and installed on the auditor's machine already.

WHAT A STORED TOKEN IS WORTH. It says a TSA saw this digest at this time. It does NOT say the digest
is a log root, that the log is honest, or that the operator did not also timestamp four other
histories. Bind it to the head (`stamp_head`) so the digest is one a verifier can recompute, and pair
it with witnesses for the part a timestamp cannot reach.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import subprocess
import tempfile
import urllib.request

__all__ = ["request_bytes", "stamp", "read_status", "verify_with_openssl", "stamp_head",
           "certificates_in", "signer_certificate", "qualified_status",
           "TimestampError", "PKI_STATUS"]

CONTENT_TYPE_REQ = "application/timestamp-query"
CONTENT_TYPE_RESP = "application/timestamp-reply"

#: RFC 3161 section 2.4.2. Anything other than 0 or 1 means no usable token came back.
PKI_STATUS = {0: "granted", 1: "grantedWithMods", 2: "rejection", 3: "waiting",
              4: "revocationWarning", 5: "revocationNotification"}

_OID_SHA256 = bytes([0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01])


class TimestampError(Exception):
    """The TSA did not return a usable token. Distinct from a transport error, because "the authority
    refused" and "the network failed" call for different actions."""


# ── minimal DER, only what a TimeStampReq needs ────────────────────────────────────────────────────
def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _len(len(body)) + body


def _int(n: int) -> bytes:
    b = n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big")
    return _tlv(0x02, b)


def request_bytes(digest: bytes, nonce: int | None = None, cert_req: bool = True) -> bytes:
    """A DER TimeStampReq (RFC 3161 section 2.4.1) over `digest`, which must be a SHA-256.

    The nonce is REQUIRED in practice even though the field is optional: without it a TSA, or anyone
    between you and it, can return a token minted earlier for the same digest, and you cannot tell a
    fresh answer from a replayed one. It is random per call and returned so a caller can pin it.

    `cert_req=True` asks the TSA to include its certificate in the response, which is what makes the
    token verifiable later by somebody who does not already hold that certificate.
    """
    if not isinstance(digest, (bytes, bytearray)) or len(digest) != 32:
        raise ValueError("expected a 32-byte SHA-256 digest; a timestamp over anything else is a "
                         "timestamp over something you cannot recompute")
    nonce = secrets.randbits(64) if nonce is None else int(nonce)
    algid = _tlv(0x30, _tlv(0x06, _OID_SHA256) + _tlv(0x05, b""))       # sha256 + NULL params
    imprint = _tlv(0x30, algid + _tlv(0x04, bytes(digest)))
    body = _int(1) + imprint + _int(nonce) + _tlv(0x01, b"\xff" if cert_req else b"\x00")
    return _tlv(0x30, body)


def read_status(token: bytes) -> dict:
    """The PKIStatus at the head of a TimeStampResp, without parsing the token itself.

    This is deliberately shallow and says so. It answers one question: did the authority GRANT a
    timestamp, or refuse one? Storing a rejection as though it were proof is the failure worth
    catching here, and it is catchable from the first few bytes.
    """
    out = {"status": None, "status_text": None, "has_token": False, "problems": []}
    try:
        if not token or token[0] != 0x30:
            out["problems"].append("not a DER SEQUENCE, so not a TimeStampResp")
            return out
        # TimeStampResp ::= SEQUENCE { status PKIStatusInfo, timeStampToken ContentInfo OPTIONAL }
        i = 1
        i += 1 if token[i] < 0x80 else 1 + (token[i] & 0x7F)            # skip outer length
        if token[i] != 0x30:
            out["problems"].append("no PKIStatusInfo")
            return out
        j = i + 1
        n = token[j]
        j += 1 if n < 0x80 else 1 + (n & 0x7F)
        if token[j] != 0x02:
            out["problems"].append("PKIStatus is not an INTEGER")
            return out
        ln = token[j + 1]
        value = int.from_bytes(token[j + 2:j + 2 + ln], "big")
        out["status"] = value
        out["status_text"] = PKI_STATUS.get(value, "unknown(%d)" % value)
        out["has_token"] = value in (0, 1)
        if not out["has_token"]:
            out["problems"].append("the authority did not grant a timestamp: %s" % out["status_text"])
    except Exception as e:                                              # noqa: BLE001
        out["problems"].append("could not read the status (%s)" % type(e).__name__)
    return out


def stamp(url: str, digest: bytes, timeout: float = 20.0, nonce: int | None = None) -> dict:
    """Ask a TSA to timestamp `digest`. Returns {token, status, url, digest, requested_utc}.

    Raises TimestampError when the authority answers without granting one, rather than returning a
    dict a caller might store: a rejection filed alongside real tokens is worse than a missing one,
    because it looks like evidence.
    """
    import time
    req = request_bytes(digest, nonce=nonce)
    request = urllib.request.Request(url, data=req,
                                     headers={"Content-Type": CONTENT_TYPE_REQ}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as r:
        token = r.read()
    st = read_status(token)
    if not st["has_token"]:
        raise TimestampError("%s: %s" % (url, "; ".join(st["problems"]) or "no token"))
    return {"token": token, "status": st, "url": url, "digest": bytes(digest).hex(),
            "requested_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def verify_with_openssl(token: bytes, digest: bytes, ca_file: str | None = None,
                        openssl: str | None = None) -> dict:
    """Verify a token with `openssl ts -verify`, the implementation an auditor already trusts.

    Delegated on purpose. A verifier for this needs CMS, X.509 path building and a trust store; a
    partial hand-rolled one that answered "valid" would pass tokens a real verifier rejects, which is
    the worst possible direction for a check whose entire job is to be believed by somebody else.

    Without `ca_file` openssl cannot complete a chain, so the result reports UNVERIFIED rather than
    failure: "I could not check this" and "this is bad" are different answers, and a caller that
    cannot tell them apart will act on the wrong one.
    """
    exe = openssl or shutil.which("openssl")
    out = {"ran": False, "verified": None, "returncode": None, "output": "", "problems": []}
    if not exe:
        out["problems"].append("openssl is not on PATH, so this token was NOT verified here")
        return out
    if not ca_file:
        out["problems"].append("no CA file given: a TSA token cannot be verified without the trust "
                               "anchor its chain leads to")
        return out
    if not os.path.exists(ca_file):
        # openssl exits non-zero for a missing CA file exactly as it does for a bad token, so without
        # this the result would say verified=False and a reader would conclude the timestamp is
        # forged. Checking the input first keeps "I could not check" out of the verdict.
        out["problems"].append("the CA file %r does not exist, so nothing was verified" % ca_file)
        return out
    d = tempfile.mkdtemp()
    try:
        tp, dp = os.path.join(d, "t.tsr"), os.path.join(d, "d.bin")
        with open(tp, "wb") as fh:
            fh.write(bytes(token))
        with open(dp, "wb") as fh:
            fh.write(bytes(digest))
        r = subprocess.run([exe, "ts", "-verify", "-digest", bytes(digest).hex(),
                            "-in", tp, "-CAfile", ca_file],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=60)
        out.update(ran=True, returncode=r.returncode,
                   output=((r.stdout or "") + (r.stderr or "")).strip()[:2000])
        out["verified"] = (r.returncode == 0)
        if not out["verified"]:
            out["problems"].append("openssl did not verify this token")
    except Exception as e:                                              # noqa: BLE001
        out["problems"].append("could not run openssl (%s)" % type(e).__name__)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return out


# ── who signed it, and were they qualified at the time ─────────────────────────────────────────────
def _tlvs(buf, start, end):
    """Walk one DER level, yielding (tag, tlv_start, body_start, body_end).

    Stops at the first malformed tag rather than raising: this walks a structure to LOOK for
    certificates, and a region that does not parse is a region with no certificate in it.

    `tlv_start` is yielded rather than recomputed by the caller, because a certificate must be
    handed on as its COMPLETE encoding, header included. Its SHA-256 is what a trusted list stores,
    so a body-only slice would hash to something that matches nothing and would report every
    authority in Europe as unlisted.
    """
    i = start
    while i < end:
        tlv_start = i
        tag = buf[i]
        j = i + 1
        if j >= end:
            return
        n = buf[j]
        j += 1
        if n & 0x80:
            k = n & 0x7F
            if k == 0 or k > 4 or j + k > end:
                return
            n = int.from_bytes(buf[j:j + k], "big")
            j += k
        if j + n > end:
            return
        yield tag, tlv_start, j, j + n
        i = j + n


def _is_certificate(buf, start, end):
    """Whether this SEQUENCE has the shape of an X.509 Certificate.

    RFC 5280: Certificate ::= SEQUENCE { tbsCertificate SEQUENCE, signatureAlgorithm SEQUENCE,
    signatureValue BIT STRING }. Checking the shape, rather than scanning for `30 82` and hoping,
    is what keeps a signature blob or a nested structure from being reported as a certificate.
    """
    parts = list(_tlvs(buf, start, end))
    if len(parts) != 3:
        return False
    return parts[0][0] == 0x30 and parts[1][0] == 0x30 and parts[2][0] == 0x03    # tbs, algid, sig


def certificates_in(token, _limit=64):
    """Every X.509 certificate embedded in a TimeStampResp, as DER bytes, outermost first.

    A TSA includes its certificate when the request asked for one (`cert_req=True`, our default), so
    a stored token usually carries the signer and its chain. Without this a caller holds a token and
    has no way to name who signed it, which makes a Trusted List check impossible on the evidence
    they actually kept.

    This finds certificates by structure and does NOT verify anything. A certificate found here is a
    certificate the token CONTAINS, not one that signed it: an attacker controls the bytes of their
    own token and can embed whatever they like. Pair it with `verify_with_openssl`, which is the part
    that decides whether the signature is real.
    """
    buf = bytes(token)
    found, seen = [], set()

    def walk(start, end, depth):
        if depth > 20 or len(found) >= _limit:
            return
        for tag, ts, bs, be in _tlvs(buf, start, end):
            if tag == 0x30 and _is_certificate(buf, bs, be):
                der = buf[ts:be]
                if der not in seen:
                    seen.add(der)
                    found.append(der)
                continue                                    # a certificate holds no further ones
            if tag & 0x20:                                  # constructed, so descend
                walk(bs, be, depth + 1)

    walk(0, len(buf), 0)
    return found


def signer_certificate(token):
    """The certificate most likely to be the TSA's own signer, or None.

    Chooses the one carrying the id-kp-timeStamping extended key usage (OID 1.3.6.1.5.5.7.3.8), which
    RFC 3161 section 2.3 REQUIRES on a TSA's signing certificate and forbids combining with any other
    purpose. That is a property of the role rather than of position in the file, so it survives a
    chain arriving in whatever order the authority chose to send it.

    Returns None when no embedded certificate carries it, rather than falling back to the first one.
    A guessed signer would flow straight into a Trusted List lookup and produce a qualified-or-not
    verdict about the wrong certificate, which is worse than no verdict.
    """
    want = bytes([0x06, 0x08, 0x2B, 0x06, 0x01, 0x05, 0x05, 0x07, 0x03, 0x08])
    for der in certificates_in(token):
        if want in der:
            return der
    return None


def qualified_status(token, trusted_list, when=None):
    """Was the authority that signed this token a QUALIFIED timestamp service at `when`?

    `when` defaults to now, which is almost always the WRONG question for a stored token and is only
    the default because a caller checking a token they just obtained has no other date in hand. Pass
    the moment the token was made. Qualified standing is held on a date: measured across 25 national
    lists on 2026-08-31, 570 of 1477 qualified timestamp services, 39 percent, have held both a
    qualified and a non-qualified status at different times.

    The verdict carries `signer_found`, because "not on the list" and "I could not tell who signed
    it" are different answers and only one of them is about the authority.
    """
    der = signer_certificate(token)
    if der is None:
        return {"qualified": None, "verdict": "NO_SIGNER_CERTIFICATE_IN_THE_TOKEN",
                "signer_found": False, "matched": [], "problems": [
                    "no embedded certificate carries the id-kp-timeStamping extended key usage that "
                    "RFC 3161 section 2.3 requires, so this token does not say who signed it. A "
                    "token requested without cert_req looks exactly like this."],
                "scope": "Nothing was checked against any trusted list."}
    out = trusted_list.qualified_at(when, cert_der=der)
    out["signer_found"] = True
    return out


def stamp_head(service, url: str, timeout: float = 20.0, trusted_list=None) -> dict:
    """Timestamp a Transparency Service's head, over a digest a verifier can recompute.

    The digest is the head's `sth_hash`, which commits to the entry count and the Merkle root, so a
    reader who holds the head can rebuild the exact bytes the TSA signed over. Timestamping the root
    alone would leave the count unattested, and timestamping something only we can produce would make
    the token unusable by the person it exists to convince.
    """
    head = service.head()
    digest = bytes.fromhex(head["sth_hash"])
    res = stamp(url, digest, timeout=timeout)
    res["head"] = head
    res["scope"] = ("This token says a Time-Stamping Authority saw this digest at this time. It does "
                    "NOT say the log is honest, and it does not stop an operator timestamping several "
                    "different histories. Pair it with witness co-signatures, which is the check a "
                    "timestamp cannot perform.")
    if trusted_list is not None:
        # Recorded NOW, while the answer is certain, rather than left for whoever reads the token
        # later. Qualified standing is granted and withdrawn over time: 570 of the 1477 qualified
        # timestamp services published across 25 national lists on 2026-08-31 have held both a
        # qualified and a non-qualified status. A reader in three years can still ask the question,
        # but they must first find a list as it stood today, and this saves them that.
        res["qualified"] = qualified_status(res["token"], trusted_list,
                                            when=res["requested_utc"])
    return res
