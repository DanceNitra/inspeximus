"""forget_subject_tombstone_probe.py — erasure that keeps the audit trail honest.

REFRAMED (post intake-gate) from a "GDPR-vs-AI-Act resolver" (theater/overclaim) to the real, narrow,
honest value: today forget() genuinely removes content, which makes verify_writes() report the now-missing
record as "deleted out-of-band" — a legitimate right-to-erasure is INDISTINGUISHABLE from tampering.
forget_subject() erases a data subject's content ACROSS provenance lineage (own source + derived taint)
and appends a signed, CONTENT-FREE deletion tombstone so verify_writes() reports the erasure as accounted-for
(chain intact, provably erased) while a record missing WITHOUT a tombstone still flags as tampering.

Mechanism is textbook (crypto-shredding, Cassandra/event-sourcing tombstones, GDPR Art.30 erasure logs,
Crosby-Wallach / Certificate-Transparency tamper-evident logs) — shipped as a zero-dep memory-lib primitive,
NOT claimed as research novelty.

Pre-registered checks map 1:1 to the intake-gate's design constraints:
  A surrogate id            the tombstone stores the record's random uuid id, ts, request_id — and NO content,
                            no content-hash (a hash of PII is still PII; EDPB). Asserted by inspection.
  B accounted-for erasure   after forget_subject, verify_writes() = OK (missing record is tombstoned, not flagged)
  C content really gone     the erased text is absent from the store AND from any recall
  D derived-taint reach     a summary derived_from the subject's record is ALSO erased (naive text-match misses it)
  E out-of-band still flags  a plain forget() (no tombstone) still makes verify_writes() report tampering
  F forged tombstone caught  tampering with the tombstone ledger is detected by verify_writes()
  G report is content-free   erasure_report() carries ids/ts/request_id only, never erased content
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mnemo import Mnemo, new_receipt_keypair


def _store(tmp):
    sk, pk = new_receipt_keypair()
    return Mnemo(path=str(tmp / "s.json"), receipts=True, receipt_key=sk, receipt_pubkey=pk), pk


def main():
    import tempfile, os
    ok = {}
    tmp = pathlib.Path(tempfile.mkdtemp())

    m, pk = _store(tmp)
    # subject 'user-42' writes two facts; an unrelated subject writes one; plus a summary derived from user-42
    a = m.remember("user-42 prefers conservative strategies", source={"doc": "user-42"})
    b = m.remember("user-42 mentioned a July deadline", source={"doc": "user-42"})
    c = m.remember("system default risk cap is 5 percent", source={"doc": "policy"})
    s = m.remember("summary: client leans conservative, Q3 timing", derived=True, derived_from=[a, b])
    assert (m.verify_writes(expected_pubkey=pk))[0], "clean store should verify"

    r = m.forget_subject("user-42", request_id="dsar-2026-0007")

    # A surrogate id + no content/content-hash in the tombstone (explicit, no precedence traps)
    allowed_keys = {"seq", "memory_id", "ts", "request_id", "prev", "hash", "pubkey", "sig"}
    keys_ok = all(set(t.keys()) <= allowed_keys for t in m._tombstones)
    ids_ok = all(t["memory_id"] in r["ids"] for t in m._tombstones)
    no_pii = not any(("conservative" in str(v).lower() or "july" in str(v).lower())
                     for t in m._tombstones for v in t.values())
    ok["A surrogate-id, no PII in tombstone"] = keys_ok and ids_ok and no_pii

    # B verify_writes OK after erasure (missing records are tombstoned, not flagged)
    okB, probsB = m.verify_writes(expected_pubkey=pk)
    ok["B accounted-for erasure (verify OK)"] = okB and probsB == []

    # C content really gone (store + recall)
    blob = " ".join(x["text"] for x in m.items).lower()
    hits = " ".join(h["text"] for h in m.recall("conservative July deadline", k=10)).lower()
    ok["C content erased from store + recall"] = ("conservative" not in blob and "july" not in blob
                                                  and "conservative" not in hits)

    # D derived-taint reach: the summary derived from user-42 is also gone
    ok["D derived summary erased (taint reach)"] = all(x["id"] != s for x in m.items)

    # unrelated subject survives (no over-erasure)
    ok["(sanity) unrelated subject kept"] = any("risk cap" in x["text"] for x in m.items)

    # E a plain forget() with NO tombstone still flags as out-of-band
    m2, pk2 = _store(pathlib.Path(tempfile.mkdtemp()))
    x = m2.remember("secret to be scrubbed", source={"doc": "u"})
    m2.remember("kept", source={"doc": "v"})
    m2.forget(ids=[x])                       # plain delete, no tombstone
    okE, probsE = m2.verify_writes(expected_pubkey=pk2)
    ok["E plain forget still flags tampering"] = (not okE) and any("out-of-band" in p for p in probsE)

    # F forged/tampered tombstone caught
    m3, pk3 = _store(pathlib.Path(tempfile.mkdtemp()))
    y = m3.remember("erase me", source={"doc": "user-9"})
    m3.forget_subject("user-9", request_id="req-1")
    assert m3.verify_writes(expected_pubkey=pk3)[0]
    m3._tombstones[0]["request_id"] = "req-FORGED"   # tamper with the ledger after the fact
    okF, probsF = m3.verify_writes(expected_pubkey=pk3)
    ok["F forged tombstone detected"] = (not okF) and any("tombstone" in p for p in probsF)

    # G erasure_report is content-free
    rep = m.erasure_report()
    ok["G erasure_report content-free"] = (rep["tombstoned_total"] == len(r["ids"])
        and all(set(e.keys()) == {"memory_id", "ts", "request_id", "signed"} for e in rep["erasures"]))

    print("=" * 70)
    print("forget_subject + deletion tombstone — erasure that keeps the audit honest")
    print("=" * 70)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("-" * 70)
    print(f"erased {r['erased']} records (request {r['request_id']}), {r['tombstones']} tombstones")
    print("RECEIPT:", "VALID — all checks hold" if all(ok.values()) else "INVALID — do not ship")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
