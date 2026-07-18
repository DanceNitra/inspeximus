"""Auditor-grade erasure certificate — round-trip + adversary test.

Proves the PRO-grade capability: an operator runs forget_subject -> erasure_certificate(); a third-party
AUDITOR runs verify_erasure_certificate() WITHOUT the private key and WITHOUT trusting the operator, and gets a
machine-checkable VALID/INVALID verdict — including the 'read the raw store' confirmation that every erased id
is genuinely gone. Then we tamper (flip a tombstone, hide an un-erased id) and confirm it flips to INVALID.
"""
import sys, pathlib, tempfile, os, json, copy
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mnemo import Mnemo, new_receipt_keypair, sign_erasure, verify_erasure_certificate

SECRET = "Alice Meyer SSN 441-90-2277"


def run():
    ok = {}
    tmp = os.path.join(tempfile.mkdtemp(), "store.json")
    sk, pub = new_receipt_keypair()
    m = Mnemo(path=tmp, receipts=True, receipt_key=sk, receipt_pubkey=pub)
    m.remember(SECRET, key="alice::ssn", source={"doc": "alice"}, pii=True)
    m.remember("the deploy channel is BLUE-9", key="deploy")   # bystander
    m._save(force=True)

    rid = "gdpr-erasure-2026-07-18-alice"
    m.forget_subject("alice", request_id=rid,
                     authorization=sign_erasure(sk, "alice", rid), authorized_by=pub)
    m._save(force=True)

    cert = m.erasure_certificate(request_id=rid)
    ok["A cert issued + is content-free (no PII)"] = (cert.get("count", 0) >= 1
                                                      and SECRET not in json.dumps(cert)
                                                      and "441-90-2277" not in json.dumps(cert))

    # AUDITOR verifies independently (no private key), against the raw store on disk
    v = verify_erasure_certificate(cert, store_path=tmp, expected_pubkey=pub)
    ok["B auditor verdict VALID"] = v["valid"]
    ok["C chain intact"] = v["checks"].get("chain_intact") is True
    ok["D signatures valid (pinned pubkey)"] = v["checks"].get("signatures_valid") is True
    ok["E erased id ABSENT from raw store"] = v["checks"].get("store_absent") is True
    ok["F bystander survived"] = b"BLUE-9" in pathlib.Path(tmp).read_bytes()

    # ADVERSARY 1: tamper a tombstone hash -> must go INVALID
    bad = copy.deepcopy(cert)
    if bad["tombstones"]:
        bad["tombstones"][0]["hash"] = "0" * 64
    v2 = verify_erasure_certificate(bad, store_path=tmp, expected_pubkey=pub)
    ok["G tampered tombstone -> INVALID"] = (v2["valid"] is False)

    # ADVERSARY 2: operator claims an id erased but it is STILL in the store -> must go INVALID
    bad2 = copy.deepcopy(cert)
    items = json.loads(pathlib.Path(tmp).read_text(encoding="utf-8"))
    live_id = next((r["id"] for r in items), None)
    if live_id:
        bad2["erased_memory_ids"] = list(bad2.get("erased_memory_ids", [])) + [live_id]
    v3 = verify_erasure_certificate(bad2, store_path=tmp, expected_pubkey=pub)
    ok["H fake 'erased' id still present -> INVALID"] = (v3["valid"] is False)

    # ADVERSARY 3: wrong expected_pubkey -> signatures must fail
    _, other_pub = new_receipt_keypair()
    v4 = verify_erasure_certificate(cert, store_path=tmp, expected_pubkey=other_pub)
    ok["I wrong pinned pubkey -> INVALID"] = (v4["valid"] is False)

    print("=" * 62)
    print("Auditor-grade erasure certificate — issue + independent verify")
    print("=" * 62)
    for k, val in ok.items():
        print(f"  [{'PASS' if val else 'FAIL'}] {k}")
    print("-" * 62)
    print("RECEIPT:", "VALID - all checks hold" if all(ok.values()) else "INVALID - do not ship")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(run())
