"""erasure_auditor.py — after you run a right-to-erasure deletion, does the data STAY deleted across the fan-out?

DSAR platforms (BigID, OneTrust, Transcend, DataGrail) orchestrate cross-system deletion and log that deletion
EXECUTED. The gap none of them close (confirmed by a compliance-engineer review, 2026-07-13): none re-attempts
ADVERSARIAL RECOVERY of the subject's data from the surviving stores — "deletion executed" is not "content no
longer reconstructible". A row can be gone while the embedding that encodes it survives in the index, a cache,
an offline snapshot, a retrieval log, or the chat history — and a retained embedding reconstructs the content
(Morris et al., "Text Embeddings Reveal (Almost) As Much As Text", EMNLP 2023; Ghost Vectors, arXiv 2606.18497,
recovers deleted clinical attributes from soft-deleted HNSW vectors).

This is a TOOL, not a research claim: register every place a subject's data can live as a StoreProbe; after your
app has run its deletion, `audit()` runs a per-store adversarial recovery attempt and reports where the data is
STILL RECOVERABLE. Honest by construction — it reports survivors, never a false "erased"; `erasure_verified` is
True only if NO registered store recovered anything. It cannot prove physical destruction, cannot cover stores
you did not register, and the vector-recovery check is a LOWER BOUND on the leak (full inversion is stronger).
Prior art credited above; the fix, when a store leaks, is hard-delete+reindex or crypto-shredding (destroy the
key, not the row — EDPB 05/2019; NIST SP 800-88 cryptographic erase).

Zero external deps for the framework; the VectorIndexProbe needs an embedder you pass in (e.g. local nomic).
"""
from __future__ import annotations
import json
import re
import time


class StoreProbe:
    """One place a subject's data can live. Implement recover(): adversarially try to get the subject's sensitive
    `values` back out of THIS store after the app's deletion, and report whether any survived."""
    name = "unnamed-store"
    kind = "generic"

    def recover(self, subject: str, values) -> dict:
        raise NotImplementedError


class TextStoreProbe(StoreProbe):
    """A text-bearing store the app may forget to purge: retrieval logs, prompt logs, chat history, a doc store.
    Recovery = the subject's value still appears verbatim (substring/word-boundary) in the retained text."""
    kind = "text"

    def __init__(self, name: str, texts):
        self.name = name
        self._get = texts if callable(texts) else (lambda: list(texts))

    def recover(self, subject, values):
        blob = " \n ".join(self._get() or []).lower()
        hits = [v for v in values if re.search(r"(?<![a-z0-9])" + re.escape(v.lower()) + r"(?![a-z0-9])", blob)]
        return {"recoverable": bool(hits), "method": "verbatim-substring", "recovered": hits}


class VectorIndexProbe(StoreProbe):
    """The wedge DSAR tools miss: an app vector index where a "deleted" row's EMBEDDING may survive (soft-delete,
    snapshot, replica). Recovery is ADVERSARIAL — not 'is the row present' but 'can the content be reconstructed
    from the surviving vectors': embed candidate value-templates and check whether the nearest retained vector
    reconstructs the subject's true value (a closed-set lower bound on embedding inversion, Morris 2023)."""
    kind = "vector"

    def __init__(self, name: str, embed, sim=None):
        self.name = name
        self.embed = embed
        self.sim = sim or _cosine
        self._vectors = []           # list of (subject, text, vector) still physically in the index

    def add(self, subject, text):
        self._vectors.append((subject, text, self.embed(text)))

    def purge(self, subject):        # what a correct hard-delete would do
        self._vectors = [(s, t, v) for (s, t, v) in self._vectors if s != subject]

    def recover(self, subject, values, template="{subject} :: {value}", candidates=None):
        if not self._vectors:
            return {"recoverable": False, "method": "nn-inversion", "recovered": []}
        recovered = []
        for v in values:
            cands = list(candidates) if candidates else [v]      # attacker's candidate set (>= the true value)
            if v not in cands:
                cands.append(v)
            qv = {c: self.embed(template.format(subject=subject, value=c)) for c in cands}
            best = max(self._vectors, key=lambda row: max(self.sim(row[2], qv[c]) for c in cands))
            # which candidate does the surviving vector most resemble?
            top = max(cands, key=lambda c: self.sim(best[2], qv[c]))
            if top == v:
                recovered.append(v)
        return {"recoverable": bool(recovered), "method": "nn-inversion (lower bound)", "recovered": recovered}


class KVCacheProbe(StoreProbe):
    """An embedding-API response cache or a Redis retrieval cache keyed by content — a store deletions routinely
    miss. Recovery = the subject's value is still present in a cached value."""
    kind = "cache"

    def __init__(self, name: str, cache):
        self.name = name
        self._get = cache if callable(cache) else (lambda: dict(cache))

    def recover(self, subject, values):
        blob = " ".join(str(x) for x in (self._get() or {}).values()).lower()
        hits = [v for v in values if v.lower() in blob]
        return {"recoverable": bool(hits), "method": "cache-value-scan", "recovered": hits}


