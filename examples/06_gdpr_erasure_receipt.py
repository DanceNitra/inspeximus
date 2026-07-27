"""
inspeximus example 06 — the signed erasure receipt (GDPR Art. 17 / EU AI Act Art. 12).

    pip install inspeximus
    python 06_gdpr_erasure_receipt.py

A data-subject-access/erasure request ("DSAR") arrives: erase everything you hold about a person.
Most agent memory stores can delete a row — but they cannot *prove* they did it. inspeximus emits a
signed, hash-chained, CONTENT-FREE tombstone per erased record, grouped by the request id, so you can
answer an auditor's "show me you erased X for request R at time T" without ever re-exposing the data.

That auditable trail is exactly what GDPR Art. 17 (right to erasure) and the EU AI Act Art. 12
(record-keeping / traceability, in force 2 Aug 2026) ask for, and what a plain vector-DB delete cannot
give you. No LLM, no embedder, no external service — one deterministic file.
"""
from inspeximus import Inspeximus, new_receipt_keypair

# --- operator signing key -----------------------------------------------------
# The tombstones are Ed25519-signed. In production, load this from your KMS / secret store and keep the
# secret half off the memory host; here we mint an ephemeral pair so the example is self-contained.
receipt_sk, receipt_pk = new_receipt_keypair()

m = Inspeximus(path=None, receipt_key=receipt_sk, receipt_pubkey=receipt_pk)   # signing ON (opt-in)

# --- records about a data subject --------------------------------------------
# Note the third record is a *derived summary* built from the subject's data — the kind of row a naive
# text-match delete usually misses. inspeximus follows derived-from taint, so it goes too.
m.remember("Customer alice@example.com is based in Frankfurt", key="alice::region", object="Frankfurt")
m.remember("correction: alice relocated to Ohio",             key="alice::region", object="Ohio")
m.remember("summary: alice is an EU customer, on the priority tier", key="alice::summary")
print(f"stored {len(m.recall('alice', k=10))} recallable record(s) about the subject")

# --- the DSAR: erase everything about the subject, with a receipt --------------
# Select the records (here by a predicate; you can also pass explicit ids). `request_id` ties the receipt
# to the ticket; `basis` records the legal ground for the erasure.
receipt = m.forget(
    where=lambda r: "alice" in (str(r.get("text", "")) + str(r.get("key", ""))).lower(),
    request_id="DSAR-2026-0042",
    basis="GDPR Art.17 right-to-erasure request",
)
print(f"\nDSAR-2026-0042 -> erased {receipt['forgotten']} record(s), {receipt['tombstones']} tombstone(s)")

# --- prove it, three ways -----------------------------------------------------

# 1) The store checks ITSELF: every prior write receipt whose row is now gone must be an intentional
#    erasure (a tombstone), never an out-of-band disappearance. (True, []) == clean.
ok, problems = m.verify_writes()
print(f"\nverify_writes(): {ok}   (no unexplained disappearances: {problems == []})")

# 2) The auditable erasure log — grouped by request id, with the tamper-evidence proof.
gov = m.governance_report()
print("governance_report():")
print(f"   erasures_total : {gov['erasures_total']}")
print(f"   by_request     : {list(gov['by_request'])}")
print(f"   proof.verified : {gov['proof']['verified']}   all_signed: {gov['proof']['all_signed']}")

# 3) A single tombstone — content-free (no PII), signed, chained to its predecessor.
last = m._tombstones[-1]
print("\none tombstone (what an auditor sees — note: NO personal data in it):")
for field in ("seq", "memory_id", "ts", "request_id", "prev", "hash", "pubkey", "sig"):
    v = last.get(field)
    print(f"   {field:<10}: {str(v)[:56]}")

# --- honest scope (read before relying on it for compliance) ------------------
print(
    "\nScope: the tombstone proves the ACT of erasure (a record with this surrogate id was erased at T\n"
    "for request R) and its Ed25519 signature is tamper-evident against anyone who does NOT hold the\n"
    "receipt key. It is an integrity primitive, not a compliance certification: the operator who holds\n"
    "the key can also forge tombstones, so for operator-adversarial audit, anchor the chain head\n"
    "(the latest hash) somewhere you witnessed out of band. It commits to the id + time + request,\n"
    "never to the content — which is the point."
)
