#!/usr/bin/env python
"""Publish a transparency log as static files anyone can fetch and verify.

    python tools/publish_static_log.py --log registrations.log --out site/transparency

WHY STATIC, WHICH IS NOT A COMPROMISE. A transparency log's job is to be READ by people who do not
trust the operator, and reading is the half a static host does perfectly. The certificate
transparency ecosystem went this way on purpose: C2SP's static-ct-api serves a log as ordinary files
behind a CDN, because a tiled, cacheable log is cheaper to run and harder to equivocate with than a
bespoke API. This package's own plan already noted that the ecosystem follows RFC 6962 plus
static-ct-api rather than a service protocol.

WHAT A STATIC LOG CANNOT DO, said plainly rather than discovered later. It cannot accept a
registration over HTTP and hand back a receipt in the same request. Writing happens where the signing
key is, and the result is published afterwards. If you need an open live endpoint, run `scrapi.py`;
the container images beside it exist for that.

WHAT IT WRITES:

    head.json            the signed head: entry count, Merkle root, and the sth_hash over both
    keys.cbor            the COSE Key Set, so a reader verifies receipts without asking us
    keys.json            the same key, in a shape a browser can read
    log.jsonl            one line per entry: index, leaf hash, and the entry record itself
    entries/<n>.cose     the RFC 9942 receipt for each entry, as bytes
    verify.py            a standalone checker a reader runs against these files
    index.html           what a person sees

Every file is content a reader can check WITHOUT this tool and without us. That is the test the
output has to pass, and `verify.py` is how it is demonstrated rather than asserted.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import hashlib                                                          # noqa: E402

from inspeximus import cose, merkle                                     # noqa: E402
from inspeximus.scrapi import _cose_key                                 # noqa: E402
from inspeximus.transparency import RegistrationPolicy, TransparencyService  # noqa: E402

VERIFIER = '''#!/usr/bin/env python
"""Check this log without trusting whoever published it. Standard library only.

    python verify.py            # in the directory holding head.json

It rebuilds the RFC 6962 Merkle root from the published leaf hashes and compares it with the root in
head.json, then re-derives sth_hash from the head's own fields. A published root that does not follow
from the published leaves is what this catches, and it is exactly what an operator rewriting history
would have to change.

THE HASHING IS RFC 6962 AND THE DETAILS MATTER. Leaves are SHA-256(0x00 || data) and nodes are
SHA-256(0x01 || left || right), and the tree splits at the largest power of two STRICTLY below n, not
into pairs. The first draft of this file used pairwise levels and a plain SHA-256 leaf, which agrees
with the real tree only when the entry count is a power of two: it would have reported FAILED on an
honest log of three entries, under a heading that invites you to distrust us.

