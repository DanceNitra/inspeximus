"""A c2sp.org/tlog-witness witness: cosign somebody else's checkpoint, and refuse when it moved.

WHY THIS IS THE PRODUCT RATHER THAN A FEATURE. Our own signature on our own log proves nothing about
equivocation, and our published key set says so in words. The signature an auditor wants comes from
somebody who is not the operator. That is a service one party can run for many logs, and it needs
almost nothing from them: no data, no records, no secrets, only the head of the tree and a proof
that it grew from the head we saw last.

WHAT A COSIGNATURE FROM HERE MEANS, exactly: at the stated time, this witness had seen an
append-only sequence of checkpoints for that origin ending at this size and root. It says nothing
about whether any entry is true, nothing about the log's contents, and nothing about a history the
log showed somebody else while we were not looking. The last one is why witnesses are worth running
in numbers rather than alone.

THE CONTRACT is the spec's, and every refusal below is a MUST from it:

  404  the origin is not one we witness
  403  no signature from a key we trust for that origin, or one that fails to verify
  400  the old size is larger than the checkpoint size
  409  the old size is not the size we last cosigned; the body is our size, so the client can catch up
  422  the tree did not grow from what we hold: a bad consistency proof, a different root at the same
       size, a non-empty proof from zero, or a size-zero checkpoint whose root is not the empty hash
  200  a cosignature, after the new head is persisted

THE STATE IS THE WHOLE GUARANTEE. `add_checkpoint` persists the new head BEFORE returning the
signature, and the check-and-write happens under one lock, because the spec's own race note is a
rollback: two requests, the larger persisted first, the smaller overwriting it, and the witness
cosigns a tree that shrank.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import time

from . import checkpoint as cp
from . import merkle

#: The spec's own ceiling. A proof longer than this is not a big tree, it is somebody spending our
#: CPU: 63 hashes already covers a tree of 2**63 leaves.
MAX_PROOF_LINES = 63

#: A request body larger than this is refused unread. The largest legitimate one is a checkpoint,
#: 63 proof lines and a handful of signatures, which is under 8 KiB.
MAX_BODY_BYTES = 16 * 1024

#: The signature type byte for a timestamped Ed25519 cosignature (c2sp.org/tlog-cosignature).
COSIGNATURE_TYPE = 0x04


class Refused(Exception):
    """A refusal carrying the status the spec prescribes, and a body when the spec prescribes one."""

    def __init__(self, status: int, reason: str, body: str = "", content_type: str = "text/plain"):
        super().__init__("%d %s" % (status, reason))
        self.status, self.reason, self.body, self.content_type = status, reason, body, content_type


def cosignature_key_id(name: str, pubkey_hex: str) -> bytes:
    """SHA-256(name || 0x0A || 0x04 || pubkey)[:4]: the 0x04 is what makes it a COSIGNATURE key id.

    Using the checkpoint's 0x01 here would produce an id no verifier computes for a cosignature, so
    every signature this witness makes would be ignored by a conforming client while looking fine to
    us.
    """
    body = name.encode("utf-8") + b"\x0a" + bytes([COSIGNATURE_TYPE]) + bytes.fromhex(pubkey_hex)
    return hashlib.sha256(body).digest()[:4]


def cosigned_message(note_text: str, timestamp: int) -> bytes:
    """`cosignature/v1`, the timestamp line, then the whole checkpoint note text."""
    if timestamp <= 0:
        raise ValueError("the timestamp must not be zero: the spec forbids omitting it")
    return ("cosignature/v1\ntime %d\n%s" % (timestamp, note_text)).encode("utf-8")


def cosign(note_text: str, name: str, secret_hex: str, pubkey_hex: str, timestamp=None) -> str:
    """One signature line: em dash, witness name, base64(key id || u64 timestamp || Ed25519 sig)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    ts = int(timestamp if timestamp is not None else time.time())
    sig = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret_hex)).sign(
        cosigned_message(note_text, ts))
    blob = cosignature_key_id(name, pubkey_hex) + struct.pack(">Q", ts) + sig
    return "— %s %s\n" % (name, base64.b64encode(blob).decode("ascii"))


