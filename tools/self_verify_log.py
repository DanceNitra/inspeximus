#!/usr/bin/env python
"""Re-verify every entry in the transparency log, hourly, and publish the verdict.

WHY IT RUNS EVERY HOUR RATHER THAN WHEN SOMEBODY ASKS. A log that is checked only when a customer
asks has been unchecked for however long nobody asked. This re-reads the whole log from disk, checks
each receipt's signature against the published key, recomputes each inclusion proof against the root
the log publishes, and writes the verdict where anybody can read it. The value is the published
FAILED, not the published OK: an operator who can quietly skip a bad hour has a status page rather
than a check.

    python tools/self_verify_log.py --site /srv/static-log \\
        --out /srv/static-log/self-verification.json

Exit 0 when the verdict is OK, 1 when it is FAILED, 2 when the run could not complete. The verdict
file is written in all three cases, because a missing file and a bad verdict look the same from
outside and only one of them is honest.

WHAT IT CHECKS, and what each check would miss alone:
  - every receipt verifies under the published key: catches a forged or edited receipt
  - every inclusion proof recomputes to the published root: catches an entry removed or reordered
  - the published root follows from the published leaves: catches a head that lies about its own tree
  - the count matches: catches a truncation that is consistent with itself

WHAT IT CANNOT DO. It is the operator checking the operator. It cannot see a history shown to
somebody else, and a determined operator could run it on a doctored copy. That is what the external
witness and the OpenTimestamps anchor are for, and the verdict file says so rather than implying
this is independent.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from inspeximus import cose, merkle                                      # noqa: E402


def _verifier(pub_hex: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))

    def verify(msg, sig):
        try:
            pk.verify(sig, msg)
            return True
        except Exception:                                        # noqa: BLE001
            return False
    return verify


def run(site: str) -> dict:
    """Re-verify every published receipt against the published head. No key, no database."""
    started = time.time()
    with open(os.path.join(site, "head.json"), encoding="utf-8") as fh:
        head = json.load(fh)
    with open(os.path.join(site, "keys.json"), encoding="utf-8") as fh:
        pubkey_hex = json.load(fh)["x_hex"]
    root = bytes.fromhex(head["writes_tip"])
    verify = _verifier(pubkey_hex)

    leaves, problems, checked, missing = [], [], 0, 0
    with open(os.path.join(site, "log.jsonl"), encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    for i, row in enumerate(rows):
        leaves.append(bytes.fromhex(row["leaf_hash"]))
        leaf_path = os.path.join(site, "entries", "%d.leaf.json" % i)
        receipt_path = os.path.join(site, "entries", "%d.cose" % i)
        if not os.path.exists(receipt_path):
            missing += 1
            problems.append("entry %d has no published receipt" % i)
            continue
        with open(receipt_path, "rb") as rf:
            receipt = rf.read()
        leaf = None
        if os.path.exists(leaf_path):
            with open(leaf_path, "rb") as lf:
                leaf = lf.read()
        out = cose.verify_receipt(receipt, verify, leaf_data=leaf, expected_root=root)
        checked += 1
        if not out.get("ok"):
            problems.append("entry %d: %s" % (i, "; ".join(out.get("problems") or ["receipt did not verify"])))

    if len(rows) != head.get("n_writes"):
        problems.append("the head claims %s entries and the log publishes %d"
                        % (head.get("n_writes"), len(rows)))
    recomputed = merkle._root_hashed(list(leaves)) if leaves else merkle.root([])
    if recomputed != root:
        problems.append("the published root is not the root of the published leaves")

    return {
        "kind": "inspeximus.self-verification/1",
        "checked_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verdict": "OK" if not problems else "FAILED",
        "entries": len(rows),
        "receipts_verified": checked,
        "entries_without_receipt": missing,
        "root": root.hex(),
        "problems": problems,
        "seconds": round(time.time() - started, 3),
        "scope": ("The operator checking the operator, over the artifacts this site publishes. It "
                  "cannot see a history shown to somebody else. Independence comes from the external "
                  "witness and the Bitcoin anchor, not from this file."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", required=True, help="the published static log directory")
    ap.add_argument("--out", required=True, help="where to write the verdict")
    a = ap.parse_args(argv)

    try:
        verdict = run(a.site)
        code = 0 if verdict["verdict"] == "OK" else 1
    except Exception as exc:                                     # noqa: BLE001
        # A run that could not complete publishes that, rather than leaving yesterday's OK in place.
        verdict = {"kind": "inspeximus.self-verification/1",
                   "checked_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "verdict": "ERROR", "error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
        code = 2

    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(verdict, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, a.out)
    print("%s: %d entries, %d receipts verified, %.3fs"
          % (verdict["verdict"], verdict.get("entries", 0), verdict.get("receipts_verified", 0),
             verdict.get("seconds", 0.0)))
    for p in verdict.get("problems", [])[:5]:
        print("  " + p)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
