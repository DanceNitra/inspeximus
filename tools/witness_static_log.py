#!/usr/bin/env python
"""Watch somebody else's published log and refuse to co-sign it if the history changed.

    python tools/witness_static_log.py \\
        --url https://dancenitra.github.io/inspeximus/transparency \\
        --state my_witness_state.json --out cosignatures/

RUN THIS AGAINST A LOG YOU DO NOT OPERATE. A witness under the same operator as the log is the
operator wearing a second name: it co-signs whatever it is shown, and its signature means nothing.
The whole value is that YOUR memory of what you saw is somewhere the log's publisher cannot edit.

WHAT IT CATCHES, and it is narrow enough to state exactly. It remembers the head it last signed for
a log. On the next run it recomputes that head's root from the first N leaves the log now publishes.
If they differ, the publisher rewrote history that you had already seen: a FORK. If the log now has
fewer entries than you remember, a ROLLBACK. Either way it refuses, loudly, and does not sign.

WHAT IT DOES NOT CATCH: whether any entry is true, and whether the log showed a DIFFERENT history to
somebody else in between your two visits. The second is why more than one witness matters and why
they should be strangers to each other.

NO CONSISTENCY PROOF IS USED OR NEEDED, which surprises people who know RFC 6962. A proof exists so a
verifier who holds only two roots can check one extends the other. This log publishes every leaf
hash, so a witness can simply rebuild the old root and compare. Fewer moving parts, and nothing to
get wrong in a proof format.

YOUR STATE FILE IS THE ENTIRE GUARANTEE. Delete it and this becomes a program that signs anything:
it has no memory of a previous head, so it cannot tell an extension from a rewrite. Keep it, commit
it, back it up.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from inspeximus import merkle                                           # noqa: E402

STH_FIELDS = ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip")


def fetch(base, name, timeout):
    url = base.rstrip("/") + "/" + name
    request = urllib.request.Request(url, headers={"User-Agent": "inspeximus-witness/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return r.read()


def sth_hash_of(head):
    body = json.dumps({k: head.get(k) for k in STH_FIELDS},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def read_log(base, timeout):
    """The published head and leaf hashes, with the root RECOMPUTED rather than believed.

    Taking `writes_tip` from head.json and signing it would make this witness a rubber stamp: it
    would attest to a number the publisher chose. The root is derived from the leaves here, and the
    published one is only ever compared against it.
    """
    head = json.loads(fetch(base, "head.json", timeout).decode("utf-8"))
    mtl = []
    for line in fetch(base, "log.jsonl", timeout).decode("utf-8").splitlines():
        line = line.strip()
        if line:
            mtl.append(bytes.fromhex(json.loads(line)["leaf_hash"]))
    return head, mtl


def _root(mtl):
    if not mtl:
        return hashlib.sha256(b"").digest()
    return merkle._root_hashed(list(mtl))


def judge(head, mtl, remembered):
    """EXTENDS, FIRST_CONTACT, FORK, ROLLBACK or MALFORMED, with the reason spelled out."""
    derived = _root(mtl).hex()
    if len(mtl) != head.get("n_writes"):
        return "MALFORMED", ("the head claims %s entries and the log publishes %d"
                             % (head.get("n_writes"), len(mtl)))
    if derived != head.get("writes_tip"):
        return "MALFORMED", "the published root is not the root of the published leaves"
    if sth_hash_of(head) != head.get("sth_hash"):
        return "MALFORMED", "sth_hash does not follow from the head's own fields"

    if not remembered:
        return "FIRST_CONTACT", ("nothing was remembered about this log, so this run establishes a "
                                 "baseline and proves nothing yet. The next run is the one that can "
                                 "catch a rewrite.")
    m = remembered["n_writes"]
    if len(mtl) < m:
        return "ROLLBACK", ("this log had %d entries when last seen and now publishes %d"
                            % (m, len(mtl)))
    rebuilt = _root(mtl[:m]).hex()
    if rebuilt != remembered["writes_tip"]:
        return "FORK", ("the first %d entries no longer produce the root signed before. Remembered "
                        "%s, the log now yields %s from its own published leaves."
                        % (m, remembered["writes_tip"][:16], rebuilt[:16]))
    return "EXTENDS", "the %d entries seen before are unchanged; %d have been added since" % (
        m, len(mtl) - m)


def load_state(path):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True, help="base URL of the published log")
    ap.add_argument("--state", required=True, help="this witness's memory. Losing it disarms it")
    ap.add_argument("--out", default=None, help="directory to write the co-signature into")
    ap.add_argument("--key-file", default=None,
                    help="Ed25519 secret hex. Also read from INSPEXIMUS_WITNESS_SECRET")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--name", default=None, help="a label for you, recorded in the co-signature")
    a = ap.parse_args(argv)

    secret = None
    if a.key_file:
        with open(a.key_file, encoding="utf-8") as fh:
            secret = fh.read().strip()
    secret = secret or (os.environ.get("INSPEXIMUS_WITNESS_SECRET") or "").strip() or None

    head, mtl = read_log(a.url, a.timeout)
    state = load_state(a.state)
    remembered = state.get(a.url)
    verdict, why = judge(head, mtl, remembered)

    print("log      : %s" % a.url)
    print("entries  : %d" % len(mtl))
    print("root     : %s" % head.get("writes_tip", "")[:32])
    print("verdict  : %s" % verdict)
    print("           %s" % why)

    if verdict in ("FORK", "ROLLBACK", "MALFORMED"):
        # The remembered head is NOT overwritten. A witness that updates its memory on a refusal
        # forgets the thing it just caught, and the next run reports EXTENDS on the rewritten log.
        print("")
        print("REFUSING to co-sign, and keeping the head this witness remembers. Publish this "
              "refusal: it is the only reason a witness is worth running.")
        return 2

    signature, pub = None, None
    if secret:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as SK
        sk = SK.from_private_bytes(bytes.fromhex(secret))
        pub = sk.public_key().public_bytes_raw().hex()
        signature = sk.sign(head["sth_hash"].encode("ascii")).hex()
        print("signed   : %s by %s" % (head["sth_hash"][:16], pub[:16]))
    else:
        print("NOT signed: no witness key was given, so this run only remembers. Set "
              "INSPEXIMUS_WITNESS_SECRET to make the observation checkable by others.")

    state[a.url] = {"n_writes": len(mtl), "writes_tip": head["writes_tip"],
                    "sth_hash": head["sth_hash"], "seen_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    save_state(a.state, state)

    if a.out and signature:
        os.makedirs(a.out, exist_ok=True)
        record = {"kind": "static-log-cosignature", "log_url": a.url.rstrip("/"),
                  "verdict": verdict, "n_writes": len(mtl),
                  "writes_tip": head["writes_tip"], "sth_hash": head["sth_hash"],
                  "witness_pubkey": pub, "witness_name": a.name or "",
                  "signature": signature, "observed_utc": state[a.url]["seen_utc"],
                  "scope": ("This says one witness saw this exact head and that it extends what the "
                            "same witness saw before. It does NOT say any entry is true, and it "
                            "cannot see a different history shown to somebody else between visits.")}
        path = os.path.join(a.out, "%s.json" % pub[:16])
        with open(path, "w", encoding="utf-8", newline="") as fh:
            json.dump(record, fh, indent=2, sort_keys=True)
        print("wrote    : %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
