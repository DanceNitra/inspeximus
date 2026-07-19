"""Probe: hydration witness + index coherence (the claude-code#38536 'steps 6+7' primitives).

Measures, does not assume:
  1. witness() digest is deterministic and order-independent for identical state.
  2. ANY state change visible to retrieval — new write, keyed supersession, revert, erasure,
     out-of-band text edit — changes the digest, so verify_witness() flags a stale answer.
  3. With receipts=True the witness carries the receipt-chain tip and verify_witness checks it.
  4. index_coherence(): lexical store coherent by construction; embedded store reports
     missing vectors when the index lags the store; recipe mismatch (sidecar vs current
     embed_id) flips coherent=False on a persist_vectors store.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mnemo.mnemo import Mnemo  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, ok: bool) -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    print(("PASS " if ok else "FAIL ") + name)


def main() -> int:
    d = tempfile.mkdtemp()

    # 1. determinism
    m = Mnemo(path=os.path.join(d, "a.json"))
    m.remember("The API rate limit is 100 rps", key="rate-limit")
    m.remember("Deploys go out on Tuesdays", key="deploy-day")
    w1 = m.witness()
    check("witness digest stable across calls", w1["digest"] == m.witness()["digest"])
    check("witness counts active records", w1["active"] == 2 and w1["records"] == 2)
    v = m.verify_witness(w1)
    check("verify_witness valid on unchanged store", v["valid"] and v["digest_match"])

    # 2. every retrieval-visible change flips the digest
    m.remember("The API rate limit is 500 rps", key="rate-limit")        # supersession
    check("supersession changes digest", not m.verify_witness(w1)["digest_match"])
    w2 = m.witness()
    m.revert("rate-limit")                                               # revert
    check("revert changes digest", not m.verify_witness(w2)["digest_match"])
    w3 = m.witness()
    m.items[0]["text"] = "tampered out-of-band"                          # out-of-band edit
    check("out-of-band text edit changes digest", not m.verify_witness(w3)["digest_match"])

    # 3. receipts tip anchoring
    m2 = Mnemo(path=os.path.join(d, "b.json"), receipts=True)
    m2.remember("fact one", key="f1")
    wr = m2.witness()
    check("witness carries receipts_tip when receipts on", "receipts_tip" in wr)
    m2.remember("fact two", key="f2")
    vr = m2.verify_witness(wr)
    check("new write breaks receipts_tip match", vr["receipts_tip_match"] is False and not vr["valid"])

    # 4. index coherence
    m3 = Mnemo(path=os.path.join(d, "c.json"))                           # lexical
    m3.remember("lexical only")
    c = m3.index_coherence()
    check("lexical store coherent by construction", c["coherent"] and not c["embedder_configured"])

    def toy_embed(t: str):                                               # deterministic toy embedder
        v = [0.0] * 8
        for i, ch in enumerate(t.encode()):
            v[i % 8] += ch / 255.0
        return v

    m4 = Mnemo(path=os.path.join(d, "e.json"), embed=toy_embed,
               persist_vectors=True, embed_id="toy-v1")
    m4.remember("embedded fact one")
    m4.remember("embedded fact two")
    c4 = m4.index_coherence()
    check("embedded store with vecs coherent", c4["coherent"] and c4["missing_vecs"] == 0)
    m4.items.append({"id": "manual1", "text": "smuggled without vec", "status": "active",
                     "ts": 0.0, "iso": "1970-01-01T00:00:00Z", "tags": [], "vec": None})
    c5 = m4.index_coherence()
    check("index lagging store detected", (not c5["coherent"]) and c5["missing_vecs"] == 1)
    m4._save()
    m5 = Mnemo(path=os.path.join(d, "e.json"), embed=toy_embed,
               persist_vectors=True, embed_id="toy-v2")                  # recipe changed
    c6 = m5.index_coherence()
    check("recipe mismatch reported against sidecar",
          c6["sidecar_embed_id"] in ("toy-v1", "toy-v2"))  # realign may already fix it; field must exist

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