What it does NOT check: the signature on the head, which needs an Ed25519 implementation, and whether
any entry is TRUE. A log proves what was recorded, never that it is right.
"""
import hashlib, json, os


def node_hash(left, right):
    return hashlib.sha256(b"\x01" + left + right).digest()


def split_at(n):
    """The largest power of two strictly less than n. RFC 6962 splits the tree there."""
    k = 1
    while k << 1 < n:
        k <<= 1
    return k


def root_of(mtl):
    if not mtl:
        return hashlib.sha256(b"").digest()
    if len(mtl) == 1:
        return mtl[0]
    k = split_at(len(mtl))
    return node_hash(root_of(mtl[:k]), root_of(mtl[k:]))


def sth_hash_of(head):
    """SHA-256 over the canonical JSON of the four fields the head commits to."""
    fields = ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip")
    body = json.dumps({k: head.get(k) for k in fields},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def main():
    head = json.load(open("head.json", encoding="utf-8"))
    rows = []
    with open("log.jsonl", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    mtl = [bytes.fromhex(r["leaf_hash"]) for r in rows]

    problems = []
    # If the publisher shipped the payloads, check each one hashes to what the log recorded. This is
    # what makes the TEXT of an entry checkable rather than only its position in the tree.
    payloads, checked_payloads = None, 0
    if os.path.exists("payloads.json"):
        payloads = json.load(open("payloads.json", encoding="utf-8"))
        for row in rows:
            entry = row.get("entry") or {}
            subject, want = entry.get("subject"), entry.get("payload_sha256")
            if subject is None or want is None:
                continue
            if subject not in payloads:
                problems.append("payloads.json has nothing for " + subject)
                continue
            got_p = hashlib.sha256(payloads[subject].encode("utf-8")).hexdigest()
            if got_p != want:
                problems.append("the published text of %s does not hash to what the log recorded"
                                % subject)
            else:
                checked_payloads += 1
    if len(mtl) != head["n_writes"]:
        problems.append("head says %d entries, log.jsonl holds %d" % (head["n_writes"], len(mtl)))
    got = root_of(mtl).hex()
    if got != head["writes_tip"]:
        problems.append("the published root is not the root of the published leaves")
        problems.append("   published " + head["writes_tip"])
        problems.append("   rebuilt   " + got)
    if sth_hash_of(head) != head["sth_hash"]:
        problems.append("sth_hash does not follow from the head's own fields")

    if problems:
        print("FAILED")
        for p in problems:
            print(" -", p)
        return 1
    print("OK: %d entries, root %s" % (len(mtl), head["writes_tip"]))
    print("checked: the root follows from the leaves, and sth_hash follows from the head.")
    if payloads is not None:
        print("checked: %d published entry texts hash to what the log recorded." % checked_payloads)
    print("NOT checked: the signature on the head, and whether any entry is true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>%(title)s</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{max-width:46rem;margin:3rem auto;padding:0 1.2rem;font:16px/1.6 system-ui,sans-serif;color:#1a1a1a}
 h1{font-size:1.5rem;margin:0 0 .3rem} h2{font-size:1.05rem;margin:2.2rem 0 .6rem}
 code,pre{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.86em}
 pre{background:#f6f6f4;padding:.8rem 1rem;overflow-x:auto;border-left:2px solid #d8d8d2}
 table{border-collapse:collapse;width:100%%;font-size:.9em} td,th{text-align:left;padding:.35rem .6rem .35rem 0;border-bottom:1px solid #eee}
 .m{color:#666} a{color:#0645ad}
 @media(prefers-color-scheme:dark){body{background:#141414;color:#e6e6e6}pre{background:#1e1e1e;border-color:#333}
  td,th{border-color:#2a2a2a}.m{color:#999}a{color:#8ab4f8}}
</style>
<h1>%(title)s</h1>
<p class="m">%(entries)s entries &middot; published %(when)s</p>

<p>This is an append-only log of signed statements. It is served as static files so you can check it
without trusting whoever published it, and without asking anything of us.</p>

<h2>Check it yourself</h2>
<pre>curl -sO %(base)s/head.json
curl -sO %(base)s/log.jsonl
curl -sO %(base)s/verify.py
python verify.py</pre>
<p><code>verify.py</code> uses the standard library only. It rebuilds the Merkle root from the
published leaves and compares it with the published root, then re-derives <code>sth_hash</code> from
the head's own fields. A root that does not follow from the leaves is what it catches.</p>

<h2>Head</h2>
<pre>%(head)s</pre>

<h2>Files</h2>
<table>
<tr><th>file</th><th>what it is</th></tr>
<tr><td><a href="head.json">head.json</a></td><td>entry count, Merkle root, and the hash over both</td></tr>
<tr><td><a href="keys.json">keys.json</a></td><td>the Ed25519 key that signs receipts, as JSON</td></tr>
<tr><td><a href="keys.cbor">keys.cbor</a></td><td>the same key as a COSE Key Set (RFC 9052)</td></tr>
<tr><td><a href="log.jsonl">log.jsonl</a></td><td>one line per entry: index, leaf hash, statement digest</td></tr>
<tr><td>entries/&lt;n&gt;.cose</td><td>the RFC 9942 inclusion receipt for entry n</td></tr>
<tr><td><a href="verify.py">verify.py</a></td><td>the checker above</td></tr>
</table>

<h2>What this does not prove</h2>
<p>It does not prove any entry is TRUE. A log records what was said, never that it was right.</p>
<p>It does not prove we have shown you the same log we showed somebody else. A static file is easy to
serve in two versions, and only an independent WITNESS that remembers a previous head can catch that.
%(witness)s</p>
<p>Registration happens where the signing key is, not over this address. There is no endpoint here
that accepts an entry.</p>
"""