class SoftDeleteProbe(StoreProbe):
    """The gap a Reddit r/RAG thread (2026-07) surfaced: a store reports a delete as DONE (HTTP 200) while the
    data physically SURVIVES until a background process runs — the '200' and 'it's gone' are separated by a
    compaction/vacuum/GC that may never trigger. Generic escape hatch for any such store (an uncompacted Chroma
    segment, an observability span carrying full chunk text, a CDC/Kafka topic with long retention, an
    embedding-provider request log): supply `residual(subject, values) -> (present: bool, detail)` that queries
    THIS store's PHYSICAL state. Recovery = logically deleted yet physically present."""
    kind = "soft-delete"

    def __init__(self, name: str, residual):
        self.name = name
        self._residual = residual

    def recover(self, subject, values):
        present, detail = self._residual(subject, values)
        return {"recoverable": bool(present), "method": "soft-delete-residual",
                "recovered": (list(values) if present else []), "detail": detail}


class QdrantSoftDeleteProbe(StoreProbe):
    """Qdrant marks deleted points in a bitmask and only physically drops them once a segment crosses the
    optimizer's `deleted_threshold` (default 0.2, with a 1000-vector minimum) — so scattered deletes on a
    mid-size collection can sit on disk below that line indefinitely. Recovery = deleted vectors are still
    physically present with compaction not yet triggered. Pass your qdrant client + collection; mnemo has NO
    qdrant dependency — the probe only calls `get_collection` on the client you pass."""
    kind = "vector-soft-delete"

    def __init__(self, name: str, client, collection, deleted_threshold: float = 0.2, min_vectors: int = 1000):
        self.name = name
        self.client = client
        self.collection = collection
        self.deleted_threshold = deleted_threshold
        self.min_vectors = min_vectors

    def recover(self, subject, values):
        info = self.client.get_collection(self.collection)
        deleted = int(_dig(info, "deleted_vectors_count", "num_deleted_vectors", "deleted") or 0)
        alive = int(_dig(info, "points_count", "vectors_count") or 0)
        total = deleted + alive
        frac = (deleted / total) if total else 0.0
        # the optimizer compacts only once frac >= threshold AND total >= min_vectors; until then residue persists
        pending = deleted > 0 and (frac < self.deleted_threshold or total < self.min_vectors)
        return {"recoverable": bool(pending), "method": "qdrant-optimizer-pending",
                "recovered": (list(values) if pending else []),
                "detail": {"deleted_present": deleted, "deleted_frac": round(frac, 4),
                           "threshold": self.deleted_threshold, "compaction_pending": bool(pending)}}


class PgVectorSoftDeleteProbe(StoreProbe):
    """pgvector inherits Postgres MVCC: a deleted row's tuple is dead but stays ON DISK until VACUUM runs, and the
    HNSW graph is only repaired at vacuum time — so 'DELETE returned' is not 'gone from disk/index'. Recovery =
    dead tuples exist for the table (VACUUM pending). Pass a DB-API connection + table name; mnemo has NO psycopg
    dependency — the probe runs one read-only query on the cursor you pass."""
    kind = "vector-soft-delete"

    def __init__(self, name: str, conn, table):
        self.name = name
        self.conn = conn
        self.table = table

    def recover(self, subject, values):
        cur = self.conn.cursor()
        cur.execute("SELECT n_dead_tup FROM pg_stat_user_tables WHERE relname = %s", (self.table,))
        row = cur.fetchone()
        dead = int(row[0]) if row and row[0] is not None else 0
        return {"recoverable": dead > 0, "method": "pgvector-vacuum-pending",
                "recovered": (list(values) if dead > 0 else []),
                "detail": {"n_dead_tup": dead, "vacuum_pending": dead > 0}}


class S3VersioningProbe(StoreProbe):
    """A versioned object store (S3 + most snapshot stores): a 'delete' on a versioned bucket just writes a DELETE
    MARKER — the prior object version stays and is one list-versions call away. Recovery = object versions (or
    delete markers hiding live versions) still exist for the subject's prefix. Pass a boto3-style client + bucket
    + prefix; mnemo has NO boto3 dependency — the probe only calls `list_object_versions` on the client you pass."""
    kind = "object-store-soft-delete"

    def __init__(self, name: str, s3, bucket, prefix: str = ""):
        self.name = name
        self.s3 = s3
        self.bucket = bucket
        self.prefix = prefix

    def recover(self, subject, values):
        resp = self.s3.list_object_versions(Bucket=self.bucket, Prefix=self.prefix) or {}
        versions = resp.get("Versions") or []
        markers = resp.get("DeleteMarkers") or []
        recoverable = len(versions) > 0                  # any surviving physical version = data recoverable
        return {"recoverable": recoverable, "method": "s3-version-residual",
                "recovered": (list(values) if recoverable else []),
                "detail": {"object_versions_present": len(versions), "delete_markers": len(markers)}}


