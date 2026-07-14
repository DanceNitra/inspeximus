"""
forget_verification_bench.py — an OPEN benchmark for a capability no memory leaderboard scores yet: after a
right-to-erasure deletion, did the subject's data provably stop being recoverable ACROSS THE FAN-OUT? MIT.

WHY (prior-art-checked): recall boards (LoCoMo, LongMemEval, BEAM) score retrieval; the governance survey
(arXiv:2604.16548) flags store/forget + auditability as under-studied with "no architecture covering all nine
governance primitives", and no public leaderboard scores "does the deleted fact still influence retrieval, and
can you PROVE it, across every store the data leaked into." This defines that eval and scores it, the same way
we defined the cross-system integrity benchmark.

THE SCENARIO (standardized). A subject's sensitive value lives in the fan-out a real RAG/agent stack has:
  1. the primary text/log store        (TextStoreProbe)      3. an embedding/response cache   (KVCacheProbe)
  2. the vector index (embeddings)      (VectorIndexProbe)    4-6. soft-delete residue in Qdrant / pgvector / S3
Two deletion strategies are graded:
  - HARD delete  : purge every store correctly              -> should verify (score 1.0)
  - SOFT delete  : delete the primary row only (the common bug) -> should LEAK (score < 1.0), naming each store
FORGET-VERIFICATION SCORE = fraction of registered stores from which the value is NO LONGER recoverable after the
deletion. A system passes only at 1.0 AND with a signed receipt that verifies. Any system can implement the six
probes against its own stores and report its score; the harness + scoring are the contribution.

RUN:  python forget_verification_bench.py        (optional local Ollama nomic embedder for the vector probe)
"""
import json, os, sys, urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from mnemo import new_source_keypair
from mnemo.erasure_auditor import (  # noqa: E402
    ErasureAuditor, TextStoreProbe, VectorIndexProbe, KVCacheProbe,
    QdrantSoftDeleteProbe, PgVectorSoftDeleteProbe, S3VersioningProbe,
    verify_compliance_receipt, ed25519_signer, ed25519_verify,
)

SUBJECT = "user:alice"
VALUE = "account balance 12.69"


def _embed():
    """Local nomic embedder if present, else a cheap deterministic hashing embedder (keeps the bench runnable)."""
    try:
        urllib.request.urlopen(urllib.request.Request(
            "http://localhost:11434/api/embed",
            data=json.dumps({"model": "nomic-embed-text", "input": ["ping"]}).encode(),
            headers={"Content-Type": "application/json"}), timeout=5).read()

        def e(t):
            r = urllib.request.urlopen(urllib.request.Request(
                "http://localhost:11434/api/embed",
                data=json.dumps({"model": "nomic-embed-text", "input": [t]}).encode(),
                headers={"Content-Type": "application/json"}), timeout=30)
            return json.loads(r.read())["embeddings"][0]
        return e, "nomic-embed-text"
    except Exception:
        import hashlib
        def e(t):
            h = hashlib.sha256(t.lower().encode()).digest()
            return [b / 255.0 for b in h]           # 32-dim deterministic; enough to separate the fixture
        return e, "hash-fallback"


# --- fake external clients so the standardized scenario runs anywhere (a real deployment passes real clients) ---
class _Q:
    def __init__(self, d, a): self.d, self.a = d, a
    def get_collection(self, n): return {"deleted_vectors_count": self.d, "points_count": self.a}
class _Pg:
    def __init__(self, dead): self.dead = dead
    def cursor(self):
        pg = self
        class C:
            def execute(self, s, p=None): self.r = (pg.dead,)
            def fetchone(self): return self.r
        return C()
class _S3:
    def __init__(self, v): self.v = v
    def list_object_versions(self, Bucket, Prefix=""): return {"Versions": [{}] * self.v, "DeleteMarkers": []}


def build(strategy, embed):
    """Register all six stores with the subject's value planted, then apply `strategy` ('hard' | 'soft')."""
    vec = VectorIndexProbe("vector-index", embed)
    vec.add(SUBJECT, f"{SUBJECT} :: {VALUE}")
    text_rows = [f"{SUBJECT} statement: {VALUE}"]
    cache = {"k1": f"cached embedding input: {VALUE}"}
    q, pg, s3 = _Q(5, 995), _Pg(7), _S3(1)                    # soft-delete residue present by default
    if strategy == "soft":
        text_rows = []                                        # the common bug: delete the primary ROW and log it,
        #                                                       but the vector index, cache, and versioned stores
        #                                                       are never touched -> the data is still recoverable
    if strategy == "hard":
        vec.purge(SUBJECT)                                    # correct hard-delete + reindex
        text_rows = []                                        # purged
        cache = {}
        q, pg, s3 = _Q(0, 1000), _Pg(0), _S3(0)               # compaction/vacuum ran; versions destroyed
    a = ErasureAuditor()
    a.register(TextStoreProbe("primary-log", text_rows))
    a.register(vec)
    a.register(KVCacheProbe("embed-cache", cache))
    a.register(QdrantSoftDeleteProbe("qdrant", q, "c"))
    a.register(PgVectorSoftDeleteProbe("pgvector", pg, "docs"))
    a.register(S3VersioningProbe("s3-snapshots", s3, "b", f"{SUBJECT}/"))
    return a


def score(auditor):
    rep = auditor.audit(SUBJECT, [VALUE, "12.69"])
    n = len(rep["results"])
    clean = sum(1 for r in rep["results"] if not r["recoverable"])
    return clean / n, rep


def main():
    embed, embname = _embed()
    sk, pk = new_source_keypair()
    print(f"\nForget-Verification Benchmark — 6-store fan-out, embedder={embname}\n")
    print(f"{'strategy':<12} {'score':>6}  {'verdict':<9} {'signed-receipt':<15} leaking stores")
    print("-" * 78)
    for strat in ("soft", "hard"):
        a = build(strat, embed)
        s, rep = score(a)
        receipt = a.compliance_receipt(SUBJECT, [VALUE, "12.69"], sign=ed25519_signer(sk), pubkey=pk,
                                       request_id=f"dsar-{strat}", basis="GDPR Art.17", now=1_700_000_000.0)
        ok, _ = verify_compliance_receipt(receipt, ed25519_verify, expected_pubkey=pk)
        verdict = "VERIFIED" if rep["erasure_verified"] else "LEAK"
        print(f"{strat:<12} {s:>6.2f}  {verdict:<9} {'ok' if ok else 'FAIL':<15} {rep['leaking_stores']}")
    out = os.path.join(os.path.dirname(__file__), "..", "agora_output", "lab", "data", "forget_verification_bench.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    a = build("soft", embed); s, rep = score(a)
    json.dump({"subject": SUBJECT, "stores": rep["stores_audited"], "soft_delete_score": round(s, 3),
               "soft_delete_leaks": rep["leaking_stores"], "embedder": embname}, open(out, "w"), indent=2)
    print(f"\nA system passes only at score 1.00 AND a receipt that verifies. wrote {out}")


if __name__ == "__main__":
    main()
