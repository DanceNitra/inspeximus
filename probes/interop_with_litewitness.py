#!/usr/bin/env python
"""Does an independent tlog-witness cosign what our client sends it, and refuse what it should?

Our `witness_checkpoint.submit` had only ever talked to our own witness, which proves the two halves
agree with each other and nothing about the protocol. This drives litewitness, the c2sp.org
tlog-witness implementation in filippo.io/torchwood, which we did not write. The CI job
`.github/workflows/tlog-witness-interop.yml` builds it on a GitHub runner, configures two logs, and
runs this file.

    python probes/interop_with_litewitness.py keygen --out DIR
    python probes/interop_with_litewitness.py run --url http://localhost:7380 --state DIR \\
        --witness-name example.test/witness --witness-ssh-pub DIR/witness.pub \\
        --live-checkpoint-url https://dancenitra.github.io/inspeximus-log/checkpoint

THE CASES, each with the answer the spec gives:
  accepted  first contact at size 1 (old 0), then 1 -> 5 and 5 -> 17 with consistency proofs, and
            our LIVE checkpoint at first contact. Each must return 200 and a cosignature that
            verifies under the witness key.
  refused   a stale old size (409, body is the witness's size), a corrupted consistency proof (422),
            a checkpoint signed by a key the witness does not know (403), and a tree that shrank
            (409 or 422; the spec requires refusal, the code depends on which check fires first).

Exit 0 only when every case matches. The result goes to stdout as JSON, one line per case.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from inspeximus import merkle                                          # noqa: E402
from inspeximus.checkpoint import signed_checkpoint, vkey               # noqa: E402
from inspeximus.witness_checkpoint import build_request, verify_cosignature  # noqa: E402

ORIGIN = "example.test/inspeximus-interop"


def _keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization as s
    k = Ed25519PrivateKey.generate()
    return (k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex(),
            k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex())


def keygen(out: str) -> int:
    os.makedirs(out, exist_ok=True)
    for label in ("log", "stranger"):
        sk, pk = _keypair()
        with open(os.path.join(out, label + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"secret": sk, "public": pk}, fh)
    log = json.load(open(os.path.join(out, "log.json"), encoding="utf-8"))
    with open(os.path.join(out, "log.vkey"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(vkey(ORIGIN, log["public"]) + "\n")
    print(vkey(ORIGIN, log["public"]))
    return 0


def _post(url: str, body: bytes):
    req = urllib.request.Request(url.rstrip("/") + "/add-checkpoint", data=body, method="POST",
                                 headers={"Content-Type": "text/plain; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _ssh_ed25519_hex(path: str) -> str:
    blob = base64.b64decode(open(path, encoding="utf-8").read().split()[1])
    # string "ssh-ed25519", then a 32-byte string: each is a u32 length and the bytes.
    n = int.from_bytes(blob[:4], "big")
    k = blob[4 + n:]
    return k[4:4 + int.from_bytes(k[:4], "big")].hex()


def run(a) -> int:
    log = json.load(open(os.path.join(a.state, "log.json"), encoding="utf-8"))
    stranger = json.load(open(os.path.join(a.state, "stranger.json"), encoding="utf-8"))
    wpub = _ssh_ed25519_hex(a.witness_ssh_pub)
    leaves = [("leaf %d" % i).encode() for i in range(20)]
    mtl = [merkle.leaf_hash(x) for x in leaves]

    def note(n, key=log):
        return signed_checkpoint(ORIGIN, n, merkle._root_hashed(mtl[:n]), key["secret"], key["public"])

    def text_of(n):
        return note(n).rpartition("\n\n")[0] + "\n"

    results = []

    def case(name, body, want, check_cosig=None):
        status, out = _post(a.url, body)
        ok = status in want
        detail = out.strip()[:160]
        if ok and check_cosig is not None:
            try:
                verify_cosignature(check_cosig, out.splitlines()[0], a.witness_name, wpub)
            except Exception as e:                                     # noqa: BLE001
                ok, detail = False, "cosignature does not verify: %s %s" % (type(e).__name__, e)
        results.append({"case": name, "status": status, "want": sorted(want), "ok": ok, "body": detail})
        print(json.dumps(results[-1]), flush=True)

    proof = lambda m, n: merkle.consistency_proof(leaves[:n], m)       # noqa: E731
    case("first contact, size 1", build_request(0, [], note(1)), {200}, text_of(1))
    case("1 -> 5 with a consistency proof", build_request(1, proof(1, 5), note(5)), {200}, text_of(5))
    case("5 -> 17 with a consistency proof", build_request(5, proof(5, 17), note(17)), {200}, text_of(17))
    case("stale old size 5 after 17", build_request(5, proof(5, 18), note(18)), {409})
    bad = [bytes(32)] + proof(17, 18)[1:]
    case("17 -> 18 with a corrupted proof", build_request(17, bad, note(18)), {422})
    case("signed by a key the witness does not know", build_request(17, proof(17, 19), note(19, stranger)),
         {403})
    case("a tree that shrank, 17 -> 10", build_request(17, [], note(10)), {409, 422, 400})

    if a.live_checkpoint_url:
        live = urllib.request.urlopen(a.live_checkpoint_url, timeout=30).read().decode("utf-8")
        case("our live checkpoint, first contact", build_request(0, [], live), {200},
             live.rpartition("\n\n")[0] + "\n")

    good = all(r["ok"] for r in results)
    print(json.dumps({"summary": "%d of %d cases as the spec says" % (sum(r["ok"] for r in results),
                                                                       len(results)), "ok": good}))
    return 0 if good else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--url", required=True)
    r.add_argument("--state", required=True)
    r.add_argument("--witness-name", required=True)
    r.add_argument("--witness-ssh-pub", required=True)
    r.add_argument("--live-checkpoint-url", default="")
    a = ap.parse_args(argv)
    return keygen(a.out) if a.cmd == "keygen" else run(a)


if __name__ == "__main__":
    raise SystemExit(main())
