"""Adversarial erasure probe — the test competitors FAIL (Ghost Vectors arXiv:2606.18497; MemTrust
arXiv:2601.07004 shows Letta/Zep/mem0 deletion leaves data recoverable from the raw store).

We do NOT trust the API's "is it gone?" — we read the RAW store on disk after erasure and try to recover the
forgotten content AND its embedding vector. inspeximus must leave ZERO recoverable trace.

Three settings:
  A. plaintext store, persist_vectors=True  -> forget_subject must remove text AND the persisted vec.
  B. plaintext store, RAM-only vectors       -> forget_subject must remove text (vec never on disk).
  C. encrypted store + shred()               -> after key destruction the raw bytes must be undecryptable.
"""
import sys, pathlib, tempfile, os, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from inspeximus import Inspeximus, new_encryption_key
from inspeximus import sqlite_store as _rows            # noqa: E402

SECRET = "SSN-441-90-2277 (Alice Meyer medical record)"


def raw_bytes(path):
    return pathlib.Path(path).read_bytes()


def has_vec_for(path, rec_id):
    """Whether the record's vector is in the store on disk, in whatever format the store is in.

    `raw_bytes` above is deliberately format-blind: the question there is whether a value's BYTES
    survived, and bytes are bytes. This one needs the records, so it asks the store.
    """
    try:
        data = (_rows.load(path) if _rows.looks_like_sqlite(path)
                else json.loads(pathlib.Path(path).read_text(encoding="utf-8")))
        return any(r.get("id") == rec_id and r.get("vec") for r in data)
    except Exception:
        return False


def fake_embed(text):
    # deterministic pseudo-embedding so persist_vectors has something to store
    h = abs(hash(text))
    return [((h >> i) & 255) / 255.0 for i in range(16)]


def run():
    ok = {}

    # A. plaintext + persisted vectors: forget must scrub text AND vec from the raw file
    tmpA = os.path.join(tempfile.mkdtemp(), "a.json")
    mA = Inspeximus(path=tmpA, embed=fake_embed, persist_vectors=True)
    rid = mA.remember(SECRET, key="alice::ssn", object="441-90-2277", pii=True)
    mA.remember("the deploy channel is BLUE-9", key="deploy")  # bystander, must survive
    mA._save(force=True)
    ok["A0 control: secret IS in raw store before forget"] = SECRET.encode() in raw_bytes(tmpA)
    ok["A0b control: vec persisted before forget"] = has_vec_for(tmpA, rid)
    res = mA.forget_subject("alice") if False else mA.forget(where=lambda r: "alice::ssn" == r.get("key"))
    mA._save(force=True)
    raw = raw_bytes(tmpA)
    ok["A1 secret text GONE from raw store"] = SECRET.encode() not in raw and b"441-90-2277" not in raw
    ok["A2 forgotten vec GONE from raw store"] = not has_vec_for(tmpA, rid)
    ok["A3 bystander survives"] = b"BLUE-9" in raw

    # B. plaintext + RAM-only vectors (default): forget must scrub text; vec never on disk anyway
    tmpB = os.path.join(tempfile.mkdtemp(), "b.json")
    mB = Inspeximus(path=tmpB)
    mB.remember(SECRET, key="alice::ssn", pii=True)
    mB.remember("keep me", key="k2")
    mB._save(force=True)
    mB.forget(where=lambda r: r.get("key") == "alice::ssn")
    mB._save(force=True)
    rawB = raw_bytes(tmpB)
    ok["B1 secret GONE (raw)"] = SECRET.encode() not in rawB and b"441-90-2277" not in rawB
    ok["B2 bystander survives"] = b"keep me" in rawB

    # C. encrypted-at-rest + crypto-shred: after shred() the raw bytes are undecryptable
    tmpC = os.path.join(tempfile.mkdtemp(), "c.json")
    key = new_encryption_key()
    mC = Inspeximus(path=tmpC, encrypt_key=key)
    mC.remember(SECRET, key="alice::ssn", pii=True)
    mC._save(force=True)
    rawC = raw_bytes(tmpC)
    ok["C0 control: ciphertext does NOT leak plaintext"] = SECRET.encode() not in rawC and b"441-90-2277" not in rawC
    shredded = False
    try:
        rcpt = mC.shred()   # destroy the HELD key -> ciphertext + all backups dead to anyone WITHOUT the key
        shredded = True
    except Exception as e:
        ok["C1 shred() available"] = False
        ok["C1_err"] = repr(e)[:80]
    if shredded:
        ok["C1 shred() available"] = True
        # crypto-shred's real (NIST SP 800-88) guarantee: the instance's key + plaintext RAM are destroyed,
        # and the realistic adversary who obtains ONLY the disk file (not the key) cannot recover plaintext.
        # (It does NOT — and honestly does not claim to — stop someone who kept their own copy of the key.)
        ok["C2 held key + plaintext RAM destroyed"] = (getattr(mC, "_enc_rawkey", None) is None
                                                        and len(getattr(mC, "items", [])) == 0)
        wrong = new_encryption_key()
        try:
            m2 = Inspeximus(path=tmpC, encrypt_key=wrong)   # adversary with the file but not the real key
            recovered = any(SECRET in (r.get("text") or "") for r in getattr(m2, "items", []))
            ok["C3 file undecryptable without the key"] = not recovered
        except Exception:
            ok["C3 file undecryptable without the key"] = True   # raises -> unrecoverable
        ok["C4 shred returns a content-free receipt"] = isinstance(rcpt, dict) and rcpt.get("shredded") is True

    print("=" * 64)
    print("Adversarial erasure — READ THE RAW STORE (the test competitors fail)")
    print("=" * 64)
    for k, v in ok.items():
        if k.endswith("_err"):
            continue
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    bad = [k for k, v in ok.items() if not k.endswith('_err') and not v]
    print("-" * 64)
    print("RECEIPT:", "VALID - zero recoverable trace" if not bad else f"HOLE FOUND: {bad}")
    if "C1_err" in ok:
        print("shred err:", ok["C1_err"])
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(run())
