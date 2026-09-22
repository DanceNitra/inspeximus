"""Register one signed statement with the hosted transparency service and verify the receipt here.

The 10.A.4 acceptance: from a client on another machine, POST a COSE Signed Statement over HTTPS,
receive a COSE Receipt (201 + Location), fetch the service's key set from `/.well-known/scitt-keys`,
and verify the receipt's signature and inclusion proof with that public key, offline, with nothing
from the server but the bytes it handed back. A second GET of the entry must return the same
receipt. The control: the receipt must NOT verify under a different key.

    python probes/register_against_the_hosted_log.py https://92.5.74.17.sslip.io

Prints one JSON line and exits non-zero on any failed check. Not run by the suite (it needs the
network and a live service), so it is listed in NOT_STANDALONE by name.
"""
import hashlib
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import cose, scitt  # noqa: E402
from inspeximus.core import new_receipt_keypair  # noqa: E402

COSE = "application/cose"


def _keys(base):
    with urllib.request.urlopen(base + "/.well-known/scitt-keys", timeout=20) as r:
        ks = cose.decode(r.read())
    key = ks["keys"][0]
    return key[-2], key[2].decode("ascii"), ks


def _verifier(pub_bytes):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    pk = Ed25519PublicKey.from_public_bytes(pub_bytes)

    def verify(msg, sig):
        try:
            pk.verify(sig, msg)
            return True
        except Exception:
            return False
    return verify


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else "https://92.5.74.17.sslip.io").rstrip("/")
    sk_hex, pk_hex = new_receipt_keypair()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    isign = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(sk_hex)).sign
    payload = hashlib.sha256(("hosted-log acceptance %s" % time.time()).encode()).digest()
    statement = scitt.signed_statement(payload, "did:web:agora.example:builder", "memory:acceptance", isign)
    req = urllib.request.Request(base + "/entries", data=statement, headers={"Content-Type": COSE}, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=30) as r:
        status, location, ctype, receipt = r.status, r.headers.get("Location"), r.headers.get("Content-Type"), r.read()
    rtt = time.time() - t0
    pub, kid, ks = _keys(base)
    out = cose.verify_receipt(receipt, _verifier(pub))
    with urllib.request.urlopen(base + location, timeout=20) as r:
        again = r.read()
    _sk2, pk2 = new_receipt_keypair()
    control = cose.verify_receipt(receipt, _verifier(bytes.fromhex(pk2)))
    res = {
        "probe": os.path.basename(__file__), "base": base, "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "post_status": status, "location": location, "content_type": ctype, "receipt_bytes": len(receipt),
        "round_trip_s": round(rtt, 3), "service_kid": kid,
        "receipt_verifies_with_service_key": bool(out["ok"]), "problems": out["problems"],
        "entry_get_returns_same_receipt": again == receipt,
        "CONTROL_receipt_rejected_under_another_key": not control["ok"],
        "receipt_sha256": hashlib.sha256(receipt).hexdigest(),
        "service_says": {k: ks.get(k) for k in ks if k not in ("keys",)},
    }
    print(json.dumps(res, default=str))
    ok = (status == 201 and out["ok"] and again == receipt and not control["ok"] and ctype == COSE)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
