"""forget() must leave a deletion receipt, or the store accuses itself of tampering.

Before 1.24.0 only forget_subject() and forget_pii() emitted tombstones. A record removed with plain
forget() therefore left a write receipt pointing at a row that no longer existed, with nothing
accounting for its absence — and verify_writes() reported exactly what it is designed to report in
that situation: "deleted out-of-band", the signature of someone editing the store behind its back.

Found by running the published wheel in a clean room and checking the claim "erasure with signed
receipts" against what the API actually does: the record was gone, the bytes were gone, and the
receipt count was zero.
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from mnemo.mnemo import Mnemo  # noqa: E402


def main():
    d = pathlib.Path(tempfile.mkdtemp(prefix="forget_tomb_"))
    m = Mnemo(path=str(d / "store.jsonl"))
    keep = m.remember("My manager is Rachel Tseng.")
    drop = m.remember("My employee ID is MCG-20250115-47.")
    m._save(force=True)

    res = m.forget(ids=drop, request_id="req-42", basis="user erasure request")
    print(f"forget() -> {res}")

    ok = True

    # 1. the record and its bytes are gone
    on_disk = (d / "store.jsonl").read_text(encoding="utf-8", errors="replace")
    gone = "MCG-20250115-47" not in on_disk and not any(r["id"] == drop for r in m.items)
    print(f"[1] record and bytes erased          : {gone}")
    ok &= gone

    # 2. a tombstone exists for it, carrying the caller's request_id and basis
    toms = [t for t in m._tombstones if t.get("memory_id") == drop]
    has_tomb = len(toms) == 1
    print(f"[2] exactly one tombstone emitted    : {has_tomb}")
    ok &= has_tomb
    if has_tomb:
        auth = toms[0].get("auth") or {}
        carried = toms[0].get("request_id") == "req-42" and auth.get("basis") == "user erasure request"
        print(f"[3] request_id + basis committed     : {carried}")
        ok &= carried

    # 3. the store's own audit no longer calls this tampering
    v = m.verify_writes()
    problems = v.get("problems") if isinstance(v, dict) else v
    out_of_band = [p for p in (problems or []) if "out-of-band" in str(p)]
    print(f"[4] verify_writes has no out-of-band : {not out_of_band}  ({problems or 'clean'})")
    ok &= not out_of_band

    # 4. the surviving record is untouched
    survived = any(r["id"] == keep for r in m.items)
    print(f"[5] unrelated record survived        : {survived}")
    ok &= survived

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
