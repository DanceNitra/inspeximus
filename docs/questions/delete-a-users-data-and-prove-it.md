# How do I delete a user's data from my AI agent's memory, and prove it?

## Short answer

Attribute each record to its data subject when you write it, for example
`remember(text, source={"doc": "user:alice"})`. To delete, call
`forget_subject("user:alice", request_id=...)`. It hard-deletes every record attributed to that
subject, plus every record derived from one of them through `derived_from`. For each deleted record
it also appends a tombstone to a hash chain. The tombstone holds the record id, a timestamp, your
request id and the authorization fields you passed, but not the record's text. If the store has a
signing key (`receipt_key` or `receipt_signer`), the tombstone is signed.

To prove the deletion, call `erasure_certificate(request_id)` and give the certificate to whoever has
to check it. They call `verify_erasure_certificate` with the certificate, the store file, your public
key and the store's receipt chain. This re-derives the tombstone chain and checks the signatures
against your public key. It also confirms that the certificate was issued from that store, and reads
the store file to confirm that every erased id is absent. The checker does not need your private key.

## Example

Needs the `cryptography` package for the signing keys: `pip install "inspeximus[crypto]"`. Run it in
an empty directory. It creates `memory.json` and its sidecar files there. With receipts on, the
library also writes a small head file under your user config directory.

```python
import json

from inspeximus import Inspeximus, new_receipt_keypair, verify_erasure_certificate
from inspeximus.audit_bundle import load_store_receipts

sk, pk = new_receipt_keypair()      # keep sk outside the store's directory; give pk to the checker
m = Inspeximus("memory.json", receipts=True, receipt_key=sk, receipt_pubkey=pk)

# Attribute each record to its data subject when you write it.
a1 = m.remember("Alice prefers email contact", source={"doc": "user:alice"})
a2 = m.remember("Alice is on the Pro plan", source={"doc": "user:alice"})
m.remember("Summary: Alice wants email about her Pro plan", derived_from=[a1, a2])
m.remember("Bob prefers phone contact", source={"doc": "user:bob"})
m.remember("Reminder: call Alice back about the renewal")    # no source: not attributed to anyone

# 1. Delete everything attributed to Alice, and everything derived from it.
result = m.forget_subject("user:alice", request_id="DSAR-0042")
print("erased:", result["erased"], "| tombstones:", result["tombstones"])
print("recall 'contact':", [h["text"] for h in m.recall("contact")])
print("recall 'Alice':  ", [h["text"] for h in m.recall("Alice")])

# 2. Issue a certificate for that request. It carries ids, hashes and signatures, not text.
cert = m.erasure_certificate(request_id="DSAR-0042")
m.flush()
print("certificate mentions alice:", "alice" in json.dumps(cert).lower())

# 3. The checker needs the certificate, the store file and your public key. Not your private key.
def check(certificate, store):
    return verify_erasure_certificate(certificate, store_path=store, expected_pubkey=pk,
                                      store_receipts=load_store_receipts(store))

report = check(cert, "memory.json")
print("valid:", report["valid"])
print("erased ids absent from the store:", report["checks"]["store_absent"])
print("certificate issued from this store:", report["checks"]["store_bound"])

# Controls: an edited certificate, and the real certificate checked against another store file.
forged = json.loads(json.dumps(cert))
forged["tombstones"][0]["request_id"] = "DSAR-9999"
print("edited certificate valid:", check(forged, "memory.json")["valid"])

other = Inspeximus("other.json", receipts=True)
other.remember("An unrelated note")
other.flush()
print("checked against another store, valid:", check(cert, "other.json")["valid"])
```

Output:

```text
erased: 3 | tombstones: 3
recall 'contact': ['Bob prefers phone contact']
recall 'Alice':   ['Reminder: call Alice back about the renewal']
certificate mentions alice: False
valid: True
erased ids absent from the store: True
certificate issued from this store: True
edited certificate valid: False
checked against another store, valid: False
```

What the output shows:

- The two records attributed to Alice and the summary derived from them are gone. Bob's record is
  still there, so the call did not remove more than it was asked to.
- The reminder mentions Alice by name but was written without a source. `forget_subject` did not
  remove it (see Limits).
- The certificate verifies against the store it was issued from. It fails in two cases: when it
  was edited after issue, and when it is checked against a different store file.

## Limits: what this does not do

- **It finds records by attribution, not by content.** A record written without a `source` naming
  the subject, and not derived from such a record, is not erased. The reminder in the example is one.
  To see what a request would remove before running it, call
  `forget_subject(subject, dry_run=True)`.
- **It covers this store only.** Copies in a vector index, prompt logs, traces, caches, backups and
  snapshots are not touched, and neither is text a model already produced from the record. The
  certificate lists these under `scope_excludes`. Stores you register with `register_erasure_target`
  are included in the cascade; stores you do not register are not.
- **It proves the recorded act, not physical destruction.** Bytes can remain in filesystem free
  space, on SSD blocks, in snapshots and in backups, and a library that reads files cannot see those
  layers.
- **The absence check is tied to a store only if the checker passes `store_receipts`.** Without
  it, `store_bound` is `None` and the check says only that the ids are absent from whichever file
  was named.
- **The signature only binds someone who does not hold the private key.** Whoever holds `sk` can
  write and sign tombstones too. The checker needs an `anchor()` taken outside the operator's
  control: a witness co-signature, a timestamp, or a copy the checker took earlier. Passed as
  `expected_anchor`, it makes a chain that lost or changed a tombstone the witness saw fail the
  check.
- **The certificate shows more than one request.** It carries the store's whole tombstone chain so
  it can be re-derived from the start. The ids, timestamps and request ids of other erasures in the
  same store are visible in it. Do not put personal data in `request_id`, `basis` or
  `authorized_by`.
- **It is not a legal determination.** The certificate's own `scope` field says it is a
  tamper-evident integrity primitive and not a compliance certification. Whether a request has been
  fulfilled is decided by you and your legal counsel, not by this library.

## See also

- [docs/ERASURE.md](../ERASURE.md): the same flow from the command line, with a residue scan.
- [docs/API.md](../API.md): `forget_subject`, `erasure_certificate` and `DeletionManifest`.
