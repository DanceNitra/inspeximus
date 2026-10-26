#!/usr/bin/env python
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
import base64, hashlib, json, os, sys


def node_hash(left, right):
    return hashlib.sha256(b"" + left + right).digest()


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
    # EACH ENTRY IS TIED TO THE LEAF THE TREE HASHED (3.20, F7 and AUDIT-A N-2). The first version took leaf_hash
    # from log.jsonl and read payload_sha256 from the same line, so a line whose entry differed from the hashed
    # leaf passed. The second tied a line through entries/<n>.leaf.json, and a line with no leaf file was "not
    # checked" with exit 0, so deleting the leaf file passed the same edit. The leaf IS the canonical JSON of the
    # entry (sorted keys, no spaces, ASCII), so the entry on the line is re-encoded here and hashed:
    # SHA-256(0x00 || canonical(entry)) must equal leaf_hash. No leaf file is needed, and deleting one changes
    # nothing. A line that carries the raw leaf (leaf_b64, a leaf that is not JSON) is tied by hashing those
    # bytes. A line with neither cannot be tied, and the run fails unless --allow-untied is given. A leaf file
    # that is published must still be the same leaf.
    allow_untied = "--allow-untied" in sys.argv[1:]
    tied, tied_raw, untied = {}, 0, 0
    for row in rows:
        index = int(row["index"])
        leaf_path = os.path.join("entries", "%d.leaf.json" % index)
        if os.path.exists(leaf_path):
            with open(leaf_path, "rb") as lf:
                if hashlib.sha256(bytes([0]) + lf.read()).hexdigest() != row["leaf_hash"]:
                    problems.append("entry %d: its leaf file does not hash to the leaf_hash log.jsonl gives" % index)
        if "entry" in row:
            leaf = json.dumps(row["entry"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            if hashlib.sha256(bytes([0]) + leaf).hexdigest() != row["leaf_hash"]:
                problems.append("entry %d: the record in log.jsonl is not the leaf the tree hashed" % index)
                continue
            tied[index] = row["entry"]
        elif "leaf_b64" in row:
            raw = base64.b64decode(row["leaf_b64"])
            if hashlib.sha256(bytes([0]) + raw).hexdigest() != row["leaf_hash"]:
                problems.append("entry %d: its leaf_b64 does not hash to the leaf_hash log.jsonl gives" % index)
                continue
            # A RAW LEAF THAT IS A RECORD IS READ AS ONE (3.20, AUDIT-A N-12). It was only counted as tied, so a line
            # rewritten to carry the real leaf as leaf_b64, beside a substituted text in payloads.json, passed with
            # that text unchecked. A leaf that decodes to a JSON object is the entry, and its payload is checked.
            try:
                as_entry = json.loads(raw.decode("utf-8"))
            except ValueError:
                as_entry = None
            if isinstance(as_entry, dict):
                tied[index] = as_entry
            else:
                tied_raw += 1
        else:
            untied += 1
    if untied and not allow_untied:
        problems.append("%d entries carry no record and no leaf, so nothing ties them to the tree "
                        "(--allow-untied accepts that)" % untied)
    # If the publisher shipped the payloads, check each one hashes to what the log recorded. This is
    # what makes the TEXT of an entry checkable rather than only its position in the tree.
    # LOOKED UP BY DIGEST, NOT BY SUBJECT. A subject holds more than one entry as soon as a claim is
    # corrected, which is the behaviour the log exists for, and a subject->text map can only carry
    # the newest. The first real correction (13 of 13 -> 14 of 14) therefore made this verifier call
    # its own log corrupt. A superseded entry whose text is no longer published is reported as
    # unavailable, which is honest, and is not a failure: the log's job is to prove nothing was
    # rewritten, and an old text we no longer publish was not rewritten, it was retired.
    payloads, checked_payloads, unavailable = None, 0, 0
    if os.path.exists("payloads.json"):
        payloads = raw = json.load(open("payloads.json", encoding="utf-8"))
        by_digest = raw.get("by_digest") if isinstance(raw, dict) and "by_digest" in raw else None
        for row in rows:
            entry = tied.get(row["index"])
            if entry is None:
                continue                                 # not tied to the tree, so its text proves nothing
            subject, want = entry.get("subject"), entry.get("payload_sha256")
            if subject is None or want is None:
                continue
            if by_digest is not None:
                text = by_digest.get(want)
                if text is None:
                    unavailable += 1                     # a retired version, or one we never shipped
                    continue
            elif subject in raw:                          # the older subject-keyed shape
                text = raw[subject]
            else:
                problems.append("payloads.json has nothing for " + subject)
                continue
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != want:
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
    print("checked: %d entries are the leaves the tree hashed." % (len(tied) + tied_raw))
    if untied:
        print("not checked: %d entries carry no record and no leaf, so they are not tied to the tree "
              "(accepted by --allow-untied)." % untied)
    if tied_raw:
        print("not checked: %d entries carry a leaf that is not a record, so they name no text to check." % tied_raw)
    if payloads is not None:
        print("checked: %d published entry texts hash to what the log recorded." % checked_payloads)
        if unavailable:
            print("not checked: %d entries hold a superseded text that is no longer published. "
                  "Their position in the tree is still proven above." % unavailable)
    print("NOT checked: the signature on the head, and whether any entry is true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
