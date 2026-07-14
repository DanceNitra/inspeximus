"""1.4.0: soft-delete residual probes for the ErasureAuditor (the r/RAG 'API 200 != gone' gap).
Each probe is tested with a FAKE client that mimics the store's API surface — no real service, no new dependency."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mnemo.erasure_auditor import (
    ErasureAuditor, SoftDeleteProbe, QdrantSoftDeleteProbe, PgVectorSoftDeleteProbe, S3VersioningProbe,
)


# ---- Qdrant: deleted vectors present below the optimizer threshold ----
class _FakeQdrant:
    def __init__(self, deleted, alive):
        self._d, self._a = deleted, alive

    def get_collection(self, name):
        return {"deleted_vectors_count": self._d, "points_count": self._a}


def test_qdrant_pending_compaction_leaks():
    # 5 deleted / 995 alive -> frac 0.005 < 0.2 threshold -> compaction pending -> still on disk
    p = QdrantSoftDeleteProbe("qdrant", _FakeQdrant(5, 995), "c")
    r = p.recover("alice", ["secret"])
    assert r["recoverable"] is True and r["detail"]["compaction_pending"] is True


def test_qdrant_no_deletes_is_clean():
    p = QdrantSoftDeleteProbe("qdrant", _FakeQdrant(0, 1000), "c")
    assert p.recover("alice", ["secret"])["recoverable"] is False


# ---- pgvector: dead tuples until VACUUM ----
class _FakeCursor:
    def __init__(self, dead): self._dead = dead; self._row = None
    def execute(self, sql, params=None): self._row = (self._dead,)
    def fetchone(self): return self._row


class _FakeConn:
    def __init__(self, dead): self._dead = dead
    def cursor(self): return _FakeCursor(self._dead)


def test_pgvector_dead_tuples_leak():
    p = PgVectorSoftDeleteProbe("pg", _FakeConn(dead=7), "docs")
    r = p.recover("alice", ["secret"])
    assert r["recoverable"] is True and r["detail"]["n_dead_tup"] == 7


def test_pgvector_after_vacuum_clean():
    p = PgVectorSoftDeleteProbe("pg", _FakeConn(dead=0), "docs")
    assert p.recover("alice", ["secret"])["recoverable"] is False


# ---- S3 versioning: a delete is just a delete marker ----
class _FakeS3:
    def __init__(self, versions, markers): self._v, self._m = versions, markers
    def list_object_versions(self, Bucket, Prefix=""):
        return {"Versions": [{"Key": Prefix}] * self._v, "DeleteMarkers": [{"Key": Prefix}] * self._m}


def test_s3_delete_marker_hides_live_version():
    p = S3VersioningProbe("s3", _FakeS3(versions=1, markers=1), "bucket", "user/alice/")
    r = p.recover("alice", ["secret"])
    assert r["recoverable"] is True and r["detail"]["object_versions_present"] == 1


def test_s3_fully_purged_clean():
    p = S3VersioningProbe("s3", _FakeS3(versions=0, markers=0), "bucket", "user/alice/")
    assert p.recover("alice", ["secret"])["recoverable"] is False


# ---- generic SoftDeleteProbe (observability spans / CDC / embedding logs) ----
def test_generic_soft_delete_probe():
    seen = {"present": True}
    p = SoftDeleteProbe("langsmith-spans", lambda subj, vals: (seen["present"], {"span_ids": ["s1"]}))
    assert p.recover("alice", ["secret"])["recoverable"] is True
    seen["present"] = False
    assert p.recover("alice", ["secret"])["recoverable"] is False


# ---- integration through the auditor ----
def test_auditor_reports_soft_delete_leaks():
    a = (ErasureAuditor()
         .register(QdrantSoftDeleteProbe("qdrant", _FakeQdrant(5, 995), "c"))
         .register(PgVectorSoftDeleteProbe("pg", _FakeConn(dead=3), "docs"))
         .register(S3VersioningProbe("s3", _FakeS3(1, 1), "b", "p")))
    rep = a.audit("alice", ["secret"])
    assert rep["erasure_verified"] is False
    assert set(rep["leaking_stores"]) == {"qdrant", "pg", "s3"}


def test_auditor_verified_when_all_purged():
    a = (ErasureAuditor()
         .register(QdrantSoftDeleteProbe("qdrant", _FakeQdrant(0, 1000), "c"))
         .register(PgVectorSoftDeleteProbe("pg", _FakeConn(0), "docs"))
         .register(S3VersioningProbe("s3", _FakeS3(0, 0), "b", "p")))
    rep = a.audit("alice", ["secret"])
    assert rep["erasure_verified"] is True and rep["leaking_stores"] == []
