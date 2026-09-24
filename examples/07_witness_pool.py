"""Witness pool -- k-of-n co-signing that makes a compromised memory host unable to show two different
histories to different clients. Runnable end-to-end.

A store's anchor() is a signed tree head committing to its whole write+erasure history. On its own it catches
a rewrite on ONE timeline (verify_consistency), but not a SPLIT-VIEW: an operator that shows client A one
history and client B another. Independent witnesses that co-sign the head close that -- an honest witness
refuses to co-sign a fork, so a client requiring k-of-n cannot be shown a forked head that reaches threshold.

Run: pip install "inspeximus[crypto]"  &&  python examples/07_witness_pool.py
"""
from inspeximus.core import Inspeximus
from inspeximus.witness_pool import Witness, collect_cosignatures

STORE_ID = "acme-prod-memory"

def main():
    # --- an inspeximus store with tamper-evident receipts, emitting a signed tree head ---
    m = Inspeximus(path=None, receipts=True)
    m.remember("customer 42 opted out of marketing", key="cust42::marketing", object="opted_out")
    m.remember("customer 42 email is a@b.com", key="cust42::email", object="a@b.com")
    anchor = m.anchor()
    print(f"store head: n_writes={anchor['n_writes']} sth={anchor['sth_hash'][:16]}...\n")

    # --- three INDEPENDENT witnesses (separate parties; here local, in real life separate hosts/keys) ---
    witnesses = [Witness() for _ in range(3)]
    allow = [w.public for w in witnesses]

    # --- client collects co-signatures and requires 2-of-3 ---
    out = collect_cosignatures(STORE_ID, anchor, witnesses)
    v = Inspeximus.verify_cosigned_anchor(anchor, out["cosignatures"], allow, threshold=2)
    print(f"client 2-of-3 check: ok={v['ok']} count={v['count']} refused={out['refused']}")
    assert v["ok"], "honest head should pass k-of-n"

    # --- the store grows honestly; witnesses happily re-sign the extended head ---
    m.remember("customer 42 corrected email to c@d.com", key="cust42::email", object="c@d.com")  # supersedes
    anchor2 = m.anchor()
    out2 = collect_cosignatures(STORE_ID, anchor2, witnesses)
    print(f"honest extension (n_writes {anchor['n_writes']}->{anchor2['n_writes']}): "
          f"signed={len(out2['cosignatures'])} refused={out2['refused']}\n")
    assert len(out2["cosignatures"]) == 3

    # --- now a COMPROMISED operator forks: a DIFFERENT history at the same size shown to another client ---
    forked = dict(anchor2); forked["writes_tip"] = "f0rged" + anchor2["writes_tip"][6:]
    from inspeximus.core import _sha256_hex, _canon
    forked["sth_hash"] = _sha256_hex(_canon({k: forked[k] for k in
                         ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip")}))
    out3 = collect_cosignatures(STORE_ID, forked, witnesses)
    print(f"operator shows a FORKED head (same size, different tip):")
    print(f"  signatures obtained: {len(out3['cosignatures'])}  (need 2 to fool a client)")
    for r in out3["refused"]:
        print(f"  witness {r['index']} REFUSED: {r['reason'][:70]}...")
    v3 = Inspeximus.verify_cosigned_anchor(forked, out3["cosignatures"], allow, threshold=2)
    print(f"  client 2-of-3 on the fork: ok={v3['ok']}  -> the fork cannot reach threshold.\n")
    assert not v3["ok"], "the fork must fail the k-of-n check"

    print("RESULT: a compromised host cannot show two different memory histories that both pass k-of-n --\n"
          "        honest witnesses refuse the fork, and detect_split_view() would prove it if one signed both.")

if __name__ == "__main__":
    main()