def verify_cosignature(note_text: str, line: str, name: str, pubkey_hex: str) -> int:
    """-> the timestamp, or raise. The client half, so a customer can check what we sent them."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not line.startswith("— " + name + " "):
        raise ValueError("this line is not a cosignature from %r" % name)
    blob = base64.b64decode(line[2:].split(" ", 1)[1])
    if blob[:4] != cosignature_key_id(name, pubkey_hex):
        raise ValueError("the key id does not match that name and key")
    ts = struct.unpack(">Q", blob[4:12])[0]
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(
        blob[12:], cosigned_message(note_text, ts))
    return ts


def parse_request(body: bytes):
    """-> (old_size, [proof hashes], note). Refuses anything the spec calls malformed.

    The size ceiling is checked by the caller before reading; this one refuses the shapes: a body
    with no blank line, a non-decimal size, more proof lines than the spec allows, base64 that is
    not a 32-byte hash.
    """
    if len(body) > MAX_BODY_BYTES:
        raise Refused(413, "the request body is larger than this witness reads")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused(400, "the request body is not UTF-8")
    head, sep, note = text.partition("\n\n")
    if not sep or not head:
        raise Refused(400, "the request is an old size line, proof lines, a blank line and a checkpoint")
    lines = head.split("\n")
    if not lines[0].startswith("old "):
        raise Refused(400, "the first line must be 'old <size>'")
    digits = lines[0][4:]
    if not digits.isdigit() or (digits != "0" and digits.startswith("0")):
        raise Refused(400, "the old size must be decimal with no leading zeroes")
    proof_lines = [x for x in lines[1:] if x]
    if len(proof_lines) > MAX_PROOF_LINES:
        raise Refused(400, "a consistency proof carries at most %d hashes" % MAX_PROOF_LINES)
    proof = []
    for line in proof_lines:
        try:
            raw = base64.b64decode(line, validate=True)
        except Exception:                                        # noqa: BLE001
            raise Refused(400, "a proof line must be base64")
        if len(raw) != 32:
            raise Refused(400, "a proof hash is 32 bytes")
        proof.append(raw)
    return int(digits), proof, note


class CheckpointWitness:
    """Witness state for many logs, persisted, with the check and the write under one lock.

    `trusted` maps an origin to the log's checkpoint public key, hex. An origin that is not in it is
    not witnessed here, which is a 404 rather than a refusal to sign: a witness is a relationship,
    not an open door.
    """

    def __init__(self, state_path: str, trusted: dict, name: str,
                 secret_hex: str, pubkey_hex: str, max_per_minute: int = 60):
        self.state_path = state_path
        self.trusted = dict(trusted or {})
        self.name, self._secret, self.pubkey = name, secret_hex, pubkey_hex
        self.max_per_minute = int(max_per_minute)
        self._hits: dict = {}

    # -- state ---------------------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:                                        # noqa: BLE001 - a first run has no file
            return {"kind": "inspeximus.witness-checkpoint/1", "logs": {}}

    def _save(self, state: dict) -> None:
        parent = os.path.dirname(os.path.abspath(self.state_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.state_path)

    def latest(self, origin: str) -> dict:
        return self._load()["logs"].get(origin, {"size": 0, "root": ""})

    # -- limits --------------------------------------------------------------------------------
    def _rate_limit(self, origin: str) -> None:
        """A per-origin ceiling per minute. Cheap, in-process, and stated rather than implied.

        A witness answers a cron job, not a crowd: a log that submits more than once a minute is
        either broken or using us as a signing oracle.
        """
        now = time.time()
        hits = [t for t in self._hits.get(origin, []) if now - t < 60.0]
        if len(hits) >= self.max_per_minute:
            raise Refused(429, "too many submissions for this origin in one minute")
        hits.append(now)
        self._hits[origin] = hits

    # -- the protocol --------------------------------------------------------------------------
    def add_checkpoint(self, body: bytes):
        """-> (status, content_type, body). Every branch below is a MUST in the spec."""
        old_size, proof, note = parse_request(body)
        try:
            text, sigs = cp.split_note(note)
        except ValueError as exc:
            raise Refused(400, "the checkpoint is not a signed note: %s" % exc)
        lines = text.split("\n")[:-1]
        if len(lines) < 3:
            raise Refused(400, "a checkpoint has an origin, a size and a root")
        origin = lines[0]
        if origin not in self.trusted:
            raise Refused(404, "this witness does not witness %r" % origin)
        self._rate_limit(origin)

        # 403: no signature from a trusted key, or one that carries our name and does not verify.
        pub = self.trusted[origin]
        # ONE guard, not two. The first version also re-checked `origin not in verified` after
        # asking verify_note to require it, and a mutation run showed why that is worse than it
        # looks: each check absorbed the other's mutant, so disabling either left all 19 tests
        # green. A defence that no test can distinguish from its twin is a defence nobody is
        # measuring.
        try:
            cp.verify_note(note, {origin: pub}, required=[origin])
        except Exception:                                        # noqa: BLE001
            raise Refused(403, "no valid signature from the key this witness trusts for that origin")

        try:
            size = int(lines[1])
            root = base64.b64decode(lines[2], validate=True)
        except Exception:                                        # noqa: BLE001
            raise Refused(400, "the size or the root is malformed")
        if len(root) != 32:
            raise Refused(400, "the root is not a 32-byte hash")
        if size == 0 and root != hashlib.sha256(b"").digest():
            raise Refused(422, "a size-zero checkpoint must carry the empty tree's root")
        if old_size > size:
            raise Refused(400, "the old size is larger than the checkpoint size")

        state = self._load()
        held = state["logs"].get(origin, {"size": 0, "root": ""})
        if old_size != held["size"]:
            raise Refused(409, "the old size is not the one this witness last cosigned",
                          body="%d\n" % held["size"], content_type="text/x.tlog.size")
        if old_size == 0 and proof:
            raise Refused(422, "the empty tree is consistent with any tree, so send no proof")
        if old_size == size:
            # Same size, and therefore the same tree, or the log is showing two histories.
            if held["root"] and bytes.fromhex(held["root"]) != root:
                raise Refused(422, "a different root at the size this witness already cosigned")
        elif old_size > 0:
            if not merkle.verify_consistency_proof(old_size, size, bytes.fromhex(held["root"]),
                                                   root, proof):
                raise Refused(422, "the consistency proof does not show this tree grew from ours")

        # PERSIST BEFORE SIGNING. The spec's race note is a rollback: the larger head written first,
        # the smaller one overwriting it, and a cosignature for a tree that shrank.
        state["logs"][origin] = {"size": size, "root": root.hex(),
                                 "cosigned_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        self._save(state)
        return 200, "text/plain; charset=utf-8", cosign(text, self.name, self._secret, self.pubkey)


def build_request(old_size: int, proof, note: str) -> bytes:
    """The client half: the body an add-checkpoint POST carries."""
    lines = ["old %d" % old_size] + [base64.b64encode(h).decode("ascii") for h in (proof or [])]
    return ("\n".join(lines) + "\n\n" + note).encode("utf-8")


# ---------------------------------------------------------------------------------- the HTTP side
def make_handler(witness: "CheckpointWitness", prefix: str = ""):
    """An http.server handler for POST <prefix>/add-checkpoint. Stdlib only, like the rest.

    The body is read under the same ceiling the parser enforces, because a server that reads first
    and checks after has already spent the memory. Every refusal carries the spec's status and the
    spec's body: a 409 answers with our size in `text/x.tlog.size` so the client can catch up
    without asking a second endpoint.
    """
    import http.server

    route = (prefix.rstrip("/") + "/add-checkpoint") or "/add-checkpoint"

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "inspeximus-witness/1"

        def _send(self, status, ctype, body: bytes):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):                                       # noqa: N802 - http.server's name
            if self.path.rstrip("/") != route.rstrip("/"):
                return self._send(404, "text/plain", b"no such endpoint\n")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send(400, "text/plain", b"a Content-Length is required\n")
            if length > MAX_BODY_BYTES:
                # Refuse without reading it all, but drain a bounded amount first. A server that
                # answers and closes mid-upload hands the client a connection reset instead of the
                # 413 it was told about, which turns a clear refusal into "the witness is broken".
                # Measured on the first run of the test below, which failed on the reset.
                self.close_connection = True
                remaining = min(length, MAX_BODY_BYTES * 4)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                return self._send(413, "text/plain", b"the request body is larger than this witness reads\n")
            body = self.rfile.read(length)
            try:
                status, ctype, out = witness.add_checkpoint(body)
            except Refused as r:
                return self._send(r.status, r.content_type, (r.body or (r.reason + "\n")).encode("utf-8"))
            except Exception:                                    # noqa: BLE001 - never leak a traceback
                return self._send(500, "text/plain", b"the witness failed to process this request\n")
            return self._send(status, ctype, out.encode("utf-8"))

        def do_GET(self):                                        # noqa: N802
            # A witness has no read API in the spec. This answers the one question an operator asks.
            if self.path.rstrip("/") in ("/health", ""):
                return self._send(200, "text/plain", b"ok\n")
            return self._send(404, "text/plain", b"no such endpoint\n")

        def log_message(self, fmt, *args):                       # keep the key and the body out of logs
            pass

    return Handler


def serve(witness: "CheckpointWitness", port: int = 9810, host: str = "127.0.0.1", prefix: str = ""):
    """Run the witness (blocking). One thread per request, which is right for a cron-rate service."""
    import http.server
    import socketserver

    class Threaded(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    httpd = Threaded((host, port), make_handler(witness, prefix))
    httpd.serve_forever()


# ------------------------------------------------------------------------------- the client side
def submit(url: str, note: str, old_size: int = 0, proof=None, timeout: float = 30.0,
           fetch_proof=None):
    """Submit a checkpoint and return the cosignature lines the witness produced.

    -> {"status", "cosignatures", "witness_size"}. On 409 the witness tells us the size it last
    cosigned; when `fetch_proof(old, new)` is given, this retries once with that size and a proof
    from it, which is the flow the spec describes for a client that lost its notes.
    """
    import urllib.error
    import urllib.request

    body = build_request(old_size, proof or [], note)
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "text/plain; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "cosignatures": r.read().decode("utf-8"), "witness_size": None}
    except urllib.error.HTTPError as e:
        if e.code == 409 and fetch_proof is not None:
            theirs = int(e.read().decode("utf-8").strip())
            size = int(note.split("\n")[1])
            return submit(url, note, theirs, fetch_proof(theirs, size), timeout, fetch_proof=None)
        detail = e.read().decode("utf-8", "replace").strip()
        raise Refused(e.code, detail or e.reason)