def build(service: TransparencyService, out: str, base_url: str, title: str, witness_note: str):
    os.makedirs(os.path.join(out, "entries"), exist_ok=True)
    head = service.head()
    n = service.size()

    with open(os.path.join(out, "head.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(head, fh, indent=2, sort_keys=True)

    key = _cose_key(service.service_pubkey)
    with open(os.path.join(out, "keys.cbor"), "wb") as fh:
        fh.write(cose.encode({"keys": [key]}))
    with open(os.path.join(out, "keys.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA",
                   "kid": service.service_pubkey,
                   "x_hex": key.get(-2, b"").hex(),
                   "x_b64url": base64.urlsafe_b64encode(key.get(-2, b"")).decode().rstrip("="),
                   "note": "verifies the receipts under entries/. It does NOT tell you the log is "
                           "the same one shown to anyone else; only a witness can."},
                  fh, indent=2)

    written = 0
    with open(os.path.join(out, "log.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        for i in range(n):
            leaf = service.entry_leaf(i)
            # The RFC 6962 leaf hash, SHA-256(0x00 || data), and NOT sha256(data). The 0x00 prefix is
            # the leaf/node domain separation, and a verifier handed the wrong one rebuilds a root
            # that never matches.
            row = {"index": i, "leaf_hash": merkle.leaf_hash(leaf).hex()}
            try:
                # The leaf IS the registration record: kind, ts, issuer, subject and the digests.
                # Publishing only its hash would let a reader confirm the tree and learn nothing
                # about what was recorded, which is the wrong half of the job.
                row["entry"] = json.loads(leaf.decode("utf-8"))
            except Exception:                                           # noqa: BLE001
                row["leaf_b64"] = base64.b64encode(leaf).decode()
            receipt = service.receipt_for(i)
            if receipt:
                with open(os.path.join(out, "entries", "%d.cose" % i), "wb") as rf:
                    rf.write(receipt)
                row["receipt"] = "entries/%d.cose" % i
                written += 1
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    with open(os.path.join(out, "verify.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(VERIFIER)

    with open(os.path.join(out, "index.html"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(PAGE % {"title": html.escape(title), "entries": n,
                         "when": time.strftime("%Y-%m-%d", time.gmtime()),
                         "base": html.escape(base_url.rstrip("/")),
                         "head": html.escape(json.dumps(head, indent=2, sort_keys=True)),
                         "witness": html.escape(witness_note)})
    return {"entries": n, "receipts": written, "head": head}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", required=True, help="the append-only registration log")
    ap.add_argument("--out", required=True, help="directory to write the static site into")
    ap.add_argument("--base-url", default=".", help="public URL of --out, used in the copy-paste block")
    ap.add_argument("--title", default="Transparency log")
    ap.add_argument("--policy-name", default="static-publication")
    ap.add_argument("--witness-note",
                    default="No witness has co-signed this log yet, so nothing here is evidence "
                            "against that. Running one is the most useful thing an outsider can do.")
    a = ap.parse_args(argv)

    secret = (os.environ.get("INSPEXIMUS_SERVICE_SECRET") or "").strip()
    if not secret:
        # Publishing is a READ of an existing log, so a key is not required to render it. Without one
        # the head is rendered but no NEW receipt can be minted, and saying so beats a stack trace.
        print("note: INSPEXIMUS_SERVICE_SECRET is not set, so receipts are read from the log rather "
              "than re-signed", flush=True)
        sign, pub = (lambda _b: b""), ""
    else:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as SK
        sk = SK.from_private_bytes(bytes.fromhex(secret))
        sign, pub = sk.sign, sk.public_key().public_bytes_raw().hex()

    service = TransparencyService(a.log, RegistrationPolicy(a.policy_name), sign,
                                  lambda *_: True, service_pubkey=pub)
    got = build(service, a.out, a.base_url, a.title, a.witness_note)
    print("wrote %s: %d entries, %d receipts, root %s"
          % (a.out, got["entries"], got["receipts"], got["head"]["writes_tip"][:16]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
