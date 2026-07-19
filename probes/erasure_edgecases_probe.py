"""Bulletproof erasure — every place PII could hide. For each edge case: put the secret in that position,
run forget_subject (+ shred for the encrypted case), then read EVERY raw file in the store dir (main store +
ALL sidecars: .receipts.json, .tombstones.json, .cusum.json, .irrev.json, .vecs, tmp) and confirm the secret
is unrecoverable. A single residue = a hole to fix before anything ships."""
import sys, pathlib, tempfile, os, glob
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mnemo import Mnemo, new_encryption_key

SEC = "Alice-SSN-441-90-2277"
SB = SEC.encode()


def residue(d):
    hits = []
    for f in glob.glob(os.path.join(d, "**", "*"), recursive=True):
        if os.path.isfile(f):
            try:
                if SB in pathlib.Path(f).read_bytes():
                    hits.append(os.path.basename(f))
            except Exception:
                pass
    return hits


def fresh():
    return os.path.join(tempfile.mkdtemp(), "s.json")


def run():
    ok = {}
    sk, pub = new_encryption_key.__self__ if False else (None, None)

    # E1 — superseded STALE value with PII (the retired version must also be erased)
    p = fresh(); m = Mnemo(path=p)
    m.remember(SEC + " OLD", key="alice::ssn", source={"doc": "alice"}, pii=True)
    m.remember(SEC + " NEW", key="alice::ssn", source={"doc": "alice"}, pii=True)   # supersedes -> OLD retired
    m._save(force=True)
    ok["E1a control: secret present before"] = bool(residue(os.path.dirname(p)))
    m.forget_subject("alice", request_id="r1"); m._save(force=True)
    ok["E1 superseded stale value erased"] = not residue(os.path.dirname(p))

    # E2 — derived lineage (a summary built from the subject's data) erased via derived_from taint
    p = fresh(); m = Mnemo(path=p)
    rid = m.remember(SEC, key="alice::ssn", source={"doc": "alice"}, pii=True)
    m.remember("summary containing " + SEC, derived=True, derived_from=[rid])
    m._save(force=True)
    m.forget_subject("alice", request_id="r2"); m._save(force=True)
    ok["E2 derived-lineage record erased"] = not residue(os.path.dirname(p))

    # E3 — PII hidden only in meta (not the text)
    p = fresh(); m = Mnemo(path=p)
    m.remember("innocuous note", key="alice::rec", source={"doc": "alice"}, meta={"ssn": SEC}, pii=True)
    m._save(force=True)
    ok["E3a control"] = bool(residue(os.path.dirname(p)))
    m.forget_subject("alice", request_id="r3"); m._save(force=True)
    ok["E3 PII-in-meta erased"] = not residue(os.path.dirname(p))

    # E4 — sidecars content-free: receipts + tombstones must NOT contain the PII
    p = fresh(); rk, rpub = _kp()
    m = Mnemo(path=p, receipts=True, receipt_key=rk, receipt_pubkey=rpub)
    m.remember(SEC, key="alice::ssn", source={"doc": "alice"}, pii=True)
    m._save(force=True)
    m.forget_subject("alice", request_id="r4"); m._save(force=True)
    side = residue(os.path.dirname(p))
    ok["E4 receipts/tombstones sidecars content-free"] = not side

    # E5 — persist_vectors + encryption + shred, all together
    p = fresh(); key = new_encryption_key()
    m = Mnemo(path=p, embed=lambda t: [float(len(t) % 7)] * 8, persist_vectors=True, encrypt_key=key)
    m.remember(SEC, key="alice::ssn", source={"doc": "alice"}, pii=True)
    m._save(force=True)
    ok["E5a control: ciphertext hides plaintext"] = not residue(os.path.dirname(p))   # encrypted -> no plaintext leak
    m.forget_subject("alice", request_id="r5"); m._save(force=True)
    m.shred()
    # after shred: no plaintext anywhere, and a fresh open with a WRONG key cannot recover
    ok["E5 no plaintext after forget+shred"] = not residue(os.path.dirname(p))
    try:
        m2 = Mnemo(path=p, encrypt_key=new_encryption_key())
        ok["E5b undecryptable without key"] = not any(SEC in (r.get("text") or "") for r in getattr(m2, "items", []))
    except Exception:
        ok["E5b undecryptable without key"] = True

    print("=" * 60)
    print("Bulletproof erasure — every place PII could hide")
    print("=" * 60)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    bad = [k for k, v in ok.items() if not v]
    print("-" * 60)
    print("RECEIPT:", "VALID - no residue anywhere" if not bad else f"HOLE(S): {bad}")
    return 0 if not bad else 1


def _kp():
    from mnemo import new_receipt_keypair
    return new_receipt_keypair()


if __name__ == "__main__":
    raise SystemExit(run())
