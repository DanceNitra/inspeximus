"""deletion_manifest.py — a CROSS-STORE deletion manifest for right-to-erasure (GDPR Art.17) across the fan-out.

The gate (2026-07-13) killed the "tamper-evident single-store erasure receipt" framing: a DPO's real problem is
not a forged deletion record, it is that a subject's data has fanned out across the memory store, the app's
vector index, retrieval logs, caches, and backups — and a receipt for one store certifies one of many copies
(erasure_fanout_probe.py measured the app vector-index copy surviving 1.00 of the time after a store delete).

This manifest is the honest deliverable: register every place a subject's data can live as an ErasureTarget;
`execute()` erases each, then RE-CHECKS whether the subject's sensitive values are still recoverable there, and
records the per-target outcome in a hash-chained, optionally-signed manifest. It is honest BY CONSTRUCTION: it
reports residual recoverability rather than hiding it, and marks the overall erasure COMPLETE only if EVERY
registered target verified the data absent. It never claims to cover targets you did not register, nor
backups, nor embedding-inversion of retained vectors (Morris et al. EMNLP 2023) — those are stated as
out-of-scope, not silently passed.

This is a coordination/evidence primitive, NOT a proof of physical destruction: it attests, tamper-evidently,
which stores were acted on and whether the data remained recoverable at check time. Prior art: DSAR/erasure
orchestration (BigID/Transcend/OneTrust discovery), crypto-shredding, EDPB "verifiable and irreversible"
erasure, RFC 6962-style hash-chained evidence.

Zero external deps (stdlib only); Ed25519 signing optional if `cryptography` is present.
"""
from __future__ import annotations
import json
import time
import hashlib

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _SK
    _HAVE_ED = True
except Exception:
    _HAVE_ED = False

_GENESIS = "0" * 64


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(b) -> str:
    return hashlib.sha256(b if isinstance(b, bytes) else _canon(b)).hexdigest()


class ErasureTarget:
    """Adapter protocol for one place a subject's data can live. Implement erase() + still_recoverable().
    `name` should identify the store to an auditor (e.g. 'mnemo-store', 'qdrant-index', 'retrieval-log')."""
    name = "unnamed-target"

    def erase(self, subject: str) -> dict:
        """Delete everything attributable to `subject`. Return {'erased': int} (best-effort count)."""
        raise NotImplementedError

    def still_recoverable(self, subject: str, values) -> bool:
        """After erase(), is ANY of the subject's sensitive `values` still recoverable from THIS store?
        Return True if recoverable (erasure incomplete here). This is the honest self-check the manifest records."""
        raise NotImplementedError


class DeletionManifest:
    def __init__(self, sign_sk_hex: str | None = None, pubkey_hex: str | None = None):
        self._targets: list[ErasureTarget] = []
        self._sk = sign_sk_hex
        self._pubkey = pubkey_hex
        self._entries: list[dict] = []

    def register(self, target: ErasureTarget) -> "DeletionManifest":
        self._targets.append(target)
        return self

    def execute(self, subject: str, values, request_id: str | None = None,
                basis: str | None = None, authorized_by: str | None = None) -> dict:
        """Erase `subject` from every registered target, verify residual recoverability of `values` per target,
        and build a tamper-evident manifest. `values` = the subject's sensitive strings to check for residue.
        Returns the manifest dict; `complete` is True only if EVERY target verified the data absent."""
        prev = _GENESIS
        entries = []
        for t in self._targets:
            try:
                res = t.erase(subject) or {}
                erased = int(res.get("erased", 0))
                recoverable = bool(t.still_recoverable(subject, values))
                err = None
            except Exception as e:                              # a target that errors is recorded, not hidden
                erased, recoverable, err = 0, True, str(e)[:160]
            e = {"target": t.name, "erased": erased, "still_recoverable": recoverable,
                 "verified_absent": (not recoverable) and err is None, "error": err,
                 "ts": time.time(), "prev": prev}
            e["hash"] = _sha256({k: e[k] for k in ("target", "erased", "still_recoverable",
                                                   "verified_absent", "error", "ts", "prev")})
            if self._sk and _HAVE_ED:
                sk = _SK.from_private_bytes(bytes.fromhex(self._sk))
                e["pubkey"] = self._pubkey
                e["sig"] = sk.sign(bytes.fromhex(e["hash"])).hex()
            entries.append(e)
            prev = e["hash"]
        self._entries = entries
        complete = bool(entries) and all(x["verified_absent"] for x in entries)
        leaks = [x["target"] for x in entries if not x["verified_absent"]]
        return {
            "subject": subject, "request_id": request_id, "basis": basis, "authorized_by": authorized_by,
            "targets": [t.name for t in self._targets],
            "entries": entries,
            "complete": complete,
            "residual_targets": leaks,
            "chain_tip": prev,
            "scope": ("Erasure spans ONLY the registered targets above. It does NOT cover unregistered stores, "
                      "backups, or reconstruction of the subject's text from RETAINED embeddings (embedding "
                      "inversion — Morris et al., EMNLP 2023). 'complete' means every registered target verified "
                      "the data no longer recoverable at check time; it is an evidence artifact, not a proof of "
                      "physical destruction."),
        }

    def verify(self, manifest: dict) -> tuple[bool, list[str]]:
        """Re-verify the manifest's hash chain (and signatures if present). Returns (ok, problems)."""
        problems: list[str] = []
        prev = _GENESIS
        for i, e in enumerate(manifest.get("entries", [])):
            if e.get("prev") != prev:
                problems.append(f"entry {i} ({e.get('target')}): broken chain link")
            core = {k: e.get(k) for k in ("target", "erased", "still_recoverable", "verified_absent",
                                          "error", "ts", "prev")}
            if _sha256(core) != e.get("hash"):
                problems.append(f"entry {i} ({e.get('target')}): hash mismatch (tampered)")
            if "sig" in e and _HAVE_ED:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey as _PK
                try:
                    _PK.from_public_bytes(bytes.fromhex(e["pubkey"])).verify(
                        bytes.fromhex(e["sig"]), bytes.fromhex(e["hash"]))
                except Exception:
                    problems.append(f"entry {i} ({e.get('target')}): invalid signature")
            prev = e.get("hash")
        return (len(problems) == 0, problems)
