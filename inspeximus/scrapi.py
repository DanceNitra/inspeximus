"""SCITT Reference API (SCRAPI) over our Transparency Service — stdlib only, no new dependency.

WHAT THIS TURNS US INTO. `transparency.py` is a conformant Transparency Service that can only be
reached by writing Python against it. SCRAPI is the interface a SCITT client already speaks, so this
is the difference between a library somebody has to learn and a service somebody can point a tool at.

    python -m inspeximus.scrapi --port 9800 --log ts.jsonl --issuer-pubkey <hex> [--secret <hex>]

THE ENDPOINTS, read from draft-ietf-scitt-scrapi-11, not remembered:

    GET  /.well-known/scitt-keys              -> application/cbor, a COSE Key Set (200)
    GET  /.well-known/scitt-keys/{kid}        -> application/cbor, one key (200 / 404)
    POST /entries          application/cose   -> application/cose, the Receipt (201 / 400 / 429)
    GET  /entries/{EntryID} Accept: cose      -> application/cose, the Receipt (200 / 204 / 404)

Errors are `application/concise-problem-details+cbor`, which is what the draft specifies, and NOT the
JSON problem type that a reader used to HTTP APIs expects. Emitting JSON there would be the sort of
"close enough" that makes a conformance claim false while every hand-written client keeps working.

TWO THINGS THIS SERVER REFUSES TO PRETEND.

It does not accept a statement it cannot authenticate. The issuer's public key must be configured, so
an operator who starts the server without one gets a refusal at startup rather than a log full of
entries whose signatures were never checked.

It serves the CURRENT root with every receipt, and says in `/.well-known/scitt-keys` that a root you
got from the same party you are auditing proves only self-consistency. Non-equivocation needs a
witness, which lives in `witness_server.py` and is a different host on purpose.

Bind to 127.0.0.1 by default. Put your own TLS in front of it for anything real; the draft assumes
HTTPS and this module does not implement it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import cose, scitt
from .transparency import RegistrationPolicy, RegistrationRefused, TransparencyService

__all__ = ["make_server", "main", "CBOR", "COSE", "PROBLEM"]

COSE = "application/cose"
CBOR = "application/cbor"
PROBLEM = "application/concise-problem-details+cbor"

#: RFC 9290 concise problem details: the title lives at key -1, the detail at -2.
_PROBLEM_TITLE = -1
_PROBLEM_DETAIL = -2


def _problem(title: str, detail: str = "") -> bytes:
    body = {_PROBLEM_TITLE: title}
    if detail:
        body[_PROBLEM_DETAIL] = detail
    return cose.encode(body)


def make_server(service: TransparencyService, host: str = "127.0.0.1", port: int = 9800):
    """Build (but do not start) a SCRAPI server over an existing Transparency Service."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "inspeximus-scrapi/1.0"

        def log_message(self, fmt, *args):        # quiet by default; the log IS the ledger
            pass

        def _send(self, code: int, ctype: str, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _accepts(self, ctype: str) -> bool:
            """An absent Accept header means the client will take anything, which is what RFC 9110
            says and what a curl user does. Refusing it would fail conformance for the commonest
            client there is."""
            accept = self.headers.get("Accept")
            return (not accept) or ("*/*" in accept) or (ctype in accept)

        # ------------------------------------------------------------------ GET
        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"

            if path == "/.well-known/scitt-keys":
                if not self._accepts(CBOR):
                    return self._send(406, PROBLEM, _problem("not acceptable",
                                                             "this endpoint serves " + CBOR))
                return self._send(200, CBOR, cose.encode(self._key_set()))

            if path.startswith("/.well-known/scitt-keys/"):
                kid = path.rsplit("/", 1)[-1]
                if kid != (service.service_pubkey or ""):
                    return self._send(404, PROBLEM, _problem("unknown kid", kid))
                return self._send(200, CBOR, cose.encode(self._key_set()))

            if path.startswith("/entries/"):
                raw = path.rsplit("/", 1)[-1]
                try:
                    index = int(raw)
                except ValueError:
                    return self._send(404, PROBLEM, _problem("no such entry", raw))
                if not 0 <= index < service.size():
                    return self._send(404, PROBLEM, _problem("no such entry", raw))
                receipt = service.receipt_for(index)
                if receipt is None:
                    # 204 is the draft's "registered, receipt not ready", and it is NOT 404: the
                    # difference is "ask again" versus "this never existed".
                    return self._send(204, PROBLEM, b"")
                return self._send(200, COSE, receipt)

            return self._send(404, PROBLEM, _problem("no such resource", path))

        # ------------------------------------------------------------------ POST
        def do_POST(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path != "/entries":
                return self._send(404, PROBLEM, _problem("no such resource", path))
            # DRAIN THE BODY BEFORE ANSWERING, even when the answer is a refusal. Responding while
            # the client is still sending closes the socket mid-write, and the client sees a
            # connection reset instead of the 400 that explains what to fix. Measured on Windows,
            # where the reset is immediate; on other platforms it is a race that shows up later.
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            statement = self.rfile.read(n) if n > 0 else b""

            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != COSE:
                return self._send(400, PROBLEM, _problem("wrong content type",
                                                         "a Signed Statement is " + COSE))
            if not statement:
                return self._send(400, PROBLEM, _problem("empty body", "expected a COSE_Sign1"))
            try:
                receipt = service.register(statement)
            except RegistrationRefused as e:
                # The policy's reasons go to the client verbatim. A refusal a caller cannot act on
                # produces a retry loop against a decision that will never change.
                return self._send(400, PROBLEM, _problem("registration refused", str(e)))
            except Exception as e:                                    # noqa: BLE001
                return self._send(400, PROBLEM, _problem("malformed statement", type(e).__name__))
            self.send_response(201)
            self.send_header("Content-Type", COSE)
            self.send_header("Location", "/entries/%d" % (service.size() - 1))
            self.send_header("Content-Length", str(len(receipt)))
            self.end_headers()
            self.wfile.write(receipt)

        def _key_set(self):
            d = service.describe()
            return {"keys": [{"kid": service.service_pubkey, "alg": "EdDSA"}],
                    "policy": d["policy"], "policy_sha256": d["policy_sha256"],
                    "entries": d["entries"], "root": d["root"], "scope": d["scope"]}

    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m inspeximus.scrapi", description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=9800)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--log", required=True, help="path to the append-only registration log")
    ap.add_argument("--secret", help="the SERVICE Ed25519 secret (hex); minted and printed if absent")
    ap.add_argument("--issuer-pubkey", action="append", default=[],
                    help="an Ed25519 public key (hex) this service accepts statements from; "
                         "repeatable. REQUIRED: a service that cannot authenticate an issuer is "
                         "recording bytes, not statements")
    ap.add_argument("--policy-name", default="scrapi-default")
    ap.add_argument("--accept-any-issuer", action="store_true",
                    help="admit statements from any issuer. The policy then SAYS so, and every "
                         "receipt means only that something was recorded")
    a = ap.parse_args(argv)

    if not a.issuer_pubkey and not a.accept_any_issuer:
        print("refusing to start: pass --issuer-pubkey (repeatable), or --accept-any-issuer to say "
              "out loud that this service vets nobody", file=sys.stderr)
        return 2

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (Ed25519PrivateKey as SK,
                                                                   Ed25519PublicKey as PK)
    from . import new_receipt_keypair
    secret = a.secret
    if not secret:
        secret, pub = new_receipt_keypair()
        print("minted a service key. KEEP IT: --secret %s" % secret, file=sys.stderr)
    sk = SK.from_private_bytes(bytes.fromhex(secret))
    pub = sk.public_key().public_bytes_raw().hex()

    anchors = [PK.from_public_bytes(bytes.fromhex(h)) for h in a.issuer_pubkey]

    def verify_issuer(msg, sig):
        for k in anchors:
            try:
                k.verify(sig, msg)
                return True
            except Exception:
                continue
        # With no anchors the policy is open by declaration, and the signature still has to be a
        # signature; we simply cannot say whose. Accepting it here is what --accept-any-issuer means.
        return bool(a.accept_any_issuer)

    policy = RegistrationPolicy(a.policy_name, accepted_issuers=[] if a.accept_any_issuer else None)
    service = TransparencyService(a.log, policy, sk.sign, verify_issuer, service_pubkey=pub)
    httpd = make_server(service, a.host, a.port)
    print("SCRAPI on http://%s:%d  log=%s  entries=%d  service_pubkey=%s"
          % (a.host, a.port, a.log, service.size(), pub), file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