class ErasureAuditor:
    def __init__(self):
        self._probes: list[StoreProbe] = []

    def register(self, probe: StoreProbe) -> "ErasureAuditor":
        self._probes.append(probe)
        return self

    def compliance_receipt(self, subject: str, values, sign=None, pubkey=None, request_id=None,
                           basis=None, now=None, **probe_kwargs) -> dict:
        """Run the audit and package it as a shareable, optionally-SIGNED proof-of-erasure receipt — the artifact
        a DPO/auditor hands a regulator under GDPR Art. 17 or EU AI Act record-keeping. It records which stores
        were checked, the adversarial-recovery verdict per store, the subject/request/basis, and a timestamp;
        sign it with YOUR key so it is tamper-evident and verifiable (BYO: pass `sign(message_bytes)->hex` + the
        `pubkey`, e.g. `ed25519_signer(sk)` below, or an HSM/KMS signer). Honest scope (same as audit()): it
        attests the AUDIT OUTCOME at audit time — not physical destruction, not unregistered stores or backups."""
        audit = self.audit(subject, values, **probe_kwargs)
        receipt = {
            "receipt_version": 1,
            "subject": subject,
            "request_id": request_id,
            "basis": basis,
            "generated_unix": float(now if now is not None else time.time()),
            "erasure_verified": audit["erasure_verified"],
            "leaking_stores": audit["leaking_stores"],
            "stores_audited": audit["stores_audited"],
            "results": audit["results"],
            "scope": audit["scope"],
        }
        if sign is not None:
            receipt["signature"] = sign(_receipt_message(receipt))
            receipt["pubkey"] = pubkey
        return receipt

    def audit(self, subject: str, values, **probe_kwargs) -> dict:
        """After the app has run its deletion, adversarially probe every registered store for the subject's
        `values`. Returns a report; erasure_verified is True only if NO store recovered anything."""
        results = []
        for p in self._probes:
            try:
                if isinstance(p, VectorIndexProbe):
                    r = p.recover(subject, values, **{k: v for k, v in probe_kwargs.items()
                                                      if k in ("template", "candidates")})
                else:
                    r = p.recover(subject, values)
                err = None
            except Exception as e:
                r, err = {"recoverable": True, "method": "error", "recovered": []}, str(e)[:160]
            results.append({"store": p.name, "kind": p.kind, "recoverable": bool(r["recoverable"]),
                            "method": r["method"], "recovered": r.get("recovered", []), "error": err})
        leaks = [x["store"] for x in results if x["recoverable"]]
        return {
            "subject": subject,
            "stores_audited": [p.name for p in self._probes],
            "results": results,
            "erasure_verified": len(leaks) == 0,
            "leaking_stores": leaks,
            "scope": ("Audits ONLY the registered stores. 'erasure_verified' means no registered store still had "
                      "the data recoverable at audit time; it is NOT a proof of physical destruction, does not "
                      "cover unregistered stores or backups, and the vector check is a LOWER BOUND on embedding "
                      "inversion (Morris 2023). When a store leaks, hard-delete+reindex or crypto-shred the key."),
        }


def _receipt_message(receipt: dict) -> bytes:
    """Canonical bytes a compliance receipt signs over — the receipt minus the signature envelope, so signing
    and verification agree byte-for-byte regardless of key order."""
    body = {k: v for k, v in receipt.items() if k not in ("signature", "pubkey")}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_compliance_receipt(receipt: dict, verify, expected_pubkey: str | None = None) -> tuple:
    """Verify a signed compliance receipt: recompute the canonical bytes and check the signature with
    `verify(message_bytes, sig_hex, pubkey)->bool`. Pin `expected_pubkey` to reject a receipt re-signed by a
    swapped key. Returns (ok, reason)."""
    sig, pk = receipt.get("signature"), receipt.get("pubkey")
    if not sig:
        return False, "unsigned receipt"
    if expected_pubkey is not None and pk != expected_pubkey:
        return False, "pubkey does not match expected_pubkey (a swapped-key re-sign)"
    try:
        ok = bool(verify(_receipt_message(receipt), sig, pk))
    except Exception as e:
        return False, "verify error: " + str(e)[:120]
    return (ok, "ok") if ok else (False, "signature does not verify (receipt was altered or wrong key)")


def ed25519_signer(sk_hex: str):
    """A `sign(message_bytes)->hex` closure over an Ed25519 secret key, for compliance_receipt(sign=...).
    Lazy cryptography import — the auditor framework itself stays dependency-free until you sign."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(sk_hex))
    return lambda msg: sk.sign(msg).hex()


def ed25519_verify(message_bytes: bytes, sig_hex: str, pubkey_hex: str) -> bool:
    """Verify an Ed25519 signature (for verify_compliance_receipt(verify=ed25519_verify))."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(bytes.fromhex(sig_hex), message_bytes)
        return True
    except Exception:
        return False


def _dig(obj, *names):
    """Read the first present field from a client response that may be a dict OR an object (client APIs vary)."""
    for n in names:
        if isinstance(obj, dict):
            if obj.get(n) is not None:
                return obj[n]
        else:
            v = getattr(obj, n, None)
            if v is not None:
                return v
    return None


def _cosine(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-9)
