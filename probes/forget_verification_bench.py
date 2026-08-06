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
from inspeximus import new_source_keypair
from inspeximus.erasure_auditor import (  # noqa: E402
    ErasureAuditor, TextStoreProbe, VectorIndexProbe, KVCacheProbe,
    QdrantSoftDeleteProbe, PgVectorSoftDeleteProbe, S3VersioningProbe,
    verify_compliance_receipt, ed25519_signer, ed25519_verify,
)

SUBJECT = "user:alice"
VALUE = "account balance 12.69"

# A DERIVED record: it quotes the subject's value, but its own subject line is the ROTATION, not the
# person. Added 2026-08-06; the conclusion first drawn from it was WRONG and is corrected below.
#
# WHAT STANDS: a subject-keyed purge, executed correctly across all six stores, scores 1.00 on the
# original fixture and 0.67 once one derived record exists. That is real and it reproduces.
#
# WHAT WAS WITHDRAWN, same day, before any of it was published:
#   1. NOVELTY. This is already scored. Chen et al., arXiv:2606.10062 (8 Jun 2026) define a Forgetting
#      Residue Score and report "raw-only deletion leaves derived summary copies recoverable in
#      approximately 20% of instances"; Zhao et al., MemoRepair, arXiv:2605.07242 (8 May 2026)
#      formalise the cascade update problem and take invalidated-memory exposure from 69.8-94.3% to 0%.
#      The mechanism is older still: Buneman/Khanna/Tan, deletion propagation through views (PODS
#      2002), and Green/Karvounarakis/Tannen, provenance semirings (PODS 2007).
#   2. THE DECISIVE ARM WAS CIRCULAR. A "lineage+derived" arm purged DERIVED_SUBJECT -- a constant
#      declared eighty lines above it. That is a second subject-keyed purge told the answer in advance,
#      and `erasure_auditor.py` contains no lineage concept at all. Replaced by the arm below.
#   3. "ONLY LINEAGE REACHES IT" IS FALSE, and the harness's own detector says so: TextStoreProbe finds
#      the derived row by verbatim value substring, so a VALUE-keyed purge reaches it too. Measured:
#      value+derived scores 1.00 with no provenance graph anywhere.
#   4. AND THE REMEDY WOULD HAVE POINTED AT AN EMPTY FIELD. This library's own writer-declared lineage
#      measured 0.00% across a 27,290-record production store (CHANGELOG 1.49.0-1.50.0).
DERIVED_SUBJECT = "rotation:payments"
DERIVED = f"{DERIVED_SUBJECT} handover note: the balance on file is {VALUE}"


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
    """Register all six stores with the subject's value planted, then apply `strategy`.

    Strategies:
      soft              the common bug -- delete the primary row, leave the rest
      hard              purge every store correctly
      hard+derived      the same correct purge, on a fan-out that ALSO holds a derived record
      lineage+derived   purge by subject AND by lineage, which is the only one that reaches it

    The first two are the original published scenario and are unchanged, so 0.17 / 1.00 still
    reproduce; the derived-record arms are added beside them rather than folded in.
    """
    with_derived = strategy.endswith("+derived")
    vec = VectorIndexProbe("vector-index", embed)
    vec.add(SUBJECT, f"{SUBJECT} :: {VALUE}")
    if with_derived:
        vec.add(DERIVED_SUBJECT, DERIVED)
    text_rows = [f"{SUBJECT} statement: {VALUE}"] + ([DERIVED] if with_derived else [])
    cache = {"k1": f"cached embedding input: {VALUE}"}
    q, pg, s3 = _Q(5, 995), _Pg(7), _S3(1)                    # soft-delete residue present by default
    if strategy == "soft":
        text_rows = []                                        # the common bug: delete the primary ROW and log it,
        #                                                       but the vector index, cache, and versioned stores
        #                                                       are never touched -> the data is still recoverable
    if strategy != "soft":
        # A SUBJECT-KEYED purge done properly across all six stores. This is what a careful team ships,
        # and on the original fixture (no derived record) it is complete -- `hard` scores 1.00.
        vec.purge(SUBJECT)                                    # correct hard-delete + reindex
        text_rows = [r for r in text_rows if SUBJECT not in r]  # every row attributable to the subject, gone
        cache = {}
        q, pg, s3 = _Q(0, 1000), _Pg(0), _S3(0)               # compaction/vacuum ran; versions destroyed
    if strategy == "value+derived":
        # VALUE-KEYED: drop anything carrying the value, whatever subject it is filed under. No
        # provenance graph, no derived_from, no lineage. This is the arm the first version of this
        # file failed to run, and it is the one that falsifies "only lineage-aware erasure reaches
        # it" -- the detector locates the row by verbatim value, so a purge keyed the same way
        # reaches it by the same route.
        vec.purge(DERIVED_SUBJECT)
        text_rows = [r for r in text_rows if VALUE not in r]
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
    for strat in ("soft", "hard", "hard+derived", "value+derived"):
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
