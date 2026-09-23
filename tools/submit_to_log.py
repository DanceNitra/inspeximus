#!/usr/bin/env python
"""Put a document in the hosted transparency log, and prove it is there without asking the server.

WHY THIS IS NOT AN HTTP ENDPOINT, and the reasoning is the whole design. The public side of that
host serves static files and nothing else: on 2026-09-23 the one remaining proxy turned out to be a
live registration endpoint reachable from the internet, on a service that accepts any issuer, and
removing it was the single largest improvement of that night. Adding a write path back would undo
it. So submission goes over SSH to the loopback service, which means the people who can write to
this log are the people who already hold a key to the machine, and the signing key never leaves it.

    python tools/submit_to_log.py receipt.json --subject "urn:agora:seal:2026-09-23" \\
        --issuer "urn:agora:builder" --key ~/.inspeximus/submitter.secret

What it does, in order: hash the document, sign a SCITT Signed Statement over that hash, hand the
statement to the service over SSH, run the publisher so the new entry reaches the static site, then
download the published proof and verify INCLUSION locally. The last step is the point. A submission
that ends at "the server said 201" has proved nothing a reader can repeat.

THE PAYLOAD IS A DIGEST, NEVER THE DOCUMENT. The log is public and small. A hash commits to the
bytes without publishing them, and the holder of the document can prove the match at any time; the
log cannot be used to disclose something by accident.

WHAT AN ENTRY PROVES. That this digest was recorded in this log at this index, and that the root
published afterwards contains it. Not that the document is true, and not that the same history was
shown to somebody else. Only a witness answers that.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from inspeximus import merkle, scitt                                   # noqa: E402

try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:                                                    # noqa: BLE001
    CTX = ssl.create_default_context()

HOST = "ubuntu@92.5.74.17"
BASE = "https://dancenitra.github.io/inspeximus-log"
SITE = "/srv/static-log-v2"
# The host's monitor pairs every new log entry with a submission recorded here BEFORE it is made.
# An entry with no record is reported as a signature nobody asked for.
RECORD = "/var/lib/sentinel/submissions.jsonl"


def _signer(key_path: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    with open(key_path, encoding="utf-8") as fh:
        secret = fh.read().strip()
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret))
    return sk.sign


def _ssh(host: str, command: str, stdin: bytes | None = None, identity: str | None = None):
    argv = ["ssh"]
    if identity:
        argv += ["-i", identity]
    argv += [host, command]
    return subprocess.run(argv, input=stdin, capture_output=True)


def _read_published(host: str, name: str, identity: str | None) -> bytes:
    """A published file, read from the host's static site over SSH.

    NOT OVER HTTPS, because the host serves nothing to the internet: the public copy is the Pages
    mirror, which republishes only after its workflow verifies a snapshot, up to half an hour later.
    The bytes read here are the ones the host pushes to that mirror, so the proof checked below is
    the proof a reader downloads once the mirror catches up.
    """
    r = _ssh(host, "cat %s/%s" % (SITE, name), identity=identity)
    if r.returncode != 0:
        raise SystemExit("could not read %s from the host: %s"
                         % (name, r.stderr.decode("utf-8", "replace")[:200]))
    return r.stdout


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "inspeximus-submit/1"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return r.read()


def submit(path: str, issuer: str, subject: str, key_path: str, host: str = HOST,
           base: str = BASE, identity: str | None = None, publish: bool = True,
           record: str | None = RECORD) -> dict:
    document = open(path, "rb").read()
    digest = hashlib.sha256(document).hexdigest()

    # THE PAYLOAD IS A SMALL JSON STATEMENT, NOT THE RAW DIGEST BYTES, and the difference is what a
    # reader can check. The published leaf carries `payload_sha256`, so a payload of the 32 raw
    # digest bytes puts a hash OF A HASH in the log: a holder with the document would have to guess
    # that they must hash it and then hash the result. With these bytes the chain is written down
    # and every step is reproducible: document -> sha256 -> this JSON -> payload_sha256 in the leaf
    # -> leaf hash -> root.
    payload = json.dumps({"document_sha256": digest, "name": os.path.basename(path)},
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    statement = scitt.signed_statement(payload, issuer, subject, _signer(key_path))

    # RECORD FIRST, SUBMIT SECOND. If the record cannot be written, nothing is submitted: an entry
    # without its record would page the owner for our own work.
    if record:
        line = json.dumps({"statement_sha256": scitt.statement_digest(scitt.without_receipts(statement)),
                           "issuer": issuer, "subject": subject,
                           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                          sort_keys=True) + "\n"
        w = _ssh(host, "sudo tee -a %s >/dev/null" % record, stdin=line.encode("utf-8"), identity=identity)
        if w.returncode != 0:
            raise SystemExit("could not record the submission, so it was not made: %s"
                             % w.stderr.decode("utf-8", "replace")[:200])

    # The service listens on loopback only. `curl --data-binary @-` hands it the bytes without ever
    # writing them to a file on the host.
    r = _ssh(host, "curl -s -i --data-binary @- -H 'Content-Type: application/cose' "
                   "http://127.0.0.1:9800/entries", stdin=statement, identity=identity)
    response = r.stdout.decode("utf-8", "replace")
    if r.returncode != 0:
        raise SystemExit("ssh failed: %s" % r.stderr.decode("utf-8", "replace")[:300])
    status = response.split("\r\n", 1)[0] if response else "(no response)"
    if " 201" not in status and " 200" not in status:
        raise SystemExit("the service refused this statement:\n%s" % response[:600])
    index = None
    for line in response.split("\r\n"):
        if line.lower().startswith("location:"):
            index = int(line.rsplit("/", 1)[-1])
    if index is None:
        raise SystemExit("the service accepted the statement but named no entry:\n%s" % response[:400])

    if publish:
        # The static site is what a reader downloads, so an entry that exists only in the service's
        # own log is not yet published in any sense that matters.
        p = _ssh(host, "sudo /usr/local/bin/publish-static-log.sh", identity=identity)
        if p.returncode != 0:
            raise SystemExit("publish failed: %s" % p.stderr.decode("utf-8", "replace")[:300])

    head = json.loads(_read_published(host, "head.json", identity))
    proof = json.loads(_read_published(host, "entries/%d.proof.json" % index, identity))
    leaf = _read_published(host, "entries/%d.leaf.json" % index, identity)
    root = bytes.fromhex(head["writes_tip"])

    included = merkle.verify_inclusion(leaf, proof["index"], proof["tree_size"],
                                       [bytes.fromhex(h) for h in proof["audit_path"]], root)

    # The leaf must commit to the payload we sent. Checking inclusion alone would prove that SOME
    # entry is in the tree at this index, which is true of every entry and says nothing about ours.
    published = json.loads(leaf.decode("utf-8"))
    payload_matches = published.get("payload_sha256") == hashlib.sha256(payload).hexdigest()

    return {
        "kind": "inspeximus.log-submission/1",
        "document": os.path.basename(path),
        "document_sha256": digest,
        "payload": payload.decode("utf-8"),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_matches_published_leaf": payload_matches,
        "issuer": issuer,
        "subject": subject,
        "index": index,
        "tree_size": head["n_writes"],
        "root": head["writes_tip"],
        "inclusion_verified": bool(included and payload_matches),
        "urls": {"proof": "%s/entries/%d.proof.json" % (base, index),
                 "leaf": "%s/entries/%d.leaf.json" % (base, index),
                 "receipt": "%s/entries/%d.cose" % (base, index),
                 "checkpoint": base + "/checkpoint"},
        "submitted_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "how_to_check": ("sha256 the document, put it in the `payload` JSON exactly as shown, "
                         "sha256 that, and it must equal `payload_sha256` in the published leaf; "
                         "then fold the leaf through the audit path to `root`."),
        "scope": ("This digest is recorded in this log at this index, and the root published "
                  "afterwards contains it. It does not say the document is true, and it cannot see "
                  "a history shown to somebody else."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", help="the document to record (its hash is what goes in the log)")
    ap.add_argument("--issuer", required=True, help="who is making this statement")
    ap.add_argument("--subject", required=True, help="what the statement is about")
    ap.add_argument("--key", required=True, help="the SUBMITTER's Ed25519 secret hex file")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--identity", default=None, help="ssh key file")
    ap.add_argument("--no-record", action="store_true",
                    help="do not record the submission for the host monitor (it will then alarm)")
    ap.add_argument("--no-publish", action="store_true",
                    help="record it but do not run the publisher; the proof will not exist yet")
    ap.add_argument("--out", default=None, help="write the submission record here")
    a = ap.parse_args(argv)

    out = submit(a.file, a.issuer, a.subject, a.key, host=a.host, base=a.base,
                 identity=a.identity, publish=not a.no_publish,
                 record=None if a.no_record else RECORD)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(out, fh, indent=2, sort_keys=True)

    print("document : %s" % out["document"])
    print("sha256   : %s" % out["document_sha256"])
    print("entry    : %d of %d" % (out["index"], out["tree_size"]))
    print("root     : %s" % out["root"])
    print("inclusion: %s" % ("VERIFIED against the published root, offline"
                             if out["inclusion_verified"] else "NOT VERIFIED"))
    print("proof    : %s" % out["urls"]["proof"])
    return 0 if out["inclusion_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
