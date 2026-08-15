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


def _header_hash(subject, request_id, basis, authorized_by, targets) -> str:
    """The chain's seed, since 1.83.0: WHO this erasure was for, WHO authorised it, under WHICH request and
    across WHICH targets.

    Those five fields sat outside every hash. `verify()` re-derived `complete` and `residual_targets` from
    the entries -- the 1.6x fix -- but never looked at the header at all, so a manifest could be repointed
    at a different data subject, name the attacker as its authoriser, carry someone else's request id and
    an empty target list, and still verify (True, []). On the one artifact whose entire job is to be
    evidence.

    Seeding the chain with them, rather than adding a separate signature, means a single edit to any of
    them breaks entry 0's `prev` link -- so the check cannot be forgotten by a future verifier that only
    walks the chain.
    """
    return _sha256({"subject": subject, "request_id": request_id, "basis": basis,
                    "authorized_by": authorized_by, "targets": sorted(targets or [])})


def _sha256(b) -> str:
    return hashlib.sha256(b if isinstance(b, bytes) else _canon(b)).hexdigest()


class ErasureTarget:
    """Adapter protocol for one place a subject's data can live. Implement erase() + still_recoverable().
    `name` should identify the store to an auditor (e.g. 'inspeximus-store', 'qdrant-index', 'retrieval-log')."""
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
        prev = _header_hash(subject, request_id, basis, authorized_by, [t.name for t in self._targets])
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

    def verify(self, manifest: dict, expected_pubkey: str | None = None,
               legacy_header: bool = False) -> tuple[bool, list[str]]:
        """Re-verify the manifest's hash chain, its HEADER, and that its verdict follows from the entries.

        The chain only ever covered the ENTRIES. `complete` and `residual_targets` sat outside it, so
        flipping `complete` to True and emptying `residual_targets` produced a manifest that verified
        `(True, [])` — a signed lie, on the one artifact whose entire job is to be evidence. An empty
        manifest verified clean too. Both were fixed by recomputing the verdict from the entries.

        The HEADER was still outside everything. `subject`, `request_id`, `basis`, `authorized_by` and
        `targets` were read by nothing, so the same manifest could be repointed at a different data
        subject, name the attacker as its authoriser and carry an empty target list, and still verify
        (True, []) — measured. Since 1.83.0 the chain is SEEDED with a hash of those five fields, so
        editing any of them breaks entry 0's link.

        `expected_pubkey` pins WHOSE signature counts. Without it a signature is verified against the key
        carried inside the entry being checked, which an attacker who can rewrite the manifest can simply
        replace with their own — the same reason `verify_writes` grew `warn_unpinned`.

        `legacy_header=True` accepts a pre-1.83 manifest whose chain starts at genesis. It is off by
        default and reported when it applies, because a manifest whose header was never bound is exactly
        the artifact this defect was about. Returns (ok, problems)."""
        problems: list[str] = []
        # NOTES are returned to the caller but do not flip `ok`. The distinction earned itself immediately:
        # the first version appended the legacy-manifest note to `problems`, so `legacy_header=True` -- the
        # escape hatch -- changed nothing at all. A flag that does not do what it says is worse than no
        # flag, and the unpinned-signature warning would likewise have failed every signed manifest that
        # exists today. Both are scope statements, not defects.
        notes: list[str] = []
        entries = manifest.get("entries") or []
        if not entries:
            problems.append("manifest has NO entries: nothing was audited, which is not the same as verified")
        recomputed_complete = bool(entries) and all(x.get("verified_absent") for x in entries)
        if manifest.get("complete") != recomputed_complete:
            problems.append(f"`complete` is {manifest.get('complete')!r} but the entries say "
                            f"{recomputed_complete!r} — the verdict does not follow from the evidence")
        recomputed_leaks = sorted(x.get("target") for x in entries if not x.get("verified_absent"))
        if sorted(manifest.get("residual_targets") or []) != recomputed_leaks:
            problems.append(f"`residual_targets` is {manifest.get('residual_targets')!r} but the entries say "
                            f"{recomputed_leaks!r}")
        # THE ENTRIES MUST COVER THE TARGETS THE HEADER NAMES. Recomputing the verdict from the
        # entries closed the "signed lie" hole and opened a quieter one: a PREFIX OF A HASH CHAIN IS
        # A VALID HASH CHAIN. Delete the trailing entry -- the one recording the target where the
        # subject's data is still present -- and the recomputation now runs over evidence that says
        # everything was clean, so `complete` reads True and `residual_targets` reads [] and verify()
        # returns (True, []). Measured 2026-08-15 on a SIGNED, key-pinned manifest.
        #
        # `targets` is inside `_header_hash`, so it cannot be trimmed to match: doing both breaks
        # entry 0's link ("broken chain link", measured). That is what makes this check sufficient
        # rather than merely another recomputation from truncatable evidence.
        #
        # Recomputing from evidence you have not counted is the same defect the docstring above
        # records as fixed, one level down.
        _declared = list(manifest.get("targets") or [])
        _seen: dict = {}
        for x in entries:
            _seen[x.get("target")] = _seen.get(x.get("target"), 0) + 1
        _missing = [t for t in _declared if t not in _seen]
        _dupes = sorted(t for t, n in _seen.items() if n > 1)
        _extra = sorted(t for t in _seen if _declared and t not in _declared)
        if _missing:
            problems.append(
                f"{len(_missing)} declared target(s) have NO entry: {_missing} -- the manifest was "
                f"TRUNCATED. A prefix of a hash chain is a valid hash chain, so the verdict was "
                f"recomputed over evidence that is missing exactly the targets it would have failed on.")
        if _dupes:
            problems.append(f"target(s) audited more than once: {_dupes} -- one entry per target, or "
                            f"the verdict depends on which copy is read")
        if _extra:
            problems.append(f"entries audit target(s) the header does not declare: {_extra}")
        # The header must be what the chain was seeded with. A pre-1.83 manifest seeded at genesis instead;
        # that is accepted only when asked for, and never silently, because "this manifest predates the
        # binding" and "this manifest was repointed at someone else" look identical from the outside.
        header = _header_hash(manifest.get("subject"), manifest.get("request_id"), manifest.get("basis"),
                              manifest.get("authorized_by"), manifest.get("targets"))
        first_prev = (manifest.get("entries") or [{}])[0].get("prev")
        if first_prev == _GENESIS and first_prev != header:
            if legacy_header:
                notes.append("pre-1.83 manifest: subject, request_id, basis, authorized_by and targets are "
                             "not bound to the chain and CANNOT be verified here — accepted on request")
                header = _GENESIS
            else:
                problems.append("this manifest's chain starts at genesis, so its subject, authoriser, "
                                "request id and target list are bound to NOTHING and could have been "
                                "rewritten. Pass legacy_header=True to accept a pre-1.83 manifest.")
                header = _GENESIS          # so the walk below reports only THIS, not every link as broken
        prev = header
        if expected_pubkey is None and any("sig" in e for e in (manifest.get("entries") or [])):
            notes.append("signatures present but expected_pubkey not pinned: each entry is verified against "
                         "the key stored INSIDE it, so a rewriter can sign with their own and pass — pin "
                         "the key you expect")
        for i, e in enumerate(manifest.get("entries", [])):
            if e.get("prev") != prev:
                problems.append(f"entry {i} ({e.get('target')}): broken chain link")
            core = {k: e.get(k) for k in ("target", "erased", "still_recoverable", "verified_absent",
                                          "error", "ts", "prev")}
            if _sha256(core) != e.get("hash"):
                problems.append(f"entry {i} ({e.get('target')}): hash mismatch (tampered)")
            if "sig" in e and _HAVE_ED:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey as _PK
                if expected_pubkey is not None and e.get("pubkey") != expected_pubkey:
                    problems.append(f"entry {i} ({e.get('target')}): signed by {str(e.get('pubkey'))[:16]}..., "
                                    f"not the pinned key")
                try:
                    _PK.from_public_bytes(bytes.fromhex(expected_pubkey or e["pubkey"])).verify(
                        bytes.fromhex(e["sig"]), bytes.fromhex(e["hash"]))
                except Exception:
                    problems.append(f"entry {i} ({e.get('target')}): invalid signature")
            prev = e.get("hash")
        return (len(problems) == 0, problems + notes)
