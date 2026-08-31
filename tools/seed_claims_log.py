#!/usr/bin/env python
"""Record every published number as a signed statement in the transparency log.

    python tools/seed_claims_log.py --log transparency/claims.log

WHAT THIS IS FOR. `claims_audit.py` already refuses to let a number reach a reader-facing file
without a row saying what it measures and which command reproduces it. That registry lives in this
repository, which means it is exactly as trustworthy as our willingness not to edit it. Putting each
row in an append-only log signed with a published key makes the difference checkable: a claim we
quietly changed later shows up as a second entry, and a claim we removed stays in the log.

So the log is not a demo of the product. It is the product applied to the one set of assertions we
most want to be believed about.

IDEMPOTENT, AND APPEND-ONLY BY CONSTRUCTION. It reads the subjects already in the log and registers
only what is missing, so running it after adding a claim appends one entry rather than rewriting
history. A claim whose TEXT changed registers again under the same subject: both versions stay, which
is the behaviour worth having, because the point is to make an edit visible rather than tidy.

Each statement carries the whole claim rather than a digest of it. Measured across the 113 rows, the
largest canonical payload is 754 bytes against a 4096-byte policy limit.

BUT THE LOG STORES ONLY THE DIGEST OF THAT PAYLOAD, so pass `--payloads-out` or a reader can see
WHEN a claim was recorded and never WHAT it said. This paragraph used to say the log was
self-contained and a reader needed nothing from this repository; that was written about the
statements, which `register()` does not keep. The file that makes it true is `payloads.json`, and
`verify.py` checks every text in it against the digest the log recorded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from inspeximus import scitt                                            # noqa: E402
from inspeximus.transparency import (RegistrationPolicy, RegistrationRefused,  # noqa: E402
                                     TransparencyService)

ISSUER = "urn:inspeximus:claims-audit"


def claim_payload(c):
    """The canonical bytes a claim is recorded as. Sorted and separator-free so the same claim always
    produces the same bytes, which is what makes a later edit detectable rather than merely likely."""
    return json.dumps({"id": c["id"], "file": c["file"], "tokens": list(c["tokens"]),
                       "status": c["status"], "command": c["command"],
                       "pin": c["pin"], "claim": c["claim"]},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def already_recorded(service):
    """The (subject, payload digest) pairs the log already holds.

    KEYED ON THE PAYLOAD DIGEST, NOT THE STATEMENT DIGEST, and that choice is what lets this run
    without the signing key. Ed25519 is deterministic, so a statement digest is reproducible only by
    whoever can sign; a payload digest is sha256 of the claim text and anyone can compute it. So CI
    can check that every claim is logged while the private key stays on one machine, which is the
    right place for it.

    A LEAF IS NOT A STATEMENT, and reading it as one is what broke this. `register()` appends a
    canonical JSON record describing the statement: kind, ts, statement_sha256, issuer, subject,
    payload_sha256, policy_sha256. Calling `verify_signed_statement` on that record fails on every
    entry, so this returned an empty set, so nothing looked recorded, so a second run appended all
    113 claims again. The log went 114 to 227 and the summary said "already in the log: 0" while
    printing a doubled entry count on the next line.

    Read back out of the log rather than tracked in a side file, because a side file is a second
    record that can disagree with the first, and then which one is the log has no answer.
    """
    seen = set()
    for i in range(service.size()):
        try:
            row = json.loads(service.entry_leaf(i).decode("utf-8"))
        except Exception:                                               # noqa: BLE001
            continue
        if isinstance(row, dict) and row.get("subject") and row.get("payload_sha256"):
            seen.add((row["subject"], row["payload_sha256"]))
    return seen


def check_only(a):
    """Is every claim in the registry already in the log? Answered without the signing key.

    This is what CI runs. It cannot append, and that is the point: the machine that enforces the
    rule does not need the ability to break it.
    """
    import claims_audit as ca
    service = TransparencyService(a.log, RegistrationPolicy(a.policy_name), lambda _b: b"",
                                  lambda *_: True, service_pubkey="")
    seen = already_recorded(service)
    missing = [c["id"] for c in ca.NUMBER_CLAIMS
               if ("claim:" + c["id"],
                   hashlib.sha256(claim_payload(c)).hexdigest()) not in seen]
    print("claims in the registry : %d" % len(ca.NUMBER_CLAIMS))
    print("entries in the log     : %d" % service.size())
    print("not yet logged         : %d" % len(missing))
    for cid in missing[:20]:
        print("   MISSING %s" % cid)
    if len(missing) > 20:
        print("   ... and %d more" % (len(missing) - 20))
    if missing:
        print("")
        print("Run this on the machine holding the key:")
        print("   python tools/seed_claims_log.py --log %s --payloads-out <dir>/payloads.json"
              % a.log)
    return 1 if missing else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", required=True)
    ap.add_argument("--policy-name", default="inspeximus-published-claims")
    ap.add_argument("--payloads-out", default=None,
                    help="write the claim texts here as payloads.json, so a reader can hash each one "
                         "and match the digest the log recorded. Without it the log proves WHEN a "
                         "claim was recorded and not WHAT it said")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="report whether every claim is already logged and exit non-zero if not. "
                         "Needs NO signing key, so CI can enforce it while the key stays put")
    a = ap.parse_args(argv)

    if a.check:
        return check_only(a)

    secret = (os.environ.get("INSPEXIMUS_SERVICE_SECRET") or "").strip()
    if not secret:
        print("refusing to run: INSPEXIMUS_SERVICE_SECRET is not set. A log signed with a key that "
              "changes every run chains to nothing.", file=sys.stderr)
        return 2
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as SK
    sk = SK.from_private_bytes(bytes.fromhex(secret))
    pub = sk.public_key().public_bytes_raw().hex()

    import claims_audit as ca
    service = TransparencyService(a.log, RegistrationPolicy(a.policy_name, max_payload_bytes=4096),
                                  sk.sign, lambda *_: True, service_pubkey=pub)
    seen = already_recorded(service)
    before = service.size()

    added, skipped, refused = 0, 0, []
    for c in ca.NUMBER_CLAIMS:
        payload = claim_payload(c)
        subject = "claim:" + c["id"]
        # BOTH SIDES OF THIS COMPARISON MUST BE KEYED THE SAME WAY. `already_recorded` returns
        # (subject, payload digest); comparing a STATEMENT digest against it matches nothing, so
        # every claim looks unlogged and the whole registry appends again on every run. That is the
        # bug this file already had once, in a different disguise.
        digest = hashlib.sha256(payload).hexdigest()
        if (subject, digest) in seen:
            skipped += 1
            continue
        statement = scitt.signed_statement(payload, issuer=ISSUER, subject=subject, sign=sk.sign)
        if a.dry_run:
            added += 1
            continue
        try:
            service.register(statement)
            added += 1
        except RegistrationRefused as e:
            refused.append((c["id"], str(e)[:90]))

    if a.payloads_out and not a.dry_run:
        # Keyed by subject, holding the exact bytes that were hashed. A reader recomputes
        # sha256(text) and compares it with payload_sha256 in log.jsonl; verify.py does this
        # automatically when the file is present.
        texts = {"claim:" + c["id"]: claim_payload(c).decode("utf-8") for c in ca.NUMBER_CLAIMS}
        # newline="" writes exactly what json.dump emits, with no platform translation, so the file
        # is byte-identical on Windows and Linux. The hashes are over the STRING VALUES rather than
        # the file, so translation would not break verification, but a file that differs by platform
        # makes two honest publishers look like they disagree.
        with open(a.payloads_out, "w", encoding="utf-8", newline="") as fh:
            json.dump(texts, fh, indent=1, sort_keys=True, ensure_ascii=False)
        print("wrote %s: %d claim texts" % (a.payloads_out, len(texts)))

    head = service.head()
    print("claims in the registry : %d" % len(ca.NUMBER_CLAIMS))
    print("already in the log     : %d" % skipped)
    print("%-23s: %d" % ("appended" if not a.dry_run else "would append", added))
    for cid, why in refused:
        print("   REFUSED %-38s %s" % (cid, why))
    print("entries %d -> %d, root %s" % (before, service.size(), head["writes_tip"][:16]))
    print("signing key (public)   : %s" % pub)
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
