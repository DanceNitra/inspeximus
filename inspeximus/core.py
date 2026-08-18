"""
inspeximus — a memory layer for AI agents.  (brand: Inspeximus)

The memory that runs an autonomous research OS over ~5,800 notes, distilled to a single file with
no required dependencies. It does the four things agent memory actually needs, the way that held up
in production:

  remember(text)      append-only raw capture, stamped with an ABSOLUTE time (never rewritten)
  recall(query, k)    value-ranked retrieval: relevance × the memory's accrued value, not just
                      cosine similarity — the high-value memories surface first
  consolidate(cap)    the "dream" pass: value-rank under a keep-budget, link near-duplicates, mark
                      stale/superseded — it only ADDS a derived layer, it never edits the raw note
  contradictions()    flag mutually-incompatible memories for REVIEW (never auto-delete)

Design rules that are not optional (each one cost us to learn):
  • Raw capture is immutable. Consolidation adds links/markers; it never overwrites the source —
    that is what stops the slow accuracy drift of LLM-rewritten memory.
  • Absolute timestamps at write time. Relative/derived times rot the moment they're consolidated.
  • Value-ranked, capacity-aware consolidation. The payoff from ranking *what to keep* scales
    super-linearly as the budget shrinks (measured), so retention tracks value, not recency — and
    NOT access-frequency: decaying on reads keeps *popular* memories, but popularity != value, so a
    pure access-reset policy starves the rarely-read-but-load-bearing fact (measured: it retains
    ~3x less total value than a value blend under a tight budget). Forgetting blends value + recency.
  • Report value at the COHORT level (tag / time-block), never per-memory: per-item value at n-of-1
    is statistical noise; cohorts are where the signal lives.
  • Contradictions are flagged for review, not auto-resolved. Silent rewrites destroy trust.

Bring your own embedder for semantic recall (any text->vector fn); with none, inspeximus falls back to a
lexical token overlap so it runs anywhere, today.

    from inspeximus import Inspeximus
    m = Inspeximus("memory.json")                 # or Inspeximus("memory.json", embed=my_embedder)
    m.remember("Pre-trend tests catch only ~31% of fatal DiD bias.", tags=["causal"], value=3)
    m.recall("difference in differences", k=5)
    m.consolidate(keep=200)
    m.contradictions()

MIT-licensed. Part of Agora (https://github.com/DanceNitra/agora).
"""
from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import logging
import sys
import math
import os
import random as _random
import re
import threading
import tempfile
import time
import uuid
from pathlib import Path

try:                                  # OPTIONAL: numpy only ACCELERATES semantic recall at scale.
    import numpy as _np               # inspeximus still runs (pure-Python cosine) with no numpy installed.
except Exception:
    _np = None

try:                                  # OPTIONAL: only needed to SIGN write receipts (see receipts=...).
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey as _Ed25519SK, Ed25519PublicKey as _Ed25519PK)
    from cryptography.hazmat.primitives import serialization as _ser
    _HAVE_ED = True
except Exception:
    _HAVE_ED = False

try:                                  # OPTIONAL: only needed for encryption-at-rest (see encrypt_key=...).
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt as _Scrypt
    _HAVE_AEAD = True
except Exception:
    _HAVE_AEAD = False

# ENCRYPTION-AT-REST + CRYPTO-SHREDDING (OPT-IN). Standard, vetted primitives only — we do NOT roll our own
# crypto. The store file is AES-256-GCM (AEAD: confidentiality + tamper-detection) with a fresh random 96-bit
# nonce PER SAVE (we re-encrypt the whole blob each save, so no nonce is ever reused with the same key). File
# layout: MAGIC(5) + salt(16) + nonce(12) + ciphertext(+16B GCM tag); the MAGIC|salt|nonce header is fed as
# AEAD associated data so a tampered header fails decryption. A raw 32-byte key is used directly; a passphrase
# is stretched with scrypt (memory-hard). HONEST SCOPE (do not overclaim): this protects the store AT REST —
# someone who reads the file, a stolen disk, or a backup. It does NOT protect a COMPROMISED RUNNING PROCESS
# (the key + plaintext live in RAM), the key holder, or against malware/keyloggers; it is not end-to-end and
# not runtime memory protection. CRYPTO-SHREDDING (shred()): destroying the key makes the ciphertext — and
# every at-rest copy/backup of it — permanently unrecoverable (NIST SP 800-88 recognises key-destruction as a
# valid "Purge"). Honest caveats: it cannot reach plaintext already copied to RAM/OS-swap, or any store that
# was persisted UNENCRYPTED before a key was set. It SUPPORTS a GDPR Art.17 erasure workflow; it does not by
# itself "guarantee compliance". Prior art credited: SQLCipher (embedded-DB at-rest AES), NIST SP 800-88
# (cryptographic erasure), the `age`/Fernet file-encryption model (whose format we deliberately diverge from).
# Renamed with the product. This marks the on-disk ENCRYPTED format, so changing it makes a store
# written under the old marker unreadable — acceptable only because the rename landed while there was
# no measurable installed base, and no store on this machine carries the old magic (checked).
_INSPEXIMUS_ENC_MAGIC = b"INSP\x01"        # versioned so the on-disk format can migrate


def new_encryption_key() -> bytes:
    """A fresh random 32-byte (AES-256) key for Inspeximus(encrypt_key=...). Store it yourself (a secrets manager /
    OS keystore); inspeximus never persists the key. Losing it = the store is unrecoverable (that IS crypto-shred)."""
    return os.urandom(32)


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if not _HAVE_AEAD:
        raise RuntimeError("encryption needs the `cryptography` package (pip install cryptography)")
    return _Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _encrypt_blob(key: bytes, plaintext: bytes, salt: bytes) -> bytes:
    """AES-256-GCM encrypt `plaintext`; salt is carried only so a passphrase can be re-derived on load."""
    if not _HAVE_AEAD:
        raise RuntimeError("encryption needs the `cryptography` package (pip install cryptography)")
    nonce = os.urandom(12)
    header = _INSPEXIMUS_ENC_MAGIC + salt + nonce
    ct = _AESGCM(key).encrypt(nonce, plaintext, header)   # header authenticated as AAD
    return header + ct


def _parse_enc_header(blob: bytes):
    """-> (salt, nonce, header, ciphertext) or raise ValueError if not a inspeximus-encrypted blob."""
    if blob[:5] != _INSPEXIMUS_ENC_MAGIC:
        raise ValueError("not a inspeximus-encrypted store")
    salt, nonce = blob[5:21], blob[21:33]
    return salt, nonce, blob[:33], blob[33:]


def _decrypt_blob(key: bytes, blob: bytes) -> bytes:
    if not _HAVE_AEAD:
        raise RuntimeError("encryption needs the `cryptography` package (pip install cryptography)")
    salt, nonce, header, ct = _parse_enc_header(blob)
    return _AESGCM(key).decrypt(nonce, ct, header)        # raises on wrong key / tampering

_GENESIS = "0" * 64

#: Keyspaces whose records a GUARD reads to decide whether to refuse. Housekeeping -- capacity eviction and
#: the consolidate() keep-budget -- must neither count nor remove them: they are bookkeeping the guard's
#: correctness rests on, not part of the recall working set those policies exist to bound. The same carve-out
#: the eviction docstring already makes for superseded history.
#:
#: MEASURED before this existed (research/probes/audit_code_guard_disarm.py): with 30 recorded deprecations,
#: `capacity=8` plus ten ORDINARY writes left 2, and check_code() then returned [] -- "clean" -- for a snippet
#: resurrecting a deleted symbol. No maintenance call was involved, and `capacity=N` is what docs/API.md
#: recommends under "Run bounded in production", so the guard disarmed itself exactly as a store filled up.
#: consolidate(keep=5) did the same. A guard that fails OPEN under the documented production config is the
#: defect class this codebase keeps finding: a clean verdict about input it structurally stopped examining.
#:
#: A new guard registers its prefix HERE. code_guard._PREFIX must appear in this tuple; the agreement is
#: asserted in tests rather than imported, so the two modules stay decoupled and cannot drift silently.
#: RESERVED CONTROL-PLANE KEYSPACE for agent-to-agent read grants (see Inspeximus.grant). A grant is an
#: ordinary hash-chained record so it inherits the write receipts, the anchor, provenance() and history()
#: rather than needing a second log -- but it is BOOKKEEPING, not a memory, so it is carved out of the
#: content read path (recall, _tenant_rows) and out of housekeeping (eviction, the consolidate keep-budget).
#: remember() REFUSES this prefix from an ordinary caller: if any writer could mint a key in this namespace,
#: an agent could grant itself access through the normal write path and the ACL would be decorative.
_ACL_PREFIX = "acl::grant::"
_ACL_LOG = logging.getLogger("inspeximus.acl")

# The ONLY statuses recall may ever return. Everything else -- provisional (never confirmed),
# discarded (a verifier said no), candidate (identity unresolved) -- is invisible until something
# deliberately adds it here. See _eligible().
_RECALLABLE = frozenset({"active", "superseded", "hub"})

#: Statuses under which a record is WITHHELD from every read path, under any flag. The complement of
#: this set is what the store will serve, and that distinction -- not the raw status string -- is what
#: `status_sha256` commits to. See _serving_class().
_WITHHELD = frozenset({"provisional", "discarded", "candidate"})


def _serving_class(rec: dict) -> str:
    """`withheld` or `served`: will this record ever reach a reader?

    THE COMMITMENT IS ON THE CLASS, NOT THE STATUS, and that is the whole design. `status` is written
    at fourteen call sites, most of them mechanical and high-volume: consolidation and supersession
    move dozens of records from `active` to `superseded` in one pass. Committing the raw string would
    demand an amendment receipt at every one of them -- chain churn proportional to housekeeping, and
    fourteen chances to miss one and raise a tamper alarm on an honest store.

    Every one of those mechanical transitions stays INSIDE `served`. Only three cross the boundary
    (confirm, promote_candidate, discard_candidate), and each is a deliberate, low-volume act by
    someone vouching for a record. So the class costs three amendments instead of fourteen and binds
    exactly the property the product sells: whether anything vouched for this record before a reader
    saw it.

    `confirmed_by` rides along in the same hash, so stamping a fabricated reviewer onto a record is
    caught by the same check as flipping its status.
    """
    return "withheld" if (rec.get("status") or "active") in _WITHHELD else "served"
# The ONLY committed fields an amendment may forgive. Two, and each earns its place by being a
# field a legitimate in-band operation genuinely rewrites:
#
#   mtype          -- slash()/restore() revoking or restoring graduation.
#   status_sha256  -- confirm(), promote_candidate() and discard_candidate(), the three acts that
#                     move a record across the withheld/served boundary. Added in 2.10.2 with the
#                     commitment itself: a field that legitimately changes cannot be bound for life
#                     the way text+key are, so binding it REQUIRES an amendment path, and the two
#                     ship together or the first honest confirm() raises a tamper alarm.
#
# text+key (immutable_sha256), attribution and the served value bind for the LIFE of the record and
# no call site in this library ever declares them -- that is the invariant this set protects, and
# widening it further is a deliberate change to what the chain guarantees, not a parameter.
# Enforced rather than described: verify_writes intersects with it, _emit_write_receipt raises on it.
_AMENDABLE = frozenset({"mtype", "status_sha256"})
#: The code-guard keyspace, named once so the reservation in remember() and the exemption in
#: _is_guard_record cannot drift apart -- they were two string literals and only one was enforced.
_CODE_GUARD_PREFIX = "code::symbol::"
_GUARD_KEYSPACES = (_CODE_GUARD_PREFIX, _ACL_PREFIX)


def _is_guard_record(rec) -> bool:
    """True for bookkeeping a guard reads; see _GUARD_KEYSPACES."""
    k = rec.get("key") or ""
    return any(k.startswith(p) for p in _GUARD_KEYSPACES)


def _is_acl_record(rec) -> bool:
    """True for an access-control (grant/revoke) record. These are acts, not memories."""
    return (rec.get("key") or "").startswith(_ACL_PREFIX)


#: meta keys the LIBRARY stamps and then READS to make a decision. `remember(meta=...)` copies the
#: caller's dict onto the record verbatim, and these are read back out of that same dict -- so before
#: 2.4.1 a writer could hand itself any of them.
#:
#: THIS IS THE SECOND TIME THIS HOLE WAS CLOSED. 2.4.0 stopped `mtype="semantic"` from reaching the
#: top trust tier by additionally requiring `meta.graduated_from_episodic`, which the library stamps
#: at the corroboration bar. Measured on the published 2.4.0: `remember(mtype="semantic",
#: meta={"graduated_from_episodic": True})` returns `warrant="earned"` -- the highest tier we report,
#: to a record with no credit, no links and no witnesses. The fix had moved the hole one level down
#: rather than closing it, which is why this is a keyspace and not another single condition.
#:
#: Raised externally by yun520-1 (openclaw/openclaw#7707): "any design where the tier is set by the
#: writer has a forgeable top tier, because the writer is motivated to mark itself highest." He was
#: right in the general form, and further than the specific case he named.
#:
#: NOT an ACL bypass, checked before saying otherwise: a grant is identified by the reserved `key`
#: prefix through `_is_acl_record`, which `remember()` already refuses to mint, so a caller-supplied
#: `meta["acl"]` never becomes a grant. It is reserved here because the library reads it, not because
#: it was exploitable.
_RESERVED_META = frozenset({
    "acl", "asserts_change", "echo_blocked", "entries", "graduated_from_episodic", "hub",
    "hub_coverage",
    "needs_rederivation", "objectless_blocked", "pre_slash", "promoted_from_candidate",
    "rederived_to", "reopened_contradiction", "reopened_meta", "reopened_reason",
    "reopened_surfaced_prior", "retracted_reason", "revert_nonce", "session_seq",
    "source_seen_at", "source_sha256",
    # Reserved for the same reason as the fingerprint itself: a writer that could set these could
    # declare its own memory observation-bound, which is exactly the trust-tier hole this keyspace
    # exists to close. `observed_sha256` arrives through source=, is copied here by remember(), and
    # is compared against the library's own read -- a claim that is checked, not a claim that is
    # believed.
    "observed_sha256", "observation_bound", "source_moved_before_capture",
    "source_at_capture_sha256",
    "slashed", "superseded_by_policy", "superseded_by_toggle", "truncated_from",

    # Read by check_sources() for environment_binding_coverage, so the library owns it: a caller
    # able to set it could inflate the very coverage number that reports whether it is set.
    "environment_binding",
})

#: READ BY THE LIBRARY, WRITTEN BY THE CALLER -- and therefore NOT reserved. The threat is a caller
#: forging something the library believes it stamped itself; a field the library only ever READS is
#: the caller's data by design, and reserving it deletes a feature. `scope` is the case that taught
#: this: it was reserved on the "the library reads it" rule, and 25 grant tests went red because
#: `recall(scope=...)` filters on a scope the CALLER sets. Declared here rather than special-cased in
#: the guard, so the next one is a visible decision instead of a silent exception.
_CALLER_META = frozenset({"scope"})

#: The same problem with a different remedy. These four ALSO have named parameters on `remember()`
#: (`user_id`, `agent_id`, `session_id`, `project`), and passing them through `meta` reached the same
#: stamped fields by a second, unmaintained route. Silently dropping them would break a caller who is
#: today getting exactly the behaviour they intended, so they are ROUTED to the named parameter rather
#: than discarded: one path into these fields instead of two, and the explicit parameter wins on
#: conflict.
#:
#: WHAT THIS DOES NOT DO, measured rather than assumed. An earlier version of this note said the route
#: "reaches the SAME validated path it skipped". That was false and a control caught it:
#: `remember(agent_id="*")` stores `*` unchecked, exactly as the meta route did, because
#: `_check_agent_id` guards the GRANT path and `remember()` never calls it. So this converges two
#: routes into one; it does not add validation, and `*` -- reserved in the grant keyspace -- is still
#: an accepted agent id here. Whether it should be is a separate question from the reserved keyspace,
#: and is not silently answered by this release.
_META_ALIASED_PARAM = {"uid": "user_id", "aid": "agent_id", "sid": "session_id", "project": "project"}


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _as_ids(ids):
    """Accept a single id as a bare string, not just a list.

    Every ids-taking method iterated its argument directly, and a str IS iterable -- over its
    CHARACTERS. So the most natural single-id call, `credit("2dfd5c3133", True)`, iterated ten
    one-character ids, matched none of them, and returned {'updated': []}. Silently doing nothing on a
    plausible input is the same defect class this module keeps finding elsewhere: the caller gets a
    success-shaped result for work that never happened. Measured before the fix on credit(), slash(),
    monitor(), spend_irreversible() and rederive() -- all five.
    """
    if ids is None:
        return []
    if isinstance(ids, str):
        return [ids]
    return list(ids)


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _dump_store(items) -> str:
    """Serialize the store: each record COMPACT, one per line, inside a normal JSON array.

    WHY NOT `json.dumps(items, indent=1)` (what this replaced): passing `indent` makes CPython abandon
    its C encoder and fall back to the pure-Python `_iterencode` path. Measured on a realistic record
    shape, that was the single largest cost in the whole library — a mixed 1,000-write workload spent
    29.8 s of its 29.9 s inside _save, and 25.6 s of that inside json.dumps, because `_save(force=True)`
    runs on every remember() and re-serializes the entire store each time.

    WHY NOT FULLY COMPACT EITHER: `json.dumps(items)` with no indent is ~4.0x faster but emits the whole
    store as ONE line. `indent=1` was chosen deliberately — the store is a user-facing artifact people
    inspect, diff in git, and hand to auditors — so collapsing it to one line trades away something the
    current format exists to provide.

    This layout keeps both. The C encoder runs per record, and the only added whitespace is a newline
    between records, so a store stays line-diffable and greppable while getting most of the speedup.
    Measured, n=20,000 records of ~48 characters, median of 5 (byte counts are exact, not medians):
    indent=1 = 237 ms / 7,704,892 B; this = 80 ms / 6,164,892 B (2.96x faster, 20.0% smaller); fully
    compact = 45 ms / 6,124,891 B. Compact is smaller than this by exactly 40,001 B -- two characters per
    record -- which is the whole price of staying line-diffable. (An earlier revision of this docstring
    put compact at 7.78 MB, LARGER than the one-record-per-line form; that is arithmetically impossible,
    since this layout is the compact encoding plus a separator per record, and the figure was wrong.)
    Round-trip verified identical for all three, and this is formatting only — every digest in the library (state_digest, receipts, the audit
    bundle) hashes PARSED per-record fields, never the file bytes, so no digest changes.

    `allow_nan=False` is preserved per record: a caller-supplied NaN/Infinity would otherwise be written
    as a bare literal that Python re-reads but every strict JSON parser (jq, JS, Rust/serde) rejects.
    """
    if not items:
        return "[]"
    inner = ",\n ".join(
        json.dumps(r, ensure_ascii=False, allow_nan=False, separators=(",", ":")) for r in items)
    return "[\n " + inner + "\n]"


def new_receipt_keypair():
    """Return (private_key_hex, public_key_hex) for signing inspeximus write receipts. Needs `cryptography`."""
    if not _HAVE_ED:
        raise RuntimeError("signing write receipts needs the `cryptography` package (pip install cryptography)")
    sk = _Ed25519SK.generate()
    return (sk.private_bytes(_ser.Encoding.Raw, _ser.PrivateFormat.Raw, _ser.NoEncryption()).hex(),
            sk.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw).hex())


def new_source_keypair():
    """Return (private_key_hex, public_key_hex) for an ATTESTING SOURCE. The private half is held by the
    source (off the memory store's write path); the public half is what a corroboration is counted by.
    This is the exogenous trust root: a source signs the claims it authored, so 'independence' is measured
    by distinct VERIFIED KEYS an attacker cannot forge, not by distinct source STRINGS it can spoof. Needs
    `cryptography`."""
    return new_receipt_keypair()


def _attest_message(text: str, source_doc, tenant=None) -> bytes:
    """Canonical message an attestation signs: the claim text bound to its canonical source, so a signature
    for 'X by source S' cannot be replayed as 'X by source T' or attached to a different claim.

    THE TENANT IS PART OF THE BINDING when the writing store is scoped. Without it a signature says only
    "this text, from this source" and stays valid after the record is moved into another tenant's rows --
    so the one thing tenant isolation most needs to be non-repudiable, WHICH tenant a record was written
    for, was the one thing the signature did not cover. Raised by an external reviewer (yun520-1,
    NousResearch/hermes-agent#34352, 2026-08-10) who signs the binding for exactly this reason.

    `tenant=None` is OMITTED rather than serialised as null, so unbound stores produce a byte-identical
    message to every version before this one and their existing signatures keep verifying. A record moved
    between the two regimes fails in BOTH directions: bound->unbound recomputes without `n` and mismatches,
    unbound->bound recomputes with it and mismatches. That is the property, and it is asserted rather than
    assumed -- see tests/test_writes_can_carry_their_own_key.py.
    """
    canon_src = Inspeximus._canon_source(source_doc) if source_doc else ""
    msg = {"t": text, "s": canon_src}
    if tenant is not None:
        msg["n"] = str(tenant)
    return _canon(msg)


def attest(text: str, source_sk_hex: str, source_doc=None) -> str:
    """Produce a source's Ed25519 signature (hex) over a claim, to pass as remember(..., attestation=(pubkey,
    sig)). The source signs 'I authored this text (as this canonical source)'. A mislabel then means forging
    the source's key, not editing the store. Honest limit: this attests AUTHORSHIP, not TRUTH — a source that
    owns its key can honestly sign a false claim (a wrong-at-write-time / MINJA attack survives a signature);
    what it buys is that a caught liar is a NON-REPUDIABLE identity you can revoke, and that Sybil variants of
    one origin collapse to one verified key. Needs `cryptography`."""
    if not _HAVE_ED:
        raise RuntimeError("attestation needs the `cryptography` package (pip install cryptography)")
    sk = _Ed25519SK.from_private_bytes(bytes.fromhex(source_sk_hex))
    return sk.sign(_attest_message(text, source_doc)).hex()


def new_ed25519_keypair() -> tuple[str, str]:
    """Convenience: mint a fresh Ed25519 keypair as (secret_hex, public_hex) for an attestation source or a
    witness, so callers need not touch `cryptography` directly. The public hex goes on an allowlist; the
    secret stays with the signer. Needs `cryptography`."""
    if not _HAVE_ED:
        raise RuntimeError("key generation needs the `cryptography` package (pip install cryptography)")
    sk = _Ed25519SK.generate()
    pk = sk.public_key()
    from cryptography.hazmat.primitives import serialization as _ser
    sk_raw = sk.private_bytes(_ser.Encoding.Raw, _ser.PrivateFormat.Raw, _ser.NoEncryption())
    pk_raw = pk.public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)
    return sk_raw.hex(), pk_raw.hex()


#: The fields an anchor()'s `sth_hash` COMMITS TO. Named once and read everywhere, because the split between
#: "what the signature covers" and "what the reader consumes" is exactly where this scheme leaked: a witness
#: signature covers only the `sth_hash` STRING, while every consumer reads these FIELDS --
#: verify_consistency() pins a store to `writes_tip`/`n_writes`, detect_split_view() compares them.
_STH_FIELDS = ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip")


def _guard_key_location(kdir, store_path) -> None:
    """Refuse a key directory that is inside the store's own directory.

    A key an attacker with the data directory can read buys nothing against the attacker it exists
    to stop, and producing one is worse than refusing because it LOOKS like protection.

    `startswith` on `abspath` was the whole check and it was wrong three ways, all measured:
      * CASE. `abspath` does not normalise case and Windows/macOS filesystems are case-insensitive,
        so the same directory in different case walked past it.
      * LINKS. `abspath` does not resolve them; a junction pointing at the store's directory (no
        admin rights needed on Windows) walked past it too.
      * SIBLINGS. A prefix test refuses `.../AgentDataBackups` because `.../AgentData` is a prefix of
        it -- a false refusal on the availability side, which is how a guard gets turned off.

    `realpath` + `normcase` + `commonpath` answers the question that was actually being asked:
    is one directory INSIDE the other, rather than does one string begin with the other.

    WHAT THIS DOES NOT CLOSE, stated because a guard that lists only its wins reads as complete.
    A HARDLINK is one inode with two names, and `realpath` resolves symlinks and junctions but not
    hardlinks -- there is nothing in the path to resolve. So `ln keyfile <store_dir>/k` (or the
    reverse) leaves the key readable from inside the data directory while this check, asked about
    the other name, correctly answers "outside". A directory hardlink is not creatable on Windows or
    Linux, so this is a FILE-level bypass: it needs the attacker to already have write access to the
    store directory AND a path to the key, which is close to the position the guard assumes they do
    not have. Detecting it means comparing st_ino/st_dev of every entry in the store directory
    against the key on every call -- a directory walk per key read -- and the honest trade is to say
    so rather than to pay it and still miss the case where the link is made afterwards.

    THE GUARD IS ABOUT ACCIDENTS, and that is the correct way to read it. It stops the documented
    one-liner that put `receipt.key` in the working directory; it does not stop an attacker who is
    already inside. Signing exists for the attacker who holds the DATA and not the key -- if they
    hold both, the chain says nothing, which is the limit written up in
    tests/test_an_unsigned_chain_is_a_stated_condition.py.
    """
    _k = os.path.normcase(os.path.realpath(str(kdir)))
    _s = os.path.normcase(os.path.realpath(os.path.dirname(str(store_path)) or "."))
    try:
        _inside = os.path.commonpath([_k, _s]) == _s
    except ValueError:                     # different drives: cannot be inside
        _inside = False
    if _inside:
        raise ValueError(
            f"refusing to keep the receipt key at {kdir}: it resolves inside the store's own "
            f"directory ({_s}), so an attacker who can edit the store can read the key and sign "
            f"whatever they like. Set INSPEXIMUS_KEY_HOME to somewhere else.")


def receipt_key_for(store_path, create: bool = True) -> str:
    """The receipt signing key for `store_path`, minted on first use, kept OUTSIDE the store's directory.

        m = Inspeximus(path="mem.json", receipts=True, receipt_key=receipt_key_for("mem.json"))

    WHY A HELPER AND NOT A PARAGRAPH OF ADVICE. An unsigned chain catches an editor who can rewrite
    the store but NOT the `.receipts` sidecar. An attacker who can write both -- a compromised agent
    process, a shared volume, a tampered backup, which is the realistic one -- appends a well-formed
    receipt for their planted record and every check reports clean. Measured 2026-08-16.

    The key is what stops them, and it does NOT have to be secret from the operator. It has to be
    absent from the files they edit. That distinction is the entire recommendation, and it is why
    this refuses to place the key inside the store's own directory: `docs/ERASURE.md` showed a
    one-liner writing `receipt.key` into the working directory, which for the common case IS the
    data directory, and a key sitting beside the store buys nothing against the attacker it exists
    to stop.

    LOCATION, in order: `INSPEXIMUS_RECEIPT_KEY` (a hex key, or a path to one), else
    `INSPEXIMUS_KEY_HOME`, else the per-user config dir (`%APPDATA%` / `$XDG_CONFIG_HOME` /
    `~/.config`) under `inspeximus/keys/`. Named by a hash of the store's absolute path, so moving a
    store loses its key -- deliberately: silently signing a moved store with a NEW key would produce
    a chain signed by two keys, which verify_writes now reports rather than accepts.

    HONEST SCOPE, because this is exactly where key management gets oversold. This raises the bar
    from "one directory" to "two locations". It is NOT a KMS: an attacker with the user's home
    directory has it, and the OPERATOR always has it -- so it does nothing against the party who runs
    the store. For that, witness the anchor externally (`build_bundle(witnesses=[...])`). Pass a real
    `receipt_signer=` when the key must never be in this process at all.

    `create=False` returns "" instead of minting, for a caller that wants to know rather than act.
    """
    import hashlib as _h
    env = os.environ.get("INSPEXIMUS_RECEIPT_KEY", "").strip()
    if env:
        if len(env) == 64 and all(c in "0123456789abcdefABCDEF" for c in env):
            return env.lower()                                   # the key itself
        if os.path.exists(env):
            # THE SAME GUARD AS THE DEFAULT ROUTE. It only covered the directory this function
            # chooses, so `INSPEXIMUS_RECEIPT_KEY=<a path inside the store dir>` walked straight past
            # it -- the exact docs/ERASURE.md footgun this helper's docstring names, still reachable
            # through the helper written to prevent it.
            _guard_key_location(os.path.dirname(os.path.abspath(env)), store_path)
            return open(env, encoding="utf-8").read().strip()
        raise ValueError(f"INSPEXIMUS_RECEIPT_KEY is set to {env!r}, which is neither a 64-hex key "
                         f"nor a path that exists. Refusing to guess and silently sign with a "
                         f"different key than you configured.")

    home = os.environ.get("INSPEXIMUS_KEY_HOME") or os.environ.get("APPDATA") \
        or os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    kdir = os.path.join(home, "inspeximus", "keys")
    tag = _h.sha256(os.path.abspath(str(store_path)).encode("utf-8", "replace")).hexdigest()[:16]
    kf = os.path.join(kdir, f"{tag}.key")

    _guard_key_location(kdir, store_path)

    if os.path.exists(kf):
        return open(kf, encoding="utf-8").read().strip()
    if not create:
        return ""
    sk, _pk = new_ed25519_keypair()
    os.makedirs(kdir, exist_ok=True)
    _fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # not world-readable, no clobber
    with os.fdopen(_fd, "w", encoding="utf-8") as f:
        f.write(sk)
    return sk


def _int_or(x, default: int) -> int:
    """int(x) or `default` — a malformed count must not crash a verifier that exists to report on it."""
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def sth_hash_of(anchor: dict) -> str:
    """Re-derive an anchor's `sth_hash` from the fields it commits to — the SAME formula anchor() uses.

    ONE implementation. There used to be two: anchor() computed it, audit_bundle.verify_bundle re-derived it
    as its check (4), and the primitive every other surface goes through -- verify_cosigned_anchor -- did not
    re-derive it at all. So the check existed, was known to be necessary, and simply never reached the
    function that needed it most."""
    return _sha256_hex(_canon({k: (anchor or {}).get(k) for k in _STH_FIELDS}))


def anchor_binds_its_fields(anchor) -> bool:
    """Does this anchor's `sth_hash` actually COMMIT to the fields this anchor carries?

    WHY THIS EXISTS (measured, 2026-08-01). A witness signs the `sth_hash` string. Nothing re-derived that
    hash from the head's own fields, so an operator could take a genuinely co-signed anchor, paste a
    DIFFERENT `writes_tip` into it, keep the original `sth_hash` and signatures -- and
    verify_cosigned_anchor returned ok=True, 3 of 3 witnesses. The auditor then ran
    verify_consistency(that anchor), which reads `writes_tip`, and it CERTIFIED THE REWRITTEN STORE as
    append-only while reporting the honest store as the fork. The guarantee did not merely fail open, it
    inverted: the co-signature authenticated a number no witness had ever seen.

    A signature over a hash nobody re-derives authenticates nothing a reader uses."""
    if not isinstance(anchor, dict):
        return False
    h = anchor.get("sth_hash")
    return isinstance(h, str) and bool(h) and sth_hash_of(anchor) == h


def witness_cosign(witness_sk_hex: str, anchor: dict, prior_anchor: dict | None = None) -> str:
    """WITNESS-side: co-sign an anchor()'s signed head commitment, so a client can require k-of-n INDEPENDENT
    witnesses before trusting the store's history. This is the external gossip layer that turns inspeximus's
    tamper-evidence — which catches a rewrite on ONE timeline (verify_consistency) — into SPLIT-VIEW detection:
    a compromised operator cannot show divergent histories to different clients without getting the witnesses
    to co-sign both. The witness signs the `sth_hash` (the commitment to the whole write + tombstone history).

    If `prior_anchor` (the last head THIS witness co-signed) is given, the witness REFUSES to sign — raising
    ValueError — on a fork/rollback it can detect with NO access to the log: a shrunk log (`n_*` rolled back),
    or the SAME log size carrying a DIFFERENT tip (a fork at a size already witnessed). Full append-only
    verification between two LARGER sizes still needs a consistency proof against a replica
    (Inspeximus.verify_consistency); this local guard is the zero-log subset. Returns the Ed25519 hex
    signature over the sth_hash. Needs `cryptography`."""
    if not _HAVE_ED:
        raise RuntimeError("witness co-signing needs the `cryptography` package (pip install cryptography)")
    h = anchor.get("sth_hash")
    if not h:
        raise ValueError("anchor has no sth_hash (produce the head with store.anchor())")
    if not anchor_binds_its_fields(anchor):
        # A witness that signs an incoherent head mints a signature over a commitment no reader can
        # re-derive -- precisely the material the field-substitution attack needs.
        raise ValueError("refusing to co-sign: sth_hash does not commit to this anchor's own fields "
                         "(n_writes/writes_tip/n_tombstones/tombstones_tip). The head is not one "
                         "store.anchor() produced, or a field was altered after it was built.")
    if prior_anchor is not None:
        for ntag, tiptag in (("n_writes", "writes_tip"), ("n_tombstones", "tombstones_tip")):
            n_new, n_old = int(anchor.get(ntag, 0)), int(prior_anchor.get(ntag, 0))
            if n_new < n_old:
                raise ValueError(f"refusing to co-sign: {ntag} rolled back {n_old} -> {n_new} (rollback)")
            if n_new == n_old and anchor.get(tiptag) != prior_anchor.get(tiptag):
                raise ValueError(f"refusing to co-sign: {ntag}={n_new} but {tiptag} differs from the prior head "
                                 f"this witness signed (split-view / fork)")
    return _Ed25519SK.from_private_bytes(bytes.fromhex(witness_sk_hex)).sign(bytes.fromhex(h)).hex()


# --- universal-executor detection (1.2.0) -------------------------------------------------------------------
# WHY: a per-tool reversibility label is unsound for VERB-POLYMORPHIC universal executors -- a shell / eval /
# arbitrary-SQL / generic-HTTP tool whose EFFECT is set by a free-form argument, so the same tool is both
# 'ls' (reversible) and 'rm -rf' (irreversible). MEASURED (inspeximus lab, ToolEmu 330 tools, 2 labelers): tool
# reversibility is ~93% decidable from the signature (Cohen's kappa 0.82) but the ~7% undecidable residual is
# exactly this class, and its realized harm-reach is ENVIRONMENT-conditional -- an isolated executor reaches
# ~0% of external/API irreversible harms but a networked, ambiently-credentialed one reaches ~0.66. So the
# thing that bounds a memory-poisoned agent's irreversible EXTERNAL harm through such a tool is executor
# CONTAINMENT, not a per-tool reversibility flag. This detector + the spend_irreversible(tool=, contained=)
# gate make that undecidability EXPLICIT: an uncontained universal executor is never silently treated as
# reversible. Honest bound: heuristic name/param match (not a proof), and `contained` is a caller ASSERTION
# inspeximus cannot verify -- it forces the declaration, it does not enforce the sandbox.
_EXECUTOR_NAME_HINTS = ("execute", "exec", "eval", "shell", "terminal", "bash", "runcommand", "run_command",
                        "runcode", "run_code", "runscript", "run_script", "runquery", "run_query", "runsql",
                        "run_sql", "command", "script", "invoke", "httprequest", "http_request", "sendrequest",
                        "send_request", "curl", "fetchurl", "fetch_url", "query")
_EXECUTOR_PARAM_HINTS = ("command", "cmd", "code", "script", "query", "sql", "expression", "expr", "payload",
                         "shell", "bash", "url", "endpoint", "request")

# non-content boilerplate the admission gate rejects (a refusal/empty is not a memory worth storing)
_NON_CONTENT = ("no sources were provided", "no sources provided", "i cannot", "i can't help",
                "as an ai language model", "as an ai", "i'm sorry", "i am sorry", "cannot assist",
                "no information available", "not enough information", "none provided")

# SELF-NARRATION markers: the ASSISTANT describing its own state / reasoning / meta-conversation, which an
# LLM memory-writer often stores as if it were a fact ABOUT THE USER, polluting the store with the model's
# own hedges and self-talk. Matched as whole phrases at word boundaries. A caller-side write-gate signal
# (flag, never auto-reject) — see check_self_narration. Honest limit: heuristic; a legitimately stored
# first-person QUOTE ("user said: I think...") can trip it, which is why it flags rather than blocks.
_SELF_NARRATION = (
    "as an ai", "as an assistant", "as your assistant", "as a language model", "i am an ai", "i'm an ai",
    "i am here to help", "i'm here to help", "i can help you", "how can i help", "let me help",
    "i think", "i believe", "i feel that", "i'm not sure", "i am not sure", "i'm unsure", "i guess",
    "i suppose", "i assume", "in my opinion", "it seems to me", "i would say", "i'd say", "i'm confident",
    "i remember that", "i recall that", "i noted that", "i have stored", "i've stored", "if i understand",
    "correct me if i", "to summarize", "as i mentioned", "as i said")

# PII DETECTION (zero-dependency regex heuristic). Ordered by specificity so a more-specific pattern
# (SSN, credit card) claims a span BEFORE a broader one (phone) can eat it. This is a lightweight DLP
# HEURISTIC for tagging + masking, NOT a compliance-grade detector: it has false negatives (obfuscated
# or non-Western formats, names, addresses) and false positives (an order id shaped like a card). Use it
# to reduce raw-PII exposure into LLM prompts and to drive data-minimization sweeps, not as a guarantee
# that a record is PII-free. Detection is deterministic and embedder-free. Order matters — see redact_pii.
_PII_PATTERNS = (
    ("ssn",         re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email",       re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ \-]?){13,16}\b")),
    ("ipv4",        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    ("phone",       re.compile(r"(?<![\w.])\+?\d[\d\s\-().]{7,}\d(?![\w.])")),
)


def detect_pii(text: str) -> dict:
    """Scan `text` and return {pii_type: [matched_substrings]} for every heuristic hit. Zero-dependency,
    deterministic. Ordered so specific patterns (SSN/credit-card) match before the broad phone pattern can
    absorb their digits. A HEURISTIC, not a guarantee — see _PII_PATTERNS. Returns {} when nothing matches."""
    found: dict = {}
    if not isinstance(text, str) or not text:
        return found
    spans: list = []                                  # claimed [start, end) so a broad pattern can't re-match
    for label, pat in _PII_PATTERNS:
        for m in pat.finditer(text):
            s, e = m.start(), m.end()
            if any(s < ce and cs < e for cs, ce in spans):   # overlaps an already-claimed (more specific) span
                continue
            spans.append((s, e))
            found.setdefault(label, []).append(m.group(0))
    return found


def redact_pii(text: str, types=None, mask: str = "[{}]") -> tuple:
    """Return (masked_text, {type: count}) with every detected PII span replaced by a typed placeholder
    (default '[EMAIL]', '[SSN]', ...). `types`: optional iterable to restrict which PII types are masked
    (default all). Non-destructive on the input string; operates right-to-left so offsets stay valid. Same
    heuristic bounds as detect_pii — masks what it detects, no more."""
    if not isinstance(text, str) or not text:
        return text, {}
    want = set(types) if types is not None else None
    hits: list = []                                   # (start, end, label)
    spans: list = []
    for label, pat in _PII_PATTERNS:
        if want is not None and label not in want:
            continue
        for m in pat.finditer(text):
            s, e = m.start(), m.end()
            if any(s < ce and cs < e for cs, ce in spans):
                continue
            spans.append((s, e))
            hits.append((s, e, label))
    counts: dict = {}
    for s, e, label in sorted(hits, key=lambda h: h[0], reverse=True):
        text = text[:s] + mask.format(label.upper()) + text[e:]
        counts[label] = counts.get(label, 0) + 1
    return text, counts


redact_pii_fn = redact_pii   # stable module alias: recall()'s `redact_pii` bool param shadows the function name


def is_universal_executor(tool, signature=None) -> bool:
    """True if `tool` is a verb-polymorphic UNIVERSAL EXECUTOR whose reversibility cannot be decided from its
    signature (shell/terminal, eval/exec, arbitrary SQL, generic HTTP, run-arbitrary-script/command).

    tool: a tool name (str) OR a dict with keys like {'name','summary','parameters'/'params'}.
    signature: optional list of parameter names (str) if `tool` is just a name.
    Heuristic: a matching executor-style name OR a free-form instruction parameter (command/code/query/url...).
    """
    name = tool.get("name", "") if isinstance(tool, dict) else str(tool or "")
    nl = re.sub(r"[^a-z0-9]", "", name.lower())
    params = list(signature or [])
    if isinstance(tool, dict):
        raw = tool.get("parameters") or tool.get("params") or []
        for p in raw:
            params.append(p.get("name", "") if isinstance(p, dict) else str(p))
    name_hit = any(h.replace("_", "") in nl for h in _EXECUTOR_NAME_HINTS)
    pl = {re.sub(r"[^a-z0-9]", "", str(p).lower()) for p in params}
    param_hit = any(h.replace("_", "") in pl for h in _EXECUTOR_PARAM_HINTS)
    # a lone 'query'/'url' param on an otherwise read-only-sounding tool is weak; require name-hint OR a
    # strong free-form param (command/code/script/sql/expression/payload/shell/bash).
    strong_param = bool(pl & {"command", "cmd", "code", "script", "sql", "expression", "expr", "payload",
                              "shell", "bash"})
    return bool(name_hit or strong_param or (param_hit and name_hit))


def sign_revert(principal_sk_hex: str, challenge: str) -> str:
    """Principal-side, OFF the memory store's box: Ed25519-sign a revert `challenge`
    (Inspeximus.revert_challenge(key) = "revert:{key}:{current_active_id}") with the private key whose public half
    the store was given as `revert_pubkey`. The resulting hex signature is the capability passed to
    revert()/route(); the store verifies it but cannot produce it. This is the affordance a text-only attacker
    (and a store-only harness) cannot synthesize. Needs `cryptography`."""
    if not _HAVE_ED:
        raise RuntimeError("signing a revert needs the `cryptography` package (pip install cryptography)")
    sk = _Ed25519SK.from_private_bytes(bytes.fromhex(principal_sk_hex))
    return sk.sign(challenge.encode()).hex()


def sign_support(source_sk_hex: str, challenge: str) -> str:
    """Source-side, OFF the memory store's box: Ed25519-sign a support challenge string obtained from
    Inspeximus.support_challenge_for(key, toward). The hex signature is passed to observe(..., support=[(source_
    pubkey_hex, sig_hex), ...]). The store verifies it against the allowlist but can never mint it, so a
    content-path attacker cannot fabricate a corroborating ground — self-minted identities count zero. The
    challenge binds the CURRENT record id and tenant, so a captured signature cannot be replayed after the
    value legitimately changes (and changes back) or across tenants. Needs `cryptography`."""
    if not _HAVE_ED:
        raise RuntimeError("signing a support ground needs the `cryptography` package (pip install cryptography)")
    sk = _Ed25519SK.from_private_bytes(bytes.fromhex(source_sk_hex))
    return sk.sign(challenge.encode()).hex()


def erasure_challenge(subject: str, request_id) -> str:
    """The canonical message an authorizing principal signs to bind an erasure to itself: a right-to-erasure
    request for `subject` under `request_id`. sign_erasure() signs this; the tombstone carries the signature so
    an auditor can prove WHO authorized the deletion (the AUTHORITY axis), not just that a free-text id was
    written."""
    return "erase:" + _sha256_hex(_canon({"subject": subject, "request_id": request_id}))


def sign_erasure(principal_sk_hex: str, subject: str, request_id) -> str:
    """Principal-side (off the store's box): Ed25519-sign erasure_challenge(subject, request_id). The hex
    signature goes into forget_subject(..., authorization=), and authorized_by= is the principal's PUBLIC key —
    together they bind the erasure to an authenticated principal the store did not mint. Needs `cryptography`."""
    if not _HAVE_ED:
        raise RuntimeError("signing an erasure needs the `cryptography` package (pip install cryptography)")
    sk = _Ed25519SK.from_private_bytes(bytes.fromhex(principal_sk_hex))
    return sk.sign(erasure_challenge(subject, request_id).encode()).hex()


#: The certificate's own statement of what it does NOT certify. It is a CONSTANT, and the verifier
#: compares against it, because the field was free text that nothing checked: rewriting it to
#: "Full GDPR compliance certification, all systems." left the certificate verifying `valid: true`.
#: The one sentence a regulator most needs — "NOT a compliance certification" — was the easiest thing
#: in the document to delete. Producer and verifier now read the same string.
_CERT_SCOPE = ("Erasure is within THIS inspeximus store only (not the app's vector store, prompt logs, or "
               "backups); covers the subject PLUS its derived_from lineage. Tamper-evident integrity "
               "primitive, NOT a compliance certification. The tombstone proves the ACT of deletion, "
               "never the content; its signature is load-bearing only against a non-holder of "
               "receipt_key — witness the anchor externally (see verify_consistency).")


def verify_erasure_certificate(cert: dict, store_path: str | None = None,
                               store_items: list | None = None,
                               expected_pubkey: str | None = None) -> dict:
    """Independently verify a inspeximus erasure certificate (from Inspeximus.erasure_certificate()). The AUDITOR's check:
    needs NO private key and does NOT trust the operator. Confirms, in order:
      1. tombstone hash-chain re-derives from genesis (append-only, untampered);
      2. every tombstone Ed25519 signature verifies against the certificate's pubkey (pinned to
         expected_pubkey if you pass one);
      3. the anchor commits to the tombstone-chain tip (a rewrite that re-signs internally still fails this if
         you pinned the anchor against an externally-witnessed one);
      4. GIVEN the store (store_path to the JSON/encrypted file, or store_items as a decrypted list), every
         erased memory id is genuinely ABSENT from it — the 'read the raw store' proof soft-delete systems fail;
      5. the certificate ATTESTS TO AT LEAST ONE ERASURE. Checks 1-4 are consistency checks and all of them
         pass vacuously on an empty scope, so a certificate for a request that erased nothing used to verify
         `valid: true` — see `checks["attests_an_erasure"]`.
    Returns {valid, checks, problems}. Pure-stdlib + Ed25519; import it standalone: `from inspeximus import
    verify_erasure_certificate`. HONEST: signatures are load-bearing only against a party who does not hold
    receipt_key; for operator-adversarial audit, pin the anchor against one you witnessed out of band."""
    problems: list = []
    checks: dict = {}
    toms = cert.get("tombstones") or []
    pub = expected_pubkey or cert.get("pubkey")

    tprev = _GENESIS
    chain_ok = True
    sigs_ok = True
    for j, t in enumerate(toms):
        core = {k: t.get(k) for k in ("seq", "memory_id", "ts", "request_id", "prev")}
        if t.get("auth"):                                       # optional committed AUTHORITY/BASIS block
            core["auth"] = t["auth"]
        if t.get("prev") != tprev:
            problems.append(f"tombstone {j}: broken chain link (a prior tombstone was altered/removed)")
            chain_ok = False
        if _sha256_hex(_canon(core)) != t.get("hash"):
            problems.append(f"tombstone {j}: hash mismatch (tampered)")
            chain_ok = False
        if "sig" in t:
            if not _HAVE_ED:
                problems.append("cannot verify signatures (cryptography not installed)")
                sigs_ok = False
            else:
                try:
                    _Ed25519PK.from_public_bytes(bytes.fromhex(t.get("pubkey") or pub or "")).verify(
                        bytes.fromhex(t["sig"]), bytes.fromhex(t["hash"]))
                    if pub and t.get("pubkey") and t.get("pubkey") != pub:
                        problems.append(f"tombstone {j}: signed by an unexpected key")
                        sigs_ok = False
                except Exception:
                    problems.append(f"tombstone {j}: invalid signature")
                    sigs_ok = False
        elif expected_pubkey:
            problems.append(f"tombstone {j}: unsigned, but a signature was required")
            sigs_ok = False
        tprev = t.get("hash")
    checks["chain_intact"] = chain_ok
    # A CHECK THAT DID NOT RUN IS NOT A CHECK THAT PASSED. `sigs_ok` starts True and is only ever set
    # False by a failing signature — so a certificate whose tombstones carry NO `sig` at all reported
    # `signatures_valid: true`, and swapping `pubkey` for zeros changed nothing, because there was
    # nothing to verify against it. A DPA reading that field learns "the signatures are valid" about a
    # document that has none. `store_absent` two checks below already models this correctly: None when
    # the proof was not performed, and `valid` refuses to count it as passed.
    signed = [t for t in toms if t.get("sig")]
    limits: list = []
    # `toms and not signed` left the EMPTY chain to the else-branch, where `sigs_ok` is still its initial
    # True — so a certificate with no tombstones at all reported `signatures_valid: true`. That is the same
    # sentence this guard was written to stop a DPA reading, one case further out: the guard fixed "has
    # tombstones, none signed" and not "has no tombstones", and the second is the case an empty certificate
    # produces. Measured on a store with zero erasures.
    if not signed:
        checks["signatures_valid"] = None
        checks["signed"] = False
        limits.append("UNSIGNED: no tombstone carries a signature, so nothing was verified against "
                      "`pubkey` — the chain proves integrity, not authorship. Set receipt_key to sign.")
    else:
        checks["signatures_valid"] = sigs_ok
        checks["signed"] = bool(signed)
        if signed and len(signed) != len(toms):
            limits.append(f"PARTIALLY SIGNED: {len(toms) - len(signed)} of {len(toms)} tombstones carry "
                          f"no signature; the authorship evidence covers only part of the chain")

    anc = cert.get("anchor") or {}
    tip = toms[-1]["hash"] if toms else _GENESIS
    checks["anchor_matches_tip"] = (anc.get("tombstones_tip") == tip)
    if not checks["anchor_matches_tip"]:
        problems.append("anchor tombstones_tip does not match the tombstone chain tip")

    # (5) THE SUMMARY MUST FOLLOW FROM THE TOMBSTONES. Until 1.63.0 `count`, `erased_memory_ids` and
    # `request_ids` were echoed straight from the certificate and never re-derived, so every one of them was
    # forgeable while `valid` stayed True — including replacing the erased ids with ids that never existed.
    # The tombstones ARE hash-chained and signed; the summary is a claim about them, and a claim that does
    # not follow from its evidence is exactly what a certificate must not carry. Same defect as the
    # DeletionManifest verdict fixed in 1.59.0, one artifact over.
    # SCOPE FIRST. erasure_certificate(request_id=X) summarises ONE request but ships the WHOLE tombstone
    # chain (deliberately, so the chain re-derives from genesis). Comparing a scoped claim against the
    # unscoped chain rejected every honest certificate from a store that had served more than one DSAR —
    # shipped in 1.63.0, and the test fixture could not see it because it only ever made one request.
    # Scope by the producer's OWN marker when it is present. `request_ids` alone cannot express "unscoped",
    # because it drops the None-request tombstones ordinary housekeeping erasures leave behind.
    if "scoped_to" in cert:
        _scope = cert.get("scoped_to")
        scope_toms = toms if _scope is None else [t for t in toms if t.get("request_id") == _scope]
    else:
        # Pre-1.67.0 certificate with no marker: accept a tombstone whose request is claimed OR
        # unattributed, which is the union the old producer could have meant either way.
        claimed = set(cert.get("request_ids") or [])
        scope_toms = [t for t in toms
                      if t.get("request_id") in claimed or t.get("request_id") is None] if claimed else toms
    claimed_reqs = cert.get("request_ids")
    derived_ids = sorted({t.get("memory_id") for t in scope_toms if t.get("memory_id")})
    # `is not None`, matching the producer at erasure_certificate(). Filtering on truthiness dropped an
    # honest empty-string request_id, so a certificate declaring request_ids: [""] failed its own chain.
    derived_reqs = sorted({t.get("request_id") for t in scope_toms if t.get("request_id") is not None})
    if sorted(set(cert.get("erased_memory_ids") or [])) != derived_ids:
        problems.append(f"erased_memory_ids does not match the tombstone chain "
                        f"(certificate claims {len(cert.get('erased_memory_ids') or [])}, "
                        f"the chain shows {len(derived_ids)})")
    if cert.get("count") is not None and cert.get("count") != len(derived_ids):
        problems.append(f"count says {cert.get('count')} but the tombstone chain holds {len(derived_ids)}")
    if claimed_reqs is not None and sorted(set(claimed_reqs)) != derived_reqs:
        problems.append(f"request_ids names {sorted(set(claimed_reqs))} but the chain holds "
                        f"{derived_reqs} for them")
    checks["summary_derivable"] = not any(
        x.startswith(("erased_memory_ids", "count", "request_ids")) for x in problems)

    # A CERTIFICATE THAT ATTESTS TO ZERO ERASURES IS NOT A VERIFIED ERASURE. Every other check here is a
    # consistency check — chain, signatures, anchor, summary, absence — and all five pass VACUOUSLY on an
    # empty scope: nothing to break a link, nothing to mis-sign, nothing to be present in the store. So
    # `valid: true` came back for two documents that certify nothing, both measured 2026-08-01:
    #   (a) a store with NO erasures at all -> count 0, and valid true;
    #   (b) `erasure_certificate(request_id="DSAR-2026-999")` on a busy store, for a request that was never
    #       performed -> count 0, `signed: true` (other requests' tombstones are signed), and valid true.
    # (b) is the dangerous one: an operator can hand a regulator an independently-verifiable certificate for
    # a deletion that never happened, and every field in it is honest. DeletionManifest.verify already
    # refuses this ("nothing was audited, which is not the same as verified") and ErasureAuditor.audit was
    # fixed for it in the same terms; this verifier was the sibling that kept the hole.
    checks["attests_an_erasure"] = bool(scope_toms)
    if not scope_toms:
        _why = ("the tombstone chain is empty" if not toms else
                "no tombstone in the chain falls within this certificate's scope "
                f"({'request_id=' + repr(cert.get('scoped_to')) if 'scoped_to' in cert else 'the claimed request_ids'})")
        # ASCII: this string is printed by `inspeximus erasure-verify` onto consoles that are not UTF-8
        # (cp1250 here), where a stray em dash is a mojibake at best and a UnicodeEncodeError at worst.
        problems.append(f"this certificate attests to ZERO erasures: {_why}. Nothing was erased under it, "
                        f"so every other check below passed vacuously - 'nothing was audited' is not the "
                        f"same as 'the data is gone'")

    erased = set(derived_ids)                # check ABSENCE against the chain, not against the claim
    checks["store_absent"] = None
    # Did the CALLER ask for the absence proof? Not asking is honest chain-only verification. Asking and
    # not getting it is not, and it used to return valid=True: a typo in `store_path` silently downgraded
    # the strongest check in this function -- the 'read the raw store' proof soft-delete systems fail --
    # to "not performed", while the verdict still said valid. Measured on a missing path and on an
    # encrypted store: both returned valid=True with store_absent=None.
    store_requested = store_items is not None or bool(store_path)
    if store_items is None and store_path:
        try:
            raw = Path(store_path).read_bytes()
            if raw[:5] == _INSPEXIMUS_ENC_MAGIC:
                problems.append("store is encrypted — supply decrypted store_items to check id-absence, "
                                "or rely on shred() (crypto-erasure) for the encrypted case")
            else:
                store_items = json.loads(raw.decode("utf-8"))
        except Exception as e:
            problems.append(f"cannot read store at {store_path}: {repr(e)[:80]}")
    if store_items is not None:
        present = {r.get("id") for r in store_items}
        leaked = sorted(erased & present)
        checks["store_absent"] = (len(leaked) == 0)
        if leaked:
            problems.append(f"{len(leaked)} erased id(s) STILL PRESENT in the store: {leaked[:5]}")

    # `valid` is computed from the CHECKS, and a check absent from this expression is decorative — the
    # summary-derivability finding was recorded in `problems` and ignored here in the first cut of this fix,
    # so a forged count still verified. `count` in the return is the DERIVED one, not the claim.
    if store_requested and checks["store_absent"] is None:
        problems.append("the absence proof was REQUESTED but could not run (store unreadable or "
                        "encrypted) — this certificate is NOT verified against a store")

    # THE SCOPE STATEMENT IS PART OF THE DOCUMENT. It was free text nobody compared, so the sentence a
    # regulator most needs — "NOT a compliance certification" — could be replaced with
    # "Full GDPR compliance certification, all systems." and the certificate still returned valid:true.
    # Nothing else in the artefact changes, which is precisely why it verified: every other field still
    # derived from the chain. A document whose limitations can be edited away is not a limited document.
    _scope_txt = cert.get("scope")
    if _scope_txt is not None and _scope_txt != _CERT_SCOPE:
        checks["scope_intact"] = False
        problems.append("the `scope` statement does not match the one this library issues — the "
                        "certificate's own declaration of what it does NOT certify has been altered")
    else:
        checks["scope_intact"] = _scope_txt is not None or None

    valid = (chain_ok and sigs_ok and checks["anchor_matches_tip"]
             and checks["summary_derivable"] and checks["scope_intact"] is not False
             and checks["attests_an_erasure"]
             and (checks["store_absent"] is True or not store_requested))
    # `limits` is separate from `problems` on purpose, the way verify_bundle already does it: a thing
    # that was NOT CHECKED is not a thing that FAILED, and collapsing the two either invalidates honest
    # unsigned certificates or hides that nothing was verified against `pubkey`.
    return {"valid": valid, "checks": checks, "problems": problems, "limits": limits,
            "count": len(erased)}


__version__ = "2.14.0"

# Internal sentinel: marks a reaffirm write already authorized by submit_revert() (which verified the
# signed INTENT). Object identity — no text/content path can ever produce it.
_SANCTIONED = object()
_WORD = re.compile(r"[a-z0-9][a-z0-9\-']{2,}")
_STOP = frozenset("the a an of for to in on and or is are was were be been with this that it its as "
                  "by at from into our we us you your he she they them his her their not no".split())


def _stem(w: str) -> str:
    return w[:-1] if (w.endswith("s") and len(w) > 4) else w   # crude plural/3rd-person fold


def _tokens(text: str) -> set:
    return {_stem(w) for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def _token_counts(text: str) -> dict:
    """Term-frequency map with the SAME tokenization as _tokens (stem + stopword filter). BM25 needs TF;
    _tokens loses it by returning a set."""
    d: dict = {}
    for w in _WORD.findall((text or "").lower()):
        if w in _STOP:
            continue
        s = _stem(w)
        d[s] = d.get(s, 0) + 1
    return d


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class AmbiguousSubject(ValueError):
    """An erasure subject whose canonical form is shared by a DIFFERENT raw source in the store.

    Raised instead of hard-deleting the other subject's records. `_canon_source` keeps only the host, so
    'crm.example.com/alice' and 'crm.example.com/bob' resolve alike; before this, a DSAR for Alice erased
    Bob and the preview reported no collateral. Subclasses ValueError so existing `except ValueError`
    handlers keep working."""


class StoreChangedOnDisk(RuntimeError):
    """Another writer changed the store file since this handle loaded or last saved it.

    The store is one JSON file written whole with an atomic replace, and it is read ONCE at open. So a second
    handle — a long-running MCP server plus a CLI invocation is the ordinary case, since both default to
    $INSPEXIMUS_PATH — used to win by simply writing last: the other writer's committed, `flush()`ed record
    was erased, and `verify_writes()` still returned True because the surviving chain was self-consistent.
    Detecting the conflict and refusing is the honest floor: no silent loss, and `reload()` offers a recovery
    path. inspeximus is a SINGLE-WRITER store; this makes that assumption enforced rather than assumed."""


def _resolve_echo_guard(explicit: bool | None = None) -> bool:
    """The ONE place the echo-guard posture is decided: explicit argument > env var > ON.

    It lives at module level rather than in `__init__` so `_surface.echo_guard_default()` can delegate to
    it instead of re-implementing it. A default that has to be re-declared at each entry point is a default
    that will be missed at one of them -- that is exactly how the nine adapters ran unguarded for ten
    releases, and how `INSPEXIMUS_ECHO_GUARD=0` came to work on the surfaces but not in the library.
    """
    if explicit is not None:
        return bool(explicit)
    return os.environ.get("INSPEXIMUS_ECHO_GUARD", "1") != "0"


# ── Current-State Applicability (CML memory-applicability v0.1) ──────────────────────────────────────
#
# Historical truth is not current authority. A record can be perfectly valid evidence of what happened
# and still be unsafe to drive an action here, now: the branch moved, the policy changed, the tenant
# differs, the window expired. check_sources() answers "is the SOURCE still what it was"; this answers
# "may this still act", and they are different questions with different remedies.
#
# The contract is safal207's (Causal-Memory-Layer#270, discussed on anthropics/claude-code#34556). We
# implement it rather than propose a variant, because a contract with one implementation is a proposal
# and the useful thing to be is the second one. The frozen fixture is consumed verbatim by the tests.

#: Fail-closed order. A source-integrity failure must never be masked by a weaker environment verdict:
#: a record whose origin cannot be verified is broken for everyone, while one that is inapplicable here
#: may be fine elsewhere. Different scopes of invalidation, not different severities.
APPLICABILITY_PRECEDENCE = ("REJECT", "UNRESOLVABLE", "ORPHAN", "DRIFT", "REVALIDATE", "MATCH")

#: Every dimension the contract compares.
APPLICABILITY_DIMENSIONS = ("repository", "branch", "commit_sha", "workspace", "actor", "tenant",
                            "policy_digest", "target_state_digest", "api_version", "model_version")

#: STRICT dimensions. If current authoritative state supplies one of these and the stored evidence
#: never bound it, that absence forces REVALIDATE instead of MATCH. The rule in one line, and it is the
#: sharpest thing in the contract: ABSENCE OF HISTORICAL CONTEXT IS NOT PERMISSION TO ASSUME CONTINUITY.
#: Without it, evidence detached from the pull-request head it was reviewed at silently regains
#: authority in a different code context, on the strength of a source digest that never moved.
APPLICABILITY_STRICT = ("repository", "commit_sha")

#: Keys a CALLER may never set: the library must not read its own verification state back out of a dict
#: the caller controls. Ours are stripped silently at write time (see _RESERVED_META); the contract also
#: names an explicit set, and both are refused here so a shared fixture agrees on the outcome.
#: Upstream states that invalidate a DERIVED record regardless of its own source integrity. This is the
#: dimension we raised on claude-code#34556 and safal207 froze into a contract (CML#272): the most common
#: reason a memory of ours stops being safe to act on is that a record it was derived FROM was retired or
#: erased -- neither source drift nor environment change, but the store's own history. It has a practical
#: advantage over every environment dimension: it needs no external fetch and no adapter, so it still
#: works when refetch_verification_coverage is zero, which on our own production store it effectively is.
#:
#: STATE IS CHECKED BEFORE DIGEST, and the fixture makes the difference visible: an `erased` dependency
#: also has no observed digest, so a digest-first reading would report it as merely unverifiable and lose
#: the fact that the parent was deliberately removed.
APPLICABILITY_DEAD_STATES = ("superseded", "retired", "erased", "revoked")


APPLICABILITY_RESERVED = frozenset({
    "warrant", "environment_verified", "provenance_verified", "source_verified", "applicability_verdict",
})


def _iso_to_epoch(s):
    """Minimal ISO-8601 parse for the contract's timestamps. Returns None on anything unparseable --
    never a guess, because a mis-parsed expiry silently grants authority it should have revoked."""
    if not s or not isinstance(s, str):
        return None
    try:
        return calendar.timegm(time.strptime(s.replace("Z", "UTC"), "%Y-%m-%dT%H:%M:%S%Z"))
    except Exception:
        try:
            return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            return None


def evaluate_applicability(source, stored_environment=None, current_environment=None,
                           caller_metadata=None, now=None, lineage=None) -> dict:
    """May this historical evidence drive an action in the CURRENT environment?

    Returns {"status": one of APPLICABILITY_PRECEDENCE, "reasons": [...]}. Pure: no store, no I/O, no
    clock of its own -- `now` is passed in, so the same inputs always produce the same verdict and a
    fixture can be replayed years later.

    `source` carries the observation, not a promise: {"locator", "refetchable", "exists",
    "expected_digest", "observed_digest"}. `refetchable` is EXPLICIT because a populated locator is not
    evidence of re-fetchability -- `agent:scholar` is a writer identity and stays UNRESOLVABLE. We
    measured that distinction on our own store at 98.3% locator coverage against 0.01% re-fetchable.
    """
    source = source or {}
    stored = stored_environment or {}
    current = current_environment or {}
    meta = caller_metadata or {}

    forged = sorted(k for k in meta
                    if k in APPLICABILITY_RESERVED or k in _RESERVED_META or k.startswith("_cml_"))
    if forged:
        return {"status": "REJECT", "reasons": ["forged_reserved_key:%s" % k for k in forged]}

    if not source.get("refetchable") or not source.get("locator"):
        return {"status": "UNRESOLVABLE", "reasons": ["source_not_refetchable"]}
    if source.get("exists") is False:
        return {"status": "ORPHAN", "reasons": ["source_missing"]}
    if source.get("expected_digest") != source.get("observed_digest"):
        return {"status": "DRIFT", "reasons": ["source_digest_changed"]}

    reasons = []

    # LINEAGE, after source integrity and before the environment. A derived record whose own source
    # still matches can be unsafe because an upstream one was retired; that is a different failure from
    # drift, with a different remedy (re-derive or refuse), and it maps to REVALIDATE rather than to a
    # seventh verdict -- the contract's choice, and the right one: the caller's action is the same.
    for dep in (lineage or []):
        did = dep.get("dependency_id")
        state = (dep.get("state") or "active").lower()
        if state in APPLICABILITY_DEAD_STATES:
            reasons.append("lineage_invalidated:%s:%s" % (did, state))
            continue
        obs = dep.get("observed_digest")
        if obs is None:
            reasons.append("lineage_unverifiable:%s" % did)
        elif obs != dep.get("expected_digest"):
            reasons.append("lineage_changed:%s" % did)

    for dim in APPLICABILITY_STRICT:
        if current.get(dim) is not None and stored.get(dim) is None:
            reasons.append("environment_unbound:%s" % dim)
    for dim in APPLICABILITY_DIMENSIONS:
        s_val, c_val = stored.get(dim), current.get(dim)
        if s_val is not None and c_val is not None and s_val != c_val:
            reasons.append("environment_mismatch:%s" % dim)

    until = _iso_to_epoch(stored.get("valid_until"))
    _now = _iso_to_epoch(now) if isinstance(now, str) else now
    if until is not None and _now is not None and _now > until:
        reasons.append("binding_expired")

    if reasons:
        return {"status": "REVALIDATE", "reasons": sorted(reasons)}
    return {"status": "MATCH", "reasons": []}


def _lock_primitive():
    """Resolve the platform lock module ONCE, at import.

    This was inside _StoreLock.__init__, so every save re-entered the import machinery. Profiling the
    identity-gate probe: 14,000 saves, 19.3 s in importlib -- more than the fsync it was there to
    accompany, and the entire reason two 90-second probes started overrunning a 180 s CI timeout. The
    platform does not change between saves; a per-instance import can only ever re-answer the same
    question. (I assumed the cost was the lock itself and optimised the handle first; that bought
    2.6 ms/save while this was sitting in the profile the whole time. Measure, then fix.)
    """
    try:
        import fcntl
        return ("fcntl", fcntl)
    except ImportError:
        pass
    try:
        import msvcrt
        return ("msvcrt", msvcrt)
    except ImportError:
        return (None, None)


_LOCK_PRIMITIVE = _lock_primitive()


class _StoreLock:
    """A real inter-process lock around the read-modify-write of one store file.

    `_file_sig` is a TOCTOU CHECK, not a lock: it is read before the write and cannot cover the write
    itself, so it detects a competing writer only when that writer already finished. It never made the
    write atomic against one running concurrently.

    Measured 2026-08-15, 6 processes x 40 honest `open -> remember -> flush` on one 2.94 MB store:
    156 JSON tears, 21 of 240 writes landing, and the store left permanently unparseable -- which the
    (correct) refusing loader turns into a total outage. This is not hypothetical: three of this
    project's own hook stores corrupted in ten days, the main one silently dead for two days, because
    Claude Code fires the PreToolUse/PostToolUse hooks concurrently for parallel tool calls.

    The OS releases the lock when the holder dies, so a killed process cannot wedge the store -- the
    property an O_EXCL lockfile does not have. Best-effort: if the platform primitive is unavailable
    the lock degrades to a no-op rather than failing the write, because a store that cannot save is a
    worse outcome than one that races.

    NOT re-entrant. Windows byte-range locks are per-handle, so a second _StoreLock on the SAME path
    inside the first would deadlock against itself. `_durable_replace` therefore does not lock; only
    its two callers do, and they lock different files.
    """

    #: One cached (threading.Lock, open file handle) per lock path, for the life of the process.
    #: Re-opening the lock file on EVERY save cost ~2 ms; measured 2026-08-15, the round trip took the
    #: save path from 3.7 to 8.2 ms and pushed two 90-second probes past a 180 s CI timeout, which is
    #: how the cost was noticed at all. The handle is cheap to hold and the OS still releases the lock
    #: if the process dies.
    #:
    #: The threading.Lock is NOT redundant. Byte-range locks are per-HANDLE on Windows, so two threads
    #: sharing one cached handle would not block each other -- caching the handle without it would
    #: have traded a cross-process race for an in-process one.
    _CACHE: dict = {}
    _CACHE_GUARD = threading.Lock()

    __slots__ = ("_path", "_fh", "_locker", "_tl")

    def __init__(self, path):
        # The lock lives in the SYSTEM TEMP DIR, keyed by a hash of the resolved store path -- NOT
        # beside the store. A first version put `<store>.lock` next to it and the erasure-residue
        # scanner immediately counted 6 files where the docs promise 3: inspeximus scans data
        # directories for DSAR residue, so dropping new files into one is not free, and a user should
        # not have to clean up after a lock. It carries no content (it is opened, never written), so
        # nothing about it is data.
        #
        # The tradeoff, stated: two users on one machine share the temp dir and therefore the lock,
        # which is what we want; two machines sharing a network mount do not -- but flock/LockFile
        # over SMB/NFS is unreliable anyway, so a co-located file would not have bought that either.
        h = hashlib.sha256(os.path.abspath(str(path)).encode("utf-8", "replace")).hexdigest()[:16]
        self._path = os.path.join(tempfile.gettempdir(), f"inspeximus-{h}.lock")
        self._fh = None
        self._tl = None
        self._locker = _LOCK_PRIMITIVE

    def __enter__(self):
        kind, mod = self._locker
        if kind is None:
            return self
        with _StoreLock._CACHE_GUARD:
            ent = _StoreLock._CACHE.get(self._path)
            if ent is None:
                ent = (threading.Lock(), None)
                _StoreLock._CACHE[self._path] = ent
        self._tl = ent[0]
        self._tl.acquire()                   # in-process first: one handle, so the OS lock cannot
        deadline = time.time() + 20.0        # separate two threads of ours
        while True:
            try:
                fh = _StoreLock._CACHE[self._path][1]
                if fh is None or fh.closed:
                    fh = open(self._path, "a+b")
                    _StoreLock._CACHE[self._path] = (self._tl, fh)
                if kind == "fcntl":
                    mod.flock(fh.fileno(), mod.LOCK_EX)
                else:
                    fh.seek(0)
                    mod.locking(fh.fileno(), mod.LK_LOCK, 1)
                self._fh = fh
                return self
            except OSError:
                _StoreLock._CACHE[self._path] = (self._tl, None)
                if time.time() >= deadline:
                    return self              # degrade to unlocked rather than lose the write
                time.sleep(0.05)

    def __exit__(self, *exc):
        kind, mod = self._locker
        try:
            if self._fh is not None:
                try:
                    if kind == "fcntl":
                        mod.flock(self._fh.fileno(), mod.LOCK_UN)
                    elif kind == "msvcrt":
                        self._fh.seek(0)
                        mod.locking(self._fh.fileno(), mod.LK_UNLCK, 1)
                except OSError:
                    pass                     # the handle stays cached; the next acquire re-opens it
                self._fh = None
        finally:
            if self._tl is not None:
                self._tl.release()
                self._tl = None
        return False


def _durable_replace(path, payload, encoding: str = "utf-8") -> None:
    """Write `payload` to a UNIQUE temp beside `path`, fsync it, then os.replace onto `path`.

    Two defects, one line apart, both measured:

      * A FIXED temp name (the store name plus ".tmp") is shared by every writer, so two processes
        wrote into the same file at overlapping offsets and os.replace promoted the BLEND. The tear
        signature is a blend, not a truncation -- one recovered store ended with two closing brackets.
        A unique name per write eliminated it outright: 156 tears -> 0 over the same 240 writes.
      * No fsync, so os.replace could publish a name pointing at unflushed bytes; a crash between the
        two leaves a valid-looking empty or partial store.

    os.replace is retried: on Windows it raises PermissionError when a reader has the target open,
    which cost ~19 of 240 writes in the measurement even after the temp name was made unique.
    """
    d = os.path.dirname(os.path.abspath(str(path))) or "."
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(str(path)) + ".", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload if isinstance(payload, bytes) else payload.encode(encoding))
            f.flush()
            os.fsync(f.fileno())
        last = None
        for attempt in range(40):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as e:            # Windows: a reader holds the target open
                last = e
                time.sleep(0.01 * (attempt + 1))
        raise last
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


class Inspeximus:
    def __init__(self, path: str | None = None, embed=None, receipts: bool = False,
                 receipt_key: str | None = None, receipt_pubkey: str | None = None,
                 receipt_signer=None,
                 capacity: int | None = None, revert_authority: str | None = None,
                 revert_pubkey: str | None = None, max_text: int | None = None,
                 tenant: str | None = None, pii_detect: bool = False,
                 encrypt_key: bytes | None = None, encrypt_passphrase: str | None = None,
                 support_authorities: list | None = None, persist_vectors: bool = False,
                 embed_query=None, embed_id: str | None = None,
                 infer_lineage: float = 0.0, echo_guard: bool | None = None,
                 agent: str | None = None, observe_recall: bool = False,
                 writer_key: str | None = None):
        """path: optional JSON file to persist to. embed: optional fn(str)->list[float] for semantic
        recall; if omitted, recall uses lexical token overlap (zero dependencies). embed_query: optional
        SEPARATE fn for embedding the recall QUERY (defaults to `embed`) — set it for an asymmetric
        embedder like nomic-embed-text, which is trained to prefix stored text with 'search_document: ' and
        queries with 'search_query: '; measured on LoCoMo (n=1536, reinforcement-controlled re-measure):
        recall_any@1 0.397 with prefixes on (the earlier 0.19->0.29 delta was measured under a since-fixed
        recall-reinforcement confound — see the 1.15.0 CHANGELOG correction; direction held, absolutes superseded).

        receipts/receipt_key (OPT-IN, default OFF -> identical legacy behavior): when enabled, every
        remember() appends a tamper-evident, hash-chained WRITE RECEIPT committing to the memory's
        content hash, persisted to a sidecar "<path>.receipts.json" (the main store format is unchanged).
        verify_writes() then proves the write history wasn't altered out-of-band — something an
        append-only store alone can't, because anyone who can edit the store file can rewrite a stored
        memory and the store would serve the altered text as original. The hash chain is zero-dependency;
        pass receipt_key (+ receipt_pubkey) from new_receipt_keypair() to also Ed25519-SIGN each receipt
        so a third party can verify it with the public key only. (Standalone version: agora-agent-receipts.)"""
        # expanduser + mkdir. Neither existed, and together they broke every documented install path:
        #   * the README's headline MCP command uses INSPEXIMUS_PATH=~/.inspeximus_memory.json, and a literal
        #     "~" is not a directory, so every write failed;
        #   * the plugin advertises .inspeximus/memory.json, and in a fresh project that folder does not
        #     exist, so remember() returned an id, in-process recall worked, and a NEW process saw nothing.
        # Measured: no file, no directory, and over MCP not even a warning — a memory layer that forgets
        # everything between sessions, on the path its own docs tell you to use.
        # os.fspath, not str: str() repr()s an os.PathLike into "<obj at 0x...>" and a bytes path
        # into "b'...'". Path() honoured __fspath__ correctly before 1.64.0, so the expanduser
        # change silently BROKE PathLike callers — the store went to a junk-named file, or on
        # POSIX to a real one nobody meant. os.fspath raises TypeError on a genuinely bad type,
        # which is the honest outcome.
        self.path = Path(os.path.expanduser(os.fspath(path))) if path else None
        if self.path is not None and self.path.parent and not self.path.parent.exists():
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass                     # unwritable parent: _save records it and flush() raises
        # ONE sentinel for "no embedder", because this class was reading two. The parameter defaults
        # to None, but `embed=False` is what the test suite and `audit_the_audits`'s own copy-opener
        # pass, and both are falsy -- so six sites read it truthily (`if self.embed:`, correct) while
        # four read it by identity (`self.embed is not None`, which calls False an embedder). The
        # divergence was not cosmetic:
        #   * `index_coherence` reported `embedder_configured: true` and `coherent: false` on a store
        #     deliberately opened WITHOUT one -- an index blamed for lagging when none exists.
        #   * the load-path realign guard called `False(text)`, caught the TypeError and wrote
        #     `vec = None`, DROPPING every persisted vector instead of skipping the realign.
        #   * `reembed()` returned `failed: N` rather than `error: no embedder configured`.
        # Coercing here fixes the class rather than the four instances, and keeps the two readings
        # from drifting apart again. An embedder is a callable and therefore always truthy, so no
        # legitimate value is lost.
        self.embed = embed or None
        self.embed_query = embed_query or None  # asymmetric query embedder (e.g. nomic search_query:); None -> use self.embed
        # Same coercion, same reason: the save path guards the recipe sidecar with `embed_id is not
        # None` and its own comment explains why a BLANK recipe must not be written ("blanking it here
        # would make the next semantic open see ''->recipe and realign for nothing") -- `embed_id=""` is
        # falsy and slips past an identity check, overwriting a real recipe with empty and causing
        # exactly the pointless realign the comment warns about. Found by re-auditing the class after
        # fixing `embed`, which is why that rule is a rule.
        self.embed_id = embed_id or None       # opaque fingerprint of the embed recipe (model+prefix); guards persisted vecs
        # OPT-IN vector persistence (default False -> legacy: vecs are a RAM-only cache, STRIPPED on save
        # to keep the file small and dodge the frozen-world GIL stall on big stores). Set True for a SMALL
        # store (e.g. the Claude Code coding memory, a few hundred items) whose process is short-lived and
        # reloaded often, so semantic recall survives a reload WITHOUT re-embedding every item each start.
        # Do NOT enable on large brain-scale stores — that is exactly the case the strip exists to protect.
        self._persist_vectors = bool(persist_vectors)
        # HARD TENANT ISOLATION (OPT-IN, default None -> unbound -> byte-identical legacy). Binding a store to
        # a tenant (Inspeximus(tenant="acme")) makes isolation a STORE PROPERTY, not a per-call argument a caller can
        # forget: every remember() is stamped with this tenant, and every read/supersession/erasure the store
        # performs is HARD-filtered to it. The guarantee is FAIL-CLOSED and non-bypassable from the content path:
        #   - recall() returns ONLY this tenant's records (a wrong/absent tenant sees nothing, never another
        #     tenant's data) — unlike the soft `scope=` recall arg, which sees everything if the caller omits it;
        #   - keyed supersession + the echo guard compare only WITHIN this tenant, so tenant A writing key
        #     "billing::plan" can never retire tenant B's same-key fact (cross-tenant write-through is closed);
        #   - forget_subject()/forget_pii()/pii_report() only ever touch this tenant's rows.
        # An UNBOUND store (tenant=None) is the admin/migration view: it sees + supersedes across everything
        # (legacy behavior) and its writes carry no tenant tag, so they are invisible to any tenant-bound store.
        # HONEST SCOPE: this isolates within ONE inspeximus store (logical multi-tenancy — the right model when many
        # agents share a process); it is NOT a substitute for separate stores/encryption keys when tenants are
        # mutually hostile and the process itself is the trust boundary. Mixing tenant-tagged and untagged writes
        # in one store is a migration state, not a steady one. Reversible: tenant=None. Receipt:
        # inspeximus/probes/tenant_isolation_probe.py (measured cross-tenant leak 0/N).
        self.tenant = str(tenant) if tenant is not None else None
        # AGENT-TO-AGENT READ GRANTS (OPT-IN, default None -> unbound -> byte-identical legacy). Binding a
        # handle to an agent (Inspeximus(agent="scribe") / store.as_agent("scribe")) makes that handle read
        # FAIL-CLOSED: it sees the records that agent OWNS (wrote through an agent-bound handle) plus the
        # records an ACTIVE grant authorises it to read -- and nothing else. With no grants issued, an
        # agent handle sees only its own writes; an UNBOUND handle (agent=None) is the operator view and is
        # exactly what it was before this feature existed. See grant() for the selector rules and the
        # honest scope. Reversible: agent=None.
        # PREFER store.as_agent("x") TO Inspeximus(agent="x"), and the reason is data loss, not style.
        # _save() serialises `self.items`, which this scoping filters, so a DIRECTLY BOUND handle persists
        # only the rows it can see and drops everyone else's on its first flush -- measured, and the same
        # for tenant= since 1.56.0. A view from as_agent() shares the parent's `_items` and forwards _save
        # to the parent, so it does not reach that path. Both are pinned in tests/test_agent_grants.py.
        self.agent = Inspeximus._check_agent_id(agent) if agent is not None else None
        self._acl_rev = 0                 # bumped by grant()/revoke() so a cached scoped view cannot go stale
        self._acl_writing = 0             # >0 only inside grant()/revoke(); the reserved-keyspace write gate
        # >0 only while the LIBRARY is stamping its own meta (sessions, revert). Same shape as
        # `_acl_writing` above and for the same reason: the reserved meta keyspace is closed to callers,
        # and the library writes those keys through the very `remember()` that enforces it. Without this
        # the fix silently breaks close_session() and revert(), whose markers would be stripped as if a
        # caller had sent them -- measured, not hypothetical: four internal call sites stamp reserved keys.
        # THREAD-LOCAL, and the reason is measured. This used to be a plain instance attribute, so
        # one thread's privileged write opened a window EVERY other thread's caller write walked
        # through: on an in-memory store shared by two threads, a forged reserved key survived on
        # 400 of 400 caller writes made during the window. The call-path argument -- 'only the
        # library can open this door' -- is about WHICH CODE opens it and says nothing about who
        # else is standing in it while it is open. File-backed stores never reached the race only
        # because the single-writer guard refuses a second writer first, which is protection by
        # accident rather than by design.
        self._priv = threading.local()
        self._acl_problems: list = []     # bounded log of grants that could NOT be evaluated (each one denied)
        # PII AUTO-DETECTION (OPT-IN, default OFF -> zero behavior change). When True, remember() runs the
        # zero-dependency regex detector (detect_pii) over each write and stamps rec['pii'] = [types...] so PII
        # records can be masked in-use (recall(redact_pii=True)), swept (forget_pii), and audited (pii_report).
        # A HEURISTIC (false negatives on obfuscated/non-Western formats + names/addresses; false positives on
        # PII-shaped ids), NOT a DLP guarantee — it REDUCES raw-PII exposure into LLM prompts + drives
        # data-minimization, it does not certify a record PII-free. Callers can also force/override per write with
        # remember(..., pii=True | ["email", ...]). Reversible: pii_detect=False.
        self.pii_detect = bool(pii_detect)
        # Bounded working set (OPT-IN, default None = unbounded append-only, byte-identical legacy).
        # When set, remember() hard-evicts the lowest-value ACTIVE memories past `capacity` using the
        # verified two-tier policy (value-protected + recency-aged, Lab 29992a). Lets inspeximus run in
        # production without unbounded growth — a gap vs bounded competitors (mem0/Letta).
        self.capacity = capacity
        # IDENTITY-CONFIDENCE FORK THRESHOLD (record-linkage clerical-review boundary / MDM steward-queue cut,
        # Fellegi-Sunter 1969). A keyed remember() carrying identity_confidence BELOW this forks a candidate
        # instead of superseding (see remember + candidates/promote_candidate). Default 0.7; only active when a
        # caller actually passes identity_confidence, so byte-identical legacy otherwise.
        self.fork_below = 0.7
        # READ-PATH REOPEN CORROBORATION (marintkael, r/RAG 2026-07-16). The confident wrong-merge is
        # unattackable at WRITE time — you cannot out-confidence your own confidence at the moment you write.
        # observe() is the mirror of the clerical-review band: a POST-write review trigger that reopens a
        # high-confidence settled interval when independent evidence CONTRADICTS it. To not flood on the benign
        # 'user restates a preference they forgot they changed' echo, a NAMED contradiction must be corroborated
        # by >= this many independent observations before it reopens; a single stray restatement stays below it.
        # (A value-obscuring revert — object=None, 'go back' — is an explicit action and reopens on first sight.)
        self.reopen_corroboration = 2
        # Per-record input cap (OPT-IN, default None = unbounded, byte-identical legacy). When set, remember()
        # truncates text longer than max_text chars and stamps meta["truncated_from"] with the original length —
        # an availability guard so a single malicious/runaway write can't exhaust memory. See SECURITY.md.
        self.max_text = max_text
        # AUTHORIZED REVERT CHANNEL (OPT-IN, default None = legacy: revert()/reaffirm are ungated).
        # When set, restoring a superseded value (revert(), route()'s revert branches, remember(reaffirm=True))
        # requires an out-of-band CAPABILITY = HMAC(revert_authority, key). The content path (route(text)) can
        # never mint it (it doesn't hold the secret), so a text-derived 'go back' cannot execute a restore —
        # it returns authorization_required and the principal confirms out of band. Textbook capability security
        # (Dennis & Van Horn 1966) / confused-deputy fix (Hardy 1988): separate the AUTHORITY (unforgeable
        # token) from the REQUEST (content). Honest boundary: this closes the content->restore path AT THE
        # STORE; it cannot stop a caller who hands the capability to the content path, nor authenticate a human.
        self.revert_authority = revert_authority
        # Asymmetric authority: the store holds only the PUBLIC key; the principal signs a revert challenge
        # with the matching PRIVATE key OFF the box (module-level sign_revert). The store can then VERIFY but
        # never MINT an authorization -> even a compromised on-box harness cannot forge a revert. Closes the
        # symmetric mode's residual (whoever holds the HMAC secret can mint). Both need cryptography for Ed25519.
        self.revert_pubkey = revert_pubkey
        # SIGNED-GROUNDS AUTHORITIES (1.9.4, marintkael r/RAG round 3). His residual on support-keyed reopen:
        # novelty-of-support is spoofable because the support strings ride the same read path the attacker owns
        # -> minting two DISTINCT fabricated strings still corroborates. When support_authorities is set (an
        # allowlist of Ed25519 public-key hexes held OUT of the content path), a novel support ground counts
        # toward the reopen threshold ONLY if it carries a valid signature by an allowlisted authority over the
        # canonical (key, contradicted-value) challenge, and independence is then measured by DISTINCT VERIFIED
        # KEYS, not distinct strings. So the fabricated-grounds attack moves from 'mint two strings' to 'forge
        # two Ed25519 signatures under allowlisted keys you do not hold'. Opt-in: None = byte-identical string
        # behaviour. Honest limit (unchanged): a signature attests SOURCE, not TRUTH — a key-holder can honestly
        # sign a false contradiction; what it buys is that Sybil variants of one source collapse to one key.
        # None = legacy string mode; a list OR dict (incl. empty) = signed mode, fail-CLOSED (no key verifies ->
        # nothing corroborates; never a silent fall-through to spoofable strings). PROVENANCE-CLASSES (1.9.5):
        # pass a dict {pubkey_hex: class_label} so keys sharing an upstream model/feed share a CLASS, and the
        # reopen threshold counts DISTINCT CLASSES, not raw keys — two commonly-sourced signers then count as one
        # (addresses the correlated-sources critique: distinct keys prove distinctness, not independence). A plain
        # list is the special case where every key is its own class (byte-identical 1.9.4 signed behaviour).
        self.support_authorities = support_authorities
        if support_authorities is None:
            self._support_pubkeys, self._support_class = None, {}
        elif isinstance(support_authorities, dict):
            self._support_pubkeys = set(support_authorities)
            self._support_class = {str(k): str(v) for k, v in support_authorities.items()}
        else:
            self._support_pubkeys = set(support_authorities)
            self._support_class = {str(k): str(k) for k in support_authorities}   # each key = its own class
        # in-stream revert nonce ledger (0.7.12): consumed on EVALUATION, landed or not. Landed intents also
        # persist their nonce in the record meta, so single-use survives a reload; a conflicted-but-unlanded
        # nonce is only held in memory (honest boundary: after a restart it would conflict again, not land).
        self._consumed_revert_nonces: set[str] = set()
        self._items: list[dict] = []
        self._file_sig = None       # (mtime_ns, size) of the file we loaded; guards against clobbering a peer
        self._tok_cache: dict[str, set] = {}     # id -> token set, so recall doesn't re-tokenize
        self._sig_cache: dict[str, str] = {}     # id -> normalized value signature (read-time conflict resolver)
        self._tc_cache: dict[str, dict] = {}     # id -> term-frequency map, for the BM25 hybrid channel
        # recall auto-mode: below this many active memories lexical is as good and free; above it the
        # lexical+semantic HYBRID (RRF) pays — measured to beat either channel alone on agent memory. Tunable.
        self.semantic_threshold = 300
        self._last_mode = "lexical"              # which mode the most recent recall() actually used
        self._mat = None                         # cached L2-normalized matrix of memory vectors (numpy)
        self._vec_rowof: dict[str, int] = {}     # memory id -> its row in self._mat
        self._mat_built_n = -1                   # item count when the matrix was built (rebuild on change)
        self._vec_mean = None                    # corpus mean vector (for anisotropy centering of semantic recall)
        # Anisotropy centering: subtract the corpus mean before cosine. Many embedders (e.g. nomic) are
        # anisotropic — all cosines compress into a narrow band — so semantic recall under-separates.
        # MEASURED on real LoCoMo (419 turns): centering lifts single-hop full-evidence recall@k by
        # +0.04..+0.07 (k=5/10/20) and is neutral on multi-hop. Reversible: set center_embeddings=False.
        self.center_embeddings = True
        # Two-tier keep-budget: when consolidate(keep) must drop surplus, PROTECT the top protect_frac
        # of the budget by RAW value (recency-immune) and fill the REST by EFFECTIVE (decay-weighted)
        # value — so a freshly-useful memory isn't evicted by a stale high-value one. A pure top-N-by-raw
        # prune keeps old high-value items forever and starves a drifting working set. MEASURED on a
        # simulation of inspeximus's own value-accrual + per-type decay: locality served-hit 0.22 -> 0.78,
        # neutral on rare-critical + poison-flood. Reversible: two_tier_keep=False -> legacy top-N-by-raw.
        self.two_tier_keep = True
        self.protect_frac = 0.30
        # Fast-novelty channel guard (OPT-IN, default OFF). inspeximus's state-toggle supersedes a standing
        # fact the moment a single similar+contradicting memory arrives — correct + fast for a TRUSTED
        # single source (configs/preferences: latest assertion wins), but a single-shot poison flip
        # (AgentPoison / MINJA) can then override a true fact. With this ON, a contradiction supersedes
        # only when CORROBORATED (earned credit, or >=2 corroborating links) — the same bar as graduation;
        # an uncorroborated single contradiction is recorded as a link but does NOT supersede. This is the
        # two-channel capstone's latency-floor tradeoff made explicit: robustness to single-shot poison at
        # the cost of lagging an uncorroborated single legitimate change. Leave OFF for trusted-source
        # stores; turn ON for adversarial / multi-tenant ingestion.
        self.supersede_requires_corroboration = False
        # Persistence supersession (OPT-IN, default OFF; set to an int >= 2 to enable). A standing fact is
        # superseded only when the contradicting NEW state is asserted by >= this many INDEPENDENT records —
        # i.e. the change must PERSIST/accumulate, not arrive once. This is the sequential-change-detection
        # (CUSUM) escape applied to memory: an isolated single-shot poison flip never crosses the threshold
        # and is rejected, while a genuinely sustained value change is adopted once `supersede_persistence`
        # corroborating records exist. The integer IS the Adaptation-Corruption law's detection-latency floor
        # d* made explicit — set it to your stream's corruption-vs-change ratio. Unlike
        # WARRANT AUTHORITIES (OPT-IN, default None -> any exogenous warrant string counts). When set to a
        # collection of trusted outcome-channel identifiers, credit_requires_warrant counts a warrant only if
        # it names one of them — so an ADAPTIVE MINJA attacker who forges a plausible warrant STRING (measured
        # to revert self-graded ASR from 0% back to ~70% when any string is accepted, probes/minja_influence_
        # gate.py cond. E) is rejected unless it can also name a declared trusted channel. This is the
        # set-membership tier; the UNFORGEABLE tier is an Ed25519-attested warrant (remember(attestation=...)
        # / strict_corroboration verified keys), which forces the attacker to forge a trusted key, not a string.
        self.warrant_authorities = None
        # supersede_requires_corroboration this needs NO external credit(): it adopts a genuine change purely
        # from repeated independent assertions, where the corroboration guard would lag one forever. MEASURED
        # (lab fea933, inspeximus's real consolidate() path): isolated-poison false-supersede 1 -> 0 while a
        # 3-record sustained change is still adopted; it Pareto-dominates both the naive (poison-fooled) and
        # corroboration-only (change-lagging) rules — see the adaptation-corruption coupling (a classical
        # quickest-change-detection tradeoff; lab f490d8).
        # Reversible: 0 or 1 -> legacy fast supersession.
        self.supersede_persistence = 0
        # ECHO GUARD (DEFAULT ON since 1.87.0). Closes the ECHO ATTACK on keyed
        # supersession: after a fact is corrected (old value -> superseded), a later RE-STATEMENT of the
        # OLD value (a benign restatement or an attacker re-injection) carries a newer valid_from and would
        # otherwise retire the FRESH value and resurrect the stale one. With this ON, an incoming keyed write
        # whose OBJECT (remember(..., object=...), else the normalized text) matches a value ALREADY
        # superseded for that key is a restatement-of-superseded: it is retired stale-on-arrival and the
        # current value is preserved. MEASURED (inspeximus/probes/echo_attack_probe_v2.py) on a MemBench echo
        # fixture: recency / mem0-v1 / bi-temporal-Graphiti-faithful all resurrect the stale value (stale
        # rate 0.21 -> 1.00 under both verbatim and paraphrased echo), and a verbatim-hash policy (MemStrata)
        # holds against verbatim (0.21) but is destroyed by paraphrase (1.00); the superseded-OBJECT ledger
        # holds against BOTH (~0.15). LOAD-BEARING LIMIT (measured, not assumed): paraphrase-resistance comes
        # ONLY from the OBJECT being value-preserving — embedding near-duplicate CANNOT separate a
        # same-value paraphrase (cos mean 0.95) from a different-value correction (0.84), they overlap
        # (~42% false-block at a 0.9 threshold), so the guard is object/text-based, NOT similarity-based; an
        # echo that OBSCURES the value (coreferent "her old hobby") is NOT caught. A genuine reversal back to
        # a superseded value needs remember(..., reaffirm=True) to bypass the guard (the guard cannot
        # un-supersede on its own). Reversible: echo_guard=False = legacy keyed supersession.
        #
        # DEFAULT ON since 1.87.0 — a BEHAVIOUR CHANGE, and the reason the old default is gone.
        # It was OFF so a direct API user "got exactly what they constructed", byte-identical to legacy.
        # In practice that meant the mechanism this library is FOR was off unless you knew to ask: every
        # product surface (MCP server, CLI, editor hook, the nine framework adapters) had to re-enable it
        # through _surface.open_store, and the adapters missed it for ten releases -- a correction made
        # through the CLI was undone by a restatement through an adapter, and then the honest re-correction
        # was refused as an echo, so the store could not be put right through the surface that broke it.
        # Measured on the live cross-system harness: with the guard off a paraphrased restatement of a
        # retired value brings it back 1.00 of the time; through the product surface, ~0.15.
        # "Correct a fact once and it stays corrected" is the first line of the README. A default that
        # contradicts it protects byte-compatibility at the cost of the promise. Set echo_guard=False
        # explicitly for the legacy behaviour.
        #
        # ONE RESOLUTION RULE, here, for the library AND every surface. The flip left the documented
        # off-switch dead in the library: `_surface.open_store` read INSPEXIMUS_ECHO_GUARD, the constructor
        # hardcoded True and took no kwarg at all, so `INSPEXIMUS_ECHO_GUARD=0` was silently ignored by a
        # direct API user -- measured, all three of =0, =1 and unset produced an identical guarded store.
        # A switch that reports nothing when it fails to take effect is worse than no switch. Precedence:
        # an EXPLICIT argument always wins (a caller who names a posture gets it, env or no env), else the
        # env var, else ON.
        self.echo_guard = _resolve_echo_guard(echo_guard)
        # STRICT corroboration (OPT-IN, default OFF). The corroboration bar
        # (episodic->semantic graduation AND the recall influence gate) counts ">=2 distinct sources". By
        # default a "source" is a canonical STRING (entity-resolved), which collapses honest sybil variants
        # ("Wikipedia"/"wikipedia.org"/URL) but is still SPOOFABLE by an attacker who supplies two unrelated
        # source strings it controls. With strict_corroboration ON, a corroborating link only counts if it
        # carries a VERIFIED KEY (remember(..., attestation=...)): independence is then measured by distinct
        # Ed25519 public keys an attacker cannot forge, so N sybil variants of one origin collapse to one
        # witness unless the attacker holds N distinct keys (a costly identity, Douceur 2002). This binds the
        # "independence" rail to the "origin-signed" rail; it does NOT make a claim TRUE (an attested source
        # can still sign a false claim), only makes manufactured independence expensive. Reversible: set False.
        #
        # WHY THIS STAYS OPT-IN, measured 2026-08-08 -- and the argument for flipping it is better than the
        # reason it cannot be flipped, so read the number before proposing it again.
        # The argument FOR: with this OFF, `corroborated` means "the writer typed two different source
        # strings", and our own replication on recovering provenance from content ruled FAILED with the
        # conclusion that the only signal that holds is metadata the writer cannot set.
        # The number AGAINST: across every store this deployment runs -- 111,264 records --
        # `attested_key` coverage is **0. Zero. 0.0000%**, while 96,716 records (86.9%) carry >=2 links.
        # `_distinct_verified_keys` counts only links whose record carries an attested_key, so flipping this
        # default would move every one of those 96,716 records from `corroborated` to `unwarranted` and the
        # tier would simply stop occurring. That is not a hardened default, it is a tier deleted by
        # omission -- the same shape as `slash(scope='source')` returning ok on 261,673 records because its
        # default scope resolved on a field no writer ever set.
        # THE REAL GAP IS COVERAGE, NOT THE DEFAULT: nothing in the deployment writes attestations. Ship
        # attestation coverage first; flip this after, and re-measure both numbers on the same day.
        # WRITER IDENTITY (optional). An Ed25519 secret (hex) this store signs its OWN writes with, so
        # `attested_key` is populated by ordinary operation instead of by every caller doing crypto by hand.
        #
        # WHY IT EXISTS, measured 2026-08-08: across every store this deployment runs -- 111,264 records --
        # `attested_key` coverage was 0.0000%. Not low. Zero. The only way to attest a write was for the
        # CALLER to hold a keypair and sign each claim, so nobody ever did, ourselves included, and the two
        # flags that read the field (`strict_corroboration`, and the distinct-key rail generally) could not
        # fire for a single record in production. Machinery with no path to it is machinery that is not there.
        #
        # HONEST SCOPE, and it is why this is not a trust root:
        #   * it attests AUTHORSHIP, not truth -- a key-holder can sign a false claim (see attest()).
        #   * it raises manufactured independence from "type two different source strings" to "hold two
        #     distinct persisted keys". Real cost, but a process free to mint keys can still mint witnesses;
        #     pin the writers you actually trust with `trust_seeds` ("key:<pubkey>") when you need more.
        #   * a compromised writer keeps its key.
        # An explicit remember(attestation=...) always wins: a claim signed by its real source must never be
        # relabelled with the local writer's key.
        self.writer_key = writer_key
        self.writer_pubkey = None
        if writer_key:
            if not _HAVE_ED:
                raise RuntimeError("writer_key needs the `cryptography` package (pip install cryptography)")
            self.writer_pubkey = _Ed25519SK.from_private_bytes(
                bytes.fromhex(writer_key)).public_key().public_bytes_raw().hex()
        self.strict_corroboration = False
        # EXOGENOUS-WARRANT credit (OPT-IN, default False). Closes the MINJA
        # self-graded-outcome hole (arXiv:2503.03704): the influence gate's earned-outcome path counts only
        # good credited with an exogenous `warrant` (an outcome the record did not author itself), so an
        # agent that self-grades its own recalled reasoning cannot corroborate a poisoned bridge into the
        # influence set. MEASURED (inspeximus/probes/minja_influence_gate.py): self-graded MINJA ASR 80% -> 0%
        # with this on, legit utility preserved when the app passes a real warrant. Reversible: set False.
        #
        # WHY THIS STAYS OPT-IN, measured 2026-08-08 -- same shape as strict_corroboration above.
        # The argument FOR flipping it: with this OFF, `earned` -- the top tier recall() reports -- is driven
        # by an outcome counter an agent can advance by grading its own recalled reasoning, i.e. the exact
        # attack this flag was built for (80% -> 0%), and opt-in leaves the forgeable configuration as the
        # one everybody gets.
        # The number AGAINST: `good_warranted > 0` coverage across the live stores is **0 of 60,077 records
        # (0.0000%)**, against `good > 0` at 96 (0.16%). Flipping it would make `earned` unreachable rather
        # than un-self-gradable. Both flags flipped together would report `unwarranted` for all 111,264
        # records -- a tier system with no positive state left, which reads as a broken feature, not a strict
        # one. Write warrants first, then flip.
        self.credit_requires_warrant = False
        # CREDIT BURST COLLAPSE (OPT-IN, default None -> OFF -> identical legacy behavior). Corroborating
        # LINKS already get this treatment: `temporal_gate` collapses co-arriving witnesses to one anchor,
        # because genuinely independent evidence spreads out in time while a burst is one coordinated act.
        # Credit never got the same rule, and that asymmetry is exploitable in BOTH directions.
        #   SUPPRESSION (the direction that has no defense here): the influence gate is
        #   `good_earned > 0 and good >= bad`, so a correct memory with G earned goods leaves the gate after
        #   G+1 failing episodes. An adversary who writes NOTHING, injects no content and forges no
        #   provenance -- controlling only the text of queries it may legitimately ask -- shapes queries so
        #   a true safety memory is co-recalled on episodes that genuinely fail. Every write-time defense in
        #   this library authenticates a WRITER; a credit edge has no author (the retrieval loop mints it
        #   from an outcome that really happened), so content scanning, signed receipts and source-diversity
        #   are all structurally blind to it. credit_requires_warrant does not help: a warrant attests the
        #   OUTCOME, never the ATTRIBUTION.
        #   ACCUMULATION (the mirror, already published): a non-causal memory riding along on successes.
        # PRIOR ART -- we claim only the TRANSFER, not a new attack class: bad-mouthing (Hoffman, Zage &
        # Nita-Rotaru, ACM Comput. Surv. 42(1), 2009) and RepTrap (Yang, Feng, Sun & Dai, SecureComm 2008)
        # WITHOUT ratings; in every prior formulation the attacker SUPPLIES the negative evidence, here it
        # supplies none. Simsek (arXiv:2604.12007) calls the same counter "associational, not causal" and
        # states no threat model.
        # Set to a number of SECONDS: same-polarity credit for one record inside that window counts ONCE,
        # so cost scales with DISTINCT OCCASIONS rather than raw volume.
        # MEASURED HERE (tests/test_credit_burst_window.py), memory with 5 earned goods:
        #   OFF            -> evicted from the gate in 6 attack episodes (~1 ms of wall clock)
        #   window = 0.05s -> 2,000 attack episodes over 117 ms bought only 3 bads; never evicted, and the
        #                     5 legitimate goods still landed in full because honest use was SPREAD OUT
        # So the property is not a speed multiplier, it is a change of CURRENCY: the adversary needs
        # good+1 WINDOWS OF ELAPSED TIME instead of good+1 requests, and request volume cannot buy time.
        # Pick the window from how far apart honest successes actually arrive in your app: anything shorter
        # than that spacing leaves legitimate learning intact while collapsing a burst.
        # A BURST OF HONEST CREDIT COLLAPSES TOO — this is polarity-symmetric and not attacker-specific. A
        # store that credits 5 successes inside one window records ONE good, so a window longer than your
        # real success spacing will under-count legitimate standing. That is the tuning cost, stated plainly.
        # HONEST RESIDUAL, and the reason T4 exists in that test file: this raises COST, it does not
        # prevent. An adversary with unlimited independent occasions and real failures is indistinguishable
        # from genuine evidence that the memory is wrong. Rejected alternatives: hysteresis on the gate
        # bought only 1.5x; requiring a warrant on `bad` looked perfect but its result was an artifact of
        # assuming warrants unforgeable (they are not, see credit_requires_warrant) and it trades
        # suppression-resistance for never demoting a wrong memory at all.
        self.credit_burst_window = None
        # SEED-ANCHORED FLOW TRUST (OPT-IN, default empty set -> OFF -> zero behavior change). The one axis
        # strict_corroboration does NOT close: distinct Ed25519 keys prove DISTINCTNESS, not COST -- a Sybil
        # mints N keypairs for free, so ">=2 distinct verified keys" is still forgeable by a determined
        # attacker (Douceur 2002). Cheng & Friedman (2005) prove no SYMMETRIC reputation function is
        # Sybilproof; only ASYMMETRIC, flow-based trust anchored to a costly/seeded root resists. This adds
        # that anchor: `trust_seeds` is a set of canonical source strings (or "key:<attested_key>") the
        # APPLICATION trusts a-priori (the operator's own source, an authenticated user). Trust then FLOWS
        # from a seed to sources it VOUCHES for -- a source U is trusted iff U is a seed, or a record whose
        # source is already trusted explicitly LINKS to a record authored by U (an endorsement edge),
        # transitively up to `trust_hops` (TrustRank/Advogato-style; Gyongyi et al. 2004). When trust_seeds is
        # non-empty, a corroborating witness counts toward the >=2-distinct-source bar ONLY if its source is
        # in the trust closure. N self-minted sources that no seed vouches for contribute ZERO trusted
        # witnesses, so they cannot manufacture standing. HONEST LIMITS: (1) inert without >=1 seed; (2) it
        # RELOCATES the residual from "mint N free keys" to "earn ONE endorsement from a seeded node" -- a
        # much higher bar, but a compromised/careless seed leaks trust into its vouched subtree (Cheng-Friedman's
        # asymmetric-flow residual, not closed); (3) the EARNED-OUTCOME path (credit(), good>0) stays orthogonal
        # and still grants standing regardless of seeds -- an unforgeable signal a writer cannot mint. Reversible:
        # empty set. Receipt: inspeximus/probes/seed_anchored_trust_probe.py.
        self.trust_seeds: set = set()
        self.trust_hops: int = 1
        # WRITE-PATH VALUE EXTRACTOR (OPT-IN, default None -> OFF -> zero behavior change). inspeximus's whole
        # governance layer keys on the supersession (key, object): keyed supersession, echo_guard, check_conflict,
        # forget_subject. But the caller has to supply key=/object= on every remember(), which the free-text
        # adapters (a conversation Session, a chat turn) don't do -- so supersession never fires on their writes.
        # Set `extractor` to a callable text -> (key, object) | None (your regex, or an LLM you call once and
        # cache) and remember() runs it whenever the caller didn't pass a key: the derived (key, object) then
        # drives supersession/echo_guard/check_conflict/forget_subject automatically, so the governance layer
        # composes over free text without threading keys through every call. HONEST: this is a before-save hook
        # (DB trigger / ORM before_save; textbook) -- the packaging is the point, not the idea. The supersession
        # is only as sound as your extractor: a mis-derived key mis-supersedes (the same risk as a wrong manual
        # key=), so keep the extractor deterministic/reviewable and prefer an explicit key when the caller knows
        # it. Fail-open: any exception in the extractor is swallowed and the write falls back to a plain append.
        self.extractor = None
        # STRICT PROVENANCE (store policy; the adversary-resistant form of the orphan rule). Default OFF ->
        # zero behavior change. When True, a write that shows NO provenance at all -- neither a `source` nor a
        # resolvable `derived_from` -- earns NO standing (orphan), regardless of any caller flag. This removes
        # the caller-elective hole in the `derived=` flag: an undeclared LLM summary (no source, lineage dropped)
        # is denied standing BY DEFAULT, not by a switch the untrusted caller can omit. To earn standing a write
        # must name a source (primary) OR name parents (derived); a bare fabrication can honestly do neither. (A
        # FAKE source string still passes here -> pair with strict_corroboration/attestation, which demands a
        # VERIFIED key, to price that too.) Biba-style default-deny at the store boundary. Reversible: OFF.
        self.strict_provenance = False
        # COHERENCE GATE (OPT-IN, default None -> OFF -> zero behavior change). When set to a float threshold in
        # [0,1], a corroborating `link` only COUNTS toward the >=2-distinct-source bar if its witness is actually
        # COHERENT with the claim (embedder cosine if `embed` is set, else lexical token-Jaccard), >= the threshold.
        # This closes the LAZY forged-source residual: a poison that clears the source COUNT with off-topic filler
        # witnesses no longer corroborates, because the filler isn't about the claim. HONEST LIMIT (measured, and
        # this is textbook adaptive-attack / common-mode territory -- Carlini-Wagner 2017, Knight-Leveson 1986,
        # PoisonedRAG): it does NOT close the residual, it RAISES the forger's bar from "2 distinct source strings"
        # to "2 distinct source strings + ON-TOPIC witness text"; a coherent forgery still passes, at a small
        # false-withhold cost on genuine recoveries phrased differently. A defense-in-depth layer, not a wall.
        self.coherence_gate: float | None = None
        # TEMPORAL GATE (OPT-IN, default None -> OFF -> zero behavior change; suggested by hannune on r/RAG). When
        # set to a window in SECONDS, corroborating links that CO-ARRIVE (their timestamps fall within the window
        # of each other) collapse to ONE anchor before the >=2-distinct-source count -- exactly as _distinct_sources
        # collapses one canonical source, but on TIME. Genuinely independent sources rarely write within seconds of
        # each other; a coordinated forgery writes its witnesses in a burst, so co-arrival is a soft flag even when
        # each source looks individually legitimate. HONEST LIMIT (textbook coordinated-burst / Sybil-timing
        # detection): a PATIENT attacker who spaces the forged writes out beyond the window defeats it (cf. the
        # sleeper -- patience buys past a timing signal). A soft decorrelated layer (timing is orthogonal to source
        # count and to content coherence), not a wall. Its value is exactly the decorrelation the attacker leaves.
        self.temporal_gate: float | None = None
        # AUTO-STAMP LINEAGE substrate: the ids of the most recent recall(), so a derived write (a summary written
        # right after) can inherit them as parents -- the lineage EDGE carried by the STORE from the recall->write
        # flow, not supplied by the untrusted LLM. Transient (not persisted); see remember(derived=True).
        self._last_recall: list[str] = []
        self._last_recall_text: str = ""            # normalized text of that recall, for infer_lineage
        # The OBSERVATION's own id list, deliberately not `_last_recall`. That attribute is the legacy
        # lineage substrate for derived=True / infer_lineage, and on a multi-hop recall it holds only the
        # LAST hop -- while `recall_iterative` returns the UNION of every hop. Reading the observation off
        # it would stamp a strict subset of what the caller actually saw, which does not merely lose data:
        # it biases the one metric this field exists to produce, silently and in a known direction
        # (understating what the free window captures). Separate lists, one meaning each.
        self._last_recall_window: list[str] = []
        self._last_recall_at: float = 0.0           # WHEN that recall happened, so a write can stamp the window's AGE
        self._last_recall_q: str = ""               # fingerprint (not text) of the query that drove it
        self._last_recall_writes: int = 0           # how many writes have already followed that recall
        # OBSERVE THE RECALL WINDOW (opt-in, default OFF = byte-identical legacy).
        #
        # The three attributes above are the store's own observation of the recall->write flow, and until now
        # BOTH ends were thrown away: they live in memory, are consumed at remember(), and die with the
        # process. So the question "on writes where the store observed a recall, how much of the true parent
        # set does the free window already capture?" cannot be asked of ANY historical store -- measured on
        # our own 8-agent deployment (2026-08-07), `derived_from` is filled on 0 of 181,523 records and none
        # carries a window field at all. There is nothing to replay. This flag persists the observation so
        # that in a few days there is.
        #
        # WHY THIS IS NOT THE `derived_from` MISTAKE AGAIN. The note above records that `derived_from` read
        # 0.00% over 27,290 real writes because "anything the writer must declare reads zero". The difference
        # is WHERE the decision sits: `derived_from` needs a judgement PER WRITE, from the caller, about
        # lineage it does not track; `observe_recall` needs ONE decision per store, at construction, and
        # after that the STORE fills it from a flow it already sees. A per-write declaration is a tax on the
        # hot path and gets skipped; a constructor flag is a deployment choice. That is why this is the
        # chokepoint fix and the flag it replaces was not.
        #
        # OBSERVATION, NOT CLAIM -- the distinction is the whole point and it must not erode. `derived_from`
        # ASSERTS parentage and therefore earns consequences: taint inheritance, the orphan rule, the
        # influence gate, evidence-grade capping. `recall_window` asserts only "the store served these ids,
        # at this time, before this write" -- which is true by construction and needs no threshold, no
        # embedding and no LLM. It feeds NOTHING. Nothing branches on it, nothing ranks on it, no gate reads
        # it. If a later change makes a gate consume it, the field stops being evidence and becomes a claim,
        # and the measurement it exists to enable is then measuring its own stamp.
        self.observe_recall: bool = bool(observe_recall)
        # Bound on a single stamp. A recall(k=500) would otherwise write 500 ids onto one record. Truncation
        # is NEVER silent: `n` carries the true count whenever it exceeds what was kept, because a cap that
        # quietly drops the tail deletes exactly the evidence half a later audit would need.
        self._recall_window_max: int = 64
        # INFER LINEAGE WITHOUT A FLAG (opt-in, default OFF = byte-identical legacy).
        #
        # The auto-stamp above only fires when the caller passes derived=True, and that flag is writer-set.
        # Measured 2026-07-25 on our own 8-agent, 43-day, 27,290-record deployment: `derived_from` coverage
        # was 0.00%, alongside `key`, `object`, `source` and `taint` -- while the fields the STORE computes
        # for itself ran at 88-90% (links, superseded). The single write call in that deployment passes four
        # arguments and none of them is a declaration. So the flag is not under-used, it is unused, in the
        # deployment most motivated to use it. Anything the writer must declare reads zero.
        #
        # `infer_lineage` is a threshold in (0,1] on the NULL-ADJUSTED overlap: how much more the new text
        # shares with what was just recalled than with a same-store baseline it was not built from. Clearing
        # it stamps that recall as the parents. No flag, no embedding, no LLM.
        #
        # !!! MEASURED AND FOUND WANTING (2026-07-25, same day it shipped). DO NOT ENABLE THIS ON THE
        # STRENGTH OF THE 1.49.0 RELEASE NOTES -- they reported a FIRING RATE and called it calibration.
        # Against constructed ground truth (probes/infer_lineage_precision.py) it fails in both regimes:
        #
        #   topically DIVERSE store, same-topic negatives : precision 0.06-0.23, recall 0.03-0.22.
        #                                                   At its best setting it stamps 43 wrong parents
        #                                                   for every 13 right ones.
        #   topically HOMOGENEOUS store                   : recall ~0, and blind BY CONSTRUCTION -- the
        #                                                   derived write overlaps the whole store exactly
        #                                                   as much as its parent (0.943 vs 0.943, lift
        #                                                   +0.000), so the null adjustment that rescues the
        #                                                   raw score here removes the entire signal.
        #
        # So the ~22% firing rate observed on our own 27,290-record deployment is NOT 22% of true
        # derivations; on this evidence most of it is noise. The threshold is not the knob it appears to be.
        # Kept, OFF, because the null-adjusted shape may still be a useful substrate for a better signal --
        # but the claim that it recovers lineage is withdrawn, not softened.
        #
        # Why it exists at all: the flagged path (derived=True) measured 0.00% over 27,290 writes. A mechanism
        # that requires the writer to opt in is a mechanism that does not run.
        self.infer_lineage = max(0.0, min(1.0, float(infer_lineage or 0.0)))
        # _save() THROTTLE: serializing the whole store (json.dumps of every item) is O(store size); doing
        # it on EVERY recall/remember froze callers once the store grew (recall mutates access value, so it
        # used to re-serialize everything each call). Coalesce disk writes to at most once / _save_min_s;
        # at most _save_min_s of access-metadata is lost on a hard crash (working memory — acceptable).
        self._save_min_s = 5.0
        self._last_save = 0.0
        self._dirty = False
        self._persist_error = None   # last _save() failure, surfaced by verify_writes() and raised by flush()
        # Sidecar failures live SEPARATELY: a successful main-store save must not erase the fact that the
        # receipt or tombstone chain never reached disk. (It did, in the first version of this fix.)
        self._sidecar_errors: dict = {}
        self._read_errors: dict = {}      # unreadable sidecars: surfaced, but NOT a persist failure
        # ENCRYPTION-AT-REST (OPT-IN, default None -> plaintext JSON, byte-identical legacy). encrypt_key is a
        # raw 32-byte AES-256 key (from new_encryption_key()); encrypt_passphrase is stretched with scrypt.
        # inspeximus NEVER persists the key/passphrase — you hold it; lose it and the store is unrecoverable (that IS
        # crypto-shred). See the module-level note for the honest threat model + shred(). The key is resolved
        # lazily against the on-disk salt so an existing encrypted store reloads with the same passphrase.
        if encrypt_key is not None and (not isinstance(encrypt_key, (bytes, bytearray)) or len(encrypt_key) != 32):
            raise ValueError("encrypt_key must be exactly 32 bytes (use inspeximus.new_encryption_key())")
        self._enc_rawkey = bytes(encrypt_key) if encrypt_key is not None else None
        self._enc_passphrase = encrypt_passphrase
        self._enc_salt = None                    # filled from the file header on load, or minted on first save
        self._encrypted = bool(encrypt_key is not None or encrypt_passphrase is not None)
        if self._encrypted and not _HAVE_AEAD:
            raise RuntimeError("encryption needs the `cryptography` package (pip install cryptography)")
        self._load_from_disk()
        # EMBED-RECIPE GUARD (persist_vectors only): persisted vectors are only comparable to a query embedded the
        # SAME way. If the store was written with a different embed recipe than the one now in use — most importantly
        # an ASYMMETRIC upgrade (e.g. adding nomic's search_document:/search_query: prefixes) — a query in the new
        # space would silently mis-match the old stored vectors and DEGRADE recall. When embed_id changes, we drop
        # the stale vectors and re-embed with the current document embedder (once, on load) so the spaces realign.
        # RAM-only stores (persist_vectors=False) strip vectors on save, so they never hit this. Sidecar: <path>.embedid.
        self._embedid_path = (self.path.parent / (self.path.name + ".embedid")) if self.path else None
        self._realigned = False
        if self._persist_vectors and self._embedid_path is not None:
            _prev = None
            if self._embedid_path.exists():
                try:
                    _prev = self._embedid_path.read_text(encoding="utf-8").strip()
                except Exception:
                    _prev = None
            _cur = self.embed_id or ""
            # ONLY records that carry a vec are in the old space, so they are the only ones to realign.
            # Re-embedding vec-less records here would (a) make a load cost one network call per record —
            # an unbounded stall, and (b) silently ADD vectors the store never had.
            _stale = [r for r in self.items if r.get("vec") and r.get("text") is not None]
            if _prev is not None and _prev != _cur and self.embed is not None and _stale:
                try:
                    _cap = int(os.environ.get("INSPEXIMUS_REALIGN_MAX", "256"))
                except Exception:
                    _cap = 256
                if len(_stale) > _cap:
                    # BOUNDED: past the cap we DROP the stale vectors instead of re-embedding them. A dropped
                    # vec degrades that record to lexical recall and is re-embedded on its next write; a
                    # synchronous re-embed of a large store on the load path would hang the caller for
                    # minutes-to-hours (and every hook-style short-lived process would pay it again).
                    sys.stderr.write(f"[inspeximus] embed recipe changed ({_prev!r} -> {_cur!r}); {len(_stale)} persisted "
                                     f"vectors exceed INSPEXIMUS_REALIGN_MAX={_cap} -> dropping them (recall degrades to "
                                     f"lexical for those records; each is re-embedded on its next write). Rebuild "
                                     f"the space deliberately with reembed() / `inspeximus reembed`, or raise the cap.\n")
                    for r in _stale:
                        r["vec"] = None
                else:
                    sys.stderr.write(f"[inspeximus] embed recipe changed ({_prev!r} -> {_cur!r}); re-embedding "
                                     f"{len(_stale)} persisted vectors to realign the space\n")
                    for r in _stale:
                        try:
                            r["vec"] = list(self.embed(r["text"]))
                        except Exception:
                            r["vec"] = None
                self._mat = None                                    # invalidate the cached matrix
                self._realigned = True                              # -> persisted ONCE at the end of __init__
        # OPT-IN write receipts (default OFF -> zero behavior change; no sidecar created)
        self.receipts_enabled = bool(receipts or receipt_key or receipt_signer)
        self._receipt_sk = receipt_key
        # OPT-IN write-authority boundary: a callable `sign(hash_hex) -> sig_hex` whose key this process
        # does not hold. When set, `receipt_key` is not used and never needs to exist here.
        self._receipt_signer = receipt_signer
        if receipt_signer is not None and receipt_key:
            raise ValueError("pass receipt_key OR receipt_signer, not both: holding the key in-process "
                             "defeats the boundary the signer exists to create")
        # The public half is DERIVED when it is not supplied. Without this, passing receipt_key alone signed
        # every receipt with `"pubkey": None`, so verify_writes() could never check the signature and reported
        # "invalid signature" on records the store had just written itself -- a false tampering alarm, which
        # for an integrity layer is worse than no signal at all. A bad key is also rejected here rather than
        # thousands of writes later, deep inside remember().
        if receipt_key and not receipt_pubkey:
            if not _HAVE_ED:
                raise RuntimeError("signing write receipts needs the `cryptography` package "
                                   "(pip install cryptography)")
            try:
                receipt_pubkey = _Ed25519SK.from_private_bytes(bytes.fromhex(receipt_key)).public_key(
                    ).public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw).hex()
            except Exception as e:
                raise ValueError("receipt_key must be a 32-byte Ed25519 private key as hex "
                                 "(use new_receipt_keypair()); got an unusable value") from e
        self.receipt_pubkey = receipt_pubkey
        self._receipts: list[dict] = []
        self._receipts_path = (self.path.parent / (self.path.name + ".receipts.json")) if self.path else None
        if self.receipts_enabled and self._receipts_path and self._receipts_path.exists():
            try:
                self._receipts = json.loads(self._receipts_path.read_text(encoding="utf-8"))
            except Exception:
                self._receipts = []
        # DELETION TOMBSTONES (erasure-with-audit). forget() genuinely removes content, which otherwise makes
        # verify_writes() report the now-missing record as "deleted out-of-band" — a legitimate GDPR-erasure is
        # then INDISTINGUISHABLE from tampering. A tombstone is a hash-chained (optionally Ed25519-signed) marker
        # that records the FACT of a deliberate erasure — the record's random surrogate id (uuid, NOT content-
        # derived), a UTC ts, and an opaque caller request_id — and NOTHING derived from the content (a hash of PII
        # is still PII, EDPB; so no content hash lands here). verify_writes() then treats a tombstoned missing
        # record as ACCOUNTED-FOR (chain intact, erased at T), while a record missing WITHOUT a tombstone still
        # flags as out-of-band tampering. HONEST SCOPE: this proves the ACT of deletion within THIS inspeximus store
        # only (not the app's vector store / logs / backups), it is NOT a compliance guarantee, and the signature
        # is load-bearing only against a party OTHER than the key holder (an operator who holds receipt_key can
        # forge tombstones too). Prior art credited: crypto-shredding, Cassandra tombstones, Art.30 erasure logs,
        # Crosby-Wallach/Certificate-Transparency tamper-evident logs.
        self._tombstones: list[dict] = []
        self._tombstones_path = (self.path.parent / (self.path.name + ".tombstones.json")) if self.path else None
        if self._tombstones_path and self._tombstones_path.exists():
            try:
                self._tombstones = json.loads(self._tombstones_path.read_text(encoding="utf-8"))
            except Exception:
                self._tombstones = []
        # PERSIST A REALIGNMENT EXACTLY ONCE. The realigned vectors and the recipe sidecar must land together:
        # the sidecar is written only inside _save(), so a caller that never saves (a READ-ONLY path — recall(),
        # a session-digest, any short-lived hook process) would redo the whole realignment on EVERY open, turning
        # one migration into a permanent per-open network storm. Saving here ends it after the first open.
        # It must NOT be done by writing the sidecar alone: that would leave the OLD vectors on disk labelled
        # with the NEW recipe — precisely the silent mismatch this guard exists to prevent.
        if self._realigned:
            self._save(force=True)

    # ── capture ──────────────────────────────────────────────────────────────
    # A THREAD-LOCAL MADE THE STORE UNCOPYABLE, and copying it is something callers legitimately do.
    # `threading.local()` cannot be pickled or deep-copied, so making the privilege window per-thread
    # broke `copy.deepcopy(store)` with TypeError -- caught by an anchor test that copies a store to
    # simulate an operator rewriting it behind the library's back. The security fix was right; storing
    # its holder as ordinary instance state was not.
    #
    # A COPY GETS A FRESH, CLOSED WINDOW rather than an inherited one, and that is the safe direction:
    # a copy taken while some thread held privilege open must not start life privileged. Copying is not
    # a way to smuggle the window across.
    def __getstate__(self):
        state = dict(self.__dict__)
        state.pop("_priv", None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._priv = threading.local()

    def _stamp(self, *a, **kw) -> str:
        """remember() for the LIBRARY's own markers, which live in the reserved meta keyspace.

        `remember()` strips reserved keys because a caller must not be able to hand itself the top
        trust tier (see _RESERVED_META). The library writes those same keys -- revert nonces, session
        sequence numbers, digest entries -- through that identical entry point, so it needs the same
        escape hatch `grant()`/`revoke()` already use for the reserved KEY prefix. Anything routed
        here is trusted by construction; nothing reachable from a caller may call it.
        """
        self._priv.n = getattr(self._priv, "n", 0) + 1
        try:
            return self.remember(*a, **kw)
        finally:
            self._priv.n -= 1

    def remember(self, text: str, tags=None, value: float = 1.0, meta: dict | None = None,
                 mtype: str | None = None, valid_from: float | None = None,
                 source: dict | None = None, key: str | None = None,
                 derived_from: list | None = None, attestation=None, derived: bool = False,
                 provisional: bool = False,
                 object: str | None = None, reaffirm: bool = False, capability: str | None = None,
                 pii=None, identity_confidence: float | None = None,
                 user_id: str | None = None, agent_id: str | None = None, session_id: str | None = None,
                 project: str | None = None) -> str:
        """Append-only raw capture. Stamped with an absolute UTC time; never edited afterward.
        mtype in {episodic, semantic, procedural} sets the decay prior (episodic fades fast,
        semantic slow, procedural barely); inferred from the text if not given. Pass it explicitly
        when the caller knows the kind — inference defaults to episodic (the conservative, fast-decay
        choice) and only promotes on clear markers.

        `key` (OPT-IN) is a deterministic supersession key — typically a (subject, relation) identifier,
        e.g. "billing-api::auth-method". When set, remembering a new value RETIRES every active record
        sharing the same key (status -> superseded), with NO similarity threshold and NO LLM call. This
        closes the 'supersession blind spot': cosine similarity cannot tell a contradicted fact from its
        replacement (we measured AUROC ~0.61, near chance — a contradiction is often MORE embedding-similar
        to the original than a rephrase is), so a similarity-based store silently serves the stale value
        (~42% of the time in our test). A deterministic (subject, relation, object) ledger drives that to
        ~0%. Bi-temporal: a back-filled record (earlier valid_from) does NOT overwrite a genuinely newer
        same-key value — the stale-on-arrival record is the one retired."""
        # AUTO-STAMP LINEAGE (jacksonxly / MemLineage arXiv:2605.14421): a derived write (a summary / consolidation)
        # that names no explicit parent inherits the store's most recent recall as its parents. The lineage EDGE is
        # carried by the STORE from the recall->write flow -- the untrusted LLM only supplies the summary text and
        # never holds the switch -- so a summary written right after a recall automatically carries its ancestors'
        # taint (a retraction reaches it; it is not an orphan) WITHOUT the caller threading derived_from through the
        # rewrite. If no recent recall exists, an explicit derived=True falls through to the orphan rule (fail-closed).
        # This is the store-side inference the storm/verify pass found to be the ONLY form with measured defense
        # value (signature-only 6/6 attacks -> 0/6 once lineage propagates); a caller-supplied source string is not.
        if derived and derived_from is None:
            derived_from = list(getattr(self, "_last_recall", []) or [])
        # ...and the same stamp WITHOUT the flag, when the store can see the derivation itself. See the
        # infer_lineage note in __init__ for why the flagged path measured 0.00% over 27,290 real writes.
        elif (derived_from is None and self.infer_lineage > 0.0
              and getattr(self, "_last_recall", None) and self._last_recall_text):
            # NULL-ADJUSTED, not raw. Measured on 27,342 real agent writes: a raw overlap threshold is
            # DEGENERATE -- median overlap against the true predecessor is 1.000 and a 0.8 threshold still
            # stamps 77% of writes, because agents reuse a small vocabulary. Against a random window from
            # the same store the overlap is still 0.54, so most of the raw score is vocabulary, not lineage.
            # Subtracting that null leaves the part that is actually about THIS recall (mean lift +0.32).
            if (self._overlap(text, self._last_recall_text)
                    - self._overlap(text, self._null_context())) >= self.infer_lineage:
                derived_from = list(self._last_recall)
                derived = True                      # so the orphan/standing rules treat it as derived
        # WRITE-PATH EXTRACTOR: derive (key, object) from the text when the caller didn't supply a key and an
        # extractor is plugged, so the governance layer keys itself over free text. Fail-open (never break a write).
        rec_asserts_change = True
        if self.extractor is not None and key is None and not derived:
            try:
                ex = self.extractor(text)
                if isinstance(ex, tuple) and len(ex) in (2, 3):
                    key = ex[0]
                    if object is None:
                        object = ex[1]
                    # OPTIONAL THIRD ELEMENT: does this sentence ASSERT A CHANGE ("changed to", "update
                    # it to", "actually it's X now") or merely restate a value ("your address remains
                    # X")? Supersession keyed on a differing object STRING cannot tell the difference,
                    # and the difference is not cosmetic: `Unit 4A` and `742 Birchwood Lane, Unit 4A`
                    # are the same fact stated at two granularities, but they differ as strings, so a
                    # restatement retires the record it agrees with. Measured (MemOps corpus): with
                    # echoes keyed, the CURRENT value became unretrievable at k=100 for 7 of 12
                    # correction chains -> 4 of 12. A 2-tuple keeps the legacy behaviour exactly.
                    if len(ex) == 3 and ex[2] is False:
                        rec_asserts_change = False
            except Exception as e:
                # A raising extractor leaves key=None, which silently disables supersession for that write —
                # and a store where every write hits it looks exactly like an unkeyed store
                # (supersession_report: 0). The write still proceeds (an extractor is an enrichment, not a
                # gate), but the failure is now counted and surfaced instead of vanishing.
                self._extractor_errors = getattr(self, "_extractor_errors", 0) + 1
                self._extractor_last_error = f"{type(e).__name__}: {e}"
        # availability guard (OPT-IN): cap a single record's text so one runaway/malicious write can't exhaust
        # memory. Truncate rather than reject (don't break the app), and record the original length. SECURITY.md.
        _trunc_from = None
        if self.max_text is not None and isinstance(text, str) and len(text) > self.max_text:
            _trunc_from = len(text)
            text = text[:self.max_text]
        for _n, _v in (("value", value), ("valid_from", valid_from)):
            if _v is not None and isinstance(_v, float) and not math.isfinite(_v):
                # inf sorted first in every recall forever; nan never compared true so the record sank
                # silently, and its bi-temporal window was undefined.
                raise ValueError(f"remember({_n}=...) must be a finite number, got {_v!r}")
        if meta is not None:
            # Reject an unserialisable `meta` AT THE WRITE that carries it. Accepting it stored one poisoned
            # record that made every subsequent _save() of the whole store fail — so the damage was not the
            # bad record, it was every good record written after it. Fail the one call that is wrong.
            try:
                json.dumps(meta, ensure_ascii=False)
            except (TypeError, ValueError) as e:
                raise ValueError(f"remember(meta=...) must be JSON-serialisable, else the whole store stops "
                                 f"persisting: {e}") from None
        # RESERVED KEYSPACE. Access-control acts are records, which is what makes them auditable -- and it
        # is also what would make the ACL decorative if any caller could write one. An agent that can call
        # remember(key="acl::grant::*::me::tag::secrets", object="granted") has granted itself access
        # through the ordinary write path. Only grant()/revoke() may mint this prefix.
        if key is not None and str(key).startswith(_ACL_PREFIX) and not getattr(self, "_acl_writing", 0):
            raise ValueError(
                f"{_ACL_PREFIX!r} is the reserved access-control keyspace: a grant may only be written by "
                f"grant()/revoke(), never through remember(). Otherwise any writer could authorise itself.")
        # THE OTHER GUARD KEYSPACE, reserved for the same reason and previously not reserved at all.
        # `_GUARD_KEYSPACES` has two prefixes; `remember()` reserved one. Guard records are excluded
        # from the capacity population BEFORE the comparison and from the consolidation keep-budget,
        # and they are still rankable by recall -- so writing under the unreserved prefix bought
        # permanent exemption from a bound the operator configured. Measured 2026-08-15, capacity=10:
        # ordinary keys -> 10 active, 8 of the attacker's 20 survive and 8 honest records are evicted;
        # `code::symbol::` keys -> 30 active, OVER CAP BY 20, and every honest record survives because
        # nothing was ever evicted. The payload is recallable throughout.
        #
        # `deprecate_symbol` is the legitimate writer and goes through the same door as grant()/revoke().
        if key is not None and str(key).startswith(_CODE_GUARD_PREFIX) \
                and not getattr(self, "_guard_writing", 0):
            raise ValueError(
                f"{_CODE_GUARD_PREFIX!r} is the reserved code-guard keyspace: records under it are exempt "
                f"from capacity eviction and from the consolidation keep-budget, so an arbitrary writer "
                f"could hold unbounded rows outside a cap the operator set. Use deprecate_symbol().")
        # RESERVED META KEYSPACE (see _RESERVED_META). The caller's dict is about to be copied onto the
        # record, and the library reads its own decisions back out of that dict, so anything it stamps
        # must not be arrivable from outside. Stripped silently rather than raising: a writer probing
        # for the top tier gets no error and no privilege, which is the behaviour we want, and an
        # honest caller was never setting these.
        if meta and not getattr(getattr(self, "_priv", None), "n", 0):
            _aliased = {}
            for _k in list(meta):
                if _k in _META_ALIASED_PARAM:
                    _aliased[_META_ALIASED_PARAM[_k]] = meta[_k]
            meta = {k: v for k, v in meta.items()
                    if k not in _RESERVED_META and k not in _META_ALIASED_PARAM}
            # Route, do not discard: the named parameter wins when both are given, since it is the
            # explicit one, and otherwise the meta value reaches the SAME validated path it skipped.
            if _aliased.get("user_id") is not None and user_id is None:
                user_id = _aliased["user_id"]
            if _aliased.get("agent_id") is not None and agent_id is None:
                agent_id = _aliased["agent_id"]
            if _aliased.get("session_id") is not None and session_id is None:
                session_id = _aliased["session_id"]
            if _aliased.get("project") is not None and project is None:
                project = _aliased["project"]
        mid = uuid.uuid4().hex[:10]
        now = time.time()
        rec = {"id": mid, "text": text, "tags": list(tags or []), "value": float(value),
               "ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               # EVENT-TIME (bi-temporal). Accepts an epoch float or an ISO-8601 string, and RAISES on
               # anything else rather than falling back to `now`: a guessed event time that reads as a
               # declared one is exactly what `valid_from_source` below exists to make impossible.
               "valid_from": Inspeximus._event_time(valid_from, now),
               "source": Inspeximus._check_source(source),   # re-checkable origin (e.g. {"doc": id, "span": [start, end]}) so a recalled fact can be traced back, not trusted blind
               # FINGERPRINT THE SOURCE IF IT IS ACTUALLY FETCHABLE. Written into the reserved meta
               # keyspace below, never into `source` -- that dict comes from the caller, and a digest
               # a writer can set is a drift check the writer can defeat. See check_sources().
               "mtype": mtype or _infer_type(text), "last_access": now,
               # WAS THE TYPE DECLARED, OR GUESSED FROM THE TEXT? `mtype` picks the decay half-life
               # (7 days episodic vs 3650 days procedural -- 521x) and `_effective_value` ranks
               # capacity eviction by it, so a label inferred from CALLER TEXT sets its own retention
               # class. Measured 2026-08-15 with attacker and honest records written interleaved at
               # the same time, so age and store order are neutral, and one word different inside the
               # attacker's own text: without it, evictions alternate 2/3, 3/3, 3/4, 4/4 -- symmetric.
               # With "always" added, 6 honest records are destroyed and 2 attacker records are, an
               # exact inversion. Word choice in a document the agent reads aims the delete.
               #
               # Tightening the regex is unwinnable -- any keyword list over attacker-controlled text
               # is bypassable -- and it is not the defect. The defect is that a guess confers a
               # PRIVILEGE. Recorded here and honoured in _effective_value; inference still helps
               # ranking, but it cannot buy immortality.
               "mtype_inferred": mtype is None,
               "status": "provisional" if provisional else "active",
               "links": [], "meta": dict(meta or {})}
        # SOURCE FINGERPRINT (see check_sources): only when the doc is a path that exists right now.
        # Reserved keyspace, so a later writer cannot forge freshness for content it changed.
        #
        # TWO PROPERTIES, NOT ONE, and we were reporting one as if it were both. Named by safal207 on
        # anthropics/claude-code#34556, where OmniMemory recorded `git rev-parse HEAD:<path>` -- the
        # COMMITTED blob -- while the agent read the WORKING TREE:
        #
        #   REFETCHABILITY      can the source be resolved again?
        #   OBSERVATION BINDING is the fingerprint of the exact bytes the agent actually observed?
        #
        # We never had the git version -- we hash the file on disk, which IS the working tree -- and
        # that made it easy to conclude this did not apply. It does, one layer in: we hash at WRITE
        # time, and an agent reads, reasons, and only then calls remember(). Measured 2026-08-16: read
        # value='A', let the file become 'B', write the memory claiming A, and the recorded digest is
        # B's while check_sources reports FRESH. A wrong-but-resolvable fingerprint is worse than none
        # -- it is false confidence rather than an absence.
        #
        # `observed_sha256` lets a caller bind what it READ. It is a caller CLAIM and cannot be
        # otherwise -- only the reader knows what it read -- so it does not replace the library's own
        # hash, it is compared against it. Disagreement means the source moved between observation
        # and capture, which is neither FRESH nor later drift and must not be filed as either.
        _sdoc = Inspeximus._raw_source(rec)
        _obs = (source or {}).get("observed_sha256") if isinstance(source, dict) else None
        if _obs is not None:
            # VALIDATED, AND IT RAISES. `observed_sha256=12345` used to be stored as-is and then
            # blow up on `.lower()` inside the blanket `except Exception: pass` below, dropping
            # `source_sha256` entirely -- so a content-only writer could make its own memory
            # permanently un-drift-checkable while the record looked ordinary. Silently losing the
            # fingerprint is the worst of the three possible behaviours; refusing the write is the
            # only one that tells the caller they got it wrong.
            _obs = str(_obs).strip().lower()
            if len(_obs) != 64 or any(c not in "0123456789abcdef" for c in _obs):
                raise ValueError(
                    f"remember(source={{'observed_sha256': ...}}) must be a 64-character hex sha256 "
                    f"of the bytes you actually read; got {str(_obs)[:24]!r}. Refusing rather than "
                    f"storing it, because an unusable fingerprint reads as an unverifiable memory.")
            rec["meta"]["observed_sha256"] = _obs
        if _sdoc and isinstance(_sdoc, str) and _obs and not (
                os.path.exists(_sdoc) and os.path.isfile(_sdoc)):
            # A NON-FILE SOURCE THE CALLER VOUCHED FOR. The capture block below is gated on
            # `os.path.isfile`, so for an `https://` doc -- the case `check_sources(resolver=)` exists
            # for -- `observed_sha256` was stored and then ignored: no fingerprint, verdict
            # UNCHECKABLE, coverage 0.0. But a caller-declared observation needs no local file; that
            # is the whole point of declaring it. The library cannot compare it here, and
            # check_sources will when the resolver fetches.
            rec["meta"]["source_sha256"] = _obs
            rec["meta"]["source_seen_at"] = now
            rec["meta"]["observation_bound"] = True
        if _sdoc and isinstance(_sdoc, str):
            try:
                if os.path.exists(_sdoc) and os.path.isfile(_sdoc):
                    with open(_sdoc, "rb") as _fh:
                        _at_write = hashlib.sha256(_fh.read()).hexdigest()
                    rec["meta"]["source_sha256"] = _obs.lower() if _obs else _at_write
                    rec["meta"]["source_seen_at"] = now
                    # The claim is bound to the bytes the caller says it read ONLY when it said so.
                    # Absent, this is a write-time hash and must not be counted as observation-bound
                    # -- that is the whole point of keeping the two numbers apart.
                    rec["meta"]["observation_bound"] = bool(_obs)
                    if _obs and _obs.lower() != _at_write:
                        # Recorded, not raised: the write is legitimate and the memory may well be
                        # right about what it read. What is NOT legitimate is calling it FRESH.
                        rec["meta"]["source_moved_before_capture"] = True
                        rec["meta"]["source_at_capture_sha256"] = _at_write
            except Exception:
                pass                       # an unreadable source is UNCHECKABLE, never a failed write
        if not rec_asserts_change:
            rec["meta"]["asserts_change"] = False       # a restatement, not a correction (see extractor block)
        if _trunc_from is not None:
            rec["meta"]["truncated_from"] = _trunc_from
        # MEMORY HIERARCHY (user > agent > session): stamp the scope this memory belongs to. A memory with only
        # uid set is user-level (shared across that user's agents/sessions); adding aid/sid narrows it. recall()
        # then filters by hierarchical VISIBILITY (a session query sees session + agent + user memories, but a
        # user-level query does NOT pull session-specifics). Only set fields are stamped -> unset = wildcard.
        if user_id is not None:
            rec["meta"]["uid"] = str(user_id)
        if agent_id is not None:
            rec["meta"]["aid"] = str(agent_id)
        if session_id is not None:
            rec["meta"]["sid"] = str(session_id)
        # PROJECT / WORKSPACE STAMP (opt-in): which project this memory belongs to, for a store shared across
        # several repos/workspaces by one coding agent. Stamped ONLY when a project is named -> an unstamped
        # record is GLOBAL (visible from every project), which is what makes adopting a project scope
        # non-destructive: memories written before you opted in stay reachable. recall(project=...) applies
        # the same wildcard rule the uid/aid/sid hierarchy above uses. project=None leaves no stamp at all,
        # so an unscoped store is byte-identical to one written before this existed.
        if project is not None:
            rec["meta"]["project"] = str(project)
        # TENANT STAMP: bind this write to the store's tenant so recall/supersession/erasure can isolate it.
        # Unbound stores (tenant=None) leave no tag -> byte-identical legacy.
        if self.tenant is not None:
            rec["tenant"] = self.tenant
        # OWNER STAMP: an agent-bound handle owns what it writes, which is what makes "grant a subset of MY
        # records to another agent" meaningful. Unbound handles leave no tag -> byte-identical legacy.
        if getattr(self, "agent", None) is not None:
            rec["owner_agent"] = self.agent
        # PII TAG: record which PII types this write carries, for masking (recall(redact_pii=True)),
        # data-minimization sweeps (forget_pii), and audit (pii_report). `pii` overrides/forces detection:
        #   pii=True -> auto-detect types; pii=["email",...] -> use these types verbatim; pii=False -> tag none;
        #   pii=None (default) -> auto-detect iff the store has pii_detect=True. Detection is on the ORIGINAL text.
        _pii_types = None
        if pii is False:
            _pii_types = []
        elif isinstance(pii, (list, tuple, set)):
            _pii_types = sorted({str(p) for p in pii})
        elif pii is True or (pii is None and self.pii_detect):
            _pii_types = sorted(detect_pii(text).keys())
        if _pii_types:
            rec["pii"] = _pii_types
        # TAINT INHERITANCE (provenance that rides through transformation): when this memory is DERIVED from
        # others (a summary, a consolidation, an LLM rewrite), it inherits the union of its parents' canonical
        # sources — transitively, since a parent's own inherited taint is included. Without this, an app-side
        # summary is a fresh record with no source, so slash()/per-source attribution can't reach it: the
        # cumulative influence cap and the retroactive slash both need provenance to survive summarization to be
        # countable at all. `derived_from` is the substrate everything else (cap, slash) is deterrence math on.
        if derived_from:
            _by = {x["id"]: x for x in self.items}
            taint, links, unresolved = set(), [], []
            for pid in derived_from:
                p = _by.get(pid)
                if p is None:
                    # DO NOT DROP IT SILENTLY. A parent id that does not resolve used to vanish here, so a
                    # caller who declared `derived_from=["<typo>"]` got a record with no lineage AND full
                    # primary standing -- the write announced itself as derived and was banked as an
                    # observation. Standing inflation through a typo. Keep the id for audit, and let the
                    # orphan rule below see that lineage was CLAIMED and none of it landed.
                    unresolved.append(str(pid))
                    continue
                links.append(pid)
                taint |= Inspeximus._rec_sources(p)     # parent's own source + its inherited taint (transitive)
            if taint:
                rec["taint"] = sorted(taint)
            if unresolved:
                rec["derived_from_unresolved"] = unresolved
            if links:
                rec["links"] = links
                rec["derived_from"] = list(links)   # explicit lineage (distinct from corroboration links) so
                #                                     a derived memory's evidence grade can be capped at its
                #                                     weakest parent's -- trust taint propagates, not just source taint
            else:
                # Lineage was claimed and NONE of it resolved: the record is exactly the orphan case the
                # `derived=True` rule below covers, reached by a different door. Same treatment.
                rec["orphan"] = True
        # RECALL-WINDOW OBSERVATION (opt-in; see observe_recall in __init__). Everything above this line is
        # about CLAIMED lineage and has consequences. This is the other half of the same flow, recorded with
        # none: what the store SERVED just before this write. It is written even when `derived_from` was also
        # stamped -- the two are deliberately kept side by side, because the open question is precisely how
        # much of the claimed set the free observation already covers, and collapsing them would destroy the
        # comparison before it could be made.
        #
        # NOTHING IS DECIDED HERE. No age cutoff, no relevance filter, no classification of the write. Each
        # of those is a parameter the later analysis would be unable to reach past -- a window stamped only
        # when it is <60s old can never answer what the window captures at 300s. So the raw observation goes
        # down and every threshold stays in the analysis, where it can be varied.
        #
        # `w` is the discriminator that does the work a write-time classifier would have done badly. One
        # recall followed by a burst of twelve writes stamps w=0..11 on them, so an analysis can restrict to
        # w=0 (the write that actually followed the recall) without anyone having decided at write time which
        # writes were "real". This matters because the library's OWN maintenance writes -- revert(),
        # rederive(), consolidate()'s distillates, route() -- go through this same method and will carry a
        # window they are not derived from. They are noise, they are identifiable after the fact (by
        # revert_of / reaffirm / tags=['distilled']), and w=0 removes nearly all of them. Excluding them here
        # by inspecting the call stack was tried and rejected: `remember_decision` is a library-internal
        # caller too, and it is the main MCP write path, so "the caller is inside this module" classifies the
        # single most important write as maintenance.
        if self.observe_recall and self._last_recall_window and self._last_recall_at > 0.0:
            _win = list(self._last_recall_window)
            _obs = {"ids": _win[:self._recall_window_max], "at": self._last_recall_at,
                    "q": self._last_recall_q, "w": self._last_recall_writes}
            if len(_win) > self._recall_window_max:
                _obs["n"] = len(_win)       # true width, so the cap can never read as a narrow recall
            rec["recall_window"] = _obs
            self._last_recall_writes += 1
        # INTEGRITY-FLOOR FOR SELF-DECLARED TRANSFORMATION OUTPUTS (prompted by jacksonxly). A write the caller
        # DECLARES a transformation output (derived=True -- a summary / consolidation / LLM rewrite) that could
        # not name/resolve ANY parent is an ORPHAN: missing lineage is treated as unverified, so it earns NO
        # corroboration standing (fails the influence gate + graduation + distinct-source bar), defaulting to
        # scope-local context, and cannot quietly survive a retraction it should have inherited. Reversible:
        # re-remember with a resolvable derived_from. Primary observations (derived=False, default) are
        # unaffected. This is Biba-style integrity (1977: low-integrity input cannot raise an object's integrity)
        # / taint-tracking default-deny applied to the graduation+recall gate -- an APPLICATION, not a new idea.
        # HONEST LIMIT (do NOT call this "fail-closed against an adversary"): `derived` is CALLER-SET, so a
        # hostile or careless caller that OMITS it is treated as a primary observation and can still earn
        # standing -- it fails OPEN. It closes the orphaned-summary hole only for COOPERATIVE callers that
        # correctly self-declare derivation but lose lineage in an untrusted transform. A truly adversary-resistant
        # version would INFER derivation from the summarize/consolidate call site rather than trust the flag.
        if derived and not rec.get("derived_from"):
            rec["orphan"] = True
        # STRICT-PROVENANCE store policy (adversary-resistant): standing requires SHOWN provenance -- a source
        # (primary) or resolvable parents (derived). A write with NEITHER is an orphan by default, so an
        # undeclared summary cannot escape by simply omitting derived=True (the caller-elective hole). See the
        # strict_provenance note in __init__. rec['source'] is None when no source was passed.
        if self.strict_provenance and not rec.get("source") and not rec.get("derived_from"):
            rec["orphan"] = True
        # WHERE THE EVENT TIME CAME FROM, written ONLY when someone actually declared it. `declared` is
        # the positive claim and carries its own evidence; ABSENCE means nobody asserted an event time,
        # which is the fact an auditor needs and is true of a defaulted record and a pre-2.8.0 record
        # alike. Writing "ingest" on every record instead cost +9.8% serialized bytes on write_n1000 and
        # +10.5% on erase_k200_n2000 -- caught by the work-counter gate, which is exactly the trade this
        # store should refuse: a tenth of every user's disk to record the absence of a claim.
        # Named after `eventTimeSource` in joshuaswarren/remnic#1666.
        if valid_from is not None:
            rec["valid_from_source"] = "declared"
        if key is not None:
            rec["key"] = str(key)
        # IDENTITY-CONFIDENCE GATE ON SUPERSESSION (Fellegi-Sunter 1969 clerical-review zone / MDM match-merge
        # stewardship, ported to agent memory). A keyed write SUPERSEDES every active same-key record with no
        # threshold -- correct only if the (entity, field) IDENTITY the value attaches to is right. When that
        # identity was resolved fuzzily (an extractor / embedding match, not a caller-asserted key), a wrong
        # match silently promotes into the authoritative interval => a confident-but-WRONG ledger, harder to
        # catch than a set. `identity_confidence` in [0,1] gates the write: >= fork_below supersedes as before;
        # BELOW it the record is forked as a CANDIDATE (status='candidate', key stashed as candidate_key) that
        # does NOT supersede and is excluded from authoritative resolution until reconciled (promote_candidate /
        # discard_candidate). None (default) = caller asserts identity => supersede, byte-identical legacy.
        # Not a new idea (record linkage's "possible match -> review", 50+ yrs); the contribution is the port +
        # the measured prevention of confident-wrong writes vs an ungated LLM baseline.
        _is_candidate = (key is not None and identity_confidence is not None
                         and identity_confidence < self.fork_below)
        if _is_candidate:
            rec["status"] = "candidate"
            rec["candidate_key"] = str(key)
            rec.pop("key", None)                       # a candidate never occupies the authoritative key
            rec["identity_confidence"] = float(identity_confidence)
        elif identity_confidence is not None:
            rec["identity_confidence"] = float(identity_confidence)
        # OBJECT (OPT-IN): the asserted VALUE for keyed supersession + the echo guard. Value-preserving
        # paraphrases share it, so echo detection is object-identity (not similarity, which provably can't
        # separate same-value paraphrase from different-value correction). Falls back to normalized text.
        if object is not None:
            rec["object"] = str(object)
        # AUTHORIZED-REVERT GATE: a reaffirm write is the one path that restores a superseded value past the
        # echo guard, so when an authority is configured it needs the same capability as revert() — else the
        # content path could just call remember(reaffirm=True) directly. A bad/missing capability is loud.
        if reaffirm and (self.revert_authority is not None or self.revert_pubkey is not None)                 and capability is not _SANCTIONED and not self._revert_authorized(key, capability):
            raise PermissionError("reaffirm/revert requires a valid capability (revert authority is set)")
        # ORIGIN ATTESTATION (OPT-IN): bind this claim to a source's VERIFIED KEY. attestation is
        # (pubkey_hex, sig_hex) or {"pubkey":..., "sig":...}; the signature (from inspeximus.attest(text, sk,
        # source_doc)) must verify over the same claim+canonical-source message, else the write is REJECTED
        # (a forged attestation is loud, not silently dropped). On success the record carries attested_key,
        # which strict_corroboration counts distinct instances of — so manufactured independence costs a real
        # key. Verifying authorship, NOT truth: an attested source can still sign a false claim.
        if attestation is not None:
            if isinstance(attestation, dict):
                pubkey_hex, sig_hex = attestation.get("pubkey"), attestation.get("sig")
            else:
                pubkey_hex, sig_hex = attestation
            if not _HAVE_ED:
                raise RuntimeError("verifying an attestation needs the `cryptography` package (pip install cryptography)")
            src_doc = source.get("doc") if isinstance(source, dict) else (source if isinstance(source, str) else None)
            try:
                _Ed25519PK.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(
                    bytes.fromhex(sig_hex), _attest_message(text, src_doc))
            except Exception as e:
                raise ValueError("attestation signature does not verify for this claim/source") from e
            rec["attested_key"] = pubkey_hex
            # KEEP THE SIGNATURE. It used to be verified here and then discarded, so the record carried a
            # public key and no way to re-check it: an auditor reading the store later had to take the
            # write path's word for it, and a non-repudiable identity you cannot re-verify is not one.
            rec["attested_sig"] = sig_hex
        elif self.writer_key:
            # No explicit attestation: this store signs its own write with its writer identity, and it
            # knows which tenant it is bound to, so the binding goes INTO the signed message.
            #
            # The externally-attested branch above deliberately does NOT do this, and the asymmetry is
            # real rather than an oversight: an outside source signs "I authored this text, as this
            # source" before it ever reaches a store, and cannot sign a binding to a tenant it has never
            # heard of. So an externally-attested record stays movable between tenants with its
            # signature intact. That is a residual, it is not closable from this side, and the honest
            # thing is to write it down rather than let the field look uniformly protective.
            src_doc = source.get("doc") if isinstance(source, dict) else (source if isinstance(source, str) else None)
            rec["attested_sig"] = _Ed25519SK.from_private_bytes(
                bytes.fromhex(self.writer_key)).sign(_attest_message(text, src_doc, self.tenant)).hex()
            rec["attested_key"] = self.writer_pubkey
        if self.embed:
            try:
                rec["vec"] = list(self.embed(text))
            except Exception:
                rec["vec"] = None
        self._items.append(rec)
        # Cleared BEFORE the supersession pass, which is the only thing that can set it. A verdict left
        # over from an earlier call would be read as this call's, and a stale signal is worse than none --
        # the caller would test a field that answers about a different write.
        self.last_write = {"id": mid, "key": key, "status": "active", "blocked": False, "policy": None}
        _retired: list = []            # a keyless write retires nothing; bound here so the receipt
        if key is not None and not _is_candidate:   # below always has a value to commit
            _retired = self._supersede_by_key(rec, reaffirm=reaffirm)   # deterministic SRO supersession (no embedding, no threshold)
            #                                                  a candidate (low identity_confidence) never supersedes
        if self.capacity is not None:
            self._evict_to_capacity(protect_id=mid)          # bounded working set (opt-in) BEFORE persisting
        self._save(force=True)        # a new memory is real content - persist immediately, not throttled
        if self.receipts_enabled:
            self._emit_write_receipt(rec, retires=_retired)
        return mid

    def _evict_to_capacity(self, protect_id: str | None = None) -> None:
        """Keep the ACTIVE working set at <= self.capacity by HARD-EVICTING the lowest-value active
        memories, using the VERIFIED two-tier policy (Lab 29992a: value-protected + recency-aged is the
        one eviction rule that is universal across regimes). Protect the top protect_frac of capacity by
        RAW value (a rare-but-critical memory survives a flood); fill the remaining budget from the REST
        by EFFECTIVE (decay-weighted) value (so a stale high-raw memory can't crowd out a freshly-useful
        one, and pure junk floods age out). Eviction REMOVES (frees space) via forget(), unlike
        consolidate(keep=) which only DEMOTES — a bounded store must actually shrink. Superseded history
        is not counted or evicted here (it is low-overhead and preserves as_of); only the active set is
        bounded. No-op when active <= capacity, so remember() stays O(1) amortized until the cap bites.

        `protect_id` exempts the record whose own write triggered this pass. Without it remember() could
        evict the very record it had just stored and still return its id: at capacity=3, writing a fourth
        low-value memory returned an id that was not in the store and could not be recalled, and
        distill_and_remember() reported `captured: 3` with two records saved. A write that is silently undone
        by itself is never what the caller asked for; admit-then-evict-others is what a bounded cache does.
        The new record can still be evicted by a LATER write — it just cannot lose to its own."""
        # Guard bookkeeping is neither counted nor evictable -- see _GUARD_KEYSPACES. Excluded from the
        # population BEFORE the capacity comparison, so a store full of deprecations does not evict real
        # memories to make room for them either.
        active = [r for r in self.items if r.get("status") == "active" and not _is_guard_record(r)]
        if len(active) <= self.capacity:
            return
        keep_new = None
        if protect_id is not None:
            keep_new = next((r for r in active if r["id"] == protect_id), None)
            if keep_new is not None:
                active = [r for r in active if r["id"] != protect_id]   # budget the REST against capacity-1
        now = time.time()
        budget = self.capacity - (1 if keep_new is not None else 0)
        if len(active) <= budget:
            return
        active.sort(key=lambda r: -r["value"])               # by RAW value (protected tier order)
        kprot = int(self.protect_frac * budget) if self.two_tier_keep else 0
        protected, rest = active[:kprot], active[kprot:]
        # Among EQUAL effective value, keep the more recent -- so eviction discards the oldest, which is
        # what the decay model says and what this did before. It used to happen by accident: sub-second
        # differences in the decay factor made the older record worth microscopically less. Quantising the
        # decay age to whole seconds (necessary; an hours-long half-life has no sub-second meaning) removed
        # the accident and silently flipped the direction, evicting the NEWEST instead. Caught by
        # test_capacity_eviction_is_advisory_not_erasure_residue, which expected the oldest to go.
        _order = {rec.get("id"): i for i, rec in enumerate(self._items)}
        rest_keep = set(id(r) for r in
                        sorted(rest, key=lambda r: (-self._eviction_value(r, now),
                                                    -_order.get(r.get("id"), -1)))[:budget - kprot])
        evict_ids = [r["id"] for r in rest if id(r) not in rest_keep]
        if evict_ids:
            self.forget(evict_ids)                            # hard delete + link/toggle scrub

    # ── write receipts (OPT-IN: tamper-evident write history) ─────────────────
    @staticmethod
    def _write_commit(rec: dict, retires=()) -> dict:
        """What a receipt commits to for a stored memory: its id, a hash of its content-bearing fields, AND a hash
        of its ATTRIBUTION (canonical sources = own source + inherited derived_from taint). Binding attribution into
        the receipt is what makes a later RELABEL detectable: k, the influence budget, the influence gate and slash
        are all keyed on the source id, so a silent relabel (rewriting a record's source, or stripping its taint)
        voids all of them at once with no inner layer to appeal to — attribution is not a fourth axis, it is the
        floor the others stand on. With the sources committed, a relabel no longer matches the receipt, so
        verify_attribution() flags it. Honest limit: this makes a relabel tamper-EVIDENT, not attribution CORRECT —
        a wrong source asserted at write time (an attacker who controls the labeling channel, e.g. MINJA) is
        committed faithfully and uselessly; that oracle problem is untouched."""
        # SPLIT, since 1.68.0. text+key are IMMUTABLE in this store (append-only; raw text is never edited),
        # so they must bind for the life of the record. `mtype` is NOT: slash() revokes graduation by
        # rewriting it, which is why 1.67.0 added amendment receipts. With all three in ONE hash, "only the
        # latest receipt binds" had to forgive text as well — and that was a laundering path: tamper the
        # text out of band, call the public slash(), and verify_writes() went False -> True with the forged
        # text standing. Measured. Separate hashes let the amendment forgive exactly the field it rewrites.
        # `content_sha256` is kept so pre-1.68 verifiers still check something meaningful.
        return {"id": rec["id"],
                "content_sha256": _sha256_hex(_canon({"text": rec.get("text"), "key": rec.get("key"),
                                                      "mtype": rec.get("mtype")})),
                "immutable_sha256": _sha256_hex(_canon({"text": rec.get("text"), "key": rec.get("key")})),
                "mtype": rec.get("mtype"),
                # THE VALUE, since 1.82.0. `object` is what supersession, the echo guard, revert(),
                # check_conflict and _obj_sig all treat as authoritative -- it is the thing the store
                # SERVES -- and it was outside every commitment. Editing "90d" to "30d" on disk left
                # verify_writes() answering True and `audit-verify --store` printing "content checked,
                # PASS", because text and key were untouched and nothing hashed the value.
                #
                # A SEPARATE field, not `object` folded into immutable_sha256: changing that hash would
                # make every receipt ever written mismatch, so an upgrade would raise a tamper alarm on
                # every honest store. Old receipts simply lack this key and are checked on what they do
                # commit to -- and they cannot be stripped of it, because the receipt hash covers the whole
                # commit dict, so removing a field breaks the chain link instead.
                #
                # It binds for life like text+key: `object` is written once in remember() before the
                # receipt is emitted, and no call site rewrites it afterwards (supersession moves `status`,
                # revert() writes a new record).
                "value_sha256": _sha256_hex(_canon({"object": rec.get("object")})),
                # WHETHER THE STORE WILL SERVE IT, since 2.10.2. `status` and `confirmed_by` were in
                # no commitment at all, so the one edit that changes what a reader sees WITHOUT
                # touching a committed field had zero coverage. Measured 2026-08-15 with two controls:
                # flipping a `discarded` record (one a reviewer explicitly REJECTED) to `active` on
                # disk, and stamping confirmed_by='security-review' on it, left verify_writes,
                # verify_attribution, verify_bundle and bind_content ALL reporting clean while recall
                # served the rejected record. The controls fire as they must -- editing `object` is
                # caught by verify_writes, and appending a record is caught by verify_attribution and
                # verify_bundle (NOT by verify_writes, which walks the receipts and never looks at a
                # record no receipt names) -- so content-edit and insertion were both covered
                # somewhere and this was the single edit covered nowhere.
                #
                # A SEPARATE field for the same reason `value_sha256` is one: folding it into an
                # existing hash would make every receipt ever written mismatch, so an upgrade would
                # raise a tamper alarm on every honest store. Old receipts simply lack the key and are
                # checked on what they do commit to, and they cannot be stripped of it either --
                # the receipt hash covers the whole commit dict.
                #
                # UNLIKE value_sha256, this one is AMENDABLE: the class legitimately changes when a
                # verifier confirms or rejects. See _serving_class for why the class and not the raw
                # status, and _AMENDABLE for what that costs.
                "status_sha256": _sha256_hex(_canon({"serving": _serving_class(rec),
                                                     "confirmed_by": rec.get("confirmed_by")})),
                # THE IDS, not a hash of them: the verifier must CHECK each against the store, so it
                # has to read them. Surrogate ids are content-free by construction -- the same standard
                # the tombstone chain already meets. Inside the receipt hash, so trimming the list
                # breaks the chain link instead of shrinking the check.
                "retires": sorted(retires or ()),
                # THE STATUS AT BIRTH, write-once and so bindable for life with no churn. It is what
                # separates "retired ON ARRIVAL" -- the echo guard, the objectless guard and the
                # back-fill rule all do that, and the record's own receipt is then the whole
                # explanation -- from "this was live and something retired it later", which has to be
                # accounted for. Without it the concealment check cannot tell a legitimately
                # stillborn record from a hidden one.
                "born_status": rec.get("status") or "active",
                # WHEN THE FACT BECAME TRUE, since 2.10.2. `valid_from` decides every `as_of()`
                # answer -- "what did the agent believe when it acted, provably" -- and it was in no
                # commitment at all. Measured 2026-08-15: edit it on disk and verify_writes,
                # verify_attribution, verify_bundle and a client-held witness taken BEFORE the edit
                # all report clean. The control is what makes that specific: `state_digest` DOES
                # cover the sibling field `ts`, so the witness is wired to timestamps in general and
                # simply not to the two that answer the temporal question.
                #
                # Write-once, so it binds for life like text+key and `object`: set in remember()
                # before the receipt is emitted, rewritten by no call site. `valid_from_source` rides
                # along -- it is the provenance OF that time, and a claim whose provenance can be
                # rewritten silently is not evidence.
                #
                # `invalidated_at` is deliberately NOT here: supersession writes it mechanically, so
                # committing it would demand an amendment per retirement -- the churn argument that
                # shaped `status_sha256`. It is handled by RECOMPUTING it in as_of() instead.
                "time_sha256": _sha256_hex(_canon({"valid_from": rec.get("valid_from"),
                                                   "valid_from_source": rec.get("valid_from_source")})),
                "attrib_sha256": _sha256_hex(_canon(sorted(Inspeximus._rec_sources(rec))))}

    def _declare_retired(self, rec: dict, reason: str) -> None:
        """Record in the chain that THIS record has been retired, and why.

        `retires` (on the retiring write) covers keyed supersession, where a newer record replaces an
        older one. The other retirement paths have no "newer" record to hang it on -- a keep-budget
        demotion, a lineage retraction, a merge resolution -- and a state toggle happens outside
        remember() entirely. Undeclared, all of them are indistinguishable from CONCEALMENT: an
        attacker marking the CURRENT record `superseded` on disk so the store serves an older value,
        or nothing at all. Measured 2026-08-15: recall returned [], no active record for the key, and
        verify_writes, verify_bundle and bind_content all reported clean.

        The cost, stated rather than discovered: one receipt per demoted record, and a keep-budget
        pass can demote many at once. That is a deliberate operator call with an explicit budget, so
        the churn is bounded and attributable -- unlike committing the raw `status`, which would put
        an amendment on every mechanical supersession forever. Silent when receipts are off, like
        every other chain write.
        """
        if self.receipts_enabled:
            self._emit_write_receipt(rec, amends=("status_sha256",), reason=reason)

    def _emit_write_receipt(self, rec: dict, amends: tuple = (), reason: str = "",
                            retires=()) -> dict:
        """`amends` names the committed fields this receipt legitimately rewrites (slash/restore -> mtype).
        It is DECLARED, not inferred, and verification forgives an earlier receipt for exactly the declared
        fields and nothing else. Inferring it — "the latest receipt wins" — is what let a public slash()
        launder a forged text past verify_writes() in 1.67.0, and the same shape silently forgave a RELABEL
        (attrib_sha256), which is the one thing committing attribution exists to catch."""
        prev = self._receipts[-1]["hash"] if self._receipts else _GENESIS
        r = {"seq": len(self._receipts), "ts": rec.get("ts"), "memory_id": rec["id"],
             "commit": self._write_commit(rec, retires), "prev": prev}
        if amends:
            bad = set(amends) - _AMENDABLE
            if bad:
                raise ValueError(f"refusing to emit a receipt amending {sorted(bad)}: only "
                                 f"{sorted(_AMENDABLE)} may be forgiven. Widening this is a "
                                 f"deliberate change to what the chain guarantees, not a parameter.")
            r["amends"] = sorted(amends)
            # WHY a record changed is as auditable as WHAT changed (yun520-1, hermes-agent#34352).
            # `amends` says which committed field a receipt rewrites; without a reason the trail cannot
            # tell a correction from a quiet rewrite. An UNSTATED reason is recorded as such rather than
            # omitted: a missing key is indistinguishable from a caller that had nothing to say, and an
            # optional field nobody is required to fill is the adoption defect, not the feature.
            r["amend_reason"] = (str(reason).strip() or "unstated")[:200]
        r["hash"] = _sha256_hex(_canon(Inspeximus._chain_core(r, "write")))
        if self._receipt_signer is not None:
            # WRITE-AUTHORITY BOUNDARY. The signing key lives OUTSIDE this process (KMS, HSM, a signing
            # sidecar), so the store can ASK for a signature but can never mint one. That is the property
            # a hash chain alone does not give you: with the key in-process, anyone who can write the
            # store file can also rewrite the chain and re-sign it, and the log degrades to a checksum of
            # whatever the attacker last wrote.
            #
            # HONEST SCOPE, because this is exactly where audit logging gets oversold: it stops an
            # attacker who has FILE access only. It does NOT stop one who can call this API in-process --
            # they ask the signer just as the application does, and the signer has no way to know the
            # difference. Separating those two is a deployment property (who may run the process), not
            # something a library can assert. Prior art for the class: Schneier & Kelsey, USENIX Security
            # 1998, on why post-compromise entries are attacker-chosen by construction.
            try:
                sig = self._receipt_signer(r["hash"])
            except Exception as e:
                # A signer that is unreachable must not silently produce an UNSIGNED receipt that later
                # verifies as "no signature required" -- that is the failure open this boundary exists to
                # prevent. Refuse the write instead.
                raise RuntimeError(f"receipt signer failed ({type(e).__name__}: {e}); refusing to append "
                                   f"an unsigned receipt while a signer is configured") from e
            if not sig:
                raise RuntimeError("receipt signer returned no signature; refusing to append an unsigned "
                                   "receipt while a signer is configured")
            r["sig"] = sig
            if self.receipt_pubkey:
                r["pubkey"] = self.receipt_pubkey
        elif self._receipt_sk and _HAVE_ED:
            sk = _Ed25519SK.from_private_bytes(bytes.fromhex(self._receipt_sk))
            r["pubkey"] = self.receipt_pubkey
            r["sig"] = sk.sign(bytes.fromhex(r["hash"])).hex()
        self._receipts.append(r)
        if self._receipts_path:
            try:
                Inspeximus._atomic_write(self._receipts_path,
                                         json.dumps(self._receipts, indent=2, ensure_ascii=False))
            except Exception as e:
                # The receipt chain IS the evidence. Losing it silently was worse than losing a record:
                # measured, 4 receipts in memory, verify_writes() -> (True, []), and ZERO on reload — the
                # store certified an integrity it could no longer demonstrate.
                self._sidecar_errors["receipts"] = f"{self._receipts_path}: {type(e).__name__}: {e}"
        return r

    def admissibility_preconditions(self) -> dict:
        """Is this store in a state where an applicability question can be ANSWERED at all?

        `evaluate_applicability` asks whether a record is admissible now. This asks the question
        underneath it, and the two are different: a store can answer the first one confidently while
        the machinery the answer rests on has quietly stopped working.

        THE LAYER IS @Stratogain's, named on safal207/Causal-Memory-Layer#289 (2026-08-17). We had
        reported collector-silence and identifier-mismatch as two separate cases; the generalisation
        is theirs:

            "Liveness is a fact about the store rather than the record. Case 4 is the same sentence
             with a different noun: key agreement is a fact about the store rather than the record."

        and the property that makes both dangerous in the same way:

            "a verdict is produced, so nothing upstream looks broken."

        Their two invariants are 1 and 2 below. The third is the same shape found in our own store
        while implementing theirs, which is what suggests the shape generalises: **a mechanism that
        is ENABLED and PRODUCING NOTHING**. Each is store-scoped, none needs a new status anywhere,
        and none is expressible on a single record -- which is the evidence that the boundary is real.

        A precondition that cannot apply is reported as `applicable: False`, never as holding. A
        store with no keys has not passed the key-agreement check; it has not taken it.

        Returns {"ok", "preconditions": [...], "limits": [...]}.
        """
        rows = self._tenant_rows()
        out = []

        # ── 1 · KEY AGREEMENT. The key a store writes under must be the key it queries by. ────────
        # @Stratogain's case: a hook wrote `session_id[:8]` and the lookup compared the full id, so
        # a session could not find its OWN observations -- and a verdict came back every time,
        # computed from a write-time hash instead. Checked from the outside: take each key the store
        # holds and ask the read path for it.
        keys = [r.get("key") for r in rows if r.get("key")]
        unreachable = []
        for k in dict.fromkeys(keys):
            try:
                if not self.history(k):
                    unreachable.append(k)
            except Exception:                                              # noqa: BLE001
                unreachable.append(k)
        out.append({
            "id": "key_agreement", "asserts": "every key the store holds resolves through the read path",
            "applicable": bool(keys), "holds": bool(keys) and not unreachable,
            "checked": len(set(keys)), "failed": unreachable[:5],
            "why_it_matters": "a key that does not resolve produces a fallback answer, not an error",
        })

        # ── 2 · OBSERVATION CHANNEL ALIVE. reads > 0 implies observations > 0. ───────────────────
        # Translated to this store's shape: a record carries a LOCATOR when the writer said where it
        # came from, and an `observed_sha256` when something actually read those bytes. Locators
        # accumulating with no observations means the channel that supplies them is producing
        # nothing while writing continues -- and coverage does not move, because new records get no
        # observation either.
        with_locator = [r for r in rows if (r.get("source") or {}).get("doc")]
        with_observation = [r for r in with_locator if (r.get("source") or {}).get("observed_sha256")]
        out.append({
            "id": "observation_channel_alive",
            "asserts": "if records carry locators, some carry a read-time observation",
            "applicable": bool(with_locator),
            "holds": bool(with_locator) and bool(with_observation),
            "locators": len(with_locator), "observations": len(with_observation),
            "why_it_matters": "with the channel dead, honest captures degrade to write-time hashes "
                              "and every call still returns a result",
        })

        # ── 3 · RECEIPTS COVER THE RECORDS. A mechanism present and producing nothing. ───────────
        # WHAT WE ACTUALLY FOUND on our own 450-record decision store, after a red-team pass
        # corrected the first version of this claim: the store was never written with receipts at
        # all -- no sidecar, nothing to cover the records. The first report said "receipts enabled,
        # chain empty", which was our own probe's constructor argument and not a property of the
        # store. The smaller surviving finding is the one worth keeping: the integrity feature this
        # library sells has never been switched on for the store its own author writes decisions to.
        # THIS ASKS THE STORE, NOT THE CALLER, and the first version did the opposite.
        #
        # `receipts_enabled` is a CONSTRUCTOR flag -- `bool(receipts or receipt_key or
        # receipt_signer)` -- never inferred from the file. So opening a never-receipted store with
        # `receipts=True` set it True, found an empty chain, and reported a failure that the caller
        # had just created. Measured on our own decision store: the same file returns FAILS / n/a /
        # n/a depending only on the argument passed to the constructor. A red-team pass caught it
        # before it was published, and it is the third time in one day that an artefact of how
        # something was opened was about to be reported as a defect in the thing.
        #
        # The store's own property is whether a receipt sidecar EXISTS. A store written without
        # receipts has not failed this precondition -- it never took it, whoever opens it and
        # however. A store that HAS a chain and left it empty or short has.
        side = getattr(self, "_receipts_path", None)
        has_chain_file = bool(side) and Path(str(side)).exists()
        chain = len(getattr(self, "_receipts", []) or [])
        out.append({
            "id": "receipt_chain_covers_records",
            "asserts": "if this store was WRITTEN with receipts, the chain covers its records",
            "applicable": has_chain_file and bool(rows),
            "holds": (not has_chain_file) or (not rows) or chain > 0,
            "records": len(rows), "chain_entries": chain,
            "chain_file_on_disk": has_chain_file,
            "opened_with_receipts": bool(getattr(self, "receipts_enabled", False)),
            "why_it_matters": "a chain that exists and is empty verifies nothing while reading as "
                              "enabled; a store that never had one has a different problem, and "
                              "conflating them lets the caller's own flag manufacture a finding",
        })

        applicable = [p for p in out if p["applicable"]]
        return {
            "ok": all(p["holds"] for p in applicable) and bool(applicable),
            "preconditions": out,
            "limits": [
                "A precondition reported `applicable: False` has NOT passed -- it did not run. "
                "Three of these can be inapplicable on a legitimate store and that is not a clean bill.",
                "These are store-level and say nothing about whether any individual record is "
                "admissible; that is what evaluate_applicability is for, and it is the layer above.",
                "The layer and invariants 1 and 2 are @Stratogain's "
                "(safal207/Causal-Memory-Layer#289); invariant 3 is the same shape found here.",
            ],
        }

    def audit_the_audits(self, probes: list | None = None) -> dict:
        """CAN THIS LIBRARY'S OWN VERIFICATION SURFACES ACTUALLY FAIL -- on THIS store?

        Every `verify_*`, `check_*` and `*_audit` here answers a question about your data. None of
        them answers the question one level up: *would this check have noticed if the thing it
        guards against had actually happened?* A check that cannot fail on your store is not
        protecting you; it is producing a reassuring string.

        This corrupts a COPY of your store in a way each surface claims to detect, and reports
        whether the surface noticed. Three outcomes per probe, and the third is the one to read:

          NOTICED         the surface was clean before and reported a problem after. Working.
          MISSED          clean before, clean after. The corruption happened and it said nothing.
          SUMMARY_HIDES_DETAIL
                          the top-level boolean stayed clean while the report itself said otherwise.
                          Not a lie and not a catch: a caller reading `ok` is reassured, a caller
                          reading the report is not, and monitoring reads booleans.
          CONTROL_FAILED  the surface was ALREADY reporting a problem on the clean copy, so its
                          reaction to the corruption proves nothing. Not scored -- a probe whose
                          control fails has measured its own setup, not the surface.

        WHY THIS EXISTS, and every entry below is a defect we shipped or nearly shipped:

          * `slash(scope='source')` was our accountability lever and its default scope resolved on
            a field no writer ever set: 0.000% coverage across 261,673 records, returning ok on
            every call, for months.
          * A probe of ours scored 14 of 14 over a store that had received nothing, because each
            check carried an escape on its own precondition.
          * A coverage gate could not see the writer's own store. An absence test was satisfied by
            nothing at all. A guard with half recall reported safe.

        The pattern is one thing, ten times: a check returning PASS for a reason unrelated to the
        property it names. That is not a bug in any one check -- it is a property of checks, and it
        needs a check of its own.

        PRIOR ART, because "prove your checks can fail" is old and this is a recombination rather
        than an invention. The parent is MUTATION TESTING (Lipton 1971; DeMillo, Lipton & Sayward
        1978): inject a fault, see whether the suite notices, and require a clean baseline first --
        which is `CONTROL_FAILED` at coarser grain. Closest living siblings are `promtool test rules`
        and Semgrep's `ruleid:`/`ok:` fixtures, both of which pair every rule with a must-fire and a
        must-not-fire case. What we did not find in any of them, having checked promtool, Semgrep,
        amcheck and Netflix's ChAP: corrupting a copy of the CALLER'S OWN LIVE DATA rather than a
        synthetic fixture, and the `SUMMARY_HIDES_DETAIL` / `CONTROL_FAILED` split. Those two are the
        contribution; the principle is not ours to claim.

        SAFETY. Everything runs against a temporary COPY. The live store is opened read-only and
        never written, because a harness that corrupts the thing it is auditing is the failure mode
        with the worst blast radius, and we have watched two of our own tools fight over one file.

        Returns {"probes": [...], "noticed": n, "missed": [...], "control_failed": [...],
        "surfaces_covered": n, "surfaces_available": n, "limits": [...]}.
        """
        import copy as _copy
        import json as _json
        import shutil as _shutil
        import tempfile as _tempfile

        cls = type(self)
        available = sorted(
            n for n in dir(cls)
            if not n.startswith("_") and callable(getattr(cls, n, None))
            and (n.startswith(("verify", "check", "detect"))
                 or n.endswith(("audit", "_report", "certificate", "integrity", "coherence"))))

        def _writes(store):                      # -> (clean?, detail)
            ok, problems = store.verify_writes()
            return bool(ok), list(problems)[:2]

        def _sources_ok(store):
            r = store.check_sources()
            return bool(r.get("ok")), r

        def _sources_drift(store):
            r = store.check_sources()
            c = r.get("counts") or {}
            return not (c.get("DRIFTED") or c.get("ORPHANED")), {"counts": c}

        def _coherent(store):
            r = store.index_coherence()
            return bool(r.get("coherent", True)), {"missing_vecs": r.get("missing_vecs")}

        # ── PRECONDITIONS: does THIS store have the shape the surface needs to be exercised at all?
        # Without these, a store that simply lacks receipts reports its receipt checks as
        # CONTROL_FAILED -- which reads as "your data is unhappy" when the truth is "this question
        # cannot be asked here". Measured on our own 451-record decision store: all three of its
        # CONTROL_FAILEDs were this, and none of them was a health finding.
        def _needs_receipt_chain(store):
            if not getattr(store, "receipts_enabled", False):
                return False, ("this store was not opened with receipts, so no write chain exists to "
                               "tamper with")
            if not getattr(store, "_receipts", None):
                return False, ("receipts are enabled but the chain is EMPTY, so nothing is sealed to "
                               "compare a tampered record against")
            return True, ""

        def _needs_bindable_source(store):
            """Did `check_sources` actually CHECK anything -- not merely find something bindable.

            The first version of this asked `coverage.bindable`, which counts records carrying a
            locator: 107 of them on our own store, so the precondition passed and the probe went
            straight to CONTROL_FAILED anyway, because none of the 107 carried a re-checkable
            FINGERPRINT and the surface had verified nothing. A criterion narrower than the property
            it stands for reports the wrong thing confidently, which is the defect this whole method
            exists to find -- committed here, in the fix for it.
            """
            r = store.check_sources()
            # `checked` is the whole criterion, and it must not be widened "for safety". A real health
            # finding on the caller's data must NOT be laundered into a coverage gap -- but DRIFTED and
            # ORPHANED are only ever counted for records the surface actually checked, so `checked > 0`
            # already subsumes them and an explicit `if DRIFTED or ORPHANED` early-return here could
            # not fire on any store that can be constructed (measured: both arrive with checked=1).
            # It was written, and then deleted, because a guard that cannot fire is the exact thing
            # this method exists to find and keeping it would have read as protection.
            if not (r.get("checked") or 0):
                return False, ("no record here carries a re-checkable source fingerprint, so "
                               "`check_sources` verified nothing before any corruption -- stripping "
                               "sources removes nothing it was reading")
            return True, ""

        def _needs_embedder(store):
            # Truthiness, not identity -- the whole reason `self.embed` is coerced in __init__.
            if not getattr(store, "embed", None):
                return False, ("no embedder is configured, so there is no derived index that could fall "
                               "behind the store")
            if not getattr(store, "_persist_vectors", False):
                return False, ("vectors are a RAM-only cache on this store (persist_vectors=False), so "
                               "there is no persisted index in the file to fall behind it")
            return True, ""

        # (name, surface, reader, corrupt, what it should catch, precondition, fixture shape)
        catalogue = [
            ("value_tampered_after_write", "verify_writes", _writes,
             lambda rows: ([dict(r, text="TAMPERED BY audit_the_audits") for r in rows[:1]] + rows[1:])
             if rows else None,
             "a record's text edited in the file after its write receipt was sealed",
             _needs_receipt_chain, {"receipts": True}),
            ("key_tampered_after_write", "verify_writes", _writes,
             lambda rows: ([dict(r, key=(r.get("key") or "k") + "-TAMPERED") for r in rows[:1]]
                           + rows[1:]) if rows else None,
             "a record's key edited in the file after its write receipt was sealed",
             _needs_receipt_chain, {"receipts": True}),
            ("source_bytes_moved", "check_sources", _sources_drift, "MUTATE_SOURCE",
             "the bytes behind a recorded locator changed after capture", None, None),
            ("source_deleted", "check_sources", _sources_drift, "DELETE_SOURCE",
             "the locator no longer resolves at all", None, None),
            ("every_source_stripped", "check_sources", _sources_ok,
             lambda rows: [{k: v for k, v in r.items() if k != "source"} for r in rows] or None,
             "no record carries a source at all -- the 261,673-record instance",
             _needs_bindable_source, {"source": True}),
            ("index_behind_store", "index_coherence", _coherent,
             lambda rows: [{k: v for k, v in r.items() if k != "vec"} for r in rows] or None,
             "every persisted vector dropped while an embedder is configured -- semantic recall "
             "ranking against an index that no longer matches the store",
             _needs_embedder, {"embedder": True}),
        ]

        def _stub_embed(text):
            """A deterministic, offline stand-in for an embedder.

            Only ever used on a FIXTURE, and only to make `embed is not None` true so that
            `index_coherence` has an index to be behind. It is not the caller's recipe and no probe
            here claims anything about their embedding quality -- what is under test is whether the
            surface reacts when the index stops matching the store.
            """
            import hashlib as _h
            return [b / 255.0 for b in _h.sha256(text.encode("utf-8")).digest()[:16]]

        def _open_copy(path, shape=None):
            """Open the copy the way the LIVE store was opened, unless a fixture asks otherwise.

            The first version re-serialised `self.items` into a bare file and opened it with
            defaults. Four of five probes came back CONTROL_FAILED, correctly: without
            `receipts=True` the copy reads as a receipts-DISABLED store and `verify_writes` answers
            "there is nothing to verify -- which is not the same as verified" before any corruption
            is applied. A copy that does not inherit its original's construction is a different
            store, and measuring it tells you nothing about the one you have.

            `embed` is deliberately NOT inherited: the caller's embedder may be a network call, and
            an audit that fires one per record is a stall, not an audit. That has a consequence worth
            stating rather than discovering -- with no embedder, `index_coherence` sees no index and
            cannot fall behind, so a naive probe of it would report the SURFACE as blind when the
            blindness belongs to this harness. That is why the coherence probe declares
            `_needs_embedder` and runs on a fixture carrying `_stub_embed` instead.
            """
            shape = shape or {}
            kw = {}
            if shape.get("embedder"):
                # persist_vectors, or the index lives in RAM only and "the index fell behind the
                # file" is not a state this store can be in -- the probe would corrupt nothing.
                kw = {"embed": _stub_embed, "persist_vectors": True, "embed_id": "audit-stub-v1"}
            return type(self)(
                path=path,
                receipts=bool(shape["receipts"]) if "receipts" in shape
                else bool(getattr(self, "receipts_enabled", False)),
                **({"embed": None} if not kw else kw))

        def _build_fixture(work, shape):
            """The smallest store in which a surface's precondition HOLDS.

            The point is to separate two things `CONTROL_FAILED` used to say at once: *your store is
            already unhappy* and *your store cannot ask this question*. Only the first is a finding
            about your data. For the second, the question is still worth answering -- just somewhere
            the precondition is true -- and the answer there is a fact about the LIBRARY: a surface
            that stays clean on a store built to trigger it is blind, and that is the loudest thing
            this method can report.
            """
            import hashlib as _h
            path = os.path.join(work, "fixture.json")
            fx = _open_copy(path, shape)
            src = None
            if shape.get("source"):
                src = os.path.join(work, "fixture_source.txt")
                with open(src, "wb") as fh:
                    fh.write(b"the bytes the fixture record was derived from")
                fx.remember("a fixture record whose source this probe controls",
                            key="__fixture_source__", object="fixture",
                            source={"doc": src, "observed_sha256": _h.sha256(
                                b"the bytes the fixture record was derived from").hexdigest()})
            fx.remember("a fixture record", key="__fixture__", object="fixture")
            fx.remember("a second fixture record", key="__fixture2__", object="fixture")
            fx.flush()
            return path, src

        live_rows = _json.loads(_json.dumps(self.items, default=str))
        results, limits = [], []
        if not live_rows:
            limits.append("this store is EMPTY, so every probe below is unreachable and the run "
                          "reports nothing rather than a clean bill -- which is itself the first "
                          "defect in the list above.")
            return {"probes": [], "noticed": 0, "missed": [], "summary_hides_detail": [],
                    "control_failed": [], "not_reachable_here": [], "blind_even_on_a_fixture": [],
                    "surfaces": {"available": len(available), "probed": 0, "unprobed": available,
                                 "demonstrated_on_your_store": [],
                                 "working_but_unreachable_here": [],
                                 "blind_even_on_a_fixture": [], "unanswerable_here": []},
                    "surfaces_covered": [], "surfaces_available": len(available), "limits": limits}

        for entry in (probes or catalogue):
            name, surface, read, mutate, catches = entry[:5]
            needs = entry[5] if len(entry) > 5 else None
            fixture_shape = entry[6] if len(entry) > 6 else None

            work = _tempfile.mkdtemp()
            path = os.path.join(work, "copy.json")
            docs = os.path.join(work, "docs")
            os.makedirs(docs, exist_ok=True)

            # File-level copy, sidecars included: the receipt chain lives in `<store>.receipts.json`
            # and a store without it is a different store.
            import glob as _glob
            for f in _glob.glob(str(self.path) + "*"):
                try:
                    _shutil.copy(f, os.path.join(work, os.path.basename(f)))
                except Exception:                                          # noqa: BLE001
                    pass
            path = os.path.join(work, os.path.basename(str(self.path)))
            rows = _copy.deepcopy(live_rows)

            # ── TIER. Ask the precondition BEFORE corrupting anything, on the caller's own copy.
            # A precondition that does not hold is not a health finding and must not be dressed as
            # one; it means the question belongs on a store where it CAN be asked, and the answer
            # there is about the library rather than about this store.
            tier, tier_why, shape, open_fn = "your store", "", None, _open_copy
            if needs is not None:
                try:
                    reachable, why_not = needs(_open_copy(path))
                except Exception as ex:                                     # noqa: BLE001
                    reachable, why_not = False, f"the precondition raised {type(ex).__name__}"
                if not reachable:
                    if not fixture_shape:
                        results.append({
                            "probe": name, "surface": surface, "catches": catches,
                            "outcome": "NOT_REACHABLE_HERE", "why": why_not,
                            "on_a_fixture": None,
                            "read_this_as": "a gap in what YOUR store can demonstrate, not a "
                                            "defect in the surface and not a clean bill"})
                        continue
                    tier, tier_why, shape = "fixture", why_not, fixture_shape
                    fwork = _tempfile.mkdtemp()
                    try:
                        path, fsrc = _build_fixture(fwork, shape)
                    except Exception as ex:                                # noqa: BLE001
                        results.append({
                            "probe": name, "surface": surface, "outcome": "CONTROL_FAILED",
                            "why": f"the fixture for this surface could not be built: "
                                   f"{type(ex).__name__}: {ex}"})
                        continue
                    rows = _json.loads(open(path, encoding="utf-8").read())
                    open_fn = lambda p, _s=shape: _open_copy(p, _s)         # noqa: E731
            # Source-bearing probes need a source that EXISTS locally, so they build one rather than
            # depending on the caller's filesystem: a probe that silently tests nothing because the
            # locator was a URL is the defect, not a limitation.
            doc = os.path.join(docs, "probe_source.txt")
            with open(doc, "wb") as fh:
                fh.write(b"the bytes this record was derived from")
            needs_source = mutate in ("MUTATE_SOURCE", "DELETE_SOURCE")
            if tier == "fixture":
                # `_build_fixture` already wrote this store through the API, sidecars and all.
                # Re-serialising `rows` over it would hand the probe a file the library did not
                # write -- the defect the seed branch below exists to avoid.
                pass
            elif not needs_source:
                with open(path, "w", encoding="utf-8") as fh:
                    _json.dump(rows, fh, default=str)
            else:
                # WRITE the source-bearing record THROUGH THE API rather than hand-building a row.
                # The first version injected a `source` dict into a copied record and check_sources
                # reported DRIFTED on the untouched copy: a record written by the library carries
                # commitments a hand-assembled dict does not. Both source probes came back
                # CONTROL_FAILED and were right to. Constructing fixtures by hand instead of through
                # the product is the same defect this method exists to find, one layer out.
                import hashlib as _h
                seed = _open_copy(path)
                seed.remember(
                    "a record whose source this probe controls",
                    key="__audit_the_audits_probe__", object="probe",
                    source={"doc": doc,
                            "observed_sha256": _h.sha256(
                                b"the bytes this record was derived from").hexdigest()})
                seed.flush()

            where = "the clean copy" if tier == "your store" else "the clean FIXTURE"
            try:
                before_clean, before_detail = read(open_fn(path))
            except Exception as ex:                                        # noqa: BLE001
                results.append({"probe": name, "surface": surface, "outcome": "CONTROL_FAILED",
                                "tier": tier,
                                "why": f"the surface raised on {where}: {type(ex).__name__}"})
                continue
            if not before_clean:
                results.append({"probe": name, "surface": surface, "outcome": "CONTROL_FAILED",
                                "tier": tier,
                                "why": f"already reported a problem on {where}, BEFORE the "
                                       f"corruption, so its reaction proves nothing",
                                "detail": before_detail})
                continue

            if mutate == "MUTATE_SOURCE":
                with open(doc, "wb") as fh:
                    fh.write(b"DIFFERENT BYTES")
            elif mutate == "DELETE_SOURCE":
                os.unlink(doc)
            else:
                changed = mutate(rows)
                if not changed:
                    results.append({"probe": name, "surface": surface, "outcome": "CONTROL_FAILED",
                                    "why": "the corruption did not change the store, so it tests "
                                           "nothing"})
                    continue
                with open(path, "w", encoding="utf-8") as fh:
                    _json.dump(changed, fh, default=str)

            try:
                after_clean, after_detail = read(open_fn(path))
            except Exception:                                              # noqa: BLE001
                after_clean, after_detail = False, {"raised": True}
            # THREE outcomes, not two, and the third came from running this against ourselves.
            # `check_sources` on a store whose every source was stripped returns ok=True -- for a
            # reasoned purpose: a store of pure DECISIONS has nothing bindable by construction and
            # calling it False forever would be wrong. But the same report also carries
            # `problem: "... so this verified NOTHING"`. The surface is honest in its detail and
            # reassuring in its boolean, and monitoring reads booleans.
            #
            # Scoring that as MISSED would have been my reader trusting a summary over the contents
            # it summarises -- the exact defect this method exists to find. So it gets its own name.
            hidden = None
            if after_clean and isinstance(after_detail, dict):
                if after_detail.get("problem"):
                    hidden = str(after_detail["problem"])[:200]
                elif after_detail.get("limits"):
                    hidden = str(after_detail["limits"])[:200]
            verdict = ("NOTICED" if not after_clean
                       else "SUMMARY_HIDES_DETAIL" if hidden else "MISSED")
            if tier == "your store":
                row = {"probe": name, "surface": surface, "catches": catches, "outcome": verdict,
                       "tier": tier, "clean_before": True, "clean_after": bool(after_clean)}
            else:
                # The surface could not be exercised on the caller's data, so the OUTCOME is about
                # their store (a coverage gap) and the fixture verdict is about the LIBRARY. Keeping
                # them in one field would have merged a question nobody could ask with an answer
                # nobody checked.
                row = {"probe": name, "surface": surface, "catches": catches,
                       "outcome": "NOT_REACHABLE_HERE", "tier": tier, "why": tier_why,
                       "on_a_fixture": verdict,
                       "read_this_as": (
                           "the surface WORKS -- your store simply cannot demonstrate it"
                           if verdict == "NOTICED" else
                           "the surface stayed clean on a store built to trigger it: a defect in "
                           "THIS LIBRARY, not in your data"
                           if verdict == "MISSED" else
                           "on a store built to trigger it the boolean stayed clean while the "
                           "report did not; monitoring reads booleans")}
            if hidden:
                row["the_boolean_said_clean_but"] = hidden
            elif verdict == "MISSED":
                row["detail"] = after_detail
            results.append(row)

        def _sel(*outcomes):
            return [r for r in results if r["outcome"] in outcomes]

        missed = _sel("MISSED")
        hides = _sel("SUMMARY_HIDES_DETAIL")
        cfail = _sel("CONTROL_FAILED")
        unreach = _sel("NOT_REACHABLE_HERE")
        # THE LOUDEST BUCKET: clean on a store built to trigger it. Not a fact about the caller.
        blind = [r for r in unreach if r.get("on_a_fixture") == "MISSED"]
        covered = sorted({r["surface"] for r in results if r["outcome"] == "NOTICED"})
        works_on_fixture = sorted({r["surface"] for r in unreach
                                   if r.get("on_a_fixture") == "NOTICED"})
        probed = sorted({r["surface"] for r in results})
        unprobed = [s for s in available if s not in set(probed)]
        # ONE place the coverage is stated, because the previous shape returned three numbers that
        # all read like coverage -- a NOTICED-only list, a probed count buried in a limits string,
        # and the total -- and a handoff written from it reported the PROBE count as a surface count.
        surfaces = {
            "available": len(available),
            "probed": len(probed),
            "unprobed": unprobed,
            "demonstrated_on_your_store": covered,
            "working_but_unreachable_here": works_on_fixture,
            "blind_even_on_a_fixture": sorted({r["surface"] for r in blind}),
            "unanswerable_here": sorted({r["surface"] for r in cfail}),
        }
        limits += [
            "%d of this class's %d verification surfaces have a probe here; the other %d are "
            "UNTESTED, which is not the same as working -- they are named in "
            "surfaces['unprobed']." % (len(probed), len(available), len(unprobed)),
            "A NOTICED verdict says the surface reacted to THIS corruption on THIS store. It is not "
            "a claim that the surface is correct, nor that it would catch a different corruption.",
            "NOT_REACHABLE_HERE is neither a pass nor a failure of your store: the surface's "
            "precondition does not hold on your data, so it was re-probed on a minimal fixture and "
            "`on_a_fixture` carries that answer. A fixture verdict of MISSED is a LIBRARY defect.",
            "The coherence fixture uses a deterministic offline stand-in embedder, so it measures "
            "whether the surface reacts to an index that stopped matching the store -- never "
            "anything about your own embedding recipe.",
            "Everything ran on a temporary copy or a fixture; the live store was not written.",
        ]
        return {"probes": results, "noticed": sum(1 for r in results if r["outcome"] == "NOTICED"),
                "missed": missed, "summary_hides_detail": hides,
                "control_failed": cfail, "not_reachable_here": unreach,
                "blind_even_on_a_fixture": blind,
                "surfaces": surfaces, "surfaces_covered": covered,
                "surfaces_available": len(available), "limits": limits}

    def identifier_contract(self) -> dict:
        """What are this store's identifiers, and which folds over them would LOSE information?

        THE QUESTION THIS ANSWERS is the one a store outlives its writer to face. Someone holds the
        file six months on, the version that wrote it is gone and the maintainer may be too. They
        cannot run anyone's conformance suite; whatever deformation happened is already in the
        bytes. What they need to decide is narrower and harder: which identifiers are canonical,
        which folds were DELIBERATE, and which are INVERTIBLE.

        Detection-time taxonomies do not help there. At remediation time the partition that matters
        is invertible versus collapsing: a deformation that is injective is a backfill job, while a
        fold that maps two distinct keys onto one cannot be undone, because you cannot un-merge.

        Measured on our own decision store before this existed: 11,438 records, zero fields
        declaring any policy, and an eight-character prefix fold would have merged 594 groups and
        lost 1,365 keys. A reader had no way to tell a deliberate case-insensitive lookup from an
        accidental casefold, so `declared` below is the writer SAYING which it is -- and `measured`
        is the store's own keys answering what each fold would cost, independently of the claim.

        HONEST SCOPE. `declared` is a statement by the code that is running now; it cannot speak for
        the version that wrote a record last year. `measured` covers only the keys present -- a fold
        that already collapsed two keys is invisible here, because the evidence of the second one is
        gone. That asymmetry is the reason to emit the declaration at write time rather than infer
        it at read time, and it is the open half of this.
        """
        import unicodedata as _ud
        rows = self._tenant_rows()
        keys = [r.get("key") for r in rows if r.get("key")]

        folds = {
            "unicode_nfc": lambda k: _ud.normalize("NFC", k),
            "casefold": lambda k: k.casefold(),
            "strip_whitespace": lambda k: k.strip(),
            "prefix_8": lambda k: k[:8],
            "prefix_12": lambda k: k[:12],
        }
        measured = {}
        for name, fn in folds.items():
            groups: dict = {}
            for k in keys:
                groups.setdefault(fn(k), []).append(k)
            merging = {g: v for g, v in groups.items() if len({*v}) > 1}
            lost = sum(len({*v}) - 1 for v in merging.values())
            measured[name] = {
                "groups_that_would_merge": len(merging),
                "keys_that_would_be_lost": lost,
                # INVERTIBLE means: applying this fold to this store's keys loses nothing, so if it
                # were ever applied the original is recoverable. It is a property of the DATA, not
                # of the fold -- the same fold is destructive on a different store.
                "invertible_on_this_store": lost == 0,
                "example": next(iter(sorted(merging.values(), key=len, reverse=True)), [])[:3],
            }

        non_ascii = [k for k in keys if any(ord(c) > 127 for c in k)]
        return {
            # WHAT THE WRITER PROMISES. Stated, so a reader does not have to infer it from data that
            # cannot distinguish a policy from an accident.
            "declared": {
                "keys_stored": "byte-exact as supplied; no normalisation, casefolding or truncation",
                "lookup": "exact string equality, case-sensitive",
                "unicode_normalisation": None,
                "declared_by_version": __version__,
            },
            "measured": measured,
            "keys": len(keys),
            "non_ascii_keys": len(non_ascii),
            "mixed_normalisation": sum(1 for k in non_ascii if _ud.normalize("NFC", k) != k),
            "limits": [
                "`declared` describes the code running now, not the version that wrote any given "
                "record. A store written by several versions can carry several contracts.",
                "`measured` sees only surviving keys: a fold that already collapsed two of them "
                "left no trace of the second, so absence of merging is not proof none occurred.",
            ],
        }

    def check_sources(self, resolver=None) -> dict:
        """Has the SOURCE each memory came from changed, or gone? Returns a report, never a boolean.

        WHY THIS EXISTS, and it is a gap we measured on ourselves rather than imagined. Decay in this
        library is TEMPORAL -- a half-life on age. Age cannot tell a fact that has been true for five
        years from one that rotted in a week; the question that can is CAUSAL: *did the thing this
        memory is about actually change?* Answering it needs a source you can go back to, and on
        2026-08-10 we measured our own deployment: 210,544 records, `source` coverage 98.3%, sources
        resolving to anything re-checkable **0.01%** -- twenty-four records. The field held
        `agent:scholar`, the identity of the WRITER. So reconciliation was not unimplemented here, it
        was impossible, and this method is the half that makes it possible.

        THE FINGERPRINT IS NOT IN `source`. `remember(source=...)` takes a caller's dict verbatim, so a
        digest stored there would be settable by the writer whose drift it is meant to detect -- the
        exact shape of the trust-tier hole closed in 2.4.1. It lives in the reserved meta keyspace,
        which the caller cannot write.

        VERDICTS, per record:
          FRESH        the source resolves and its content still hashes to what we recorded
          DRIFTED      it resolves and the content changed -> re-read it, do not serve it blind
          ORPHANED     it does not resolve any more -> the source is gone
          UNCHECKABLE  no fingerprint: either no source, or one that names a writer rather than a
                       document. This is the honest denominator and the report LEADS with it.

        A REPORT THAT CANNOT FLATTER. `ok` is False whenever nothing was checkable, because "0 drifted"
        over 0 checked is the same sentence as a clean store and must not read like one. This is the
        `verify_attestations` rule applied to the outside world: an empty check is not a passing one.

        `resolver` is an optional callable taking the source doc and returning bytes (or None if it is
        gone), so a caller can point this at a git object store, an S3 bucket or an HTTP fetch. The
        default reads local files only -- deliberately, because guessing how to fetch an arbitrary
        identifier is how a checker starts inventing ORPHANED verdicts.
        """
        counts = {"FRESH": 0, "DRIFTED": 0, "ORPHANED": 0, "UNCHECKABLE": 0, "UNBOUND_CAPTURE": 0,
                  "NOT_BINDABLE": 0}
        #
        drifted, orphaned, unbound_capture = [], [], []
        bound = 0
        # SCOPED, unlike verify_attestations. That one is store-level because relocation between
        # tenants is only visible whole-store; drift is per-record and per-source, so a tenant's
        # report is complete inside its own slice -- and the report carries record ids, which is
        # exactly what must not cross a tenant boundary.
        for r in self.items:
            fp = (r.get("meta") or {}).get("source_sha256")
            doc = Inspeximus._raw_source(r)
            # BEFORE THE RESOLVE, because it is a fact about the RECORD and not about the source.
            # `observation_bound` says the fingerprint was taken from the bytes this memory was
            # actually derived from. Counting it after the ORPHANED branch meant every record whose
            # source cannot be resolved today was quietly dropped from the count -- so the reported
            # binding coverage rose as sources went missing, which is exactly backwards, and a store
            # whose sources had all moved reported binding 0 while every record was bound.
            if (r.get("meta") or {}).get("observation_bound"):
                bound += 1
            if not doc:
                # NOT BINDABLE IN ANY WINDOW, and that is different from unchecked. A record with no
                # document anchor has nothing to re-read, ever -- no fingerprint can be taken now,
                # next week, or after any amount of backfill. Counting it as UNCHECKABLE put it in a
                # bucket that looks like a migration backlog, and inside a denominator the number
                # could never escape.
                #
                # It is the majority state for a DECISION store and correctly so: "we chose X
                # because Y" has no source document. Measured on our own: 11,108 of 11,165 records.
                # A metric that reads those as un-fingerprinted material reports a healthy store as
                # permanently broken.
                counts["NOT_BINDABLE"] += 1
                continue
            if not fp:
                # A document IS named and we have no fingerprint for it. THIS is the backlog, and it
                # is the number worth chasing, because backfilling it is possible.
                counts["UNCHECKABLE"] += 1
                continue
            try:
                blob = resolver(doc) if resolver else (
                    open(doc, "rb").read() if os.path.exists(doc) else None)
            except Exception:
                blob = None
            if blob is None:
                counts["ORPHANED"] += 1
                orphaned.append(r.get("id"))
                continue
            # THE SOURCE MOVED BETWEEN OBSERVATION AND CAPTURE, which is neither of the other two and
            # must not be filed as either. FRESH would say the bytes still match what produced this
            # memory; DRIFTED would say they changed AFTERWARDS. Here they changed BEFORE, so the
            # memory may be perfectly right about what it read while the file on disk was already
            # something else. Named separately because the remedy differs: re-read and re-capture,
            # not re-derive. See remember() and claude-code#34556.
            # BOTH FACTS, not one. `source_moved_before_capture` used to preempt the comparison, so
            # a record with that flag could never ALSO be reported as DRIFTED -- and since the flag
            # lives in the reserved keyspace no caller can clear it, one such record pinned `ok` to
            # False permanently while hiding whether its source had since changed as well. They are
            # independent questions with different remedies.
            if (r.get("meta") or {}).get("source_moved_before_capture"):
                counts["UNBOUND_CAPTURE"] += 1
                unbound_capture.append(r.get("id"))
                if hashlib.sha256(blob).hexdigest() != fp:
                    drifted.append(r.get("id"))
            elif hashlib.sha256(blob).hexdigest() == fp:
                counts["FRESH"] += 1
            else:
                counts["DRIFTED"] += 1
                drifted.append(r.get("id"))
        checked = counts["FRESH"] + counts["DRIFTED"] + counts["ORPHANED"]             + counts["UNBOUND_CAPTURE"]
        total = sum(counts.values())

        # ---- THE COVERAGE METRICS, kept separate on purpose (CML / claude-code#34556). It said
        # FOUR while listing five, which is the kind of drift a reader trusts and should not.
        # safal207 measured his own corpus at 5/5 records carrying a source locator and declined to call
        # that "stale-check coverage", because a locator you cannot RE-FETCH answers a different
        # question. Collapsing them is how a schema gets reported as a guarantee: our own deployment has
        # 98.3% locator coverage and 0.01% that re-fetches. Four numbers, four different remedies.
        _n = len(self.items) or 0
        # THE DENOMINATOR IS BINDABLE SOURCES, and `not_bindable` is reported BESIDE it, never
        # inside. Same move as splitting `declared` out of `observed` rather than averaging them,
        # applied one level up: @Stratogain's framing, conceded on claude-code#34556.
        _nb = counts["NOT_BINDABLE"]
        _bindable = _n - _nb
        _with_locator = 0
        _bound_env = 0
        for _r in self.items:
            if Inspeximus._raw_source(_r):
                _with_locator += 1
            if ((_r.get("meta") or {}).get("environment_binding")):
                _bound_env += 1
        coverage = {
            # can the evidence point back to an origin at all?
            "locator_coverage": round(_with_locator / _n, 4) if _n else 0.0,
            # can that origin be re-read and deterministically compared? (fingerprint present AND read)
            "refetch_verification_coverage": (round(checked / _bindable, 4)
                                              if _bindable else None),
            # is the fingerprint of the bytes the agent ACTUALLY OBSERVED, or merely of the file as it
            # stood when remember() ran? A FIFTH number because it is a fifth question, and because
            # one can be 100% while the other is 0: safal207's point on claude-code#34556, made about
            # HEAD-vs-working-tree and true here about read-time-vs-write-time. Measured on ourselves
            # 2026-08-16 before the fix: a memory claiming value='A', captured after the file had
            # become 'B', recorded B's digest and reported FRESH. A wrong-but-resolvable fingerprint
            # is worse than none -- it is false confidence rather than an absence.
            # DECLARED, not proven, and the name has to say so. A caller that never opened the file,
            # hashed it at write time and passed that as `observed_sha256` scores 1.0 here -- the
            # metric cannot distinguish itself from the write-time hash it exists to distinguish
            # itself from. That is inherent: only the reader knows what it read. What the library
            # CAN check is disagreement, and it does; what it cannot check is a caller that lies in
            # the agreeing direction, so the number is named for what it measures.
            "declared_observation_binding_coverage": (round(bound / _bindable, 4)
                                                      if _bindable else None),
            # Kept under the old name too for one release: renaming a published metric out from under
            # a caller is its own kind of silent breakage.
            "observation_binding_coverage": (round(bound / _bindable, 4)
                                             if _bindable else None),
            # can the AUTHORITATIVE source be enumerated, so deletions are detectable at all? An
            # index-side scan can never answer this: it reports what is present, and a deleted document
            # emits exactly one event that nothing later mentions. Without an enumerator this is not a
            # low number, it is an UNKNOWN, and it says so rather than reporting 0.0 as if measured.
            # BESIDE THE RATIOS, NEVER INSIDE THEM. Without these two a reader cannot tell a
            # coverage of 0.0 over 57 bindable records from 0.0 over 11,165, and those call for
            # completely different reactions. `None` rather than 0.0 when NOTHING is bindable: a
            # ratio with an empty denominator is undefined, and printing 0.0 there would say "we
            # measured, and it is bad" about a store where there was nothing to measure.
            "not_bindable": _nb,
            "bindable": _bindable,
            "source_enumeration_coverage": None,
            # is the record bound to an environment (repo/commit/tenant/policy/model/TTL)? We do not
            # write that binding anywhere yet, so this is honestly 0.0 rather than absent -- the
            # REVALIDATE half of the contract is unbuilt here.
            "environment_binding_coverage": round(_bound_env / _n, 4) if _n else 0.0,
        }

        report = {
            "counts": counts, "checked": checked, "total": total,
            "checkable_fraction": round(checked / total, 4) if total else 0.0,
            "coverage": coverage,
            "drifted": drifted[:200], "orphaned": orphaned[:200],
            "unbound_capture": unbound_capture[:200],
            # A capture whose source had already moved is a finding, not a note: the memory is bound
            # to bytes that are not on disk and never will be again, so nothing here can re-verify
            # what produced it. Folding it into `ok` is the difference between reporting the state
            # and flattering it.
            # `ok` still requires something to have been CHECKED -- an empty check is not a
            # passing one -- but a store of pure decisions has nothing checkable by construction,
            # and calling that False forever is the same "permanently broken" reading the
            # denominator fix removes. So: nothing bindable at all is reported through `limits`
            # and `not_bindable`, and does not by itself make the verdict False.
            # AN EMPTY STORE IS NOT A DECISIONS-ONLY STORE, and my first version of this exemption
            # collapsed them: `_bindable == 0` is true of both, so a store with NO RECORDS AT ALL
            # started reporting ok. An existing test said so in one line -- "no records is not a
            # division by zero, and it is not a clean bill either" -- and it was right. A store
            # whose records are decisions has something to be right about; an empty one does not.
            "ok": (not drifted and not orphaned and not unbound_capture
                   and (bool(checked) or (_bindable == 0 and _n > 0))),
        }
        if not checked:
            report["problem"] = (
                "%d records carry no re-checkable source fingerprint, so this verified NOTHING. "
                "Pass source={'doc': <a path/URL you can fetch again>} at write time; a source that "
                "names the writer cannot answer whether the content changed." % total)
        return report

    def verify_attestations(self, expected_key: str | None = None) -> tuple[bool, list[str]]:
        """Re-check every stored attestation against the record it sits on. Returns (ok, problems).

        THIS EXISTED NOWHERE UNTIL 2.4.0, which was the defect. 2.3.0 started keeping `attested_sig`
        precisely so an auditor would not have to take the write path's word for it -- its own changelog
        says "a non-repudiable identity you cannot re-verify is not one" -- and then shipped no way to
        re-verify it. Across this library the signature was written and never read: the only check
        anywhere was a hand-rolled one inside a test. A field nobody can re-compute is a claim, not an
        attestation.

        What it catches, and each is a real edit an out-of-band writer can make to the JSON:
          * the text changed under a signature (the signature covers the text);
          * the source relabelled (it covers the canonical source, so 'X by S' cannot be replayed as
            'X by T');
          * a record MOVED BETWEEN TENANTS, for writes this store signed itself -- 2.4.0 binds the
            tenant into the message, so relocating a row invalidates it;
          * a signature present with no key, or a key with no signature.

        `expected_key` additionally pins WHO: every attestation must carry that public key. Without it
        this answers "is each signature internally consistent", which a forger holding any key can also
        satisfy -- so a run with no `expected_key` is an integrity check, not an identity one.

        HONEST LIMITS. Externally-attested records (`remember(..., attestation=...)`) are NOT
        tenant-bound: an outside signer cannot sign a binding to a tenant it never saw, so those rows
        verify identically in any tenant and this reports them as valid. Records with no attestation at
        all are skipped and counted, not failed -- most stores have none, and failing them would make
        the check useless where it matters. And a valid signature attests AUTHORSHIP, never truth.

        IT ALSO BINDS NO TIME, and this is a note for whoever adds a backfill rather than a live defect:
        there is no such path today, so every signature here was made at write time. The moment one
        exists -- and "sign my historical records" is a natural thing to want -- a signature made during
        a migration will attest that the store holds this content NOW, not that the content is what it
        was when the record was written, and NOTHING in the record distinguishes the two. The weaker
        guarantee then inherits the stronger one's appearance, silently, which is the shape of every
        defect in this file's history. RFC 3161 is the gap: a timestamp token supplies "this datum
        existed before time T" and a backfilled signature cannot. If you build that path, carry
        `signed_at` separately from the record's own timestamp and have this verifier report the two
        cases differently -- custody-attested is not attested-since-creation. (Raised with a
        collaborator hitting it first, on NousResearch/hermes-agent#34352, 2026-08-11.)

        THIS BINDS PLACEMENT, NOT CARDINALITY, and the distinction is not academic here: the failure
        that motivated tenant binding was tenant-scoped data LOSS (2.3.2 -- a scoped handle's save
        serialised its own filtered view). A row that is GONE carries no failing signature, so this
        answers `ok` on a store that just lost a tenant. Measured: delete every acme row from a signed
        five-record store and this returns ok=True with zero problems, while relocating one row returns
        ok=False -- the same run, so the verifier is demonstrably alive. **`verify_writes()` is the
        check for absence**: with receipts on it names the vanished ids ("written but missing from the
        store (deleted out-of-band)"). Signature for placement, receipt chain for cardinality; neither
        substitutes for the other, and offering this one as protection against loss would be a
        category error.
        """
        problems: list[str] = []
        checked = skipped = 0
        for r in self._items:
            key, sig = r.get("attested_key"), r.get("attested_sig")
            if not key and not sig:
                skipped += 1
                continue
            rid = r.get("id", "?")
            if bool(key) != bool(sig):
                problems.append("%s carries %s without the other -- an attestation that cannot be checked"
                                % (rid, "attested_key" if key else "attested_sig"))
                continue
            if expected_key and key != expected_key:
                problems.append("%s is attested by %s..., not the expected %s..."
                                % (rid, str(key)[:12], str(expected_key)[:12]))
            if not _HAVE_ED:
                problems.append("%s cannot be verified: the `cryptography` package is not installed" % rid)
                continue
            src = r.get("source")
            src_doc = src.get("doc") if isinstance(src, dict) else (src if isinstance(src, str) else None)
            text = r.get("text", "")
            # THE NO-TENANT FALLBACK IS A DOWNGRADE CHANNEL, so it is not offered to a key that had no
            # excuse for omitting the tenant. An unbound store leaves the field out of the message --
            # that omission is what keeps pre-2.4.0 signatures verifying -- and this loop first tried
            # the record's tenant and THEN no-tenant for every key alike. So a row signed while unbound
            # and later GIVEN a tenant still verified: measured, placing an unbound-signed row into
            # `beta` returned ok=True with zero problems, which is a row being promoted into a tenant it
            # was never signed for.
            #
            # Our own writer always knows the tenant it is writing under, so for a key we can NAME --
            # this store's `writer_pubkey`, or one pinned through `expected_key` -- the bound form is
            # the only form accepted. A foreign signer genuinely cannot bind a tenant it never saw, so
            # those keep both candidates; that residual is precisely why `expected_key` exists, and
            # verifying from a store that knows neither key cannot tell the two apart.
            nameable = {k for k in (getattr(self, "writer_pubkey", None), expected_key) if k}
            _tn = r.get("tenant")
            ok_any = False
            for cand in ((_tn,) if (_tn is None or key in nameable) else (_tn, None)):
                try:
                    _Ed25519PK.from_public_bytes(bytes.fromhex(key)).verify(
                        bytes.fromhex(sig), _attest_message(text, src_doc, cand))
                    ok_any = True
                    break
                except Exception:
                    continue
            checked += 1
            if not ok_any:
                problems.append("%s: the stored signature does not verify -- its text, source or tenant "
                                "changed after it was signed" % rid)
        if not checked and not problems:
            problems.append("no record carries an attestation, so this verified NOTHING (%d skipped). "
                            "An empty check is not a passing one." % skipped)
        return (not problems), problems

    def verify_writes(self, expected_pubkey: str | None = None, warn_unpinned: bool = False,
                      legacy_strict: bool = True, value_strict: bool = True,
                      coverage_strict: bool = True,
                      require_signed: bool = False) -> tuple[bool, list[str]]:
        """Verify the write-receipt chain AND that each stored memory still matches its write receipt.
        Returns (ok, problems). Catches out-of-band edits to the store the normal flow can't see.
        Requires receipts to have been enabled at write time.

        `legacy_strict` (default True, a BEHAVIOUR CHANGE in 1.73.0) checks PRE-1.68 receipts against
        EVERY receipt rather than only the latest. It fails closed on the <=1.67 laundering path, at the
        cost of a false positive on a legitimate pre-1.68 slash()/restore(), which is indistinguishable
        from the attack by construction. Pass False to restore the previous, quieter behaviour."""
        problems: list[str] = []
        legacy_flagged: set = set()
        if self.receipts_enabled and not self._receipts and self.items:
            problems.append(f"receipts are enabled but the chain is EMPTY while the store holds "
                            f"{len(self.items)} record(s) -- nothing here is covered by a write receipt")
        elif not self.receipts_enabled and self.items:
            problems.append(f"write receipts are DISABLED: {len(self.items)} record(s) exist with no write "
                            f"chain, so there is nothing to verify -- which is not the same as verified")
        for what, err in (getattr(self, "_read_errors", None) or {}).items():
            problems.append(f"the {what} sidecar is UNREADABLE ({err}) — the state it holds cannot be "
                            f"consulted, so anything gated on it fails closed")
        for what, err in (getattr(self, "_sidecar_errors", None) or {}).items():
            problems.append(f"the {what} chain was NOT persisted ({err}) — it exists in memory only, so the "
                            f"evidence this store's integrity rests on will not survive a reload")
        if getattr(self, "_extractor_errors", 0):
            problems.append(f"the key/object extractor raised on {self._extractor_errors} write(s) "
                            f"(last: {self._extractor_last_error}) — those records are UNKEYED, so "
                            f"supersession never ran for them")
        if getattr(self, "_persist_error", None):
            # An unwritable store makes every other check here a statement about MEMORY, not about what an
            # auditor would find on disk. Reported first because it invalidates the rest.
            problems.append(f"store not persisted: {self._persist_error['error']} "
                            f"(in-memory state has not reached {self._persist_error['path']})")
        prev = _GENESIS
        by_id = {it["id"]: it for it in self.items}
        for i, r in enumerate(self._receipts):
            # ONE definition, shared with anchor() and the offline bundle verifier -- see _chain_core.
            # `amends` must be inside the hash: it decides which fields a later receipt forgives, so an
            # attacker who could append it to an existing receipt would switch the text check off while the
            # chain still verified. An unhashed authorisation is not an authorisation.
            core = Inspeximus._chain_core(r, "write")
            if r.get("prev") != prev:
                problems.append(f"receipt {i}: broken chain link (a prior receipt was altered/removed)")
            if _sha256_hex(_canon(core)) != r.get("hash"):
                problems.append(f"receipt {i}: receipt tampered (hash mismatch)")
            if "sig" in r and _HAVE_ED:
                try:
                    _Ed25519PK.from_public_bytes(bytes.fromhex(r["pubkey"])).verify(
                        bytes.fromhex(r["sig"]), bytes.fromhex(r["hash"]))
                    if expected_pubkey and r.get("pubkey") != expected_pubkey:
                        problems.append(f"receipt {i}: signed by an unexpected key")
                except Exception:
                    problems.append(f"receipt {i}: invalid signature")
            elif expected_pubkey:
                problems.append(f"receipt {i}: unsigned, but a signature was required")
            cur = by_id.get(r["memory_id"])
            if cur is None:
                # a missing record is only a PROBLEM if it was NOT deliberately erased. A deletion tombstone
                # (forget_subject) makes the erasure accounted-for: the write-chain stays intact and the record
                # is provably erased, not silently tampered away. No tombstone -> still flag as out-of-band.
                if not any(t.get("memory_id") == r["memory_id"] for t in self._tombstones):
                    problems.append(f"memory {r['memory_id']}: written but missing from the store (deleted out-of-band)")
            else:
                # compare only the fields THIS receipt committed to (a receipt written before attribution was
                # committed has no attrib_sha256 — don't fault it for a field it never promised)
                # Compare against the LATEST receipt for this record, not every one of them. A record may
                # legitimately be amended in-band — slash() revokes graduation by rewriting `mtype`, which is
                # a committed field — and the amendment appends a new receipt. Faulting the superseded
                # earlier receipt made our own accountability lever raise a tamper alarm (measured: 27 of 45
                # random operation sequences). Append-only is preserved: every receipt stays in the chain and
                # the amendment is itself the evidence of when standing changed.
                # NOTE: no `continue` here — an early version used one and skipped the `prev = r["hash"]`
                # at the end of the loop body, so every later receipt reported "broken chain link".
                cc = self._write_commit(cur)
                rc = r.get("commit") or {}
                if "immutable_sha256" in rc:
                    # A receipt is forgiven for exactly the fields a LATER receipt DECLARED it was amending,
                    # and for nothing else -- INTERSECTED WITH `_AMENDABLE`, because the declaration is
                    # attacker-writable and the vocabulary was not.
                    #
                    # This comment used to read "text+key (immutable_sha256) and attribution are never
                    # declared by any call site, so they bind on every receipt for the life of the record."
                    # That was a description of our own call sites, not a property of the chain, and an
                    # append-only log lets anyone add a call site. Measured 2026-08-15: tamper the stored
                    # text out of band (verify_writes correctly goes False), then append ONE well-formed
                    # receipt whose `amends` names immutable_sha256 -- with a correct hash over the real
                    # preimage, so the chain link is genuine -- and verify_writes returns to True over text
                    # that was never written. That is the 1.67.0 laundering path, re-entered through the
                    # amends vocabulary instead of through slash().
                    forgiven = {f for x in self._receipts
                                if x["memory_id"] == r["memory_id"] and x.get("seq", 0) > r.get("seq", 0)
                                for f in (x.get("amends") or ())} & _AMENDABLE
                    # `k in rc` so a pre-1.82 receipt, which has no `value_sha256`, is checked on what it
                    # does carry instead of failing on a field that did not exist when it was written. It
                    # cannot be used to strip a field either: the receipt hash covers the whole commit, so
                    # deleting one breaks the chain link.
                    bad = any(rc.get(k) != cc.get(k)
                              for k in ("immutable_sha256", "mtype", "value_sha256",
                                        "status_sha256", "time_sha256", "attrib_sha256")
                              if k not in forgiven and k in rc)
                elif legacy_strict:
                    # PRE-1.68 receipt, checked STRICTLY: text/key/mtype are one hash here, so the only
                    # way to bind the original content is to check EVERY receipt, not just the latest.
                    #
                    # This is the difference between failing closed and failing open, and it is measured.
                    # Under <=1.67.0 an attacker could edit the stored text out of band and then call the
                    # PUBLIC slash(), which appended a fresh receipt committing to the FORGED text. Nothing
                    # in the past was rewritten, so append-only held, the receipt chain stayed internally
                    # consistent, and an externally witnessed anchor still re-derives its prefix intact --
                    # the tamper is invisible to the chain, to the anchor, and to every later version.
                    # Checking only the latest receipt (the 1.68-1.72 fallback) carried that invisibility
                    # forward forever for stores written before the split.
                    #
                    # The cost is a FALSE POSITIVE on a LEGITIMATE slash()/restore() performed under
                    # <=1.67, which is indistinguishable from the attack by construction -- same shape,
                    # same append. That is the honest trade: an alarm you must investigate, instead of
                    # silence you cannot trust. Pass legacy_strict=False if you know your pre-1.68
                    # amendments were legitimate, or re-write the record once to upgrade its receipt.
                    bad = any(cc.get(k) != v for k, v in rc.items())
                    if bad:
                        legacy_flagged.add(r["memory_id"])
                elif max((x.get("seq", 0) for x in self._receipts
                          if x["memory_id"] == r["memory_id"]), default=r.get("seq", 0)) == r.get("seq", 0):
                    bad = any(cc.get(k) != v for k, v in rc.items())
                else:
                    bad = False
                if bad:
                    # SAY WHICH FIELD. Until 2.10.2 every mismatch printed "stored content no longer
                    # matches", which was true of text and object and actively misleading of the two
                    # fields added since: an operator told their CONTENT was edited goes and diffs
                    # text that is byte-identical, finds nothing, and concludes the alarm is noise.
                    # A message must name the remedy it wants, and the remedy differs -- a text
                    # mismatch is a rewritten memory, a status_sha256 mismatch is a record made
                    # visible (or vouched-for) by someone who did not go through confirm().
                    _WHAT = {"immutable_sha256": "its TEXT or KEY", "mtype": "its TYPE",
                             "value_sha256": "its VALUE (`object`)",
                             "status_sha256": "whether the store SERVES it, or who confirmed it",
                             "time_sha256": "WHEN the fact became true (`valid_from`), or where that "
                                            "time came from",
                             "content_sha256": "its text/key/type"}
                    _diff = [k for k, v in rc.items() if k in _WHAT and cc.get(k) != v]
                    # `content_sha256` is the pre-1.68 COMPOSITE of text+key+mtype, so it co-fires
                    # with whichever specific hash actually moved. Naming both reads as two findings
                    # where there is one. Keep it only when it is the only signal -- on a legacy
                    # receipt that carries nothing finer.
                    if len(_diff) > 1:
                        _diff = [k for k in _diff if k != "content_sha256"]
                    _why = ("; ".join(_WHAT[k] for k in _diff) if _diff
                            else "a field its receipt commits to")
                    problems.append(
                        f"memory {r['memory_id']}: {_why} no longer matches its write receipt "
                        f"(edited after write)")
            prev = r.get("hash")
        # verify the DELETION-TOMBSTONE chain too — else a forged tombstone could hide a real out-of-band delete
        tprev = _GENESIS
        for j, t in enumerate(self._tombstones):
            core = Inspeximus._tombstone_core(t)
            if t.get("prev") != tprev:
                problems.append(f"tombstone {j}: broken chain link (a prior tombstone was altered/removed)")
            if _sha256_hex(_canon(core)) != t.get("hash"):
                problems.append(f"tombstone {j}: tombstone tampered (hash mismatch)")
            if "sig" in t and _HAVE_ED:
                try:
                    _Ed25519PK.from_public_bytes(bytes.fromhex(t["pubkey"])).verify(
                        bytes.fromhex(t["sig"]), bytes.fromhex(t["hash"]))
                    if expected_pubkey and t.get("pubkey") != expected_pubkey:
                        problems.append(f"tombstone {j}: signed by an unexpected key")
                except Exception:
                    problems.append(f"tombstone {j}: invalid signature")
            elif expected_pubkey:
                problems.append(f"tombstone {j}: unsigned, but a signature was required")
            tprev = t.get("hash")
        # OPT-IN footgun advisory (default off = byte-identical legacy): a signature verified against the
        # receipt's OWN pubkey is not operator-adversarial-safe — a store-rewriter can swap sig+pubkey together.
        # With warn_unpinned=True and signatures present but no expected_pubkey pinned, surface it as a problem.
        if warn_unpinned and expected_pubkey is None and (
                any("sig" in r for r in self._receipts) or any("sig" in t for t in self._tombstones)):
            problems.append("signatures present but expected_pubkey not pinned: a store-rewriter can swap the "
                            "key and still pass — pass expected_pubkey, or witness anchor() externally")
        if legacy_flagged:
            problems.append(
                f"{len(legacy_flagged)} record(s) carry PRE-1.68 receipts that no longer match their stored "
                f"content. Under <=1.67.0 a public slash() re-committed a receipt to whatever the text said "
                f"AT THAT MOMENT, so an out-of-band edit could be laundered into the chain without breaking "
                f"append-only — invisible to the chain, to a witnessed anchor, and to every later version. "
                f"A LEGITIMATE slash()/restore() under <=1.67 looks identical, so this may be benign: check "
                f"the text against a copy you trust. Re-writing a record upgrades its receipt; pass "
                f"legacy_strict=False to silence this once you have checked.")
        # PRE-1.82 receipts do not commit `object` -- the value the store SERVES. Silently applying the new
        # check only where it happens to be present would be this repository's most-repeated defect: a
        # record that cannot be checked reported exactly like one that passed. So it is named, on the same
        # terms as the pre-1.68 case above: fail closed, explain, and give the caller a way to accept it.
        if value_strict:
            latest: dict = {}
            for r in self._receipts:
                if r.get("seq", 0) >= latest.get(r["memory_id"], {}).get("seq", -1):
                    latest[r["memory_id"]] = r
            uncovered = sorted(
                rec["id"] for rec in self.items
                if rec.get("status") == "active" and rec.get("object") is not None
                and "value_sha256" not in ((latest.get(rec["id"], {}).get("commit")) or {})
                and rec["id"] in latest)
            if uncovered:
                problems.append(
                    f"{len(uncovered)} record(s) carry PRE-1.82 receipts that do not commit `object`, the "
                    f"VALUE this store serves and that supersession, the echo guard and revert() all key "
                    f"on. Editing that value out of band is invisible to those receipts while text and key "
                    f"still match, so they are not verified on the field that matters most. Check them "
                    f"against a copy you trust, then call recommit(ids=[...]) to bind their CURRENT state "
                    f"into the chain -- or pass value_strict=False to accept the gap without a record of "
                    f"having done so. ({', '.join(uncovered[:5])}"
                    + (f", +{len(uncovered) - 5} more" if len(uncovered) > 5 else "") + ")")
        # SIGNATURE COVERAGE, on the PRIMARY surface. Signatures were verified here when present and
        # demanded only when `expected_pubkey` was passed, so an UNSIGNED entry appended to a SIGNED
        # chain fell through the `elif expected_pubkey:` branch and nothing was said. Measured
        # 2026-08-16: plant a record, hand-mint a well-formed hash-linked receipt for it with no
        # signature, and verify_writes returned (True, []) on a store opened with receipt_key. The
        # equivalent check already existed in verify_bundle and had simply never reached the tool an
        # agent actually calls -- the same one-surface-of-two shape this file keeps meeting.
        #
        # A CHAIN SIGNED IN PLACES IS NOT SIGNED, and that is not a preference: it is exactly what
        # the append attack produces. So it is a problem with no opt-in required.
        #
        # A FULLY unsigned chain is legitimate -- receipts without a key are the documented default
        # and catch an editor who cannot also rewrite the sidecar -- so it is NOT a problem unless
        # the caller asks. `require_signed=True` is how an operator who configured a key finds out
        # that the key stopped being used.
        _chain = list(self._receipts or ()) + list(self._tombstones or ())
        if _chain:
            _signed = sum(1 for r in _chain if r.get("sig"))
            if 0 < _signed < len(_chain):
                problems.append(
                    f"{len(_chain) - _signed} of {len(_chain)} chain entries carry NO signature "
                    f"while {_signed} do. A chain signed in places is not signed: something without "
                    f"the key appended to it.")
            elif _signed == 0 and require_signed:
                problems.append(
                    f"UNSIGNED: none of the {len(_chain)} chain entries carry a signature. The chain "
                    f"is internally consistent, which is not the same as attributable -- an editor "
                    f"who can rewrite the .receipts sidecar can rewrite the chain with it. Pass "
                    f"receipt_key= (with the key OUTSIDE the store's directory) or a receipt_signer.")

        # A RECORD NO RECEIPT NAMES IS NOT A CHECKED RECORD. This walks the RECEIPTS and looks each
        # one's record up, so a record present in the store that no receipt mentions was never
        # examined at all -- and the MCP tool's own docstring promises "no silent edits/INSERTIONS/
        # reordering". Measured 2026-08-15: append one JSON object to the store file and this returned
        # (True, []) while recall served the fabricated memory. The attacker holds no key and forges
        # nothing; they ADD rather than edit, and the check is built to notice editing.
        #
        # OVER EVERY STATUS, not just `active`. `verify_attribution` -- the one surface that did catch
        # the injection -- sweeps active only, so changing one word in the planted record to
        # `superseded` flipped it to ok=True while `as_of()` and `history()` still served the value.
        # Same attack, one field different, opposite verdict.
        #
        # This also closes receipt TRUNCATION, which was the same blindness weaponised: drop the
        # trailing receipt and that record's text and value could then be rewritten freely, because
        # nothing named it any more.
        #
        # `coverage_strict=False` for the honest case this fires on -- a store that turned receipts on
        # part-way, whose older records genuinely have none. An opt-out with a name, like
        # legacy_strict and value_strict, not a silent exemption.
        if coverage_strict and self.receipts_enabled and self._receipts:
            _named = {rc["memory_id"] for rc in self._receipts}
            _uncovered = sorted(r["id"] for r in self._items if r["id"] not in _named)
            if _uncovered:
                problems.append(
                    f"{len(_uncovered)} record(s) are covered by NO write receipt, so nothing here "
                    f"vouches for them: {_uncovered[:5]}{' ...' if len(_uncovered) > 5 else ''}. They "
                    f"were inserted out of band, or written while receipts were off. Pass "
                    f"coverage_strict=False if this store enabled receipts part-way.")

        # A RECORD A LATER WRITE RETIRED MUST NOT BE ACTIVE. This is the half `status_sha256` cannot
        # cover: it folds `active` and `superseded` into one serving class on purpose, so SWAPPING the
        # two on disk was invisible to it -- the store then serves the corrected-away value as current
        # and hides the correction, with every verifier reporting clean. Measured 2026-08-15, and the
        # same edit on a `provisional` record WAS caught, which is what makes it a coverage hole in
        # that commitment rather than a missing mechanism.
        #
        # This is the RESURRECTION direction -- a retired record made current again. The mirror
        # image, CONCEALMENT, is handled by the block below it: it needed every retiring path to
        # declare itself, which `_declare_retired` now does for the five that had no newer record to
        # hang a declaration on.
        if self.receipts_enabled and self._receipts:
            _live = {r["id"] for r in self._items if r.get("status") == "active"}
            for _rid in sorted({rid for rc in self._receipts
                                for rid in ((rc.get("commit") or {}).get("retires") or ())
                                if rid in _live}):
                problems.append(
                    f"memory {_rid}: a later write RETIRED this record and it is ACTIVE again -- the "
                    f"store is serving a value that was corrected away (edited after write)")
            # THE OTHER DIRECTION: CONCEALMENT. `retires` catches un-retiring an old record; hiding
            # the CURRENT one is the mirror image, and it was still open after that fix. Measured
            # 2026-08-15: mark the live record `superseded` on disk and recall returns [], there is
            # no active record for the key, and verify_writes, verify_bundle and bind_content are all
            # clean. Composed with the injection hole it became a controlled SUBSTITUTION -- conceal
            # the true payment policy, inject a replacement, and the agent is served the forgery.
            #
            # THE RULE: a record that was BORN recallable and is not recallable today must be
            # accounted for -- by a later receipt (its own retirement declaration, or another write's
            # `retires`), or by a tombstone. Every legitimate path now declares itself; see
            # _declare_retired for the five that had nothing to hang a declaration on.
            #
            # `born_status` is what makes this possible without false alarms: a record retired ON
            # ARRIVAL by the echo, objectless or back-fill guards was never recallable, so its own
            # birth receipt is the whole explanation and it is not swept.
            _first: dict = {}
            _later: dict = {}
            for rc in self._receipts:
                mid = rc.get("memory_id")
                if mid not in _first:
                    _first[mid] = rc
                else:
                    _later[mid] = rc
            _tombed = {t.get("memory_id") for t in (self._tombstones or ())}
            _named = {rid for rc in self._receipts
                      for rid in ((rc.get("commit") or {}).get("retires") or ())}
            _hidden = []
            for r in self._items:
                mid = r["id"]
                rc = _first.get(mid)
                if rc is None:
                    continue                      # uncovered: the coverage sweep above owns that case
                born = (rc.get("commit") or {}).get("born_status")
                if born is None:
                    continue                      # pre-2.10.2 receipt: nothing to compare against
                # BORN AS THE LIVE VALUE, which is the only thing that can be HIDDEN. Not "born
                # recallable": `superseded` is in `_RECALLABLE` (history is readable with
                # include_superseded), so the first version of this line skipped nothing for a record
                # retired ON ARRIVAL and reported every honest echo as concealment. `hub` counts --
                # it is a ranking demotion, not a retirement.
                if born not in ("active", "hub"):
                    continue                      # never the current value, so never hidden
                if r.get("status") in ("active", "hub"):
                    continue                      # still current, or demoted in RANKING only
                if mid in _later or mid in _named or mid in _tombed:
                    continue                      # accounted for
                _hidden.append(mid)
            for mid in sorted(_hidden):
                problems.append(
                    f"memory {mid}: born ACTIVE and is no longer current, with nothing accounting for "
                    f"it -- no retirement declared, no later write claiming it, no tombstone. A live "
                    f"record was hidden (edited after write)")
        return (len(problems) == 0, problems)

    def recommit(self, ids=None) -> dict:
        """Append a fresh write receipt for records whose receipts predate a commitment field.

        Why this exists: 1.82.0 started committing `object`, the value the store SERVES. Records written
        before it carry receipts that never hashed the value, so `verify_writes()` names them and cannot
        check them. The message told operators to "re-write the record" -- and that turned out to be advice
        that does not work: `slash()` appends a receipt only for a GRADUATED memory, so an ordinary record
        had no path at all. Building the path beat rewording the limitation.

        HONEST SCOPE, and it is the whole of it: this binds the record's state AS IT IS NOW. It is not a
        validation of the past and cannot be -- if the value was already edited out of band, this commits
        the edited one and the store will verify clean afterwards. That is why it is an explicit operator
        action with named ids rather than something an upgrade does for you: run it only on records you
        have checked against a copy you trust. Compared with `value_strict=False`, which silences the
        report, this leaves the decision IN the chain: a new receipt, at a new sequence, with a timestamp.

        Returns {recommitted, skipped}. Requires receipts enabled."""
        if not self.receipts_enabled:
            return {"recommitted": [], "skipped": [],
                    "problems": ["write receipts are disabled, so there is no chain to append to"]}
        wanted = set(ids) if ids is not None else None
        done, skipped = [], []
        # _tenant_rows(), not self.items: on a tenant view the latter is the SHARED store, so an unscoped
        # sweep would re-commit another tenant's records. The isolation guard refused to let this method
        # exist unclassified, which is exactly what that guard is for.
        for rec in self._tenant_rows():
            if rec.get("status") != "active":
                continue
            if wanted is not None and rec["id"] not in wanted:
                continue
            latest = max((r for r in self._receipts if r["memory_id"] == rec["id"]),
                         key=lambda r: r.get("seq", 0), default=None)
            if latest is not None and "value_sha256" in ((latest.get("commit")) or {}):
                skipped.append(rec["id"])            # already covered; a no-op receipt is chain noise
                continue
            self._emit_write_receipt(rec)
            done.append(rec["id"])
        if done:
            self._save(force=True)
        return {"recommitted": done, "skipped": skipped, "problems": []}

    def verify_attribution(self) -> dict:
        """Tamper-evidence for the ATTRIBUTION FLOOR. k, the influence budget, the influence gate and slash are all
        keyed on a memory's canonical source id; a post-hoc RELABEL (rewriting a record's source, or stripping its
        inherited derived_from taint) therefore voids all of them at once, silently, with no inner layer to appeal
        to — attribution is not a fourth axis, it is the floor the others stand on. This binds each write's
        attribution into the tamper-evident receipt chain (see _write_commit) and reports, per memory, whether its
        CURRENT canonical sources still match what was committed at write time. A relabel is thus LOUD, not silent.

        Returns {ok, chain_ok, relabeled, uncommitted, missing}:
          - relabeled: active memory ids whose current sources differ from their receipt (the attack this catches);
          - uncommitted: active ids with no attribution in their receipt (written before this was added, or the
            memory was never receipted) — cannot be checked, so not trusted;
          - missing: ids in the receipt chain no longer in the store.
        TWO honest limits (do NOT read this as tamper-PROOF):
        1. tamper-evidence != CORRECTNESS. A source that was WRONG at write time (an attacker who controls the
           labeling channel, e.g. MINJA) is committed faithfully and this cannot tell it was wrong — the
           genuinely-open oracle problem, untouched.
        2. the chain is only tamper-EVIDENT if it is SIGNED with a receipt_key held OFF the write path (or its head
           is externally anchored). UNSIGNED (the default), an attacker who can silently relabel rec['source'] can
           equally recompute the whole sidecar receipt chain with the new sources and pass this check — so bare
           verify_attribution() only catches a relabel by an actor who can edit the store but NOT the .receipts
           sidecar (e.g. an out-of-band DB edit). For the 'loud' property to hold against a store-capable attacker
           you MUST pass receipt_key=... (Ed25519) with the key out of reach, or anchor the chain head externally.
        Requires receipts enabled at write time. The crypto is textbook (Haber-Stornetta 1991 hash-chains,
        Schneier-Kelsey 1998 tamper-evident logs); the only new bit is committing attribution so a source-keyed
        defense set's single silent failure (relabel) becomes detectable."""
        # chain integrity = the receipt log's OWN hashes link and aren't tampered/mis-signed. Kept independent of
        # whether stored content was later LEGITIMATELY mutated (e.g. slash changes mtype) — that is the relabeled
        # question below, not a log-integrity failure.
        chain_ok, prev = True, _GENESIS
        for r in self._receipts:
            # THE FIFTH SITE, and it was already WRONG. A hand-written copy of the write preimage,
            # frozen at the pre-1.68.0 shape, so it missed `amends` (1.68.0) and `amend_reason`
            # (2.10.0) -- the drift `_chain_core`'s own docstring records as "four definitions of one
            # preimage, and the fix reached two of them". Measured 2026-08-15: after a LEGITIMATE
            # slash(), verify_writes() and anchor() said clean while verify_attribution() and
            # provenance() both reported chain_ok=False -- a false alarm on the surface whose whole
            # job is to make a relabel loud, on every store that has ever revoked standing.
            _core = Inspeximus._chain_core(r, "write")
            if r.get("prev") != prev or _sha256_hex(_canon(_core)) != r.get("hash"):
                chain_ok = False
            if "sig" in r and _HAVE_ED:
                try:
                    _Ed25519PK.from_public_bytes(bytes.fromhex(r["pubkey"])).verify(
                        bytes.fromhex(r["sig"]), bytes.fromhex(r["hash"]))
                except Exception:
                    chain_ok = False
            prev = r.get("hash")
        by_id = {it["id"]: it for it in self.items}
        committed = {}                         # latest committed attribution hash per memory id (None if pre-attrib)
        for r in self._receipts:
            committed[r["memory_id"]] = (r.get("commit") or {}).get("attrib_sha256")
        relabeled, uncommitted, missing = [], [], []
        for mid, a in committed.items():
            cur = by_id.get(mid)
            if cur is None:
                missing.append(mid)
            elif cur.get("status") != "active":
                continue
            elif a is None:
                uncommitted.append(mid)
            elif _sha256_hex(_canon(sorted(Inspeximus._rec_sources(cur)))) != a:
                relabeled.append(mid)
        # Records with NO receipt at all never entered `committed`, so they could not land in `relabeled`
        # OR in `uncommitted` -- they were not unchecked, they were UNCOUNTED. On a store written with
        # receipts off (the default) this returned {'ok': True, 'uncommitted': []} while every `source`
        # label had been rewritten on disk. The docstring already promised these appear in `uncommitted`.
        for mid, cur in by_id.items():
            if cur.get("status") == "active" and mid not in committed:
                uncommitted.append(mid)

        problems: list[str] = []
        if not self.receipts_enabled and self.items:
            # The sibling one call site over, `verify_writes`, has said exactly this since 1.62.0. Silence
            # here is how the same store answered False there and True here in the same breath.
            problems.append(f"write receipts are DISABLED: {len(self.items)} record(s) exist with no "
                            f"attribution commitment, so there is nothing to verify -- which is not the "
                            f"same as verified")
        elif self.receipts_enabled and not self._receipts and self.items:
            problems.append(f"receipts are enabled but the chain is EMPTY while the store holds "
                            f"{len(self.items)} record(s) -- no attribution here is committed")
        elif uncommitted:
            problems.append(f"{len(uncommitted)} active record(s) carry no committed attribution and "
                            f"cannot be checked; an unverifiable label is not a verified one")
        # `ok` now requires that everything was CHECKABLE, not merely that nothing checked came back bad.
        return {"ok": chain_ok and not relabeled and not uncommitted and not problems,
                "chain_ok": chain_ok, "relabeled": relabeled, "uncommitted": sorted(set(uncommitted)),
                "missing": missing, "problems": problems}

    @staticmethod
    def _obj_sig(r: dict) -> str:
        """The supersession OBJECT signature: the explicit `object` value if set, else normalized text.
        Value-preserving paraphrases share the object; a verbatim-only fallback (text) matches MemStrata."""
        o = r.get("object")
        s = o if o is not None else r.get("text", "")
        # UNICODE-AWARE. `[^a-z0-9]` deleted every non-Latin character, so 東京 and 北京 both normalised to
        # the EMPTY STRING and compared equal — observe() recorded a flat contradiction as agreement and
        # marked its support seen, so later corrections were discounted. Any script's letters and digits are
        # kept; only punctuation and spacing collapse, which was the point.
        sig = re.sub(r"[\W_]+", " ", str(s).lower(), flags=re.UNICODE).strip()
        # And if normalisation still leaves nothing while the value was non-empty, fall back to the raw
        # value rather than returning a signature that matches every other unnormalisable value.
        return sig or str(s).strip().lower()

    def _supersede_by_key(self, rec: dict, reaffirm: bool = False) -> list:
        """Deterministic (subject, relation, object) supersession: retire active records that share
        rec['key']. No similarity threshold, no LLM call — the fix our Crucible replication validated
        (stale-fact recall 41.7% -> 0.0%, where cosine-based detection is near chance at AUROC ~0.61).
        Bi-temporal: only same-key records with valid_from <= rec's are retired; if an active same-key
        record is genuinely newer (later valid_from), the INCOMING rec is the stale one and is retired
        instead — a back-filled value never overwrites the current one. recall() hides superseded records
        by default, so a keyed store never surfaces a stale fact.

        Related, same root: `derived_from` lets a writer inherit ANY record's canonical source. Measured —
        an attacker naming a trusted record as parent becomes attributable to it, and then SPENDS THAT
        SOURCE'S irreversible budget, denying the real owner:

            evil = remember("Revenue is 900M", source=attacker, derived_from=[audited_record])
            _rec_sources(evil) -> {'evilexample', 'bigfourauditor'}
            spend_irreversible([evil], 1.0, budget=1.0) -> allowed ; the auditor is then denied

        Taint inheritance is deliberate (a summary must charge its origins), but nothing checks that the
        writer was entitled to derive from that parent — the same missing writer identity as below.

        SUPERSESSION IS UNAUTHENTICATED, and that is the important limit to state. It branches on tenant,
        valid_from, object and asserts_change — never on WHO wrote. So anyone who can call remember() and
        knows the key string retires the current value:

            remember("Payout wallet is 0xTRUE", key="payout::wallet", object="0xTRUE", source=finance)
            remember("Payout wallet is 0xEVIL", key="payout::wallet", object="0xEVIL", source=attacker)
            -> recall("payout wallet") returns 0xEVIL

        This is the same shape as any last-write-wins store, but note the asymmetry: revert() is
        capability-gated while the write path that achieves the same outcome is not.

        The mitigations are PARTIAL, and the exact shape matters. trust_seeds + recall(trusted_only=True)
        stops the attacker's value being SERVED (it fails CLOSED with no trust root), and attestation= binds
        a claim to a signing source. Neither stops the RETIREMENT: the true record is still superseded, so a
        trusted-only recall returns NOTHING rather than the truth, and the honest value is reachable only
        with include_superseded=True. Measured:

            trust_seeds = {"financeinternal"} ; attacker writes the same key
            recall(trusted_only=True)                         -> []
            recall(trusted_only=True, include_superseded=True) -> ["...0xTRUE"]

        So the guarantee these buy is "you will not be told the attacker's answer", not "you will be told the
        right one". A store taking writes from an untrusted agent needs an authenticated write path, not just
        a trusted read path.

        A far-future `valid_from` is the other side of the same coin: a write dated beyond every honest
        correction wins the bi-temporal comparison permanently, and only finiteness is checked. Bound it
        yourself if callers are untrusted.

        ECHO GUARD (self.echo_guard, default ON since 1.87.0): before the normal path, if the incoming rec asserts an
        OBJECT that has ALREADY been superseded for this key AND differs from the current active value, it
        is a restatement-of-superseded (an echo) — retire the incoming rec stale-on-arrival and keep the
        current value, so a later re-mention of the old value cannot resurrect it. reaffirm=True bypasses
        the guard (a genuine, authoritative reversal back to a previously-superseded value)."""
        _retired: list = []                    # ids this write retires; committed by its receipt
        if rec.get("status") == "provisional":
            # A record that is NOT authoritative cannot retire one that is. Measured while building
            # this: without the check, writing an unconfirmed "Tolkien was born in 1802" against the
            # key holding a verified 1892 marked the VERIFIED record `superseded` and left the
            # unconfirmed one invisible -- so recall returned NOTHING. A fabricated sentence would
            # have silently knocked out a real fact, which is strictly worse than not shipping the
            # feature. Supersession belongs at confirm(), the moment the value becomes authoritative.
            return _retired
        k = rec.get("key")
        if not k:
            return _retired
        vf_new = rec.get("valid_from", rec["ts"])
        tv = rec.get("tenant")                         # tenant isolation: only same-tenant records collide on a key
        # WHICH ROWS THIS KEY CAN COLLIDE WITH. Normally the handle's own (tenant- and ACL-scoped) view, so a
        # write can never retire a record the writer cannot see.
        #
        # THAT MATTERS MOST FOR AGENTS, and it is the write-side half of the read ACL. Supersession above is
        # documented as UNAUTHENTICATED: anyone who can call remember() and knows the key string retires the
        # current value. In a multi-agent store that would let agent B destroy agent A's current value by
        # guessing "payout::wallet", with no read access to it at any point -- a read ACL alone does not
        # close that, because the damage is done by writing. Resolving against the scoped view does.
        # MEASURED CONSEQUENCE, stated rather than left to be discovered: two agent-bound handles keying the
        # same string each keep their own active record, so from the OPERATOR view that key now has two
        # active values. Scoped writes buy write-isolation and pay for it in operator-side ambiguity.
        # Unscoped (operator) writes are untouched: keys stay global and last-write-wins.
        #
        # Access-control rows are the one exception:
        # they are deliberately invisible THROUGH an agent handle, so resolving them against `items` would
        # leave an agent unable to revoke its own grant -- the revoke would land beside the grant instead of
        # retiring it, and _acl_grants_for() would then read two disagreeing acts and fail closed forever.
        _pool = ([r for r in self._items if r.get("tenant") == tv] if k.startswith(_ACL_PREFIX)
                 else self.items)
        if self.echo_guard and not reaffirm:
            new_sig = self._obj_sig(rec)
            same_key = [r for r in _pool if r is not rec and r.get("key") == k and r.get("tenant") == tv]
            active = [r for r in same_key if r.get("status") == "active"]
            # OBJECT-LESS CLOBBER GUARD: on a key managed with explicit objects (a value ledger), a keyed
            # write carrying NO object cannot displace an object-bearing value — measured hole: a value-free
            # reversion utterance ("go back to the old one") keyed onto the ledger superseded the real value
            # with junk text (revert_by_reference_probe.py, B2 resistance 0.00 -> 1.00 with this guard).
            # Changing a ledgered value requires an explicit object, reaffirm=True, or revert(). Keys that
            # never used explicit objects (text-fallback legacy) are unaffected.
            if rec.get("object") is None and any(r.get("object") is not None for r in active):
                rec["status"] = "superseded"               # retired stale-on-arrival
                rec["superseded_ts"] = time.time()
                rec["invalidated_at"] = vf_new
                m = rec.setdefault("meta", {})
                m["objectless_blocked"] = True
                m["superseded_by_toggle"] = active[0]["id"]
                m["superseded_by_policy"] = "objectless_guard"
                return _retired
            superseded_sigs = {self._obj_sig(r) for r in same_key if r.get("status") == "superseded"}
            if (active and new_sig in superseded_sigs
                    and all(self._obj_sig(a) != new_sig for a in active)):
                rec["status"] = "superseded"           # the echo is retired on arrival
                rec["superseded_ts"] = time.time()
                rec["invalidated_at"] = vf_new
                m = rec.setdefault("meta", {})
                m["echo_blocked"] = True
                m["superseded_by_toggle"] = active[0]["id"]
                m["superseded_by_policy"] = "echo_guard"
                # TELL THE CALLER. remember() returns an id whether the write landed or was retired here,
                # so a legitimate reversal (A -> B -> A, third write true) silently left the store on B
                # while the call looked like a success. Measured: a KV round-trip through the LangGraph
                # adapter returns the previous value, an oscillating status flag ends inverted, and after
                # revert() the reverted-away value can never be written again -- one defect reached through
                # seven doors, all of them "a demoted write reported as a landed one".
                # route(), the other write path, already returns {"intent": "echo", "action": "blocked"}.
                # The signal existed; it just was not on the primary path. Now it is, without changing
                # remember()'s return type: `store.last_write` carries the verdict for the call just made.
                self.last_write = {
                    "id": rec["id"], "key": rec.get("key"), "status": "superseded",
                    "blocked": True, "policy": "echo_guard", "current_id": active[0]["id"],
                    "note": ("this asserted a value that was already superseded for this key, so it was "
                             "retired on arrival and the current value is unchanged. If the value has "
                             "genuinely returned, write it with reaffirm=True."),
                }
                return _retired                        # current value preserved; skip normal supersession
        # A record that does not ASSERT A CHANGE never retires anything. It is the store's only way to
        # tell "your address remains 742 Birchwood Lane, Unit 4A" (agreement, possibly at a different
        # granularity) from "actually it's Unit 3A now" (a correction). Without it, keying the echoes of
        # a value makes the echoes supersede each other and the current answer disappears from recall.
        if rec.get("meta", {}).get("asserts_change") is False:
            return _retired
        new_sig_r = self._obj_sig(rec)
        for r in _pool:
            if r is rec or r.get("status") != "active" or r.get("key") != k or r.get("tenant") != tv:
                continue
            # A RESTATEMENT IS NOT A SUPERSESSION. Last-write-wins used to retire any active same-key
            # record, including one asserting the SAME value, so "your title is Senior Data Analyst"
            # retired the sentence it agrees with and each key kept exactly one active record no matter
            # how often the value was confirmed. Measured cost (MemOps corpus, keying_recall.py): with
            # echoes keyed, current-value coverage in a top-20 recall fell 5/12 -> 3/12 — the store was
            # deleting its own evidence for the CURRENT answer. Supersession means replaced by a
            # DIFFERENT value; agreement reaffirms.
            # ...but a LITERAL duplicate is not a restatement either. Two differently-worded sentences
            # carrying one value are evidence worth keeping; the same text written twice under the same
            # key is one fact stored twice, and for a keyed KV caller it is simply a re-put. Keeping
            # both broke LangGraph's own checkpointer conformance suite
            # (test_put_writes_idempotent: "Expected exactly 1 write total, got 2"), because writing
            # the same write twice must leave one row.
            if self._obj_sig(r) == new_sig_r and (r.get("text") or "") != (rec.get("text") or ""):
                continue
            vf_r = r.get("valid_from", r["ts"])
            if vf_r <= vf_new:                 # r is the older value -> retire it
                r["status"] = "superseded"
                r["superseded_ts"] = time.time()
                r["invalidated_at"] = vf_new
                rm = r.setdefault("meta", {})
                rm["superseded_by_toggle"] = rec["id"]
                rm["superseded_by_policy"] = "keyed_reaffirm" if reaffirm else "keyed_lww"
                # WHAT THIS WRITE RETIRED, on the writing record, before its receipt is emitted.
                #
                # `status_sha256` binds the withheld/served class and deliberately folds `active` and
                # `superseded` together, to keep consolidation from churning the chain. An adversarial
                # pass found the hole in that within the hour: SWAP the two on disk and the store
                # serves the corrected-away value as current and hides the correction, while every
                # verifier reports clean. Resurrecting a retracted decision is the worst shape it takes.
                #
                # `retires` is a fact ABOUT THE WRITE, not about current state, so unlike a status it
                # binds for life and needs no amendment: this write DID retire those ids, permanently.
                # It therefore costs ZERO extra receipts, so the churn objection that ruled out
                # committing the raw status does not apply to it.
                #
                # This half is free. The CONCEALMENT half -- hiding the current record rather than
                # un-retiring an old one -- is not: it needs the retirement paths that have no newer
                # record to emit their own declaration, one receipt each. See _declare_retired.
                #
                # RETURNED, not written onto the record. The first version stored it as `rec["retires"]`
                # and a test caught the mistake within the run: it holds RANDOM SURROGATE IDS, so two
                # identically-built stores stopped comparing equal, and the record shape grew a field
                # that duplicates what the receipt says. A fact about the write belongs in the receipt.
                if r["id"] not in _retired:
                    _retired.append(r["id"])
            else:                              # an active same-key value is newer -> incoming is stale-on-arrival
                rec["status"] = "superseded"
                rec["superseded_ts"] = time.time()
                rec["invalidated_at"] = vf_r
                rm = rec.setdefault("meta", {})
                rm["superseded_by_toggle"] = r["id"]
                rm["superseded_by_policy"] = "keyed_lww_backfill"
        return _retired

    # ── candidate reconciliation queue (identity-confidence gate; Fellegi-Sunter clerical review / MDM steward
    #    queue, ported to agent memory). A fuzzy-identity keyed write forks a candidate instead of superseding;
    #    these three methods are the steward path that promotes or discards it. ────────────────────────────────
    def provisional(self, limit: int = 50) -> list:
        """Records written but NOT yet confirmed — the queue a verifier has to say yes or no to.

        WHY THIS EXISTS. yun520-1, on openclaw/openclaw#7707, on our measurement that a
        period-and-capital sentence splitter is 30% wrong on hard text and turns "J. R. R. Tolkien"
        into three fabricated claims:

            the splitter now gets to mint first-class records, which means the splitter is a trust
            boundary, and it is a worse one than the chunking it replaced (chunk boundaries are
            syntactic, sentence boundaries are semantic) ... the splitter must never be the sole
            authority that mints a record.

        So a writer that cannot vouch for what it produced writes `provisional=True`. The record is
        stored, keyed, lineage-stamped and auditable — it simply cannot be retrieved, by any flag,
        until something confirms it. "J." as a standalone fact never reaches a context window.

        This is NOT the `candidates()` queue. That one asks WHICH KEY a write belongs to (identity
        confidence below fork_below) and promoting one supersedes an authoritative value. This one
        asks whether the CONTENT is real, and confirming one supersedes nothing. Conflating them
        would let an unverified fragment take over a key on promotion.
        """
        out = [r for r in self._tenant_rows() if r.get("status") == "provisional"]
        out.sort(key=lambda r: -(r.get("ts") or 0))
        return [{"id": r["id"], "text": r.get("text"), "key": r.get("key"),
                 "derived_from": list(r.get("derived_from") or []), "ts": r.get("ts")}
                for r in out[:limit]]

    def confirm(self, mid: str, by: str = "") -> dict:
        """Confirm a provisional record: it becomes ordinary and retrievable. `by` names what vouched.

        The name is recorded because "confirmed" with no author is the same shape as an optional
        reason nobody fills — it reads as verification while asserting nothing. An unconfirmed
        record stays out of recall forever, which is the fail-closed direction: the cost of a
        forgotten confirmation is a missing memory, and the cost of the opposite is a fabricated one.
        """
        rec = next((r for r in self._tenant_rows()
                    if r["id"] == mid and r.get("status") == "provisional"), None)
        if rec is None:
            return {"confirmed": False, "reason": "no provisional record with that id"}
        rec["status"] = "active"
        rec["confirmed_by"] = (str(by).strip() or "unstated")[:120]
        rec["confirmed_at"] = time.time()
        # A CROSSING: withheld -> served. The amendment is what separates this confirmation from an
        # attacker editing the same two fields on disk -- and it puts WHO vouched, and why, in the
        # chain rather than only in the record they could rewrite.
        self._emit_write_receipt(rec, amends=("status_sha256",),
                                 reason=f"confirmed by {rec['confirmed_by']}")
        self._save(force=True)
        return {"confirmed": True, "id": mid, "by": rec["confirmed_by"]}

    def discard_provisional(self, mid: str, basis: str = "") -> dict:
        """Reject a provisional record — the verifier saying no, which is the point of the queue."""
        rec = next((r for r in self._tenant_rows()
                    if r["id"] == mid and r.get("status") == "provisional"), None)
        if rec is None:
            return {"discarded": False, "reason": "no provisional record with that id"}
        rec["status"] = "discarded"
        rec["discard_basis"] = (str(basis).strip() or "unstated")[:200]
        self._save(force=True)
        return {"discarded": True, "id": mid}

    def candidates(self, key: str | None = None) -> list:
        """The reconciliation queue: forked candidate records awaiting an identity decision (writes whose
        identity_confidence fell below fork_below, so they did NOT supersede). Each entry shows what it WOULD
        change: the proposed key, the candidate's value/text, its confidence, and the CURRENT authoritative
        value it would replace if promoted. Tenant-scoped when bound. Read-only.

        Returns a list of {id, candidate_key, object, text, identity_confidence, current: {id, object, text} | None}."""
        tv = self.tenant
        out = []
        for r in self.items:
            if r.get("status") != "candidate":
                continue
            if self.tenant is not None and r.get("tenant") != tv:
                continue
            ck = r.get("candidate_key")
            if key is not None and ck != str(key):
                continue
            cur = next((a for a in self.items if a.get("key") == ck and a.get("status") == "active"
                        and a.get("tenant") == r.get("tenant")), None)
            out.append({"id": r["id"], "candidate_key": ck, "object": r.get("object"),
                        "text": r.get("text"), "identity_confidence": r.get("identity_confidence"),
                        "current": ({"id": cur["id"], "object": cur.get("object"), "text": cur.get("text")}
                                    if cur else None)})
        return out

    def promote_candidate(self, cid: str, capability: str | None = None) -> dict:
        """STEWARD DECISION: accept a candidate's identity. It becomes the authoritative value for its key and
        supersedes the prior active same-key value (a confirmed correction). Because promoting a fuzzy match
        INTO the authoritative interval is exactly the write the gate was protecting, it takes the same
        capability as revert()/reaffirm when a revert authority is configured (else the content path could
        launder a fuzzy match to authority by promoting it). Returns {promoted, key, superseded:[ids]}."""
        rec = next((r for r in self.items if r["id"] == cid and r.get("status") == "candidate"), None)
        if rec is None:
            raise KeyError(f"no candidate with id {cid}")
        if self.tenant is not None and rec.get("tenant") != self.tenant:
            raise KeyError(f"no candidate with id {cid}")     # tenant isolation
        ck = rec.get("candidate_key")
        if (self.revert_authority is not None or self.revert_pubkey is not None) \
                and capability is not _SANCTIONED and not self._revert_authorized(ck, capability):
            raise PermissionError("promote_candidate requires a valid capability (revert authority is set)")
        before = [r["id"] for r in self.items if r.get("key") == ck and r.get("status") == "active"
                  and r.get("tenant") == rec.get("tenant")]
        rec["status"] = "active"
        rec["key"] = ck
        rec.pop("candidate_key", None)
        rec.setdefault("meta", {})["promoted_from_candidate"] = True
        self._emit_write_receipt(rec, amends=("status_sha256",),   # crossing: withheld -> served
                                 reason=f"candidate promoted to the authoritative value for {ck}")
        self._supersede_by_key(rec)                            # now retires the prior authoritative value
        self._save(force=True)
        after = {r["id"] for r in self.items if r.get("key") == ck and r.get("status") == "active"}
        return {"promoted": cid, "key": ck, "superseded": [i for i in before if i not in after]}

    def discard_candidate(self, cid: str, basis: str | None = None) -> dict:
        """STEWARD DECISION: reject a candidate (wrong identity / spurious). It is retired without ever touching
        the authoritative value. Returns {discarded}."""
        rec = next((r for r in self.items if r["id"] == cid and r.get("status") == "candidate"), None)
        if rec is None:
            raise KeyError(f"no candidate with id {cid}")
        if self.tenant is not None and rec.get("tenant") != self.tenant:
            raise KeyError(f"no candidate with id {cid}")
        rec["status"] = "superseded"
        rec["superseded_ts"] = time.time()
        m = rec.setdefault("meta", {})
        m["superseded_by_policy"] = "candidate_discarded"
        self._emit_write_receipt(rec, amends=("status_sha256",),   # crossing: withheld -> served
                                 reason="candidate rejected: " + (basis or "unstated"))
        if basis:
            m["discard_basis"] = basis
        self._save(force=True)
        return {"discarded": cid}

    def _current_active(self, key: str):
        tv = self.tenant
        return next((r for r in self.items if r.get("key") == str(key) and r.get("status") == "active"
                     and (tv is None or r.get("tenant") == tv)), None)

    @staticmethod
    def _support_sig(s) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()

    @staticmethod
    def _as_list(x):
        return list(x) if isinstance(x, (list, tuple, set)) else [x]

    def support_challenge_for(self, key: str, toward) -> str:
        """The exact message an attesting source signs to corroborate an observe() contradiction of `key`'s
        current value toward `toward` (None = value-obscuring revert). Mirrors revert_challenge: it binds the
        CURRENT active record id and the tenant, so a captured signature cannot be replayed after the value
        legitimately changes and changes back (cross-time) or across tenants sharing one allowlist. Surface this
        to the signer; sign_support() signs it."""
        cur = self._current_active(key)
        cur_id = cur["id"] if cur else ""
        return "support:" + _sha256_hex(_canon({
            "key": str(key), "toward": (toward if toward is not None else "__revert__"),
            "cur": cur_id, "tenant": self.tenant or ""}))

    def _verify_support(self, pubkey_hex, sig_hex, challenge: str) -> bool:
        """A signed support ground counts only if its key is allowlisted AND its Ed25519 signature verifies over
        the current, tenant-and-record-bound challenge. The store verifies but can never mint it."""
        if not self._support_pubkeys or pubkey_hex not in self._support_pubkeys:
            return False
        if not _HAVE_ED:
            raise RuntimeError("verifying a signed support ground needs the `cryptography` package")
        try:
            _Ed25519PK.from_public_bytes(bytes.fromhex(pubkey_hex)).verify(
                bytes.fromhex(sig_hex), challenge.encode())
            return True
        except Exception:
            return False

    def _verified_support_classes(self, support, key, toward) -> set:
        """The set of DISTINCT PROVENANCE CLASSES that validly signed THIS contradiction (bound to the current
        record + tenant). Self-minted keys/strings count zero (Sybil resistance relative to the allowlist), and
        keys declared to share a class collapse to one — so the threshold counts independent-ish SOURCES, not raw
        keys. Items are (pubkey_hex, sig_hex). Honest limit: 'class' is a DECLARED grouping by whoever curates
        the allowlist; the store enforces it but cannot verify two classes are truly causally independent."""
        challenge = self.support_challenge_for(key, toward)
        out = set()
        for item in self._as_list(support):
            if isinstance(item, (tuple, list)) and len(item) == 2:
                pk, sg = item
                if self._verify_support(pk, sg, challenge):
                    out.add(self._support_class.get(pk, pk))
        return out

    def remember_decision(self, decision: str, because: str | None = None, context: str | None = None,
                          topic: str | None = None, tags=None, value: float = 2.0,
                          capability: str | None = None, source=None, derived_from=None,
                          project: str | None = None,
                          user_id: str | None = None, agent_id: str | None = None,
                          session_id: str | None = None) -> str:
        """Capture a DECISION — the memory that actually matters and that a raw event-log misses. A coding/agent
        session logging only commands + file-states records the MECHANICS but not the CONCLUSIONS ("we decided X
        because Y"), so recall can't answer "what did we decide / send / choose". This stores the decision as a
        durable (procedural-decay), higher-value memory, with its rationale (`because`) and situation (`context`)
        kept in meta for retrieval.

        `topic` (recommended) becomes a deterministic supersession key `decision::<topic>` — so a NEW decision on
        the same topic RETIRES the old one (inspeximus's keyed supersession: recall always returns the CURRENT decision,
        the reversal is a ledgered/attributable event, and `revert('decision::<topic>')` restores the prior one).
        This is inspeximus's integrity moat applied to decisions — something an LLM-extracted fact store cannot do:
        decisions stay current, correctable, revertible, and auditable, with NO LLM and NO similarity guesswork.

        This is the DETERMINISTIC half of decision capture (the caller/agent states the decision). The OPTIONAL
        LLM half — distilling decisions out of a raw transcript automatically, the way mem0/Zep extract facts on
        write — is `distill_and_remember()` (you choose whether to pay an LLM; the store/correction/erasure stays
        deterministic). Returns the new memory id."""
        text = "DECISION: " + decision.strip()
        if because:
            text += " — because: " + because.strip()
        md = {"kind": "decision"}
        if because:
            md["rationale"] = because.strip()
        if context:
            md["context"] = context.strip()
        key = ("decision::" + topic.strip()) if topic else None
        # `object` is the DECISION, not the topic. The topic is already the KEY; passing it as the value
        # too made every decision on a topic look like a restatement of the same value, so keyed
        # supersession -- which is object-identity aware precisely so a paraphrase does not count as a
        # correction -- treated the second decision as a reaffirm and retired nothing. Measured: two
        # decisions on one topic left TWO active records, while plain remember(key=...) left one. Every
        # sentence of the docstring above ("a NEW decision RETIRES the old one", "recall always returns
        # the CURRENT decision", "revert restores the prior one") described behaviour that did not happen.
        # user/agent/session pass through to the memory hierarchy. `session_id` in particular: a decision
        # is the single most important thing a session digest carries, and without the stamp
        # close_session() cannot tell WHICH session recorded it and has to fall back to a time window.
        # remember() took the triple from the start; this wrapper silently dropped it.
        return self.remember(text, tags=(list(tags) if tags else []) + ["decision"], value=value,
                             mtype="procedural", key=key, object=(decision.strip() or None),
                             meta=md, capability=capability, project=project,
                             source={"doc": source} if isinstance(source, str) and source else source,
                             derived_from=derived_from or None,
                             user_id=user_id, agent_id=agent_id, session_id=session_id)

    # The extraction contract for distill_and_remember (the OPTIONAL LLM capture half). A caller's distiller feeds
    # this prompt + the raw text to any LLM and returns the parsed JSON list. inspeximus owns the STRUCTURE (extract ->
    # remember with keyed supersession) + this spec; the LLM only proposes what to keep. Analogous to mem0's
    # FACT_RETRIEVAL_PROMPT, but the distilled items land in inspeximus's deterministic, correctable, revertible store.
    DISTILL_PROMPT = (
        "You distill a conversation/transcript into the few memories worth keeping. Extract ONLY durable, "
        "reusable items; drop chit-chat, transient state, and anything already obvious. Return a JSON object "
        "{\"items\": [...]} where each item is:\n"
        "  {\"kind\": \"decision\"|\"fact\", \"text\": <one clear sentence>, \"topic\": <short stable slug or \"\">, "
        "\"because\": <rationale, only for decisions, else \"\">, "
        "\"support\": <a SHORT verbatim quote (>=12 chars) copied EXACTLY from the transcript that grounds this item>}\n"
        "- kind=\"decision\": a choice/conclusion/plan (\"we decided/chose/dropped/will…\"). Give a `topic` slug so "
        "a later decision on the same topic supersedes it (e.g. \"release::v2\", \"vendor::db\").\n"
        "- kind=\"fact\": a durable fact/preference/detail worth recalling later.\n"
        "- `support` MUST be an exact substring of the transcript (do NOT paraphrase it). Items whose `support` is not "
        "found verbatim in the transcript are DROPPED — never invent a quote to pass this check.\n"
        "Return {\"items\": []} if nothing is worth keeping. No prose outside the JSON."
    )

    @staticmethod
    def _support_ok(support, text: str) -> bool:
        """Correctness gate: an extracted item is kept ONLY if its `support` quote appears VERBATIM in the source
        transcript (case-insensitive, whitespace-collapsed, >=12 non-space chars). This is the deterministic guard
        that stops a hallucinated decision — a plausible sentence the LLM invented but that was never said — from
        landing in the durable store and inverting the correction moat. No LLM, no similarity: pure substring."""
        s = " ".join(str(support or "").split()).lower()
        if len(s.replace(" ", "")) < 12:
            return False
        return s in " ".join(str(text or "").split()).lower()

    def distill_and_remember(self, text: str, distiller, source: dict | None = None,
                             require_support: bool = True) -> dict:
        """OPTIONAL LLM capture: turn a raw conversation/transcript into the few memories worth keeping — the
        auto-capture-what-matters that a raw event log misses and that mem0/Zep do with an LLM on the write path.
        inspeximus stays zero-dependency/zero-LLM in its CORE: YOU inject `distiller`, a callable `distiller(prompt, text)
        -> str|dict|list` that runs any LLM (or a subagent) with `Inspeximus.DISTILL_PROMPT` and returns the JSON (a
        raw string is parsed here; a dict/list is accepted directly). Each extracted item is then stored
        DETERMINISTICALLY: a `decision` via remember_decision() (durable, with `topic`-keyed supersession + revert),
        a `fact` via remember() (semantic). So the LLM only proposes WHAT to keep; the store/correction/erasure/
        supersession stay deterministic and auditable — the trust layer never depends on the LLM.

        Fail-open: a distiller error or a malformed item is skipped, never crashes the call. Returns
        {captured, decisions, facts, ids}."""
        import json as _json
        try:
            raw = distiller(Inspeximus.DISTILL_PROMPT, text)
        except Exception:
            return {"captured": 0, "decisions": 0, "facts": 0, "ids": [], "error": "distiller_failed"}
        items = raw
        if isinstance(raw, str):
            try:
                s = raw.strip()
                if "```" in s:                                   # tolerate ```json fenced output
                    s = s.split("```")[1].lstrip("json").strip() if s.count("```") >= 2 else s
                obj = _json.loads(s)
                items = obj.get("items", obj) if isinstance(obj, dict) else obj
            except Exception:
                return {"captured": 0, "decisions": 0, "facts": 0, "ids": [], "error": "unparseable_distiller_output"}
        if isinstance(items, dict):
            items = items.get("items", [])
        ids, nd, nf, dropped, errors = [], 0, 0, 0, []
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            t = str(it.get("text") or "").strip()
            if not t:
                continue
            if require_support and not self._support_ok(it.get("support"), text):
                dropped += 1                                  # unsupported/hallucinated item -> never stored
                continue
            topic = str(it.get("topic") or "").strip() or None
            try:
                if str(it.get("kind") or "").lower() == "decision":
                    ids.append(self.remember_decision(t, because=(it.get("because") or None), topic=topic)); nd += 1
                else:
                    ids.append(self.remember(t, mtype="semantic", tags=["distilled"],
                                             key=("fact::" + topic) if topic else None,
                                             object=topic, source=source)); nf += 1
            except Exception as e:
                # COUNTED AND NAMED, not swallowed. This bare `continue` turned every failed write into
                # silence: pass a `source` in the wrong shape and remember() raises "must be a dict, got
                # str" for every item, while this returned {captured: 0, dropped: 0, ids: []} -- which
                # reads as "the transcript had nothing worth keeping" when the truth is "everything was
                # extracted and then thrown away". Measured, found by a caller (me) passing a string.
                # `dropped` alone would still not say WHY, and the two reasons are opposite: an
                # unsupported item is the guard working, an errored one is the caller's bug.
                errors.append(f"{type(e).__name__}: {str(e)[:120]}")
                continue
        out = {"captured": len(ids), "decisions": nd, "facts": nf, "dropped": dropped, "ids": ids}
        if errors:
            out["errors"] = errors
            out["failed"] = len(errors)
        return out

    def observe(self, text: str, key: str, object: str | None = None, support=None,
                meta: dict | None = None) -> dict:
        """READ-PATH contradiction check (marintkael's mirror of the Fellegi-Sunter clerical-review band, r/RAG
        2026-07-16). Ingest an OBSERVATION (evidence, NOT an authoritative write) about `key` that CONTRADICTS the
        current high-confidence settled value. It NEVER writes an authoritative value — it can only REOPEN a
        settled record for steward review (reopened()/resolve_reopened); the record stays 'active' so recall()
        still returns it. Catches the confident wrong-merge write-time can't, and gives the value-obscuring revert
        something to key on. It does NOT decide the reopened case — legit-vs-injected still needs authority.

        TWO keying modes:
        * SUPPORT-KEYED (pass `support`, marintkael's fix 2026-07-16) — this is justification-based truth
          maintenance (Doyle 1979 JTMS: a node's belief is a function of its SUPPORT set, not the proposition, and
          a relabel fires when a NEW justification arrives; de Kleer 1986 ATMS: distinct minimal-environment
          labels = 'distinct novel supports'; Dung 1995 reinstatement: an argument reopens only when a NEW
          attacker/defender enters). Key reopen on NOVELTY-OF-SUPPORT, not on
          value. A restatement whose grounds the ledger has already seen (or that carries no support) is an ECHO
          -> silenced, even though it contradicts the current value; only a contradiction resting on grounds NOT
          in the record's justification set reopens. So replay collapses into the echo case BY CONSTRUCTION (same
          value, same stale support) and the value-disagreement DoS lever falls off, while an honest late
          correction that brings NEW ground still gets through. Corroboration counts DISTINCT novel support
          signatures (splitting one intent into two emissions shares support -> one sig -> does not corroborate),
          which is independence measured at the support level. HONEST LIMIT: novelty-of-support is itself a
          provenance judgement pushed one level down, not certified — reopened() stays a queue, not a resolution.
        * VALUE-KEYED (omit `support`, legacy 1.9.2): reopen on a value-contradiction corroborated by >=
          reopen_corroboration observations. Kept byte-identical for existing callers.

        Returns {reopened, key, pending, need, surfaced_prior, review_id, ...}."""
        cur = self._current_active(key)
        if cur is None:
            return {"reopened": False, "key": str(key), "pending": 0, "need": self.reopen_corroboration,
                    "surfaced_prior": None, "review_id": None, "no_current": True}
        ic = cur.get("identity_confidence")            # only HIGH-confidence settled records are guarded
        if not (ic is None or ic >= self.fork_below):
            return {"reopened": False, "key": str(key), "pending": 0, "need": self.reopen_corroboration,
                    "surfaced_prior": None, "review_id": None, "low_confidence": True}
        m = cur.setdefault("meta", {})
        agrees = object is not None and self._obj_sig({"object": object, "text": text}) == self._obj_sig(cur)
        if agrees:
            if support is not None:                     # an agreeing observation's grounds are now 'seen'
                seen = set(m.get("_support_seen", []))
                now = (self._verified_support_classes(support, key, object) if self.support_authorities is not None
                       else {self._support_sig(s) for s in self._as_list(support)})
                m["_support_seen"] = list(seen | now)
                self._save(force=True)
            return {"reopened": False, "key": str(key), "pending": 0, "need": self.reopen_corroboration,
                    "surfaced_prior": None, "review_id": None, "agreed": True}
        prior = self._latest_superseded_object(key, cur)
        if support is not None and self.support_authorities is not None:
            # SIGNED-GROUNDS (marintkael round 3): only a ground signed by a DISTINCT allowlisted authority over
            # support_challenge(key, toward) corroborates; the fabricated-grounds attack moves from 'mint two
            # strings' to 'forge two signatures under keys you do not hold'.
            seen = set(m.get("_support_seen", []))
            verified = self._verified_support_classes(support, key, object)
            novel = verified - seen
            m["_support_seen"] = list(seen | verified)
            if not novel:
                self._save(force=True)
                return {"reopened": False, "key": str(key), "pending": len(verified),
                        "need": self.reopen_corroboration, "surfaced_prior": prior, "review_id": None,
                        "echo": True, "verified_grounds": len(verified)}
            vsig = self._obj_sig({"object": object, "text": text}) if object is not None else "__revert__"
            nov = m.setdefault("_reopen_support", {})
            accrued = set(nov.get(vsig, [])) | novel
            nov[vsig] = list(accrued)
            self._save(force=True)
            if len(accrued) >= self.reopen_corroboration:
                return self._do_reopen(cur, prior, "signed_support_contradiction", object, meta)
            return {"reopened": False, "key": str(key), "pending": len(accrued),
                    "need": self.reopen_corroboration, "surfaced_prior": prior, "review_id": None}
        if support is not None:
            # SUPPORT-KEYED (string): an echo (no novel grounds) is silenced even though it disagrees on value.
            seen = set(m.get("_support_seen", []))
            sigs = {self._support_sig(s) for s in self._as_list(support) if self._support_sig(s)}
            novel = sigs - seen
            m["_support_seen"] = list(seen | sigs)      # discount all grounds now seen
            if not novel:
                self._save(force=True)
                return {"reopened": False, "key": str(key), "pending": 0, "need": self.reopen_corroboration,
                        "surfaced_prior": prior, "review_id": None, "echo": True}
            vsig = self._obj_sig({"object": object, "text": text}) if object is not None else "__revert__"
            nov = m.setdefault("_reopen_support", {})
            accrued = set(nov.get(vsig, [])) | novel    # DISTINCT novel grounds only (independence at support level)
            nov[vsig] = list(accrued)
            self._save(force=True)
            if len(accrued) >= self.reopen_corroboration:
                return self._do_reopen(cur, prior, "novel_support_contradiction", object, meta)
            return {"reopened": False, "key": str(key), "pending": len(accrued),
                    "need": self.reopen_corroboration, "surfaced_prior": prior, "review_id": None}
        # VALUE-KEYED (legacy 1.9.2): value-obscuring revert reopens on first sight; named contradiction is gated
        if object is None:
            return self._do_reopen(cur, prior, "value_obscuring_revert", None, meta)
        sig = self._obj_sig({"object": object, "text": text})
        contra = m.setdefault("_reopen_contra", {})
        contra[sig] = int(contra.get(sig, 0)) + 1
        self._save(force=True)
        if contra[sig] >= self.reopen_corroboration:
            return self._do_reopen(cur, prior, "corroborated_contradiction", object, meta)
        return {"reopened": False, "key": str(key), "pending": contra[sig],
                "need": self.reopen_corroboration, "surfaced_prior": prior, "review_id": None}

    def _latest_superseded_object(self, key: str, cur: dict):
        tv = cur.get("tenant")
        sup = [r for r in self.items if r.get("key") == str(key) and r.get("status") == "superseded"
               and r.get("tenant") == tv and self._obj_sig(r) != self._obj_sig(cur)]
        sup.sort(key=lambda r: r.get("superseded_ts", r.get("ts", 0)))
        return sup[-1].get("object") if sup else None

    def _flag_contested(self, rec: dict, reason: str, contra_object, meta) -> None:
        """Mark a record CONTESTED from the consolidation path, without the observe() flow's side effects.

        `_do_reopen` belongs to `observe()`, where an accrual has just reached its threshold and is being
        consumed -- which is why it pops `_reopen_contra`/`_reopen_support` and force-saves. Called from
        `consolidate()` those are both wrong: the pop destroys an accrual another party is part-way
        through (measured: one attacker write reset a 1-of-2 observation), and the force-save fires per
        pair inside an O(n^2) loop. This sets the flag and nothing else; the pass saves once at the end."""
        m = rec.setdefault("meta", {})
        rec["reopened"] = True
        rec["reopened_ts"] = time.time()
        m["reopened_reason"] = reason
        m.setdefault("reopened_surfaced_prior", None)
        if contra_object is not None:
            m["reopened_contradiction"] = contra_object
        if meta:
            m.setdefault("reopened_meta", {}).update(meta)
        self._dirty = True

    def _do_reopen(self, cur: dict, prior, reason: str, contra_object, meta) -> dict:
        m = cur.setdefault("meta", {})
        # flag, NOT a status change: the record stays 'active' so recall() still returns it as the current best
        # guess (an agent left with nothing is worse), it is only surfaced by reopened() for steward review.
        cur["reopened"] = True
        cur["reopened_ts"] = time.time()
        m["reopened_reason"] = reason
        m["reopened_surfaced_prior"] = prior
        if contra_object is not None:
            m["reopened_contradiction"] = contra_object
        if meta:
            m.setdefault("reopened_meta", {}).update(meta)
        m.pop("_reopen_contra", None)
        m.pop("_reopen_support", None)
        self._save(force=True)
        return {"reopened": True, "key": cur.get("key"), "pending": self.reopen_corroboration,
                "need": self.reopen_corroboration, "surfaced_prior": prior, "review_id": cur["id"]}

    def reopened(self, key: str | None = None) -> list:
        """The POST-write review queue: settled records reopened because corroborated evidence contradicted them
        (the mirror of candidates(), which holds a match BEFORE the write). Each entry shows the still-current
        value, why it reopened, and the prior value offered to reaffirm. Read-only, tenant-scoped."""
        tv = self.tenant
        out = []
        for r in self._tenant_rows():
            if not r.get("reopened") or r.get("status") != "active":
                continue
            if tv is not None and r.get("tenant") != tv:
                continue
            if key is not None and r.get("key") != str(key):
                continue
            m = r.get("meta", {})
            out.append({"id": r["id"], "key": r.get("key"), "object": r.get("object"), "text": r.get("text"),
                        "reason": m.get("reopened_reason"), "surfaced_prior": m.get("reopened_surfaced_prior"),
                        "contradiction": m.get("reopened_contradiction"),
                        # WHICH record contested this one. Without it a steward facing N entries from one
                        # unverified source has N investigations instead of one filter.
                        "contested_by": (m.get("reopened_meta") or {}).get("contested_by")})
        return out

    def resolve_reopened(self, rid: str, decision: str, capability: str | None = None) -> dict:
        """STEWARD DECISION on a reopened interval. decision='keep_current' clears the flag (false alarm ->
        status back to active). decision='reaffirm_prior' restores the surfaced prior value via the authorized
        revert path (remember(reaffirm=True)) — so it takes the revert capability when one is configured, exactly
        like promote_candidate (the content path must not launder a restore to authority). Returns a summary."""
        rec = next((r for r in self.items if r["id"] == rid and r.get("reopened")
                    and r.get("status") == "active"), None)
        if rec is None:
            raise KeyError(f"no reopened record with id {rid}")
        if self.tenant is not None and rec.get("tenant") != self.tenant:
            raise KeyError(f"no reopened record with id {rid}")
        if decision == "keep_current":
            rec.pop("reopened", None)
            rec.pop("reopened_ts", None)
            m = rec.get("meta", {})
            for kk in ("reopened_reason", "reopened_surfaced_prior", "reopened_contradiction", "reopened_meta"):
                m.pop(kk, None)
            self._save(force=True)
            return {"resolved": rid, "decision": "keep_current", "key": rec.get("key")}
        if decision == "reaffirm_prior":
            prior = rec.get("meta", {}).get("reopened_surfaced_prior")
            if prior is None:
                raise ValueError("no surfaced prior value to reaffirm")
            key = rec.get("key")
            rec.pop("reopened", None)                       # unflag; the reaffirm write will supersede it
            # derived from the reopened record the prior value was surfaced from -- same principle as
            # revert: a call site the store owns, where the parent is known rather than inferred.
            new_id = self.remember(f"the {key} is {prior}", key=key, object=prior, reaffirm=True,
                                   capability=capability, derived_from=[rid])
            return {"resolved": rid, "decision": "reaffirm_prior", "key": key, "reaffirmed_object": prior,
                    "new_id": new_id}
        raise ValueError("decision must be 'keep_current' or 'reaffirm_prior'")

    def remember_dedup(self, text: str, tags=None, value: float = 1.0, meta: dict | None = None,
                       mtype: str | None = None, dup_threshold: float = 0.95) -> str:
        """OPT-IN write that skips redundant appends. If an active memory is near-identical (similarity >=
        dup_threshold) AND carries the SAME value(s) (no numeric clash), this returns that memory's id WITHOUT
        appending a duplicate raw row -- cutting raw-store bloat from repeated identical writes. A near-identical
        text with a DIFFERENT number (a value UPDATE) is NOT a duplicate: it appends, so the consolidation pass can
        supersede the stale value. Default `remember()` stays strictly append-only (the 'zero rewrites' contract);
        this is a separate opt-in path for high-duplicate ingest."""
        hits = self.recall(text, k=1)
        if hits:
            h = hits[0]
            s = self._similarity(text, h, self._qvec(text) if self.embed else None)
            if s >= dup_threshold and not _value_clash(text, h["text"]):
                return h["id"]            # NO-OP: near-identical, same value -> skip the redundant append
        return self.remember(text, tags=tags, value=value, meta=meta, mtype=mtype)

    def forget(self, ids=None, where=None, redact_links: bool = True,
               request_id: str | None = None, basis: str | None = None,
               authorized_by: str | None = None, authorization: str | None = None,
               dry_run: bool = False,
               verify_residue_in: str | None = None, residue_values=None) -> dict:
        """HARD-DELETE memories — the one operation that genuinely REMOVES content. inspeximus is otherwise
        append-only: supersession / invalidation only DEMOTE a record (it still exists, recallable with
        include_superseded). forget() is for the cases where demotion is not enough: a right-to-be-forgotten
        / erasure request, a poisoned or libellous memory, or a hard correction.

        Select by `ids` (a single id or an iterable) and/or `where` (a predicate fn(record)->bool; e.g.
        lambda r: 'secret' in r['text']). VERIFIED FORGETTING: the matched records are deleted AND their ids
        are scrubbed from every surviving record's `links` and toggle-supersession pointers, and the cached
        vec matrix + token caches are dropped — so a forgotten memory cannot resurface via recall, via a
        consolidation link, or via a stale derived-summary pointer. This is complete because consolidation
        never copies raw text into other records (it only links ids and toggles status) — there is no merged
        blob left holding the forgotten content.

        EVERY deletion emits a hash-chained, content-free tombstone, exactly like forget_subject() and
        forget_pii(). Until 1.24.0 only those two did, which meant a record removed through this method left
        the store accusing ITSELF: verify_writes() found a write receipt whose record was gone with nothing
        accounting for it, and reported "deleted out-of-band" — the signature of tampering — after a
        perfectly legitimate API call. `request_id` and `basis` are carried into the tombstone's committed
        hash so an auditor can see why a record went, not merely that it did.

        `dry_run=True` PREVIEWS instead of deleting: it returns {would_forget, ids, sample, dry_run:True}
        (a few matched texts so you can eyeball what the selector caught) and touches NOTHING — no delete, no
        tombstone, no save. The safety valve on the one irreversible operation: review a bulk `where` match
        before you commit it.

        Returns {forgotten, ids, scrubbed_links, tombstones} (or the dry_run preview above)."""
        target = set()
        if ids is not None:
            target |= ({ids} if isinstance(ids, str) else set(ids))
        if where is not None:
            for r in self._tenant_rows():
                try:
                    if where(r):
                        target.add(r["id"])
                except Exception as e:
                    # A predicate that RAISED on one record used to be skipped silently, so an erasure that
                    # had not examined every record returned the success shape of one that had. On a deletion
                    # path a partial sweep must never look complete.
                    raise ValueError(
                        f"forget(where=...) raised on record {r['id']}: {type(e).__name__}: {e}. Refusing "
                        f"to delete a partial match — an erasure that skipped records is not an erasure."
                    ) from None
        # Scope the SELECTION to this tenant, then delete from the shared list. Until 1.54.0 this filtered
        # against self.items, so a tenant view could pass another tenant's id and hard-delete it —
        # `beta.forget([acme_id])` returned {'forgotten': 1} and acme's row was gone. Ids not visible to this
        # tenant are dropped exactly like ids that do not exist, so the call cannot probe for them either.
        target &= {r["id"] for r in self._tenant_rows()}
        if dry_run:
            by_id = {r["id"]: r for r in self._tenant_rows()}
            sample = [{"id": t, "text": (by_id[t].get("text") or "")[:120], "key": by_id[t].get("key")}
                      for t in sorted(target)[:10]]
            return {"would_forget": len(target), "ids": sorted(target), "sample": sample, "dry_run": True}
        if not target:
            # `coverage` belongs here for the same reason `residue_in_store` does, and it was the one
            # early return still missing it: a caller who has to check whether the field exists before
            # reading it will eventually read its absence as "nothing to report".
            return {"forgotten": 0, "ids": [], "scrubbed_links": 0, "tombstones": 0,
                    "coverage": self._erasure_coverage(None, len(getattr(self, "_erasure_targets", []))),
                    "residue_in_store": {"ok": True, "checked_records": 0, "searched_values": 0, "findings": [], "problems": [], "method": "nothing was erased, so there is nothing that could be left over"}}
        # Capture what we are about to destroy, ONLY if the caller asked for a residue check. After the
        # rows are gone the values are gone with them, so this is the one moment it can be done at all --
        # which is why a residue check bolted on afterwards can never work. Held in a local, written
        # nowhere, and dropped when this call returns.
        _residue_values = list(residue_values or [])
        # Captured UNCONDITIONALLY now, not only when a cross-store scan was requested. The in-store
        # check below needs the same values, and this is the single instant they exist: once the rows
        # go the values go with them, and the tombstone is content-free by design. Held in a local,
        # written nowhere, dropped when this call returns.
        for r in self._items:
            if r["id"] in target:
                for field in ("text", "object"):
                    v = r.get(field)
                    if isinstance(v, str) and v.strip():
                        _residue_values.append(v)
        self._items = [r for r in self._items if r["id"] not in target]
        scrubbed = 0
        if redact_links:
            for r in self.items:
                if r.get("links"):
                    before = len(r["links"])
                    r["links"] = [l for l in r["links"] if l not in target]
                    scrubbed += before - len(r["links"])
                meta = r.get("meta")
                if meta and meta.get("superseded_by_toggle") in target:
                    meta.pop("superseded_by_toggle", None)   # drop dangling toggle pointer (no ghost stale-derived)
                if meta and meta.get("rederived_to") in target:
                    # rederive() treats this as a single-shot "already corrected" guard. If the rederived copy
                    # is erased, a live pointer to it locks the record on its KNOWN-WRONG value forever and
                    # rederive returns 0/0 with no note. Only BEHAVIOUR-gating pointers are dropped here;
                    # the history fields (derived_from, taint, revert_of, rederived_from, duplicate_of,
                    # resolved_over) are deliberately KEPT — erasure_audit reports them as dangling_lineage,
                    # and scrubbing them would delete the evidence and make the audit read clean.
                    meta.pop("rederived_to", None)
        for tid in target:
            self._tok_cache.pop(tid, None)
            self._sig_cache.pop(tid, None)
        now = time.time()
        for tid in sorted(target):                           # deterministic order -> reproducible chain
            self._emit_tombstone(tid, now, request_id, basis=basis or "forget",
                                 authorized_by=authorized_by, authorization=authorization, defer=True)
        # ONE sidecar write for the whole batch. Each _emit_tombstone used to rewrite the ENTIRE chain, so
        # erasing k records cost k rewrites of a chain growing to k -- O(k^2) serialization plus k atomic
        # replaces. Deferring is also strictly SAFER on a crash: the old order left j-of-k tombstones on
        # disk claiming erasures the store save had not yet performed, i.e. a deletion proof for records
        # still present. Now it is all-or-nothing, and still written BEFORE _save, so a crash can only lose
        # the proof of a deletion that did not happen -- never the reverse.
        if target:
            self._flush_tombstones()
        self._mat = None; self._mat_built_n = -1             # force vec-matrix rebuild (drops forgotten rows)
        self._save(force=True)                               # a deletion is real content change — persist now
        out = {"forgotten": len(target), "ids": sorted(target), "scrubbed_links": scrubbed,
               "tombstones": len(target)}
        # coverage on EVERY erasure path, not just forget_subject. It shipped on that one alone earlier
        # today, which is the same mistake `_resolve_subject`'s docstring records 1.53.0 making: a fix at
        # one caller while the siblings keep the gap. A field the caller relies on is worse than useless
        # when it is sometimes absent -- its absence reads as "nothing to report" rather than "nobody
        # looked". forget() is the base every other path funnels through, so it belongs here.
        out["coverage"] = self._erasure_coverage(None, len(getattr(self, "_erasure_targets", [])))
        # THIS store, always. `verify_residue_in` answers the question for other stores on disk and
        # nothing answered it here: measured, a record reading "summary: she lives at 5 Elm St" survived
        # forget_subject('hr/alice') holding the erased address verbatim, and the erasure returned
        # `erased: 1` with nothing else to say. The values were in hand at that moment and nobody looked.
        # Heuristic and labelled as one -- a paraphrase carries the fact without the string -- so it is
        # reported beside the count, never as the verdict.
        from .erasure_residue import scan_records
        out["residue_in_store"] = scan_records(self.items, _residue_values)
        if verify_residue_in:
            # Prove the bytes went, not just the rows. The report carries fingerprints, never the values.
            from .erasure_residue import scan_residue
            out["residue"] = scan_residue(verify_residue_in, _residue_values)
        return out

    @staticmethod
    def _tombstone_core(t: dict) -> dict:
        """The hash-committed fields of a tombstone. Backward-compatible: the AUTHORITY/BASIS block is included
        ONLY when present, so tombstones written without it hash exactly as before (older stores still verify)."""
        core = {k: t.get(k) for k in ("seq", "memory_id", "ts", "request_id", "prev")}
        if t.get("auth"):
            core["auth"] = t["auth"]
        return core

    def _emit_tombstone(self, memory_id: str, ts: float, request_id: str | None,
                        basis: str | None = None, authorized_by: str | None = None,
                        authorization: str | None = None, defer: bool = False) -> dict:
        """Append one hash-chained (optionally signed) deletion marker. Commits to the record's random surrogate
        id + ts + opaque request_id, PLUS an optional tamper-evident AUTHORITY/BASIS block: `basis` (why the
        record was erased — the decision basis), `authorized_by` (the authorizing principal's PUBLIC key), and
        `authorization` (that principal's Ed25519 signature over erasure_challenge(subject, request_id), from
        sign_erasure()). Still content-free (a hash of PII is still PII). When present, these are inside the
        committed hash, so an auditor can reconstruct WHO authorized the erasure and ON WHAT BASIS — not just a
        free-text id — and detect any later tampering with them."""
        prev = self._tombstones[-1]["hash"] if self._tombstones else _GENESIS
        t = {"seq": len(self._tombstones), "memory_id": memory_id, "ts": ts,
             "request_id": request_id, "prev": prev}
        if basis is not None or authorized_by is not None or authorization is not None:
            t["auth"] = {"basis": basis, "authorized_by": authorized_by, "authorization": authorization}
        # WHOSE ERASURE THIS WAS -- deliberately OUTSIDE the committed core, and the tradeoff is
        # stated rather than glossed. `_tombstone_core` has no tenant key, so a tenant view had no
        # way to filter and read EVERY tenant's erasure record: their memory_ids, their `request_id`
        # strings and the whole free-text `auth.basis`. Measured 2026-08-15, acme reading globex's
        # row: request_id "DSAR-globex-jane.doe-2026-000123" and basis "gdpr_art17 request by
        # jane.doe@globex.example, DOB 1984-03-02". Operator-authored free text that NAMES DATA
        # SUBJECTS is the worst possible payload for a cross-tenant read, and the surface leaking it
        # is the compliance surface.
        #
        # It cannot go INSIDE the core: that would change every existing tombstone's hash and break
        # the chain on upgrade. So the stamp is a VIEW FILTER, not an audit claim -- it is not
        # tamper-evident, and nothing in this file may treat it as evidence of anything. Verified by
        # re-hashing live tombstones with the key added: identical, chain_intact preserved.
        if self.tenant is not None:
            t["tenant"] = self.tenant
        t["hash"] = _sha256_hex(_canon(Inspeximus._tombstone_core(t)))
        # BOTH SIGNING PATHS, in the same order the write receipt uses them. This honoured
        # `receipt_key` (the in-process key) and ignored `receipt_signer` (the external KMS/HSM)
        # entirely -- so the deployment that took the write-authority boundary seriously, the one
        # where the store cannot mint a signature at all, got UNSIGNED erasure proofs. Measured
        # 2026-08-15: with receipt_signer= the receipts came back 1/1 signed and the tombstones 0/1,
        # the signer was called exactly once, and governance_report reported all_signed: False with
        # nothing saying why. A GDPR deletion proof is the last artifact that should be unsigned in
        # the most careful configuration.
        #
        # Fails CLOSED like the write path: a configured signer that errors or returns nothing must
        # not degrade to an unsigned tombstone that later reads as "no signature required".
        if self._receipt_signer is not None:
            try:
                _sig = self._receipt_signer(t["hash"])
            except Exception as e:
                raise RuntimeError(f"receipt signer failed ({type(e).__name__}: {e}); refusing to "
                                   f"append an unsigned tombstone while a signer is configured") from e
            if not _sig:
                raise RuntimeError("receipt signer returned no signature; refusing to append an "
                                   "unsigned tombstone while a signer is configured")
            t["sig"] = _sig
            if self.receipt_pubkey:
                t["pubkey"] = self.receipt_pubkey
        elif self._receipt_sk and _HAVE_ED:
            sk = _Ed25519SK.from_private_bytes(bytes.fromhex(self._receipt_sk))
            t["pubkey"] = self.receipt_pubkey
            t["sig"] = sk.sign(bytes.fromhex(t["hash"])).hex()
        self._tombstones.append(t)
        # `defer` is for a BATCH erasure, which is the only caller that emits more than one: it writes the
        # chain once at the end instead of once per tombstone. The default stays False so a single emit is
        # durable the moment it returns, exactly as before.
        if not defer:
            self._flush_tombstones()
        return t

    def _flush_tombstones(self) -> None:
        """Persist the whole tombstone chain. Split out of _emit_tombstone so a batch erasure can write once."""
        if not self._tombstones_path:
            return
        try:
            Inspeximus._atomic_write(self._tombstones_path,
                                     json.dumps(self._tombstones, indent=2, ensure_ascii=False))
        except Exception as e:
            # A tombstone is the PROOF an erasure happened. Silently losing it meant forget_subject
            # returned tombstones:1 and erasure_certificate said verified, while a reload showed
            # erasures_total: 0 — the deletion record a DSAR response rests on, gone without a word.
            self._sidecar_errors["tombstones"] = f"{self._tombstones_path}: {type(e).__name__}: {e}"

    def register_erasure_target(self, target) -> "Inspeximus":
        """Register an APP-SIDE store (the app's vector index, an embedding/response cache, a retrieval log)
        for cross-store right-to-erasure. Targets implement the two-method ErasureTarget protocol
        (inspeximus.deletion_manifest): erase(subject) and still_recoverable(subject, values). Once any target is
        registered, forget_subject() cascades the erasure through every target and returns a hash-chained
        DeletionManifest that is honest BY CONSTRUCTION: 'complete' only if every store (this one included)
        verified the data no longer recoverable, and it NAMES the stores that still leak. Targets are live
        client adapters, so they are RAM-only: re-register on every process start. Motivated by a measured
        gap: a copy the app embedded into its own vector index survives every memory store's native delete
        (erasure_fanout_probe: 8/8) — the store alone cannot fix that; a registered fan-out can."""
        if not hasattr(self, "_erasure_targets"):
            self._erasure_targets = []
        self._erasure_targets.append(target)
        return self

    def forget_subject(self, subject: str, request_id: str | None = None, basis: str | None = None,
                       authorized_by: str | None = None, authorization: str | None = None,
                       values=None, dry_run: bool = False, allow_ambiguous: bool = False,
                       exact: bool = False) -> dict:
        """RIGHT-TO-ERASURE across provenance lineage, with a tamper-evident audit of the ACT. Hard-deletes
        every active memory ATTRIBUTABLE to `subject` — its own canonical source OR any record that inherited
        `subject` through derived_from taint (so a summary/consolidation built from the subject's data is erased
        too, which a naive text-match delete would miss) — then records a signed, CONTENT-FREE tombstone per
        erased record so verify_writes() reports the now-missing rows as deliberately erased (not out-of-band
        tampering). `subject` is matched against canonical sources (`_rec_sources`): pass the same source string
        you wrote with (`remember(..., source={'doc': subject})`), or `'id:<record id>'` for a record written
        without any source. (Until 1.53.0 this line also offered an attested key as `'key:<hex>'`.
        `_rec_sources` has never emitted that prefix, so such a call silently erased nothing.)

        Returns {erased, ids, request_id, tombstones}. HONEST SCOPE (read before relying on it for compliance):
        this erases + proves-deletion WITHIN THIS inspeximus store only — NOT the app's vector store, prompt logs,
        or backups; it is an integrity primitive, NOT a compliance certification. The tombstone proves the ACT
        (a record with this surrogate id was erased at T for request R), never the CONTENT, and its signature is
        load-bearing only against a party who does NOT hold receipt_key (the operator who holds the key can forge
        tombstones too — anchor the chain head externally for operator-adversarial audit). Prior art: crypto-
        shredding; Cassandra/event-sourcing tombstones; GDPR Art.30 erasure logs; Crosby-Wallach / Certificate
        Transparency tamper-evident logs.

        `exact=True` proceeds on the collision-safe subset instead of refusing: only records whose RAW
        source string equals `subject` (plus what inherited from them) are erased, and a different source
        that merely canonicalises alike is left untouched. Use it when a DSAR must complete and the guard
        has fired — refusing is right by default, but a guard that cannot be satisfied turns one hostile
        write into a permanent block on a legal obligation.

        `dry_run=True` PREVIEWS the blast radius and touches NOTHING — no delete, no tombstone, no manifest
        cascade, no save. It returns {would_erase, ids, direct, inherited, sample, also_carrying, targets,
        dry_run:True}, and the split is the point: `direct` records name the subject as their own source,
        while `inherited` ones are reached only through derived_from taint. That second number is the one an
        operator cannot predict, and it is not hypothetical — a record repaired by rederive() inherits the
        taint of the source it was rewritten from, so erasing that source takes the repair with it and the
        store is left holding neither the old value nor the corrected one. `also_carrying` lists the OTHER
        subjects whose data would go down with this request, which is where a single erasure quietly becomes
        several. Preview before you commit.

        WHAT THE PREVIEW DOES NOT SHOW. (a) Records that SURVIVE but are modified — `forget()` scrubs the
        deleted ids from survivors' `links` and drops `superseded_by_toggle` pointers into the deleted set, so
        a survivor can silently lose corroboration or its revert target; none of that appears in
        `would_erase`. (b) The app-side cascade: `targets` NAMES the registered erasure targets but the
        preview does not run the manifest against them. (c) Anything outside this store. (d) `sample` returns
        record TEXT, including records ordinary recall would not surface, and the preview writes no receipt —
        treat it as a read of the content, and mind where your call path logs it."""
        # match the subject against canonical sources; accept either the raw string the caller wrote or its
        # entity-resolved form (_canon_source collapses "user-42"/"user_42"/"User 42" -> one canonical id).
        cand = {subject, Inspeximus._canon_source(subject)}
        subj_ids = [r["id"] for r in self.items if cand & Inspeximus._rec_sources(r)
                    and (self.tenant is None or r.get("tenant") == self.tenant)]   # tenant isolation on erasure
        subj_ids = self._narrow_to_subject(subject, subj_ids)
        collisions = self._erasure_collisions(subject, cand, subj_ids)
        if collisions and not allow_ambiguous:
            subj_ids = [rid for rid in subj_ids if rid not in collisions["ids"]]
        if dry_run:
            return self._erasure_preview(subject, cand, subj_ids, collisions, allow_ambiguous)
        if collisions and exact and not allow_ambiguous:
            # EXACT is the records whose RAW source is this subject, plus the LINEAGE DESCENDANTS of those
            # records — computed forward from them, not "everything carrying the shared canonical taint".
            # The first version simply cleared `collisions`, which left in every record that inherited from
            # the COLLIDING subject: measured, a summary derived from the other person's record was
            # hard-deleted. That is the third-party over-erasure the guard exists to prevent, reintroduced by
            # its own escape hatch. `_erasure_collisions` says the derived tier cannot separate colliding
            # subjects and that it "refuses rather than guessing" — so exact must not guess either.
            # The id-map is built ONCE. It used to sit inside this comprehension's condition, so it was
            # rebuilt for every rid in subj_ids -- an O(len(subj_ids) x n) scan in the erasure path.
            # Pure function of _tenant_rows(), which nothing here mutates, so hoisting is identical.
            _rows_by_id = {r["id"]: r for r in self._tenant_rows()}
            roots = {rid for rid in subj_ids
                     if self._raw_source(_rows_by_id.get(rid, {})) == subject}
            keep, frontier = set(roots), set(roots)
            while frontier:                       # forward closure over declared derived_from edges
                nxt = {r["id"] for r in self._tenant_rows()
                       if r["id"] not in keep and (set(r.get("derived_from") or []) & frontier)}
                keep |= nxt
                frontier = nxt
            subj_ids = [rid for rid in subj_ids if rid in keep]
            collisions = {}
        if False:
            # EXACT mode: erase only the records whose RAW source is this subject, leaving the colliding
            # one alone. The safe set is already computed above — refusing outright meant a single junk
            # write whose source canonicalises onto a victim's ("User_42" vs "user-42") made every later
            # DSAR for that person unperformable, with allow_ambiguous the only escape and it deletes both.
            # An attacker-triggerable denial of a legal obligation is worse than the collision it guards.
            collisions = {}
        if collisions and not allow_ambiguous:
            raise AmbiguousSubject(
                f"'{subject}' canonicalizes to '{Inspeximus._canon_source(subject)}', which is ALSO the "
                f"canonical form of {sorted(collisions['sources'])} in this store. Erasing would hard-delete "
                f"{len(collisions['ids'])} record(s) belonging to a different subject. Pass the exact source "
                f"string, or allow_ambiguous=True to erase all of them deliberately.")
        if not subj_ids:
            # coverage belongs here too: "nothing matched" is itself an answer a DSAR reply is built on,
            # and a field the caller can only rely on when it is ALWAYS present.
            return {"erased": 0, "ids": [], "request_id": request_id, "tombstones": 0,
                    "coverage": self._erasure_coverage(None, len(getattr(self, "_erasure_targets", []))),
                    "residue_in_store": {"ok": True, "checked_records": 0, "searched_values": 0, "findings": [], "problems": [], "method": "nothing was erased, so there is nothing that could be left over"}}
        # capture the sensitive values BEFORE deletion so the cross-store residue check has something to
        # verify against (caller-supplied `values` win; else the erased records' own text/object strings).
        targets = list(getattr(self, "_erasure_targets", []))
        if targets and values is None:
            ids_set = set(subj_ids)
            values = []
            for r in self.items:
                if r["id"] in ids_set:
                    for v in (r.get("text"), r.get("object")):
                        if v and str(v).strip():
                            values.append(str(v))
        # forget() has emitted the tombstones itself since 1.24.0, so the erasure's own request_id,
        # basis and authorisation go THROUGH it. Emitting a second time here (as this did until 1.24.3)
        # wrote TWO receipts per record — one carrying the real basis, one carrying the generic
        # basis="forget" — so an auditor saw a single deletion twice, with conflicting reasons.
        res = self.forget(ids=subj_ids, request_id=request_id, basis=basis,
                          authorized_by=authorized_by, authorization=authorization)
        out = {"erased": res["forgotten"], "ids": res["ids"],
               "request_id": request_id, "tombstones": len(res["ids"]),
               # PROPAGATED, not recomputed -- and this is the third time a field added to forget() had
               # to be carried up by hand. `coverage` was the same shape this morning, and the fix at one
               # caller while a sibling keeps the gap is what _resolve_subject's docstring records 1.53.0
               # doing. A field the caller relies on is worse than useless when it is sometimes absent:
               # its absence reads as "nothing to report" rather than "nobody looked".
               "residue_in_store": res.get("residue_in_store")}
        if targets:
            out["manifest"] = self._erasure_manifest(subject, values or [], targets, request_id,
                                                     basis, authorized_by, already_erased=res["forgotten"])
        out["coverage"] = self._erasure_coverage(out.get("manifest"), len(targets))
        return out

    @staticmethod
    def _erasure_coverage(manifest: dict | None, n_targets: int) -> dict:
        """WHAT THIS ERASURE ACTUALLY COVERED — measured, not a disclaimer.

        The result used to read `{"erased": 2, "request_id": ..., "tombstones": 2}` and say nothing at all
        about the world outside this store. Measured: a store-native delete leaves the application's own
        vector index fully populated (8/8 residue); wired to a registered target it is 0/8. So the default
        caller -- who has registered nothing -- was handed a confident number about a surface this library
        never looked at, which is the defect class this codebase keeps finding, here on the surface that
        answers "did we erase this person".

        The certificate and governance report did carry a scope SENTENCE, but the same sentence appears
        whether you wired every store or none: a constant is not a coverage report. This returns the state.

        `complete` is true only when at least one external target was registered AND every one of them
        confirmed erasure. With none registered it is False and `unregistered` says so -- not because the
        store failed, but because nobody asked it about anything else, and that is exactly the fact a DSAR
        answer must not omit.
        """
        cov = {"store": True, "external_targets": n_targets, "confirmed": 0,
               "complete": False, "unregistered": n_targets == 0}
        if not manifest:
            cov["note"] = ("no external erasure target is registered, so this covers THIS store only -- "
                           "any copy the application embedded elsewhere (vector index, prompt logs, "
                           "backups) is untouched and unaccounted for. Register targets with "
                           "register_erasure_target() to erase across them and get a verifiable receipt.")
            return cov
        # READ the manifest's own verdict, do not recompute it. DeletionManifest.execute already sets
        # `complete` (every entry verified_absent) and `residual_targets` (the ones that did not), and a
        # second implementation here would be a second thing to drift. The first version of this function
        # guessed the field names, iterated `targets` -- which is a list of NAMES, not records -- and
        # raised AttributeError on the first real call. Guessing a structure instead of reading it is the
        # habit this audit exists to break.
        entries = manifest.get("entries") or []
        # `confirmed` counts EXTERNAL targets only, so it stays comparable to `external_targets` beside
        # which it is printed. It used to include the manifest's `_SelfTarget` -- this store attesting
        # about itself -- and the two numbers then said things they did not mean (measured):
        #     1 external target, data absent    external_targets=1 confirmed=2   more confirmations than
        #                                                                        targets: unreadable
        #     1 external target, DISSENTING     external_targets=1 confirmed=1   reads as "the external
        #                                                                        target confirmed"; it did
        #                                                                        the opposite
        # The second is the one that matters: a dissent displayed as 1-of-1. The store's own answer is
        # still reported, under its own name, because dropping it would hide that the self-check ran.
        # `complete` is unchanged and still includes the self target -- an erasure is not complete if the
        # data survives HERE, and that half was measured correct on every arm.
        ext = [e for e in entries if e.get("target") != "inspeximus-store"]
        cov["confirmed"] = sum(1 for e in ext if e.get("verified_absent") is True)
        cov["store_self_check"] = next(
            (bool(e.get("verified_absent")) for e in entries if e.get("target") == "inspeximus-store"),
            None)
        cov["complete"] = bool(manifest.get("complete"))
        # WHO checked. `verified_absent` is the TARGET's own answer to still_recoverable(), so `complete`
        # means "every registered target said the data is gone", not "we confirmed it is gone". Measured:
        # a target that erases nothing but reports success AND returns still_recoverable=False gets a
        # clean verdict while the data sits there. That is the trust boundary, not a bug -- the library
        # cannot see inside a store it was handed an interface to -- but a receipt that says "verified"
        # when it means "attested" is the same overclaim as the erasure certificate reporting valid
        # signatures over an unsigned chain, which was fixed this morning. So the word goes in the field.
        cov["attested_by_targets"] = True
        cov["verification"] = ("each target's own still_recoverable() report; this store cannot "
                               "independently inspect a target it does not own")
        leaks = list(manifest.get("residual_targets") or [])
        if leaks:
            cov["unconfirmed"] = leaks
            cov["note"] = (f"{len(leaks)} registered target(s) did not verify the data absent: "
                           f"{', '.join(str(m) for m in leaks[:5])}. This erasure is NOT complete across "
                           f"stores and must not be reported as such.")
        elif not cov["complete"]:
            cov["note"] = "the manifest reports no verified targets; treat this erasure as store-only."
        return cov

    @staticmethod
    def _check_source(source):
        """A source dict must carry `doc` — that is the only key `_rec_sources` reads.

        `source={"who": ..., "url": ...}` was accepted and then silently attributed to `id:<record id>`:
        provenance gone, `slash(scope='source')` matching nothing, and `verify_attribution` reporting ok on a
        relabel. Silent un-attribution on a library that sells provenance is the wrong default."""
        if source is None:
            return None
        if not isinstance(source, dict):
            raise ValueError(f"remember(source=...) must be a dict, got {type(source).__name__}")
        if "doc" not in source:
            raise ValueError(
                f"remember(source=...) needs a 'doc' key — it is the identifier erasure, slashing and "
                f"attribution all resolve on. Got keys {sorted(source)}; pass source={{'doc': <id>, ...}}.")
        return dict(source)

    @staticmethod
    def _raw_source(rec: dict):
        """The record's own source string exactly as the writer passed it — NOT entity-resolved."""
        src = rec.get("source")
        return src.get("doc") if isinstance(src, dict) else (src if isinstance(src, str) else None)

    def _source_expansion_collisions(self, caught: list, targets: list) -> dict:
        """Targets pulled in by CANONICAL source that do not share a RAW source with the caught records.

        slash() and monitor() expand from the records you name to every record sharing their canonical
        source, which is right for a sybil publisher and wrong for two people under one host: caught on
        'crm.example.com/alice', `slash` forfeited 'crm.example.com/bob' too (measured: slashed 2, Bob's
        standing inverted to bad). Same lossy-key-as-selector defect as the erasure paths, one lever over."""
        caught_raw = {self._raw_source(r) for r in caught if self._raw_source(r)}
        if not caught_raw:
            return {}
        other = {}
        for r in targets:
            raw = self._raw_source(r)
            if raw and raw not in caught_raw and Inspeximus._canon_source(raw) in {
                    Inspeximus._canon_source(c) for c in caught_raw}:
                other.setdefault(raw, []).append(r["id"])
        if not other:
            return {}
        return {"sources": sorted(other), "ids": {i for v in other.values() for i in v}}

    def _resolve_subject(self, subject: str, allow_ambiguous: bool = False, destructive: bool = True):
        """THE one place a subject string becomes a set of records. Returns (cand, ids, collisions).

        Every subject-scoped operation used to inline `cand = {subject, _canon_source(subject)}` and select
        against it. 1.53.0 put a collision guard on `forget_subject` only — so the sibling paths kept the
        defect: `forget_pii(subject=...)` still hard-deleted the other subject, `retract_lineage` demoted it,
        and `rederive` REWROTE its text and re-emitted it (measured, all three). The guard has to live at the
        resolution step, not at one caller, or "fixed" means fixed in one of five places."""
        cand = {subject, Inspeximus._canon_source(subject)}
        ids = [r["id"] for r in self._tenant_rows() if cand & Inspeximus._rec_sources(r)]
        # ...and narrowed to the records that really are this subject, path and all. The coarse canonical
        # form keeps only the host, so 'crm/alice', 'crm/bob' and 'crm/nobody-here' were one key: measured,
        # a request naming a subject that was NEVER in the store hard-deleted another person's records and
        # reported success. forget_subject was fixed first and that repeated this function's own history --
        # its docstring records that 1.53.0 guarded one caller while four siblings kept the defect. It
        # belongs here, where every subject-scoped path resolves.
        ids = self._narrow_to_subject(subject, ids)
        collisions = self._erasure_collisions(subject, cand, ids) if destructive else {}
        if collisions and not allow_ambiguous:
            ids = [rid for rid in ids if rid not in collisions["ids"]]
        return cand, ids, collisions

    @staticmethod
    def _ambiguous_error(subject: str, collisions: dict, call: str) -> "AmbiguousSubject":
        return AmbiguousSubject(
            f"{call}: '{subject}' canonicalizes to '{Inspeximus._canon_source(subject)}', which is ALSO the "
            f"canonical form of {sorted(collisions['sources'])} in this store. Proceeding would affect "
            f"{len(collisions['ids'])} record(s) belonging to a different subject. Pass the exact source "
            f"string, or allow_ambiguous=True to include them deliberately.")

    def _erasure_collisions(self, subject: str, cand: set, subj_ids: list) -> dict:
        """Records swept in by a subject whose RAW source is a different string that merely canonicalizes the
        same way.

        `_canon_source` is deliberately lossy — it exists to collapse sybil variants of one publisher
        ('Wikipedia', 'wikipedia.org', 'https://www.wikipedia.org/wiki/X') into one attribution key, and for
        that it is right. As an ERASURE selector it is not: it keeps only the host, so
        'crm.example.com/alice' and 'crm.example.com/bob' are one key, and a DSAR for Alice hard-deleted
        Bob's record while the preview reported no collateral at all. Measured, then fixed.

        Detection is deliberately narrow: a collision needs an EXACT raw match for `subject` to exist in the
        store (so the caller demonstrably wrote that identifier) plus at least one other raw source landing on
        the same canonical key. That leaves the intended case working — writing 'user-42' and erasing
        'User 42' has no exact match, so it still resolves canonically.

        LIMIT, and it is not small: `taint` stores already-canonical keys, so a record that merely INHERITED
        from Alice is indistinguishable from one that inherited from Bob. Colliding subjects cannot be
        separated in the derived tier without re-writing taint, which no migration here attempts. This
        catches the direct case and refuses rather than guessing."""
        raws = {}
        for rid in subj_ids:
            raw = self._raw_source({r["id"]: r for r in self.items}[rid])
            if raw:
                raws.setdefault(raw, []).append(rid)
        if subject not in raws:                   # no exact identifier written -> canonical resolution intended
            return {}
        # A collision is a DIFFERENT raw source that lands on the SAME canonical key. A matched record whose
        # own source is simply another string (reached through taint) is ordinary cascade, not ambiguity —
        # flagging those refused every inherited erasure, which is the feature working as designed.
        canon = Inspeximus._canon_source(subject)
        other = {raw: ids for raw, ids in raws.items()
                 if raw != subject and Inspeximus._canon_source(raw) == canon}
        if not other:
            return {}
        return {"sources": sorted(other), "ids": {rid for ids in other.values() for rid in ids}}

    def _erasure_preview(self, subject: str, cand: set, subj_ids: list,
                         collisions: dict | None = None, allow_ambiguous: bool = False) -> dict:
        """The read-only half of forget_subject(). Splits the matched set into records that name the subject
        themselves and records reached only through inherited taint, and names the other subjects that would
        be caught in the same sweep. Mutates nothing."""
        by_id = {r["id"]: r for r in self.items}
        direct, inherited, others = [], [], {}
        for rid in subj_ids:
            r = by_id[rid]
            # Compare on the record's OWN raw source resolved the same way _rec_sources does it, or the
            # synthetic id key for a source-less record. An earlier version read every value of a dict source
            # and had no id fallback, so a record matched purely by taint (source={'doc':'summary-svc',
            # 'author':'user-42'}) was reported as DIRECT — inverting the one number this split exists for.
            raw = self._raw_source(r)
            own = {Inspeximus._canon_source(raw)} if raw else {"id:" + rid}
            (direct if cand & own else inherited).append(rid)
            for s in Inspeximus._rec_sources(r):
                if s not in cand and s != Inspeximus._canon_source(subject) and not s.startswith("id:"):
                    others.setdefault(s, 0)
                    others[s] += 1

        def _row(rid, why):
            r = by_id[rid]
            return {"id": rid, "why": why, "key": r.get("key"),
                    "text": (r.get("text") or "")[:120],
                    "taint": sorted(Inspeximus._rec_sources(r) - {f"id:{rid}"})}

        sample = ([_row(i, "direct") for i in sorted(direct)[:5]]
                  + [_row(i, "inherited") for i in sorted(inherited)[:5]])
        out = {"would_erase": len(subj_ids), "ids": sorted(subj_ids),
               "direct": len(direct), "inherited": len(inherited), "sample": sample,
               "also_carrying": dict(sorted(others.items(), key=lambda kv: -kv[1])),
               "targets": [getattr(t, "name", type(t).__name__)
                           for t in getattr(self, "_erasure_targets", [])],
               # The preview carries `coverage` too. The real run gained it earlier today; the preview did
               # not, so the surface built for deciding WHETHER to erase was the one surface that did not
               # state what the erasure would and would not cover. `targets: []` looks like "no external
               # copies", which is the opposite of what it means -- none REGISTERED, so any copy the app
               # embedded elsewhere is untouched AND unaccounted for. `confirmed` is 0 by construction
               # here: a rehearsal contacts nobody, so nothing can have attested.
               "coverage": dict(Inspeximus._erasure_coverage(
                   None, len(getattr(self, "_erasure_targets", []))), preview=True),
               "dry_run": True}
        if collisions:
            out["ambiguous_with"] = collisions["sources"]
            out["excluded_by_ambiguity"] = 0 if allow_ambiguous else len(collisions["ids"])
        return out

    def _erasure_manifest(self, subject: str, values: list, targets: list, request_id, basis,
                          authorized_by, already_erased: int) -> dict:
        """Cascade a subject erasure through the registered app-side targets and return the hash-chained
        DeletionManifest. This store itself is always the FIRST target (self-check on the same instrument):
        after the purge above, is any captured value still recoverable from items/recall? Honest scope is
        carried inside the manifest; see deletion_manifest.DeletionManifest."""
        from .deletion_manifest import DeletionManifest, ErasureTarget

        store = self
        n_erased = already_erased

        class _SelfTarget(ErasureTarget):
            name = "inspeximus-store"

            def erase(self, subj):                       # already purged by forget_subject
                return {"erased": n_erased}

            def still_recoverable(self, subj, vals):
                blob = " ".join((r.get("text") or "") + " " + str(r.get("object") or "")
                                for r in store.items).lower()
                return any(v.lower() in blob for v in (vals or []) if v)

        man = DeletionManifest()
        man.register(_SelfTarget())
        for t in targets:
            man.register(t)
        return man.execute(subject, values, request_id=request_id, basis=basis,
                           authorized_by=authorized_by)

    def _load_from_disk(self) -> None:
        """Read the store file into `_items` and record the file fingerprint we loaded from.

        The fingerprint is what makes concurrent writers detectable: `_save` compares it to the file's
        current (mtime_ns, size) and refuses rather than overwriting another writer's work."""
        if self.path and self.path.exists():
            raw = self.path.read_bytes()
            if raw[:5] == _INSPEXIMUS_ENC_MAGIC:                           # encrypted store -> decrypt or FAIL LOUD
                if not self._encrypted:
                    raise ValueError("store is encrypted; pass encrypt_key= or encrypt_passphrase= to open it")
                self._enc_salt = raw[5:21]                            # reuse the store's salt (passphrase re-derivation)
                try:
                    self._items = json.loads(_decrypt_blob(self._resolve_key(), raw))
                except Exception as e:                                # wrong key / tampered / truncated -> never
                    raise ValueError("cannot decrypt store (wrong key/passphrase, or the file was tampered)") from e
                #                                                       silently return [] (would risk overwriting real data)
            elif self._encrypted:
                # SYMMETRY. The branch above refuses an encrypted file opened without a key; this one
                # used to ACCEPT a plaintext file opened WITH one. Measured 2026-08-15: substituting a
                # plaintext JSON file under an encrypted store's path opened without error, served the
                # attacker's records, and the next save re-encrypted them under the real key -- after
                # which the substituted content is indistinguishable from genuine data. verify_writes()
                # does complain, but never names the real problem, and the caller who ASKED for
                # encryption is never told they got plaintext.
                #
                # The encrypted branch itself is sound: a corrupt file that keeps the magic is correctly
                # refused. Only the dispatch was one-directional. Both mismatches must be errors.
                raise ValueError(
                    f"the store at {self.path} is NOT encrypted, but this handle was opened with "
                    f"encrypt_key=/encrypt_passphrase=. Refusing to read it: a plaintext file under an "
                    f"encrypted store's path is a substitution, and saving would re-encrypt the "
                    f"substituted content under your key. Move the file aside if you meant to migrate "
                    f"it, or open it without a key to read it as plaintext.")
            else:
                try:
                    self._items = json.loads(raw.decode("utf-8"))    # legacy plaintext JSON
                except Exception as e:
                    # NEVER swallow this. A truncated/corrupt file loaded as [] and the very next _save()
                    # wrote that empty list over it: 5 records in, 0 loaded, 1 on disk after the next write.
                    # The encrypted branch above has always raised here; the plaintext branch silently
                    # destroyed the store instead. Refuse to open rather than overwrite what we cannot read.
                    raise ValueError(
                        f"cannot parse the store at {self.path} ({e}). Refusing to open it, because "
                        f"continuing would overwrite the file with an empty store. Restore a backup, or "
                        f"move the file aside if you meant to start fresh.") from None
        for r in self._items:
            # A record missing a field newer code assumes crashed six methods with a bare KeyError — and made
            # index_coherence report `coherent: true` with an undercount, which is worse than crashing. Foreign,
            # hand-edited and pre-upgrade stores are ordinary; normalise once here instead of guarding at every
            # read. Only absent keys are filled; nothing existing is touched.
            r.setdefault("status", "active")
            r.setdefault("tags", [])
            r.setdefault("links", [])
            r.setdefault("meta", {})
            r.setdefault("value", 1.0)
            r.setdefault("text", "")
            # A FIXED fallback, never time.time(): inventing a timestamp at load made state_digest
            # differ across two opens of identical bytes, so a witness or anchor pinned to such a
            # store could never re-verify. An undated legacy record is honestly undated.
            r.setdefault("ts", r.get("valid_from", 0.0))
            r.setdefault("last_access", r["ts"])
            r.setdefault("valid_from", r["ts"])
            r.setdefault("mtype", _infer_type(r.get("text") or ""))
            r.setdefault("iso", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["ts"])))
        self._file_sig = self._stat_sig()

    @staticmethod
    def _atomic_write(path, text: str) -> None:
        """Write via a temp file + os.replace. The sidecars used plain write_text, so a crash or a competing
        writer mid-write could leave a TRUNCATED receipt/tombstone chain — i.e. the evidence file corrupt
        while the store itself was fine, which is the worst way round."""
        # The same fixed-temp-name defect the store itself had, on the EVIDENCE files --
        # receipts and tombstones -- where a tear is worse: the chain breaks and
        # verify_writes reports the store TAMPERED when it was only raced.
        with _StoreLock(path):
            _durable_replace(path, text)

    _ABSENT = ("absent",)          #: sentinel: the file did not exist when we looked

    def _stat_sig(self):
        """(mtime_ns, size) of the store file, or the ABSENT sentinel if it is not there.

        A sentinel, not None. `None` meant "unknown" and `_save` skipped the guard on it, so two handles
        opening a path that does not exist yet BOTH had an ungated first write — and the second silently
        replaced the first. Two workers starting together on a fresh store is the commonest concurrency case
        there is, and it was the one case with no guard at all."""
        try:
            st = self.path.stat()
            return (st.st_mtime_ns, st.st_size)
        except AttributeError:
            return None                                   # no path: a RAM-only store, nothing to guard
        except OSError:
            return Inspeximus._ABSENT

    def reload(self) -> dict:
        """Re-read the store from disk and re-apply any records THIS handle holds that are not on disk.

        The recovery path after StoreChangedOnDisk: it keeps the other writer's records and re-adds ours by id
        (append-only union), so neither side loses a write. It cannot resurrect intent we never persisted —
        a deletion this handle made and could not save is simply not re-applied — so re-run it if it mattered.
        A record this handle TOMBSTONED is not resurrected, and where the merge leaves two active records
        under one key the store's own last-write-wins rule is re-applied rather than left contradictory.
        Returns {reloaded, readded, demoted, kept_buried}."""
        mine = {r["id"]: r for r in self._items}
        self._items = []
        self._file_sig = None
        self._load_from_disk()
        buried = {t.get("memory_id") for t in (self._tombstones or [])}
        # Drop what THIS handle deliberately erased, whichever side it came from. Filtering only the
        # re-added set was not enough: the record came back from DISK, so a tombstoned erasure was undone by
        # its own recovery path while the tombstone still claimed it had happened.
        resurrected = [r["id"] for r in self._items if r["id"] in buried]
        self._items = [r for r in self._items if r["id"] not in buried]
        on_disk = {r["id"] for r in self._items}
        readded = [r for rid, r in mine.items() if rid not in on_disk and rid not in buried]
        self._items.extend(readded)
        # A union by id is not enough. The disk copy of a record THIS handle had superseded comes back
        # ACTIVE, so a merged store ended up holding two contradictory active records under one key while
        # verify_writes() reported True -- the recovery path breaking the one property the store exists for.
        # Re-apply the store's own rule (last write wins by ts) per key.
        # Keyed on (TENANT, key) and only across DIFFERING values — both learned from a stateful property
        # run. Keying on `key` alone retired tenant A's current value because tenant B happened to use the
        # same key name (a tenant-isolation break in the recovery path, with verify_writes() still True).
        # And demoting same-VALUE rows destroyed restatements that _supersede_by_key deliberately keeps
        # ("a restatement is not a supersession"), so reload() was not state-preserving where flush()+reopen
        # is. Mirror the store's own rule instead of inventing a second one.
        by_key: dict = {}
        for r in self._items:
            if r.get("key") and r.get("status") == "active":
                by_key.setdefault((r.get("tenant"), r["key"]), []).append(r)
        demoted = 0
        for (_tenant, _key), rows in by_key.items():
            if len(rows) < 2:
                continue
            rows.sort(key=lambda r: r.get("ts", 0.0))
            newest_sig = Inspeximus._obj_sig(rows[-1])
            for r in rows[:-1]:
                if Inspeximus._obj_sig(r) == newest_sig:
                    continue                      # a restatement of the same value, kept by design
                r["status"] = "superseded"
                r.setdefault("meta", {})["superseded_by_policy"] = "reload_merge_lww"
                self._declare_retired(r, "reload merge: a newer value for this key won")
                demoted += 1
        self._file_sig = self._stat_sig()
        self._items_view_rev = None
        self._save(force=True)
        return {"reloaded": len(on_disk), "readded": len(readded), "demoted": demoted,
                "kept_buried": len(resurrected)}

    @property
    def items(self) -> list:
        """The records this store may see. **Tenant-scoped when the store is bound.**

        This is the structural half of tenant isolation. It used to be a plain attribute holding every
        tenant's records, and 46 methods read it directly — so isolation depended on each of them remembering
        to filter, and an audit found `history`, `provenance`, `as_of`, `why_recalled`, `revert` and the
        aggregate reports had not. Patching them one at a time fixed the instances and left the class: a
        method added tomorrow would read the shared list again, silently.

        Now the scoping lives under the reads instead of beside them. `self.items` cannot see another
        tenant's rows, so a new method is isolated by construction rather than by review. The real list is
        `self._items`; only the handful of call sites that genuinely own it (load, append, forget, shred)
        touch that, and they are the ones a reviewer should look at.

        Cached per (tenant, revision) so the filter is not re-run on every read of a hot path. A BOUND store
        returns an immutable tuple; an unbound one returns the real list. The asymmetry is deliberate: a write
        into a scoped view can only ever be a mistake, and it should raise rather than half-work.

        MIGRATION: records written before tenancy carry no `tenant` field, so a bound handle does not see
        them. Adopting tenancy on an existing store makes prior memories reachable only from the unbound
        store -- stamp them first if you need them scoped.

        AGENT GRANTS ride the SAME chokepoint, for the same reason. When the handle is bound to an agent
        (`store.as_agent("scribe")`), this filter additionally keeps only the records that agent owns or has
        an ACTIVE grant for, so a method added tomorrow is access-controlled by construction rather than by
        review. It is a FAIL-CLOSED allow-list: a grant that cannot be evaluated authorises nothing."""
        if self.tenant is None and getattr(self, "agent", None) is None:
            return self._items
        rev = (len(self._items), id(self._items))
        ck = (self.tenant, getattr(self, "agent", None), rev, getattr(self, "_acl_rev", 0))
        if getattr(self, "_items_view_rev", None) != ck:
            rows = self._items
            if self.tenant is not None:
                rows = [r for r in rows if r.get("tenant") == self.tenant]
            if getattr(self, "agent", None) is not None:
                rows = self._acl_visible(rows)
            # A TUPLE, not a list. The first version cached a list and returned the object itself, so
            # `view.items.append(rec)` did not merely fail to persist -- it planted a PHANTOM record that
            # every later reader saw, including fresh handles, and that recall ranked first. It was never on
            # disk and vanished on the next write. Immutable means the mistake raises instead of haunting.
            self._items_view = tuple(rows)
            self._items_view_rev = ck
        return self._items_view

    @items.setter
    def items(self, value):
        """Assigning the whole list is how `forget` used to work, and under a tenant-scoped read that would
        replace every tenant's records with this tenant's survivors. Route deliberate whole-list writes to
        `_items` so the intent is visible at the call site."""
        raise AttributeError(
            "assign to _items, not items: a whole-list write from a tenant-bound store would drop every "
            "other tenant's records. If you mean the shared list, say so explicitly.")

    def _tenant_rows(self) -> list:
        """The records THIS store is allowed to touch: all rows for an unbound (admin) store, else only the
        bound tenant's rows. The one place tenant scoping is resolved for the whole-store audit/sweep methods.

        ACCESS-CONTROL rows stay IN. They are ordinary records, and keeping them here is what makes a grant
        inspectable through the machinery that already exists -- history(), provenance() and
        supersession_report() answer "who could read this, and when was it taken back" with no second log.
        The first version of this filtered them out here, and the cost was immediate: history() on a grant
        key returned an empty list, so the audit trail the feature exists to provide was unreachable from
        the audit surface. Where a grant would be NOISE rather than evidence -- the memory counts, the
        contradiction scan, recall -- it is filtered at that reader instead; see _content_rows()."""
        if self.tenant is None:
            return list(self.items)
        return [r for r in self.items if r.get("tenant") == self.tenant]

    def _content_rows(self) -> list:
        """_tenant_rows() minus the access-control bookkeeping: the rows that are MEMORIES. Readers that
        describe or scan the content of a store use this, so issuing a grant cannot inflate an active-record
        count or make the contradiction scan flag "granted" against "revoked" as an incompatible pair."""
        return [r for r in self._tenant_rows() if not _is_acl_record(r)]

    def pii_report(self) -> dict:
        """Audit view of PII exposure across this store (tenant-scoped when bound): how many ACTIVE records
        carry each detected PII type, and their ids. Reads the `pii` tags stamped at write time (pii_detect /
        remember(pii=...)); it does NOT re-scan text here, so it reflects exactly what was tagged. Use it to
        drive a data-minimization review or a forget_pii() sweep. Read-only; returns no raw PII values.

        Returns {records_with_pii, by_type: {type: count}, ids: {type: [id,...]}}."""
        by_type: dict = {}
        ids: dict = {}
        n = 0
        for r in self._tenant_rows():
            if r.get("status") != "active":
                continue
            types = r.get("pii")
            if not types:
                continue
            n += 1
            for t in types:
                by_type[t] = by_type.get(t, 0) + 1
                ids.setdefault(t, []).append(r["id"])
        return {"records_with_pii": n, "by_type": by_type, "ids": ids}

    def forget_pii(self, types=None, subject: str | None = None, request_id: str | None = None,
                   basis: str | None = None, allow_ambiguous: bool = False) -> dict:
        """DATA-MINIMIZATION SWEEP: hard-delete (+ tombstone) every record carrying a PII tag, optionally
        restricted to specific `types` (e.g. ['email','ssn']) and/or a `subject` (a canonical source string,
        as in forget_subject). Tenant-scoped when the store is bound. Like forget_subject this genuinely REMOVES
        content and records a content-free, hash-chained tombstone per erased row so verify_writes() reads the
        deletion as deliberate, not tampering. Same HONEST SCOPE as forget_subject: erases within THIS inspeximus
        store only, not the app's vector store / logs / backups; not a compliance certification.

        Returns {erased, ids, request_id, tombstones}."""
        want = set(types) if types is not None else None
        cand = None
        sel_ids = None
        if subject is not None:
            cand, _sel, _coll = self._resolve_subject(subject, allow_ambiguous)
            if _coll and not allow_ambiguous:
                raise Inspeximus._ambiguous_error(subject, _coll, "forget_pii")
            # Select by the RESOLVED ids, not by the coarse candidate set. Re-matching on `cand` below
            # threw away the resolver's narrowing, so this path still deleted every record sharing a host
            # with the subject -- including for a subject that was never in the store. The resolver is
            # "THE one place a subject becomes a set of records" only if its callers use the set.
            sel_ids = set(_sel)
        target = []
        for r in self._tenant_rows():
            tags = r.get("pii")
            if not tags:
                continue
            if want is not None and not (want & set(tags)):
                continue
            if sel_ids is not None and r["id"] not in sel_ids:
                continue
            if cand is not None and not (cand & Inspeximus._rec_sources(r)):
                continue
            target.append(r["id"])
        cov = self._erasure_coverage(None, len(getattr(self, "_erasure_targets", [])))
        if not target:
            return {"erased": 0, "ids": [], "request_id": request_id, "tombstones": 0,
                    "coverage": cov,
                    "residue_in_store": {"ok": True, "checked_records": 0, "searched_values": 0, "findings": [], "problems": [], "method": "nothing was erased, so there is nothing that could be left over"}}
        # same as forget_subject: forget() emits the receipts, so pass the reason through it rather
        # than writing a second tombstone per record on top of the one it already wrote
        res = self.forget(ids=target, request_id=request_id, basis=basis or "pii_minimization")
        return {"erased": res["forgotten"], "ids": res["ids"],
                "request_id": request_id, "tombstones": len(res["ids"]),
                "coverage": res.get("coverage", cov),
                "residue_in_store": res.get("residue_in_store")}

    def for_tenant(self, tenant: str):
        """Return a TENANT VIEW over THIS store (one physical store, many logically-isolated tenants). The view
        SHARES this store's items, caches, file, and config by reference — so `store.for_tenant('a')` and
        `store.for_tenant('b')` read/write ONE store with no clobber — but every write it makes is stamped with
        its tenant and every read/supersession/erasure it performs is hard-filtered to it (fail-closed, exactly
        like a tenant-bound Inspeximus). Typical use: the operator holds the unbound `store` (admin/migration view)
        and hands each request a `store.for_tenant(user_id)` handle that cannot see another tenant's data.

            store = Inspeximus(path="all.json")
            acme = store.for_tenant("acme"); acme.remember("secret", key="k", object="s")
            globex = store.for_tenant("globex")
            acme.recall("secret")     # -> only acme rows; globex.recall(...) never sees them
        """
        return _TenantView(self, str(tenant), agent=getattr(self, "agent", None))

    # ── agent-to-agent read grants (scoped, revocable, recorded in the same chain) ────────────────────
    #
    # WHY THESE SELECTORS. A grant names a subset of memories by `scope`, `tag`, `key` or explicit `ids` --
    # never by QUERY. Membership in a scope/tag/key/id set is a STORED FACT, decidable in one pass with no
    # embedder, no similarity threshold and no LLM, and it means the same thing tomorrow as today. A
    # query-shaped selector would make the authorised set a function of a similarity score, so the same
    # grant would cover different records after a re-embed, a corpus change, or a different k -- an ACL
    # that silently widens is not an ACL. Deterministic membership is the property worth keeping.
    #
    # HONEST SCOPE. This is logical isolation INSIDE one store and one process, like tenancy: the shared
    # `_items` list is still reachable from a private attribute, so it is the right model when many agents
    # share a runtime, and it is NOT a substitute for separate stores/keys when the agents are mutually
    # hostile and the process itself is the trust boundary. And the WRITE path is unchanged: grants gate
    # READS. Finally, `by` (the granting agent) is an identity the caller asserts, not one this library
    # authenticates -- exactly the limit already stated for supersession. Where an authenticated identity
    # matters, the surface that holds it must supply it.

    #: The selector kinds, in the order _acl_selector() accepts them. Read by that method rather than kept
    #: beside it as documentation: a list nothing consults cannot hold anything in agreement.
    _ACL_KINDS = ("scope", "tag", "key", "ids")

    @staticmethod
    def _check_agent_id(agent) -> str:
        """An agent id must be a plain, non-empty string that cannot collide inside a grant key. Rejected
        rather than sanitised: quietly rewriting an identifier is how two different agents end up sharing
        one ACL entry."""
        if not isinstance(agent, str):
            raise ValueError(f"agent id must be a string, got {type(agent).__name__}")
        a = agent.strip()
        if not a:
            raise ValueError("agent id must not be empty")
        if "::" in a or a == "*":
            raise ValueError(f"agent id must not contain '::' or be '*' (reserved in the grant keyspace): {agent!r}")
        return a

    @staticmethod
    def _acl_selector(scope=None, tag=None, key=None, ids=None):
        """Normalise EXACTLY ONE selector into (kind, value, token). Zero selectors is refused rather than
        read as 'everything' -- fail closed is the whole posture, and 'grant() with no arguments means
        total access' is the accident this refusal exists to prevent."""
        given = [(k, v) for k, v in zip(Inspeximus._ACL_KINDS, (scope, tag, key, ids)) if v is not None]
        if len(given) != 1:
            raise ValueError(
                f"a grant needs EXACTLY ONE selector - {'=, '.join(Inspeximus._ACL_KINDS)}=. Given "
                f"{len(given)} ({[k for k, _ in given]}). No selector is not 'everything': it is ambiguous, "
                "and an access-control decision that cannot be evaluated must deny.")
        kind, value = given[0]
        if kind == "ids":
            if isinstance(value, str):
                value = [value]
            vals = sorted({str(x) for x in value if str(x).strip()})
            if not vals:
                raise ValueError("grant(ids=[...]) needs at least one record id")
            return kind, vals, _sha256_hex(_canon(vals))[:16]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"grant({kind}=...) needs a non-empty string")
        return kind, value.strip(), value.strip()

    def _acl_key(self, by: str, agent: str, kind: str, token: str) -> str:
        """One grant key per (granter, grantee, selector). The granter is IN the key so revoking Alice's
        grant to Bob cannot disturb Carol's independent grant to Bob -- they are different keys, and keyed
        supersession only ever adjudicates within a key."""
        return f"{_ACL_PREFIX}{by}::{agent}::{kind}::{token}"

    def _acl_resolve_by(self, by) -> str:
        """Who is issuing this act. An agent-bound handle can only ever act as ITSELF (fail closed: a handle
        cannot mint a grant in another agent's name and thereby lend records it does not own). The unbound
        operator handle may name a `by` explicitly -- that is how a CLI/MCP surface, which has no session
        identity of its own, records the agent that actually asked."""
        me = getattr(self, "agent", None)
        if by is None:
            return me if me is not None else "*"
        b = Inspeximus._check_agent_id(by)
        if me is not None and b != me:
            raise PermissionError(
                f"this handle is bound to agent {me!r} and cannot issue a grant as {b!r}. Use the operator "
                f"(unbound) handle if you are administering another agent's grants.")
        return b

    def _acl_write(self, agent: str, by: str, kind, value, token: str, state: str, note) -> dict:
        """Record one access-control ACT as an ordinary hash-chained record, so it inherits the write
        receipt, the anchor, provenance() and history() instead of needing a second log."""
        k = self._acl_key(by, agent, kind, token)
        who = "the operator" if by == "*" else f"agent {by!r}"
        what = (f"records with id in {value}" if kind == "ids" else f"records whose {kind} == {value!r}")
        text = (f"ACL: {who} granted agent {agent!r} read access to {what}." if state == "granted"
                else f"ACL: {who} revoked read access to {what} for agent {agent!r}.")
        if note:
            text += f" Note: {note}"
        meta = {"acl": {"agent": agent, "by": by, "kind": kind, "value": value, "state": state}}
        if note:
            meta["acl"]["note"] = str(note)
        self._acl_writing += 1
        try:
            # reaffirm=True on purpose: grant -> revoke -> grant is a LEGITIMATE reversal, and without it the
            # echo guard would retire the re-grant on arrival as a restatement of a superseded value. Access
            # genuinely comes back; that is the case reaffirm exists for.
            mid = self._stamp(text, key=k, object=state, mtype="procedural", value=0.0,
                                meta=meta, reaffirm=True)
        finally:
            self._acl_writing -= 1
        self._acl_rev += 1
        return {"ok": True, "id": mid, "key": k, "agent": agent, "by": by,
                "kind": kind, "value": value, "state": state}

    def grant(self, agent: str, scope: str | None = None, tag: str | None = None,
              key: str | None = None, ids=None, by: str | None = None, note: str | None = None) -> dict:
        """Give `agent` READ access to a subset of this store's memories, and record the act.

        Exactly one selector: `scope=` (meta['scope']), `tag=` (a tag), `key=` (a supersession key) or
        `ids=` (explicit record ids). Membership is exact-match on a STORED field -- no embedder, no
        threshold, no LLM -- so the authorised set is the same tomorrow as today (see the note above the
        selector helper for why a query selector was refused).

        WHOSE records. From the unbound operator handle the grant covers every record the selector matches.
        From an agent-bound handle (`store.as_agent("alice").grant("bob", tag="billing")`) it covers only
        records ALICE owns, so an agent can lend what it wrote and never more. `by=` records the granting
        agent from a surface (CLI/MCP) that has no session identity; it is an asserted identity, not an
        authenticated one.

        Reading with it: `store.as_agent("bob").recall(...)`. Ending it: `revoke(...)` with the same
        selector -- effective on the next read. Both acts land in the write-receipt chain and the anchor,
        so `history(<grant key>)` and the audit bundle show who was allowed what, and when it was taken back.
        """
        a = Inspeximus._check_agent_id(agent)
        b = self._acl_resolve_by(by)
        kind, value, token = Inspeximus._acl_selector(scope, tag, key, ids)
        return self._acl_write(a, b, kind, value, token, "granted", note)

    def revoke(self, agent: str, scope: str | None = None, tag: str | None = None,
               key: str | None = None, ids=None, by: str | None = None, note: str | None = None) -> dict:
        """End a grant. Effective on the NEXT read.

        It writes a REVOKED act over the same grant key, which retires the grant by ordinary keyed
        supersession. It deletes nothing: the owner keeps the records, every other agent keeps its own
        grants (a different granter or grantee is a different key), and the grant itself stays in the
        history as evidence that access existed and was withdrawn. Revoking something that was never
        granted is allowed and is recorded -- `was_granted` in the result says which case it was."""
        a = Inspeximus._check_agent_id(agent)
        b = self._acl_resolve_by(by)
        kind, value, token = Inspeximus._acl_selector(scope, tag, key, ids)
        k = self._acl_key(b, a, kind, token)
        # `self._items`, not `self.items`, in the five siblings' company -- and this one lacked the
        # tenant predicate they all carry (_acl_grants_for :4551, _acl_match :4584, can_read :4653,
        # grants :4672, grant_log :4692). So `was_granted` answered a yes/no question about ANOTHER
        # tenant's grants: revoke('bob', tag='roadmap') returned True iff the victim had granted
        # exactly that, one bit per probe, and the probe landed in the ATTACKER's tenant so the
        # victim's own grant_log never moved. The oracle is minor; "five filter and one does not" is
        # the drift the items chokepoint exists to end.
        was = any(r.get("key") == k and r.get("status") == "active"
                  and (self.tenant is None or r.get("tenant") == self.tenant)
                  and ((r.get("meta") or {}).get("acl") or {}).get("state") == "granted"
                  for r in self._items)
        out = self._acl_write(a, b, kind, value, token, "revoked", note)
        out["was_granted"] = was
        return out

    def _acl_note_problem(self, reason: str, rec_id=None, agent=None) -> None:
        """A grant that cannot be evaluated DENIES, and says so. Kept bounded (last 200) so a malformed
        store cannot grow the process without limit, and mirrored to the `inspeximus.acl` logger."""
        _ACL_LOG.warning("grant denied: %s (agent=%r, grant=%r)", reason, agent, rec_id)
        probs = self._acl_problems
        probs.append({"ts": time.time(), "agent": agent, "grant": rec_id, "reason": reason})
        if len(probs) > 200:
            del probs[:-200]

    def _acl_grants_for(self, agent: str) -> list:
        """The ACTIVE grants that apply to `agent` in THIS handle's tenant, read from the SHARED list.

        It must read `_items`, not `items`: `items` is the very filter this feeds, and access-control rows
        are invisible to an agent-bound handle by design (an agent does not get to read the ACL).

        FAIL CLOSED on disagreement: a supersession leaves one active act per grant key, but a hand-edited
        or partially-restored store can leave two that disagree. 'granted' and 'revoked' both active is not
        a tie to break in the reader's favour -- the whole key is dropped and the ambiguity is logged."""
        tv = self.tenant
        by_key: dict = {}
        for r in self._items:
            if not _is_acl_record(r) or r.get("status") != "active":
                continue
            if r.get("tenant") != tv:
                continue                                # the ACL is per-tenant, like everything else
            acl = (r.get("meta") or {}).get("acl")
            if not isinstance(acl, dict):
                self._acl_note_problem("grant record carries no readable acl metadata", r.get("id"))
                continue
            if acl.get("agent") != agent:
                continue
            by_key.setdefault(r.get("key"), []).append(r)
        out = []
        for k, rows in by_key.items():
            states = {((r.get("meta") or {}).get("acl") or {}).get("state") for r in rows}
            if len(states) != 1 or not (states <= {"granted", "revoked"}):
                self._acl_note_problem(
                    f"grant key {k!r} has {len(rows)} active acts with disagreeing states {sorted(map(str, states))}; "
                    f"an unresolvable grant authorises nothing", rows[0].get("id"), agent)
                continue
            if states == {"granted"}:
                out.append(rows[0])
        return out

    def _acl_match(self, g: dict, rec: dict) -> bool:
        """Does grant record `g` authorise reading `rec`? Every branch that cannot decide returns False.

        THE SELECTOR IS VALIDATED HERE, not only where the grant was minted, and that is the load-bearing
        part. `scope` and `key` are compared with `==` against a field that is ABSENT on most records, so a
        grant whose stored value is missing or null degenerates to `None == None` and authorises every
        record that has no scope (or no key) -- the widest possible grant, produced by the emptiest possible
        input, on the read path. That is the shape a sibling unit just found in a signed certificate: a
        check that holds VACUOUSLY once its scope is empty, and here it fails OPEN rather than merely
        reporting a hollow pass. grant() cannot mint such a record, but a hand-edited store, a partial
        restore or a future writer can, so the evaluator refuses it independently."""
        acl = (g.get("meta") or {}).get("acl") or {}
        if g.get("tenant") != rec.get("tenant"):
            return False                       # a grant never reaches across a tenant boundary
        by = acl.get("by")
        if not isinstance(by, str) or not by:
            self._acl_note_problem(f"grant names no granter (by={by!r}); it authorises nothing",
                                   g.get("id"), acl.get("agent"))
            return False
        if by != "*" and rec.get("owner_agent") != by:
            return False                       # an agent may lend only what it owns
        kind, val = acl.get("kind"), acl.get("value")
        if kind in ("scope", "tag", "key"):
            if not isinstance(val, str) or not val:
                self._acl_note_problem(
                    f"grant selector {kind}={val!r} is empty or not a string; an unevaluable selector "
                    f"authorises nothing (it would otherwise match every record that lacks the field)",
                    g.get("id"), acl.get("agent"))
                return False
            if kind == "scope":
                return (rec.get("meta") or {}).get("scope") == val
            if kind == "tag":
                return val in (rec.get("tags") or [])
            return rec.get("key") == val
        if kind == "ids":
            if not isinstance(val, (list, tuple, set)) or not val:
                self._acl_note_problem(f"grant selector ids={val!r} is empty or not a list; it authorises "
                                       f"nothing", g.get("id"), acl.get("agent"))
                return False
            return rec.get("id") in set(val)
        self._acl_note_problem(f"grant has an unknown selector kind {kind!r}", g.get("id"), acl.get("agent"))
        return False

    def _acl_visible(self, rows) -> list:
        """The ALLOW-LIST for this handle's agent: records it owns, plus records an active grant covers.
        Access-control rows themselves are never visible through an agent handle."""
        me = self.agent
        if not isinstance(me, str) or not me:
            # An agent handle with no usable identity can own nothing and be granted nothing. Returning the
            # rows unfiltered here would be the whole feature failing open on a falsy value.
            self._acl_note_problem(f"agent handle has no usable identity ({me!r}); it reads nothing", None, me)
            return []
        grants = self._acl_grants_for(me)
        out = []
        for r in rows:
            if _is_acl_record(r):
                continue
            if r.get("owner_agent") == me or any(self._acl_match(g, r) for g in grants):
                out.append(r)
        return out

    def can_read(self, agent: str, id: str) -> dict:
        """Explain one access decision: {allowed, reason, via}. `via` is the grant record's id when access
        came from a grant, 'owner' when the agent wrote the record itself, and None on a denial. The
        surface that makes an ACL inspectable one record at a time, without having to run a recall.

        It also carries `problems`: the grants that could NOT be evaluated while answering THIS question,
        each of which denied. That matters because a denial caused by a malformed grant and a denial caused
        by no grant at all read identically from the outside -- "bob sees nothing" is the same sentence
        either way, and only one of them is a store someone needs to go and fix."""
        a = Inspeximus._check_agent_id(agent)
        tv = self.tenant
        seen = len(self._acl_problems)

        def _out(allowed, reason, via):
            # The problems recorded WHILE answering this call, not the whole history: an operator asking
            # about bob should not be handed a defect that was logged during someone else's lookup.
            return {"allowed": allowed, "reason": reason, "via": via,
                    "problems": [p["reason"] for p in self._acl_problems[seen:]]}

        rec = next((r for r in self._items if r.get("id") == id
                    and (tv is None or r.get("tenant") == tv)), None)
        if rec is None:
            return _out(False, "no such record in this handle's scope", None)
        if _is_acl_record(rec):
            return _out(False, "access-control records are not readable through an agent handle", None)
        if rec.get("owner_agent") == a:
            return _out(True, "the agent owns this record", "owner")
        for g in self._acl_grants_for(a):
            if self._acl_match(g, rec):
                acl = (g.get("meta") or {}).get("acl") or {}
                return _out(True, f"granted by {acl.get('by')!r} on {acl.get('kind')}={acl.get('value')!r}",
                            g.get("id"))
        return _out(False, "no active grant covers this record for this agent", None)

    def grants(self, agent: str | None = None) -> list:
        """The grants that are in force right now (optionally for one agent), newest first. Read-only."""
        want = Inspeximus._check_agent_id(agent) if agent is not None else None
        rows = []
        for r in self._items:
            if not _is_acl_record(r) or r.get("status") != "active" or r.get("tenant") != self.tenant:
                continue
            acl = (r.get("meta") or {}).get("acl") or {}
            if acl.get("state") != "granted":
                continue
            if want is not None and acl.get("agent") != want:
                continue
            rows.append({"id": r["id"], "key": r.get("key"), "agent": acl.get("agent"), "by": acl.get("by"),
                         "kind": acl.get("kind"), "value": acl.get("value"), "ts": r.get("ts"),
                         "note": acl.get("note")})
        return sorted(rows, key=lambda x: x["ts"] or 0, reverse=True)

    def grant_log(self, agent: str | None = None) -> list:
        """EVERY access-control act -- grants, revocations and the ones a later act retired -- newest first.
        The answer to 'who could read this, and when was it taken back'. Read-only.

        Each entry also carries `problems`-free plain fields; grants that could not be EVALUATED are not
        acts and are surfaced separately by the `inspeximus.acl` logger and by can_read()."""
        want = Inspeximus._check_agent_id(agent) if agent is not None else None
        rows = []
        for r in self._items:
            if not _is_acl_record(r) or r.get("tenant") != self.tenant:
                continue
            acl = (r.get("meta") or {}).get("acl") or {}
            if want is not None and acl.get("agent") != want:
                continue
            rows.append({"id": r["id"], "key": r.get("key"), "agent": acl.get("agent"), "by": acl.get("by"),
                         "kind": acl.get("kind"), "value": acl.get("value"), "state": acl.get("state"),
                         "status": r.get("status"), "ts": r.get("ts"), "note": acl.get("note")})
        return sorted(rows, key=lambda x: x["ts"] or 0, reverse=True)

    def as_agent(self, agent: str):
        """A READ HANDLE bound to `agent` over THIS store: same file, same records, same config, but every
        read hard-filtered to what that agent owns or has been granted, and every write it makes stamped as
        that agent's. Fail-closed -- with no grants issued it sees only its own writes.

            store.remember("shared roadmap", tags=["roadmap"])          # operator-owned
            store.grant("bob", tag="roadmap")
            store.as_agent("bob").recall("roadmap")                     # -> the record
            store.as_agent("eve").recall("roadmap")                     # -> nothing
            store.revoke("bob", tag="roadmap")
            store.as_agent("bob").recall("roadmap")                     # -> nothing, on the next read
        """
        return _TenantView(self, self.tenant, agent=Inspeximus._check_agent_id(agent))

    def _visible_tombstones(self) -> tuple:
        """This handle's tombstones, plus how many were withheld for want of a tenant stamp.

        FAIL-CLOSED on the unstamped ones. A tombstone written before 2.10.2 has no `tenant`, so a
        bound handle cannot tell whether it is looking at its own erasure or someone else's -- and
        the payload is operator free text naming data subjects. Withheld and COUNTED, never withheld
        silently: a report that quietly shows less than it used to is how an operator concludes an
        erasure never happened.
        """
        if self.tenant is None:
            return tuple(self._tombstones), 0
        mine = tuple(t for t in self._tombstones if t.get("tenant") == self.tenant)
        return mine, sum(1 for t in self._tombstones if "tenant" not in t)

    def erasure_report(self) -> dict:
        """Audit view of deliberate erasures: total tombstones + each {memory_id, ts, request_id}. Read-only;
        carries NO erased content (by construction). The durable proof-of-deletion trail behind forget_subject."""
        toms, withheld = self._visible_tombstones()
        out = {"tombstoned_total": len(toms),
               "erasures": [{"memory_id": t["memory_id"], "ts": t.get("ts"),
                             "request_id": t.get("request_id"), "signed": "sig" in t}
                            for t in toms]}
        if withheld:
            out["limits"] = [f"{withheld} tombstone(s) predate tenant stamping and are WITHHELD from "
                             f"this bound handle: without a stamp they cannot be attributed, and a "
                             f"`request_id` is operator free text that routinely names a data subject. "
                             f"Read them from an unbound (operator) handle."]
        return out

    def erasure_audit(self, subject: str | None = None, values=None) -> dict:
        """AFTER an erasure: what does the store's lineage say survived — and how much did it actually see?

        `forget_subject()` deletes what is attributable to a subject and tombstones the act. This answers the
        question an operator hits afterwards: did anything survive that still carries the erased material?
        The hard case is never the record itself; it is the summary built from it, which no longer resembles
        the subject's data. (Graphiti documents the same boundary: its `remove_episode` does not regenerate
        node summaries that other episodes still support.)

        **Read `coverage` before `verdict`.** Every structural check here walks DECLARED `derived_from` edges.
        A store whose writers never declared lineage has no edges to walk, so it would report no residue while
        having inspected nothing — a materially different statement from "checked, nothing found". `coverage`
        makes that difference visible instead of collapsing both into one reassuring boolean:
        `{records, with_declared_lineage, undeclared_derived, declared_ratio, subject_reachable_records}`.
        When nothing is declared the verdict is **`unaudited`**, and when something claimed derivation that
        the walk could not resolve it is **`partially_audited`** — neither is a pass.

        Returns `{verdict, residue, advisory, coverage, checked, limits}`:
          - `verdict` — `residue_found` | `partially_audited` | `no_declared_residue` | `unaudited`.
            `no_declared_residue` is the ONLY pass, and it asserts only this: every record that announced
            itself as derived resolved its parents, and walking those parents found nothing surviving that
            carries the erased material. It does not assert that the subject had no undeclared derivative,
            and `declared_ratio` is store-wide, so it never vouches for one subject — read
            `subject_reachable_records` for that.
          - `residue` — findings attributable to a DELIBERATE erasure (the vanished record carries a deletion
            tombstone with a request_id or an authority/basis block): `subject_still_attributable`,
            `taint_without_origin` (a derivative outlived the origin it inherited), `dangling_lineage`, and
            `tombstone_gap` (a receipted record gone with no tombstone at all).
          - `advisory` — the same shapes, but where the missing record was removed with NO erasure request.
            Capacity eviction and the consolidation keep-budget both hard-delete for size reasons, and would
            otherwise masquerade as erasure residue in any bounded store. Reported, never counted.
          - `value_possibly_recoverable` — HEURISTIC, only when `values=` is passed; listed in `advisory` and
            never drives the verdict, because matching text proves neither presence (a paraphrase carries the
            fact without the string) nor absence.

        Deterministic, read-only, no LLM. This is evidence about what the store has RECORDED, not proof that
        no copy of the material remains.

        HONEST SCOPE, also returned in `limits`: (1) a derivative whose writer never declared `derived_from`
        carries no taint and is invisible to every structural check here — deliberately the under-tainting
        side of the overtainting/undertainting trade-off argued for dynamic taint analysis by Schwartz, Avgerinos
        & Brumley (IEEE S&P 2010) -- program analysis, not lineage, so we borrow the trade-off, not a result. (2) It covers THIS store only — never your vector index, prompt logs, model weights or
        backups. (3) It reads metadata the writer supplied, so it cannot detect what was never declared, and
        a party that stops declaring lineage will always look clean. Do not treat a pass as discharging an
        erasure obligation.

        Prior art: DELF-style deletion-correctness auditing (Cohn-Gordon et al., "DELF: Safeguarding deletion
        correctness in Online Social Networks", USENIX Security 2020) applied to an agent-memory store, with
        the orphan/dangling half being classical referential-integrity checking."""
        residue: list[dict] = []
        advisory: list[dict] = []
        by_id = {r["id"]: r for r in self.items}
        tombs = {t.get("memory_id"): t for t in (getattr(self, "_tombstones", None) or [])}

        def _deliberate(mid: str) -> bool:
            """Was this record ERASED on purpose, or merely dropped for space? Every hard delete routes
            through forget() and tombstones, INCLUDING capacity eviction and the consolidation keep-budget —
            so the presence of a tombstone proves nothing. What separates them is intent the caller had to
            supply: a `request_id`, or a `basis` other than the generic "forget" default that housekeeping
            leaves behind. Without this, an erasure audit fires on every bounded store that ever evicted."""
            t = tombs.get(mid)
            if not t:
                return False
            basis = (t.get("auth") or {}).get("basis")
            return bool(t.get("request_id")) or (bool(basis) and basis != "forget")

        def _add(deliberate: bool, kind: str, rid: str, detail: str, cause: str | None = None):
            f = {"kind": kind, "id": rid, "detail": detail}
            if cause:
                f["cause"] = cause
            (residue if deliberate else advisory).append(f)

        # every canonical source that some surviving record still claims as its OWN (not inherited)
        own_sources: set = set()
        for r in self.items:
            src = r.get("source")
            doc = src.get("doc") if isinstance(src, dict) else (src if isinstance(src, str) else None)
            if doc:
                own_sources.add(Inspeximus._canon_source(doc))

        if subject is not None:
            # The coarse candidate set FIRST, then the same narrowing the erasure paths use. Matching on
            # `_canon_source` alone is the lossy-key-as-selector defect one lever over: 'hr/carol',
            # 'hr/dave' and 'hr/nobody-here' all collapse to 'hr', so on a two-person store this reported
            # (measured, all three arms identical) that a subject NEVER WRITTEN was still attributable to
            # a real record -- and, worse in the other direction, that a correctly completed DSAR for
            # carol had left residue, because dave's record shared the host. A compliance surface telling
            # an operator their erasure failed when it succeeded is not a lesser error than the reverse.
            #
            # `_narrow_to_subject` is reused rather than reimplemented: it travels declared derived_from
            # edges from a root whose RAW source matches, and admits inherited taint only when
            # canonicalisation loses nothing about the subject. Two copies of that rule would drift, which
            # is how this surface came to disagree with forget_subject() in the first place.
            cand = {subject, Inspeximus._canon_source(subject)}
            coarse = [r["id"] for r in self._tenant_rows() if cand & Inspeximus._rec_sources(r)]
            for rid in self._narrow_to_subject(subject, coarse):
                r = by_id.get(rid)
                if r is None:
                    continue
                _add(True, "subject_still_attributable", rid,
                     f"still attributable to {subject!r} (status={r.get('status')})")

        for r in self.items:
            # a declared parent that is gone: erased (residue) or evicted/consolidated away (advisory)?
            missing = [pid for pid in (r.get("derived_from") or []) if pid not in by_id]
            for pid in missing:
                if _deliberate(pid):
                    _add(True, "dangling_lineage", r["id"],
                         f"declares parent {pid}, which was deliberately erased")
                else:
                    _add(False, "dangling_lineage", r["id"],
                         f"declares parent {pid}, which is gone with no erasure request",
                         cause="removed without an erasure request (capacity eviction or consolidation)")
            # A RECALL-WINDOW id that is gone. `recall_window` is a history/evidence field, so forget() keeps
            # it for the same reason it keeps derived_from and taint (see the note there: scrubbing history
            # deletes the evidence and makes this audit read clean). But "kept" only stays honest while THIS
            # surface can see it -- a pointer channel the audit never walks is residue that reports as no
            # residue, which is the failure mode the whole erasure story exists to deny. It is reported under
            # its own kind, never merged into dangling_lineage: an observation that the store served an id is
            # not a claim of parentage, and an auditor who reads them as one would over-count declared
            # lineage in a store that never declared any.
            for pid in [p for p in ((r.get("recall_window") or {}).get("ids") or []) if p not in by_id]:
                if _deliberate(pid):
                    _add(True, "dangling_recall_window", r["id"],
                         f"records having been served {pid}, which was deliberately erased")
                else:
                    _add(False, "dangling_recall_window", r["id"],
                         f"records having been served {pid}, which is gone with no erasure request",
                         cause="removed without an erasure request (capacity eviction or consolidation)")
            # inherited a source whose origin no longer survives anywhere
            for t in (r.get("taint") or []):
                if t.startswith("id:"):
                    continue                  # a source-less parent is tainted by its own id; see dangling
                if t in own_sources:
                    continue
                deliberate = any(_deliberate(pid) for pid in missing)
                _add(deliberate, "taint_without_origin", r["id"],
                     f"carries inherited source {t!r}, but no surviving record claims it"
                     + (": the origin was erased, this derivative was not" if deliberate else ""),
                     None if deliberate else "origin absent, but no erasure request accounts for it")

        # A receipted write whose record is gone must be accounted for by SOME tombstone.
        #
        # NOT FROM A TENANT VIEW. `by_id` is built from `self.items` (tenant-SCOPED) and cross-checked
        # against `self._receipts` (store-WIDE), so every OTHER tenant's live record looks like a
        # receipted write that vanished without a tombstone. Measured 2026-08-15: on any receipted
        # multi-tenant store a tenant's erasure_audit() returned `residue_found` 100% of the time --
        # with nobody having erased anything at all -- and the residue list was precisely the other
        # tenants' complete live id set. Two harms in one: a compliance verdict that CANNOT return a
        # pass, and an enumeration of every id in the store.
        #
        # Tombstones carry no tenant field (they are hashed by `_tombstone_core`, which has no such
        # key), so the check cannot be scoped correctly yet. A check that cannot see its target must
        # report UNCHECKED, never `residue` -- rule #12, and the reason it is in `limits` rather than
        # silently dropped.
        _tenant_bound = getattr(self, "tenant", None) is not None
        if not _tenant_bound and getattr(self, "_receipts", None):
            for mid in {rc.get("memory_id") for rc in self._receipts}:
                if mid not in by_id and mid not in tombs:
                    _add(True, "tombstone_gap", mid,
                         "written and later removed, with no deletion tombstone")

        declared = sum(1 for r in self.items if r.get("derived_from"))
        undeclared = sum(1 for r in self.items if r.get("orphan"))
        # SUBJECT-SCOPED REACH. `declared` is a STORE-WIDE count and cannot vouch for a subject-scoped
        # question. Our own test documented the hole in its assertion comment ("lineage exists elsewhere,
        # so not 'unaudited'"): a store whose only declared edge is billing -> billing-summary walked a
        # real edge, and no edge it walked could ever have reached an erased `user-42`. This counts the
        # surviving records the walk could actually have followed TO THIS SUBJECT: one that declares a
        # parent now tombstoned, or one carrying the subject's canonical source as inherited taint.
        #
        # It is REPORTED, and deliberately does NOT gate the verdict. After a correct cascade the
        # derivatives are erased too, so reach legitimately reads 0 -- and tombstones are content-free by
        # design (a hash of PII is still PII), so nothing here can separate "cascade erased them" from
        # "they were never declared". A gate that a correct erasure can never pass measures nothing, which
        # is the same defect in the other direction. So it goes where an operator is told to look first.
        subject_reach = None
        if subject is not None:
            _canon = Inspeximus._canon_source(subject)
            # `_deliberate`, not bare `in tombs`: every hard delete tombstones, including capacity
            # eviction and the consolidation keep-budget, so counting any tombstoned parent inflates
            # reach on a busy bounded store for reasons unrelated to the subject. This field exists to
            # warn when reach is zero, and an over-count silences that warning -- the wrong direction
            # for a safety signal to fail in.
            subject_reach = sum(1 for r in self.items
                                if any(_deliberate(pid) for pid in (r.get("derived_from") or []))
                                or _canon in (r.get("taint") or []))
        coverage = {"records": len(self.items), "with_declared_lineage": declared,
                    "undeclared_derived": undeclared,
                    "declared_ratio": round(declared / len(self.items), 3) if self.items else 0.0,
                    "subject_reachable_records": subject_reach,
                    # Reported BESIDE declared lineage and never folded into it. On a store with
                    # observe_recall on, this is typically the larger number by orders of magnitude
                    # (declaring is per-write and gets skipped; observing is automatic), and an auditor
                    # who read the two as one would conclude the store has lineage it does not have.
                    "with_recall_window": sum(1 for r in self.items if r.get("recall_window"))}

        limits = [
            "this is evidence about what the store RECORDED, not proof that no copy of the material remains",
            "a derivative whose writer never declared derived_from carries no taint and is invisible here; "
            "read `coverage` before trusting a pass",
            "covers THIS store only -- not your vector index, prompt logs, model weights or backups",
            "does not discharge an erasure obligation; a party that stops declaring lineage always looks clean",
            "`declared_ratio` is STORE-WIDE and never vouches for one subject: a store whose lineage is "
            "entirely about some other subject walked real edges, none of which could reach this one. "
            "`subject_reachable_records` counts what the walk could actually follow to THIS subject, and "
            "0 there means the structural checks said nothing about it, whatever the verdict says",
            "`recall_window` is an OBSERVATION that the store served an id before a write, not a claim of "
            "parentage: it is reported as dangling_recall_window and counted separately in coverage, and it "
            "must not be read as lineage",
        ]
        if _tenant_bound:
            limits.append(
                "tombstone_gap NOT CHECKED: this handle is bound to a tenant, and receipts are "
                "store-wide, so every other tenant's live record would read as a gap. Run "
                "erasure_audit() from an unbound (operator) handle for that check.")
        if values:
            # Word boundaries alone are NOT enough: plain \b lets 'UTC' fire inside 'UTC-8', reporting a
            # DIFFERENT, longer value as recovered. So the match must not continue across - / : + or an
            # INTERIOR dot ('v1.2.3'). A sentence-final dot is not interior: putting a bare . in the
            # exclusion class silently lost every value that happened to end a sentence ('the tz is UTC.').
            for r in self.items:
                blob = ((r.get("text") or "") + " " + str(r.get("object") or "")).lower()
                for v in values:
                    v = str(v).strip().lower()
                    if v and re.search(r"(?<![\w\-/:+])(?<!\w\.)" + re.escape(v)
                                       + r"(?![\w\-/:+])(?!\.\w)", blob):
                        advisory.append({"kind": "value_possibly_recoverable", "id": r["id"],
                                         "detail": f"surviving text still contains {v!r} on a word boundary",
                                         "cause": "heuristic text match, not evidence"})
                        break
            limits.append("value_possibly_recoverable is a HEURISTIC and never drives the verdict: matching "
                          "text proves neither presence (a paraphrase carries the fact) nor absence")

        if residue:
            verdict = "residue_found"
        elif declared == 0:
            verdict = "unaudited"          # nothing declared => nothing structural was inspected
        elif undeclared:
            # PARTIAL COVERAGE IS NOT A PASS. Until 2.6.0 the only demotion was `declared == 0`, so a
            # store with ONE resolvable edge and four hundred records that claimed derivation and
            # resolved none returned the pass verdict. Thomas Willner recorded exactly this in the
            # LLM Errata PRIOR_ART.md while reviewing us: "Its tests force `unaudited` when declared
            # lineage is zero, but a nonzero incomplete ratio can still return `no_declared_residue`."
            # He is right, and it is the same defect we had just reported to him from the other side:
            # an enumerable store that comes back empty must not be recorded like one that was walked.
            #
            # The gate is the ORPHAN COUNT, not `declared_ratio`. A ratio threshold would be an absolute
            # cut on a relative score: most records are roots and never derive from anything, so a
            # healthy store sits at a low ratio forever and any threshold either fires always or never.
            # `orphan` is positive evidence instead of a proportion -- the record ANNOUNCED that it was
            # derived and the walk could not resolve where from -- so it says the one thing a coverage
            # verdict needs: there is a hole, and we know it is there.
            verdict = "partially_audited"
        else:
            verdict = "no_declared_residue"
        return {"verdict": verdict, "residue": residue, "advisory": advisory, "coverage": coverage,
                "checked": {"subject": subject, "values_scanned": len(values or [])}, "limits": limits}
    def state_digest(self) -> str:
        """Deterministic SHA-256 fingerprint of the CURRENT store state. Order-independent (records are
        sorted by id) over exactly: id, status, ts, key, tenant, and the content hash — so a supersession,
        revert, erasure, re-key or text edit changes the digest. Zero-LLM, O(n) hashing, no configuration.
        This is the "revision X" a hydration witness pins to.

        HONEST SCOPE — what it does NOT cover, and why. `value` and the outcome standing (`good`/`bad`) are
        OUTSIDE the digest, and those are what RANKING uses. So an out-of-band edit of `value`, or a
        `credit()` call, changes which record recall returns first WITHOUT changing the digest, and
        `verify_witness()` still reports `valid: True`. Measured; an earlier version of this docstring said
        "any out-of-band edit changes the digest", which was false in exactly the two fields that decide
        which fact wins.

        This is a deliberate trade, not an oversight, and the reason is narrower than this used to claim.
        It said "`recall()` itself bumps `value` and `last_access`, so a digest covering them would change
        on every READ" -- measured 2026-08-16 and FALSE by default: `reinforce` defaults to False, and 25
        consecutive recalls left `value` at 1.0, `last_access` bit-identical and the digest unchanged. With
        `reinforce=True` the bump is real (1.0 -> 2.25 over five recalls), so the exclusion is still right:
        a store that reinforces would churn its digest on every REINFORCING read and a witness could never
        match anything. The missing word was doing all the work. If you need
        ranking-state integrity, pin it separately — the write receipts commit to content and attribution,
        not to standing."""
        h = hashlib.sha256()
        for r in sorted(self._tenant_rows(), key=lambda x: x.get("id") or ""):
            line = "\x1f".join([
                str(r.get("id") or ""), str(r.get("status") or "active"),
                repr(r.get("ts")), str(r.get("key") or ""), str(r.get("tenant") or ""),
                hashlib.sha256((r.get("text") or "").encode("utf-8")).hexdigest(),
            ])
            h.update(line.encode("utf-8")); h.update(b"\x1e")
        return h.hexdigest()

    def witness(self, records=None, bind_sources: bool = False) -> dict:
        """HYDRATION WITNESS: a compact, deterministic receipt of the store state an answer was derived
        from — "this answer reflects store state as of revision X". Attach it to any answer assembled
        from recall() results; verify later with verify_witness(). When write receipts are enabled
        (receipts=True), the witness also carries the receipt-chain tip, anchoring the claimed state to
        the tamper-evident write history. It inherits state_digest()'s blind spot: `value` and outcome
        standing are not covered, so a tamper that only reorders RANKING leaves a witness verifying.
        HONEST SCOPE: the witness pins the STORE + this store's view of
        its index inputs; it cannot attest external caches or copies it never saw.

        AND IT IS UNSIGNED, which matters because this package uses the word "witness" for two
        different things. THIS one is a receipt an HONEST caller carries from their own verification
        to their own use: measured, a holder who edits the pinned digests to whatever the file says
        now gets a clean verdict, and that is not a hole, it is what an unsigned self-issued receipt
        is. It binds a caller to their own earlier check; it does not bind a caller who lies.
        `witness_pool.Witness.attest()` is the operator-adversarial one -- signed by a third party,
        for the case where the party being audited is the one handing you the artifact.

        `bind_sources=True` closes the THIRD invalidation window, and it is the one this library did
        not have. Provenance has a temporal chain — OBSERVE, BIND, CAPTURE, VERIFY, USE — and a
        mutation in each gap is a different failure with a different remedy:

            OBSERVE -> CAPTURE   the fingerprint is of bytes nobody read   UNBOUND_CAPTURE
            CAPTURE -> VERIFY    the source changed since capture          DRIFTED
            VERIFY  -> USE       the source changed after it checked out   STALE_AT_USE

        The framing is @safal207's on anthropics/claude-code#34556. Measured on ourselves before
        building it: `check_sources` returns FRESH, the file changes, `recall` serves the old text,
        and `verify_witness` answers digest_match=True — correctly, because the STORE did not change.
        A caller who verified and then acted had nothing telling them the ground moved. It was never
        invisible (a second `check_sources` sees it); it was UNBOUND, which is worse, because the
        check that would have caught it is the one nobody thought to run again.

        Pass the recall results the answer used — `witness(hits, bind_sources=True)` — and the
        witness carries their source locators and fingerprints. Only those: binding every source in
        the store would cost a full re-read to attest records the answer never touched.

        WHAT IT REFUSES TO PRETEND. A record with no fingerprint CANNOT be bound, and is listed in
        `sources_unbound` rather than skipped, because a witness that binds nothing and reports
        nothing is the vacuous pass this repository keeps meeting. `sources_bound` is "n/of" for the
        same reason `declared_observation_binding_coverage` carries the word declared: coverage is
        not a guarantee, and the number has to let you tell them apart.
        """
        act = sum(1 for r in self.items if r.get("status") == "active")
        w = {"inspeximus_hydration_witness": 1, "digest": self.state_digest(),
             "records": len(self.items), "active": act,
             "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if self.embed_id:
            w["embed_id"] = self.embed_id
        if getattr(self, "_receipts", None):
            w["receipts_tip"] = self._receipts[-1].get("hash")
        if bind_sources:
            w.update(self._bind_sources(records))
        return w

    def _bind_sources(self, records=None) -> dict:
        """The source half of a hydration witness: what each covered record was reading, and its digest.

        `records` may be recall() results, record dicts, or bare ids. None means every active record,
        which is honest but expensive — the useful unit is the handful an answer actually used.
        """
        if records is None:
            ids = [r.get("id") for r in self.items if r.get("status") == "active"]
        elif isinstance(records, (str, bytes)):
            ids = [records]
        else:
            ids = [r.get("id") if isinstance(r, dict) else r for r in records]

        by_id = {r.get("id"): r for r in self.items}
        bound, unbound, unknown, not_bindable = {}, [], [], []
        for rid in ids:
            rec = by_id.get(rid)
            if rec is None:
                # NAMED, not dropped. An id the store does not hold is a caller error or an erasure
                # between recall and witness, and either way the answer rested on something this
                # witness cannot speak for.
                unknown.append(rid)
                continue
            meta = rec.get("meta") or {}
            fp = meta.get("source_sha256")
            doc = Inspeximus._raw_source(rec)
            if fp and doc:
                # BOUND, AND WHAT KIND. A write-time hash is a perfectly good baseline for THIS
                # window -- "has the source changed since I checked" needs only a fingerprint that
                # was true at check time. It is NOT evidence the memory was ever right about the
                # bytes it read, which is the OBSERVE->CAPTURE question and a different guarantee.
                # Binding both and reporting them as one number would be the exact conflation this
                # feature exists to undo, so the kind travels with the pin.
                bound[rid] = {"doc": doc, "sha256": fp,
                              "observation_bound": bool(meta.get("observation_bound"))}
            elif doc:
                unbound.append(rid)          # a document we simply have no fingerprint for
            else:
                not_bindable.append(rid)     # nothing to re-read, in any window
        _obs = sum(1 for v in bound.values() if v["observation_bound"])
        _denom = len(bound) + len(unbound) + len(unknown)
        out = {"sources": bound,
               # NOT_BINDABLE is out of the denominator here too, for the reason it is out of
               # check_sources': an answer assembled entirely from decisions would otherwise report
               # 0/N binding and read as a failure, when there was never anything to bind.
               "sources_bound": f"{len(bound)}/{_denom}" if _denom else "0/0",
               "sources_observation_bound": f"{_obs}/{len(bound)}"}
        if not_bindable:
            out["sources_not_bindable"] = not_bindable
        if unbound:
            out["sources_unbound"] = unbound
        if unknown:
            out["sources_unknown"] = unknown
        return out

    def verify_witness(self, w: dict, resolver=None) -> dict:
        """Check a hydration witness against the store as it is NOW. digest_match=True means the store
        is byte-for-byte in the state the witness pinned (no write, supersession, revert, or erasure has
        happened since); False means the answer that carried this witness reflects a PRIOR revision —
        which is exactly what the receipt exists to make visible.

        A witness built with `bind_sources=True` also re-reads the SOURCES it pinned, which is the
        VERIFY -> USE window: the store can be untouched while the world the memory describes has
        moved. `resolver(doc) -> bytes|None` fetches non-file sources, exactly as in check_sources.

        THE TWO ANSWERS STAY SEPARATE, because collapsing them is the defect this whole line of work
        came from: `digest_match` is about the STORE, `sources_match` is about the WORLD, and one
        score hiding which of them broke tells you nothing about the remedy. A moved source is
        `stale_at_use` and wants revalidation; a changed digest wants re-derivation.

        `valid` DOES require both — but only when the witness carries sources, because then the
        caller explicitly asked for that guarantee and `valid: True` beside a moved source would be
        a false assurance. A witness that pinned no sources keeps the old meaning exactly.
        """
        cur = self.state_digest()
        out = {"digest_match": cur == w.get("digest"), "current_digest": cur}
        limits: list = []
        if "receipts_tip" in w:
            tip = self._receipts[-1].get("hash") if getattr(self, "_receipts", None) else None
            out["receipts_tip_match"] = (tip == w.get("receipts_tip"))
        _tip_ok = out.get("receipts_tip_match", True)
        if self.tenant is not None and not _tip_ok:
            # THE RECEIPT CHAIN IS PER-STORE AND THE WITNESS IS PER-TENANT, so ANOTHER tenant's
            # write moves this tenant's tip while their own records are untouched. Failing here
            # would mark every tenant's receipt invalid on a busy shared store, and an alarm that
            # fires on other people's activity is one that gets switched off before it ever catches
            # anything. Reported, not fatal -- the tenant's own claim is `digest_match`, which is
            # scoped and stays True. An UNBOUND store keeps the strict meaning: there, a moved tip
            # IS the store's history changing, which is exactly what the anchor exists to catch.
            _tip_ok = True
            limits.append(
                "the shared write chain moved since this witness was taken, which on a tenant-bound "
                "store usually means ANOTHER tenant wrote. It does not mean this tenant's records "
                "changed -- see digest_match for that. Take the witness from the unbound store if "
                "you need the whole chain attested.")
        out["valid"] = out["digest_match"] and _tip_ok

        if "sources" in w:
            pinned = w.get("sources") or {}
            moved, orphaned, matched = [], [], 0
            for rid, s in pinned.items():
                doc = (s or {}).get("doc")
                try:
                    blob = resolver(doc) if resolver else (
                        open(doc, "rb").read() if doc and os.path.exists(doc) else None)
                except Exception:
                    blob = None
                if blob is None:
                    # NOT a match and NOT a mismatch. A source that cannot be re-read answers the
                    # question with silence, and silence must not read as pass.
                    orphaned.append(rid)
                elif hashlib.sha256(blob).hexdigest() == (s or {}).get("sha256"):
                    matched += 1
                else:
                    moved.append(rid)
            out["sources_checked"] = len(pinned)
            out["sources_matched"] = matched
            out["sources_moved"] = moved
            out["sources_orphaned"] = orphaned
            out["stale_at_use"] = bool(moved)
            # EMPTY IS NOT CLEAN -- but "nothing was checked" and "nothing was CHECKABLE" are two
            # different empties, and collapsing them is the same mistake one level down from the
            # NOT_BINDABLE split. A caller who asked to bind sources and got none from records that
            # NAME documents has an unmet request: False. A caller whose answer is made of decisions
            # never had anything to bind: None, with a limit saying so, matching check_sources'
            # verdict on a pure-decision store. False there would make an honest answer read as a
            # failed check forever, which is precisely what this release is fixing above.
            # THE WITNESS DECLARES ITS OWN DENOMINATOR, and that is what makes the two empties
            # distinguishable to a verifier holding a document someone else may have edited.
            #
            # Measured the moment the None case landed: an attacker who empties `sources` produced
            # exactly the shape of "nothing was ever bindable", so the tamper bought a pass. The
            # honest empty carries `sources_bound: "0/0"`; the tampered one still says "1/1" while
            # presenting zero pins, and that contradiction is the tell.
            try:
                _db, _dd = (int(x) for x in str(w.get("sources_bound", "0/0")).split("/", 1))
            except Exception:
                _db = _dd = -1
            if _db != len(pinned) or _dd < 0:
                out["sources_match"] = False
                limits.append(
                    f"this witness says it bound {w.get('sources_bound')} sources and presents "
                    f"{len(pinned)}: the two disagree, so it has been edited or truncated since it "
                    f"was issued. Refusing to read it as a clean world.")
                out["valid"] = False
                out["sources_checked"] = len(pinned)
                out["sources_matched"] = matched
                out["sources_moved"] = moved
                out["sources_orphaned"] = orphaned
                out["stale_at_use"] = bool(moved)
                if limits:
                    out["limits"] = limits
                return out
            _askable = _dd > 0
            if not _askable:
                out["sources_match"] = None
                limits.append(
                    "nothing in this answer could be bound to a source in any window -- these are "
                    "decisions, not derived facts. sources_match is undefined rather than False: "
                    "there was nothing to check, which is not the same as a check that failed.")
            elif not pinned:
                out["sources_match"] = False
                limits.append(
                    "this witness asked to bind sources and bound NONE, though some records DO name "
                    "a document, so sources_match is False because nothing was checked rather than "
                    "because something moved. Those records need an observed_sha256 to be bindable.")
            else:
                out["sources_match"] = not moved and not orphaned
            if w.get("sources_unbound"):
                limits.append(
                    f"{len(w['sources_unbound'])} record(s) in this answer NAME a source and carry "
                    f"no fingerprint for it, so the VERIFY->USE window is open for them however "
                    f"this verdict reads. This is the backfillable kind: {w['sources_unbound'][:5]}")
            if w.get("sources_not_bindable"):
                limits.append(
                    f"{len(w['sources_not_bindable'])} record(s) have no document anchor at all and "
                    f"can never be bound in any window -- a decision, not a derived fact. They are "
                    f"NOT counted against coverage, and chasing them is wasted effort: "
                    f"{w['sources_not_bindable'][:5]}")
            if w.get("sources_unknown"):
                limits.append(
                    f"{len(w['sources_unknown'])} id(s) the witness names are not in this store: "
                    f"{w['sources_unknown'][:5]}")
            if orphaned:
                limits.append(
                    f"{len(orphaned)} pinned source(s) could not be re-read, so they are neither "
                    f"fresh nor moved: {orphaned[:5]}")
            _wt = [r for r, v in pinned.items() if not (v or {}).get("observation_bound")]
            if _wt:
                # A DIFFERENT WINDOW, STATED SEPARATELY. These pins rest on a hash taken when the
                # record was written rather than on the bytes the agent read, so this verdict
                # answers "the source has not moved since you checked" and says nothing about
                # whether the memory was bound to what was actually observed.
                limits.append(
                    f"{len(_wt)} pinned source(s) rest on a WRITE-TIME hash, not an observed one, "
                    f"so VERIFY->USE is covered for them while OBSERVE->CAPTURE is not: {_wt[:5]}")
            # None (nothing was bindable) must not drag `valid` down; False must.
            out["valid"] = out["valid"] and (out["sources_match"] is not False)
        if limits:
            out["limits"] = limits
        return out

    def index_coherence(self) -> dict:
        """Does the derived semantic index agree with the store? An append-only or git-backed store can be
        perfectly governed and STILL serve a stale value if the embedding index lags or was built with a
        different recipe (the class of bug behind inspeximus's own 1.15-1.18 realign fixes). Deterministic,
        read-only. Reports: active text-bearing records missing a vector while an embedder is configured
        (index behind store), vectors persisted under a DIFFERENT embed recipe than the current one
        (unrankable against fresh queries), and whether vectors survive a save at all on this store.
        coherent=True means semantic recall on this store ranks against vectors that match the current
        store content and recipe."""
        has_embedder = self.embed is not None
        act_text = [r for r in self._tenant_rows() if r.get("status") == "active" and r.get("text")]
        missing = sum(1 for r in act_text if not r.get("vec")) if has_embedder else 0
        sidecar = None
        if getattr(self, "_embedid_path", None) is not None and self._embedid_path.exists():
            try:
                sidecar = self._embedid_path.read_text(encoding="utf-8").strip() or None
            except OSError:
                sidecar = None
        recipe_match = True
        if self._persist_vectors and sidecar is not None and (self.embed_id or "") != sidecar:
            recipe_match = False
        out = {"coherent": (missing == 0 and recipe_match),
               "embedder_configured": has_embedder,
               "active_text_records": len(act_text), "missing_vecs": missing,
               "recipe_match": recipe_match, "persist_vectors": self._persist_vectors,
               "embed_id": self.embed_id or None, "sidecar_embed_id": sidecar}
        if not has_embedder:
            out["note"] = "lexical-only store: no derived index to drift; coherent by construction"
        elif not self._persist_vectors:
            out["note"] = ("persist_vectors=False: vectors are a RAM-only cache rebuilt per process; "
                           "a fresh open starts with an empty index until the backfill re-embeds")
        return out

    def erasure_certificate(self, request_id: str | None = None, expected_pubkey: str | None = None) -> dict:
        """Portable, INDEPENDENTLY-VERIFIABLE erasure certificate — the auditor-grade receipt for a
        right-to-erasure demand. Packages the signed deletion tombstones (the full hash-chain, so it can be
        re-derived from genesis), the request-scoped erased ids, the receipt public key, and a CT-style
        anchor() into ONE self-contained JSON document. A third party checks it with the module-level
        `verify_erasure_certificate(cert, store_path=...)` WITHOUT the operator's private key and WITHOUT
        trusting the operator: it re-derives the tombstone chain, verifies each Ed25519 signature, confirms the
        anchor commits to the chain tip, and — given the store — confirms every erased id is genuinely ABSENT
        from it (the 'read the raw store' proof that soft-delete / history-keeping systems fail). Content-free:
        commits to surrogate ids + timestamps + opaque request, never PII. HONEST SCOPE = governance_report()'s
        (within THIS store; the ACT not the content; the signature is load-bearing only against a non-holder of
        receipt_key — witness the anchor externally). Pair with shred() for encrypted-at-rest crypto-erasure."""
        # OPERATOR-ONLY, and this is a genuine either/or rather than an unfinished scoping job.
        # The certificate ships the FULL chain from genesis ON PURPOSE, so a third party can
        # re-derive it without trusting us. You cannot scope the `tombstones` array -- the `prev`
        # links break and `chain_intact` fails -- and you cannot redact `request_id` or `auth`
        # either, because both are INSIDE `_tombstone_core` and redaction breaks the hash. So a
        # scoped certificate is not a narrower certificate; it is a broken one.
        #
        # Measured 2026-08-15: a bound handle issued a certificate scoped to ANOTHER tenant's
        # request_id, carrying that tenant's erased ids and the free-text basis "gdpr_art17 request
        # by jane.doe@globex.example, DOB 1984-03-02". Refusing LOUDLY beats narrowing quietly: a
        # certificate a third party cannot verify from genesis is not the artifact this docstring
        # promises, so silently degrading it would break the feature to close the leak.
        if self.tenant is not None:
            raise AttributeError(
                "erasure_certificate() is operator-only and is not available on a tenant-bound "
                "handle. The certificate must ship the whole tombstone chain from genesis so a "
                "third party can re-derive it; scoping it to one tenant breaks `prev` and the "
                "signatures, and the chain carries every tenant's request_id and authority basis. "
                "Issue it from an unbound handle, or give each tenant its own store file.")
        toms = self._tombstones                                  # full chain (content-free) so it verifies from genesis
        scoped = [t for t in toms if request_id is None or t.get("request_id") == request_id]
        erased_ids = sorted({t.get("memory_id") for t in scoped if t.get("memory_id")})
        ok, problems = self.verify_writes(expected_pubkey)
        return {
            "inspeximus_erasure_certificate": "1.0",
            "issued_ts": time.time(),
            "issued_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # EXPLICIT scope marker. `request_ids` drops None, so a verifier could not tell "unscoped" from
            # "scoped to exactly these" — and an honest UNSCOPED certificate from a store where any erasure
            # ran without a request_id failed its own chain. Measured.
            "scoped_to": request_id,
            "request_ids": sorted({t.get("request_id") for t in scoped if t.get("request_id") is not None}),
            "erased_memory_ids": erased_ids,
            "count": len(erased_ids),
            "tombstones": toms,                                  # full signed chain for independent re-verification
            "pubkey": self.receipt_pubkey,
            "anchor": self.anchor(),
            "self_check": {"verified": ok, "problems": problems},
            "scope": _CERT_SCOPE,
            "verify_with": "inspeximus.verify_erasure_certificate(cert, store_path=<file>)  # or store_items=<list>",
        }

    def governance_report(self, expected_pubkey: str | None = None) -> dict:
        """ONE auditor-facing surface for erasure-with-proof — the compliance view of forget_subject, built for
        the right-to-erasure demand (GDPR Art.17) that an EU-AI-Act operator has to satisfy while keeping an
        auditable record of the ACT (Art.30). It stitches the three primitives an auditor would otherwise call
        separately — the tombstone ledger, the per-request breakdown, and the tamper-evidence verdict — into a
        single report, and states in-band exactly what it does and does NOT certify.

        Returns {erasures_total, by_request:{request_id:{erased, memory_ids}}, proof:{verified, problems,
        all_signed, expected_pubkey}, scope}. `proof.verified` is verify_writes() over BOTH the write-receipt
        chain and the deletion-tombstone chain — a forged or dropped tombstone (hiding a real out-of-band
        delete) shows up here.

        HONEST SCOPE (read before relying on it for compliance): erasure is WITHIN this inspeximus store only — NOT
        the app's vector store, prompt logs, or backups — and it covers the subject PLUS its derived_from
        lineage (a summary built from the subject's data is erased too). It is a tamper-evident INTEGRITY
        primitive, NOT a compliance certification. The tombstone proves the ACT of deletion (a record with this
        surrogate id was erased at T for request R), never the CONTENT (a hash of PII is still PII). The
        signature is load-bearing only against a party who does NOT hold receipt_key (the operator who holds
        the key can forge tombstones too — anchor the chain head externally for operator-adversarial audit).
        Prior art: crypto-shredding; Cassandra / event-sourcing tombstones; GDPR Art.17/30 erasure logs;
        Crosby-Wallach / Certificate Transparency tamper-evident logs."""
        ok, problems = self.verify_writes(expected_pubkey)
        toms, _withheld = self._visible_tombstones()
        by_req: dict = {}
        for t in toms:
            by_req.setdefault(t.get("request_id"), []).append(t.get("memory_id"))
        return {
            "erasures_total": len(toms),
            "by_request": {rid: {"erased": len(ids), "memory_ids": sorted(i for i in ids if i)}
                           for rid, ids in by_req.items()},
            "proof": {
                "verified": ok,
                "problems": problems,
                "all_signed": bool(toms) and all("sig" in t for t in toms),
                "expected_pubkey": expected_pubkey,
                # honest trust level of the signatures (the footgun made visible to the auditor):
                "signature_authenticity": ("pinned to expected_pubkey" if expected_pubkey else
                                           "self-referential — a store-rewriter can swap the key; pin "
                                           "expected_pubkey or witness anchor() externally"),
                # CT-style anchor: a compact, externally-witnessable commitment to the whole history, so an
                # auditor can detect an operator (key-holder) rewrite via verify_consistency() against a prior
                # witnessed anchor — the operator-adversarial hole verify_writes cannot close on its own.
                "anchor": self.anchor(),
            },
            "scope": ("Erasure is within THIS inspeximus store only (not the app's vector store, prompt logs, or "
                      "backups); covers the subject PLUS its derived_from lineage. Tamper-evident integrity "
                      "primitive, NOT a compliance certification. The tombstone proves the ACT of deletion, "
                      "never the content; its signature is load-bearing only against a non-holder of "
                      "receipt_key. Anchor the chain head externally for operator-adversarial audit."),
        }

    @staticmethod
    def _chain_core(rec: dict, kind: str) -> dict:
        """THE definition of a receipt's hash preimage. Every producer and every verifier must use this one.

        `amends` was added to the hash in 1.68.0 in `_emit_write_receipt` and `verify_writes` only, and this
        function -- shared by `_recompute_tip` (which `anchor()` uses) and `audit_bundle._rewalk` -- was
        left behind. The result: after any slash()/restore(), verify_writes() said clean while
        verify_bundle() reported "write chain breaks", and anchor() silently committed to a TRUNCATED chain
        (n_writes=1 with two receipts present), because _recompute_tip falls back to the last prefix it can
        re-derive. Four definitions of one preimage, and the fix reached two of them."""
        if kind == "write":
            core = {k: rec.get(k) for k in ("seq", "ts", "memory_id", "commit", "prev")}
            if rec.get("amends"):
                core["amends"] = rec["amends"]
            # `amend_reason` joins the preimage HERE, in the one shared definition, for the reason this
            # docstring already records: 1.68.0 added `amends` to the emitter and the verifier and missed
            # `_recompute_tip` and `audit_bundle._rewalk`, and anchor() then committed to a truncated chain.
            # A reason outside the hash would be worse than none: anyone with file access could append a
            # flattering one to a laundering amendment, or strip an inconvenient one, and the chain would
            # still verify. Committed, it can be contradicted but not rewritten.
            if rec.get("amend_reason"):
                core["amend_reason"] = rec["amend_reason"]
            return core
        return Inspeximus._tombstone_core(rec)                                                   # tombstone

    def _recompute_tip(self, records, n: int, kind: str):
        """Re-derive the hash-chain tip over the FIRST n records from genesis, verifying each record's own
        hash and prev-link as it goes. Returns the tip hash, or None if the prefix is internally inconsistent
        (a record whose stored hash doesn't match its recomputed content, or a broken prev-link)."""
        prev = _GENESIS
        for r in records[:n]:
            if r.get("prev") != prev:
                return None
            h = _sha256_hex(_canon(Inspeximus._chain_core(r, kind)))
            if h != r.get("hash"):
                return None
            prev = h
        return prev

    def explain_growth(self, prior_anchor: dict, writes: int = 0, amendments: int = 0,
                       erasures: int = 0) -> dict:
        """Reconcile the chain's GROWTH since an anchor against what the caller says it did.

        A hash chain proves nobody rewrote the past. It says nothing about whether the entries appended
        SINCE are ones you asked for -- and that is the whole of the post-compromise gap (Schneier &
        Kelsey, USENIX Security 1998): after an attacker can write, new entries are attacker-chosen and
        internally valid. Laundering a record costs exactly ONE extra receipt, and nothing in the chain
        marks it as unexpected, because from the chain's point of view it is an ordinary amendment.

        The missing piece is a DENOMINATOR, and only the application has it: you know how many writes,
        amendments (slash/restore) and erasures you performed. This compares that against what the chain
        actually grew by, and itemises the difference.

        `prior_anchor` is an anchor you took earlier -- ideally one witnessed externally, since an
        attacker who can edit the store can also edit an anchor you left beside it.

        Returns {ok, expected, actual, unexplained, prefix_intact, problems}. `unexplained` itemises the
        surplus receipts (seq, memory_id, kind) so an operator gets a place to look, not just a count.

        HONEST SCOPE: this detects UNEXPECTED growth. It cannot tell an attacker's amendment from your
        own if you under-count, and a caller who passes whatever makes it pass has built a gate that
        cannot fail. It is also blind to an attacker who appends nothing -- content substitution with no
        new receipt is `bind_content`'s job, not this one.
        """
        problems: list = []
        n_before = int((prior_anchor or {}).get("n_writes") or 0)
        witnessed_tip = str((prior_anchor or {}).get("writes_tip") or "")

        prefix_intact = True
        if witnessed_tip:
            redrived = self._recompute_tip(self._receipts, n_before, "write")
            prefix_intact = (redrived == witnessed_tip)
            if not prefix_intact:
                problems.append(
                    "the witnessed history no longer re-derives: the receipts up to this anchor do not "
                    "reproduce its tip, so the past was rewritten (not merely appended to)")

        added = self._receipts[n_before:]
        n_amend = sum(1 for r in added if r.get("amends"))
        n_plain = len(added) - n_amend
        n_tomb_before = int((prior_anchor or {}).get("n_tombstones") or 0)
        n_erased = max(0, len(self._tombstones) - n_tomb_before)

        expected = {"writes": int(writes), "amendments": int(amendments), "erasures": int(erasures)}
        actual = {"writes": n_plain, "amendments": n_amend, "erasures": n_erased}

        unexplained: list = []
        surplus_plain = n_plain - int(writes)
        surplus_amend = n_amend - int(amendments)
        if surplus_plain > 0:
            for r in [x for x in added if not x.get("amends")][-surplus_plain:]:
                unexplained.append({"seq": r.get("seq"), "memory_id": r.get("memory_id"), "kind": "write"})
        if surplus_amend > 0:
            for r in [x for x in added if x.get("amends")][-surplus_amend:]:
                unexplained.append({"seq": r.get("seq"), "memory_id": r.get("memory_id"),
                                    "kind": "amendment", "amends": r.get("amends")})
        if surplus_plain > 0 or surplus_amend > 0:
            problems.append(
                f"the chain grew by more than you accounted for: {surplus_plain} unexplained write(s) and "
                f"{surplus_amend} unexplained amendment(s). An amendment you did not make is what "
                f"laundering an edited record costs.")
        if actual["erasures"] > expected["erasures"]:
            problems.append(f"{actual['erasures'] - expected['erasures']} erasure(s) you did not account "
                            f"for; each leaves a signed tombstone, so they are attributable")
        if surplus_plain < 0 or surplus_amend < 0:
            problems.append(
                "the chain grew by LESS than you accounted for -- receipts you expected are missing, which "
                "is not something an append-only chain should be able to do")

        return {"ok": not problems, "expected": expected, "actual": actual,
                "unexplained": unexplained, "prefix_intact": prefix_intact, "problems": problems}

    def anchor(self, sign=None) -> dict:
        """Emit a SIGNED HEAD COMMITMENT — a compact, EXTERNALLY-publishable commitment

        NOT a Merkle "signed tree head", and the difference is a capability, not a name. RFC 6962's STH is the
        signed ROOT OF A MERKLE TREE, and that structure is what buys O(log n) INCLUSION proofs ("record X is in
        the log") and succinct consistency proofs a client can check WITHOUT holding the log. This is a hash
        CHAIN: `verify_consistency` re-derives the tip over the full prefix, so the verifier must possess the
        log, and there is NO inclusion proof at all. We borrow CT's WITNESSING MODEL (untrusted log + external
        witnesses make append-only violations detectable) — not its proof system. Corrected 2026-08-08 after a
        SCITT feasibility check: IETF SCITT Receipts MUST support inclusion proofs, so this structure cannot
        emit one, and the old wording promised a proof capability the code does not have.

        to the entire write + tombstone history at this instant: {n_writes, writes_tip, n_tombstones,
        tombstones_tip, ts}. Because each chain is hash-linked, its tip hash commits to every prior entry, so
        publishing this anchor to a place the operator cannot retroactively alter (a public log, a witness, the
        auditor's own records) closes the one hole verify_writes/governance_report cannot: an operator who HOLDS
        receipt_key can rewrite the whole history AND re-sign it so it verifies internally — but they cannot make
        the rewritten tip equal an anchor an outsider already witnessed. This is the CT model (Laurie-Langley-
        Kasper RFC 6962): the log is untrusted; external witnesses + consistency proofs make append-only violations
        detectable without trusting the log operator. `sign(bytes)->hex` (OPT-IN) lets an EXTERNAL witness co-sign
        the anchor; inspeximus deliberately does NOT sign it with receipt_key (that key is the very thing not trusted
        here). HONEST BOUNDARY: inspeximus produces the anchor and the consistency proof; the external WITNESSING (that
        the auditor recorded a prior anchor out of band) is the auditor's job — without a prior witnessed anchor
        there is nothing to be consistent WITH."""
        writes_tip = self._receipts[-1]["hash"] if self._receipts else _GENESIS
        tomb_tip = self._tombstones[-1]["hash"] if self._tombstones else _GENESIS
        sth = {"n_writes": len(self._receipts), "writes_tip": writes_tip,
               "n_tombstones": len(self._tombstones), "tombstones_tip": tomb_tip,
               "ts": time.time()}
        # RFC 6962 roots, ADDITIVE. The chain tips stay exactly as they were, byte for byte, so an
        # anchor a witness co-signed before this version still verifies and `verify_consistency`
        # is untouched. What the roots ADD is the proof the chain cannot give: an inclusion proof
        # ("this record is in your log") checkable in O(log n) by someone who does NOT hold the log.
        # `sth_hash` deliberately still covers only the four original fields -- widening it would
        # change the bytes every existing witness signature was made over.
        sth["writes_root"] = self.merkle_root("write")
        sth["tombstones_root"] = self.merkle_root("tombstone")
        sth["merkle"] = "rfc6962-sha256"
        sth["sth_hash"] = _sha256_hex(_canon({k: sth[k] for k in
                                              ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip")}))
        # A SECOND commitment over the roots, so a witness that understands them can bind them too
        # without invalidating one that does not. Both are published; a verifier uses what it knows.
        sth["root_hash"] = _sha256_hex(_canon({k: sth[k] for k in
                                               ("n_writes", "writes_root", "n_tombstones",
                                                "tombstones_root", "merkle")}))
        if sign is not None:
            try:
                sth["witness_sig"] = sign(bytes.fromhex(sth["sth_hash"]))
            except Exception as e:
                # An unsigned anchor was returned, byte-identical to anchor() with no signer at all — so the
                # caller who asked for external witnessing (the ONLY operator-adversarial property here)
                # could not tell it had not happened.
                raise RuntimeError(
                    f"the witness signer raised ({type(e).__name__}: {e}); refusing to return an anchor that "
                    f"looks unsigned when co-signing was requested") from None
        return sth

    def _merkle_leaves(self, kind: str = "write") -> list[bytes]:
        """The leaf DATA for each entry: the same canonical bytes the hash chain commits to.

        Reusing `_chain_core` is deliberate -- the leaf is then the record's CONTENT, so an inclusion
        proof proves "this record is in the log", not merely "something occupied slot i".
        """
        records = self._receipts if kind == "write" else self._tombstones
        return [_canon(Inspeximus._chain_core(r, kind)) for r in records]

    def merkle_root(self, kind: str = "write") -> str:
        """RFC 6962 Merkle Tree Hash over the write (or tombstone) log, as hex."""
        from inspeximus.merkle import root as _mroot
        return _mroot(self._merkle_leaves(kind)).hex()

    def inclusion_proof(self, index: int, kind: str = "write") -> dict:
        """Prove entry `index` is in the log, in O(log n), WITHOUT handing over the log.

        This is the capability a hash chain cannot provide at any cost, and the one IETF SCITT
        requires of a Receipt. The returned bundle is self-contained: give it plus a root the
        verifier already trusts to `verify_inclusion` and they can check it offline, with no access
        to the store and no trust in its operator.
        """
        from inspeximus.merkle import inclusion_proof as _proof
        leaves = self._merkle_leaves(kind)
        path = _proof(leaves, index)
        return {"kind": kind, "index": index, "tree_size": len(leaves),
                "leaf": leaves[index].decode("utf-8"),   # canonical JSON, safe to carry as text
                "audit_path": [h.hex() for h in path],
                "root": self.merkle_root(kind), "merkle": "rfc6962-sha256"}

    @staticmethod
    def verify_inclusion(bundle: dict, expected_root: str | None = None) -> bool:
        """Check an inclusion_proof() bundle offline. Pass the root YOU witnessed, not the one in the
        bundle -- a bundle that carries its own root and is checked against it proves nothing, which
        is why `expected_root` defaults to None and falls back only for a self-consistency check."""
        from inspeximus.merkle import verify_inclusion as _vi
        try:
            root_hex = expected_root or bundle.get("root")
            return _vi(str(bundle["leaf"]).encode("utf-8"), int(bundle["index"]),
                       int(bundle["tree_size"]),
                       [bytes.fromhex(h) for h in bundle.get("audit_path", [])],
                       bytes.fromhex(str(root_hex)))
        except Exception:
            return False

    def merkle_consistency_proof(self, m: int, kind: str = "write") -> dict:
        """O(log n) proof that the log is an append-only extension of its own first `m` entries --
        the succinct version of verify_consistency, checkable without a replica of the log."""
        from inspeximus.merkle import consistency_proof as _cp
        leaves = self._merkle_leaves(kind)
        return {"kind": kind, "m": m, "n": len(leaves),
                "proof": [h.hex() for h in _cp(leaves, m)],
                "root": self.merkle_root(kind), "merkle": "rfc6962-sha256"}

    def verify_consistency(self, prior_anchor: dict) -> tuple[bool, list[str]]:
        """Prove the current log is an APPEND-ONLY extension of a previously-witnessed anchor() — the check an
        auditor runs against an anchor they recorded out of band. Re-derives each chain's tip over its first
        prior_anchor['n_*'] entries and confirms it equals the anchored tip, AND that the log did not shrink.
        A mismatch means the operator REWROTE or ROLLED BACK history after the anchor — caught even though they
        hold receipt_key and the rewrite verifies internally. Returns (ok, problems)."""
        problems: list[str] = []
        for kind, records, ntag, tiptag in (("write", self._receipts, "n_writes", "writes_tip"),
                                            ("tombstone", self._tombstones, "n_tombstones", "tombstones_tip")):
            n0 = int(prior_anchor.get(ntag, 0))
            if len(records) < n0:
                problems.append(f"{kind} log shrank: {len(records)} < anchored {n0} (rolled back / truncated)")
                continue
            tip = self._recompute_tip(records, n0, kind)
            if tip is None:
                problems.append(f"{kind} chain broken within the first {n0} entries (a prior entry was altered)")
            elif tip != prior_anchor.get(tiptag):
                problems.append(f"{kind} history rewritten after the anchor: tip {tip[:12]}.. != "
                                f"anchored {str(prior_anchor.get(tiptag))[:12]}.. (fork detected)")
        return (len(problems) == 0, problems)

    @staticmethod
    def verify_cosigned_anchor(anchor: dict, cosignatures, witnesses, threshold: int = 1) -> dict:
        """CLIENT-side k-of-n trust on an anchor: how many DISTINCT allowlisted witnesses validly co-signed
        THIS anchor's sth_hash? An operator that forks the history must get `threshold` independent witnesses
        to co-sign the forked head — honest witnesses refuse (witness_cosign), so a fork cannot reach the
        threshold without corrupting them; that is the upgrade over 'trust the operator'. `cosignatures` =
        iterable of (pubkey_hex, sig_hex). `witnesses` = the allowlist: a set/list of pubkey_hex, OR a
        {pubkey_hex: class} map so Sybil variants declared to one class collapse to a single vote
        (independence is a DECLARED grouping the allowlist curator owns — the store enforces it but cannot
        prove two classes are causally independent). Returns {ok, count, threshold, signers,
        covers_history[, limits, error]}; ok = count >= threshold. Read-only; needs no access to the log.
        Needs `cryptography`.

        THREE THINGS THIS REFUSES TO REPORT AS SUCCESS, each a verifier passing over nothing:
        the anchor's `sth_hash` is re-derived from its own fields first, so genuine signatures over a
        SUBSTITUTED n_writes/writes_tip yield `error` rather than a co-signed verdict; `threshold` below 1
        is rejected, because a quorum of zero is met by an anchor no witness ever signed; and an anchor
        over an empty receipt chain reports `covers_history: False` plus a `limits` line, since a valid
        co-signature over a history of nothing is evidence about no stored data at all."""
        if not _HAVE_ED:
            raise RuntimeError("verifying witness co-signatures needs the `cryptography` package")
        # `covers_history` is a property of the ANCHOR, not of the verdict, so it is computed once here and
        # reported on every return path. Returning it only on success would leave the refusal paths with a
        # field that reads False for a head that covers plenty -- a declared field answering a question it
        # never looked at.
        _a = anchor if isinstance(anchor, dict) else {}
        covers = (_int_or(_a.get("n_writes"), 0) + _int_or(_a.get("n_tombstones"), 0)) > 0
        # A QUORUM OF ZERO IS NOT A QUORUM. threshold<=0 made `count >= threshold` true for an anchor with
        # NO signatures, an EMPTY allowlist, and no witnesses in existence -- ok=True, signers=[]. That is
        # the vacuous pass: every check in the function is a comparison, and comparisons over an empty set
        # all succeed. A caller that computes k from `len(configured_witnesses)` and lands on 0 because the
        # config failed to load got "externally witnessed" for free.
        if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
            return {"ok": False, "count": 0, "threshold": threshold, "signers": [], "covers_history": covers,
                    "error": f"threshold must be an integer >= 1 (got {threshold!r}); a threshold of 0 or "
                             f"less is satisfied by an anchor no witness ever signed"}
        h = anchor.get("sth_hash") if isinstance(anchor, dict) else None
        try:
            msg = bytes.fromhex(h) if isinstance(h, str) else None
        except ValueError:
            msg = None
        if not msg:
            return {"ok": False, "count": 0, "threshold": threshold, "signers": [],
                    "covers_history": covers, "error": "anchor has no valid sth_hash"}
        # BIND THE SIGNATURE TO THE FIELDS THE CALLER WILL READ. The signatures below cover the sth_hash
        # STRING only; every consumer of the verdict then reads n_writes/writes_tip (verify_consistency
        # pins a store to them, detect_split_view compares them). Without re-deriving the hash from those
        # fields, an operator could keep a genuine sth_hash + genuine signatures, paste in the tip of a
        # REWRITTEN history, and collect ok=True from 3 of 3 honest witnesses -- after which
        # verify_consistency certified the rewrite and flagged the honest store as the fork.
        if not anchor_binds_its_fields(anchor):
            return {"ok": False, "count": 0, "threshold": threshold, "signers": [],
                    "covers_history": covers,
                    # ASCII: this string is printed by `inspeximus witness verify` onto a console that is
                    # not always UTF-8 (cp1250 here), where an em dash renders as a replacement character.
                    "error": "anchor sth_hash does not commit to this anchor's own fields "
                             "(n_writes/writes_tip/n_tombstones/tombstones_tip) - a co-signature over it "
                             "would authenticate a head no witness saw; refusing to count it"}
        allow = set(witnesses); cls = witnesses if isinstance(witnesses, dict) else {}
        classes, signers = set(), []
        for item in (cosignatures or []):
            if not (isinstance(item, (list, tuple)) and len(item) == 2):
                continue
            pk, sg = item
            if not (isinstance(pk, str) and isinstance(sg, str)) or pk not in allow:   # malformed rejects, never crashes
                continue
            try:
                _Ed25519PK.from_public_bytes(bytes.fromhex(pk)).verify(bytes.fromhex(sg), msg)
            except Exception:
                continue
            c = cls.get(pk, pk)
            if c not in classes:
                classes.add(c); signers.append(pk)
        count = len(classes)
        # EMPTY SCOPE. store.anchor() over a store with no receipt chain is a perfectly valid signed head
        # of NOTHING: witnesses co-sign it, this returns ok=True, and the report reads "3 of 3 witnesses
        # co-signed" over zero writes and zero erasures. That is a true sentence about an empty history,
        # and it is exactly how a verifier misleads -- so `ok` keeps its narrow contract (did k allowlisted
        # witnesses sign THIS head) and the scope is reported alongside it rather than folded into it.
        # `ok=True, covers_history=False` is a signature over an empty log, not evidence about any data.
        out = {"ok": count >= threshold, "count": count, "threshold": threshold, "signers": signers,
               "covers_history": covers}
        if not covers:
            out["limits"] = ["this anchor commits to 0 writes and 0 erasures: a valid co-signature over an "
                             "EMPTY history, which is evidence about no stored data at all"]
        return out

    @staticmethod
    def detect_split_view(anchor_a: dict, cosigs_a, anchor_b: dict, cosigs_b, witnesses) -> dict:
        """AUDITOR-side fork proof: given two co-signed anchors — e.g. the head the operator showed client A
        and the one it showed client B — is there a witness that validly co-signed BOTH over an INCONSISTENT
        pair of heads (the SAME log size carrying a DIFFERENT tip)? One such witness is cryptographic proof of
        a split-view: an honest witness refuses that second signature (witness_cosign), so a valid double-sign
        means the operator presented divergent histories (or that witness is dishonest — either way, a detected
        fork). Returns {fork, inconsistent, at, evidence, both_cosigned}: `inconsistent` = the two heads
        disagree at a shared size; `evidence` = witnesses that validly signed BOTH (the proof); `both_cosigned`
        = both heads independently carry >=1 valid allowlisted co-signature. HONEST LIMIT: decidable from signed
        head commitments alone ONLY at a SHARED size — if the logs differ in size, append-only-vs-fork needs a
        consistency proof (verify_consistency), reported here as inconsistent=False (undetermined)."""
        def _int(x, d):
            try:
                return int(x)
            except (TypeError, ValueError):
                return d
        a = anchor_a if isinstance(anchor_a, dict) else {}
        b = anchor_b if isinstance(anchor_b, dict) else {}
        inconsistent, where, diff_size = False, [], False
        for ntag, tiptag in (("n_writes", "writes_tip"), ("n_tombstones", "tombstones_tip")):
            na, nb = _int(a.get(ntag), -1), _int(b.get(ntag), -2)
            if na == nb and a.get(tiptag) != b.get(tiptag):
                inconsistent = True; where.append(ntag)         # same size, different tip = decidable fork
            elif na != nb:
                diff_size = True                                # different size on this chain -> not STH-decidable
        va = Inspeximus.verify_cosigned_anchor(a, cosigs_a, witnesses, threshold=1)
        vb = Inspeximus.verify_cosigned_anchor(b, cosigs_b, witnesses, threshold=1)
        common = sorted(set(va["signers"]) & set(vb["signers"]))
        # An anchor whose sth_hash does not commit to its own fields yields no valid signers, so it would
        # otherwise leave the auditor reading "inconsistent heads, no witness proof" -- when the real
        # answer is "one of these is not a head any witness could have signed". Say which.
        malformed = [side for side, anc in (("a", a), ("b", b)) if not anchor_binds_its_fields(anc)]
        # undetermined: no decidable inconsistency AND the heads differ in size, so append-only-vs-fork
        # cannot be settled from signed head commitments alone — run verify_consistency against a replica.
        undetermined = (not inconsistent) and diff_size
        return {"fork": bool(inconsistent and common), "inconsistent": inconsistent, "undetermined": undetermined,
                "at": where, "evidence": common, "both_cosigned": bool(va["ok"] and vb["ok"]),
                "malformed": malformed,
                "note": ("anchor(s) " + "/".join(malformed) + " do not bind their own fields - not a head "
                         "store.anchor() produced" if malformed else
                         "different-size heads: run verify_consistency against a replica to settle append-only"
                         if undetermined else "")}

    def retract_lineage(self, subject: str, reason: str = "lineage_corrected",
                        allow_ambiguous: bool = False) -> dict:
        """Lineage-aware correction: the MIDDLE PATH between a value-only supersession (which leaves records
        DERIVED from a now-corrected fact still active — the knowledge-editing 'ripple effect', Cohen et al.
        RippleEdits, TACL 2024) and forget_subject (which HARD-DELETES the lineage, losing the legitimate
        payload entangled in those derived facts). retract_lineage DEMOTES `subject` and every record that
        inherited it through derived_from taint to status='superseded' — excluded from default recall but
        RETAINED (recallable with include_superseded) and stamped needs_rederivation, so an app can re-derive
        the affected facts against the corrected root rather than lose them. This is retract-and-retain +
        dependency-directed propagation — classic Truth-Maintenance (Doyle, AIJ 1979) and provenance/bitemporal
        invalidation-with-retention, recently ported to LLM-agent memory (TOKI, arXiv 2606.06240; MemLineage,
        arXiv 2605.14421); inspeximus's contribution is only that it rides the same derived_from taint as forget_
        subject, so it needs no separate graph. CAVEAT: it can only cascade on links that were actually
        recorded — derived writes that never carried derived_from are invisible to it. `subject` matches
        canonical sources exactly like forget_subject. Returns {demoted, ids}. Reversible: nothing is deleted;
        only status + meta change."""
        cand, _sel, _coll = self._resolve_subject(subject, allow_ambiguous)
        if _coll and not allow_ambiguous:
            raise Inspeximus._ambiguous_error(subject, _coll, "retract_lineage")
        _ok = set(_sel)
        targets = [r for r in self._tenant_rows()
                   if r.get("status") == "active" and r["id"] in _ok]
        now = time.time()
        ids = []
        for r in targets:
            r["status"] = "superseded"
            r["invalidated_at"] = now
            meta = r.setdefault("meta", {})
            meta["retracted_reason"] = reason
            meta["needs_rederivation"] = True
            self._declare_retired(r, f"lineage retracted: {reason}")
            ids.append(r["id"])
        if ids:
            self._mat = None; self._mat_built_n = -1        # status change alters the recall pool
            self._save(force=True)
        return {"demoted": len(ids), "ids": sorted(ids)}

    def rederive(self, subject: str, rewrite=None, key: str | None = None,
                 allow_ambiguous: bool = False) -> dict:
        """Complete the correction lifecycle: REGENERATE the derived facts that retract_lineage demoted, against
        the corrected root — so the payload entangled in a poisoned lineage (a connection-string location, a
        backup schedule) comes back as ACTIVE facts asserting the corrected value, with clean derived_from
        lineage to the corrected root. corrupt -> launder -> correct -> retract_lineage -> rederive is the full
        loop; without this step the demoted facts stay out of active recall and the agent has simply lost them.

        Flow: find records stamped needs_rederivation for `subject` (not yet rederived), read the OLD value from
        the retracted keyed root and the NEW value from the key's current active record (write the correction
        BEFORE calling this), rewrite each derived record's text, and re-remember it with derived_from -> the
        corrected root (so a future correction can cascade again).

        `rewrite(text, old_value, new_value) -> new_text | None` is caller-supplied — pass an LLM-backed
        function for paraphrased facts. The DEFAULT is deterministic and honest: verbatim value substitution;
        a derived fact that does not contain the old value verbatim is SKIPPED (returned in `skipped`), never
        guessed. Each demoted record is stamped rederived_to (single-shot; a repeat call won't duplicate).
        Returns {rederived, skipped, ids, old_value, new_value}."""
        cand, _sel, _coll = self._resolve_subject(subject, allow_ambiguous)
        if _coll and not allow_ambiguous:
            raise Inspeximus._ambiguous_error(subject, _coll, "rederive")
        _ok = set(_sel)
        flagged = [r for r in self._tenant_rows()
                   if (r.get("meta") or {}).get("needs_rederivation")
                   and not (r.get("meta") or {}).get("rederived_to")
                   and r["id"] in _ok]
        if not flagged:
            return {"rederived": 0, "skipped": 0, "ids": [], "old_value": "", "new_value": ""}
        root = next((r for r in flagged if r.get("key")), None)
        k = key or (root.get("key") if root else None)
        if not k:
            return {"rederived": 0, "skipped": len(flagged), "ids": [],
                    "note": "no key resolvable from the retracted lineage; pass key="}
        old_v = str((root or {}).get("object") or "")
        cur_id = self._current_active_id(k)
        cur = next((r for r in self.items if r["id"] == cur_id), None)
        new_v = str((cur or {}).get("object") or "")
        if not cur or not new_v or new_v == old_v:
            return {"rederived": 0, "skipped": len(flagged), "ids": [],
                    "note": "no corrected current value for the key — write the correction first"}
        if rewrite is None:
            def rewrite(text, old, new):
                if old and old.lower() in (text or "").lower():
                    return re.sub(re.escape(old), new, text, flags=re.IGNORECASE)
                return None                       # paraphrase: needs a caller-supplied (LLM) rewrite; skip
        done, skipped, ids, failed = 0, 0, [], []
        for r in flagged:
            if r.get("key"):                      # the root itself is replaced by the correction, not rederived
                continue
            try:
                nt = rewrite(r.get("text", ""), old_v, new_v)
            except Exception as e:
                # A rewriter that RAISED is not the same as a fact that could not be rewritten. Folding both
                # into `skipped` told the caller "paraphrased, nothing to do" when their LLM was down, and
                # they never retried. Counted separately so a retry is possible.
                nt = None
                failed.append({"id": r["id"], "error": f"{type(e).__name__}: {e}"})
            if not nt or nt == r.get("text"):
                skipped += 1
                continue
            rid = self.remember(nt, tags=r.get("tags"), value=r.get("value", 1.0), mtype=r.get("mtype"),
                                derived_from=[cur_id, r["id"]], meta={"rederived_from": r["id"]})
            r.setdefault("meta", {})["rederived_to"] = rid
            done += 1
            ids.append(rid)
        if done:
            self._save(force=True)
        out = {"rederived": done, "skipped": skipped, "ids": ids,
               "old_value": old_v, "new_value": new_v}
        if failed:
            out["failed"] = failed          # the rewriter raised on these -- retryable, unlike `skipped`
        return out

    def _current_active_id(self, key: str) -> str:
        """id of the record currently CURRENT for `key` (the thing a revert would undo), or "" if none.
        The capability binds to this so a captured authorization cannot be REPLAYED after the state moves
        or RETARGETED to another key."""
        act = [r for r in self.items if r.get("key") == key and r.get("status") == "active"]
        if not act:
            return ""
        return max(act, key=lambda r: r.get("valid_from", r["ts"]))["id"]

    def revert_challenge(self, key: str) -> str:
        """The exact message a revert authorization must be issued over: "revert:{key}:{current_active_id}".
        The principal signs THIS (out of band) to authorize undoing the current value of `key`. Surfaced so an
        asymmetric holder (who only has the private key, not the store) knows what to sign; route() also
        returns it on an authorization_required result."""
        return "revert:" + key + ":" + self._current_active_id(key)

    def revert_capability(self, key: str) -> str:
        """SYMMETRIC mint (needs `revert_authority`, the harness-held secret): HMAC(secret, challenge). Defends
        the CONTENT path (text can't mint it) but the harness holding the secret can — for a store whose own box
        must not be trusted to mint, use `revert_pubkey` + module-level sign_revert() instead (only the off-box
        private key signs, the store only verifies)."""
        if self.revert_authority is None:
            raise RuntimeError("no revert_authority set (symmetric mint); use revert_pubkey for asymmetric")
        return hmac.new(self.revert_authority.encode(), self.revert_challenge(key).encode(),
                        hashlib.sha256).hexdigest()

    def _revert_authorized(self, key: str, capability: str | None) -> bool:
        """True if this restore is allowed. No authority configured -> always (legacy). Symmetric
        (revert_authority) -> capability must equal revert_capability(key) (constant-time). Asymmetric
        (revert_pubkey) -> capability is an Ed25519 signature (hex) by the principal's key over
        revert_challenge(key); the store VERIFIES it but cannot MINT it, so a compromised on-box harness still
        cannot authorize a revert. Both bind to the current active id (anti-replay / anti-retarget)."""
        if self.revert_authority is None and self.revert_pubkey is None:
            return True
        if not capability:
            return False
        if self.revert_pubkey is not None:
            if not _HAVE_ED:
                raise RuntimeError("verifying a revert signature needs the `cryptography` package")
            try:
                _Ed25519PK.from_public_bytes(bytes.fromhex(self.revert_pubkey)).verify(
                    bytes.fromhex(capability), self.revert_challenge(key).encode())
                return True
            except Exception:
                return False
        return hmac.compare_digest(self.revert_capability(key), capability)

    def revert(self, key: str, capability: str | None = None) -> dict:
        """CONTROL-PLANE revert: restore the value that the current active record for `key` superseded.
        The ledger knows what "the old one" is — no value token needed.

        If the store was created with `revert_authority`, this requires `capability` = revert_capability(key)
        (an out-of-band token the content path cannot mint); a missing/wrong one returns
        {"ok": False, "reason": "authorization_required"} and changes nothing. This is the AUTHENTICATION half:
        an unmarked "go back" and a stale echo are byte-identical, so the tie-break cannot come from the text —
        it comes from an authority whose origin an attacker who can only write text cannot author.

        Why an explicit API and not a content write: a value-OBSCURING reversion utterance ("go back
        to the old one", "the earlier value was right") carries NO object to key on, so no content-level
        mechanism can distinguish a legitimate user revert from an attacker-injected one — the two are
        byte-identical text differing only in provenance. inspeximus resolves this by CHANNEL SEPARATION:
        content writes can never undo a supersession (echo_guard retires restatements; an object-less
        utterance never touches the key at all), and reverting is possible ONLY through this explicit
        call, which the harness invokes for an authorized principal. Honest boundary: this moves the
        legitimate-vs-injected decision from the store to the calling agent — a store cannot make it
        (identical content, different provenance), but it CAN guarantee that content alone never flips
        a corrected value, which is the property injected text needs.

        Target selection is deterministic from the supersession ledger: the record whose
        `superseded_by_toggle` points at the current active record (i.e. exactly what the current value
        replaced), never an echo-blocked arrival (those were retired stale-on-arrival, they were never
        the current value). Append-only: history is not edited — the revert writes a NEW record with
        reaffirm=True (the one sanctioned path past the echo guard), so the flip is itself a ledgered,
        attributable event. Returns {"ok": True, "restored": id, "superseded": id, ...} or
        {"ok": False, "reason": ...}."""
        if not self._revert_authorized(key, capability):
            return {"ok": False, "reason": "authorization_required",
                    "challenge": self.revert_challenge(key)}
        same_key = [r for r in self._tenant_rows() if r.get("key") == key]
        active = [r for r in same_key if r.get("status") == "active"]
        if not active:
            return {"ok": False, "reason": "no active record for key"}
        cur = max(active, key=lambda r: r.get("valid_from", r["ts"]))
        prev = [r for r in same_key
                if r.get("status") == "superseded"
                and (r.get("meta") or {}).get("superseded_by_toggle") == cur["id"]
                and not (r.get("meta") or {}).get("echo_blocked")
                and not (r.get("meta") or {}).get("objectless_blocked")]
        if not prev:
            return {"ok": False, "reason": "no superseded predecessor for key"}
        tgt = max(prev, key=lambda r: r.get("valid_from", r["ts"]))
        # THE STORE DECLARES ITS OWN DERIVATION. A revert rebuilds a record's text from a specific prior
        # record, so the edge is not a guess -- it is already written two lines down as meta['revert_of'].
        # Until 1.51.0 it lived ONLY there, and meta is not a field provenance() or erasure_audit() walk, so
        # a restored value looked parentless to every lineage check. Measured across our own 27,290-record
        # deployment, declared lineage was 0.00%; content-based inference was tried and withdrawn in 1.50.0
        # at precision 0.06-0.23. This is the third option and the only one that is exact: at a call site the
        # store OWNS, the parent is known, so state it.
        rid = self.remember(tgt["text"], tags=tgt.get("tags"), value=tgt.get("value", 1.0),
                            mtype=tgt.get("mtype"), key=key, object=tgt.get("object"),
                            reaffirm=True, capability=capability, derived_from=[tgt["id"]],
                            meta={"revert_of": tgt["id"], "reverted_from": cur["id"]})
        return {"ok": True, "restored": rid, "superseded": cur["id"],
                "reverted_to_object": tgt.get("object"), "reverted_to_text": tgt["text"]}

    # ── IN-STREAM revert (0.7.12, design by jacksonxly r/RAG): scheduling, not acceptance ────────
    # The optimistic model (revert_challenge/revert_capability above) snapshots the current active id and
    # then RACES the writer to redeem it: under sustained same-slot writes it starves by construction, and
    # the only optimistic rescue (accepting a slightly stale base) is a bounded-N replay window. The
    # in-stream model instead signs the COMMAND (an intent carrying its own precondition + a single-use
    # nonce) and evaluates it at its position in the per-key write stream:
    #   - a RELATIVE intent ("go back", base = the active id at mint) lands iff its base is still current,
    #     else returns a CLEAN CONFLICT — a first-class outcome distinct from authorization_required. A
    #     relative revert over a moved base does not deserve to land (landing it anyway IS replay).
    #   - an ABSOLUTE intent (a named historical target) lands deterministically regardless of intervening
    #     writes — an absolute target was never a stale cap. Single-use via the nonce ledger.
    # Net: unconditional liveness for named reverts, bounded evaluation with clean conflict for relative
    # ones, replay window stays 1. (In-process the stream IS the call order; multi-actor fairness is the
    # caller's scheduling duty — an unfair writer-priority scheduler can still tail-latency the reverter.)

    def revert_intent(self, key: str, nonce: str | None = None) -> str:
        """Mint point for a RELATIVE in-stream revert: "revert:{key}@{base_id}#{nonce}". The principal signs
        THIS string (sign_revert / HMAC); base = the active id now, so the precondition travels inside the
        signed command instead of being re-derived at redeem time."""
        nonce = nonce or hashlib.sha256(os.urandom(16)).hexdigest()[:16]
        return "revert:" + key + "@" + self._current_active_id(key) + "#" + nonce

    def restore_intent(self, key: str, target: str, nonce: str | None = None) -> str:
        """Mint point for an ABSOLUTE in-stream revert to a NAMED historical value: no precondition, so it
        lands regardless of intervening writes — exactly once (the nonce is single-use). ABA-immune (0.7.15,
        jacksonxly): the intent also carries the ID of the specific historical record that held `target` at
        mint time, so it revives THAT instance, never a same-value look-alike re-asserted (or legitimately
        re-killed) in the gap. If no such record exists yet, the id is empty and submit falls back to value
        resolution (and reports it)."""
        nonce = nonce or hashlib.sha256(os.urandom(16)).hexdigest()[:16]
        held = [r for r in self.items
                if r.get("key") == key and r.get("object") == str(target)
                and not (r.get("meta") or {}).get("echo_blocked")
                and not (r.get("meta") or {}).get("objectless_blocked")]
        tid = max(held, key=lambda r: r.get("valid_from", r["ts"]))["id"] if held else ""
        return "restore:" + key + "=" + str(target) + "@" + tid + "#" + nonce

    def _intent_authorized(self, intent: str, capability: str | None) -> bool:
        """Same crypto as _revert_authorized, but over the INTENT string (the signed command)."""
        if self.revert_authority is None and self.revert_pubkey is None:
            return True
        if not capability:
            return False
        if self.revert_pubkey is not None:
            if not _HAVE_ED:
                raise RuntimeError("verifying a revert signature needs the `cryptography` package")
            try:
                _Ed25519PK.from_public_bytes(bytes.fromhex(self.revert_pubkey)).verify(
                    bytes.fromhex(capability), intent.encode())
                return True
            except Exception:
                return False
        want = hmac.new(self.revert_authority.encode(), intent.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, capability)

    def _nonce_consumed(self, nonce: str) -> bool:
        if nonce in self._consumed_revert_nonces:
            return True
        # landed intents persist their nonce in the ledgered record, so single-use survives a reload
        return any((r.get("meta") or {}).get("revert_nonce") == nonce for r in self.items)

    def submit_revert(self, intent: str, capability: str | None = None) -> dict:
        """Evaluate a signed revert INTENT at this position in the write stream. Outcomes are first-class:
        {"ok": True, ...} landed · {"ok": False, "reason": "conflict"} the relative base moved (definitive,
        not a retry loop — re-issue or name a target) · "replay_rejected" nonce already consumed ·
        "authorization_required" bad/missing capability · "unknown_target" absolute target never held the
        key. Consumes the nonce on evaluation, landed or not."""
        if not self._intent_authorized(intent, capability):
            return {"ok": False, "reason": "authorization_required", "intent": intent}
        m_rel = re.match(r"^revert:(.+)@([0-9a-f]*)#([0-9a-f]+)$", intent)
        m_abs = re.match(r"^restore:(.+?)=([^@#]*)(?:@([0-9a-f]*))?#([0-9a-f]+)$", intent)
        if not m_rel and not m_abs:
            return {"ok": False, "reason": "malformed_intent", "intent": intent}
        nonce = m_rel.group(3) if m_rel else m_abs.group(4)
        if self._nonce_consumed(nonce):
            return {"ok": False, "reason": "replay_rejected"}
        self._consumed_revert_nonces.add(nonce)
        if m_rel:
            key, base = m_rel.group(1), m_rel.group(2)
            cur_id = self._current_active_id(key)
            if base != cur_id:
                return {"ok": False, "reason": "conflict", "key": key, "base_id": base,
                        "current_id": cur_id,
                        "note": "base moved; a relative revert over a moved base does not deserve to land"}
            same_key = [r for r in self.items if r.get("key") == key]
            active = [r for r in same_key if r.get("status") == "active"]
            if not active:
                return {"ok": False, "reason": "no active record for key"}
            cur = max(active, key=lambda r: r.get("valid_from", r["ts"]))
            prev = [r for r in same_key
                    if r.get("status") == "superseded"
                    and (r.get("meta") or {}).get("superseded_by_toggle") == cur["id"]
                    and not (r.get("meta") or {}).get("echo_blocked")
                    and not (r.get("meta") or {}).get("objectless_blocked")]
            if not prev:
                return {"ok": False, "reason": "no superseded predecessor for key"}
            tgt = max(prev, key=lambda r: r.get("valid_from", r["ts"]))
            rid = self._stamp(tgt["text"], tags=tgt.get("tags"), value=tgt.get("value", 1.0),
                                mtype=tgt.get("mtype"), key=key, object=tgt.get("object"),
                                reaffirm=True, capability=_SANCTIONED, derived_from=[tgt["id"]],
                                meta={"revert_of": tgt["id"], "reverted_from": cur["id"],
                                      "revert_nonce": nonce, "instream": "relative"})
            return {"ok": True, "kind": "relative", "restored": rid, "superseded": cur["id"],
                    "reverted_to_object": tgt.get("object")}
        key, target, tid = m_abs.group(1), m_abs.group(2), m_abs.group(3)
        chain = self._route_chain(key)
        # ABA-immune (0.7.15): if the intent carries the id of the specific record it was minted against, that
        # exact instance must still exist and still have held `target` — a re-asserted same-value look-alike is
        # a different id and will NOT satisfy it. Fall back to value resolution only for legacy id-less intents.
        id_bound = bool(tid)
        rec = None
        if id_bound:
            rec = next((r for r in self.items if r.get("id") == tid and r.get("key") == key
                        and r.get("object") == target), None)
            if rec is None:
                return {"ok": False, "reason": "unknown_target", "key": key, "target": target,
                        "target_id": tid, "id_bound": True,
                        "note": "the specific record this restore was minted against is not in history"}
        elif target not in chain:
            return {"ok": False, "reason": "unknown_target", "key": key, "target": target,
                    "note": "an absolute intent can only restore a value that actually held the key"}
        if chain and chain[-1] == target:
            return {"ok": True, "kind": "absolute", "restored": None, "target": target,
                    "id_bound": id_bound, "note": "target already current (no-op land)"}
        # The ABSOLUTE path has no `tgt` -- that name is bound only in the RELATIVE branch above, which
        # returns before reaching here. Referencing it raised UnboundLocalError on EVERY absolute restore
        # that had something to do (an existing, non-current target), so submit_revert's absolute half and
        # restore_now -- the documented "mint + submit in ONE call" liveness primitive -- were both dead on
        # arrival. 572 tests covered the relative path only. Resolve the source record explicitly:
        # id-bound intents already have it; legacy value-resolved intents take the most recent record that
        # actually held the value, so the lineage edge points at real history rather than nothing.
        src_rec = rec if rec is not None else max(
            (r for r in self.items if r.get("key") == key and r.get("object") == target),
            key=lambda r: r.get("valid_from", r.get("ts", 0.0)), default=None)
        rid = self._stamp(f"restore {key} to {target}", key=key, object=target,
                            reaffirm=True, capability=_SANCTIONED,
                            derived_from=([src_rec["id"]] if src_rec else None),
                            meta={"routed": "revert_named_instream", "revert_nonce": nonce,
                                  "instream": "absolute", "restore_of_id": tid or None})
        return {"ok": True, "kind": "absolute", "restored": rid, "target": target, "id_bound": id_bound}

    # ── the LIVENESS FLOOR (0.7.13, jacksonxly r/RAG): the store owns no-infinite-bypass ─────────
    # jackson's boundary: the store must GUARANTEE a submitted revert can't be bypassed unboundedly
    # (worst case "lands later" = harness policy; worst case "never lands" = a store liveness property).
    # In this synchronous store the floor holds BY CONSTRUCTION: submit_revert is terminal — it evaluates
    # atomically against the current state on the call itself and either lands or conflicts, it is never
    # left "pending" for writes to bypass. So the maximum bypass of a submitted revert is ZERO; a harness
    # can only choose WHEN the call runs (deprioritize -> lands later), never turn it into never-evaluated.
    # revert_now / restore_now make that a first-class primitive: mint + submit in ONE call, so a caller
    # cannot wedge writes into the mint->submit window and hand-roll a starvation-prone pattern. "If a
    # caller can break it, it isn't a guarantee, it's a hope" — so the land-now path is the store's, not
    # something every caller re-implements.

    def restore_now(self, key: str, target: str, sign=None, capability: str | None = None) -> dict:
        """ABSOLUTE revert, atomic: mint + submit with no gap. The absolute path owes the LAND, so this
        lands (exactly once) regardless of intervening writes. `sign(intent)->cap` for the asymmetric
        (revert_pubkey) store; `capability` for the symmetric one; neither for a no-authority store."""
        intent = self.restore_intent(key, target)
        cap = capability if capability is not None else (sign(intent) if sign else None)
        return self.submit_revert(intent, cap)

    def revert_now(self, key: str, sign=None, capability: str | None = None) -> dict:
        """RELATIVE revert, atomic: mint + submit with zero gap, so the only failure is a genuine
        same-instant conflict (the value already moved), never a bypass/starvation from writes sneaking
        into the mint->submit window. The relative path owes FAIRNESS: evaluated now, lands or conflicts."""
        intent = self.revert_intent(key)
        cap = capability if capability is not None else (sign(intent) if sign else None)
        return self.submit_revert(intent, cap)

    # ── value-obscuring reversion classifier (0.7.14): the Marat decomposition, shipped ─────────
    def classify_reversion(self, candidate: str, key: str, embed=None,
                           margin: float = 0.06, floor: float = 0.50) -> dict:
        """Classify whether `candidate` reopens a SUPERSEDED value for `key` ("revert"), affirms the current
        one ("keep"), or does not resolve ("abstain"). This is the value-obscuring reversion result from the
        joint TAT/inspeximus analysis (Marat Sultanov), factorized into its two independent halves and shipped:

          1. REFERENCE RESOLUTION (a text problem): embed the candidate and, using the ledger's own split of
             the key's history into SUPERSEDED (old) and CURRENT records, measure how much closer the
             candidate sits to the old side than the current side. Needs an embedder (`self.embed` or the
             `embed` arg); with none it abstains rather than guessing. This is the structural-similarity step,
             scored as a MARGIN (max sim to old records minus max sim to current records) — the same
             discriminating quantity the decomposition used, not an absolute similarity.
          2. RECENCY ATTRIBUTION (a ledger problem): the old-versus-current split is read straight from
             inspeximus's supersession ledger. No text method is asked to decide which value is current.

        Abstains when the reference does not DISCRIMINATE old from current: |margin| < `margin` (a bare
        "go back" is roughly equally near both, so it names no side) or the best match is below `floor` (an
        off-topic utterance). That is exactly the boundary the analysis measured — where a guess is wrong and
        the authorized-revert channel (submit_revert) is the correct path instead.

        CLASSIFIES ONLY, never restores: a content-path utterance must not flip a corrected value without an
        out-of-band authorization. Returns {intent, target, confidence, current} for an authorized caller to
        act on with submit_revert — consistent with the channel-separation design.
        """
        e = embed or self.embed
        if e is None:
            return {"intent": "abstain", "reason": "no_embedder"}
        recs = [r for r in self.items
                if r.get("key") == key and r.get("object") is not None
                and not (r.get("meta") or {}).get("echo_blocked")
                and not (r.get("meta") or {}).get("objectless_blocked")]
        if len(recs) < 2:
            return {"intent": "abstain", "reason": "insufficient_history"}
        cur_id = self._current_active_id(key)
        current_val = next((r["object"] for r in recs if r["id"] == cur_id), recs[-1]["object"])
        try:
            cvec = list(e(candidate))
        except Exception:
            return {"intent": "abstain", "reason": "embed_failed"}

        def sim(r):
            v = r.get("vec")
            if not v:
                try:
                    v = list(e(r["text"]))
                except Exception:
                    return None
            return _cosine(cvec, v)

        old_scored = [(sim(r), r) for r in recs
                      if not (r["id"] == cur_id or r.get("object") == current_val)]
        cur_scored = [(sim(r), r) for r in recs
                      if r["id"] == cur_id or r.get("object") == current_val]
        old_scored = [(s, r) for s, r in old_scored if s is not None]
        cur_scored = [(s, r) for s, r in cur_scored if s is not None]
        if not old_scored or not cur_scored:
            return {"intent": "abstain", "reason": "no_vectors"}
        best_old_sim, best_old = max(old_scored, key=lambda x: x[0])
        best_cur_sim = max(s for s, _ in cur_scored)
        if max(best_old_sim, best_cur_sim) < floor:
            return {"intent": "abstain", "reason": "unresolved_reference",
                    "confidence": round(max(best_old_sim, best_cur_sim), 3)}
        m = best_old_sim - best_cur_sim
        if abs(m) < margin:
            return {"intent": "abstain", "reason": "unresolved_reference",
                    "margin": round(m, 3)}
        if m > 0:
            return {"intent": "revert", "target": best_old.get("object"), "current": current_val,
                    "margin": round(m, 3),
                    "note": "content-path signal only; restore via submit_revert with authorization"}
        return {"intent": "keep", "current": current_val, "margin": round(m, 3)}

    # ── route(): the write-path intent router (tagger + fuzzy-version resolver) ─
    _ROUTE_REVERT = re.compile(
        r"\b(go back|put .{0,24}back|roll ?back|revert|undo|restore|switch .{0,24}back|set .{0,24}back"
        r"|back to (what|the (original|previous|first|initial))|the way it was|change it back"
        r"|what we (had|started with)|very first|initial pick)\b")
    _ROUTE_ORIGINAL = re.compile(r"\b(original|very first|started with|initial)\b")
    _ROUTE_CORRECT = re.compile(r"\b(correction|actually|update|scratch that|is now|moved to"
                                r"|was switched|changed to)\b")
    _ROUTE_CHANGE_AWARE = re.compile(r"\b(changed|moved|switched|updated|correction|went through)\b")
    _ROUTE_DELETE = re.compile(r"\b(forget|delete|remove|erase|scrub|wipe|drop) (that|this|it|the|my|about)"
                               r"|\bno longer (true|valid|the case|relevant|applies)"
                               r"|\bdisregard (that|this|the)|\bthat'?s? (wrong|no longer)|\bnever ?mind\b")

    def _route_chain(self, key: str) -> list[str]:
        """values that were actually CURRENT at some point for `key`, oldest->newest — skips arrivals the
        guards retired stale-on-arrival (echo_blocked / objectless_blocked were never the current value)."""
        chain = []
        for r in self.items:
            if r.get("key") != key or r.get("object") is None:
                continue
            # THE SAME ALLOWLIST recall() uses, for the same reason. The router is the SECOND read
            # path and it never got the gate: with no status filter, a `provisional` row nobody has
            # confirmed -- or a `discarded` one a verifier explicitly REJECTED -- contributed its
            # object to the version chain and, being written later, became chain[-1], i.e. "the
            # current value". Measured 2026-08-15: with a discarded db-EVIL in the chain, the
            # genuine correction route()d to {"action":"noop","note":"value already current"} and
            # was never written -- permanently, 3/3 retries -- while the store kept serving db-7.
            # An authorized absolute restore likewise returned ok:true, burned its single-use nonce
            # and changed nothing; and a discarded row was itself a valid restore TARGET, so a
            # rejected value could be made active.
            #
            # An allowlist, not a denylist, for the reason `_eligible` already records: `discarded`
            # was born after the denylist would have been written, and the next status will be too.
            if r.get("status") not in _RECALLABLE:
                continue
            m = r.get("meta") or {}
            if m.get("echo_blocked") or m.get("objectless_blocked"):
                continue
            if not chain or chain[-1] != r["object"]:
                chain.append(r["object"])
        return chain

    def _route_key(self, low: str) -> str | None:
        """Match the utterance to a ledgered key at WORD BOUNDARIES (longest key wins).

        Plain `in` was an unbounded substring test, and route() executes reverts and deletes on the result:
        "go back to the earlier **heart** condition" matched the key `art` and reverted it, unconfirmed,
        because a default store has no revert authority configured. A key must appear as a word, not as a
        fragment of one — the same word-boundary rule the rest of this file already uses for values."""
        keys = {r["key"] for r in self.items if r.get("key") and r.get("object") is not None}
        hits = [k for k in keys
                if re.search(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"(?![a-z0-9])", low)]
        return max(hits, key=len) if hits else None

    def route(self, text: str, key: str | None = None, object: str | None = None,
              context: str | None = None, policy: str = "safe", capability: str | None = None,
              source=None) -> dict:
        """WRITE-PATH INTENT ROUTER: tag an utterance (assert / correct / revert / echo), resolve a fuzzy
        version reference against the key's timeline, and execute the right ledger operation — so a
        value-obscuring revert ("go back to what we had") works without the caller naming a value, and a
        similarity/cosine path never runs on a revert (a revert is an instruction on the version graph,
        not a value). This ships the split measured in inspeximus/probes/intent_tagger_router_probe.py.

        Resolution (deterministic, no LLM):
          - a revert-marked utterance -> revert. Target: a named historical value if present in the text;
            "original / very first / started with" -> the FIRST version; otherwise the predecessor via
            revert(). Restores go through the sanctioned reaffirm channel, so the flip is ledgered.
          - a value-bearing utterance whose value is new or current -> remember() (keyed supersession).
          - a value-bearing utterance whose value was SUPERSEDED for the key, with no revert marker ->
            the ambiguous echo-or-reaffirm case, and `policy` decides (see below).
        key/object are derived from the extractor hook when not passed; a revert with no resolvable key
        falls back to a plain note (never guesses a ledger key it can't match).

        THE HONEST LIMIT (measured, not asserted): an unmarked restatement of a superseded value is
        AMBIGUOUS BY CONSTRUCTION — a stale echo and a deliberate reaffirm can be byte-identical, so no
        classifier (LLMs measured at ~coin-flip: 0.35-0.55) can separate them from text. `policy` picks
        the failure mode you accept:
          - "safe" (default): treat as an echo — never restores. With echo_guard on it lands retired
            (judge-logged 'echo_guard'); with echo_guard off it is written WITHOUT the key so it cannot
            LWW-clobber the current value. Cost: a legitimate unmarked reaffirm is refused (measured
            1.00 echo-blocked / 0.00 reaffirm-honored — reproduce with
            `python probes/echo_policy_panel.py`, which needs no dataset, no network and no LLM).
          - "context": restore when `context` (the preceding turn) shows change-awareness (a change word
            + the current value). Separates honest twins (1.00/1.00) but is FORGEABLE — an attacker who
            writes two turns walks through it (forged-context echo restored 100%). Use only when the
            context channel is trusted.
          - "trusting": treat as a reaffirm — always restores (0.00 echo-blocked / 1.00 honored).
        The unforgeable separator is provenance — an authorized revert() call or an explicit marker —
        never smarter classification; that is the channel-separation thesis, now with the receipt.

        Returns {"intent", "action", "key", ...} describing what was done."""
        low = text.lower()
        # ONE rule for every write site in this function, not four copies of it. `route` has five places
        # that call remember(), and provenance was added to exactly one of them -- the correction -- while
        # a commit message said the hole was closed. The echo branch then wrote an unattributed record
        # holding the subject's address verbatim, and it survived her erasure.
        #
        # `_parent()` is the OWNED half: a correction, reaffirm or echo is about whatever the key already
        # holds, and the store knows which record that is, so it declares the edge rather than forwarding
        # a caller's argument. It returns None when there is no key or nothing current -- declaring a
        # parent that does not exist would be inventing provenance, which is the failure this month
        # produced twice.
        _src = {"doc": source} if isinstance(source, str) and source else source

        def _parent(k=None):
            # Takes the key EXPLICITLY, because the revert branches resolve their own
            # (`k = key or self._route_key(low)`) and closing over the `key` parameter would have looked
            # up a different key -- or None -- and silently declared no parent on exactly the branches
            # this was extended to cover. A helper that reads the wrong variable is worse than four
            # copies, because it looks like it was applied everywhere.
            kk = k if k is not None else key
            cur_rec = self._current_active(kk) if kk else None
            return [cur_rec["id"]] if cur_rec else None

        if (key is None or object is None) and self.extractor is not None:
            try:
                ex = self.extractor(text)
                if ex:
                    key = key if key is not None else ex[0]
                    object = object if object is not None else ex[1]
            except Exception:
                pass
        # DELETE intent ("forget/delete/remove that", "no longer true"). Content alone must NOT be able to destroy
        # memory (the channel-separation moat), so a routed delete is gated by the SAME capability as revert — an
        # unauthorized utterance gets authorization_required, never silent deletion. This is the mem0 DELETE event,
        # done safely: mem0 lets its LLM issue DELETE on the write path; inspeximus requires an out-of-band capability.
        # ORDERING: only a delete utterance that carries NO value and NO revert marker reaches this branch.
        # The delete vocabulary overlaps both of the branches below, and it used to run first, so
        # "drop the beta flag; region is now us-east" (a correction) and "undo that, it's no longer valid"
        # (a revert) were both swallowed as deletes and their writes never happened.
        if self._ROUTE_DELETE.search(low) and object is None and not self._ROUTE_REVERT.search(low):
            k = key or self._route_key(low)
            if k is None:
                # No key resolved, so there is nothing this is a restatement OF -- but the caller's
                # source is still a source, and dropping it made the record unattributable for no reason.
                rid = self.remember(text, source=_src)
                return {"intent": "delete", "action": "noted", "event": "NOOP", "key": None, "id": rid,
                        "reason": "no ledger key resolved to delete"}
            # A routed delete is IRREVERSIBLE (forget() is a hard delete of every active record for the key),
            # so it must NOT inherit _revert_authorized's "no authority configured -> allow (legacy)" rule.
            # That rule is safe for revert, which only moves along the version graph, but on a DEFAULT store it
            # hands plain content the power to destroy the ledger — exactly the moat this branch claims to hold.
            # Deleting therefore requires an authority to be CONFIGURED, and then satisfied.
            gated = self.revert_authority is not None or self.revert_pubkey is not None
            if not gated:
                return {"intent": "delete", "action": "authorization_required", "event": "DELETE", "key": k,
                        "challenge": None, "reason": "routed deletion is refused on a store with no "
                        "revert_authority/revert_pubkey configured — content alone must not destroy memory; "
                        "delete out of band with forget()/forget_subject()"}
            if not self._revert_authorized(k, capability):
                return {"intent": "delete", "action": "authorization_required", "event": "DELETE", "key": k,
                        "challenge": self.revert_challenge(k)}
            ids = [r["id"] for r in self.items if r.get("key") == k and r.get("status") == "active"
                   and r.get("tenant") == self.tenant]
            res = self.forget(ids=ids) if ids else {"forgotten": 0}
            # This IS an erasure the caller triggered, so it answers like one. forget() computed both
            # fields underneath and route dropped them on the floor -- the caller of route() had no way
            # to learn that a survivor still held what they just deleted.
            return {"intent": "delete", "action": "deleted", "event": "DELETE", "key": k,
                    "forgotten": res.get("forgotten", 0),
                    "coverage": res.get("coverage"),
                    "residue_in_store": res.get("residue_in_store")}
        if self._ROUTE_REVERT.search(low):
            k = key or self._route_key(low)
            if k is None:
                rid = self.remember(text, source=_src)      # same as the delete branch: no key, but a
                return {"intent": "revert", "action": "noted", "key": None, "id": rid,   # source is one
                        "reason": "no ledger key resolved from the utterance"}
            chain = self._route_chain(k)
            cur = chain[-1] if chain else None
            if not self._revert_authorized(k, capability):
                # content path cannot mint the capability; do NOT execute, hand the decision out of band
                return {"intent": "revert", "action": "authorization_required", "key": k,
                        "challenge": self.revert_challenge(k)}
            named = None
            for v in chain[:-1]:
                if re.search(rf"\b{re.escape(str(v).lower())}\b", low):
                    named = v
            if named is None and object is not None and object in chain[:-1]:
                named = object
            if named is not None and named != cur:
                # A restore writes ON A KEY, so it is about whatever that key holds -- the same argument
                # as the correction and echo branches, and these two were missed when those were fixed.
                # `_parent(k)` is passed the RESOLVED key, not the parameter, which may be None here.
                rid = self.remember(f"restore {k} to {named}", key=k, object=named, reaffirm=True,
                                    capability=capability, meta={"routed": "revert_named"},
                                    source=_src, derived_from=_parent(k))
                return {"intent": "revert", "action": "restored", "key": k, "target": named, "id": rid}
            if self._ROUTE_ORIGINAL.search(low) and len(chain) > 1 and chain[0] != cur:
                rid = self.remember(f"restore {k} to {chain[0]}", key=k, object=chain[0], reaffirm=True,
                                    capability=capability, meta={"routed": "revert_original"},
                                    source=_src, derived_from=_parent(k))
                return {"intent": "revert", "action": "restored", "key": k, "target": chain[0], "id": rid}
            res = self.revert(k, capability=capability)
            return {"intent": "revert", "action": "reverted" if res.get("ok") else "failed",
                    "key": k, **{kk: vv for kk, vv in res.items() if kk != "ok"}}
        if object is None or key is None:
            # No key means nothing to derive from, so `source` is the only lever here -- and it is the
            # caller's to pull. Inferring a subject from the text would be inventing one.
            rid = self.remember(text, key=key, object=object, source=_src, derived_from=_parent())
            return {"intent": "assert", "action": "remembered", "event": "ADD", "key": key, "id": rid}
        chain = self._route_chain(key)
        cur = chain[-1] if chain else None
        if object == cur:                                    # NOOP: value already current -> skip the duplicate write
            # "id": None is explicit, not incidental — every other branch returns an id, and callers written
            # against them (including the MCP route tool) would otherwise KeyError on a NOOP.
            return {"intent": "assert", "action": "noop", "event": "NOOP", "key": key, "id": None,
                    "note": "value already current; duplicate write skipped (dedup)"}
        if object not in chain:
            # A CORRECTION IS DERIVED FROM WHAT IT CORRECTS, and the store knows which record that is --
            # so it declares the edge itself, exactly as revert/submit_revert already do. This is not
            # bookkeeping. Measured before: alice's address is written with source hr/alice, corrected
            # through route(), and her right-to-erasure request then erased the OLD address and LEFT THE
            # NEW ONE, reporting success -- the correction carried no source and no lineage, so nothing
            # connected it to her. Erasure that deletes the stale value and keeps the current one is the
            # exact inverse of erasure. The same correction through remember(source=...) erased both.
            #
            # The caller can still name a source; when they do not, the lineage edge alone is enough for
            # forget_subject to reach the correction, because it cascades along derived_from.
            rid = self.remember(text, key=key, object=object, source=_src, derived_from=_parent())
            intent = "correct" if (cur is not None and self._ROUTE_CORRECT.search(low)) else "assert"
            event = "UPDATE" if cur is not None else "ADD"   # supersedes a prior value vs first value for the key
            return {"intent": intent, "action": "remembered", "event": event, "key": key, "id": rid}
        # unmarked assertion of a superseded value — the ambiguous echo-or-reaffirm case
        if policy == "trusting" or (
                policy == "context" and context and self._ROUTE_CHANGE_AWARE.search(context.lower())
                and cur is not None and str(cur).lower() in context.lower()):
            if not self._revert_authorized(key, capability):
                return {"intent": "reaffirm", "action": "authorization_required", "key": key,
                        "target": object, "challenge": self.revert_challenge(key)}
            rid = self.remember(text, key=key, object=object, reaffirm=True, capability=capability,
                                meta={"routed": f"reaffirm_{policy}"},
                                source=_src, derived_from=_parent())
            return {"intent": "reaffirm", "action": "restored", "key": key, "target": object, "id": rid}
        # THE SAME ARGUMENT AS THE CORRECTION BRANCH, and it was missed here. A reaffirm or an echo is a
        # RESTATEMENT of a value on a key, so it is about whatever that key already holds -- and route
        # knows which record that is. Measured before this: an echo record carrying "alice addr is 5 Elm
        # St and 9OakAve" survived forget_subject('hr/alice') with source=None and derived_from=None,
        # holding her address verbatim. Found by the in-store residue check, which flagged ok=false on a
        # branch nobody was looking at -- one write site fixed and three left is the class defect this
        # repository keeps recording, applied inside a single function this time.
        if self.echo_guard:
            rid = self.remember(text, key=key, object=object,      # guard retires it, judge-logged
                                source=_src, derived_from=_parent())
        else:
            # Deliberately KEYLESS so it cannot LWW-clobber the current value. Keyless is not the same as
            # unattributable: the lineage edge still connects it to the record it restates.
            rid = self.remember(text, meta={"routed": "echo_unkeyed"},
                                source=_src, derived_from=_parent())
        return {"intent": "echo", "action": "blocked", "key": key, "id": rid,
                "policy": policy, "note": "unmarked restatement of a superseded value; not restored"}

    @staticmethod
    def _event_time(valid_from, now: float) -> float:
        """Coerce a caller-supplied event time. Epoch number or ISO-8601 string; never a guess.

        Raises rather than defaulting on an unparseable value. Returning `now` there would stamp a
        record with an event time nobody asserted while `valid_from_source` said `declared`, which is
        worse than the crash it replaces because it is silent.
        """
        if valid_from is None:
            return now
        if isinstance(valid_from, (int, float)) and not isinstance(valid_from, bool):
            return float(valid_from)
        epoch = _iso_to_epoch(valid_from) if isinstance(valid_from, str) else None
        if epoch is None:
            raise ValueError(
                "valid_from must be a UTC epoch number or an ISO-8601 string such as "
                "'2024-03-01T00:00:00Z'; got %r" % (valid_from,))
        return float(epoch)

    def as_of(self, key: str, when: float, as_recorded: float | None = None) -> dict | None:
        """POINT-IN-TIME query: the value that was CURRENT for `key` at event-time `when` (a UTC
        epoch float). This is the bi-temporal 'as-of' / time-travel read — reconstruct history, not
        just the latest value. No graph DB: keyed supersession already stamps every record with a
        validity interval [valid_from, invalidated_at) (invalidated_at=None means still current), so
        the answer is the record whose interval contains `when`.

        Why it matters: a plain memory store only tells you the value NOW; audit, debugging, and
        'what did the agent believe when it made decision X' need the value as of that moment. A
        back-filled record (added later with an earlier valid_from) is placed by its event-time, so
        as_of reflects when facts were TRUE, not when they were written.

        Returns {object, text, valid_from, valid_from_source, invalidated_at, id} for the record valid
        at `when`, or
        None if nothing was known for `key` yet at that time. Ties (overlapping intervals from an
        unclean history) resolve to the latest valid_from <= when.

        BITEMPORAL (1.5.0): pass `as_recorded` (a transaction-time epoch) to reconstruct the KNOWLEDGE STATE as
        of that recording time — "what did we BELIEVE, at tx-time `as_recorded`, was true at valid-time `when`" —
        using only records written by then (ts <= as_recorded) with supersession recomputed within that set, so a
        correction recorded LATER cannot leak into the earlier belief. This is the second clock: valid-time
        (`when`, world truth) x transaction-time (`as_recorded`, what the store knew). Audit/replay: "what did the
        agent believe when it acted", provably, without the later correction contaminating the reconstruction."""
        if as_recorded is None:
            # RECOMPUTED from `valid_from` ordering, not read from the stored `invalidated_at`.
            #
            # This branch used to trust the stored field while the `as_recorded` branch below already
            # derived the interval end from the next record's `valid_from` -- two implementations of
            # one rule, and only one of them was safe. `invalidated_at` is mechanically written by
            # supersession, so it cannot be committed without churning the receipt chain; deriving it
            # means it does not need to be. Measured 2026-08-15: with the stored field trusted,
            # editing `net30.invalidated_at` and `net90.valid_from` together moved the 2024-06 answer
            # from net30 to net90 -- the store then says it believed a value at a date when it
            # believed the opposite -- and editing either one alone blanked the answer entirely.
            # Now the only field that decides is `valid_from`, and that one IS committed.
            #
            # The stored field is still written and still returned, as a cache and for display. It is
            # simply no longer load-bearing, which is the point: a tampered cache changes no answer.
            _rows = [r for r in self._tenant_rows() if r.get("key") == key]
            cands = [r for r in _rows if r.get("valid_from", r["ts"]) <= when]
            best = max(cands, key=lambda r: (r.get("valid_from", r["ts"]), r["ts"]), default=None)
            if best is not None:
                _bvf = best.get("valid_from", best["ts"])
                _nxt = [r.get("valid_from", r["ts"]) for r in _rows
                        if r.get("valid_from", r["ts"]) > _bvf]
                if _nxt and min(_nxt) <= when:
                    best = None            # a later record already took over before `when`
        else:
            # transaction-time filter: only records written by `as_recorded`; supersession recomputed within it
            # (a record is superseded only by a LATER-valid_from record that was itself already recorded by then).
            cands = [r for r in self._tenant_rows() if r.get("key") == key
                     and r.get("valid_from", r["ts"]) <= when and r["ts"] <= as_recorded]
            best = max(cands, key=lambda r: (r.get("valid_from", r["ts"]), r["ts"]), default=None)
        if best is None:
            return None
        out = {"object": best.get("object"), "text": best.get("text"),
               "valid_from": best.get("valid_from", best["ts"]),
               # WHERE THAT TIME CAME FROM, surfaced rather than merely stored. A field nothing reads
               # is decoration. Records written before this existed carry no value and report None:
               # unknown provenance is a different claim from "we defaulted it", and guessing `ingest`
               # for them would re-create the ambiguity this field was added to remove.
               "valid_from_source": best.get("valid_from_source"),
               "invalidated_at": best.get("invalidated_at"), "id": best["id"]}
        if as_recorded is None:
            # Report the DERIVED end, for the same reason the branch above selects on it: a caller
            # comparing `invalidated_at` against its own clock must not be handed a number the store
            # itself no longer trusts.
            _nxt = [r.get("valid_from", r["ts"]) for r in self._tenant_rows()
                    if r.get("key") == key and r.get("valid_from", r["ts"]) > out["valid_from"]]
            out["invalidated_at"] = min(_nxt) if _nxt else None
        if as_recorded is not None:
            nxt = [r.get("valid_from", r["ts"]) for r in self._tenant_rows()
                   if r.get("key") == key and r["ts"] <= as_recorded
                   and r.get("valid_from", r["ts"]) > out["valid_from"]]
            out["invalidated_at"] = min(nxt) if nxt else None   # invalidation AS KNOWN at as_recorded
            out["as_recorded"] = as_recorded
        return out

    def believed_at(self, key: str, as_recorded: float) -> dict | None:
        """The value the store would have returned as CURRENT for `key` if frozen at transaction-time
        `as_recorded` — the latest-asserted value known by then, ignoring any correction recorded AFTER. Answers
        'what did the agent believe when it acted at time T', for replay and audit. Returns
        {object, text, valid_from, id, as_recorded} or None."""
        cands = [r for r in self.items if r.get("key") == key and r["ts"] <= as_recorded]
        best = max(cands, key=lambda r: (r.get("valid_from", r["ts"]), r["ts"]), default=None)
        if best is None:
            return None
        return {"object": best.get("object"), "text": best.get("text"),
                "valid_from": best.get("valid_from", best["ts"]), "id": best["id"], "as_recorded": as_recorded}

    def _null_context(self, n: int = 12) -> str:
        """A same-store baseline for infer_lineage: text this write was NOT built from.

        Deterministic (a fixed stride, no RNG) so a write is reproducible, and drawn from the store's own
        records so it carries the same vocabulary — which is the whole point. Without it the overlap score
        measures how repetitive the corpus is, not what the write came from.
        """
        pool = [r for r in self.items if r.get("text") and r["id"] not in set(self._last_recall)]
        if len(pool) <= n:
            return " ".join(str(r.get("text") or "") for r in pool)
        stride = max(1, len(pool) // n)
        return " ".join(str(pool[i]["text"] or "") for i in range(0, stride * n, stride))

    @staticmethod
    def _overlap(new_text: str, recalled: str) -> float:
        """Fraction of the NEW text's content words that appeared in what was just recalled.

        Asymmetric on purpose: the question is "was this write built out of that recall", so the denominator
        is the new text. A short summary drawn from a long recall scores high (correct); a long new
        observation that happens to mention one recalled word scores low (also correct). Stopwords are
        dropped so shared grammar cannot carry the score, and a text with too few content words scores 0
        rather than being decided by noise.
        """
        stop = {"the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be", "been", "to", "of",
                "in", "on", "at", "for", "with", "that", "this", "it", "as", "by", "from", "has", "have",
                "had", "not", "no", "so", "if", "then", "than", "we", "i", "you", "they", "he", "she"}
        def words(s):
            return {w for w in re.findall(r"[a-z0-9]+", str(s or "").lower()) if w not in stop and len(w) > 2}
        nw = words(new_text)
        if len(nw) < 4:                 # too short to judge; refuse rather than guess
            return 0.0
        return len(nw & words(recalled)) / len(nw)

    def history(self, key: str) -> list[dict]:
        """The full validity timeline for `key`: every value it has held, in event-time order, each
        with its [valid_from, invalidated_at) interval, status, and — when it was retired — WHICH
        policy adjudicated the retirement (meta['superseded_by_policy']). The audit trail behind as_of()."""
        recs = [r for r in self._tenant_rows() if r.get("key") == key]
        recs.sort(key=lambda r: r.get("valid_from", r["ts"]))
        return [{"object": r.get("object"), "text": r.get("text"), "status": r.get("status"),
                 "valid_from": r.get("valid_from", r["ts"]), "invalidated_at": r.get("invalidated_at"),
                 "policy": (r.get("meta") or {}).get("superseded_by_policy"),
                 "id": r["id"]} for r in recs]

    def provenance(self, key: str | None = None, id: str | None = None) -> dict:
        """ONE call that answers "where did this fact come from, and how far does that answer bind?"

        The most-asked question of a memory layer is not "can you undo it" — it is "why do you hold this,
        and who told you". inspeximus already carries every part of the answer: the declared source and the
        lineage taint it inherited through summarization, the origin attestation, the supersession timeline
        with the policy that adjudicated each retirement, the evidence grade, and the write-receipt
        commitment that makes a later relabel loud. But they live in history() / grade() /
        verify_attribution() / anchor(). This assembles them for ONE fact, in the order an auditor asks.

        Pass `key=` (the supersession key — provenance of a FACT, across every value it has held) or `id=`
        (one record; if that record is keyed, its whole chain is still reported).

        Returns {key, found, current, origin, trust, timeline, integrity, limits}:
          - origin: the declared source; the canonical sources the record is attributable to (its own plus
            taint inherited transitively via derived_from); whether an origin ATTESTATION bound it to a
            verified key; the ancestors a retraction would reach; the orphan flag; and the acting
            user/agent/session when the caller supplied them.
          - trust: the evidence grade (claimed | corroborated | verified | settled), earned from external
            ratifications and corroboration — never settable by the writer.
          - timeline: history(key) — every value held, its validity interval, and WHICH policy retired it.
          - integrity: whether THIS record is covered by a write receipt; whether its content and its
            attribution still match what was committed at write time (relabel detection); whether the
            receipt chain verifies and is signed; plus the current anchor, so the answer can be pinned.

        Read-only. HONEST LIMITS (returned in `limits` too, so a caller rendering this cannot quietly drop
        them): provenance here is tamper-EVIDENT, not CORRECT — a source that was already wrong at write
        time is committed faithfully and nothing here can tell; and UNSIGNED (the default) the receipt
        chain only catches an editor who cannot ALSO rewrite the .receipts sidecar. Pass receipt_key=, or
        have anchor() witnessed externally, for the loud property to hold against a store-capable actor."""
        if (key is None) == (id is None):
            raise ValueError("provenance() takes exactly one of key= or id=")
        by_id = {x["id"]: x for x in self._tenant_rows()}
        if id is not None:
            rec = by_id.get(str(id))
            key = rec.get("key") if rec is not None else None
        else:
            key = str(key)
            same = [r for r in self._tenant_rows() if r.get("key") == key]
            same.sort(key=lambda r: r.get("valid_from", r["ts"]))
            active = [r for r in same if r.get("status") == "active"]
            rec = active[-1] if active else (same[-1] if same else None)
        out: dict = {"key": key, "found": rec is not None}
        limits = [
            "tamper-evident, not correct: a source that was wrong at write time is committed faithfully",
            "unsigned receipts only catch an editor who cannot also rewrite the .receipts sidecar: "
            "pass receipt_key= or have anchor() witnessed externally",
        ]
        if rec is None:
            out.update({"current": None, "origin": None, "trust": None, "timeline": [],
                        "integrity": None, "limits": limits})
            return out
        out["current"] = {"id": rec["id"], "text": rec.get("text"), "object": rec.get("object"),
                          "status": rec.get("status"), "mtype": rec.get("mtype"), "ts": rec.get("ts"),
                          "valid_from": rec.get("valid_from", rec["ts"])}
        # ── origin: declared source + the lineage a retraction would travel ──────────────────────────
        ancestors, unresolved, stack = [], [], list(rec.get("derived_from") or [])
        while stack:                                   # transitive: provenance rides through summarization
            pid = stack.pop()
            if pid in ancestors or pid in unresolved:
                continue
            p = by_id.get(pid)
            if p is None:
                unresolved.append(pid)                 # a dangling parent = lineage we cannot follow
                continue
            ancestors.append(pid)
            stack.extend(p.get("derived_from") or [])
        _meta = rec.get("meta") or {}            # the tenancy triple is stored under meta as uid/aid/sid
        actor = {name: _meta[short] for short, name in
                 (("uid", "user_id"), ("aid", "agent_id"), ("sid", "session_id")) if _meta.get(short)}
        out["origin"] = {
            "source": rec.get("source"),
            "canonical_sources": sorted(Inspeximus._rec_sources(rec)),
            "attested": bool(rec.get("attested_key")), "attested_key": rec.get("attested_key"),
            "derived": bool(rec.get("derived_from")), "derived_from": list(rec.get("derived_from") or []),
            "ancestors": ancestors, "unresolved_parents": unresolved,
            "inherited_taint": sorted(rec.get("taint") or []),
            "orphan": bool(rec.get("orphan")), "actor": actor or None,
        }
        out["trust"] = self.grade(rec, _by_id=by_id)
        out["timeline"] = self.history(key) if key else []
        out["superseded_count"] = sum(1 for r in out["timeline"] if r.get("status") == "superseded")
        # ── integrity: does the record still match what the receipt chain committed to? ──────────────
        integ: dict = {"receipted": False, "content_matches_receipt": None,
                       "attribution_matches_receipt": None, "chain_ok": None, "signed": False}
        receipts = getattr(self, "_receipts", None) or []
        mine = [r for r in receipts if r.get("memory_id") == rec["id"]]
        if mine:
            # THE FIRST RECEIPT, and EVERY committed field. Two defects lived in the three lines this
            # replaces, and both were the sibling surface's own lessons unlearned here.
            #
            # (1) It compared `content_sha256`, the PRE-1.68 composite of text+key+mtype. Since then
            #     the binding fields are immutable_sha256, value_sha256, status_sha256, time_sha256
            #     and attrib_sha256. So editing `object` -- the value the store SERVES, and what
            #     supersession, revert() and the echo guard all key on -- left provenance reporting
            #     `content_matches_receipt: True` while verify_writes on the same store said False.
            #     Measured 2026-08-15: 90d -> 30d on disk, one surface caught it and this one did not.
            #
            # (2) It read `mine[-1]`, the LATEST receipt. verify_writes carries a long comment about
            #     why that is a laundering path -- tamper out of band, append a well-formed amendment,
            #     and the latest receipt commits to the forged content. (I could not make that land
            #     through this surface in the fixture I built, so it is fixed as a structure, not
            #     reported as a reproduced exploit.) The first receipt is what binds; later ones may
            #     forgive only the fields they DECLARE, which is what `_AMENDABLE` is for.
            first = min(mine, key=lambda r: r.get("seq", 0))
            committed = first.get("commit") or {}
            latest = max(mine, key=lambda r: r.get("seq", 0))
            current = self._write_commit(rec)
            forgiven = {f for r in mine if r.get("seq", 0) > first.get("seq", 0)
                        for f in (r.get("amends") or ())} & _AMENDABLE
            integ["receipted"] = True
            integ["signed"] = "sig" in latest
            _fields = [k for k in ("immutable_sha256", "value_sha256", "status_sha256",
                                   "time_sha256", "content_sha256")
                       if k in committed and k not in forgiven]
            _bad = [k for k in _fields if committed.get(k) != current.get(k)]
            integ["content_matches_receipt"] = not _bad
            if _bad:
                integ["content_mismatch_fields"] = _bad
            integ["attribution_matches_receipt"] = (
                None if committed.get("attrib_sha256") is None       # written before attribution was committed
                else committed.get("attrib_sha256") == current["attrib_sha256"])
        if receipts:
            integ["chain_ok"] = self.verify_attribution().get("chain_ok")
        if receipts or getattr(self, "_tombstones", None):
            integ["anchor"] = self.anchor()
        else:
            limits.append("receipts are off: no write-time commitment exists to compare against "
                          "(construct with receipts=True)")
        out["integrity"] = integ
        out["limits"] = limits
        return out

    def decisions_in_force(self, *, tag: str = "decision", key_prefix: str = "decision::",
                           limit: int | None = None) -> list[dict]:
        """Every keyed decision that is CURRENT, ENUMERATED rather than searched.

        TWO KINDS OF DECISION, and only one of them has a "current" value. A decision recorded with
        `remember_decision(topic=...)` is a VALUE on a topic: it is keyed `decision::<topic>` and a
        later one on the same topic retires it, so asking "what is in force" is meaningful and the
        answer is one record. A commit message is an EVENT: keyed `commit::<sha>`, unique forever,
        and it does not retract its predecessor. Both are decisions and both belong in the store, but
        enumerating the second kind returns the whole project history -- on any real repository that
        is thousands of records, and it would bury the handful that are actually in force.

        So `key_prefix` defaults to the value namespace. Pass `key_prefix=""` to enumerate the current
        record for every key regardless of kind, or `"commit::"` for the commit log alone.

        Why this exists as a separate call. `recall()` finds a decision by similarity, and similarity is
        the wrong instrument for this question. Measured on the cross-session dogfood corpus (2,571
        records): the query "how does a release get published these days?" shares ZERO tokens with the
        decision it is asking for ("publish releases from the trusted publisher workflow"), so the
        current decision ranks 25th with the read-purity default and 29th with the library default,
        below a draft note that merely repeats the query's surface words. The decision was in the store
        the whole time and reachable; ranking put it out of reach of any sane k.

        But "which decision is in force on topic X" does not need ranking at all. The store already
        knows: keyed supersession leaves exactly one active record per key, so the answer is a scan,
        not a search. This returns it -- deterministic, O(n), no embedder, no model, and no dependence
        on whether a session digest happened to capture the decision when it was made.

        Ordering is newest-first with the id as the tie-break, so two calls on the same bytes return the
        same list in the same order. `tag` filters to decision-shaped records; pass tag=None to
        enumerate the current record for every key. Retired records are never returned, which is the
        half of the correction case that already worked -- this call fixes the other half.
        """
        # The tie-break is INSERTION ORDER, not the id. `ts` is whole seconds, so two decisions written
        # in the same second tie on it, and an id tie-break then orders them by a hash -- deterministic,
        # but "newest first" stops being true and `limit` returns an arbitrary one of the two. Position
        # in the store is the write order and is stable on disk, so it is both.
        best: dict = {}
        for pos, r in enumerate(self._tenant_rows()):
            if r.get("status") != "active":
                continue
            k = r.get("key")
            if not k:
                continue                      # an unkeyed decision cannot supersede, so it has no "force"
            if key_prefix and not str(k).startswith(key_prefix):
                continue
            if tag and tag not in (r.get("tags") or []):
                continue
            prev = best.get(k)
            if prev is None or (float(r.get("ts") or 0), pos) > (float(prev[0].get("ts") or 0), prev[1]):
                best[k] = (r, pos)
        out = [r for r, _ in sorted(best.values(),
                                    key=lambda rp: (-float(rp[0].get("ts") or 0), -rp[1]))]
        return out[:limit] if limit else out

    def supersession_report(self) -> dict:
        """Audit view of WHY memories were retired: a count of superseded records per adjudicating
        policy (keyed_lww / keyed_lww_backfill / keyed_reaffirm / echo_guard / objectless_guard /
        state_toggle / toggle_corroborated / toggle_persistence / keep_budget; 'unstamped' = retired
        before 0.6.18 or by an external edit). Every supersession site stamps
        meta['superseded_by_policy'] at write/consolidate time, so the resolver that adjudicated each
        conflict is inspectable per record — the write-time judge log TOKI (arXiv:2606.06240) points
        out most memory systems omit. Read-only; the raw rows stay untouched."""
        counts: dict = {}
        for r in self._tenant_rows():
            if r.get("status") != "superseded":
                continue
            p = (r.get("meta") or {}).get("superseded_by_policy") or "unstamped"
            counts[p] = counts.get(p, 0) + 1
        return {"superseded_total": sum(counts.values()), "by_policy": counts}

    # ── retrieval (value-ranked) ──────────────────────────────────────────────
    def _qvec(self, query: str, embedder=None):
        """Embed a query ONCE per scan, or None (no embedder / failure). Callers pass the result
        into _similarity so a recall over N memories costs 1 embedding, not N. `embedder` overrides
        the default `self.embed` — recall() passes `self.embed_query` so an asymmetric embedder (e.g.
        nomic-embed-text, which wants `search_query:` for queries vs `search_document:` for stored text)
        embeds the query correctly; internal callers embedding STORED text keep the document embedder."""
        emb = embedder or self.embed
        if not emb:
            return None
        try:
            return emb(query)
        except Exception:
            return None

    def _vec_matrix(self):
        """Cached L2-normalized matrix (numpy) of every memory that carries a vec — so a semantic
        recall is ONE matmul, not an O(N·d) pure-Python cosine loop. Rebuilt only when the item count
        changes (remember / bulk load); status changes (consolidate) don't touch the vectors."""
        if _np is None:
            return None
        if self._mat is None or self._mat_built_n != len(self.items):
            rows, ids = [], []
            for r in self.items:
                if r.get("vec"):
                    rows.append(r["vec"]); ids.append(r["id"])
            if rows:
                M = _np.asarray(rows, dtype=_np.float32)
                self._vec_mean = M.mean(axis=0)               # corpus mean (computed regardless; used to center)
                if self.center_embeddings:
                    M = M - self._vec_mean                    # de-anisotropise: remove the common component
                M /= (_np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
                self._mat = M
                self._vec_rowof = {i: k for k, i in enumerate(ids)}
            else:
                self._mat, self._vec_rowof, self._vec_mean = None, {}, None
            self._mat_built_n = len(self.items)
        return self._mat

    def _rec_tokens(self, rec: dict) -> set:
        """Token set for a memory, cached by id — recall over N memories shouldn't re-tokenize."""
        rid = rec.get("id") or id(rec)
        t = self._tok_cache.get(rid)
        if t is None:
            t = _tokens(rec["text"]); self._tok_cache[rid] = t
        return t

    def _retired_values(self) -> list:
        """Per key: (retired value strings, current value string). The read-side of supersession.

        WHY THIS EXISTS (measured). Supersession retires a
        RECORD, not a VALUE. In structured data those are the same thing; in conversational prose one
        value is smeared across a dozen sentences — the user states it, the assistant echoes it, a
        summary repeats it, a template quotes it — and retiring the single sentence that happened to
        carry a key accomplishes nothing. Measured on the MemOps corpus: 33,186 records, 5.2% keyed,
        0.33% superseded, and `Junior Data Analyst` alive in fifteen records after its correction.
        That is why the store tied a keep-everything baseline on stale-fact rate (0.2105 vs 0.1250).

        So the correction must be applied at READ time to the VALUE, not to the row: whatever the
        current value of a key is, every record asserting one of that key's retired values is stale,
        keyed or not. Deterministic, zero-LLM, and it is what the product already claims to do."""
        out = []
        by_key: dict = {}
        for r in self.items:
            k = r.get("key")
            if k:
                by_key.setdefault(str(k), []).append(r)
        for k, recs in by_key.items():
            cur = self._current_active(k)
            if not cur:
                continue
            cur_v = str(cur.get("object") or "").strip()
            if not cur_v:
                continue                                    # no explicit object -> no value to compare
            cur_tok = _tokens(cur_v)
            retired = []
            for r in recs:
                v = str(r.get("object") or "").strip()
                # A retired value is any OTHER object ever asserted under this key — not only the rows
                # supersession happened to mark, since marking is exactly what under-fires here.
                if len(v) < 4 or v.lower() == cur_v.lower():
                    continue
                # DISTINGUISHING tokens, not the raw string. Extracted objects carry conversational
                # tails ('Senior Data Analyst as of yesterday'), so full-string containment is the wrong
                # test in both directions: it fails to recognise the correction, and it lets a value
                # that is merely a TRUNCATION of the current one ('Data Analyst') look like a rival.
                # A retired value with no token of its own says nothing the current value does not —
                # it is a truncation, and suppressing on it withheld the very record stating the
                # current title (measured on A01_update). Skip it.
                v_tok = _tokens(v)
                mark = v_tok - cur_tok                      # what makes the retired value retired
                cur_mark = cur_tok - v_tok                  # ...and what makes the current one current
                if mark and cur_mark:
                    retired.append((v, mark, cur_mark))
            if retired:
                out.append((retired, cur.get("tenant"), cur.get("id")))
        return out

    def _stale_by_value(self, rec: dict, retired_map: list) -> str | None:
        """Does this record assert a retired value of some key, WITHOUT also asserting the current one?
        Returns the retired string it carries, else None. Decided on distinguishing TOKENS: a record is
        stale when it carries what makes the retired value retired ('junior') and none of what makes the
        current value current ('senior'), so 'your current title is Senior Data Analyst' is kept while
        'Summary: title Junior Data Analyst' is withheld."""
        rec_tok = self._rec_tokens(rec)
        low = (rec.get("text") or "").lower()
        for retired, tenant, cur_id in retired_map:
            if rec.get("id") == cur_id:
                continue
            if tenant is not None and rec.get("tenant") is not None and rec.get("tenant") != tenant:
                continue
            for v, mark, cur_mark in retired:
                if cur_mark & rec_tok:
                    continue                                # states the current value -> never stale
                if mark <= rec_tok and v.lower() in low:
                    return v
        return None

    def _rec_sig(self, rec: dict) -> str:
        """Normalized value signature: the record's token set, sorted and joined — identical restatements
        of one value collapse to one signature regardless of word order. Cached by id."""
        rid = rec.get("id") or id(rec)
        s = self._sig_cache.get(rid)
        if s is None:
            s = " ".join(sorted(self._rec_tokens(rec)))
            self._sig_cache[rid] = s
        return s

    def _resolve_read_conflicts(self, scored: list, k: int) -> tuple[list, dict]:
        """Read-time newest-VALUE-BIRTH conflict resolution over the top pool (see the recall() stage
        comment for semantics). Returns (reordered scored, {winner_id: [loser_ids]})."""
        bound = max(4 * k, 50)
        pool, tail = scored[:bound], scored[bound:]
        toks = [self._rec_tokens(t[2]) for t in pool]
        sigs = [self._rec_sig(t[2]) for t in pool]
        # birth of a VALUE = earliest assertion of its signature anywhere in the store, superseded rows
        # included — an echo restating a retired value inherits the retired birth and can never look fresh.
        # The birth key is (event_time, WRITE POSITION), not the bare timestamp. `ts` is a wall clock with
        # ~15 ms granularity here, so records written in a loop sometimes share a tick and sometimes do not:
        # measured, two values born in the same tick made this resolver pick a different winner between runs
        # and recall returned 5 distinct orders over 60 identical single-process runs, in EVERY mode. Position
        # says "asserted later" exactly and cannot drift, and it is unique per record, so the key is TOTAL —
        # which is why the old `s` (signature-string) tiebreak on the max below is gone rather than kept as a
        # third component that nothing can reach.
        birth: dict = {}
        for i, r in enumerate(self.items):
            sg = self._rec_sig(r)
            b = (r.get("valid_from") or r.get("ts") or 0, i)
            if sg not in birth or b < birth[sg]:
                birth[sg] = b
        clusters: list[list[int]] = []
        for i in range(len(pool)):
            placed = False
            for cl in clusters:
                j = cl[0]
                if sigs[i] == sigs[j]:
                    cl.append(i); placed = True; break
                a, b = toks[i], toks[j]
                if a and b and (len(a & b) / len(a | b)) >= 0.6:   # near-dup subject, different value
                    cl.append(i); placed = True; break
            if not placed:
                clusters.append([i])
        drop: set = set()
        losers: dict = {}
        for cl in clusters:
            if len(cl) < 2:
                continue
            by_val: dict = {}
            for i in cl:
                by_val.setdefault(sigs[i], []).append(i)
            if len(by_val) < 2:
                continue                                           # restatements of ONE value: dedup is MMR's job
            win_sig = max(by_val, key=lambda s: birth.get(s, (0, -1)))  # newest birth wins; the key is total
            winner = min(by_val[win_sig])                          # its highest-scored member
            lose = [i for i in cl if sigs[i] != win_sig]
            if lose:
                losers[pool[winner][2]["id"]] = [pool[i][2]["id"] for i in lose]
                drop.update(lose)
        if not drop:
            return scored, {}
        resolved = ([pool[i] for i in range(len(pool)) if i not in drop]
                    + [pool[i] for i in sorted(drop)] + tail)
        return resolved, losers

    def _rec_tokcount(self, rec: dict) -> dict:
        """Term-frequency map for a memory, cached by id (for the BM25 hybrid channel)."""
        rid = rec.get("id") or id(rec)
        c = self._tc_cache.get(rid)
        if c is None:
            c = _token_counts(rec["text"]); self._tc_cache[rid] = c
        return c

    def _bm25_scores(self, qtok: set, pool: list, k1: float = 1.5, b: float = 0.75) -> list:
        """Okapi BM25 score of `query` (token set) against every record in `pool` — the strong lexical
        channel for the hybrid. df/avgdl are computed over the pool (the live corpus). Returns a list of
        scores aligned to `pool`. Pure-Python, zero-dependency. We MEASURED BM25 (not token-overlap) as the
        lexical channel that makes the hybrid beat either alone (inspeximus/probes/locomo_retrieval_map.py)."""
        N = len(pool)
        if N == 0:
            return []
        counts = [self._rec_tokcount(r) for r in pool]
        dl = [sum(c.values()) for c in counts]
        avgdl = (sum(dl) / N) or 1.0
        df: dict = {}
        for c in counts:
            for t in c:
                df[t] = df.get(t, 0) + 1
        idf = {t: math.log(1 + (N - n + 0.5) / (n + 0.5)) for t, n in df.items()}
        # SORTED, not set order. Floating-point addition is not associative, so summing the per-term
        # contributions in a different order gives a slightly different total -- and `qtok` is a SET of
        # strings, whose iteration order is randomised per process. That is the whole source of the
        # run-to-run score noise: measured at 5.7e-10, enough to rotate nominally tied records in 7% of
        # runs and to change the answer outright under a different PYTHONHASHSEED.
        #
        # It was tempting to absorb it at the sort by quantising the ranking score. That was tried and
        # reverted: in a crowded store the target ranks first while being EXACTLY equal to 58 competitors
        # and 5.7e-10 above two more, so the noise and the smallest MEANINGFUL gap are the same size and
        # no quantum separates them. The noise had to go at its source instead, which is here, and costs
        # one sorted().
        _qterms = sorted(qtok)
        out = []
        for c, L in zip(counts, dl):
            s = 0.0
            for t in _qterms:
                f = c.get(t, 0)
                if f:
                    s += idf.get(t, 0.0) * (f * (k1 + 1)) / (f + k1 * (1 - b + b * L / avgdl))
            out.append(s)
        return out

    def _similarity(self, query: str, rec: dict, qvec=None, qtok: set | None = None) -> float:
        if qvec is not None and rec.get("vec"):
            return max(0.0, _cosine(qvec, rec["vec"]))
        q = qtok if qtok is not None else _tokens(query)
        t = self._rec_tokens(rec)
        if not q or not t:
            return 0.0
        return len(q & t) / min(len(q), len(t))     # overlap coefficient — forgiving without an embedder

    def _corroboration_facts(self, r, _by_id):
        """(good, bad, good_earned, distinct) for one record -- the raw inputs behind BOTH the
        graduation bar and the `warrant` tier reported by recall(with_warrant=True).

        One function because they were two copies of the same four lines, and when the graduation
        copy moved out of recall() in 2.0.1 the warrant copy was left referring to locals that no
        longer existed. Two tests caught the NameError; a third copy would not have been caught."""
        _good = float(r.get("good", 0) or 0)
        _bad = float(r.get("bad", 0) or 0)
        _good_earned = (float(r.get("good_warranted", 0) or 0)
                        if getattr(self, "credit_requires_warrant", False) else _good)
        _links = (self._gated_links(r, _by_id)
                  if (self.coherence_gate is not None or self.temporal_gate is not None) else r.get("links"))
        _distinct = (self._distinct_verified_keys(_links, _by_id) if self.strict_corroboration
                     else self._distinct_sources(_links, _by_id))
        return _good, _bad, _good_earned, _distinct

    def _graduation_corroborated(self, r, _by_id) -> bool:
        """The corroboration bar an episodic memory must clear to become semantic.

        Shared by the read path and by `consolidate()` so the two cannot drift apart. Carries the
        influence-gate rule including the exogenous-warrant one: when `credit_requires_warrant` is on,
        only WARRANTED good graduates, else a MINJA-style self-graded bridge would graduate itself and
        then pass the gate unconditionally on its "semantic" mtype (the graduation bypass this closes).
        A landed retraction (`slashed`) or a record with no lineage (`orphan`) blocks it either way."""
        _good, _bad, _good_earned, _distinct = self._corroboration_facts(r, _by_id)
        return (((_good_earned > 0 and _good >= _bad) or _distinct >= 2)
                and not (r.get("meta") or {}).get("slashed")
                and not r.get("orphan"))

    def _may_graduate(self, r, _by_id) -> bool:
        """Every condition for episodic -> semantic maturation, in one place."""
        return (r.get("mtype") == "episodic"
                and float(r.get("value", 0) or 0) >= _GRADUATE_VALUE
                and self._graduation_corroborated(r, _by_id))


    def recall(self, query: str, k: int = 6, include_superseded: bool = False,
               include_hubs: bool = False, mode: str = "auto", min_relevance: float = 0.0,
               scope: str | None = None, as_of: float | None = None,
               where: dict | None = None, influence_only: bool = False,
               prefer=None, prefer_trust: float = 1.0,
               prefer_max_boost: float | None = None, near: dict | None = None,
               tie_recent: float | None = None,
               with_status: bool = False, with_warrant: bool = False,
               redact_pii: bool = False, rerank=None, rerank_pool: int | None = None,
               reinforce: bool = False, trusted_only: bool = False, mmr: float | None = None,
               user_id: str | None = None, agent_id: str | None = None, session_id: str | None = None,
               rerank_by: str | None = None, resolve_conflicts: bool = False,
               suppress_stale_values: bool = False, project: str | None = None,
               observe: bool = True) -> list[dict]:
        """Top-k memories by RELEVANCE × VALUE — high-value memories outrank merely-similar ones.
        Memories the dream pass flagged as hubs (universal matchers) are skipped unless include_hubs.

        mode: 'auto' (default) uses LEXICAL token overlap while the store is small (< semantic_threshold
        active memories) and a LEXICAL+SEMANTIC HYBRID (Reciprocal Rank Fusion) once it grows past that —
        the hybrid robustly beat either channel alone in our agent-memory benchmark (details in the recall
        body / inspeximus/probes/locomo_retrieval_map.py). Force a single channel with mode='lexical' /
        'semantic', or the fusion explicitly with mode='hybrid'. Semantic/hybrid need an embedder (set on
        the store); without one, or if embedding fails, recall falls back to lexical automatically.

        where: an OPT-IN metadata pre-filter applied to the candidate pool BEFORE ranking — the cheap
        'filter before you rank' lever (measured on LoCoMo: a metadata pre-filter can beat retriever choice;
        inspeximus/probes/locomo_metadata_prefilter.py). A dict of field -> condition; a record must match ALL
        fields (AND). Each field is matched against the record's top-level attributes first, then its meta
        dict, so both `valid_from`/`mtype`/`key` and any `meta` key work. A condition is either a scalar
        (equality), a list/tuple/set (membership), or a dict of operators:
        {"$gte","$lte","$gt","$lt","$in","$nin","$ne","$contains"} — e.g. a time range
        where={"valid_from": {"$gte": t0, "$lte": t1}} (hard-filter the SOLVED half), or a closed-set
        entity where={"speaker": {"$in": ["Caroline","Mel"]}}. NOTE: this is a HARD filter — a record that
        doesn't match is removed, so on lossy/predicted extraction prefer a broad/loose filter (or rerank)
        over an aggressive one, since a wrong filter hard-deletes the answer (measured harm mode).

        project (OPT-IN, default None -> zero behaviour change): restrict the pool to memories written for
        this project/workspace, PLUS every memory that carries no project stamp. Records stamped for a
        DIFFERENT project are dropped before ranking. Pair it with remember(project=...). The
        unstamped-is-global rule is deliberate: a store written before you adopted project scoping has no
        stamps, so adopting one narrows what you see WITHOUT hiding anything you already had. Pass
        project=None to search across every project (the "I know I wrote this somewhere" query).

        influence_only (OPT-IN, default False -> zero behavior change): restrict the result to CORROBORATED
        memories — those that meet the same bar inspeximus uses for episodic->semantic GRADUATION (an EARNED
        net-positive outcome via credit() [good>0 and good>=bad], OR already-graduated 'semantic' type, OR
        >=2 DISTINCT-canonical-source corroborating links). This is the retrieve-then-INFLUENCE split: recall
        freely for context, but call with influence_only=True for the set that is allowed to DRIVE an action.
        MEASURED (inspeximus/probes/agentpoison_influence_gate*.py) against a real AgentPoison-style single-
        instance retrieval-poisoning attack (Chen et al., NeurIPS 2024, arXiv:2407.12784; PoisonedRAG, Zou
        et al., arXiv:2402.07867): a natural-sentence trigger hijacks RAW top-1 retrieval 88-100% and is
        scale-invariant (60->10k memories), and retrieval-time / embedding-geometry defenses do NOT
        generalize across encoders — but influence_only drops the single-instance poison's rank-1 hijack to
        0% on all three tested retrievers (MiniLM/BGE/Contriever) and all scales, because an injected poison
        never earns corroboration while legitimate memories earn it through use. It GENERALIZES precisely
        because it lives in provenance metadata, not embedding geometry. HONEST COST (calibration tradeoff):
        a rare-but-true memory that has not yet earned corroboration is filtered too (measured recall 1.00
        corroborated vs 0.08 uncorroborated) — so this is for adversarial / untrusted-ingestion use, where a
        recalled-but-uncorroborated memory should inform but not unilaterally drive an action. It RAISES
        attacker cost (a single free injection is filtered; defeating it needs >=3 coordinated records with
        >=2 independent forged provenances) rather than making poisoning impossible. Reversible: default
        False = legacy recall. Call `influence_gate_report()` first to see this gate's LIVE cost on your store
        (it is density-dependent: ~51% of legit recalls filtered when memories are used ~once, ~6% when dense
        — inspeximus/probes/oracle_separation_density.py) and the load-bearing caveat that it rides on an
        un-self-gradable credit() oracle.

        prefer / prefer_trust (OPT-IN, default None -> zero behavior change): a SOFT, trust-weighted metadata
        filter. Unlike `where` (a HARD filter that DELETES non-matching records — so a wrong filter hard-
        deletes the answer), `prefer` takes the same condition dict but only BOOSTS matching records'
        score by (1 + prefer_trust * gain), leaving non-matching records rankable. prefer_trust in [0,1] is
        HOW MUCH to trust the filter cue this call: pass a low value when your metadata extractor is unsure
        (weak/ambiguous match) so the filter gracefully backs off toward plain recall; prefer_trust=0 == no
        filter. This is the a-priori-trust lever: weight the filter by the RELIABILITY of the extraction
        (e.g. alias-match strength: exact-name hit -> ~1.0, no-name/ambiguous guess -> ~0.0), NOT by the
        extractor model's own self-reported confidence (which is corrupted in the overconfident-on-wrong
        case). MEASURED (inspeximus/probes/locomo_soft_prefer_filter.py) on LoCoMo: soft prefer weighted by
        alias-strength gives the filter's benefit on reliable (exact-name) queries while backing off on
        ambiguous ones where extraction fails -- beating both no filter and a hard `where` filter under
        imperfect extraction. Reversible: prefer=None = legacy recall.

        MULTI-DIMENSION prefer (compose several soft cues at once): pass `prefer` as a LIST of specs, each
        either {"cond": <dict>, "trust": <0..1>} or a (cond, trust) tuple. Matching dimensions compose as a
        PRODUCT of neutral-at-1.0 factors — pref = Π (1 + trust_i * gain) over the dims a record matches — so
        a record matching two cues is boosted more than one matching a single cue, and non-matching dims are
        inert (factor 1.0). A single dict + scalar prefer_trust is the one-dimension case (unchanged). Cap the
        TOTAL boost with `prefer_max_boost` (a ceiling on the product, like Elasticsearch function_score's
        max_boost); default None = uncapped. MEASURED (inspeximus/probes/locomo_composed_soft_filters.py) on LoCoMo:
        on questions carrying two independent cues (a resolved time window AND a named speaker), the product
        composition reached recall@20 0.865 vs 0.755 for the best single cue (+0.110, bootstrap CI excludes 0);
        a naive summed boost CAPPED at one dimension's trust crowded out (-0.053, the cap flattened the joint
        evidence — the classic 'combine outside the saturating form' failure, BM25F, Robertson et al. CIKM
        2004). So: compose as a PRODUCT, and if you cap, cap the product, not the summed trusts. This mirrors
        production search (Elasticsearch function_score defaults score_mode=multiply). Reversible: a single
        dict / None behaves exactly as before.

        tie_recent (OPT-IN, default None -> zero behavior change): NEAR-TIE RECENCY REORDER for stale-vs-
        fresh fact competition. When a fact is later corrected in free text, SRO supersession never triggers
        and the STALE value can outrank the fresh one (measured on MemBench knowledge_update: the stale
        value wins rank-1 in 32.7% of update questions, identically for raw cosine and inspeximus semantic —
        inspeximus/probes/membench_recall_probe_v2.py). Pass a small similarity epsilon (measured sweet spot
        0.02-0.05 on centered cosine): candidates whose RELEVANCE is within tie_recent of the strongest
        candidate's relevance are re-ordered newest-first (by valid_from, falling back to ts) ahead of the
        rest; everything below the band keeps its score order. MEASURED
        (inspeximus/probes/membench_recency_tiebreak_probe.py, 222 questions incl. 3 control splits):
        tie_recent=0.05 cuts stale-beats-fresh 0.327 -> 0.109 (3x) at ~zero hit@1/5 cost on non-update
        control splits; a LINEAR position bonus was measured USELESS (no SBF movement before it damages
        controls) — the band reorder is the shape that works. HONEST SCOPE: (a) in the benchmark the
        correction always comes after the original mention (by construction); the control-split cost is
        the fairness check; (b) adversarial hole: an ECHO of the stale value re-stated AFTER the correction
        would be promoted — tie_recent trusts recency inside the band, so do not use it on hostile
        ingestion without provenance gating (combine with influence_only). Reversible: None = legacy."""
        # Normalize `prefer` into a list of (cond_dict, clamped_trust) specs. Back-compat: a plain dict uses
        # the scalar prefer_trust (the legacy one-dimension path, byte-identical scoring); a list composes.
        _prefer_specs: list = []
        if prefer:
            if isinstance(prefer, dict):
                _t0 = max(0.0, min(1.0, float(prefer_trust)))
                if _t0 > 0.0:
                    _prefer_specs = [(prefer, _t0)]
            elif isinstance(prefer, (list, tuple)):
                for _spec in prefer:
                    if isinstance(_spec, dict) and "cond" in _spec:
                        _c, _t = _spec["cond"], float(_spec.get("trust", prefer_trust))
                    elif isinstance(_spec, (list, tuple)) and len(_spec) == 2:
                        _c, _t = _spec[0], float(_spec[1])
                    else:
                        raise ValueError("prefer list items must be {'cond':..,'trust':..} or (cond, trust)")
                    _t = max(0.0, min(1.0, _t))
                    if _t > 0.0 and _c:
                        _prefer_specs.append((_c, _t))
            else:
                raise ValueError("prefer must be a dict (one dimension) or a list of (cond, trust) specs")
        # `near` (OPT-IN, default None -> zero behaviour change): a SOFT, CONTINUOUS proximity cue -- the
        # numeric analogue of `prefer`. `prefer` matches CATEGORICAL meta (theme == 'identity'); `near`
        # boosts records by their CLOSENESS to a target VECTOR in named NUMERIC meta dims (e.g. a TAT 5-D
        # state chunk, or any embedding-like feature stored in meta). Spec:
        #   near = {"target": {"theme": 0.29, "role": 0.33, ...}, "trust": 0.7, "half": 0.2}
        # For each candidate, distance = per-dim-normalised Euclidean over the target dims present as NUMBERS
        # in the record's meta; boost = 1 + trust * exp(-distance / half) (neutral 1.0 when far or when the
        # record lacks the dims, so a missing/weak cue degrades gracefully, never hard-deletes). `half` is the
        # distance at which the boost is ~1+trust/e. Composes multiplicatively with `prefer` and text sim.
        # MEASURED (inspeximus/probes/continuous_chunk_recall_probe.py): on a real TAT 5-D state trace, near-boost on
        # the state vector beats plain text recall on state/regime-relevance retrieval (precision@5 0.984 vs 0.758)
        # where categorical filters cannot (the values are continuous). Soft cue that re-ranks the pool, not a
        # vector index; coverage-weighted + NaN-guarded. Reversible: near=None = byte-identical legacy recall.
        _near = None
        if near:
            _nt = near.get("target") or {}
            _numt = {d: float(v) for d, v in _nt.items()
                     if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v}   # numeric, not bool, not NaN
            if _numt:
                _near = (_numt, max(0.0, min(1.0, float(near.get("trust", 1.0)))), max(1e-9, float(near.get("half", 0.25))))
        def _eligible(r: dict) -> bool:
            s = r["status"]
            if s not in _RECALLABLE:
                # AN ALLOWLIST, and FIRST, above every other branch. Both halves are the guarantee.
                #
                # First, because a denylist below the bitemporal branch is not a gate: placed there,
                # `recall(as_of=...)` returned provisional records, since that branch returns True
                # before any status check runs.
                #
                # An allowlist, because a denylist only knows the statuses somebody remembered to
                # add. `discard_provisional()` set status="discarded" and that fell straight through
                # to `return include_superseded` — a REJECTED record, surfaced by a flag named for
                # supersession, minutes after the denylist above it was written. A status added next
                # year is invisible until someone decides it should be visible, which is the safe
                # default for a store whose job is to not hand back things nobody verified.
                return False
            if as_of is not None:
                # Bi-temporal "as of T": a memory counts if it was VALID at time T — valid_from <= T and not yet
                # invalidated by T — INCLUDING records now superseded (they were current back then). Records
                # superseded by the pre-bitemporal pass carry no invalidated_at; treat them as still-valid here.
                vf = r.get("valid_from", r["ts"])
                inv = r.get("invalidated_at")
                if vf > as_of or (inv is not None and inv <= as_of):
                    return False
                return include_hubs if s == "hub" else True
            if s == "active":
                return True
            if s == "hub":
                return include_hubs
            return include_superseded            # superseded / other non-active
        # Access-control acts are bookkeeping, not memories: a grant is never a recall hit, for the operator
        # either. Without this, issuing a grant would put "ACL: ... granted agent 'bob' read access ..."
        # into the answer set of every loosely-related query.
        pool = [r for r in self.items if _eligible(r) and not _is_acl_record(r)]
        # HARD TENANT ISOLATION (fail-closed, non-bypassable): a tenant-bound store sees ONLY its own tenant's
        # records, always — this is enforced here on the STORE, not via a caller argument, so no forgotten
        # parameter can leak another tenant's data. An unbound store (tenant=None) is the admin view (sees all).
        if self.tenant is not None:
            pool = [r for r in pool if r.get("tenant") == self.tenant]
        # Scope/namespace isolation: when a scope is requested, recall ONLY sees memories tagged with that scope
        # (meta['scope']) BEFORE ranking — a shared store (e.g. many agents / tenants in one Inspeximus) cannot bleed
        # one scope's memories into another's recall. scope=None (default) sees everything (legacy behavior).
        if scope is not None:
            pool = [r for r in pool if (r.get("meta") or {}).get("scope") == scope]
        # PROJECT / WORKSPACE isolation (opt-in): one store, several repos. A named project sees its OWN
        # memories plus every UNSTAMPED (global) one — the same wildcard rule the uid/aid/sid hierarchy below
        # uses, and the reason opting in is non-destructive: everything written before you passed a project is
        # unstamped, so it stays reachable from every project instead of vanishing the day you adopt a scope.
        # A record stamped for ANOTHER project is filtered out HERE, before ranking, so a peer project's
        # memories cannot occupy top-k slots and then be dropped (which would silently shrink k).
        # project=None (default) filters nothing at all — byte-identical legacy behaviour, and the way to
        # search ACROSS projects deliberately.
        if project is not None:
            pool = [r for r in pool
                    if (r.get("meta") or {}).get("project") in (None, str(project))]
        # MEMORY HIERARCHY visibility (user > agent > session): when the query names any of user/agent/session,
        # a memory is visible iff, for each NAMED level, the memory is EITHER unscoped at that level (wildcard)
        # OR equal to the query's value; an UNNAMED query level is unconstrained. So (a) a session query sees that
        # session's memories PLUS the user's/agent's shared (unscoped-session) memories, but NOT a peer session's;
        # (b) users are isolated from each other and peer sessions from each other; (c) a broad user-only query
        # sees all that user's own memories (incl. their sessions' — same user, not a leak). All None = legacy.
        if user_id is not None or agent_id is not None or session_id is not None:
            _want = {"uid": user_id, "aid": agent_id, "sid": session_id}

            def _visible(r):
                m = r.get("meta") or {}
                for lvl, qv in _want.items():
                    if qv is None:
                        continue
                    mv = m.get(lvl)
                    if mv is not None and str(mv) != str(qv):
                        return False
                return True
            pool = [r for r in pool if _visible(r)]
        # TRUSTED-ONLY (OPT-IN, needs trust_seeds): keep only candidates whose ORIGIN is anchored to the trust root —
        # the record is itself attested by a seed key, its (entity-resolved) source is seed-vouched, OR a trusted
        # actor endorses it via a link (the trust closure). Filtered HERE, BEFORE ranking, so recall returns the top
        # TRUSTED hit even at k=1 (not the top hit then dropped). The deterministic, zero-LLM defense against
        # forged-provenance memory poisoning: an attacker can forge a warrant STRING and mint Sybil Ed25519 keys, but
        # cannot sign as a TRUSTED key, so its poison never enters the pool. Trust is a root set ONCE (CA-style), not
        # a per-query oracle. High-friction by design — anchor the facts that MATTER (bank, medication, instructions).
        if trusted_only:
            # FAIL CLOSED. With no trust_seeds there is no trust root, so NOTHING can be anchored to it and the
            # honest answer is "no trusted memories" — not the whole untrusted pool. Skipping the filter here
            # (the old `and self.trust_seeds`) silently returned exactly the poisoned records the caller asked
            # to exclude, and looked identical to a successful trusted recall.
            if not self.trust_seeds:
                pool = []
            else:
                _trusted = self._trusted_sources({it["id"]: it for it in self.items})
                pool = [r for r in pool
                        if ("key:" + str(r.get("attested_key"))) in self.trust_seeds
                        or self._canon_of(r) in _trusted]
        # Metadata pre-filter (the 'filter before you rank' lever): keep only records matching ALL `where`
        # conditions, matched against top-level fields then meta. Deterministic, no embedder, O(pool).
        if where:
            pool = [r for r in pool if self._cond_match(r, where)]
        # Influence gate (retrieve-then-influence split): keep only CORROBORATED memories in the set that is
        # allowed to drive an action. Same bar as episodic->semantic graduation; embedder-independent, so it
        # generalizes across retrievers where geometry-based poison defenses do not (see the docstring).
        if influence_only:
            _byid = {x["id"]: x for x in self.items}
            pool = [r for r in pool if self._corroborated(r, _byid)]
        # Mode selection. 'hybrid' = lexical (token overlap) + semantic (embedding) fused with Reciprocal
        # Rank Fusion. We MEASURED hybrid robustly beating EITHER channel alone for agent memory on LoCoMo
        # (recall@20 0.61 hybrid vs 0.55 lexical vs 0.53 semantic; +0.057 over the best single channel,
        # 9/10 conversations, conversation-level bootstrap CI excludes 0). So 'auto' now fuses (was: switch
        # lexical->semantic at the threshold). Receipt: inspeximus/probes/locomo_retrieval_map.py. RRF needs no
        # tuning and no extra dependency. Force a single channel with mode='lexical'/'semantic'.
        has_embed = self.embed is not None
        if mode == "lexical" or not has_embed:
            sel = "lexical"
        elif mode in ("semantic", "hybrid"):
            sel = mode
        else:                                                 # 'auto': fuse once the store is worth it
            sel = "hybrid" if len(pool) >= self.semantic_threshold else "lexical"
        qvec = self._qvec(query, self.embed_query) if sel in ("semantic", "hybrid") else None
        if qvec is None and sel != "lexical":
            sel = "lexical"                                   # embedder absent or failed -> graceful fallback
        self._last_mode = sel
        qtok = _tokens(query)                                 # tokenize the query once (lexical + fallback)
        # Vectorized semantic fast-path: one matmul gives the cosine to every vec-bearing memory.
        sims_vec = None
        if qvec is not None and _np is not None:
            M = self._vec_matrix()
            if M is not None:
                qv = _np.asarray(qvec, dtype=_np.float32)
                if self.center_embeddings and self._vec_mean is not None:
                    qv = qv - self._vec_mean              # center the query the SAME way as the matrix
                sims_vec = M @ (qv / (float(_np.linalg.norm(qv)) or 1.0))
        _now = time.time()                                # for per-type decay of the ranking value
        _by_id = {x["id"]: x for x in self.items}         # for provenance lookups (source-episode status)
        def _semsim(r) -> float:
            if sims_vec is not None and r.get("vec") and r["id"] in self._vec_rowof:
                return max(0.0, float(sims_vec[self._vec_rowof[r["id"]]]))
            return max(0.0, _cosine(qvec, r["vec"])) if (qvec is not None and r.get("vec")) else 0.0
        def _lexsim(r) -> float:
            t = self._rec_tokens(r)
            return (len(qtok & t) / min(len(qtok), len(t))) if (qtok and t) else 0.0
        def _candrec(r, sim):                             # provenance gate + value, shared by all modes
            # Provenance gate: a memory that absorbed near-duplicates (links) is STALE-DERIVED if any of
            # those sources was later CONTRADICTED (state-toggle supersession) — the merged summary
            # outlived a fact it summarized. Demote it (don't drop — flag for re-consolidation), so a
            # consolidated claim can't quietly outrank the fresh memory that overturned its source.
            stale = bool(r.get("links")) and any(
                (_by_id.get(lid, {}).get("meta") or {}).get("superseded_by_toggle") for lid in r["links"])
            r["_stale_derived"] = stale                   # surfaced in the returned record
            return (sim, 0.5 if stale else 1.0, self._effective_value(r, _now), r)
        cands = []                                        # (sim, prov, eff_value, r), sim in [0,1]
        # Relevance-floor ABSTENTION: drop candidates below an absolute similarity floor; if the WHOLE top-k
        # falls below it, recall() returns [] ("not in memory") instead of padding context with a weak false
        # match. min_relevance=0.0 (default) keeps legacy behavior (only sim<=0 dropped). In hybrid the floor
        # is applied to the stronger of the two raw channels, then the FUSED rank score becomes the relevance.
        if sel == "hybrid":
            bm = self._bm25_scores(qtok, pool)            # strong BM25 lexical channel over the live corpus
            scn = []                                      # (r, sem, bm25) candidates above the floor
            for r, bx in zip(pool, bm):
                sem = _semsim(r)
                if (sem <= 0 or sem < min_relevance) and bx <= 0:
                    continue                              # abstain only when BOTH channels are empty/below floor
                scn.append((r, sem, bx))
            if scn:
                order_sem = sorted(range(len(scn)), key=lambda i: -scn[i][1])
                order_bm = sorted(range(len(scn)), key=lambda i: -scn[i][2])
                rrf = [0.0] * len(scn)
                for rank, i in enumerate(order_sem): rrf[i] += 1.0 / (60 + rank)
                for rank, i in enumerate(order_bm):  rrf[i] += 1.0 / (60 + rank)
                mx = max(rrf) or 1.0
                for i, (r, sem, bx) in enumerate(scn):
                    cands.append(_candrec(r, rrf[i] / mx))    # normalize the fused rank score to a [0,1] relevance
        else:
            for r in pool:
                sim = _semsim(r) if sel == "semantic" else _lexsim(r)
                if sim <= 0 or sim < min_relevance:
                    continue
                cands.append(_candrec(r, sim))
        # Calibration WAS-IT-RIGHT: a per-memory Beta(good,bad) posterior nudges the score by track record.
        # cal_mode controls how the outcome-credit channel is allowed to act (our measured signal-reliability
        # law: a selection signal only beats relevance once reliability p > the no-signal floor 1/(1+D)):
        #   'full'  (default) — cal in [0.5, 1.5] (legacy: can promote AND demote).
        #   'boost' — cal in [1.0, 1.5]: outcome-credit can PROMOTE a proven memory but never DEMOTE one below
        #             its relevance, so a wrong/random credit cannot suppress a correct memory (kills backfire).
        #   'gated' — disable cal (->1.0) for this recall when the pooled signal looks weaker than 1/(1+D).
        mode = getattr(self, "cal_mode", "full")
        gate_off = False
        if mode == "gated" and cands:
            top = max(c[0] for c in cands)
            near = [c for c in cands if c[0] >= top * 0.95]      # candidates relevance can't separate
            D = len(near)
            if D >= 2:
                g = sum(float(c[3].get("good", 0) or 0) for c in near)
                b = sum(float(c[3].get("bad", 0) or 0) for c in near)
                if (g + 1.0) / (g + b + 2.0) <= 1.0 / (1.0 + D):
                    gate_off = True
        # Soft `prefer` filter: multiplicatively boost records matching each prefer condition. Non-matching
        # records are left rankable (unlike hard `where`), so a wrong/weak cue degrades gracefully instead of
        # hard-deleting the answer. Multiple cues COMPOSE as a product of neutral-at-1.0 factors
        # (pref = Π (1 + trust_i * _PREFER_GAIN) over matched dims); one dim reproduces the legacy scalar path.
        # Optionally cap the product at prefer_max_boost (measured: cap the PRODUCT, never the summed trusts).
        scored = []
        for sim, prov, evalue, r in cands:
            if gate_off:
                cal = 1.0
            else:
                cal = 0.5 + self._reliability(r)
                if mode == "boost" and cal < 1.0:
                    cal = 1.0
            pref = 1.0
            for _cond, _tr in _prefer_specs:
                if self._cond_match(r, _cond):
                    pref *= (1.0 + _tr * _PREFER_GAIN)
            if prefer_max_boost is not None and pref > prefer_max_boost:
                pref = prefer_max_boost
            nb = 1.0
            if _near is not None:
                _tgt, _ntr, _half = _near
                _rm = r.get("meta") or {}
                _sq = 0.0; _dn = 0
                for _d, _tv in _tgt.items():
                    _rv = _rm.get(_d)
                    if isinstance(_rv, (int, float)) and not isinstance(_rv, bool) and _rv == _rv:  # numeric, not bool/NaN
                        _sq += (float(_rv) - _tv) ** 2; _dn += 1
                if _dn:
                    # per-dim-normalised proximity, coverage-weighted so a record matching FEWER target dims can't
                    # unfairly out-boost one matching all with modest error; NaN-guarded so a bad value never
                    # corrupts the whole ranking order.
                    nb = 1.0 + _ntr * (_dn / len(_tgt)) * math.exp(-(math.sqrt(_sq / _dn)) / _half)
                    if not math.isfinite(nb):
                        nb = 1.0
            score = sim * (1.0 + math.log1p(max(0.0, evalue))) * prov * cal * pref * nb
            scored.append((score, sim, r))
        # DETERMINISTIC tie-break. Sorting on the score alone left ties to Python's stable sort, which
        # preserves whatever order the candidates arrived in -- and candidates are gathered through sets of
        # record-id STRINGS, whose iteration order depends on per-process hash randomisation. Two identical
        # stores answering the same query therefore returned different top-k ORDER between processes:
        # measured, three records tied at score 0.564 and swapped places in 4 of 20 runs, and under
        # PYTHONHASHSEED=0 vs 1 the order differs outright. For a library whose stated property is
        # determinism, and for any consumer that takes the top-1 of a tie, that is a wrong answer rather
        # than a cosmetic one. `ts` then `id` is stable across processes and meaningful: older first.
        # The tie-break is INSERTION POSITION, resolved against the store's own list. First attempt used
        # (ts, id) and made it worse -- records written in the same clock tick share `ts`, so the tie fell
        # through to `id`, which is random per instance: 4/20 runs agreed instead of 16/20. Position is the
        # only key here that is both stable across processes and meaningful (older first).
        # Keyed on the record ID, not on object identity: `items` hands out copies, so `id(obj)` never
        # matched and the tie-break silently did nothing (18/20 instead of 20/20, which looked like an
        # improvement rather than a lookup that always missed). Building a dict for LOOKUP is fine -- it
        # was ITERATING a set of random id strings that made the order vary in the first place.
        # The sort key must be TOTAL, or ties fall back to arrival order -- and candidates arrive through
        # sets of record-id strings, whose iteration depends on per-process hash randomisation and on ids
        # that are random per store. Measured before this: two identical stores, identical scores,
        # different top-k order in 6 of 60 runs, on BOTH the default and the reinforce=False path (it was
        # never about reinforcement; an earlier reading of one sample said it was, and was wrong).
        # Position first, because older-first is the meaningful order. `text` last so the key stays total
        # even when the position lookup misses -- a tie-break that silently degrades to arrival order is
        # how this survived a position-based fix that measured 18/20 and looked like progress.
        # The score is QUANTISED for ranking (see _RANK_QUANTUM). The lexical channel accumulates over an
        # unordered collection, so two records equal in every way that matters differ in the last bits of
        # a float, and a raw comparison treats that summation noise as a real difference. Measured on a
        # store where three records score 0.564: mode="lexical" reordered on 40 of 40 runs, "semantic" 2,
        # "hybrid" 4, and "auto" inherits whichever it routes to.
        #
        # Ties then keep INSERTION order, because Python's sort is stable and candidates are gathered in
        # store order. That is asserted in tests/test_recall_is_deterministic.py rather than re-imposed
        # here: an explicit (position, text) tie-break was written first and NO mutation could tell it
        # apart from its absence -- eight-way ties came back in insertion order with it removed, on four
        # hash seeds. A guard nothing can distinguish from nothing is a check that cannot fail, and this
        # repository has spent the week deleting those. The property is real and now tested; the code that
        # merely restated it is gone.
        # REVERTED to the raw score, deliberately, after the quantised version broke retrieval.
        #
        # The determinism defect is real: the lexical channel accumulates over an unordered collection, so
        # the same record scores differently between runs (measured spread 5.7e-10), and three nominally
        # tied records rotated in 7% of runs. Quantising to 6 places removed that completely -- one order
        # over 120 runs in every mode, identical across six hash seeds.
        #
        # And it cost more than it bought. In the ADK crowded-store scenario (60 other users' memories
        # plus the one you asked for) the target ranks FIRST on the raw score while being EXACTLY equal to
        # 58 of them and 5.7e-10 above the other two. Quantising merged that margin away, the target fell
        # to the tie, and the memory the caller asked for stopped being found at all. "Deterministic but
        # cannot find your memory in a crowded store" is a worse property than "finds it, with a 7% tie
        # rotation", so the trade is refused until there is a fix that does not have to choose.
        #
        # What the measurement actually says: the run-to-run noise (5.7e-10) and the smallest MEANINGFUL
        # gap in this corpus (5.7e-10) are the same size. No quantum can separate them. The fix has to
        # remove the noise at its source -- a deterministic accumulation order in the lexical scorer --
        # not paper over it at the sort.
        # Equal relevance -> the MORE RECENT memory first, declared rather than emergent.
        #
        # This was always the behaviour; it just arrived by accident. Sub-second differences in the decay
        # factor gave a newly-written record a microscopic edge, and that edge was what surfaced the
        # memory you asked for in a crowded store: measured, the target ranked FIRST while being exactly
        # tied with 58 competitors on relevance. Quantising the decay age to whole seconds -- necessary,
        # because sub-second resolution on an hours-long half-life is pure noise -- removed the accident
        # and the target fell out of the window with it. The ADK audit caught that before release.
        #
        # So recency becomes an explicit tie-break. It is the same policy the store already holds
        # everywhere else: when two memories are equally relevant, the newer one is the better answer.
        # ...and INSERTION POSITION last, because `ts` is not fine enough to be a total order: the clock
        # granularity is ~15 ms here and eight records written in a loop share a timestamp, which put the
        # tie straight back on arrival order (3 distinct orders over 120 runs). An earlier version of this
        # key was deleted as unfalsifiable when the only fixture was small enough that arrival order and
        # insertion order coincided -- that judgement was made on too weak a test, and this is where it
        # earns its place.
        # Recency is expressed as INSERTION POSITION, not as `ts`. Same policy, no clock: a wall-clock
        # timestamp has ~15 ms granularity here, so records written in a loop sometimes share a tick and
        # sometimes do not, and sorting on it put the order back at the mercy of machine jitter (3 distinct
        # orders over 120 runs even with a total key). Position says "written later" exactly, and cannot
        # drift.
        _pos = {rec.get("id"): i for i, rec in enumerate(self._items)}
        scored.sort(key=lambda x: (-x[0], -_pos.get(x[2].get("id"), -1)))
        # Near-tie recency reorder (OPT-IN via tie_recent; see docstring for the measured provenance).
        # Band on RELEVANCE (sim), not the composite score: the composite mixes value/calibration channels
        # whose scale varies per store, while sim is the [0,1] channel the epsilon was measured on.
        if tie_recent is not None and scored:
            _eps = max(0.0, float(tie_recent))
            _top_sim = max(t[1] for t in scored)
            _tied = [t for t in scored if t[1] >= _top_sim - _eps]
            _rest = [t for t in scored if t[1] < _top_sim - _eps]
            # TOTAL key: event time, then WRITE POSITION. `ts` alone is not total -- the wall clock here has
            # ~15 ms granularity, so records written in a loop share a tick in one run and not in the next,
            # and the band (which arrives in SCORE order, not write order) then reordered differently between
            # runs: measured 3 distinct orders over 60 identical single-process runs. Position is the same
            # policy without the clock, and it is what the main sort above already uses.
            _tied.sort(key=lambda t: (-(t[2].get("valid_from") or t[2]["ts"]),
                                      -_pos.get(t[2].get("id"), -1)))
            scored = _tied + _rest
        # OPT-IN READ-TIME CONFLICT RESOLVER (resolve_conflicts=True, default OFF -> byte-identical legacy).
        # The write-time guards (keyed supersession, echo_guard) cannot reach an UN-KEYED re-assertion of a
        # retired value: it lands as an independent record, embeds near-identically to the correction, and can
        # out-rank it (the measured stale-serve failure; cf. arXiv 2606.01435's read-time resolution result).
        # This stage clusters near-duplicate same-subject candidates in the top pool (token-Jaccard >= 0.6, or
        # identical normalized text) and resolves each cluster by VALUE BIRTH: a value's timestamp is its
        # EARLIEST assertion anywhere in the store (superseded rows included), so restating an old value never
        # refreshes it — the echo keeps its old birth and LOSES to the correction, while a genuinely new value
        # wins as the newest birth. Losing candidates are demoted below the kept pool (backfilled, not hidden);
        # the surviving hit carries `resolved_over: [ids]` for explainability. Deterministic, zero-LLM.
        # KNOWN LIMIT (documented, same as echo_guard): a deliberate reversal back to an older value is
        # indistinguishable from an echo at read time — use keys + remember(reaffirm=True) for that.
        _rc_losers: dict = {}
        if resolve_conflicts and len(scored) > 1:
            scored, _rc_losers = self._resolve_read_conflicts(scored, k)
        # OPT-IN VALUE-LEVEL STALE SUPPRESSION (suppress_stale_values=True, default OFF -> legacy order).
        # The conflict resolver above only reaches candidates that CLUSTER (token-Jaccard >= 0.6); in prose
        # the fourteen other sentences carrying a retired value are phrased differently and never cluster,
        # so they survive every write-time and read-time guard we had. This stage carries the correction to
        # the VALUE: any candidate asserting a retired value of some key, while not also asserting that
        # key's current value, is demoted below the kept pool (backfilled, not hidden — same contract as
        # the resolver). See _retired_values() for the measurement that motivated it. Deterministic, zero-LLM.
        # WITHHELD, not merely demoted, and for a measured reason: demotion only helps while clean candidates
        # outnumber k. Ask a 5-record store for k=3 with three echoes of the retired value and reordering
        # returns all three anyway — the leak is unchanged. This is the same contract recall() already applies
        # to a superseded ROW (hidden by default, `include_superseded=True` to see it); applying it to the
        # VALUE is the whole point. The withheld candidates are appended after the kept pool so a caller
        # passing include_superseded still sees them, and an all-stale result falls back to the legacy order
        # rather than returning nothing.
        if suppress_stale_values and len(scored) > 1:
            _rm = self._retired_values()
            if _rm:
                _keep, _stale = [], []
                for _t in scored:
                    (_keep if self._stale_by_value(_t[2], _rm) is None else _stale).append(_t)
                if _keep:                                   # never empty the result: all-stale -> no-op
                    scored = _keep + _stale if include_superseded else _keep
        # OPT-IN reranker hook (retrieve-then-rerank). `rerank(query, records) -> list[float]` (one relevance
        # score per record, higher=better) lets a caller plug a cross-encoder / model reranker over the top
        # candidates — the one lever MEASURED to lift multi-hop recall beyond inspeximus's zero-LLM base (LoCoMo
        # multi-hop full-recall ~0.30 -> ~0.48 with a reader-reranker; [[locomo-iterative-lever-full-benchmark]]).
        # Model-agnostic (inspeximus never imports a model) and MOAT-SAFE: no LLM runs unless the caller supplies one,
        # and the WRITE path is untouched. rerank_pool bounds how many top candidates are reranked (default
        # max(4*k, 50)). Fail-open: any error keeps the pre-rerank order.
        if rerank is not None and scored:
            _m = int(rerank_pool) if rerank_pool else max(4 * k, 50)
            _head = scored[:_m]
            try:
                _rs = rerank(query, [t[2] for t in _head])
                if _rs is not None and len(_rs) == len(_head):
                    _order = sorted(range(len(_head)), key=lambda i: -float(_rs[i]))
                    scored = [_head[i] for i in _order] + scored[_m:]
            except Exception:
                pass
        # OPT-IN MMR / result-dedup (mmr in [0,1]): rerank the top pool for DIVERSITY so recall does not return k
        # near-duplicate memories (the unbounded-redundant-results failure that mem0/hindsight explicitly declined
        # to fix). Greedy Maximal Marginal Relevance: next = argmax [ mmr*rel(d) - (1-mmr)*max cos(d, chosen) ].
        # rel = the composite score min-max normalized over the pool (comparable to the [0,1] cosine diversity
        # term); diversity uses record vectors, falling back to token-Jaccard so LEXICAL recall dedups too.
        # mmr=1.0 == pure relevance (no-op); lower = more diverse. Zero-LLM, deterministic. Composes AFTER rerank.
        if mmr is not None and len(scored) > 1:
            lam = max(0.0, min(1.0, float(mmr)))
            _mp = int(rerank_pool) if rerank_pool else max(4 * k, 50)
            pool, tail = scored[:_mp], scored[_mp:]
            rels = [t[0] for t in pool]
            lo, hi = min(rels), max(rels)
            norm = [((rl - lo) / (hi - lo)) if hi > lo else 1.0 for rl in rels]
            _toks = [set((t[2].get("text") or "").lower().split()) for t in pool]

            def _dsim(i, j):
                vi, vj = pool[i][2].get("vec"), pool[j][2].get("vec")
                if vi and vj:
                    return max(0.0, _cosine(vi, vj))
                a, b = _toks[i], _toks[j]
                return (len(a & b) / len(a | b)) if (a and b) else 0.0

            # Greedy MMR, bounded to the k items actually returned and memoized per pair. Selecting the WHOLE
            # pool (and recomputing every cosine on each sweep) costs ~p^3/6 similarity calls: fine at the
            # default p=50, but ~1.3M at k=50 and ~1e9 for a caller passing rerank_pool=2000 — a hang. Only the
            # first k survive `scored[:k]` anyway; the unselected remainder keeps its relevance order.
            _sim_memo: dict = {}

            def _dsim_memo(i, c):
                kk = (i, c) if i < c else (c, i)
                v = _sim_memo.get(kk)
                if v is None:
                    v = _sim_memo[kk] = _dsim(i, c)
                return v

            chosen, remaining = [], list(range(len(pool)))
            while remaining and len(chosen) < k:
                best_i, best_v = remaining[0], None
                for i in remaining:
                    div = max((_dsim_memo(i, c) for c in chosen), default=0.0)
                    val = lam * norm[i] - (1.0 - lam) * div
                    if best_v is None or val > best_v:
                        best_v, best_i = val, i
                chosen.append(best_i)
                remaining.remove(best_i)
            scored = [pool[i] for i in chosen] + [pool[i] for i in remaining] + tail
        # OPT-IN NAMED RERANKER MENU (rerank_by): a discoverable set of deterministic, zero-LLM reorderings of the
        # top relevant pool — the "named reranker" depth a serious retrieval layer exposes (cf. Zep's menu), no LLM.
        # Complements the `mmr=` diversity knob and the `rerank=` cross-encoder hook:
        #   'recency'     — newest (event-time valid_from, else ts) first among the relevant pool
        #   'value'       — highest accrued importance first
        #   'reliability' — best track record first (Beta good/bad posterior: was-it-right, not just similar)
        #   'relevance'   — pure relevance order (explicit no-op passthrough)
        # Reorders only the top pool (rerank_pool, default max(4k,50)); relevance filtering already applied.
        if rerank_by:
            _mp = int(rerank_pool) if rerank_pool else max(4 * k, 50)
            pool, tail = scored[:_mp], scored[_mp:]
            rb = rerank_by.lower()
            if rb == "recency":
                # TOTAL key (event time, then WRITE POSITION) for the same measured reason as the tie_recent
                # band above: this pool arrives in SCORE order, and a wall clock too coarse to separate a
                # write loop makes the sort's tie STRUCTURE differ between runs. Measured on this reranker:
                # 31 distinct orders over 60 identical single-process runs, at one fixed PYTHONHASHSEED.
                pool.sort(key=lambda t: (-(t[2].get("valid_from") or t[2].get("ts") or 0),
                                         -_pos.get(t[2].get("id"), -1)))
            elif rb == "value":
                pool.sort(key=lambda t: -float(t[2].get("value") or 0))
            elif rb == "reliability":
                pool.sort(key=lambda t: -self._reliability(t[2]))
            elif rb == "relevance":
                pass                                          # explicit no-op: keep pure relevance order
            scored = pool + tail
        # AN EXPLICIT ASK FOR HISTORY RESERVES SLOTS. `include_superseded=True` only stops FILTERING
        # superseded rows (see the admissibility branch above); they then compete on relevance like
        # everything else, and in a store of any size they lose to the active records that share the
        # query's vocabulary. MEASURED 2026-08-18: a 402-record store holding exactly one superseded
        # row returned ZERO superseded records at k=20 with the flag ON -- byte-identical to the flag
        # OFF. The flag read as permission for history to compete, which is not what a caller asking
        # for history means, and no test covered it. A bounded share of k (a quarter, at least one) is
        # now reserved for superseded rows that would otherwise fall outside the cut; the reservation
        # is capped so the current record set is never starved, and nothing changes when the flag is
        # off or when no superseded row was excluded.
        if include_superseded and len(scored) > k > 0:
            _sup = [t for t in scored[k:] if (t[2].get("status") or "") == "superseded"]
            if _sup:
                _res = min(len(_sup), max(1, k // 4))
                _promote = _sup[:_res]
                _pid = {id(t) for t in _promote}
                _rest = [t for t in scored if id(t) not in _pid]
                _cut = max(0, k - _res)
                scored = _rest[:_cut] + _promote + _rest[_cut:]
        out = []
        _top_sim = scored[0][1] if scored else 1.0   # normalize reinforcement by this query's best match
        for score, sim, r in scored[:k]:
            # Relevance-weighted reinforcement: a strong, on-target hit reinforces value MORE than a
            # marginal one that merely squeaked into the top-k. A flat +bump lets a memory that is a
            # weak false-positive for many queries become 'immortal' — the popular-but-irrelevant
            # failure mode. Weighting by this recall's relevance (normalized to the query's best hit)
            # ties reinforcement to how well the memory actually answered. (Independently converged on
            # in production by the Dakera and mem0 teams: weight access events by recall score, not raw
            # count.)
            # reinforce=False: a NON-MUTATING read (no value bump, no decay-clock reset, no graduation) — for
            # eval/benchmark or read-only consumers where recall order must not depend on prior queries. The
            # per-hit reinforcement below optimizes value-weighted importance for a WARM store, but on a cold
            # query stream it is an order-dependent confound (measured to depress recall_any ~0.10 @low-k).
            if reinforce:
                rel = (sim / _top_sim) if _top_sim > 0 else 1.0
                r["value"] += 0.25 * rel
                r["last_access"] = _now             # ...and resets the per-type decay clock
            # Type GRADUATION: an episodic memory recalled into high accrued value has proven durable,
            # so promote it to semantic — it stops fading on the fast 7-day episodic clock and decays
            # on the slow semantic one instead. (Dakera's access-driven episodic->semantic promotion,
            # gated on accrued VALUE rather than raw access count, so a popular-but-trivial memory
            # doesn't graduate.)
            # POISON guard (HARDENED 2026-06-25): durability must be EARNED by INDEPENDENT corroboration, not
            # mere recall-frequency. The value bump above is correctness-blind, so a confabulation recalled
            # enough would otherwise graduate to the durable (slow-decay) tier and entrench itself. A
            # self-assertable `source` string or a SINGLE `links` edge is attacker-settable (AgentPoison /
            # MINJA / OWASP-ASI06), so neither alone may confer durability. Require either an EARNED net-positive
            # outcome (good>0 and good>=bad — set only by credit() resolving real work, not self-assertable), OR
            # >=2 DISTINCT corroborating links (no single self-created edge suffices). An uncorroborated popular
            # memory stays episodic and fades on the fast clock unless earned.
            # SYBIL HARDENING (entity resolution): count DISTINCT CANONICAL sources among the corroborating
            # links, not the raw link count. A naive "≥2 links" lets an attacker mint independence by naming
            # one origin many ways ("Wikipedia" / "wikipedia.org" / a full URL → 3 links, 1 real source).
            # Canonicalizing source identifiers before counting collapses those to one; a link whose record
            # has no source counts as its own id, so genuinely source-less corroboration is unchanged.
            corroborated = self._graduation_corroborated(r, _by_id)
            if reinforce and self._may_graduate(r, _by_id):
                r["mtype"] = "semantic"
                r.setdefault("meta", {})["graduated_from_episodic"] = True
            _o = {"id": r["id"], "text": r["text"], "tags": r["tags"], "iso": r["iso"],
                  "value": round(r["value"], 2), "relevance": round(sim, 3),
                  "score": round(score, 3), "links": r["links"],
                  **({"resolved_over": _rc_losers[r["id"]]} if r["id"] in _rc_losers else {}),
                  "reliability": round(self._reliability(r), 3),
                  "source": r.get("source"),    # re-checkable origin (provenance), surfaced so a recalled fact can be traced back
                  "stale_derived": bool(r.get("_stale_derived"))}
            if r.get("reopened"):
                # A read-path review-trigger (observe()) reopened this settled record on a corroborated
                # contradiction: recall still returns it as the current best guess, but the CONSUMER must know
                # it is contested — otherwise the agent acts on a value a steward has flagged, with full
                # confidence. Surface the flag + the surfaced prior so a caller can branch (defer, ask, hedge).
                _m = r.get("meta", {})
                _o["under_review"] = True
                _o["review_reason"] = _m.get("reopened_reason")
                if _m.get("reopened_surfaced_prior") is not None:
                    _o["review_prior"] = _m.get("reopened_surfaced_prior")
            if with_status:     # OPT-IN: carry the honest truth-status at the point of use (convergence-backed
                cr = self.convergence_report(r, _by_id=_by_id)   # vs adjudicated), never let convergence read as truth
                _o["convergence"] = cr["status"]
                if cr.get("low_source_diversity"):
                    _o["low_source_diversity"] = True
            if with_warrant:    # OPT-IN: a LEGIBLE warrant tier a consumer can BRANCH ON, so "no independent
                # channel" is an explicit state, not a quiet low score a downstream reads as a soft yes (the
                # silent-weight-0-decays-to-"unverified-but-present" failure; jacksonxly, r/RAG 2026-07). Tiers:
                #   'earned'       -- un-self-gradable outcome credit (good>0>=bad) OR a semantic memory that
                #                     GRADUATED there through the corroboration bar
                #   'corroborated' -- >=2 distinct sources/verified-keys, but not yet outcome-earned (weaker)
                #   'unwarranted'  -- single self-asserted, orphan (no lineage), or slashed -> DO NOT treat as a
                #                     confirmation; weight it ~0 and, critically, mark it so downstream sees the abstention.
                #
                # THE TOP TIER MUST NOT BE SETTABLE BY THE WRITER. `mtype="semantic"` is an accepted
                # argument to remember(), so the bare `mtype == "semantic"` test handed `earned` to any
                # record whose author simply asked for it -- good=None, bad=None, links=[] and still the
                # highest tier we report. Measured 2026-08-08 (research/probes/warrant_tier_adversarial.py
                # in the agora repo, A1). The influence gate below already refused exactly this and says so
                # in its own comment at the `require_warrant` branch; the LABEL was left behind, which is
                # the same one-call-site-over drift that this file has been bitten by before. So gate the
                # semantic clause on the graduation marker the code already stamps, and read credit through
                # `_good_earned` so `credit_requires_warrant` reaches the tier too (it computed the value
                # and then discarded it -- A2). Same definition as _graduation_corroborated: one rule, one
                # answer. Nothing changes by default for outcome credit, since _good_earned IS _good until
                # the flag is on; what changes is that a DECLARED semantic no longer outranks a corroborated
                # one.
                _good, _bad, _good_earned, _distinct = self._corroboration_facts(r, _by_id)
                if (r.get("meta") or {}).get("slashed") or r.get("orphan"):
                    _o["warrant"] = "unwarranted"
                elif ((_good_earned > 0 and _good >= _bad)
                      or (r.get("mtype") == "semantic"
                          and (r.get("meta") or {}).get("graduated_from_episodic"))):
                    _o["warrant"] = "earned"
                elif _distinct >= 2:
                    _o["warrant"] = "corroborated"
                else:
                    _o["warrant"] = "unwarranted"
                # NO SILENT MASKING. `warrant` is one scalar and `earned` is tested first, so a record
                # backed by BOTH channels reported only the outcome one and the corroboration vanished
                # -- two independent facts collapsed into a total order. That is also why describing the
                # tiers as "channels, not levels" was untrue of this code. The two channels are now
                # reported separately alongside the tier: `earned` and `corroborated` are different
                # KINDS of evidence (an outcome the record did not author vs. independent witnesses),
                # and which one a consumer should weigh more depends on which is anchored in their
                # deployment -- `credit_requires_warrant` for the first, `strict_corroboration` for the
                # second. The scalar stays for back-compat and keeps its precedence.
                _o["warrant_earned"] = bool(
                    (_good_earned > 0 and _good >= _bad)
                    or (r.get("mtype") == "semantic"
                        and (r.get("meta") or {}).get("graduated_from_episodic")))
                _o["warrant_corroborated"] = bool(_distinct >= 2)
                _o["warrant_sources"] = int(_distinct)
            if r.get("pii"):
                _o["pii"] = list(r["pii"])          # surface which PII types this record carries (audit/branch)
            # PII MASKING (OPT-IN redact_pii): mask detected PII in the RETURNED text only — the stored record is
            # untouched, so an agent gets usable context without raw PII flowing into an LLM prompt. Heuristic
            # (detect_pii bounds); pair with pii_detect/forget_pii for data-minimization, not as a guarantee.
            if redact_pii:
                _o["text"], _masked = redact_pii_fn(_o["text"])
                if _masked:
                    _o["pii_masked"] = _masked
            out.append(_o)
        # AUTO-STAMP LINEAGE: remember what this recall surfaced, so a derived write built from it (a summary
        # written next) can inherit these as parents. Store-carried lineage from the recall->write flow.
        self._last_recall = [o["id"] for o in out]
        # keep the recalled TEXT too, so infer_lineage can decide from content instead of a caller's flag
        self._last_recall_text = " ".join(str(o.get("text") or "") for o in out) if self.infer_lineage else ""
        # ...and WHEN, WHAT ASKED, and a fresh write-counter, so a write that stamps this window carries the
        # two things that decide whether the window is even plausible: its AGE and its POSITION in the run of
        # writes that followed. Neither is thresholded here -- a cutoff baked in at write time is a parameter
        # the later analysis could never reach past, so the raw observation is stored and every cutoff is left
        # to the analysis. The query is fingerprinted so an analysis can tell which writes came from the same
        # recall -- which is what exposes a window set by a COLLEAGUE's query in a shared-store deployment.
        # `q` IS A GROUPING KEY AND NOT A PRIVACY MEASURE, and this comment claimed otherwise until an
        # adversarial review corrected it: hashing personal data is pseudonymisation, not anonymisation
        # (AEPD/EDPS joint paper), and a short query drawn from a low-entropy space is recoverable by
        # dictionary search. Storing the digest rather than the text lowers incidental exposure; it does
        # not put the query out of scope. If the queries carry personal data, so does this field.
        #
        # `observe=False` marks a read that is NOT part of a recall->write flow, and it is needed because
        # some reads are about the store rather than for the writer: a scoring pass that credits memories
        # after an outcome, a maintenance sweep, or -- the case that forced this -- one agent reading a
        # COLLEAGUE's store. That last one is not merely noisy, it is unfilterable: a foreign read resets
        # the colleague's window AND its write counter, so the colleague's next write looks exactly like a
        # write that followed its own recall (w=0, fresh `at`). No later analysis could separate them, so
        # the caller that knows the read was foreign has to say so here.
        #
        # It INVALIDATES the window rather than freezing it. Leaving the old `at`/`q`/`w` in place while
        # `_last_recall` moved on would stamp the next write with one recall's ids and another recall's
        # timestamp -- a record that looks complete and is internally false, which is worse than no record.
        # `_last_recall` / `_last_recall_text` are deliberately still updated: they drive the pre-existing
        # derived=True and infer_lineage paths, and changing those here would be a behaviour change wearing
        # an observability change's clothes.
        if self.observe_recall:
            if observe:
                self._note_recall_window([o["id"] for o in out], query)
            else:
                self._last_recall_at = 0.0      # sentinel: no valid observation to attribute to a write
        # NOTE: recall is a READ. It nudges in-memory access value / graduation, but must NOT persist the
        # whole store here — serializing (json.dumps) on every recall, across many agents' stores,
        # saturated the thread pool and FROZE the world. The in-memory nudges are persisted on the next
        # remember()/consolidate()/flush(); losing recent access metadata on a hard crash is harmless.
        if out:
            self._dirty = True   # mark for the next throttled/forced save; do NOT serialize on the read path
        return out

    def _note_recall_window(self, ids, query) -> None:
        """Record what the store served for ONE LOGICAL recall operation, and when, and what asked.

        Called once per operation, not once per underlying `recall()`: a multi-hop retrieval
        (`recall_iterative`, or a `recall_iterative_followup` that bridges to more records) is one act of
        retrieval from the caller's point of view, and the window has to be the whole set it saw or the
        stamp understates what the caller was actually holding when it wrote. `w` resets here for the same
        reason — a follow-up hop is not a write, and counting it as one would make the position of the
        first real write unrecoverable."""
        if not self.observe_recall:
            return
        self._last_recall_window = list(ids)
        self._last_recall_at = time.time()
        self._last_recall_q = hashlib.sha256(str(query or "").encode("utf-8", "replace")).hexdigest()[:12]
        self._last_recall_writes = 0

    @staticmethod
    def _canon_subject(doc) -> str:
        """Identity-preserving normalisation of a SUBJECT, for erasure. Keeps the path.

        `_canon_source` exists for entity resolution -- collapsing sybil variants of one ORIGIN
        ('Wikipedia', 'wikipedia.org', 'https://www.wikipedia.org/wiki/X') to a single trust key -- and to
        do that it keeps only the host: `s.split("/")[0]`. That is right for attribution and WRONG for
        identity, because in the convention this library's own docstrings use, the path IS the person.

        MEASURED, which is how this was found: 'crm/alice', 'crm/nobody-here' and 'crm/bob' all canonicalise
        to 'crm'. With one real source in that bucket no collision fires, so forget_subject('crm/nobody-here')
        -- a right-to-erasure request naming somebody who was never in the store -- hard-deleted BOTH of
        crm/alice's records and returned erased=2. Not a clean verdict about unexamined input this time: a
        DELETION on unexamined identity.

        Same normalisation, path retained. 'User_42' and 'user-42' still resolve alike, so the tolerance the
        ambiguity guard exists for is unchanged; 'crm/alice' and 'crm/bob' no longer do, which is correct --
        they are two people.
        """
        s = str(doc or "").strip().lower()
        s = re.sub(r"^[a-z]+://", "", s)
        s = re.sub(r"^www\.", "", s)
        s = s.split("?")[0].rstrip("/")
        s = re.sub(r"\.(org|com|net|io|gov|edu|co|ai|dev|info|news)(?=/|$)", "", s)
        # COLLAPSE punctuation to a separator, do not DELETE it. Deleting it made 'crm/alice-1' and
        # 'crm/alice1' one identity, so a DSAR naming an identifier absent from the store erased a real
        # one -- measured, and the collision guard cannot fire because it needs an exact raw match that
        # does not exist. Collapsing keeps the tolerance the guard is for ('User_42' and 'user-42' both
        # become 'user-42') while keeping a digit that follows a separator distinct from one that does not.
        return re.sub(r"[^a-z0-9]+", "-", s).strip("-")

    def _narrow_to_subject(self, subject: str, ids: list) -> list:
        """Keep only the records whose own source really is this subject, path and all.

        The broad canonical match above is what finds candidates; this is what stops it deleting a
        different person who merely shares a host. Applied only when at least one candidate matches at the
        fine level -- a subject given as 'id:<record id>', or any convention where the coarse match is the
        only one available, keeps working untouched rather than silently erasing nothing.
        """
        want = Inspeximus._canon_subject(subject)
        if not want or str(subject).startswith("id:"):
            return ids
        by_id = {r["id"]: r for r in self.items}
        # TAINT CANNOT SELECT FOR DELETION. It stores already-CANONICAL source keys, so once provenance
        # is inherited, two subjects sharing a coarse bucket are indistinguishable in it -- a limit that
        # was known and written down, and that the first version of this narrowing then used as a
        # selector anyway. Measured, that turned the stated limit into two data-loss paths:
        #   forget_subject("crm/nobody-here") erased a summary derived from crm/alice -- the ghost
        #     returning through the very branch this docstring claimed it could not use;
        #   forget_subject("crm/alice") erased a summary derived from crm/BOB, with no refusal, because
        #     the summary's raw source is the writer service and the collision guard never sees it.
        # Inheritance now travels ONLY along declared derived_from edges from a finely-matched root,
        # which is provenance the store actually recorded rather than a bucket it can no longer resolve.
        # ...with ONE exception, and it is decided by whether canonicalisation loses anything about THIS
        # subject. `taint` holds `_canon_source` keys, so it can attribute precisely only when the coarse
        # key still spells the whole subject: 'user-42' survives canonicalisation intact, 'crm/alice' does
        # not -- it becomes 'crm', which is also 'crm/bob' and 'crm/nobody-here'. Removing the taint path
        # outright fixed the two data-loss cases and broke a real one: erase the parent by id first and the
        # derived records are reachable ONLY by taint, so a later forget_subject left dangling lineage and
        # the audit rightly reported residue. Allowing it only for path-free subjects keeps that cleanup
        # working while a subject whose identity lives in the discarded path can never reach a stranger.
        coarse = Inspeximus._canon_source(subject)
        taint_is_sound = want.replace("-", "") == coarse
        inherited = ({rid for rid in ids if coarse in set(by_id.get(rid, {}).get("taint") or [])}
                     if taint_is_sound else set())
        roots = {rid for rid in ids
                 if Inspeximus._canon_subject(self._raw_source(by_id.get(rid, {}))) == want}
        if not roots and inherited:
            return [rid for rid in ids if rid in inherited]
        if not roots:
            # nothing in the store really is this subject -- only records that share a coarse bucket with
            # it. That is the ghost case: erase nothing rather than somebody else's data.
            return []
        # ...and everything downstream of those roots. The first version of this narrowing kept ONLY the
        # roots and dropped the derived-lineage cascade, which is the whole point of forget_subject: a
        # summary built from the subject's data must go too. Thirteen tests caught it, and an incomplete
        # erasure would have been a worse defect than the over-broad one being fixed. Same forward closure
        # the exact=True path already uses.
        keep, frontier = set(roots), set(roots)
        while frontier:
            nxt = {r["id"] for r in self.items
                   if r["id"] not in keep and (set(r.get("derived_from") or []) & frontier)}
            keep |= nxt
            frontier = nxt
        keep |= inherited
        return [rid for rid in ids if rid in keep]

    @staticmethod
    def _canon_source(doc) -> str:
        """Entity-resolution canonicalization of a source identifier, so sybil variants of one origin
        ('Wikipedia', 'wikipedia.org', 'https://www.wikipedia.org/wiki/X') collapse to a single key."""
        s = str(doc or "").strip().lower()
        s = re.sub(r"^[a-z]+://", "", s)                 # strip scheme
        s = re.sub(r"^www\.", "", s)                     # strip www.
        s = s.split("/")[0].split("?")[0]                # host / first path segment only
        s = re.sub(r"\.(org|com|net|io|gov|edu|co|ai|dev|info|news)$", "", s)  # strip a common TLD
        s = re.sub(r"[^a-z0-9]+", "", s)                 # collapse remaining punctuation
        return s

    @staticmethod
    def _rec_sources(rec: dict) -> set:
        """The canonical sources a record is attributable to: its OWN source (entity-resolved) PLUS any taint
        inherited from parents via derived_from (so provenance rides through summarization/consolidation). A
        source-less record is attributable to its own id, so nothing is silently un-attributable. Used by
        slash()/restore() so forfeiting a source also reaches every derived record it fed."""
        src = rec.get("source")
        doc = src.get("doc") if isinstance(src, dict) else (src if isinstance(src, str) else None)
        own = Inspeximus._canon_source(doc) if doc else "id:" + rec["id"]
        return {own} | set(rec.get("taint") or [])

    @staticmethod
    def _distinct_sources(links, by_id) -> int:
        """Count DISTINCT canonical sources among corroborating links — entity resolution BEFORE counting,
        so 'three names for one source' sybil variants count as one. A link whose record carries no source
        counts as its own id, so genuinely source-less corroboration is not penalised (no regression).

        LINEAGE BEATS THE DOCUMENT NAME (2.5.1). yun520-1 raised this on DeepSeek-V3#1462: corroborating
        sources must point at different UPSTREAM EVIDENCE CHAINS, because otherwise "two links are one
        belief's echo". He credited our '>=2 distinct-source links' wording with capturing it. Measured
        (probes/corroboration_counts_an_echo_as_two_witnesses.py, three controls holding): it did not.
        One memo restated into two documents passed as two independent witnesses, because this counted
        `source.doc` STRINGS and never looked at `derived_from`.

        So a link that declares ancestry is now identified BY that ancestry rather than by the name of
        the document it was restated into: two restatements of one parent collapse to one witness, two
        genuinely separate chains stay two, and a link with no lineage keeps exactly its previous
        identity — which is why this costs nothing on existing data (measured across 84 stores /
        240,810 records: 198,086 carry >=2 links, 108 carry any lineage, 0 would lose corroboration).
        That last number is also the warning: the rail is correct and, today, almost unreached. The
        remaining work is lineage COVERAGE, not this function.
        """
        keys = set()
        for lid in (links or []):
            lr = by_id.get(lid)
            if lr is None:
                continue
            parents = lr.get("derived_from") or []
            if parents:
                keys.add(("lineage",) + tuple(sorted(str(p) for p in parents)))
                continue
            src = lr.get("source")
            doc = src.get("doc") if isinstance(src, dict) else (src if isinstance(src, str) else None)
            keys.add(Inspeximus._canon_source(doc) if doc else "id:" + lid)
        return len(keys)

    @staticmethod
    def _distinct_verified_keys(links, by_id) -> int:
        """Count DISTINCT VERIFIED KEYS among corroborating links: an attacker cannot manufacture N
        'independent' witnesses without N distinct Ed25519 keys it holds (forging one = breaking the
        signature). Links whose record carries no attested_key do NOT count here — strict corroboration
        demands cryptographic, not string, independence. Complements _distinct_sources (the default,
        string-based, spoofable rail)."""
        keys = set()
        for lid in (links or []):
            lr = by_id.get(lid)
            if lr is not None and lr.get("attested_key"):
                keys.add(lr["attested_key"])
        return len(keys)

    @staticmethod
    def _is_corroborated(rec: dict, by_id: dict, strict: bool = False, require_warrant: bool = False) -> bool:
        """The corroboration bar shared by episodic->semantic graduation and the recall influence gate:
        an EARNED net-positive outcome (good>0 and good>=bad — set by credit() on real work, not
        self-assertable), OR an already-graduated 'semantic' memory, OR >=2 corroborating links from
        distinct sources. `strict` selects the independence measure for that last path: distinct VERIFIED
        KEYS (unforgeable) when True, distinct canonical-source STRINGS (spoofable but zero-setup) when
        False. A single fresh self-asserted memory (the AgentPoison single-instance poison) meets none.
        `require_warrant` (set by the store flag credit_requires_warrant) closes the MINJA self-graded-
        outcome hole: the earned-outcome path then counts only EXOGENOUSLY-WARRANTED good (credit() called
        with a warrant naming an outcome source outside the record's own lineage), so an agent that credits
        its OWN recalled reasoning as a success cannot self-corroborate a poisoned bridge into the influence
        set. Measured: inspeximus/probes/minja_influence_gate.py (self-graded ASR 80% -> 0% with the flag on).
        A LANDED RETRACTION WINS: a record slash()'d (meta['slashed']) is not corroborated on ANY path — incl.
        distinct-link corroboration — so a caught poison cannot stay load-bearing via independent-looking links
        (jacksonxly's invariant: nothing false stays load-bearing past the correctness signal). restore() clears
        the flag, so this is reversible; receipt inspeximus/probes/retraction_propagation.py.
        FAIL-CLOSED PROVENANCE: an ORPHAN (a declared transformation output that named no parent, meta-flag
        rec['orphan']) is likewise not corroborated on any path -- missing lineage is treated as unverified, so
        an app-side summary that dropped its derived_from cannot quietly earn standing or survive a retraction."""
        return Inspeximus._corroboration_verdict(rec, by_id, strict, require_warrant)[0]

    @staticmethod
    def _corroboration_verdict(rec: dict, by_id: dict, strict: bool = False,
                               require_warrant: bool = False) -> tuple:
        """The gate predicate AND the reason, as one value: (passes, reason).

        SINGLE SOURCE OF TRUTH, and it exists because there was a second one. `influence_gate_report()`
        re-derived this test inline and had already DRIFTED from it: it omitted the slashed/orphan hard
        blocks, ignored require_warrant (counting raw `good` where the gate counts `good_warranted`), and
        did not apply the require_warrant condition on the semantic path. All three errors run the same
        way -- the report counted records as corroborated that the gate refuses -- so it OVERSTATED
        corroborated_frac and UNDERSTATED would_block_frac on exactly the stores that had switched the
        extra defenses on. MEASURED on a store with credit_requires_warrant=True, six records with
        unwarranted good credit and two of them slashed: the gate passes 0 of 6, the report claimed 6 of
        6 with would_block_frac 0.00. That report's stated purpose is to tell an operator whether the
        gate is affordable before enabling it, so the instrument was maximally wrong in the direction
        that causes an outage -- enable it on that advice and recall(influence_only=True) returns
        nothing. A predicate with two implementations has one implementation and one bug waiting.

        `reason` names the bar that decided it, so an inspector can say WHY a record was refused without
        re-deriving any of this a third time.
        """
        if (rec.get("meta") or {}).get("slashed"):
            return False, "slashed: a landed retraction blocks corroboration on every path"
        if rec.get("orphan"):
            return False, "orphan: declared derivation named no parent, so lineage is unverified"
        good = float(rec.get("good", 0) or 0)
        bad = float(rec.get("bad", 0) or 0)
        good_earned = float(rec.get("good_warranted", 0) or 0) if require_warrant else good
        if good_earned > 0 and good >= bad:
            return True, ("earned outcome (warranted good %.3g >= bad %.3g)" % (good_earned, bad)
                          if require_warrant else
                          "earned outcome (good %.3g >= bad %.3g)" % (good, bad))
        # NAME THE BAR THAT ACTUALLY DECIDED. A record that once had standing and lost it to negative
        # outcomes must not be reported as "0 corroborating sources" -- that is the last test in the
        # chain, not the one that refused it, and it hides the single case an operator most needs to
        # see: a constraint suppressed by accumulated bad credit rather than one that never earned any.
        if good > 0 and bad > good:
            return False, ("outcome standing lost (good %.3g < bad %.3g)" % (good, bad)
                           + ("" if not require_warrant else
                              "; warranted good %.3g" % good_earned))
        if require_warrant and good > 0 and good_earned <= 0:
            return False, ("good %.3g present but none warranted, and credit_requires_warrant is on"
                           % good)
        if rec.get("mtype") == "semantic":
            # A 'semantic' mtype counts as corroborated because it is normally an EARNED, graduated-durable
            # memory. But remember() also auto-classifies short declarative statements as semantic AT WRITE
            # TIME — and MINJA's progressive-shortening bridges are exactly such query-shaped declaratives, so
            # they would be born semantic and bypass the gate with zero corroboration. Under require_warrant
            # only EARNED semantic (graduated_from_episodic through the corroboration bar) passes; a write-time
            # semantic classification is treated as an unproven episodic claim.
            if not require_warrant or (rec.get("meta") or {}).get("graduated_from_episodic"):
                return True, "semantic tier (earned/graduated)" if require_warrant else "semantic tier"
        if strict:
            n = Inspeximus._distinct_verified_keys(rec.get("links"), by_id)
            return (n >= 2), "%d distinct verified corroborating key(s), need 2" % n
        n = Inspeximus._distinct_sources(rec.get("links"), by_id)
        return (n >= 2), "%d distinct corroborating source(s), need 2" % n

    def _coherence(self, a_rec: dict, b_rec: dict) -> float:
        """Semantic coherence of two records in [0,1]: embedder cosine if `embed` is set and both carry a vec,
        else lexical token-Jaccard. Used by the OPT-IN coherence gate to test whether a corroborating witness is
        actually ABOUT the claim (not off-topic filler minted to game the source count)."""
        va, vb = a_rec.get("vec"), b_rec.get("vec")
        if self.embed and va and vb:
            num = sum(x * y for x, y in zip(va, vb))
            na = math.sqrt(sum(x * x for x in va)); nb = math.sqrt(sum(y * y for y in vb))
            if na > 0 and nb > 0:
                return max(0.0, min(1.0, num / (na * nb)))
        ta = set(re.findall(r"[a-z0-9]+", (a_rec.get("text") or "").lower()))
        tb = set(re.findall(r"[a-z0-9]+", (b_rec.get("text") or "").lower()))
        return len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0

    def _temporal_collapse(self, links: list, by_id: dict) -> list:
        """Collapse CO-ARRIVING corroborating links to one anchor each: greedy over ascending ts, a link opens a
        new cluster only if it lands > self.temporal_gate seconds after the current cluster's anchor; links inside
        the window are dropped (treated as one coordinated write). Genuinely independent sources spread out in
        time; a burst collapses to one."""
        win = self.temporal_gate
        recs = sorted((by_id[l] for l in links if by_id.get(l) is not None),
                      key=lambda r: float(r.get("ts", 0) or 0))
        kept, anchor = [], None
        for r in recs:
            t = float(r.get("ts", 0) or 0)
            if anchor is None or (t - anchor) > win:
                kept.append(r["id"]); anchor = t
        return kept

    def _gated_links(self, rec: dict, by_id: dict) -> list:
        """The effective corroborating links after the OPT-IN gates: drop off-topic witnesses (coherence_gate),
        then collapse co-arriving witnesses to one anchor (temporal_gate). Both off (default) -> links unchanged."""
        links = rec.get("links") or []
        if self.coherence_gate is not None:
            links = [lid for lid in links
                     if by_id.get(lid) is not None and self._coherence(rec, by_id[lid]) >= self.coherence_gate]
        if self.temporal_gate is not None and len(links) > 1:
            links = self._temporal_collapse(links, by_id)
        return links

    # kept for back-compat; _gated_links is the combined path
    def _coherent_links(self, rec: dict, by_id: dict) -> list:
        if self.coherence_gate is None:
            return rec.get("links") or []
        return [lid for lid in (rec.get("links") or [])
                if by_id.get(lid) is not None and self._coherence(rec, by_id[lid]) >= self.coherence_gate]

    @staticmethod
    def _canon_of(rec: dict) -> str:
        """The single canonical source string of ONE record (same rule _distinct_sources counts by): its
        entity-resolved `source.doc`/string, else 'id:'+its id. (It does NOT emit a 'key:<k>' form; the docstring claimed that until 1.54.0 and no caller ever saw one -- _trusted_sources tests the attested key on a separate branch.)"""
        src = rec.get("source")
        doc = src.get("doc") if isinstance(src, dict) else (src if isinstance(src, str) else None)
        return Inspeximus._canon_source(doc) if doc else "id:" + rec.get("id", "")

    def _trusted_sources(self, by_id: dict) -> set:
        """Trust closure grown from `trust_seeds` via VOUCH edges, bounded by `trust_hops` (asymmetric,
        flow-based; Gyongyi et al. 2004 TrustRank / Cheng-Friedman 2005). A source U enters the closure iff
        U is a seed, or a record whose source is ALREADY trusted has a `link` to a record authored by U (an
        explicit endorsement by a trusted actor). Free self-minted sources that no seed vouches for never
        enter. Recomputed per corroboration check (stores are small; O(hops * links))."""
        trusted = set(self.trust_seeds)
        if not trusted:
            return trusted
        for _ in range(max(0, int(self.trust_hops))):
            added = set()
            for r in by_id.values():
                # a record authored by a trusted source vouches for the sources of the records it links to
                if self._canon_of(r) in trusted or ("key:" + str(r.get("attested_key"))) in trusted:
                    for lid in (r.get("links") or []):
                        lr = by_id.get(lid)
                        if lr is not None:
                            added.add(self._canon_of(lr))
            if added <= trusted:
                break
            trusted |= added
        return trusted

    def _corroborated(self, rec: dict, by_id: dict) -> bool:
        """Instance corroboration check = the static bar, plus the OPT-IN coherence + temporal gates: only ON-TOPIC,
        temporally-independent corroborating links count toward the >=2-distinct-source path, plus the OPT-IN
        seed-anchored trust filter: when `trust_seeds` is set, only witnesses whose source is in the trust
        closure count. Default (no gates, empty seeds) == static bar."""
        links = rec.get("links")
        if (self.coherence_gate is not None or self.temporal_gate is not None) and links:
            eff = self._gated_links(rec, by_id)
            if eff != links:
                rec = {**rec, "links": eff}; links = eff   # shallow copy; never mutate the stored record
        if self.trust_seeds and links:
            trusted = self._trusted_sources(by_id)
            # keep only corroborating witnesses authored by a trust-reachable source (own source or the
            # seeds themselves always qualify); a Sybil's un-vouched sources are dropped before the count.
            eff = [lid for lid in links if by_id.get(lid) is not None
                   and self._canon_of(by_id[lid]) in trusted]
            if eff != links:
                rec = {**rec, "links": eff}
        return Inspeximus._is_corroborated(rec, by_id, self.strict_corroboration,
                                      require_warrant=getattr(self, "credit_requires_warrant", False))

    def influence_gate_report(self) -> dict:
        """Report the LIVE COST of the influence gate (recall(influence_only=True)) on THIS store, so you can
        judge whether it is affordable before enabling it. The gate keeps only CORROBORATED memories
        (_is_corroborated); its cost is that not-yet-earned LEGITIMATE memories are filtered too, and that cost
        is DENSITY-DEPENDENT. MEASURED on a controlled corpus with real embeddings
        (inspeximus/probes/oracle_separation_density.py): the fraction of legitimate high-stakes recalls the gate
        blocks falls from ~51% when each memory is used ~once (sparse) to ~6% when each is used ~8x (dense),
        because a legit memory only earns standing through repeated successful use — so in a SPARSE store the
        gate is expensive (it filters most legit recalls); grow density, or credit() real outcomes, before
        relying on influence_only for anything but adversarial/untrusted ingestion.
        A SECOND, load-bearing caveat the same probe measured: the gate rides ENTIRELY on credit() being an
        outcome oracle the attacker CANNOT SELF-GRADE. A MINJA-style self-graded outcome (arXiv:2503.03704)
        collapses the gate at every density — it can even block legit MORE than poison. Never let recalled
        memory content drive its own credit(); issue outcomes from the application, on real resolved work.
        Returns {active, corroborated, corroborated_frac, would_block_frac, standing_lost, by_path{earned_outcome, semantic,
        multi_source}, advice}. Read-only; no side effects."""
        byid = {x["id"]: x for x in self.items}
        active = [r for r in self.items if r.get("status") == "active"]
        n = len(active)
        earned = sem = multi = corr = 0
        blocked = 0
        for r in active:
            # Route through the SAME predicate recall() uses, via the instance wrapper so the opt-in
            # coherence/temporal/trust-seed gates apply too. This replaced an inline re-derivation that
            # had drifted from the gate (no slashed/orphan block, require_warrant ignored) and therefore
            # told operators the gate was cheaper than it is -- see _corroboration_verdict.
            ok = self._corroborated(r, byid)
            if ok:
                corr += 1
                _, why = Inspeximus._corroboration_verdict(
                    r, byid, self.strict_corroboration, getattr(self, "credit_requires_warrant", False))
                if why.startswith("earned"):
                    earned += 1
                elif why.startswith("semantic"):
                    sem += 1
                else:
                    multi += 1
            else:
                blocked += 1
                # THE EMIT. A record with good>0 and bad>good did not merely fail to earn standing --
                # it HAD standing and lost it to accumulated negative outcomes. That is the shape of a
                # suppressed constraint, and until now nothing surfaced it: a caller had to suspect a
                # specific record and interrogate it. Counting it here puts it in a routine surface, so
                # an operator who never suspected suppression still sees a non-zero number.
                # Deliberately a COUNT, not an alarm: losing standing is also what correction looks
                # like when a memory was genuinely wrong, and the two are not distinguishable from the
                # counters alone. It says "look here", not "you are under attack".
        standing_lost = sum(1 for r in active
                            if float(r.get("good", 0) or 0) > 0
                            and float(r.get("bad", 0) or 0) > float(r.get("good", 0) or 0))
        frac = (corr / n) if n else 0.0
        advice = ("cheap - most active memories are corroborated" if frac >= 0.7 else
                  "affordable" if frac >= 0.4 else
                  "expensive - store too sparse; influence_only will filter most legit recalls. Grow density "
                  "or credit() real outcomes first, or use it only for untrusted-ingestion defense.")
        return {"active": n, "corroborated": corr, "corroborated_frac": round(frac, 3),
                "would_block_frac": round(1.0 - frac, 3), "strict_corroboration": self.strict_corroboration,
                "by_path": {"earned_outcome": earned, "semantic": sem,
                            ("multi_verified_key" if self.strict_corroboration else "multi_source"): multi},
                "standing_lost": standing_lost,
                "advice": advice}

    @staticmethod
    def _cond_match(r: dict, conds: dict) -> bool:
        """Does record r satisfy ALL of `conds` (a where/prefer dict)? Each field is matched against the
        record's top-level attributes first, then its meta dict. A condition is a scalar (equality), a
        list/tuple/set (membership), or a dict of operators ($eq/$ne/$in/$nin/$gte/$lte/$gt/$lt/$contains).
        Shared by the hard `where` pre-filter and the soft `prefer` trust-weighted boost."""
        meta = r.get("meta") or {}
        for field, cond in conds.items():
            val = r[field] if field in r else meta.get(field)
            if isinstance(cond, dict):
                for op, cv in cond.items():
                    if op in ("$eq", "eq"):
                        if val != cv: return False
                    elif op in ("$ne", "ne"):
                        if val == cv: return False
                    elif op in ("$in", "in"):
                        if val not in cv: return False
                    elif op in ("$nin", "nin"):
                        if val in cv: return False
                    elif op in ("$gte", "gte"):
                        if val is None or val < cv: return False
                    elif op in ("$lte", "lte"):
                        if val is None or val > cv: return False
                    elif op in ("$gt", "gt"):
                        if val is None or val <= cv: return False
                    elif op in ("$lt", "lt"):
                        if val is None or val >= cv: return False
                    elif op in ("$contains", "contains"):
                        if val is None or cv not in val: return False
                    else:
                        raise ValueError(f"recall condition: unknown operator {op!r}")
            elif isinstance(cond, (list, tuple, set)):
                if val not in cond: return False
            else:
                if val != cond: return False
        return True

    @staticmethod
    def _reliability(r: dict) -> float:
        """Per-memory track record as a Beta(1+good, 1+bad) posterior MEAN: 0.5 with no outcomes yet,
        ->1 if recalls into it kept resolving WELL, ->0 if they kept resolving badly. Counts only grow."""
        g = float(r.get("good", 0) or 0)
        b = float(r.get("bad", 0) or 0)
        return (g + 1.0) / (g + b + 2.0)

    @staticmethod
    def _outcome_good(outcome) -> bool:
        """Parse a credit/monitor outcome: bool, a sign (>0 good), or a verdict string."""
        if isinstance(outcome, bool):
            return outcome
        if isinstance(outcome, (int, float)):
            return outcome > 0
        return str(outcome).strip().lower() in ("good", "right", "correct", "reproduced", "hit", "true", "win", "+")

    def credit(self, ids, outcome, weight: float = 1.0, warrant=None) -> dict:
        """Close the accuracy loop onto the substrate. When the work a set of memories was recalled into
        gets a real verdict (a forecast resolves, a replication is ruled REPRODUCED/FAILED, a hypothesis is
        severe-tested), call credit(recalled_ids, outcome): each memory's Beta(good,bad) track record is
        nudged so future recall ranks by WAS-IT-RIGHT, not merely was-it-recalled. Append-only to the
        counts; never edits raw text. `outcome` may be a bool, a sign (>0 good), or a verdict string
        (good/right/correct/reproduced/hit vs bad/wrong/failed/miss).

        `warrant` (OPT-IN, matters only when the store flag credit_requires_warrant is on): a token/string
        naming the EXOGENOUS outcome source that vouches for this credit — a resolved ticket, a graded
        forecast, an external verdict — i.e. ground truth the recalled memory did NOT produce itself. A good
        credit whose warrant is exogenous to the record (not None, and not the record's own source/lineage)
        also increments `good_warranted`; the influence gate then counts only warranted good. This
        structurally breaks the MINJA self-graded-outcome loop (an agent crediting its own recalled poison as
        a success): with no exogenous outcome to name, the self-grade raises good but never good_warranted, so
        it cannot promote the poison into the influence set. HONEST RESIDUAL: a warrant STRING is spoofable
        the same way a source string is (an attacker who can forge an outcome token can still warrant) — it
        raises attacker cost and is meant to be paired with verifiable provenance, not a proof of truth.
        NO EVIDENCE TRAIL, and this is the limit to know. A credit changes `good`/`bad`, which the influence
        gate and corroboration check read — so it decides whether a record can be served under
        recall(influence_only=True). But it emits NO write receipt, and `state_digest()` does not cover
        standing, so after `credit([poison], outcome=True, weight=1e6)`:

            receipts    1 -> 1        (unchanged)
            state_digest              (unchanged)
            verify_writes() -> True   (clean)

        Measured. The promotion is invisible to every integrity surface this library sells. The self-grading
        risk is already disclaimed via credit_requires_warrant (a warrant STRING is spoofable); what was not
        said is that the promotion leaves no trace to audit afterwards. If outcomes come from anywhere an
        attacker influences, gate them at the source — there is no after-the-fact check here.
        """
        good = Inspeximus._outcome_good(outcome)
        by_id = {x["id"]: x for x in self._tenant_rows()}
        key, updated = ("good" if good else "bad"), []
        win, now, collapsed = self.credit_burst_window, time.time(), []
        for i in _as_ids(ids):
            rec = by_id.get(i)
            if rec is None:
                continue
            if win:
                # Same-polarity credit for this record inside the window is ONE occasion, not many.
                last = (rec.get("credit_seen") or {}).get(key)
                if last is not None and (now - float(last)) < float(win):
                    collapsed.append(i)
                    continue
                rec.setdefault("credit_seen", {})[key] = now
            rec[key] = float(rec.get(key, 0) or 0) + float(weight)
            if good and self._warrant_is_exogenous(rec, warrant):
                rec["good_warranted"] = float(rec.get("good_warranted", 0) or 0) + float(weight)
            updated.append(i)
        if updated:
            self._save()
        return {"updated": updated, "outcome": key, "weight": weight,
                **({"collapsed": collapsed} if collapsed else {})}

    def _warrant_is_exogenous(self, rec: dict, warrant) -> bool:
        """A warrant vouches for an outcome the record did NOT author itself. Exogenous = a non-empty token
        that is neither the record's own canonical source nor any tenant/source in its transitive lineage.
        Conservative by design: an absent warrant is never exogenous, so self-graded credit (the MINJA path)
        earns no warranted-good.

        BOTH SIDES ARE CANONICALIZED, and that is the whole fix. `_rec_sources()` returns
        `_canon_source(doc)` -- `"crucible/claim-17"` becomes `"crucible"`, `"https://x.com/a/b"` becomes
        `"x"` -- while this compared the RAW warrant against that canonical set. The two normalizations
        never met, so the one concrete protection the docstring names ("not the record's own source") was
        dead for every realistic source: any path, URL, or `arxiv:`-style id slipped straight through and a
        record could vouch for ITSELF. Only a single-token source like `"plainword"` ever matched.
        Measured 2026-08-09: source `{"doc": "crucible/claim-17"}` credited with
        `warrant="crucible/claim-17"` earned `good_warranted=1.0` -- the exact MINJA self-grade that
        `credit_requires_warrant` exists to refuse.

        Comparing canonical-to-canonical is deliberately the CONSERVATIVE direction: a warrant that
        canonicalizes onto the record's own source is now refused even when the raw strings differ. For a
        guard, a false refusal costs a credit; a false acceptance costs the guarantee.
        """
        if not warrant:
            return False
        w = str(warrant).strip().lower()
        if not w:
            return False
        own = {str(s).strip().lower() for s in Inspeximus._rec_sources(rec) if s}
        own.discard("")
        w_canon = str(Inspeximus._canon_source(w) or "").strip().lower()
        if w in own or (w_canon and w_canon in own):
            return False
        auth = getattr(self, "warrant_authorities", None)
        if auth is not None and w not in {str(a).strip().lower() for a in auth}:
            return False              # forged string that names no declared trusted channel does not count
        return True

    def propagate_outcome(self, outcome, ids=None, weight: float = 1.0,
                          driving_only: bool = True) -> dict:
        """CLOSE THE RETRIEVAL LOOP automatically: when the action the LAST recall informed gets a verdict,
        credit the memories that DROVE it — so a retrieved-and-acted-on memory earns its outcome signal
        without the caller hand-threading ids into credit(). This raises the earned-outcome COVERAGE of
        memory, which we measured to be the binding constraint (retrieval->earned conversion ~28% on a live
        store; the rest of the recalled set never converts to a gradable outcome — the attribution gap, not
        a fundamental ceiling; inspeximus/probes/retrieval_exposure_coverage_probe.py + the outcome-propagation
        lift measured in inspeximus/probes/outcome_propagation_probe.py).

        `ids` defaults to the last recall set (self._last_recall). `driving_only=True` (default) restricts
        the credited set to the DECISION-DRIVING subset: pass the specific id(s) the action actually used
        (the app knows which memory it acted on), or, if ids is None, inspeximus credits only the recall set's
        CORROBORATED members (the same bar as recall(influence_only=True)) — so a poison that merely rode
        into the recall set as soft context cannot earn credit for an honest action's success (the recall-
        set-attribution poison surface). LOAD-BEARING LIMIT (not hidden): driving_only=True with ids=None
        has a COLD-START — a fresh legit memory that is not yet corroborated earns nothing, so first-use
        credit needs the app to name the driver explicitly (pass ids). driving_only=False credits the whole
        recall set (max conversion, but forgeable — only for trusted ingestion). Poison-safety of the
        explicit-driver path is exactly that of the recall that selected the driver: use recall(...,
        influence_only=True) for high-stakes so a hijack poison is never the driver in the first place."""
        if ids is None:
            ids = list(getattr(self, "_last_recall", []) or [])
            if driving_only:
                by_id = {x["id"]: x for x in self.items}
                ids = [i for i in ids if (i in by_id and self._corroborated(by_id[i], by_id))]
        else:
            ids = [ids] if isinstance(ids, str) else list(ids)
        r = self.credit(ids, outcome, weight=weight)
        r["propagated"] = len(ids)
        return r

    # ── evidence-grade RATCHET (OPT-IN) ───────────────────────────────────────
    # Two axes a claim can NEVER self-assign at write time; each moves UP only on an EXTERNAL event:
    #   confidence: claimed -> corroborated -> verified -> settled
    #   novelty:    known   -> novel   (only if an external prior-art search came back EMPTY)
    # This operationalizes the finding that autonomous pipelines OVER-LABEL: the generator sets the cheap
    # default (claimed / known) for free; every upgrade has a defined price paid by a party OTHER than the
    # writer -- an independent witness, a reproduction, a distinct verified key, an empty prior-art search.
    # The generator cannot move its own claim up; grade() is a pure function of ratifications + the existing
    # corroboration/credit substrate, so there is nothing to spoof. Honest limit: distinct `by_key` is an
    # IDENTITY count (Douceur cost), spoofable unless paired with attestation (verified keys) exactly as
    # strict_corroboration is; and a ratifier can be WRONG -- this bounds who may upgrade a label, not truth.
    _GRADES = ("claimed", "corroborated", "verified", "settled")
    _RATIFY_KINDS = ("independent_witness", "reproduction", "prior_art_empty", "audit")

    def ratify(self, id: str, kind: str, by_key: str, lens: str | None = None, note: str | None = None) -> dict:
        """Record an EXTERNAL ratification of a claim. `kind` in _RATIFY_KINDS; `by_key` is the ratifier's
        identity (a source id or, better, a verified pubkey) and MUST differ from the claim's own attested
        key/source -- self-ratification is rejected (the whole point of the ratchet). Duplicate (by_key, kind,
        lens) does not stack, so a correlated/repeat auditor adds nothing. Returns {ok, grade, novel, reason}."""
        if kind not in Inspeximus._RATIFY_KINDS:
            raise ValueError(f"kind must be one of {Inspeximus._RATIFY_KINDS}")
        by_id = {x["id"]: x for x in self.items}
        rec = by_id.get(id)
        if rec is None:
            return {"ok": False, "reason": "no such id"}
        author = {rec.get("attested_key")}
        src = rec.get("source") or {}
        author.add(src.get("doc") if isinstance(src, dict) else src)
        if by_key in author:
            return {"ok": False, "reason": "self-ratification rejected (by_key is the claim's own author)"}
        rats = rec.setdefault("ratifications", [])
        if any(r.get("by_key") == by_key and r.get("kind") == kind and r.get("lens") == lens for r in rats):
            g = self.grade(rec)
            return {"ok": False, "reason": "duplicate (by_key, kind, lens) -- does not stack",
                    "grade": g["grade"], "novel": g["novel"]}
        rats.append({"kind": kind, "by_key": by_key, "lens": lens, "note": note, "ts": time.time()})
        self._save(force=True)
        g = self.grade(rec)
        return {"ok": True, "grade": g["grade"], "novel": g["novel"], "reason": f"{kind} recorded"}

    def grade(self, target, strict: bool | None = None, _by_id: dict | None = None) -> dict:
        """Compute a claim's CURRENT evidence grade + novelty from external ratifications and the existing
        corroboration/credit substrate. Pure/read-only; nothing here is settable by the writer. Returns
        {grade, novel, evidence}. `strict` (defaults to self.strict_corroboration) selects distinct verified
        keys vs distinct source strings for the multi-source corroboration path. `_by_id` is an optional cached
        id->record map (a caller grading many records can pass it to skip the per-call rebuild)."""
        by_id = _by_id if _by_id is not None else {x["id"]: x for x in self.items}
        rec = target if isinstance(target, dict) else by_id.get(target)
        if rec is None:
            return {"grade": None, "novel": None, "evidence": {"reason": "no such id"}}
        strict = self.strict_corroboration if strict is None else strict
        good = float(rec.get("good", 0) or 0); bad = float(rec.get("bad", 0) or 0)
        rats = rec.get("ratifications", []) or []
        # distinct EXTERNAL ratifiers per kind, and distinct lenses (correlated-auditor guard)
        def keys(kind):
            return {r.get("by_key") for r in rats if r.get("kind") == kind}
        repro = keys("reproduction"); witness = keys("independent_witness")
        prior_empty = keys("prior_art_empty")
        lenses = {r.get("lens") for r in rats if r.get("kind") in ("reproduction", "audit") and r.get("lens")}
        multi = (Inspeximus._distinct_verified_keys(rec.get("links"), by_id) >= 2) if strict \
            else (Inspeximus._distinct_sources(rec.get("links"), by_id) >= 2)
        earned = good > 0 and good >= bad
        attested = bool(rec.get("attested_key"))
        corroborated = multi or bool(witness) or bool(repro) or earned
        verified = bool(repro) or (attested and corroborated)
        settled = verified and earned and len(repro) >= 1 and len(lenses) >= 2   # diverse, reproduced, track record
        g = "settled" if settled else ("verified" if verified else ("corroborated" if corroborated else "claimed"))
        # novelty is a SEPARATE axis, and can ONLY be earned by an external empty prior-art search
        # (never self-assertable); a discredited claim (bad>good) forfeits novel standing.
        novel = bool(prior_empty) and good >= bad
        return {"grade": g, "novel": novel, "evidence": {
            "multi_source": multi, "attested": attested, "earned_outcome": earned,
            "reproductions": len(repro), "witnesses": len(witness),
            "prior_art_empty": bool(prior_empty), "distinct_lenses": len(lenses)}}

    def convergence_report(self, target, _by_id: dict | None = None) -> dict:
        """Read-only: distinguish CONVERGENCE-BACKED (independent sources agree) from ADJUDICATED (an out-of-band
        check with a DIFFERENT failure mode confirmed it). Corroboration measures independence of ORIGIN, never
        correctness -- so genuinely independent sources can converge on a FALSE claim ("authenticated-but-false")
        and nothing in the record content catches it. This surfaces the honest status so a consumer never reads
        convergence as truth, and flags LOW SOURCE DIVERSITY (uniform agreement from few distinct origins should
        RAISE suspicion, not confidence -- errors are correlated when checks share a substrate). Adjudication
        belongs above this layer, through an ORTHOGONAL check: ratify(kind='reproduction'|'audit') from an
        identity that is NOT the claim's own author -- only that lifts corroborated -> verified. Redundancy
        recovers a wrong consensus only to the degree the checks' failure modes are independent (a known result:
        Knight & Leveson 1986 on N-version programming; Condorcet/Ladha 1992 on correlated votes; Campbell &
        Fiske 1959 on shared-method variance). Returns {status, grade, distinct_sources, corroborating_links,
        low_source_diversity, adjudicated, notes}. Nothing here is settable by the writer. `_by_id` is an
        optional cached id->record map (recall passes it when surfacing status for many results)."""
        by_id = _by_id if _by_id is not None else {x["id"]: x for x in self.items}
        rec = target if isinstance(target, dict) else by_id.get(target)
        if rec is None:
            return {"status": None, "reason": "no such id"}
        g = self.grade(rec, _by_id=by_id)
        ev = g["evidence"]
        links = [l for l in (rec.get("links") or []) if l in by_id]
        n_src = Inspeximus._distinct_sources(rec.get("links"), by_id)
        n_keys = Inspeximus._distinct_verified_keys(rec.get("links"), by_id)
        adjudicated = ev["reproductions"] > 0 or (ev["attested"] and (ev["multi_source"] or ev["witnesses"] > 0))
        convergence_only = (ev["multi_source"] or ev["witnesses"] > 0) and not adjudicated
        low_diversity = len(links) >= 2 and n_src <= 1
        if g["grade"] in ("verified", "settled"):
            status = "adjudicated"          # an out-of-band check (different failure mode) confirmed it
        elif convergence_only:
            status = "convergence-backed"   # sources agree; NOT established true -- do not promote to true
        else:
            status = g["grade"]             # claimed, or corroborated via an earned outcome only
        notes = []
        if convergence_only:
            notes.append("convergence-backed: independent sources agree, but this is NOT adjudicated true; "
                         "route to an ORTHOGONAL out-of-band check (ratify kind='reproduction'/'audit') before "
                         "relying on it -- corroboration cannot see an authenticated-but-false claim")
        if low_diversity:
            notes.append("low source diversity: >=2 corroborating links resolve to <=1 distinct origin -- "
                         "correlated-origin agreement; raise suspicion, do not read as stronger corroboration")
        if adjudicated:
            notes.append("adjudicated: confirmed by an out-of-band check with a different failure mode")
        # lineage cap: a DERIVED memory (a summary/consolidation) is only as adjudicated as its WEAKEST parent
        # -- a chain is as independent as its least-independent link, so trust taint propagates, not just source
        # taint. grade() is non-recursive, so grading parents here cannot loop. One level (immediate parents).
        parents = [p for p in (rec.get("derived_from") or []) if p in by_id]
        lineage_grade = g["grade"]
        if parents:
            rank = {gr: i for i, gr in enumerate(Inspeximus._GRADES)}
            par_grades = [self.grade(by_id[p], _by_id=by_id)["grade"] for p in parents]
            lineage_grade = min([g["grade"]] + par_grades, key=lambda gr: rank.get(gr, 0))
            if rank.get(lineage_grade, 0) < rank.get(g["grade"], 0):
                notes.append("lineage-capped: derived from a weaker input (grade '%s') -- a derived memory is "
                             "only as adjudicated as its least-adjudicated parent" % lineage_grade)
        return {"status": status, "grade": g["grade"], "lineage_grade": lineage_grade,
                "distinct_sources": n_src, "distinct_verified_keys": n_keys, "corroborating_links": len(links),
                "low_source_diversity": low_diversity, "adjudicated": adjudicated,
                "notes": notes or ["no corroboration yet (claimed)"]}

    def slash(self, ids, scope: str = "source", allow_ambiguous: bool = False,
              reason: str = "") -> dict:
        """Retroactive standing forfeiture — the accountability lever for a CAUGHT poison. When a memory is
        caught driving a bad outcome (the application detects/attributes it), slash() FORFEITS the entire
        accrued outcome-standing of its SOURCE (scope='source', default — every active memory sharing that
        canonical source) or just the named memories (scope='memory'). A patient 'sleeper' that banked good
        credit over many benign memories under one identity loses ALL of it on one catch, so its accrued
        reputation IS the bond and its patience becomes its largest exposed stake.
        WHY this and not credit(bad): credit() is append-only, so a net-positive sleeper survives one bad
        (good=50, bad=1 stays trusted). slash() zeroes `good`, books a dominating `bad`, AND revokes any
        episodic->semantic graduation, so the source goes net-negative and immediately FAILS the corroboration
        / influence gate (recall(influence_only=True) and episodic->semantic graduation). WHY not forget():
        forget() deletes; slash() KEEPS the records for audit and only strips their standing — they can still be
        recalled for context, just not trusted to drive an action. This makes cost-of-corruption scale with the
        accrued standing + detection (Becker expected-penalty: the penalty must beat gain / P(caught)), which is
        the lever that bites a time-rich patient attacker a per-action cap only lets him amortize. MEASURED
        motivation: inspeximus/probes/triad_attacker_split.py + reversibility_gate_frontier.py (the residual against a
        patient sleeper is a slow-cumulative in-domain attack; retroactive forfeiture, not a throughput cap, is
        the dominant control). Returns {slashed, sources, ids}. Records + raw text untouched; only good/bad/mtype
        change, auditable via meta['slashed']. Reversible: nothing is deleted."""
        by_id = {x["id"]: x for x in self.items}
        caught = [by_id[i] for i in _as_ids(ids) if i in by_id]
        if scope == "source":
            bad_sources = set().union(*(Inspeximus._rec_sources(r) for r in caught)) if caught else set()
            # a record is caught if its own source OR any inherited taint intersects the slashed sources ->
            # forfeiting a source also burns every derived summary/consolidation it fed (provenance-carried).
            _rows = [r for r in self._tenant_rows() if r.get("status") == "active"]
            targets = [r for r in _rows if (Inspeximus._rec_sources(r) & bad_sources)]
            # DEPENDENCY-DIRECTED RETRACTION, walked at SLASH time (Doyle, A Truth Maintenance System,
            # AIJ 12(3) 1979: retract by propagating through justification edges; Biba 1975 low-water-mark
            # for the integrity direction). Until now this was a set intersection against `taint`, which is
            # computed ONCE at write time and never revisited -- so a descendant whose parent's provenance
            # arrived later, or whose taint was written before the parent had a source, kept full standing
            # through a retraction it declared its dependence on. `forget_subject` already closed forward
            # over `derived_from`; the accountability lever did not, and the two disagreed about who a
            # retraction reaches. They now walk the same edges.
            # Cost is one pass per newly-reached generation, bounded by the number of active records.
            _ids = {r["id"] for r in targets}
            while True:
                _next = [r for r in _rows if r["id"] not in _ids
                         and (set(r.get("derived_from") or []) & _ids)]
                if not _next:
                    break
                targets.extend(_next)
                _ids |= {r["id"] for r in _next}
            _coll = self._source_expansion_collisions(caught, targets)
            if _coll and not allow_ambiguous:
                raise Inspeximus._ambiguous_error(
                    ", ".join(sorted({self._raw_source(r) for r in caught if self._raw_source(r)})),
                    _coll, "slash(scope='source')")
            sources = sorted(bad_sources)
        elif scope == "lineage":
            # NAMED RECORDS PLUS EVERYTHING TRANSITIVELY DERIVED FROM THEM, and nothing else. The case
            # scope='memory' cannot serve and scope='source' over-serves: you caught ONE memory driving a
            # bad outcome and want the conclusions built on it to lose standing too, without forfeiting
            # every other memory that happens to share its source. Doyle's dependency-directed retraction
            # with the justification set given explicitly instead of inferred from a source label.
            _rows = [r for r in self._tenant_rows() if r.get("status") == "active"]
            targets = list(caught)
            _ids = {r["id"] for r in targets}
            while True:
                _next = [r for r in _rows if r["id"] not in _ids
                         and (set(r.get("derived_from") or []) & _ids)]
                if not _next:
                    break
                targets.extend(_next)
                _ids |= {r["id"] for r in _next}
            sources = []
        else:                                    # scope='memory' — only the named records
            targets, sources = caught, []
        slashed = []
        for r in targets:
            meta = r.setdefault("meta", {})
            if not meta.get("slashed"):          # record pre-slash state ONCE (for audit + restore); don't
                meta["pre_slash"] = {"good": float(r.get("good", 0) or 0),   # clobber it on a double-slash
                                     "bad": float(r.get("bad", 0) or 0), "mtype": r.get("mtype", "episodic")}
            g = float(r.get("good", 0) or 0); b = float(r.get("bad", 0) or 0)
            r["good"] = 0.0
            r["bad"] = g + b + 1.0               # dominating -> net-negative -> blocked by the influence gate
            _mtype_changed = False
            if r.get("mtype") == "semantic":
                r["mtype"] = "episodic"          # revoke graduation, else it still passes _is_corroborated
                _mtype_changed = True
            meta["slashed"] = True
            slashed.append(r["id"])
            if _mtype_changed and self.receipts_enabled:
                # AMEND THE CHAIN. `mtype` is a committed field, so revoking graduation made verify_writes()
                # report "stored content no longer matches its write receipt (edited after write)" — a
                # tamper alarm raised by our own accountability lever. Measured: it fired in 27 of 45 random
                # operation sequences, first at op 3. The code already called this mutation legitimate in a
                # comment; the honest follow-through is to RECORD it, not to stop committing to mtype.
                # Append-only, so the amendment is itself evidence of when standing was revoked.
                self._emit_write_receipt(r, amends=("mtype",), reason=reason)
        if slashed:
            # DURABLE, NOT THROTTLED. `_save()` batches writes on a 5s timer, which is right for a
            # remember() in a hot loop and wrong for the one operation whose whole purpose is to take
            # standing AWAY. Measured 2026-07-31: slash() a credited record, lose the process before
            # the timer fires, reopen -- meta.slashed and the zeroed good/bad are GONE and the record
            # is load-bearing again. A retraction that a crash can undo is not a retraction, and the
            # failure is silent on both sides: the caller got {"slashed": 1} and believed it.
            # Found by an adversarial reviewer of a reply that was about to assert the opposite.
            self._save(force=True)
        return {"slashed": len(slashed), "sources": sources, "ids": slashed}

    def restore(self, ids, scope: str = "source", allow_ambiguous: bool = False,
                reason: str = "") -> dict:
        """Undo a slash() — the safety valve. Detection is imperfect (a self-graded / MINJA-style oracle can be
        tricked into flagging a LEGIT source, so slash() can be WEAPONISED to knock out a rival's memory), so a
        forfeiture must be reversible. When a slashed source is exonerated, restore() recovers its EXACT
        pre-slash standing from meta['pre_slash'] (good/bad/graduation) — or, if none was recorded, a clean
        slate (good=0, bad=0) so it must re-earn rather than snapping back to trusted. scope='source' restores
        every active memory sharing the caught record's canonical source; scope='memory' only the named records.
        Only records currently marked meta['slashed'] are touched. Returns {restored, sources, ids}. This is the
        deliberate cost of the retroactive lever: because the penalty is heavy (whole accrued standing), the
        appeal has to be cheap — otherwise slash() itself becomes the attack surface."""
        by_id = {x["id"]: x for x in self.items}
        seed = [by_id[i] for i in _as_ids(ids) if i in by_id]
        if scope == "source":
            srcs = set().union(*(Inspeximus._rec_sources(r) for r in seed)) if seed else set()
            _slashed_rows = [r for r in self.items if (r.get("meta") or {}).get("slashed")]
            targets = [r for r in _slashed_rows if (Inspeximus._rec_sources(r) & srcs)]
            # THE SAME EDGES, IN THE OTHER DIRECTION. slash() now walks `derived_from` forward, so restore()
            # must walk it too or the appeal is narrower than the penalty: a descendant reached only by the
            # lineage walk would stay forfeit forever, with no operation able to clear it. An exoneration
            # that cannot reach everything the forfeiture reached is not an exoneration.
            _ids = {r["id"] for r in targets}
            while True:
                _next = [r for r in _slashed_rows if r["id"] not in _ids
                         and (set(r.get("derived_from") or []) & _ids)]
                if not _next:
                    break
                targets.extend(_next)
                _ids |= {r["id"] for r in _next}
            _coll = self._source_expansion_collisions(seed, targets)
            if _coll and not allow_ambiguous:
                # Exonerating A silently exonerated B: measured, restore([alice], scope='source') cleared a
                # slash B had earned on his OWN catch. The mirror of the slash() collision, and worse, because
                # it RE-ADMITS a source that was correctly forfeited.
                raise Inspeximus._ambiguous_error(
                    ", ".join(sorted({self._raw_source(r) for r in seed if self._raw_source(r)})),
                    _coll, "restore(scope='source')")
            sources = sorted(srcs)
        elif scope == "lineage":
            # mirrors slash(scope='lineage') exactly, or the appeal is narrower than the penalty
            _slashed_rows = [r for r in self.items if (r.get("meta") or {}).get("slashed")]
            targets = [r for r in seed if (r.get("meta") or {}).get("slashed")]
            _ids = {r["id"] for r in targets}
            while True:
                _next = [r for r in _slashed_rows if r["id"] not in _ids
                         and (set(r.get("derived_from") or []) & _ids)]
                if not _next:
                    break
                targets.extend(_next)
                _ids |= {r["id"] for r in _next}
            sources = []
        else:
            targets, sources = [r for r in seed if (r.get("meta") or {}).get("slashed")], []
        restored = []
        for r in targets:
            meta = r.get("meta") or {}
            prev = meta.pop("pre_slash", None)
            _mtype_before = r.get("mtype")
            if prev:                              # recover exact pre-slash standing
                r["good"] = float(prev.get("good", 0) or 0)
                r["bad"] = float(prev.get("bad", 0) or 0)
                r["mtype"] = prev.get("mtype", r.get("mtype", "episodic"))
            else:                                 # no record -> clean slate (must re-earn, don't snap to trusted)
                r["good"] = 0.0; r["bad"] = 0.0
            meta["slashed"] = False
            restored.append(r["id"])
            if r.get("mtype") != _mtype_before and self.receipts_enabled:
                # Same amendment as slash(): restoring graduation rewrites a COMMITTED field, so without a
                # new receipt the chain still asserts the slashed mtype and verify_writes() reports tampering
                # after a legitimate exoneration. slash and restore must be symmetric here.
                self._emit_write_receipt(r, amends=("mtype",), reason=reason)
        if restored:
            # Durable for the same reason slash() is: a restore the process can lose leaves the
            # record slashed on disk while every in-memory reader believes it was rehabilitated.
            # The safety valve has to be at least as durable as the lever it undoes.
            self._save(force=True)
        return {"restored": len(restored), "sources": sources, "ids": restored}

    def _cusum_state(self) -> dict:
        """Per-source CUSUM statistics, lazily loaded from a side file (like write receipts) so a patient
        attacker can't reset the detector by spanning sessions. In-memory-only when the store has no path."""
        if getattr(self, "_cusum", None) is None:
            self._cusum = {}
            if self.path:
                try:
                    self._cusum = json.loads((self.path.with_name(self.path.name + ".cusum.json"))
                                             .read_text(encoding="utf-8"))
                except Exception:
                    self._cusum = {}
        # Tenant-scoped: a bound handle must not read or spend another tenant's bucket.
        return self._cusum if self.tenant is None else _TenantBucket(self._cusum, self.tenant)

    def _save_cusum(self):
        if self.path:
            try:
                (self.path.with_name(self.path.name + ".cusum.json")).write_text(
                    json.dumps(self._cusum, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                self._sidecar_errors['cusum'] = f"{type(e).__name__}: {e}"

    def monitor(self, ids, outcome, k: float = 0.3, h: float = 3.0,
                auto_slash: bool = False, weight: float = 1.0,
                allow_ambiguous: bool = False) -> dict:
        """Per-SOURCE cumulative (CUSUM-type) poison DETECTOR — raises a case on a source whose cumulative
        bad-rate breaches a budget; you (or a human) then decide whether to slash(). This is the cumulative
        trigger the retroactive slash needs: slash can't fire per-slice (per-slice P(detected)~=0, and the
        deterrence bond scales with 1/P(detected), so the penalty blows up on the slow salami attack), so the
        signal has to be cumulative. On each attributed outcome, a one-sided CUSUM: S = max(0, S + weight*(bad -
        k)); alarm at S >= h. Attribution rides the derived_from taint (a bad outcome on a summary charges ALL
        its inherited sources), so slices later summarized still accumulate against their origin — the per-source
        budget and the slash are the same plumbing on the provenance substrate.
        Drop-in for credit() (also records the per-memory good/bad standing): monitor(recalled_ids, outcome).

        THREE honest limits (from a full adversarial review — do not overclaim this 'solves' poisoning):
        1. NOT exact-optimal: (x - k) is the Gaussian-mean-shift CUSUM; the exactly-optimal statistic for a
           Bernoulli bad-indicator increments by the log-likelihood ratio. This is a CUSUM-TYPE detector.
        2. k IS A TOLERATED-RATE PRICE, NOT A WALL: an attacker who holds its per-source bad-rate at/below k
           produces non-positive drift -> S -> 0 -> PROVABLY undetectable (the ARL<->detectability duality). So
           this catches the careless/impatient poisoner; a patient one nets a bounded k x exposure residual —
           the detection-latency floor moved to k, not closed. Lowering k to chase it just raises false alarms
           on honest sources.
        3. DO NOT AUTO-FIRE THE IRREVERSIBLE PENALTY. auto_slash DEFAULTS OFF for a reason: 70 years of
           auto-penalty systems (SPC -> fraud -> content moderation) converged on automatic DETECTION + a
           human-reviewable, REVERSIBLE penalty, never auto-fired forfeiture — because a drifting base-rate
           guarantees false alarms, and with taint a single false positive nukes a whole downstream tree
           (guilt-by-linkage), and if outcomes are attacker-influenceable (MINJA) the trigger becomes a framing
           weapon (feed bad outcomes attributed to a rival -> auto-slash the rival; cf. RepTrap / bad-mouthing).
           Recommended: on alarm, cap/freeze the source's forward influence (reversible) and queue a HUMAN
           review; confirm the slash by hand; keep restore() one call away.

        Tuning: `k` in (0,1) is the bad-rate you contractually tolerate (drift reference); `h` sets the
        false-alarm rate (ARL ~ exp(h)) and the detection delay ~ h/(true_rate - k) — the Lorden floor.
        `auto_slash=True` is an explicit opt-in for a high-integrity, un-self-gradable oracle only. State
        persists to a side file (cross-session). Returns {alarms, slashed, cusum}. Undo a false alarm with
        restore()."""
        self.credit(ids, outcome, weight)                    # standing accrues normally...
        bad = 0.0 if Inspeximus._outcome_good(outcome) else 1.0
        by_id = {x["id"]: x for x in self.items}
        recs = [by_id[i] for i in _as_ids(ids) if i in by_id]
        srcs = set().union(*(Inspeximus._rec_sources(r) for r in recs)) if recs else set()
        _peers = [r for r in self._tenant_rows() if Inspeximus._rec_sources(r) & srcs]
        _coll = self._source_expansion_collisions(recs, _peers)
        if _coll and not allow_ambiguous:
            # The CUSUM bucket IS the canonical source, so two subjects under one host share one detector:
            # 20 bad outcomes on Alice put Bob one call from an alarm he never earned.
            raise Inspeximus._ambiguous_error(
                ", ".join(sorted({self._raw_source(r) for r in recs if self._raw_source(r)})),
                _coll, "monitor()")
        S = self._cusum_state()
        alarms = []
        for s in srcs:
            S[s] = max(0.0, float(S.get(s, 0.0)) + float(weight) * (bad - k))
            if S[s] >= h:
                alarms.append(s)
        slashed = {}
        if auto_slash and alarms:
            for s in alarms:
                rep = next((r["id"] for r in self.items
                            if r.get("status") == "active" and s in Inspeximus._rec_sources(r)), None)
                if rep:
                    slashed[s] = self.slash([rep], scope="source")["slashed"]
                S[s] = 0.0                                    # reset the breached statistic after firing
        self._save_cusum()
        return {"alarms": alarms, "slashed": slashed, "cusum": {k2: round(v, 3) for k2, v in S.items()}}

    def _budget_state(self) -> dict:
        """Per-source CUMULATIVE irreversible-influence spend, lazily loaded from a side file (like the CUSUM
        state) so the lifetime budget survives restarts — a patient attacker must not reset its spent budget by
        spanning sessions. In-memory-only when the store has no path."""
        if getattr(self, "_irrev", None) is None:
            self._irrev = {}
            if self.path:
                _bp = self.path.with_name(self.path.name + ".irrev.json")
                try:
                    self._irrev = json.loads(_bp.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    pass                                     # no spend yet: an empty budget is correct
                except Exception as e:
                    # FAIL CLOSED. A corrupt sidecar used to reset the state to {}, so a 0.9 spend against a
                    # 1.0 budget was allowed a SECOND time (cumulative 1.8) — the exact "reset by spanning
                    # sessions" the docstring says must not happen, reachable by corrupting one file. If we
                    # cannot read how much was spent, we must not authorise more.
                    # A READ failure, not a write one. Recording it in _sidecar_errors made
                    # flush() raise OSError "could not persist" on a store that had persisted
                    # perfectly — and under the CLI's persistence check, exit 3 NOT PERSISTED.
                    # Reporting the wrong failure is its own defect.
                    self._read_errors["irrev"] = f"{_bp}: unreadable ({type(e).__name__}: {e})"
                    self._irrev = None
                    raise RuntimeError(
                        f"the irreversible-influence budget at {_bp} is unreadable ({e}). Refusing to "
                        f"authorise a spend against an unknown balance — restore or delete the file "
                        f"deliberately if you intend to reset it.") from None
                except BaseException:
                    self._irrev = {}
        # Tenant-scoped: a bound handle must not read or spend another tenant's bucket.
        return self._irrev if self.tenant is None else _TenantBucket(self._irrev, self.tenant)

    def _save_budget(self):
        if self.path:
            try:
                (self.path.with_name(self.path.name + ".irrev.json")).write_text(
                    json.dumps(self._irrev, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                self._sidecar_errors['irrev'] = f"{type(e).__name__}: {e}"

    def spend_irreversible(self, ids, amount: float = 1.0, budget: float = 1.0,
                           allow_ambiguous: bool = False,
                           provenance_lo: float | None = None, require_earned: bool = False,
                           tool=None, contained: bool | None = None) -> dict:
        """Per-source LIFETIME budget on IRREVERSIBLE influence — the integral cap that bounds the one residual
        the rate-detector (monitor) provably CANNOT: the strictly-below-k patient attacker. monitor()'s k is a
        tolerated RATE, so an attacker holding bad-rate BELOW k gives the CUSUM negative drift -> no detection
        power above the baseline false-alarm rate, netting k x exposure — and exposure is PER-WINDOW, so over an
        unbounded horizon its TOTAL irreversible blast grows without bound. Capping a rate never bounds the
        integral. This caps the integral directly: each irreversible (hard-to-undo) action an application is about
        to take is metered against the LIFETIME budget of every source that influenced it (own source OR inherited
        derived_from taint). Once a source has spent its budget, further irreversible actions carrying its influence
        are DENIED — so THAT source's total irreversible damage is bounded by the budget, a finite number YOU
        choose, not a rate you tolerate forever. This bounds a KNOWN source only: a Sybil/whitewashing attacker
        gets a fresh budget per identity, so the identity count (bounded by a DIFFERENT axis — a capability ceiling
        or identity cost; Douceur, Friedman-Resnick) is what caps the total, not this — the budget relocates the
        residual to the identity axis, it does not close it.

        Call it as the gate on the IRREVERSIBLE TAIL ONLY: spend_irreversible(recalled_ids, amount=blast) returns
        {'allowed': bool}; iff allowed, the caller performs the irreversible action and the spend is booked
        (monotonic). REVERSIBLE influence is never metered — genuinely-good sources keep UNBOUNDED reversible
        influence and only their irreversible tail is finite (the everyone-pays shape of a hard capability ceiling,
        but charged only where the action is actually irreversible). The tightest contributing source binds: an
        action tainted by an exhausted source is blocked, exactly as slash() burns every derived summary a caught
        source fed.

        Orthogonal to standing: NOT refunded by restore() (an irreversible action cannot be un-done, so its
        integral is permanent) and does NOT read good/bad. It composes with the other axes as price(k, monitor) +
        BOUND(this lifetime budget) + a reversible human gate(slash/restore) on the irreversible tail — but this is
        NOT a closed decomposition: all three lean on a fourth, unbounded axis, SOUND ATTRIBUTION — the budget, the
        rate detector, and the gate all assume the app can correctly assign an action to its source and correctly
        label its irreversibility + blast `amount`; a misattributed or mislabeled action (or an attacker-influenced
        classifier) escapes all of them. Two more honest limits: refund-free forfeiture is itself a griefing vector
        (get bad irreversible actions attributed/taint-linked to a rival's source and you permanently exhaust its
        budget — RepTrap on the budget axis), and `amount` is caller-supplied so one action of size=budget exhausts
        a source in a single shot (k never enters the guarantee; the bound is the budget, per source). Prior art
        (textbook; the shipped plumbing is what's new): a total-budget-on-cumulative-cost is the differential-
        privacy privacy budget (a total epsilon caps cumulative leakage across queries under composition; Dwork &
        Roth 2014), an SRE error budget, a VaR / loss limit, and Sagas' compensable-vs-non-compensable split
        (Garcia-Molina & Salem 1987) — 'cap the integral, not the rate'. Persists to a side file (cross-session).
        Returns {allowed, exhausted, sources, spent}. Deliberately no cheap programmatic refund — raise a reviewed
        source's ceiling by calling with a higher budget or editing the side file by hand."""
        by_id = {x["id"]: x for x in self.items}
        recs = [by_id[i] for i in _as_ids(ids) if i in by_id]
        srcs = sorted(set().union(*(Inspeximus._rec_sources(r) for r in recs)) if recs else set())
        _peers = [r for r in self._tenant_rows() if Inspeximus._rec_sources(r) & set(srcs)]
        _coll = self._source_expansion_collisions(recs, _peers)
        if _coll and not allow_ambiguous:
            # One lifetime budget per canonical source: Alice spending it left Bob `allowed: False`.
            raise Inspeximus._ambiguous_error(
                ", ".join(sorted({self._raw_source(r) for r in recs if self._raw_source(r)})),
                _coll, "spend_irreversible()")
        B = self._budget_state()
        # UNIVERSAL-EXECUTOR gate (OPT-IN, 1.2.0; tool=None -> legacy path, byte-identical). If this irreversible
        # action routes through a verb-polymorphic universal executor (shell/eval/arbitrary-SQL/generic-HTTP), a
        # per-tool reversibility label is UNSOUND and the executor's external harm-reach is bounded only by
        # containment. So an UNCONTAINED universal executor is denied outright (reversibility undecidable +
        # unbounded external reach), regardless of budget -- the caller must sandbox it (contained=True) or route
        # the effect through a specific, signature-decidable tool. contained=True falls through to the normal
        # per-source budget check (the local-state residual is still metered). See is_universal_executor().
        if tool is not None and is_universal_executor(tool):
            if contained is not True:
                return {"allowed": False, "exhausted": [], "sources": srcs,
                        "spent": {s: round(float(B.get(s, 0.0)), 4) for s in srcs},
                        "universal_executor": True, "contained": bool(contained),
                        "reason": "universal_executor_uncontained: reversibility is undecidable from the tool "
                                  "signature and external harm-reach is unbounded without containment; pass "
                                  "contained=True only if the executor has no ambient network/credentials, or "
                                  "route through a specific tool."}
        # PROVENANCE-SCALED cap (OPT-IN, provenance_lo=None -> uniform legacy path, byte-identical): a source with
        # NO corroborated contributing record is capped at the small `provenance_lo` instead of `budget`, so a
        # LOW-PROVENANCE memory recalled into an irreversible action binds that action's budget against ITSELF
        # (not the honest actor). This scopes the hard floor to the consequential slice -- the thing that can
        # actually cash out -- rather than the whole store (jacksonxly's lever-1 refinement, r/RAG 2026-07).
        # HONEST: `provenance_lo` is a tunable policy knob, not a measured constant, and it still relocates to the
        # Sybil identity axis (a fresh low-provenance identity gets a fresh provenance_lo). See
        # inspeximus/probes/soft_influence_taint_probe.py.
        if provenance_lo is None:
            _cap = lambda s: float(budget)
        else:
            # require_earned=True narrows the full-budget grant from _corroborated (which by default accepts the
            # FORGEABLE >=2-distinct-source path) to EARNED OUTCOME only (good>0 and good>=bad — the one signal a
            # sybil cannot mint; a forged/attested >=2-witness sybil clears _corroborated but not this). Slashed/
            # orphan records never qualify. Cost: any source without earned outcome is capped at provenance_lo, so
            # this heavily throttles the (large) not-yet-earned legitimate slice -- opt-in for high-stakes only.
            def _grants_full(r):
                if (r.get("meta") or {}).get("slashed") or r.get("orphan"):
                    return False
                if require_earned:
                    g = float(r.get("good", 0) or 0); b = float(r.get("bad", 0) or 0)
                    return g > 0 and g >= b
                return self._corroborated(r, by_id)
            _full_srcs = set()
            for r in recs:
                if _grants_full(r):
                    _full_srcs |= Inspeximus._rec_sources(r)
            _cap = lambda s: float(budget) if s in _full_srcs else float(provenance_lo)
        # the tightest contributing source binds: deny if ANY contributing source would exceed its lifetime budget
        exhausted = [s for s in srcs if float(B.get(s, 0.0)) + float(amount) > _cap(s)]
        allowed = not exhausted
        if allowed:
            for s in srcs:
                B[s] = float(B.get(s, 0.0)) + float(amount)   # monotonic; never decremented
            self._save_budget()
        return {"allowed": allowed, "exhausted": exhausted, "sources": srcs,
                "spent": {s: round(float(B.get(s, 0.0)), 4) for s in srcs}}

    def irreversible_budget_report(self, budget: float = 1.0) -> dict:
        """Audit view of the per-source lifetime irreversible-influence budget (spend_irreversible): for every
        source that has spent anything, its cumulative spent / remaining / whether it is exhausted. Read-only."""
        B = self._budget_state()
        out = {}
        for s, v in sorted(B.items()):
            row = {"spent": round(float(v), 4),
                   "remaining": round(max(0.0, float(budget) - float(v)), 4),
                   "exhausted": float(v) >= float(budget)}
            # An UNBOUND (operator) handle reads the raw store-global dict, whose keys are now
            # "<tenant>\0<source>". Unpack that into a field rather than printing the separator: the
            # scoping fix must not turn the operator's audit view into mojibake, and "who spent it"
            # is exactly what the victim's own report could not tell them before.
            if self.tenant is None and "\u0000" in s:
                t, s = s.split("\u0000", 1)
                row["tenant"] = t
                s = f"{t}/{s}"
            out[s] = row
        return out

    def _eviction_value(self, r: dict, now: float) -> float:
        """`_effective_value`, but an INFERRED type does not keep its half-life.

        `mtype` picks the decay half-life (7 days episodic vs 3650 days procedural, a 521x spread)
        and eviction ranks by it, so a label GUESSED from caller text sets its own retention class.
        Measured 2026-08-15 with the arms interleaved and aged one day per round: adding the single
        word "always" to the attacker's own text moved two evictions off the attacker's records and
        onto the user's, at every scale (control 2/2, 4/4, 6/6, 8/8; attack 3/1, 5/3, 7/5, 9/7).

        EVICTION ONLY, and that boundary is the fix rather than a detail. The first version applied
        this inside `_effective_value` itself, which RANKING also uses -- so every inferred record
        decayed at one rate, a time gap stopped reordering anything, and a test whose whole job is to
        catch a vacuous tie assertion caught it. Inference is genuinely useful for ranking; it is
        only a privilege where it decides what gets DELETED and what survives forever.

        Tightening the regex is unwinnable: any keyword list over attacker-controlled text is
        bypassable, and an inaccurate label is not the defect. A guess conferring a privilege is.
        """
        if not r.get("mtype_inferred"):
            return self._effective_value(r, now)
        _r = dict(r)
        _r["mtype"] = "episodic"
        return self._effective_value(_r, now)

    def _effective_value(self, r: dict, now: float) -> float:
        """Recall weight = stored value decayed by time since last access, at the memory's TYPE
        half-life (episodic fades fast, semantic slow, procedural barely). Access resets the clock,
        so memories that keep being useful stay alive while stored-but-never-recalled ones fade.
        Reversible: raw value/text are untouched; only the effective ranking weight decays."""
        hl = _HALFLIFE_S.get(r.get("mtype", "episodic"), _HALFLIFE_S["episodic"])
        # Age is quantised to WHOLE SECONDS. The half-lives here are hours to days, so sub-second
        # resolution carries no meaning -- but it did carry noise: `now` and `last_access` are wall clocks,
        # so two runs of the same code produced decay factors differing at ~1e-10, which propagated into
        # the ranking score and rotated nominally tied records in 7% of runs. Sorting the BM25 terms fixed
        # the other half of that noise; this is the rest of it, and both had to go at the source because
        # the noise and the smallest MEANINGFUL score gap in a crowded store are the same size (5.7e-10),
        # so nothing downstream can separate them.
        age = float(int(max(0.0, now - r.get("last_access", r.get("ts", now)))))
        return r["value"] * (0.5 ** (age / hl))

    # ── consolidation (the "dream" pass) ──────────────────────────────────────
    def _common_vocab(self, active: list[dict], min_df_frac: float = 0.002):
        """Token sets per memory + the corpus's COMMON vocabulary (tokens shared by enough
        memories to be real content, not one-off noise). Cheap, O(total tokens)."""
        from collections import Counter
        df: Counter = Counter()
        toks = []
        for r in active:
            tk = _tokens(r["text"]); toks.append(tk); df.update(tk)
        min_df = max(3, int(min_df_frac * len(active)))
        common = {w for w, c in df.items() if c >= min_df}
        return toks, common

    def recall_iterative(self, query: str, ask_followup, k: int = 6, rounds: int = 1,
                         **recall_kw) -> list[dict]:
        """Multi-hop recall. One-shot top-k misses evidence reachable only via a BRIDGE entity (a fact whose
        detail lives in a memory NOT similar to the query). This does: retrieve -> let a capable model read the
        results and name what's missing, emitting follow-up queries -> retrieve again -> merge (dedup by id).
        `ask_followup(query, current_results) -> list[str]` is caller-supplied, so inspeximus stays model-agnostic
        (inject any model/LLM) and NO LLM enters the library — the reasoning is the caller's, the retrieval is ours.
        It is the one mechanism that moved the multi-hop bottleneck where static retrieval tricks
        (dense-neighbour, lexical bridges, PRF/Rocchio, multi-query RRF, cross-encoder rerank) did not.
        More expensive (a model call in the loop), so it's an explicit mode, not the default.

        THE MEASURED NUMBERS, AND WHY THERE ARE TWO. This docstring used to report "~3.3x (0.057 -> 0.186,
        n=70)" while our own benchmark notes reported "0.145 -> 0.297" for the same lever. They are two
        different FIXTURES reported under one claim, not a disagreement, and the operating point is the whole
        difference. Both are multi-hop FULL-EVIDENCE recall@50 on LoCoMo with a model reader in the loop
        (reader sees the question + round-1 hits only, never the gold), equal retrieval budget B=50:

            n=276, ALL 10 conversations (the full benchmark):  flat 0.145 -> iterative 0.297   = 2.05x
            n=70,  the 3 HARDEST conversations (a subset):     flat 0.057 -> iterative 0.186   = 3.3x

        The 3.3x is the harder subset and the flattering ratio; the full-benchmark 2.05x is the citable one,
        and it is what this docstring now leads with. NEITHER is reproduced by this repository's test suite:
        both need the LoCoMo corpus (not shipped) plus a model reader and local nomic-embed vectors. What IS
        reproduced here, on every run, is the two-phase MCP/CLI surface built on this method — see
        `recall_iterative_start` / `recall_iterative_followup` below and
        `probes/recall_iterative_surface_multihop.py`."""
        seen: dict = {}
        for r in self.recall(query, k=k, **recall_kw):
            seen[r["id"]] = r
        for _ in range(max(0, int(rounds))):
            try:
                followups = ask_followup(query, list(seen.values())) or []
            except Exception:
                followups = []
            for fq in followups:
                if not isinstance(fq, str) or not fq.strip():
                    continue
                for r in self.recall(fq, k=k, **recall_kw):
                    seen.setdefault(r["id"], r)
        # One logical retrieval, so ONE window: the union the caller receives, not the last hop that
        # happened to run. Each self.recall() above already overwrote the window with its own hits; this
        # restores it to what is actually being returned, under the ORIGINAL query.
        self._note_recall_window(list(seen), query)
        return list(seen.values())

    # ── the same lever, INVERTED for a surface that cannot take a callable ───────────────────────────────
    # recall_iterative() needs `ask_followup`, a Python callable. Over MCP there is no callable to pass, and
    # over a CLI there is no process to hold one -- which is why the one retrieval lever that measurably works
    # was reachable from the library and from nowhere a user or an agent actually stands. The inversion: the
    # MCP client IS a model, so hand the loop back to it as two stateless calls.
    #   start()    -> round-1 hits + the follow-up instruction + `prior_ids` (the continuation token)
    #   followup() -> the caller's model answers, we retrieve again and return only what is NEW
    # NO SERVER-SIDE SESSION. `prior_ids` is the entire continuation state and it travels with the caller, so
    # there is no session table to grow, expire, leak across tenants, or serve to the wrong client. It also
    # keeps the second call's payload to the NEW records only: the caller already holds round-1.
    MAX_FOLLOWUPS = 8          # hard ceiling on follow-ups honoured per call -- see the bound below

    def recall_iterative_start(self, query: str, k: int = 6, max_followups: int = 3,
                               **recall_kw) -> dict:
        """PHASE 1 of the client-driven multi-hop loop: retrieve round-1 and ask the CALLER's model what is
        missing. `ask` is the instruction to hand your model;
        `prior_ids` is the continuation token to hand back.

        Returns {k, max_followups, round, hits, prior_ids, ask, next_call, bounds}; the query is NOT echoed
        back (see the comment in the body).

        BOUND (asserted in tests/test_recall_iterative_surface.py): exactly ONE recall() call, and at most
        `k` records in the response. Both are independent of store size -- the payload is a function of `k`
        alone, never of n. That is the property the O(n^2)/150 MB surfaces in this codebase did not have."""
        k = max(1, int(k))
        mf = max(0, min(int(max_followups), Inspeximus.MAX_FOLLOWUPS))
        hits = self.recall(query, k=k, **recall_kw) or []
        # The QUERY IS NOT ECHOED BACK, here or in the follow-up result, and that is deliberate. The caller
        # sent it and already has it, so echoing buys nothing -- while a memory server that reflects caller
        # text into a model's context is an injection amplifier, and the tenant-isolation sweep (which fails
        # any method whose OUTPUT contains a string it was asked about) rightly cannot tell a reflected
        # argument from a retrieved record. It flagged both of these methods until the echo came out.
        return {
            "k": k, "max_followups": mf, "round": 1,
            "hits": hits,
            "prior_ids": [h.get("id") for h in hits],
            "ask": (
                f"Read these {len(hits)} memories against the question. If answering needs a fact that is NOT "
                f"here but is REACHABLE from something they name -- a person, system, ticket, place or date "
                f"that the memories mention and the question does not -- write up to {mf} short search "
                f"queries that would retrieve it, naming that bridge entity explicitly. If the memories "
                f"already contain everything needed, return NO follow-ups and do not make the second call."),
            "next_call": ("recall_followup(query=<same query>, prior_ids=<prior_ids from this result>, "
                          "followups=[<your queries>])"),
            "bounds": {"recall_calls": 1, "max_records": k,
                       "independent_of_store_size": True},
        }

    def recall_iterative_followup(self, query: str, followups: list | None = None,
                                  prior_ids: list | None = None, k: int = 6,
                                  max_followups: int = 3, **recall_kw) -> dict:
        """PHASE 2: the caller's model has read round-1 and named the bridge; retrieve on its follow-up
        queries and return ONLY the records the caller does not already hold. Returns {followups_used, followups_dropped,
        new_hits, bridged, merged_ids, recall_calls, bounds}.

        Call it again with the updated `merged_ids` as `prior_ids` for a further round -- rounds are the
        caller's loop, not server state.

        `prior_ids` is what makes `new_hits` mean "new". Omit it and every follow-up hit is reported as new,
        including records round 1 already returned; that is a caller error the result cannot detect, so it is
        stated here rather than papered over with a hidden extra round-1 recall (which would silently double
        the work bound).

        BOUND (asserted): at most min(len(followups), max_followups) recall() calls, with max_followups
        itself capped at Inspeximus.MAX_FOLLOWUPS=8; at most k * max_followups records in `new_hits`. Both
        depend only on (k, max_followups) -- never on store size."""
        k = max(1, int(k))
        mf = max(0, min(int(max_followups), Inspeximus.MAX_FOLLOWUPS))
        fq_all = [f.strip() for f in (followups or []) if isinstance(f, str) and f.strip()]
        used, dropped = fq_all[:mf], max(0, len(fq_all) - mf)
        merged = [i for i in (prior_ids or []) if isinstance(i, str)]
        seen = set(merged)
        new: list[dict] = []
        for fq in used:
            for r in (self.recall(fq, k=k, **recall_kw) or []):
                rid = r.get("id")
                if rid in seen:
                    continue
                seen.add(rid)
                merged.append(rid)
                new.append(r)
        # `merged` is round-1 plus everything the bridge added -- exactly what the caller now holds, and so
        # exactly the window a write after this call should carry. Without this the window would be the last
        # follow-up's hits alone, dropping the round-1 records the caller is most likely writing from.
        self._note_recall_window(merged, query)
        return {
            "followups_used": used, "followups_dropped": dropped,
            "new_hits": new, "bridged": len(new), "merged_ids": merged,
            "recall_calls": len(used),
            "bounds": {"recall_calls": len(used), "max_recall_calls": mf,
                       "max_new_records": k * mf, "independent_of_store_size": True},
        }

    # ── clean memory: write-admission gate + inspector (1.3.0) ────────────────────────────────────
    def admit(self, text: str, tags=None, value: float = 1.0, meta: dict | None = None,
              mtype: str | None = None, dup_threshold: float = 0.92, min_tokens: int = 2,
              quality: bool = True, **kw) -> dict:
        """WRITE-ADMISSION GATE — decide whether a candidate memory is worth storing BEFORE it bloats the store,
        then store it or point at the existing duplicate. Counters agent memory's #1 real-world failure:
        indiscriminate writes (audited mem0 stores measured ~98% junk, one line cloned 800+ times). Two checks,
        both opt-out:
          - quality: reject empty / too-short / obvious non-content (a refusal or "no sources ..." is not a memory).
          - dedup: if an ACTIVE memory is near-identical (similarity >= dup_threshold) with no value clash, do NOT
            append a copy; return that memory's id instead.
        A value UPDATE (same text, different number) is NOT a duplicate — it is admitted so consolidation can
        supersede the stale value. Returns {"admitted","id","reason","duplicate_of","similarity"}."""
        t = (text or "").strip()
        if quality:
            if not t:
                return {"admitted": False, "id": None, "reason": "empty", "duplicate_of": None, "similarity": None}
            if len(_tokens(t)) < min_tokens:
                return {"admitted": False, "id": None, "reason": "too_short", "duplicate_of": None,
                        "similarity": None}
            low = t.lower()
            if any(p in low for p in _NON_CONTENT):
                return {"admitted": False, "id": None, "reason": "non_content", "duplicate_of": None,
                        "similarity": None}
        hits = self.recall(t, k=1)
        if hits:
            h = hits[0]
            s = self._similarity(t, h, self._qvec(t) if self.embed else None)
            if s >= dup_threshold and not _value_clash(t, h["text"]):
                return {"admitted": False, "id": h["id"], "reason": "duplicate", "duplicate_of": h["id"],
                        "similarity": round(float(s), 4)}
        mid = self.remember(t, tags=tags, value=value, meta=meta, mtype=mtype, **kw)
        return {"admitted": True, "id": mid, "reason": "admitted", "duplicate_of": None, "similarity": None}

    def why_recalled(self, query: str, id: str | None = None, k: int = 12):
        """INSPECTOR — explain WHY memories rank for a query, so 'why did this surface / why not' stops being an
        archaeology dig. Returns the per-candidate score breakdown recall() actually ranks by: semantic (cosine),
        lexical (token overlap), effective_value (decayed rank weight), corroboration (good/bad), the stale-derived
        flag, and the memory's RANK in the live recall(). With `id`, returns just that record's breakdown plus
        whether it surfaced in the top-k. Read-only."""
        now = time.time()
        qvec = self._qvec(query) if self.embed else None
        qtok = _tokens(query)
        # reinforce=False, passed EXPLICITLY. The reason written here was wrong: it said
        # "recall() defaults reinforce=True", and the signature two thousand lines up says
        # `reinforce: bool = False`. Measured 2026-08-16 -- the default does not bump -- so this
        # call was never fixing a live defect. Keeping it explicit is still right: this method is
        # documented "Read-only", and an instrument must not rely on a default staying what it is
        # today to avoid changing the state it measures. What changes is the justification, which
        # asserted the opposite of the signature it was describing.
        ranked = self.recall(query, k=k, reinforce=False)
        rank_of = {r["id"]: i + 1 for i, r in enumerate(ranked)}
        _full = {x["id"]: x for x in self._tenant_rows()}          # recall() may return vec-less projections

        def _verdict(r):
            return Inspeximus._corroboration_verdict(
                r, _full, self.strict_corroboration,
                getattr(self, "credit_requires_warrant", False))

        def _passes(r):
            return self._corroborated(r, _full)

        def _why(r):
            return _verdict(r)[1]

        def _brk(rec):
            r = _full.get(rec["id"], rec)                 # resolve the full record so the vec is present
            sem = max(0.0, _cosine(qvec, r["vec"])) if (qvec is not None and r.get("vec")) else 0.0
            t = self._rec_tokens(r)
            lex = (len(qtok & t) / min(len(qtok), len(t))) if (qtok and t) else 0.0
            return {"id": r["id"], "text": (r.get("text") or "")[:80],
                    "semantic": round(float(sem), 4), "lexical": round(float(lex), 4),
                    "effective_value": round(self._effective_value(r, now), 4),
                    "good": float(r.get("good", 0) or 0), "bad": float(r.get("bad", 0) or 0),
                    "stale_derived": bool(r.get("_stale_derived")), "rank": rank_of.get(r["id"]),
                    # The gate's OWN verdict, not a re-derivation. `good`/`bad` above are inputs to
                    # it, not the test -- a caller reading them as corroboration gets it wrong when
                    # a record is slashed, orphaned, or unwarranted under credit_requires_warrant.
                    "gated_out": not _passes(r), "gate_reason": _why(r)}
        if id is not None:
            rec = next((r for r in self._tenant_rows() if r["id"] == id), None)
            if rec is None:
                return {"id": id, "found": False}
            b = _brk(rec); b["surfaced"] = rec["id"] in rank_of
            return b
        return [_brk(r) for r in ranked]

    def memory_report(self, dup_threshold: float = 0.9) -> dict:
        """INSPECTOR overview — 'what is in memory, and is it clean'. Counts active/superseded, by type,
        consolidated (linked), decayed (effective value < 10% of stored), and a near-duplicate REDUNDANCY estimate
        (active memories whose nearest active neighbour is >= dup_threshold, no value clash — sampled at 400 for
        cost). Read-only; the surface that proves a store did NOT accumulate 800 copies of one fact.

        COST, because "sampled at 400 for cost" says a bound was applied without saying what it costs, and
        this is an MCP tool an agent can call mid-conversation. The sample caps the number of QUERIES, not
        the work each one does: every one of the 400 is a full `recall`, which scores every active record.
        So the redundancy estimate is O(400 x n), and n is the whole store.

        MEASURED on this machine, no embedder (the lexical path), records of the shape
        "record N alpha beta gamma deploy salary" over 7 sources, median of 5 with the observed range:

            n=2,000    ~2 s    (1.81-2.34, spread 25%)
            n=8,000   ~12 s   (10.63-12.41, spread 15%)

        Two significant figures, deliberately: the run-to-run spread here is 15-25%, so a three-digit
        number would be a precision this measurement cannot support. Treat these as the order of
        magnitude on one machine, not a benchmark. The call COUNT is the part that does not vary --
        profiled at n=4,000, essentially the whole run is inside those 400 recalls (1.6M _lexsim calls),
        and tests/test_memory_report_cost_is_as_documented.py pins the count rather than the seconds.
        The other four figures (active, superseded, by_type, linked, decayed) are single passes and
        effectively free; if you want a cheap store summary, they are what you are paying for.

        This is a documented cost, not a defect to route around: the sampling is deliberate and seeded, and
        making the nearest-neighbour search sublinear needs an inverted index, which is an architectural
        change rather than a local one. Call it out of band on a large store."""
        now = time.time()
        # _content_rows(), not _tenant_rows(): a grant is an ACT, and counting the access-control
        # bookkeeping as memory would make "what is in memory, and is it clean" answer about the ACL.
        _rows = self._content_rows()
        act = [r for r in _rows if r.get("status") == "active"]
        sup = [r for r in _rows if r.get("status") == "superseded"]
        from collections import Counter
        by_type = dict(Counter(r.get("mtype", "episodic") for r in act))
        linked = sum(1 for r in act if r.get("links"))
        decayed = sum(1 for r in act if self._effective_value(r, now) < 0.1 * float(r.get("value", 1.0) or 1.0))
        redundant = 0
        # A RANDOM sample, seeded, not the first 400. `act[:400]` is the OLDEST 400 in insertion order and
        # duplication accumulates in the tail, so the same 1000-record store reported redundant_frac 1.0,
        # 0.245 or 0.99 depending only on the order the records went in -- spread 0.755, true value 0.600.
        # Seeded, because a number offered as evidence that a store did NOT accumulate copies of a fact
        # must not move between runs. Same cost as the slice; order-dependence drops to 0.008.
        sample = act if len(act) <= 400 else _random.Random(0).sample(act, 400)
        for r in sample:
            other = [h for h in self.recall(r["text"], k=2) if h["id"] != r["id"]]
            if other:
                s = self._similarity(r["text"], other[0], self._qvec(r["text"]) if self.embed else None)
                if s >= dup_threshold and not _value_clash(r["text"], other[0]["text"]):
                    redundant += 1
        # A write RETIRED ON ARRIVAL is a different event from one a later assertion replaced, and this
        # report was blind to the difference: a store that dropped six writes by policy and one that
        # accepted six corrections returned byte-identical summaries (measured, 165 chars both). Since
        # 1.87.0 the guard is ON by default, so the retirements happen without anyone opting in, and the
        # inspector overview is where an operator would first notice writes going missing. The per-policy
        # detail is in supersession_report(); this is the pointer that tells them to go and read it.
        by_policy = self.supersession_report().get("by_policy") or {}
        retired = {p: n for p, n in by_policy.items() if p in ("echo_guard", "objectless_guard")}
        return {"total": len(_rows), "active": len(act), "superseded": len(sup), "by_type": by_type,
                "consolidated": linked, "decayed": decayed, "redundant_estimate": redundant,
                "redundant_frac": round(redundant / max(1, len(sample)), 3), "sampled": len(sample),
                "retired_on_arrival": sum(retired.values()), "retired_by_policy": retired}

    def _resolve_state_toggle(self, a: dict, b: dict, active: list, dup_threshold: float,
                              by_id: dict) -> tuple:
        """Two similar records CONTRADICT each other. Which one wins, or does neither?

        THE ONE PLACE THAT DECIDES, because there used to be two and they disagreed. `consolidate()`
        had all three protections; `consolidate_clusters()` reimplemented the same resolution with
        NONE of them, and `sleep()` -- the documented idle call, and an MCP tool -- calls
        consolidate_clusters unconditionally. So a user who turned on the hardening flags precisely
        because they feared single-shot poison had them on the path they call explicitly and off the
        path that runs on idle.

        Measured 2026-08-15 on one corpus, same store, same flags:

            consolidate()          supersede_requires_corroboration=True -> honest fact ACTIVE
            sleep()                supersede_requires_corroboration=True -> honest fact SUPERSEDED
            consolidate()          supersede_persistence=3               -> honest fact ACTIVE
            sleep()                supersede_persistence=3               -> honest fact SUPERSEDED
            consolidate()          back-filled record (older valid_from) -> correctly retired
            sleep()                back-filled record                    -> RETIRED THE CURRENT ONE
            consolidate()          refused overturn -> reopened=True, queue length 2
            sleep()                refused overturn -> reopened=None,  queue length 0

        Four divergences, not one. Copying the corroboration `if` across would have closed a quarter
        of it and left a fix that reads complete -- which is why this is an extraction rather than a
        patch: a guard added here in a year cannot land on one path only.

        Returns ("toggled", older) or ("linked", None). The caller counts and, on "toggled", checks
        whether its own anchor was the record retired.
        """
        # VALIDITY time, not ingest order. A fact learned LATE about an EARLIER state must not
        # overwrite the genuinely-current one just because it arrived later. The cluster path used
        # `a["ts"] <= b["ts"]`, so a back-filled record retired the current value -- the exact
        # inversion this rule exists to prevent.
        _vf = lambda r: r.get("valid_from", r["ts"])
        older, newer = (a, b) if _vf(a) <= _vf(b) else (b, a)

        # Fast-novelty guard (opt-in): supersede only on a CORROBORATED contradiction -- earned
        # credit, or the same bar as graduation. An uncorroborated single contradiction is recorded
        # as a link but does not override a standing fact.
        if self.supersede_requires_corroboration and not self._graduation_corroborated(newer, by_id):
            a["links"].append(b["id"])
            # FAIL LOUD, not just fail safe. BOTH records, because both survive a refused overturn
            # and because picking one made the flag depend on sort order.
            for _r, _o in ((older, newer), (newer, older)):
                self._flag_contested(_r, "uncorroborated_contradiction",
                                     _o.get("object"), {"contested_by": _o["id"]})
            return ("linked", None)

        # Persistence (CUSUM) guard: supersede only once the NEW state is asserted by
        # >= supersede_persistence independent records. An isolated poison flip stays below it.
        if self.supersede_persistence > 1:
            nvec = self._qvec(newer["text"])
            support = sum(
                1 for r in active if r["status"] == "active"
                and self._similarity(newer["text"], r, nvec) >= dup_threshold
                and not _value_clash(newer["text"], r["text"])
                and not _negation_clash(newer["text"], r["text"])
                and (_value_clash(older["text"], r["text"]) or _negation_clash(older["text"], r["text"])))
            if support < self.supersede_persistence:
                a["links"].append(b["id"])
                for _r, _o in ((older, newer), (newer, older)):
                    self._flag_contested(_r, "insufficient_persistence", _o.get("object"),
                                         {"contested_by": _o["id"], "support": support,
                                          "required": self.supersede_persistence})
                return ("linked", None)

        older["status"] = "superseded"
        older["superseded_ts"] = time.time()
        older["invalidated_at"] = _vf(newer)      # bi-temporal: when it stopped being current
        om = older.setdefault("meta", {})
        om["superseded_by_toggle"] = newer["id"]
        om["superseded_by_policy"] = ("toggle_corroborated" if self.supersede_requires_corroboration
                                      else ("toggle_persistence" if self.supersede_persistence > 1
                                            else "state_toggle"))
        # Accuracy loop: being OVERTURNED is a was-wrong signal -- debit the superseded claim, credit
        # the one that corrected the record.
        older["bad"] = float(older.get("bad", 0) or 0) + 1.0
        newer["good"] = float(newer.get("good", 0) or 0) + 1.0
        self._declare_retired(older, f"state toggle: contradicted by {newer['id']}")
        return ("toggled", older)

    def consolidate(self, keep: int | None = None, dup_threshold: float = 0.82,
                    hub_coverage: float = 0.12, link_duplicates: bool = True) -> dict:
        """The dream pass. ADDS a derived layer (status + links); never edits raw text. Three steps:

        1. HUB PASS — flag indiscriminate "universal-matcher" memories. Under lexical recall the
           similarity is the overlap coefficient |q∩t|/min(|q|,|t|), so a memory whose token set
           covers a large fraction of the corpus's common vocabulary scores ~1.0 against ALMOST ANY
           query and drowns the specific memory the user actually wanted (measured on a 6k-note
           vault: such hubs sat in the top-10 for ~47% of queries). We mark them `status:'hub'`
           (reversible; recall skips them unless include_hubs) — measured to lift recall@5 ~+22%.
        2. near-duplicate LINKING (dedup without delete) — EXCEPT a polarity clash, which is a
           STATE TOGGLE (preference flip): supersede the OLDER, since a contradiction is not a dup.
        3. keep-budget: mark the lowest-value surplus `superseded`.

        hub_coverage: a memory covering ≥ this fraction of the common vocabulary is a hub (0 disables).
        link_duplicates: the dup pass is O(n²); pass False to skip it on large stores.

        TENANT ISOLATION: on a tenant-bound store/view the dream pass operates ONLY on that tenant's rows, so
        one tenant's consolidation can never link, hub-flag, supersede, or evict another tenant's memory. An
        unbound store consolidates across everything (admin/legacy)."""
        active = [r for r in self.items if r["status"] == "active"
                  and (self.tenant is None or r.get("tenant") == self.tenant)
                  and not Inspeximus._is_session_bookkeeping(r)]

        # ── MATURATION PASS: episodic -> semantic ───────────────────────────────────────────────
        # This lives here, and not on the read path, because of a regression we shipped in 2.0.0.
        # Graduation used to be a side effect of `recall()` and was written as `if reinforce and ...`.
        # When 2.0.0 made `reinforce` default to False to give callers a pure read, graduation went
        # with it: measured 5 of 6 records graduating with reinforcement on and 0 of 6 on the new
        # default, with no other route in the package (credit + sleep + consolidate all left it at 0).
        # Nothing failed. 2422 tests passed, because every one of them was written for a store whose
        # reads reinforced, so none could tell "graduation is correct" from "graduation never ran".
        # Maturation is a consolidation concern, so it belongs in the dream pass, where it happens at
        # a moment the caller chooses instead of as a side effect of asking a question.
        _by_id = {r["id"]: r for r in self.items}
        graduated = 0
        for r in active:
            if self._may_graduate(r, _by_id):
                r["mtype"] = "semantic"
                r.setdefault("meta", {})["graduated_from_episodic"] = True
                graduated += 1
        if graduated:
            self._dirty = True     # a tier change must survive the process, like the rest of this pass

        hubs = 0
        if hub_coverage and len(active) >= 50:
            toks, common = self._common_vocab(active)
            nv = len(common) or 1
            for r, tk in zip(active, toks):
                shared = len(tk & common)
                cov = shared / nv
                # A genuine 'universal matcher' overlaps MANY of the corpus's common words. Requiring an absolute
                # floor (>= 3 shared common words) on top of the coverage fraction prevents the low-diversity /
                # templated-store failure: when the common vocabulary is tiny (e.g. a handful of repeated attribute
                # words), a legitimate memory trivially covers >= hub_coverage of it with just ONE common word, which
                # would wrongly flag every memory a hub and SILENTLY EMPTY recall. (Measured: 3-5 shared attrs -> 100%
                # hub-flagged, 0% recall, before this floor.)
                if shared >= 3 and cov >= hub_coverage:
                    r["status"] = "hub"
                    r.setdefault("meta", {})["hub"] = True
                    r["meta"]["hub_coverage"] = round(cov, 3)
                    r["superseded_ts"] = time.time()
                    hubs += 1
            active = [r for r in active if r["status"] == "active"]
        active.sort(key=lambda r: -r["value"])
        linked = toggled = 0
        if link_duplicates:
            # Pairwise near-duplicate pass. A high-similarity pair is normally LINKED (dedup without
            # delete) — UNLESS it's a polarity clash (one negates the other), which is a STATE TOGGLE
            # (a preference flip / contradiction), not a duplicate. Then we supersede the OLDER memory
            # so recall returns the NEW state, instead of letting high vector similarity silently
            # merge a contradiction into one blob. (state-toggle guard.)
            for i, a in enumerate(active):
                if a["status"] != "active":          # superseded by an earlier toggle this pass
                    continue
                avec = self._qvec(a["text"])         # embed each anchor once, not once per partner
                for b in active[i + 1:]:
                    if b["status"] != "active" or b["id"] in a["links"]:
                        continue
                    if self._similarity(a["text"], b, avec) >= dup_threshold:
                        if _negation_clash(a["text"], b["text"]) or _value_clash(a["text"], b["text"]):
                            # ONE resolution, shared with consolidate_clusters(). It lived here as an
                            # inlined block and the cluster path had its own copy with none of the
                            # three guards; the copies are gone so a future guard cannot land on one
                            # path only. See _resolve_state_toggle for the four measured divergences.
                            _verdict, _older = self._resolve_state_toggle(
                                a, b, active, dup_threshold, _by_id)
                            if _verdict == "linked":
                                linked += 1
                                continue
                            toggled += 1
                            if _older is a:
                                break                # this anchor is gone; advance to the next
                        else:
                            a["links"].append(b["id"]); linked += 1
        staled = 0
        # RE-DERIVE THE POPULATION. `active` was captured before the hub and toggle passes, both of
        # which RETIRE records — so the keep-budget below was slicing a list that no longer described
        # the store. Measured on 30 records with 30 distinct keys whose texts were near-identical: the
        # toggle pass retired 29, the budget then dropped `keep`-onwards from the STALE list, and the
        # store came back with ZERO active records while the report still said `kept: 10`. recall()
        # returned nothing. With genuinely distinct texts the same call behaves correctly (30 -> 10),
        # which is why this survived: the bug only shows when an earlier pass has already fired.
        # ...and guard bookkeeping is out of the budget entirely (_GUARD_KEYSPACES): measured, keep=5 over 30
        # recorded deprecations demoted 25 of them and check_code() went quiet about symbols a refactor had
        # really deleted. The keep-budget bounds the recall working set, which this is not part of.
        active = [r for r in active if r.get("status") == "active" and not _is_guard_record(r)]
        if keep is not None and len(active) > keep:
            # active is sorted by -raw value (above). Legacy = keep the top-`keep` by raw value. Two-tier =
            # protect the top kprot by raw value (recency-immune), then fill the remaining budget from the
            # REST by EFFECTIVE (decay-weighted) value, so a stale high-raw-value memory can't crowd out a
            # freshly-useful one. (kprot=0 for tiny budgets -> pure recency-aware fill.)
            if self.two_tier_keep:
                now = time.time()
                kprot = int(self.protect_frac * keep)
                protected, rest = active[:kprot], active[kprot:]
                rest_keep = set(id(r) for r in
                                sorted(rest, key=lambda r: -self._effective_value(r, now))[:keep - kprot])
                drop = [r for r in rest if id(r) not in rest_keep]
            else:
                drop = active[keep:]
            for r in drop:
                r["status"] = "superseded"; r["superseded_ts"] = time.time(); staled += 1
                r.setdefault("meta", {})["superseded_by_policy"] = "keep_budget"
                self._declare_retired(r, "keep-budget: outside the retained set")
        self._save()
        # `kept` used to be `keep` — the REQUEST echoed back, never measured. It sat in the same dict as
        # `active`, so a run that left 0 active still reported `kept: 10` and the two contradicted each
        # other in one line. It is the surviving population now, and the request is reported separately
        # so a caller can still see what was asked for.
        _live = len([r for r in self.items if r["status"] == "active"])
        return {"active": _live, "graduated": graduated, "hubs_flagged": hubs, "linked_pairs": linked, "toggled": toggled,
                "staled": staled, "kept": _live, "keep_requested": keep, "total": len(self.items)}

    # ── cluster-triggered consolidation ───────────────────────────────────────
    def _cluster_active(self, sim_threshold: float = 0.5) -> list[list[dict]]:
        """Cheap greedy single-pass clustering of ACTIVE memories by similarity (O(n·#clusters)).
        Highest-value member is the cluster representative; each memory joins the most-similar
        cluster above the threshold, else starts its own. Lexical or semantic per the store's mode."""
        active = sorted([r for r in self.items if r["status"] == "active"
                         and (self.tenant is None or r.get("tenant") == self.tenant)],   # tenant-scoped clustering
                        key=lambda r: -r["value"])
        cents: list[dict] = []
        for r in active:
            rvec = self._qvec(r["text"])
            best = None
            for c in cents:
                s = self._similarity(c["rec"]["text"], r, c["vec"])
                if s >= sim_threshold and (best is None or s > best[1]):
                    best = (c, s)
            if best:
                best[0]["members"].append(r)
            else:
                cents.append({"rec": r, "vec": rvec, "members": [r]})
        return [c["members"] for c in cents]

    def consolidate_clusters(self, threshold: int = 15, cluster_sim: float = 0.5,
                             dup_threshold: float = 0.82, keep_per_cluster: int | None = None) -> dict:
        """Cluster-TRIGGERED consolidation: consolidate a semantic cluster only once it has grown past
        `threshold` members — not a global nightly blanket. Avoids (1) prematurely consolidating sparse
        topics, where the raw episodes are still the best representation, and (2) unbounded growth in
        dense ones. Cheap to call often (no-op until a cluster is ripe). Runs dedup + the state-toggle
        guard (+ optional keep-budget) WITHIN each ripe cluster only."""
        clusters = [[r for r in c if not Inspeximus._is_session_bookkeeping(r)]
                    for c in self._cluster_active(cluster_sim)]
        fired = linked = toggled = staled = 0
        # The population the persistence guard counts support in, and the id map the corroboration
        # guard resolves links through. Computed once: both are store-wide questions, not per-cluster.
        _live = [r for r in self.items if r.get("status") == "active"]
        _by_id = {r["id"]: r for r in self.items}
        for members in clusters:
            if len(members) < threshold:
                continue                              # sparse — leave the raw episodes alone
            fired += 1
            members.sort(key=lambda r: -r["value"])
            for i, a in enumerate(members):
                if a["status"] != "active":
                    continue
                avec = self._qvec(a["text"])
                for b in members[i + 1:]:
                    if b["status"] != "active" or b["id"] in a["links"]:
                        continue
                    if self._similarity(a["text"], b, avec) >= dup_threshold:
                        if _negation_clash(a["text"], b["text"]) or _value_clash(a["text"], b["text"]):
                            # ONE resolution, shared with consolidate(). This loop used to inline its
                            # own -- ts ordering, no corroboration guard, no persistence guard, no
                            # fail-loud flag -- and sleep() runs THIS one.
                            _verdict, _older = self._resolve_state_toggle(
                                a, b, _live, dup_threshold, _by_id)
                            if _verdict == "linked":
                                linked += 1
                                continue
                            toggled += 1
                            if _older is a:
                                break
                        else:
                            a["links"].append(b["id"]); linked += 1
            if keep_per_cluster is not None:
                act = sorted([r for r in members if r["status"] == "active"], key=lambda r: -r["value"])
                for r in act[keep_per_cluster:]:
                    r["status"] = "superseded"; r["superseded_ts"] = time.time(); staled += 1
                    r.setdefault("meta", {})["superseded_by_policy"] = "keep_budget"
                    self._declare_retired(r, "keep-budget: outside the cluster's retained set")
        self._save()
        return {"clusters_total": len(clusters), "clusters_fired": fired, "threshold": threshold,
                "linked_pairs": linked, "toggled": toggled, "staled": staled}

    def apply_retention(self, max_age_days: float, drop_superseded: bool = True,
                        drop_stale_episodic: bool = True) -> dict:
        """TIME-BASED RETENTION / data minimization (GDPR Art. 5(1)(e) storage limitation — the age-bound
        companion to `capacity=`'s size bound and to `forget_subject`'s subject erasure). Hard-deletes memories
        older than `max_age_days` (by ingest time), but NEVER the current value of a key, and never a graduated
        `semantic`/`procedural` fact — those are the live state, not stale accumulation. By default it drops two
        classes: (1) SUPERSEDED records past the cutoff (old retired values — minimizing retained PII; note this
        disables `as_of()`/`history()` for those intervals, so the audit-vs-minimization trade-off is yours via
        `drop_superseded`); (2) stale un-keyed EPISODIC records past the cutoff (old raw conversation turns).
        Call it directly or let `sleep(retention_days=…)` apply it on idle. Textbook (DB TTL / log retention /
        storage-limitation), packaged as a native zero-dependency retention primitive. Returns
        {expired, ids, cutoff_iso, dropped_superseded, dropped_stale_episodic, kept_active}."""
        cutoff = time.time() - float(max_age_days) * 86400.0
        drop, sup_n, epi_n = [], 0, 0
        for r in self.items:
            if r.get("ts", 0) >= cutoff:
                continue                                        # recent -> keep
            st = r.get("status")
            if drop_superseded and st == "superseded":
                drop.append(r["id"]); sup_n += 1
            elif drop_stale_episodic and st == "active" and r.get("key") is None \
                    and (r.get("mtype") or "episodic") == "episodic":
                drop.append(r["id"]); epi_n += 1
            # active keyed values, active semantic/procedural, and anything recent are NEVER expired
        if drop:
            self.forget(ids=drop)
        return {"expired": len(drop), "ids": sorted(drop),
                "cutoff_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff)),
                "dropped_superseded": sup_n, "dropped_stale_episodic": epi_n,
                "kept_active": sum(1 for r in self.items if r.get("status") == "active")}

    def sleep(self, cluster_threshold: int = 15, keep: int | None = None,
              retention_days: float | None = None) -> dict:
        """SLEEP-TIME COMPUTE: one idempotent, cheap idle-maintenance call the host runs whenever the
        agent is idle. The write path (remember) stays fast — append + keyed supersession + (opt-in)
        capacity eviction — and the EXPENSIVE O(n) reorganization is deferred here: cluster-triggered
        consolidation (dedup + state-toggle linking within ripe clusters), then optional keep-budget
        pruning and capacity re-affirmation. Cheap-to-call: a no-op until a cluster is ripe / capacity
        is exceeded, so the host can invoke it on every idle tick. Idempotent: a second immediate call
        does no new work. Never edits raw text. Returns what the pass did.

        This is inspeximus's answer to Letta-style sleep-time compute, but as a pure library primitive (the
        host schedules the idle window; inspeximus provides the deferred maintenance op) — no agent loop, no
        graph DB, no hosted service."""
        report = {"consolidated_clusters": self.consolidate_clusters(threshold=cluster_threshold)}
        if keep is not None:
            report["keep_budget"] = self.consolidate(keep=keep)
        elif self.capacity is not None:
            before = sum(1 for r in self.items if r.get("status") == "active")
            self._evict_to_capacity()
            report["evicted_on_sleep"] = before - sum(1 for r in self.items if r.get("status") == "active")
        if retention_days is not None:
            report["retention"] = self.apply_retention(retention_days)
        return report

    # ── SESSION BOUNDARY + CROSS-SESSION DIGEST (deterministic, zero-LLM) ─────────────────────────────
    # An agent finishes a session; when the next one starts it should already know what happened. The
    # usual implementation sends the transcript to an LLM to summarise it. This does not: the digest is a
    # LEDGER DIFF over the store's own supersession ledger — which keys changed value, which decisions
    # were recorded, what was erased, what is still open — so it is instant, free, byte-reproducible, and
    # auditable line by line. There is no summariser here to hallucinate, drift, or cost anything.
    #
    # Three primitives, in the order a host calls them:
    #   open_session()    -> mark the boundary (keyed, so exactly one session is open at a time)
    #   close_session()   -> write ONE digest record for the window (the ledger diff)
    #   session_context() -> the size-bounded block to inject at the START of the next session
    #
    # WHY ONE KEY FOR EVERY DIGEST. All digests share `SESSION_DIGEST_KEY`, so a new one RETIRES the
    # previous one through ordinary keyed supersession. That buys three things for free: `recall("what
    # changed last session", k=1)` is unambiguous (exactly one digest is ever active), `history(key)` is
    # the full session timeline, and `revert(key)` steps back one session. A per-session key would put N
    # near-identical digests in the pool and make the k=1 answer a coin flip between them.

    SESSION_DIGEST_KEY = "session::digest"          # the single keyed ledger of session digests
    SESSION_OPEN_KEY = "session::open"              # the boundary marker (keyed -> one open session)
    SESSION_TAGS = ("session-digest", "session-boundary")   # never digest our own bookkeeping
    # Raw tool exhaust. A file's current contents and a shell command are captured (they are useful
    # WITHIN a session, via recall) but they are not what the next session needs to resume, and a
    # ledger diff that did not demote them would just be the event log again — the thing a context
    # window already does badly. Their salience is CAPPED, so no amount of accrued value promotes them.
    SESSION_MECHANICS_TAGS = ("bash", "file", "edit", "tool", "mechanics")
    SESSION_MECHANICS_CAP = 1.0
    SESSION_SALIENCE_THRESHOLD = 2.5                # below this a record is not worth a next session's context
    SESSION_OPEN_TAGS = ("open", "todo", "question", "open-thread")

    @staticmethod
    def _is_session_bookkeeping(rec: dict) -> bool:
        """Is this record the session machinery itself (a digest or a boundary marker) rather than a
        memory? Consolidation must skip these, and skipping them in ONE of its passes is not enough --
        measured, the digest survived the hub pass and was then retired by the NEAR-DUPLICATE pass as a
        `_value_clash` against a note it summarises. That is the correct verdict for two ordinary
        memories and the wrong one here: a session digest is a cross-cutting summary, so it is similar to
        everything it covers BY CONSTRUCTION, and every consolidation heuristic (universal-matcher, near
        duplicate, state toggle, keep-budget) reads that similarity as a reason to demote it. Retiring
        the digest silently ends the cross-session loop -- the next SessionStart injects nothing and
        reports an honest, wrong `items: 0`.

        These records are keyed and already have their own supersession chain (one active digest, one
        open marker), so nothing consolidation offers applies to them."""
        return bool(set(rec.get("tags") or []) & set(Inspeximus.SESSION_TAGS)) or \
            rec.get("key") in (Inspeximus.SESSION_DIGEST_KEY, Inspeximus.SESSION_OPEN_KEY)

    @staticmethod
    def session_salience(rec: dict, corrector_ids=()) -> float:
        """How much does this record deserve a place in the NEXT session's context? Deterministic,
        content-only, no LLM, no similarity. The weights are constants, not a learned model, so the same
        record scores the same everywhere and the threshold is auditable:

            decision tag                    3.0     what we concluded, and why
            open thread (tag / reopened)    3.0     what is still unresolved
            knowledge tag                   2.0     curated, durable knowledge
            it CORRECTED an earlier value  +2.0     (its write retired a same-key record)
            mtype procedural / semantic    +1.0 / +0.5
            carries a supersession key     +0.5     a keyed fact is live state, not chatter
            accrued value                  +0.2 x min(value, 5)

        then CAPPED at SESSION_MECHANICS_CAP for raw tool exhaust (bash/file/edit tags), and 0.0 for the
        session bookkeeping itself. Default admission bar is SESSION_SALIENCE_THRESHOLD (2.5), which a
        decision (>=4.4) and a correction of a keyed fact (>=3.2) clear and which a shell command (<=1.0),
        a file state (<=1.0) and an untagged episodic note (0.2) do not.

        HONEST SCOPE — the trade this makes, stated rather than hidden. A plain durable fact written with
        no tag and no key scores 1.2 and is NOT injected. That is deliberate: the digest carries
        decisions, corrections and open threads, not everything true. If a fact must survive the session
        boundary, record it as a decision (`remember_decision`), give it a `key` so a later change is a
        correction, or tag it `knowledge` — each of which is a statement about the fact's durability that
        the store can check, unlike a summariser's opinion of it."""
        tags = set(rec.get("tags") or [])
        if tags & set(Inspeximus.SESSION_TAGS):
            return 0.0
        s = 0.0
        if "decision" in tags:
            s += 3.0
        if (tags & set(Inspeximus.SESSION_OPEN_TAGS)) or rec.get("reopened"):
            s += 3.0
        if "knowledge" in tags:
            s += 2.0
        if rec.get("id") in corrector_ids:
            s += 2.0
        mt = rec.get("mtype") or "episodic"
        s += {"procedural": 1.0, "semantic": 0.5}.get(mt, 0.0)
        if rec.get("key"):
            s += 0.5
        try:
            s += 0.2 * min(float(rec.get("value") or 0.0), 5.0)
        except (TypeError, ValueError):
            pass
        if tags & set(Inspeximus.SESSION_MECHANICS_TAGS):
            s = min(s, Inspeximus.SESSION_MECHANICS_CAP)
        return round(s, 3)

    def _session_correctors(self) -> dict:
        """id -> [texts of the records it retired]. A record is a CORRECTION iff some other record names
        it in meta['superseded_by_toggle'] — the stamp every supersession path already writes (keyed_lww,
        state_toggle, echo_guard, ...). Read straight off the ledger; nothing is inferred."""
        out: dict = {}
        for r in self._tenant_rows():
            nid = (r.get("meta") or {}).get("superseded_by_toggle")
            if nid:
                out.setdefault(nid, []).append(r)
        return out

    def _session_digests(self) -> list:
        """Every session-digest record for this tenant, oldest first (the whole keyed chain: the one
        active digest plus every superseded predecessor)."""
        recs = [r for r in self._tenant_rows() if r.get("key") == self.SESSION_DIGEST_KEY]
        recs.sort(key=lambda r: ((r.get("meta") or {}).get("session_seq") or 0, r.get("ts") or 0))
        return recs

    def open_session(self, session_id: str | None = None, label: str | None = None) -> dict:
        """Mark a SESSION BOUNDARY. Writes one keyed marker, so opening a new session automatically
        retires the previous marker — the "exactly one session is open" invariant is the store's own
        keyed supersession, not a flag someone has to remember to clear.

        `session_id` is the host's id for the session (Claude Code passes one on every hook event). Left
        out, it is derived from the sequence number, which keeps the whole mechanism reproducible from
        the event log alone. Returns {session_id, session_seq, opened_ts, digest_at_open, marker_id}."""
        seq = len(self._session_digests()) + 1
        sid = str(session_id) if session_id else f"s{seq}"
        at_open = self.state_digest()
        mid = self._stamp(
            f"SESSION {seq} OPEN ({sid})", key=self.SESSION_OPEN_KEY, object=f"{seq}:{sid}",
            tags=["session-boundary"], mtype="episodic",
            meta={"kind": "session_open", "session_seq": seq, "sid": sid, "label": label,
                  "digest_at_open": at_open})
        return {"session_id": sid, "session_seq": seq, "opened_ts": time.time(),
                "digest_at_open": at_open, "marker_id": mid}

    def _session_window(self, session_id: str | None = None, since: float | None = None):
        """The records that belong to the session being closed, and how that was decided.

        Preference order, most precise first: (1) an explicit `since` timestamp; (2) records STAMPED with
        this session_id (`remember(session_id=...)` -> meta['sid'], which the Claude Code hooks set on
        every capture); (3) everything written after the open marker; (4) everything written after the
        last digest. A window that resolved to nothing is reported as such rather than silently digesting
        the whole store — the failure mode where "no session boundary" reads as "one enormous session"."""
        rows = [r for r in self._tenant_rows()
                if not (set(r.get("tags") or []) & set(self.SESSION_TAGS))]
        sid = str(session_id) if session_id else None
        if since is None and sid:
            stamped = [r for r in rows if str((r.get("meta") or {}).get("sid") or "") == sid]
            if stamped:
                return stamped, "sid"
        t0 = since
        if t0 is None:
            marker = next((r for r in self._tenant_rows()
                           if r.get("key") == self.SESSION_OPEN_KEY and r.get("status") == "active"),
                          None)
            if marker is not None:
                t0 = marker.get("ts")
        mode = "since" if since is not None else ("marker" if t0 is not None else "last_digest")
        if t0 is None:
            digs = self._session_digests()
            t0 = digs[-1].get("ts") if digs else 0.0
        return [r for r in rows if (r.get("ts") or 0.0) >= t0], mode

    def _session_entries(self, rows: list, threshold: float) -> tuple:
        """The LEDGER DIFF for a window: (kept_entries, considered, rejected). Each entry is typed by what
        the ledger says happened — a decision recorded, a key whose value CHANGED, a key newly
        established, a thread left open — not by what a model thought the session was about."""
        correctors = self._session_correctors()
        kept, considered, rejected = [], 0, 0
        for r in rows:
            if r.get("status") != "active":     # a value retired later in the same session is not the
                continue                        # session's conclusion; the value that replaced it is
            considered += 1
            sal = self.session_salience(r, correctors)
            if sal < threshold:
                rejected += 1
                continue
            tags = set(r.get("tags") or [])
            prior = correctors.get(r.get("id")) or []
            if "decision" in tags:
                kind = "decision"
            elif prior:
                kind = "correction"
            elif (tags & set(self.SESSION_OPEN_TAGS)) or r.get("reopened"):
                kind = "open"
            elif r.get("key"):
                kind = "key_new"
            else:
                kind = "note"
            kept.append({"kind": kind, "id": r.get("id"), "key": r.get("key"),
                         "object": r.get("object"), "text": r.get("text") or "",
                         "salience": sal,
                         "was": (prior[0].get("object") or prior[0].get("text")) if prior else None})
        return kept, considered, rejected

    _SESSION_KIND_ORDER = {"decision": 0, "correction": 1, "open": 2, "key_new": 3, "note": 4}
    _SESSION_KIND_MARK = {"decision": "*", "correction": "!", "open": "?", "key_new": "+", "note": "-"}
    _SESSION_KIND_HEAD = {"decision": "decisions recorded:", "correction": "corrections (a stored value changed):",
                          "open": "still open:", "key_new": "new keyed facts:", "note": "notes:"}

    @staticmethod
    def _session_sort_key(e: dict) -> tuple:
        """CONTENT-ONLY ordering — no timestamps, no record ids. That is what makes the rendered digest
        byte-identical across two independently-built stores holding the same event log, which is the
        zero-LLM claim in falsifiable form."""
        return (Inspeximus._SESSION_KIND_ORDER.get(e.get("kind"), 9),
                -float(e.get("salience") or 0.0), e.get("text") or "")

    @staticmethod
    def _session_line(e: dict, max_entry_chars: int) -> str:
        mark = Inspeximus._SESSION_KIND_MARK.get(e.get("kind"), "-")
        txt = " ".join((e.get("text") or "").split())
        if e.get("kind") == "correction" and e.get("was"):
            txt += "  (was: " + " ".join(str(e["was"]).split())[:60] + ")"
        if len(txt) > max_entry_chars:
            txt = txt[:max_entry_chars].rstrip() + "..."
        return f"  {mark} {txt}"

    @staticmethod
    def _session_render(header: str, entries: list, max_chars: int, max_entry_chars: int,
                        footer: str = "") -> tuple:
        """Assemble a block that is GUARANTEED <= max_chars. Lines are added while they fit; the footer is
        reserved up front so the bound holds with it attached, and a final slice is the backstop. Returns
        (text, n_written, truncated)."""
        reserve = len(footer) + 1 if footer else 0
        out, n = [header], 0
        used = len(header)
        head_done = set()
        for e in entries:
            head = Inspeximus._SESSION_KIND_HEAD.get(e.get("kind"), "")
            pending = []
            if head and head not in head_done:
                pending.append(head)
            pending.append(Inspeximus._session_line(e, max_entry_chars))
            cost = sum(len(p) + 1 for p in pending)
            if used + cost + reserve > max_chars:
                break
            out += pending
            head_done.add(head)
            used += cost
            n += 1
        truncated = n < len(entries)
        if footer and used + reserve <= max_chars:
            out.append(footer)
        text = "\n".join(out)
        return text[:max_chars], n, truncated

    def close_session(self, session_id: str | None = None, *, since: float | None = None,
                      threshold: float | None = None, max_items: int = 12, max_chars: int = 1200,
                      max_entry_chars: int = 200, sleep_pass: bool = False,
                      write: bool = True) -> dict:
        """SESSION END: write ONE digest record describing what this session ESTABLISHED — a deterministic
        ledger diff, not an LLM summary. No model is called anywhere on this path.

        What lands in it, all read off the store's own ledger: decisions recorded (`remember_decision`),
        keys whose value CHANGED (a record that retired an earlier same-key value — the stamp keyed
        supersession already writes), keys newly established, threads left open (`reopened()` or an
        `open`/`todo` tag), and the erasure count for the window from the tombstone chain. Everything
        below `threshold` salience is dropped, so the digest is the session's conclusions rather than its
        transcript; `session_salience` documents the weights and what they deliberately exclude.

        The digest is stored under ONE key (`SESSION_DIGEST_KEY`), so it supersedes the previous session's
        — `recall("what changed last session", k=1)` is unambiguous, `history()` is the session timeline,
        and `revert()` steps back a session.

        `write=False` computes and returns the digest without storing it (a preview, and the way to assert
        determinism: the same store must render the same text twice).

        `sleep_pass=True` runs the existing `sleep()` maintenance here first — SessionEnd is the natural
        idle window for it. OFF by default because `consolidate_clusters` is O(n^2) over active records
        and this runs in the agent's exit path; turn it on for small stores or a background caller.

        Returns {written, id, session_id, session_seq, text, chars, bound, items, considered,
        rejected_below_threshold, erased, truncated, store_digest, mode}."""
        thr = self.SESSION_SALIENCE_THRESHOLD if threshold is None else float(threshold)
        report: dict = {"threshold": thr, "bound": max_chars}
        if sleep_pass and write:
            report["sleep"] = self.sleep()
        rows, mode = self._session_window(session_id, since)
        t0 = min((r.get("ts") or 0.0) for r in rows) if rows else None
        entries, considered, rejected = self._session_entries(rows, thr)
        entries.sort(key=self._session_sort_key)
        entries = entries[:max_items]
        erased = 0
        if t0 is not None:
            erased = sum(1 for t in getattr(self, "_tombstones", []) or []
                         if (t.get("ts") or 0.0) >= t0)
        seq = len(self._session_digests()) + 1
        marker = next((r for r in self._tenant_rows()
                       if r.get("key") == self.SESSION_OPEN_KEY and r.get("status") == "active"), None)
        sid = str(session_id) if session_id else (
            str((marker.get("meta") or {}).get("sid")) if marker else f"s{seq}")
        # The header carries the words a resuming agent actually types ("what changed ... last session"),
        # because this record has to be findable by plain lexical recall in a store of thousands.
        header = (f"SESSION DIGEST {seq} -- what changed in the last session "
                  f"(deterministic ledger diff, no LLM):")
        footer_bits = [f"[{len(entries)} of {considered} records kept at salience >= {thr}"]
        if erased:
            footer_bits.append(f"{erased} erased")
        footer = "; ".join(footer_bits) + "]"
        text, n_written, truncated = self._session_render(header, entries, max_chars,
                                                          max_entry_chars, footer)
        entries = entries[:n_written]
        report.update({"session_id": sid, "session_seq": seq, "text": text, "chars": len(text),
                       "items": n_written, "considered": considered,
                       "rejected_below_threshold": rejected, "erased": erased,
                       "truncated": truncated, "mode": mode, "window_records": len(rows),
                       "store_digest": self.state_digest()})
        if not write:
            report["written"] = False
            report["id"] = None
            return report
        mid = self._stamp(
            text, key=self.SESSION_DIGEST_KEY,
            object=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            tags=["session-digest"], mtype="semantic", value=3.0,
            meta={"kind": "session_digest", "session_seq": seq, "sid": sid,
                  "entries": entries, "considered": considered,
                  "rejected_below_threshold": rejected, "erased": erased,
                  "threshold": thr, "store_digest_at_close": report["store_digest"]})
        report["written"] = True
        report["id"] = mid
        return report

    def session_context(self, *, max_sessions: int = 3, max_items: int = 10, max_chars: int = 1200,
                        max_entry_chars: int = 200, threshold: float | None = None) -> dict:
        """SESSION START: the size-bounded block to inject so the next session already knows what
        happened. Built from the stored digests, then RE-RESOLVED against the live store — which is the
        part a summarised transcript cannot do. Every entry is looked up by id:

          * retired since (a later session changed its mind) -> DROPPED, and if it was keyed, replaced by
            the value that is current NOW. A correction made in session 3 rewrites what session 4 is
            told, with no re-summarisation and no LLM.
          * erased since -> DROPPED. A right-to-erasure request reaches the injected context too, instead
            of a deleted fact living on inside a frozen summary.
          * hub-flagged by consolidation -> DROPPED.

        Then re-scored, re-thresholded, ranked newest-session-first and cut to `max_chars`. The returned
        `text` is guaranteed <= max_chars; `items`, `dropped_*` and `substituted_current` say what the
        bound and the threshold actually did, so an empty injection is visibly an empty injection."""
        thr = self.SESSION_SALIENCE_THRESHOLD if threshold is None else float(threshold)
        digests = self._session_digests()[-max_sessions:]
        by_id = {r.get("id"): r for r in self._tenant_rows()}
        correctors = self._session_correctors()
        counts = {"dropped_superseded": 0, "dropped_erased": 0, "dropped_hub": 0,
                  "dropped_below_threshold": 0, "substituted_current": 0}
        seen, cands = set(), []
        for d in reversed(digests):                       # newest session first
            seq = (d.get("meta") or {}).get("session_seq") or 0
            for e in ((d.get("meta") or {}).get("entries") or []):
                live = by_id.get(e.get("id"))
                if live is None:
                    counts["dropped_erased"] += 1
                    continue
                if live.get("status") == "hub":
                    counts["dropped_hub"] += 1
                    continue
                if live.get("status") != "active":
                    k = live.get("key")
                    cur = next((r for r in self._tenant_rows()
                                if k and r.get("key") == k and r.get("status") == "active"), None)
                    if cur is None:
                        counts["dropped_superseded"] += 1
                        continue
                    counts["substituted_current"] += 1
                    live = cur
                dedup = live.get("key") or live.get("id")
                if dedup in seen:
                    continue
                sal = self.session_salience(live, correctors)
                if sal < thr:
                    counts["dropped_below_threshold"] += 1
                    continue
                seen.add(dedup)
                prior = correctors.get(live.get("id")) or []
                cands.append({"kind": e.get("kind"), "id": live.get("id"), "key": live.get("key"),
                              "text": live.get("text") or "", "salience": sal, "session_seq": seq,
                              "was": (prior[0].get("object") or prior[0].get("text")) if prior else None})
        cands.sort(key=lambda e: (-int(e.get("session_seq") or 0), self._session_sort_key(e)))
        cands = cands[:max_items]
        out = {"enabled": True, "sessions": len(digests), "candidates": len(seen) + counts["dropped_below_threshold"],
               "bound": max_chars, "threshold": thr}
        out.update(counts)
        if not cands:
            out.update({"text": "", "chars": 0, "items": 0, "truncated": False})
            return out
        header = ("[inspeximus] resuming from the previous session(s) -- what changed, decided and stayed open "
                  "(deterministic ledger diff, no LLM):")
        text, n, truncated = self._session_render(header, cands, max_chars, max_entry_chars)
        out.update({"text": text, "chars": len(text), "items": n, "truncated": truncated})
        return out

    # ── contradiction surfacing (flag, never auto-delete) ─────────────────────
    def contradictions(self, sim_threshold: float = 0.5, incompatible=None) -> list[dict]:
        """Flag mutually-incompatible memories among RELATED ones (similarity-gated) for human review.
        `incompatible(a_text, b_text)->bool` defaults to a negation/polarity heuristic."""
        inc = incompatible or _negation_clash
        # _content_rows(): "granted" vs "revoked" on one grant key is a negation clash by the default
        # heuristic, so without this every revocation would be reported as a contradictory memory pair.
        active = [r for r in self._content_rows() if r["status"] == "active"
                  and (self.tenant is None or r.get("tenant") == self.tenant)]   # tenant-scoped
        flags = []
        for i, a in enumerate(active):
            avec = self._qvec(a["text"])             # embed each anchor once, not once per partner
            # ...and TOKENIZE it once too. _similarity() takes a hoisted `qtok` for exactly this and
            # this caller never passed it, so on the no-embedder path (the zero-dependency default)
            # _tokens(a["text"]) ran again for every partner — O(n^2) tokenizations of n distinct
            # strings. Pure function of the text, so passing the precomputed set is result-identical.
            # This does NOT change the O(n^2) pair scan, which is inherent to an all-pairs check and is
            # documented as such in check_conflict(); it removes redundant work inside it.
            atok = _tokens(a["text"])
            for b in active[i + 1:]:
                if self._similarity(a["text"], b, avec, atok) >= sim_threshold and inc(a["text"], b["text"]):
                    flags.append({"a": a["id"], "b": b["id"],
                                  "a_text": a["text"][:120], "b_text": b["text"][:120]})
        return flags

    def check_conflict(self, text: str, key: str | None = None, object: str | None = None,
                       sim_threshold: float = 0.5, incompatible=None) -> list[dict]:
        """WRITE-TIME conflict check (READ-ONLY, no LLM): would committing this new fact CONTRADICT an
        existing active memory? Call it BEFORE remember() to flag/gate a write instead of trusting the
        write path — the pattern practitioners land on ("score each new fact against what's stored, flag
        conflicts before they commit"). Returns the conflicting active records (empty list = clean), each
        tagged with the conflict kind; it does NOT write, so you decide (commit / review / reject).

        Two deterministic signals, both cheap (O(neighbourhood), not the O(n^2) `contradictions()` scan):
          - keyed_value_change: an active memory shares `key` but carries a DIFFERENT `object` (or, if no
            object is given, its text clashes) — a value update on a managed key, the thing to gate on for
            a high-stakes fact.
          - clash: among memories SIMILAR to `text` (>= sim_threshold), a value clash (numeric update) or a
            negation/polarity flip. Crucially this is NOT triggered by a pure duplicate — a restated
            identical fact has no value/negation clash — so it separates a contradiction from a near-dup,
            which a cosine-similarity gate cannot (a corrected value is often MORE embedding-similar to the
            original than a rephrase). Pass `incompatible(a, b) -> bool` (e.g. an LLM judge) to also catch a
            purely SEMANTIC contradiction with no numeric/negation marker ("...Berlin" vs "...Munich"),
            which the deterministic default does not.

        Mechanism is textbook — a DB CHECK-constraint / uniqueness validate-on-write, and TMS-style
        contradiction-on-assert (Doyle 1979) / AGM consistency-on-revision — brought into a zero-dependency
        memory store as a native, dependency-free primitive; the packaging is the point, not the idea."""
        inc = incompatible or (lambda a, b: _value_clash(a, b) or _negation_clash(a, b))
        active = [r for r in self.items if r.get("status") == "active"
                  and (self.tenant is None or r.get("tenant") == self.tenant)]   # tenant-scoped conflict check
        hits, seen = [], set()
        if key is not None:                                    # (1) value change on a managed key
            for r in active:
                if r.get("key") != key or r["id"] in seen:
                    continue
                if object is not None and r.get("object") is not None:
                    conflict = (r["object"] != object)         # both objects known -> compare directly
                else:
                    conflict = inc(text, r["text"])            # missing an object -> fall back to text clash
                if conflict:
                    hits.append((r, "keyed_value_change")); seen.add(r["id"])
        tvec = self._qvec(text)                                # (2) clash among similar neighbours
        for r in active:
            if r["id"] in seen:
                continue
            if self._similarity(text, r, tvec) >= sim_threshold and inc(text, r["text"]):
                hits.append((r, "clash")); seen.add(r["id"])
        return [{"id": r["id"], "kind": kind, "key": r.get("key"), "object": r.get("object"),
                 "text": r["text"][:200]} for r, kind in hits]

    def verify_claim(self, text: str, key: str | None = None, object: str | None = None,
                     sim_threshold: float = 0.5, incompatible=None) -> dict:
        """READ-TIME grounding check (READ-ONLY, no LLM): is this asserted memory-claim SUPPORTED by the
        CURRENT stored truth? The output-side complement to check_conflict (which gates WRITES). Call it on a
        claim an agent is about to ASSERT back to the user ("you told me X", "I remember that Y") to catch a
        claim that is ungrounded OR — the case a write-gate / tombstone store cannot see from the store alone
        — one that cites a value which WAS true but has since been SUPERSEDED or reverted. Deterministic and
        supersession-AWARE, which an LLM grounding judge does not reliably get (a corrected value is usually
        MORE embedding-similar to the claim than a rephrase, so a cosine/LLM check reads it as 'grounded').
        Does NOT write. A record's stored `object` (its value) is the discriminator, so a CATEGORICAL
        correction (Berlin->Munich) is caught, not only a numeric one. Returns {'verdict', 'current',
        'matched'} with verdict in:
          - 'supported'        : the claim asserts the ACTIVE value (exact object, the value appears in the
                                 claim, or — for object-less free text — no numeric/negation clash)
          - 'stale_superseded' : the claim asserts a RETIRED value whose key now holds a DIFFERENT current value
                                 — the reply is citing a corrected fact (the dangerous case; 'current' = truth now)
          - 'contradicted'     : asserts against the CURRENT active value
          - 'unverifiable'     : a similar record exists and does NOT refute the claim, but neither side
                                 carries a value and the claim is not a restatement -- so nothing here can
                                 confirm it. Until 1.80.0 this returned 'supported', which is how
                                 "allergic to shellfish" licensed the claim "allergic to peanuts" and
                                 attached the contradicting record as its evidence. Treat it as NOT
                                 grounded; pass `key`/`object` for a decidable answer.
          - 'unsupported'      : no matching memory (possible fabrication)
        HONEST LIMIT: on the KEYLESS path a categorical contradiction with no matching retired value may report
        'unsupported' rather than 'contradicted' (without a key there is no value axis to contradict on);
        pass `key` (and ideally `object`) for the precise supersession-aware verdict.

        This is the deterministic, retire-history-aware read-side of the 'model proposes, store decides'
        boundary: a write-gate stops a corrected fact being re-STORED, but only a check against current-truth-
        vs-history catches the same corrected fact being re-ASSERTED in the generated reply."""
        inc = incompatible or (lambda a, b: _value_clash(a, b) or _negation_clash(a, b))
        low = (text or "").lower()

        def _restates(claim: str, rec_text: str):
            """With no value on either side, is the claim a RESTATEMENT of this record, or merely
            unrefuted by it? Returns True (restatement), or None for "no value axis -- cannot tell".

            Never False: a non-restatement is not evidence against, only absence of evidence for.
            Deliberately strict, because the failure it exists to stop is answering `supported` on the
            strength of two sentences sharing a subject."""
            def _words(s):
                return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in _STOP}
            a, b = _words(claim), _words(rec_text)
            if not a or not b:
                return None
            if a == b or a <= b or b <= a:                  # one is the other, possibly with filler
                return True
            overlap = len(a & b) / len(a | b)
            return True if overlap >= 0.9 else None
        def _matches(rec_object, rec_text):
            # Does the CLAIM assert this record's value? Use the stored object as the discriminator so a
            # CATEGORICAL correction (Berlin->Munich; no number, no negation) is caught, not only numeric ones
            # (the numeric/negation clash heuristic alone was blind to categorical value changes).
            if object is not None and rec_object is not None:
                return str(rec_object) == str(object)                       # both values known -> exact compare
            if object is not None:
                # The CALLER knows the value but the record does not (`object=` is optional on remember(),
                # so most real stores are in this state). Until 1.54.0 this fell through to the clash
                # heuristic, which is blind to a categorical change — so re-asserting a retired password
                # verdicted `supported` even when the caller had passed object='hunter2' explicitly. The
                # caller's value is a usable discriminator against the record's TEXT; this is the mirror of
                # the branch below.
                v = str(object).lower().strip()
                return bool(v) and re.search(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])",
                                             (rec_text or "").lower()) is not None
            if rec_object is not None:
                v = str(rec_object).lower().strip()                         # record's known value in the claim?
                return bool(v) and re.search(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", low) is not None
            # NO VALUE AXIS ANYWHERE. This used to return `not inc(...)` -- absence of a numeric or negation
            # clash reported as support. "the patient is allergic to shellfish" therefore verdicted the claim
            # "the patient is allergic to peanuts" as SUPPORTED, and attached the contradicting record as the
            # evidence. Two nouns do not clash numerically, and `object=` is optional on remember(), so most
            # real stores sit on exactly this path. This is the gate an agent calls before asserting something
            # to a person; "I found nothing that disagrees" is not "the store says so", and returning the
            # stronger of the two is how a fabrication gets licensed.
            if inc(text, rec_text or ""):
                return False                                                # positive evidence AGAINST
            return _restates(text, rec_text or "")                          # True, or None for "cannot tell"
        def _out(verdict, cur, matched):
            return {"verdict": verdict,
                    "current": (cur.get("object") if isinstance(cur, dict) else cur),
                    "matched": matched}
        # (1) keyed path — precise and supersession-aware
        if key is not None:
            cur = self._current_active(key)
            m = _matches(cur.get("object"), cur["text"]) if cur is not None else False
            if m is True:
                return _out("supported", cur, {"id": cur["id"], "object": cur.get("object"),
                                                "text": cur["text"][:200]})
            if m is None:
                # Neither side carries a value, and the claim is not a restatement. The honest answer is
                # that this store cannot tell -- with the record attached so the caller can judge.
                return _out("unverifiable", cur, {"id": cur["id"], "object": cur.get("object"),
                                                  "text": cur["text"][:200]})
            stale = next((h for h in self.history(key)
                          if h["status"] != "active"
                          and _matches(h.get("object"), h.get("text")) is True), None)
            if stale is not None:
                return _out("stale_superseded", (cur.get("object") if cur else None),
                            {"id": stale["id"], "object": stale.get("object"),
                             "text": (stale.get("text") or "")[:200],
                             "invalidated_at": stale.get("invalidated_at"), "policy": stale.get("policy")})
            if cur is not None:
                return _out("contradicted", cur, {"id": cur["id"], "object": cur.get("object"),
                                                  "text": cur["text"][:200]})
            return _out("unsupported", None, None)
        # (2) keyless path — similarity search: active (support / contradict), then retired (stale)
        tvec = self._qvec(text)
        rows = [r for r in self.items if self.tenant is None or r.get("tenant") == self.tenant]
        contra = undecidable = None
        for r in rows:
            if r.get("status") != "active":
                continue
            if self._similarity(text, r, tvec) >= sim_threshold:
                m = _matches(r.get("object"), r["text"])                    # claim asserts THIS record's value?
                if m is True:
                    return _out("supported", r.get("object"),
                                {"id": r["id"], "object": r.get("object"), "text": r["text"][:200]})
                if m is None and undecidable is None:
                    undecidable = r                     # similar, unrefuted, but nothing to check it against
                if contra is None and inc(text, r["text"]):
                    contra = r
        for r in rows:
            if r.get("status") == "active":
                continue
            if (self._similarity(text, r, tvec) >= sim_threshold
                    and _matches(r.get("object"), r["text"]) is True):
                return _out("stale_superseded", None,
                            {"id": r["id"], "object": r.get("object"), "text": r["text"][:200]})
        if contra is not None:
            return _out("contradicted", contra.get("object"),
                        {"id": contra["id"], "object": contra.get("object"), "text": contra["text"][:200]})
        if undecidable is not None:
            # A similar record exists and does not refute the claim, but nothing here can confirm it either.
            # Reported as its own verdict rather than folded into `supported` (which would license the
            # claim) or `unsupported` (which would call a real memory a fabrication).
            return _out("unverifiable", undecidable.get("object"),
                        {"id": undecidable["id"], "object": undecidable.get("object"),
                         "text": undecidable["text"][:200]})
        return _out("unsupported", None, None)

    def selection_integrity(self, query: str, k: int = 6, pool: int = 50) -> dict:
        """Make SELECTION-LEVEL manipulation AUDITABLE. Tamper-evidence/provenance verify that WHAT you
        retrieved is authentic, but are blind by construction to an attacker who injects authentic-looking
        UNTRUSTED writes that REROUTE which trusted facts land in the top-k — every cited record stays genuine
        while the SELECTION is steered (Fei et al., 'Selection Integrity for LLM Graph Memory', arXiv
        2606.12290: a faithful information-flow check is no defense against selection rerouting). inspeximus's
        answer, in its flag-don't-silently-fix spirit: diff the top-k the agent ACTUALLY gets against the top-k
        of only PROVENANCE-QUALIFIED memories (attested by / vouched by the trust root, via recall's
        trusted_only), and surface any qualified fact that untrusted writes DISPLACED out of the result, plus
        the untrusted records now occupying top-k slots. Deterministic, read-only (recall reinforcement off).

        Returns {stable, displaced, untrusted_in_topk, k, note?}: stable=True iff no provenance-qualified fact
        was pushed out of the top-k by untrusted writes. HONEST SCOPE: it makes the reroute VISIBLE for the
        caller to gate on — it does NOT prevent the untrusted write (a flag, by design), and 'qualified' is
        exactly your attestation / trust-seed policy. With no trust root configured it cannot distinguish
        trusted from untrusted and says so (note)."""
        actual = self.recall(query, k=k, reinforce=False)
        actual_ids = {r["id"] for r in actual}
        if not self.trust_seeds:
            return {"stable": None, "displaced": [], "untrusted_in_topk": [],
                    "k": k, "note": "no trust root configured (set trust_seeds / attest writes) — selection "
                                    "integrity cannot distinguish trusted from untrusted here (unknown, not safe)"}
        qualified = self.recall(query, k=k, trusted_only=True, reinforce=False)
        trusted_pool = self.recall(query, k=max(k, pool), trusted_only=True, reinforce=False)
        trusted_ids = {r["id"] for r in trusted_pool}
        displaced = [{"id": r["id"], "text": (r.get("text") or "")[:160]}
                     for r in qualified if r["id"] not in actual_ids]
        untrusted = [{"id": r["id"], "text": (r.get("text") or "")[:160]}
                     for r in actual if r["id"] not in trusted_ids]
        if not trusted_ids:
            # `stable = not displaced` is structurally True when the trusted recall returns NOTHING, so a
            # trust_seeds entry that matches no record (e.g. the literal source string where the store keys
            # on its canonical form) reported stable=True with the whole top-k untrusted. Empty seeds already
            # failed CLOSED; wrong seeds failed OPEN, which is the more dangerous of the two.
            return {"stable": None, "displaced": [], "untrusted_in_topk": untrusted, "k": k,
                    "note": f"trust_seeds are configured but match no record in this store "
                            f"(canonical forms: {sorted(Inspeximus._canon_source(x) for x in self.trust_seeds)}) "
                            f"— selection integrity is unknown here, not stable"}
        return {"stable": not displaced, "displaced": displaced,
                "untrusted_in_topk": untrusted, "k": k}

    @staticmethod
    def check_self_narration(text: str) -> dict:
        """WRITE-TIME self-narration guard (READ-ONLY, no LLM, no state): does this candidate write read as the
        ASSISTANT narrating its own reasoning/state ("as an AI…", "I think…", "I remember that…") rather than a
        fact about the user/world? An LLM memory-writer routinely stores its own hedges and self-talk as if they
        were user facts, silently polluting the store — the specific failure a fabrication write-gate should
        catch. Deterministic phrase match at word boundaries. Returns {'self_narration': bool, 'markers': [...]}
        so the caller can gate/rewrite the write; it FLAGS, never blocks (inspeximus never auto-rejects). Honest
        limit: heuristic — a legitimately stored first-person QUOTE can trip it, hence flag-not-block."""
        markers: list = []
        if isinstance(text, str) and text:
            low = text.lower()
            for phrase in _SELF_NARRATION:
                # word-boundary match so 'i think' does not fire inside 'within' etc.
                if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", low):
                    markers.append(phrase)
        return {"self_narration": bool(markers), "markers": markers}

    # ── value, reported at the COHORT level ───────────────────────────────────
    def value_by_cohort(self) -> dict:
        """Per-TAG value rollup. Deliberately not per-memory: at n-of-1, per-item value is noise;
        the cohort (tag / time-block) is where the signal is real."""
        out: dict[str, dict] = {}
        for r in self._tenant_rows():
            if r["status"] != "active":
                continue
            for tag in (r["tags"] or ["(untagged)"]):
                c = out.setdefault(tag, {"count": 0, "value": 0.0})
                c["count"] += 1; c["value"] += r["value"]
        return {k: {"count": v["count"], "value": round(v["value"], 2),
                    "avg": round(v["value"] / v["count"], 2)} for k, v in out.items()}

    def graph(self, include_superseded: bool = False) -> dict:
        """Deterministic knowledge GRAPH over keyed (subject::relation, object) memories — zero-LLM, no graph DB.
        Every memory stored with a key of the form 'subject::relation' AND an `object` is an edge
        subject -[relation]-> object; entities are the subjects + objects. This gives the 'graph memory' view
        mem0/Zep/cognee ship, but DERIVED deterministically from inspeximus's existing supersession triples (no LLM
        entity-extraction, no separate graph store). It covers memories keyed explicitly OR via the optional
        `extractor` hook, so extractor-keyed free text also enters the graph. Only ACTIVE edges by default, so a
        superseded fact drops out of the graph (the graph reflects CURRENT truth) unless include_superseded.
        Returns {'nodes': [entity,...], 'edges': [{subject, relation, object, id, text}, ...]}."""
        edges, nodes = [], set()
        for r in self.items:
            if not include_superseded and r.get("status") != "active":
                continue
            if self.tenant is not None and r.get("tenant") != self.tenant:
                continue
            k = r.get("key") or ""
            obj = r.get("object")
            if "::" in k and obj:
                subj, rel = k.split("::", 1)
                edges.append({"subject": subj, "relation": rel, "object": str(obj),
                              "id": r["id"], "text": r.get("text", "")})
                nodes.add(subj); nodes.add(str(obj))
        return {"nodes": sorted(nodes), "edges": edges}

    def subgraph(self, entity: str, hops: int = 1, include_superseded: bool = False) -> dict:
        """MULTI-HOP graph traversal from `entity` (matched as a subject OR an object), up to `hops` edges away —
        the 'connected memories' / multi-hop retrieval a graph memory offers, as a deterministic BFS over the
        (subject, relation, object) edges (no LLM, no graph DB). Returns {'nodes', 'edges'} reachable within hops."""
        g = self.graph(include_superseded=include_superseded)
        adj: dict = {}
        for e in g["edges"]:
            adj.setdefault(e["subject"], []).append(e)
            adj.setdefault(e["object"], []).append(e)
        seen_nodes, seen_edges, edge_ids = {entity}, [], set()
        frontier = {entity}
        for _ in range(max(0, int(hops))):
            nxt = set()
            for node in frontier:
                for e in adj.get(node, []):
                    if e["id"] not in edge_ids:
                        edge_ids.add(e["id"]); seen_edges.append(e)
                    for other in (e["subject"], e["object"]):
                        if other not in seen_nodes:
                            seen_nodes.add(other); nxt.add(other)
            frontier = nxt
            if not frontier:
                break
        return {"nodes": sorted(seen_nodes), "edges": seen_edges}

    def _resolve_key(self) -> bytes:
        """The 32-byte AES key for this store. A raw key is used directly; a passphrase is scrypt-derived
        against the store's salt (from the file header on load, or minted on first save) and cached so scrypt
        isn't re-run on every save."""
        if self._enc_rawkey is not None:
            if self._enc_salt is None:
                self._enc_salt = b"\x00" * 16          # raw key needs no KDF salt; fixed placeholder in the header
            return self._enc_rawkey
        if self._enc_passphrase is not None:
            if self._enc_salt is None:
                self._enc_salt = os.urandom(16)
            if getattr(self, "_enc_derived", None) is None:
                self._enc_derived = _derive_key(self._enc_passphrase, self._enc_salt)
            return self._enc_derived
        raise RuntimeError("no encryption key configured (was the store shredded?)")

    def shred(self) -> dict:
        """CRYPTO-SHRED: destroy the in-memory key so the encrypted store on disk — and EVERY at-rest copy or
        backup of that ciphertext — becomes permanently unreadable (NIST SP 800-88 recognises key-destruction as
        a 'Purge'). Requires an encrypted store; also clears the plaintext records from RAM. Returns a
        content-free receipt. HONEST LIMITS (do not sell as more): it cannot reach plaintext already copied
        elsewhere (another process's RAM, OS swap/hibernation, prior logs), nor any copy that was saved
        UNENCRYPTED before a key was set. It SUPPORTS a right-to-erasure (GDPR Art.17) workflow; it does not by
        itself certify compliance. The ciphertext file is left in place on purpose — the point of crypto-shred is
        that you do NOT have to reach every copy; without the key they are all equally dead."""
        if not self._encrypted:
            raise RuntimeError("shred() requires an encrypted store (encrypt_key= / encrypt_passphrase=)")
        self._enc_rawkey = None
        self._enc_passphrase = None
        self._enc_derived = None
        if self.tenant is not None:
            # shred() destroys the encryption key, which is a property of the whole FILE. Dropping only this
            # tenant's rows while making every other tenant's data unrecoverable is not a coherent operation,
            # and it is not one a tenant should be able to perform on the others.
            raise PermissionError(
                "shred() destroys the store's encryption key for every tenant — call it on the unbound "
                "store, not on a tenant view. To remove one tenant's data use forget()/forget_subject().")
        n = len(self._items)
        self._items = []
        self._mat = None
        self._tok_cache = {}
        return {"shredded": True, "records_dropped": n, "ts": time.time(),
                "note": "encryption key destroyed; the store at rest (and its backups) is now unrecoverable"}

    def reembed(self, only_missing: bool = True, batch: int | None = None) -> dict:
        """Re-embed records that carry no vector, then persist. The EXPLICIT counterpart to the bounded
        embed-recipe guard: when a recipe change finds more than INSPEXIMUS_REALIGN_MAX stale vectors, the guard
        DROPS them (those records fall back to lexical recall) rather than making every open pay one network
        call per record. This is how you deliberately pay that cost once — a foreground call with a count you
        can see — instead of implicitly on a load path that might be a short-lived hook process.
        only_missing=False rebuilds the whole space. `batch` caps how many are done in this call, so a large
        store can be worked through incrementally."""
        if self.embed is None:
            return {"reembedded": 0, "failed": 0, "remaining": 0, "error": "no embedder configured"}
        todo = [r for r in self.items if r.get("text") is not None and (not only_missing or not r.get("vec"))]
        if batch:
            todo = todo[:int(batch)]
        done = failed = 0
        for r in todo:
            try:
                r["vec"] = list(self.embed(r["text"])); done += 1
            except Exception:
                r["vec"] = None; failed += 1
        self._mat = None
        self._save(force=True)
        out = {"reembedded": done, "failed": failed,
               "remaining": sum(1 for r in self.items if r.get("text") is not None and not r.get("vec"))}
        if not self._persist_vectors:
            # _save strips vectors on a RAM-only store, so this warmed the cache for THIS process only.
            out["warning"] = ("persist_vectors=False: vectors are not written to disk, so the next open "
                              "re-embeds again. Open the store with persist_vectors=True to keep them.")
        return out

    def _save(self, force: bool = False):
        if not self.path:
            return
        # Throttle: coalesce frequent writes (e.g. one per recall) so a large store isn't re-serialized
        # on the hot path. force=True (or flush()) bypasses it for shutdown/critical persistence.
        now = time.time()
        if not force and (now - self._last_save) < self._save_min_s:
            self._dirty = True
            return
        try:
            # Persist text/metadata only; the `vec` embedding arrays are a re-derivable in-memory CACHE
            # and are STRIPPED here. json.dumps of N x 768-dim float vectors is huge, slow, and holds the
            # GIL for many seconds - which froze the whole event loop even from a worker thread (the
            # frozen-world bug, 2026-06-20). Vectors stay in self.items (RAM) so recall is unaffected this
            # session; on reload they are re-embedded lazily. Keeps the store file small + the save fast.
            # PERSIST THE WHOLE STORE, NOT THIS HANDLE'S VIEW. `self.items` is TENANT-SCOPED when the
            # handle is bound, so serialising it wrote only the bound tenant's rows and dropped every
            # other tenant's records from the file. Measured on 2.3.1: projA writes 3 records and
            # flushes; a projB-bound handle on the same path then flushes; projA is left with 0. The
            # control -- the same sequence through two UNBOUND handles -- keeps both, so it bites only
            # once a handle is scoped, which is exactly when isolation is supposed to be protecting you.
            #
            # The `items` SETTER already refuses this move ("Route deliberate whole-list writes to
            # `_items`"). The persist path read the same property and was missed: one guarantee, two
            # implementations, one of them unchecked.
            rows = self._items
            slim = list(rows) if self._persist_vectors else \
                [{k: v for k, v in r.items() if k != "vec"} for r in rows]
            # Atomic write: a partial/interleaved write can't corrupt the store (crash- and
            # concurrent-writer-safe — last writer wins, never a torn JSON file).
            # ONE critical section for the check AND the write. Split apart, the window
            # between them is exactly the race the check exists to report.
            with _StoreLock(self.path):
                if self._file_sig is not None and self._stat_sig() != self._file_sig:
                    # Another handle wrote this file since we loaded or last saved it. Writing now replaces its
                    # records with ours -- measured: B's committed, flush()ed record erased by A's next save,
                    # with verify_writes() still True on both sides because each chain was self-consistent.
                    # inspeximus is a SINGLE-WRITER store; refuse rather than lose the other writer's work.
                    raise StoreChangedOnDisk(
                        f"{self.path} changed on disk since this handle loaded it (another process or another "
                        f"Inspeximus() on the same path). Saving would erase its records. Call reload() to merge "
                        f"the two and retry, or give each writer its own store file.")
                # allow_nan=False: a caller-supplied NaN/Infinity was written as a bare literal, which
                # Python re-reads but every STRICT JSON parser (jq, JS, Rust/serde) rejects — so the
                # store silently stopped being valid JSON for the audit bundle and any non-Python reader,
                # while state_digest and verify_writes both still reported healthy.
                data = _dump_store(slim)
                if self._encrypted:                                   # AES-256-GCM at rest (never a plaintext tmp)
                    key = self._resolve_key()                         # sets self._enc_salt on first save
                    payload = _encrypt_blob(key, data.encode("utf-8"), self._enc_salt)
                else:
                    payload = data
                _durable_replace(self.path, payload)
            # record the embed recipe the persisted vectors were made with (only when vectors are actually
            # persisted) so a later open with a different recipe re-embeds instead of silently mismatching.
            # embed_id None means THIS opener has no recipe (e.g. a lexical hook run on a semantic store) —
            # the persisted vectors keep whatever recipe made them, so the sidecar must stay untouched:
            # blanking it here would make the next semantic open see ''->recipe and realign for nothing.
            if self._persist_vectors and getattr(self, "_embedid_path", None) is not None \
                    and self.embed_id is not None:
                try:
                    self._embedid_path.write_text(self.embed_id, encoding="utf-8")
                except Exception as e:
                    self._sidecar_errors['embedid'] = f"{type(e).__name__}: {e}"
            self._file_sig = self._stat_sig()        # our own write is not a conflict
            self._last_save = now
            self._dirty = False
            self._persist_error = None
        except StoreChangedOnDisk:
            raise                                    # the caller must see this one; it is not a disk failure
        except Exception as e:
            # Until 1.54.0 this was `pass`, and it also left _dirty False — so flush() became a no-op and
            # EVERY later write was lost too, while verify_writes() returned True. Measured: five records in
            # memory, one on disk, integrity reported OK. A memory library that loses memories silently and
            # then certifies itself is the worst failure it can have.
            # The hot path still does not raise (one unserialisable meta value must not kill a running app),
            # but the failure is now recorded, retried on the next save, surfaced by verify_writes(), and
            # RAISED by flush() — the call whose whole purpose is "make sure it is on disk".
            self._dirty = True
            self._persist_error = {"at": now, "error": f"{type(e).__name__}: {e}", "path": str(self.path)}

    def flush(self):
        """Force-persist any pending throttled changes (call on clean shutdown).

        RAISES if persistence fails. This is the explicit "make sure it is written" call, so a caller that
        gets no exception is entitled to believe the store is on disk."""
        if self._dirty:
            self._save(force=True)
        if self._persist_error:
            raise OSError(f"inspeximus could not persist to {self._persist_error['path']}: "
                          f"{self._persist_error['error']}")
        if self._sidecar_errors:
            raise OSError("inspeximus could not persist: "
                          + "; ".join(f"{k} -> {v}" for k, v in self._sidecar_errors.items()))


# ── per-type decay priors (the half-life a memory's ranking value decays at, by kind) ──────────
# episodic = events (fade fast); semantic = durable facts (fade slow); procedural = rules/prefs
# (barely fade). Access resets the decay clock (see Inspeximus._effective_value). Tunable.
_HALFLIFE_S = {"episodic": 7 * 86400, "semantic": 180 * 86400, "procedural": 3650 * 86400}
# accrued value at which a repeatedly-recalled EPISODIC memory graduates to semantic (≈16 strong
# recalls from the 1.0 floor); proven-durable, so it should decay on the slow clock, not the fast one.
_GRADUATE_VALUE = 5.0
# Max multiplicative boost for a fully-trusted soft `prefer` filter match (prefer_trust=1 -> x4). At
# prefer_trust=1 this strongly prefers matches (approaching a hard filter) but never DELETES non-matches,
# so a highly-relevant non-match can still surface; prefer_trust=0 -> no boost. Fixed a priori (not tuned
# on the eval) so the measured win isn't an overfit.
_PREFER_GAIN = 3.0
_PROCEDURAL_RE = re.compile(r"\b(always|never|prefers?|rule|workflow|convention|policy|habit|"
                            r"setting|must|should|avoid|don't|do not)\b", re.I)
_SEMANTIC_RE = re.compile(r"\b(means|defined|definition|theorem|law of|equals|consists? of|"
                          r"is a |is an |is the |refers to)\b", re.I)


def _infer_type(text: str) -> str:
    """Conservative type inference: default EPISODIC (fast decay) and only promote on clear markers.
    Callers that know the kind should pass mtype explicitly."""
    t = text or ""
    if _PROCEDURAL_RE.search(t):
        return "procedural"
    if _SEMANTIC_RE.search(t):
        return "semantic"
    return "episodic"


def _negation_clash(a: str, b: str) -> bool:
    """Cheap default: two highly-related statements where exactly one negates. Replace with an
    LLM judge for production — but gate it behind similarity first to keep it O(neighbourhood)."""
    neg = re.compile(r"\b(not|no|never|cannot|can't|doesn't|isn't|won't|fails?|false)\b", re.I)
    return bool(neg.search(a)) != bool(neg.search(b))


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _value_clash(a: str, b: str) -> bool:
    """A VALUE UPDATE: two already-near-duplicate statements that are identical EXCEPT for a differing
    numeric value ('retry limit is 5' -> '... is 12'). This is a state toggle (the fact's value changed),
    NOT a duplicate — so the older should be superseded, not merged. Gated behind the caller's similarity
    check; the tight 'non-numeric remainder is identical' condition keeps genuinely-distinct facts safe."""
    # A value UPDATE keeps the same numbers in the same ORDER except ONE position whose value changed
    # ('timeout is 5' -> 'is 12'; '5 of 10' -> '7 of 10'). Compare numbers POSITIONALLY, not as sets: a
    # set view is ambiguous for ENUMERATED facts because an index can equal another row's value
    # ('step 1 takes 5 min' vs 'step 5 takes 13 min' share the literal 5), which set-math reads as a
    # single change and would silently supersede a coexisting record. (Measured: a 6-item enumerated store
    # lost 5/6 facts under the set rule; 0/6 under this positional rule.)
    na, nb = _NUM.findall(a), _NUM.findall(b)             # ORDERED, not sets
    if not na or len(na) != len(nb):
        return False                                      # no numbers, or different count -> not a single update
    if sum(1 for x, y in zip(na, nb) if x != y) != 1:
        return False                                      # exactly one positional value changed
    # Compare the word-skeleton with ALL numbers stripped: _tokens keeps 3+ digit numbers as tokens
    # (_WORD requires length >= 3), so a multi-digit value ('...is 123') would otherwise spuriously make
    # the skeletons differ and miss the update. Strip numbers first, exactly as before this guard existed.
    return _tokens(_NUM.sub("", a)) == _tokens(_NUM.sub("", b))   # identical apart from the one value


class _TenantBucket:
    """A tenant-scoped view over one of the store-global sidecar dicts (CUSUM, irreversible budget).

    Both sidecars were keyed by the bare canonical source and shared store-wide, while the safety
    guard reading them (`_peers`, from `_tenant_rows()`) was tenant-SCOPED. Tenancy therefore
    DISARMED the guard. Measured 2026-08-15 with a positive control: the AmbiguousSubject collision
    guard fires correctly when both records sit in one tenant, and is silent when the identical pair
    is split across two -- so an attacker spends the victim's irreversible budget to exhaustion
    through the shared bucket. No source-guessing is required on a shared SaaS host, where every
    Notion URL canonicalizes to `notionso`. The counter is monotonic by design (no refund path), and
    the victim's own report shows the spend with no attribution, so it cannot tell it was attacked.
    The same bucket drives monitor()'s CUSUM, so a victim reporting a GOOD outcome can be made to
    raise a poison alarm against itself.

    A view rather than rewritten call sites: the accesses are `B.get(s, 0.0)`, `B[s] = ...` and
    `.items()` in the reports, spread over two methods and two report surfaces. Prefixing inside one
    object keeps every caller honest by construction, which is the same argument the `items` property
    chokepoint already won.

    An UNBOUND handle gets the raw dict: an operator auditing the whole store should see the whole
    store, and existing single-tenant sidecar files keep working unchanged (their keys are bare).
    """

    __slots__ = ("_d", "_p")

    def __init__(self, d: dict, tenant: str):
        self._d = d
        self._p = f"{tenant}\u0000"

    def _k(self, k):
        return self._p + str(k)

    def get(self, k, default=None):
        return self._d.get(self._k(k), default)

    def __getitem__(self, k):
        return self._d[self._k(k)]

    def __setitem__(self, k, v):
        self._d[self._k(k)] = v

    def __delitem__(self, k):
        del self._d[self._k(k)]

    def __contains__(self, k):
        return self._k(k) in self._d

    def __iter__(self):
        n = len(self._p)
        return (k[n:] for k in self._d if k.startswith(self._p))

    def __len__(self):
        return sum(1 for _ in iter(self))

    def keys(self):
        return list(iter(self))

    def items(self):
        n = len(self._p)
        return [(k[n:], v) for k, v in self._d.items() if k.startswith(self._p)]

    def values(self):
        return [v for _, v in self.items()]

    def setdefault(self, k, default=None):
        return self._d.setdefault(self._k(k), default)


class _TenantView:
    """A logically-isolated view over a shared Inspeximus store (see Inspeximus.for_tenant). It carries its OWN `tenant`
    but forwards EVERY other attribute to the parent store, so all data + config are shared by reference (one
    items list, one file, one cache) while the tenant-sensitive operations run bound to THIS view's tenant.

    Implementation: the handful of tenant-aware Inspeximus methods are re-bound onto the view (so `self.tenant` inside
    them is the VIEW's tenant), and their tenant-aware internal helpers (_supersede_by_key, _tenant_rows) are
    re-bound too; everything else (`items`, `_save`, `_qvec`, `embed`, config flags, ...) resolves to the parent
    via __getattr__, so reads/writes land on the shared store. Non-tenant methods (credit, verify_*, anchor, ...)
    are used as-is on the parent through __getattr__ and are unaffected by tenancy.

    SINCE 1.90.0 the same view carries a second, independent scoping dimension: `agent` (see
    Inspeximus.as_agent), the agent-to-agent read ACL. It is the same class deliberately -- the two
    dimensions compose (tenant first, then the grant allow-list), and a second view class would have meant
    a second copy of the classification below, which is the machinery that stops a newly added method from
    leaking by default. Either dimension may be None."""
    __slots__ = ("_parent", "tenant", "agent")

    def __init__(self, parent: "Inspeximus", tenant: str | None, agent: str | None = None):
        object.__setattr__(self, "_parent", parent)
        object.__setattr__(self, "tenant", tenant)
        object.__setattr__(self, "agent", agent)

    #: Methods that are genuinely STORE-LEVEL — they operate on the shared file, index, receipt chain or
    #: config, where "this tenant's slice" is not a meaningful scope. Passing these through is deliberate.
    #: Anything NOT here and not rebound below raises, so a method added to Inspeximus tomorrow cannot leak
    #: by default. That is the whole point: the previous version forwarded EVERYTHING, and 54 of 79 public
    #: methods reached the parent as tenant=None (admin) — `A.history(B_key)` returned B's plaintext.
    _STORE_LEVEL = frozenset({
        "flush", "reload", "reembed", "anchor", "witness",
        "verify_writes", "verify_consistency", "verify_witness", "verify_cosigned_anchor",
        "verify_attribution", "register_erasure_target", "explain_growth",
        # `verify_attestations` is store-level for a REASON, not by resemblance to its neighbours above.
        # 2.4.0 binds the tenant into the signed message so that a record MOVED BETWEEN TENANTS stops
        # verifying. Scoped to one tenant that check would be ONE-SIDED -- not blind, which is what an
        # earlier version of this comment claimed and measurement disproved: the DESTINATION slice does
        # fail the moved row, identically to a store-level run. It is the SOURCE that goes quiet, because
        # the row simply leaves its slice and the tenant reads clean. So detection would depend on
        # somebody running the check for wherever the row landed -- including the untagged `tenant=None`
        # slice, which no tenant-bound handle covers at all. Store-level is the only run guaranteed to
        # cover every destination. What it returns is ids, key prefixes and counts -- never record text --
        # which is the same bar `merkle_root`/`inclusion_proof` are listed under below. It is still swept
        # by the tenant and agent leak tests, not exempted.
        "verify_attestations",
        # `audit_the_audits` grades the LIBRARY's surfaces, not a tenant's slice: it corrupts a
        # temporary copy of the whole store and returns verdicts, counts and the surfaces' own
        # problem strings -- never record text. Scoped to one tenant it would answer a question
        # nobody asked ("can these checks fail on this slice"), while the honest question is whether
        # they can fail at all on this file. Swept by the tenant/agent leak tests like the rest.
        "audit_the_audits",
        # Store-scoped by definition: all three invariants are facts about the STORE rather than
        # about any record, which is precisely @Stratogain's argument for why the layer exists.
        "admissibility_preconditions",
        "detect_split_view", "check_self_narration",
        "revert_capability",
        # `believed_at` is NOT store-level: it is a read surface that returns a record's plaintext as of a
        # timestamp, so it is rebound on the view below and scoped like every other read.
        # A pure static scorer: it reads the record dict handed to it and touches no rows, so it has no
        # tenant to get wrong. (The methods that SELECT the records it scores -- close_session,
        # session_context, _session_entries -- are rebound on the view, not listed here.)
        "session_salience",
        # The RFC 6962 transparency surface. Store-level on purpose and after checking what the leaf
        # actually carries: `_chain_core` is {seq, ts, memory_id, commit, prev}, where `commit` is a
        # dict of SHA-256 digests -- ids, counters and hashes, never record text. A transparency log
        # is whole-log by construction (that is what makes an inclusion proof mean anything, and why
        # CT logs are public), and `anchor` -- which commits to the same history -- has been listed
        # here all along. `verify_inclusion` is a pure static verifier over a bundle handed to it.
        # These are still SWEPT by the tenant/agent leak tests via _ARGS, not exempted from them:
        # the classification says "no tenant to get wrong", the sweep is what proves it.
        "merkle_root", "inclusion_proof", "merkle_consistency_proof", "verify_inclusion",
    })

    def __getattr__(self, name):
        if name.startswith("_") or name in _TenantView._STORE_LEVEL:
            return getattr(self._parent, name)       # private helpers + shared-store operations
        attr = getattr(self._parent, name, None)
        if callable(attr) and not name.startswith("__"):
            raise AttributeError(
                f"{name}() is not classified for tenant views. It would run on the shared store as tenant="
                f"None (admin) and could read or delete another tenant's records. Add it to the rebound "
                f"surface on _TenantView (and make it use self._tenant_rows()), or to _STORE_LEVEL if it is "
                f"genuinely store-wide.")
        return getattr(self._parent, name)           # data attributes (items, config, caches) stay shared

    def __setattr__(self, name, value):     # config writes go to the shared parent (the scopes are slot-local)
        if name in ("tenant", "agent"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._parent, name, value)

    @property
    def items(self) -> list:
        """Scoped to THIS view's tenant. Without it, `view.items` resolved through __getattr__ to the parent's
        property, which evaluates against the PARENT's tenant (None = admin) and handed back every tenant's
        raw records — defeating the allow-list entirely. Measured before this."""
        return Inspeximus.items.fget(self)

    @property
    def _items(self) -> list:
        return self._parent._items              # the real, shared list (writes and audits go here)

    def _stamp(self, *a, **k):
        """MUST be rebound, not forwarded. `__getattr__` sends private names straight to the PARENT,
        so a forwarded `_stamp` runs with the parent as `self` -- tenant None -- and every internal
        marker it writes lands unscoped. Measured while adding it: a tenant's session digest came back
        EMPTY, because close_session() had stamped that digest outside the tenant entirely. Rebinding
        keeps `self` the VIEW, so the privileged write goes through the view's own remember() and stays
        where it belongs. The private-name forward is documented at the top of this class; it is still
        the trap it says it is."""
        return Inspeximus._stamp(self, *a, **k)

    def for_tenant(self, tenant: str):           # re-scope from the same shared store
        # KEEP the agent binding. Re-scoping the tenant must never drop the ACL: `view.for_tenant(t)` that
        # returned an operator handle would be a privilege ESCALATION reachable from inside the sandbox.
        return _TenantView(self._parent, str(tenant), agent=self.agent)

    def as_agent(self, agent: str):              # bind (or re-bind) the ACL dimension, keeping the tenant
        return _TenantView(self._parent, self.tenant, agent=Inspeximus._check_agent_id(agent))

    # tenant-sensitive surface: rebound so `self` is the VIEW (its tenant), state stays the parent's
    def apply_retention(self, *a, **k):       return Inspeximus.apply_retention(self, *a, **k)
    def shred(self, *a, **k):                 return Inspeximus.shred(self, *a, **k)
    # ratify / admit / classify_reversion were classified _STORE_LEVEL and are not: each reads record
    # rows and returns or mutates record content. Measured by execution 2026-08-15 -- acme.ratify(globex_id,
    # "reproduction", "mallory") appended to globex's row on disk and lifted its grade to "verified";
    # acme.admit(victim_text) returned globex's id and similarity, and an admit that SUCCEEDED from a
    # tenant view wrote its row with tenant=None, so the writer could never see its own memory again;
    # classify_reversion returned another tenant's current AND superseded object values.
    # revert_intent / restore_intent were _STORE_LEVEL as well, so they were parent-bound TWICE over
    # -- the classification and the un-rebound helper both. restore_intent embeds the matching record's
    # id in the string it mints, so a non-empty `@<id>` segment CONFIRMS a guessed object value in
    # another tenant and an empty one denies it. Measured: guessing 120000 and 250000 both returned an
    # id; 999999 and 42 returned none.
    def revert_intent(self, *a, **k):       return Inspeximus.revert_intent(self, *a, **k)
    def restore_intent(self, *a, **k):      return Inspeximus.restore_intent(self, *a, **k)
    # The sidecar STATE accessors. Rebinding _TenantBucket inside them was not enough: both names are
    # private, so __getattr__ forwarded them to the parent and they ran with tenant=None -- the scoping
    # branch was live code that a tenant view could never reach. The same private-surface defect as
    # _route_chain, one floor up, found by re-running the attack after the "fix".
    def _cusum_state(self, *a, **k):        return Inspeximus._cusum_state(self, *a, **k)
    def _budget_state(self, *a, **k):       return Inspeximus._budget_state(self, *a, **k)
    # The tombstone writer and reader. THIRD time in one day that a private name defeated a scoping
    # fix: the `if self.tenant is not None` stamp inside _emit_tombstone was live code a tenant view
    # could never reach, exactly as with _route_chain and _budget_state. The structural test below
    # now keys on `self.tenant` itself rather than on which collection a helper happens to read,
    # because "reads records" and "reads a sidecar" were both too narrow -- the invariant is that a
    # private method making a TENANT decision must run with the tenant, and there is no other way to
    # get one.
    def _emit_tombstone(self, *a, **k):     return Inspeximus._emit_tombstone(self, *a, **k)
    def _visible_tombstones(self, *a, **k): return Inspeximus._visible_tombstones(self, *a, **k)
    # THE ONE THAT DELETES, and the seventh instance of this defect found in one day. It was not
    # missed -- I DECLARED it store-wide in the structural test, reasoning that capacity is a property
    # of the file. Measured consequence of that reasoning: on a shared store at capacity=10, globex
    # writing five of its OWN records hard-deleted five of acme's, via forget(), with a tombstone that
    # names no cause, and acme was told nothing. A helper that DELETES across a security boundary is
    # not store-wide however the cap is defined.
    #
    # SEMANTICS CHANGE, stated because it is a decision and not a side effect: once view-bound,
    # `self.items` is tenant-scoped, so `capacity` becomes PER-TENANT on a bound handle rather than
    # per-store. A shared cap that any tenant can spend on any other tenant's records is not a cap,
    # it is a cross-tenant delete primitive. An unbound (operator) handle is unchanged.
    def _evict_to_capacity(self, *a, **k): return Inspeximus._evict_to_capacity(self, *a, **k)
    # Reached today only through rebound public callers, so `_tenant_rows()` is already scoped when it
    # runs and cross-tenant erasure does NOT reproduce (measured: acme erasing a shared subject name
    # took its own record and left both of globex's). Rebound anyway, because that safety is a
    # property of the current call graph and not of this method -- one new caller on the private path
    # and it resolves a subject against every tenant's rows, on the DELETE path.
    def _resolve_subject(self, *a, **k):    return Inspeximus._resolve_subject(self, *a, **k)
    def ratify(self, *a, **k):              return Inspeximus.ratify(self, *a, **k)
    def admit(self, *a, **k):               return Inspeximus.admit(self, *a, **k)
    def classify_reversion(self, *a, **k):  return Inspeximus.classify_reversion(self, *a, **k)
    def grade(self, *a, **k):                 return Inspeximus.grade(self, *a, **k)
    def erasure_certificate(self, *a, **k):   return Inspeximus.erasure_certificate(self, *a, **k)
    def sleep(self, *a, **k):           return Inspeximus.sleep(self, *a, **k)
    def check_sources(self, *a, **k):  return Inspeximus.check_sources(self, *a, **k)
    # Rebound rather than passed through: the report NAMES KEYS (in `example`, for the folds that would
    # merge them), and a key is exactly the thing that must not cross a tenant boundary. `declared` is
    # store-wide -- one writer, one policy -- but `measured` is a property of the rows in scope.
    def identifier_contract(self, *a, **k):  return Inspeximus.identifier_contract(self, *a, **k)
    def remember(self, *a, **k):        return Inspeximus.remember(self, *a, **k)
    def history(self, *a, **k):             return Inspeximus.history(self, *a, **k)
    def provenance(self, *a, **k):          return Inspeximus.provenance(self, *a, **k)
    def as_of(self, *a, **k):               return Inspeximus.as_of(self, *a, **k)
    def why_recalled(self, *a, **k):        return Inspeximus.why_recalled(self, *a, **k)
    def credit(self, *a, **k):              return Inspeximus.credit(self, *a, **k)
    def memory_report(self, *a, **k):       return Inspeximus.memory_report(self, *a, **k)
    def value_by_cohort(self, *a, **k):     return Inspeximus.value_by_cohort(self, *a, **k)
    def index_coherence(self, *a, **k):     return Inspeximus.index_coherence(self, *a, **k)
    # Tenant-scoped: it reads `_tenant_rows()`, so a view sees only its own decisions. Forwarding it
    # unclassified would have run it against the shared store as tenant=None -- the leak the
    # classification test exists to stop, and it caught this one.
    def decisions_in_force(self, *a, **k): return Inspeximus.decisions_in_force(self, *a, **k)
    def supersession_report(self, *a, **k): return Inspeximus.supersession_report(self, *a, **k)
    def state_digest(self, *a, **k):        return Inspeximus.state_digest(self, *a, **k)
    # NOT REBOUND UNTIL NOW, AND SHIPPED THAT WAY. `state_digest` was rebound and `witness`, which
    # wraps it, was not -- so on a tenant view the whole method ran PARENT-bound and a tenant's
    # hydration receipt attested the WHOLE store: measured, acme.witness() reported records=2 and
    # the root digest on a store where acme owns one record. Two costs, and the second is worse:
    # it leaks how much other tenants hold, and it makes every other tenant's write flip this
    # tenant's verify_witness to invalid -- a false alarm on a shared store is how a real one gets
    # ignored. Found while adding bind_sources, which inherited it.
    def witness(self, *a, **k):             return Inspeximus.witness(self, *a, **k)
    def verify_witness(self, *a, **k):      return Inspeximus.verify_witness(self, *a, **k)
    # REBOUND THE DAY IT WAS WRITTEN, by the sweep rather than by me remembering. `_bind_sources`
    # reads records, and a private helper reached through `__getattr__` runs PARENT-bound -- so a
    # tenant's hydration witness would have pinned every tenant's sources, leaking their file paths
    # into an artifact meant to be handed onward. No exception, no failing unit test, just rows from
    # the wrong slice: exactly the class the structural sweep exists for, catching a method twenty
    # minutes old.
    def _bind_sources(self, *a, **k):       return Inspeximus._bind_sources(self, *a, **k)
    def erasure_report(self, *a, **k):      return Inspeximus.erasure_report(self, *a, **k)
    def governance_report(self, *a, **k):   return Inspeximus.governance_report(self, *a, **k)
    def forget(self, *a, **k):              return Inspeximus.forget(self, *a, **k)
    def retract_lineage(self, *a, **k):     return Inspeximus.retract_lineage(self, *a, **k)
    def rederive(self, *a, **k):            return Inspeximus.rederive(self, *a, **k)
    def revert(self, *a, **k):              return Inspeximus.revert(self, *a, **k)
    def submit_revert(self, *a, **k):       return Inspeximus.submit_revert(self, *a, **k)
    def revert_now(self, *a, **k):          return Inspeximus.revert_now(self, *a, **k)
    def revert_challenge(self, *a, **k):    return Inspeximus.revert_challenge(self, *a, **k)
    def restore(self, *a, **k):             return Inspeximus.restore(self, *a, **k)
    def restore_now(self, *a, **k):         return Inspeximus.restore_now(self, *a, **k)
    def slash(self, *a, **k):               return Inspeximus.slash(self, *a, **k)
    def monitor(self, *a, **k):             return Inspeximus.monitor(self, *a, **k)
    def spend_irreversible(self, *a, **k):  return Inspeximus.spend_irreversible(self, *a, **k)
    def influence_gate_report(self, *a, **k): return Inspeximus.influence_gate_report(self, *a, **k)
    def irreversible_budget_report(self, *a, **k): return Inspeximus.irreversible_budget_report(self, *a, **k)
    def erasure_audit(self, *a, **k):       return Inspeximus.erasure_audit(self, *a, **k)
    def convergence_report(self, *a, **k):  return Inspeximus.convergence_report(self, *a, **k)
    def propagate_outcome(self, *a, **k):   return Inspeximus.propagate_outcome(self, *a, **k)
    def recall_iterative(self, *a, **k):    return Inspeximus.recall_iterative(self, *a, **k)
    # The two-phase inversion of the same lever (the MCP/CLI surface). Bound here for the same reason
    # recall_iterative is: reached through __getattr__ they would run PARENT-bound and the inner recall()
    # would read the parent's tenant, i.e. every tenant's records.
    def recall_iterative_start(self, *a, **k):    return Inspeximus.recall_iterative_start(self, *a, **k)
    def recall_iterative_followup(self, *a, **k): return Inspeximus.recall_iterative_followup(self, *a, **k)
    def recall(self, *a, **k):          return Inspeximus.recall(self, *a, **k)
    def forget_subject(self, *a, **k):  return Inspeximus.forget_subject(self, *a, **k)
    def forget_pii(self, *a, **k):      return Inspeximus.forget_pii(self, *a, **k)
    def pii_report(self, *a, **k):      return Inspeximus.pii_report(self, *a, **k)
    def remember_dedup(self, *a, **k):  return Inspeximus.remember_dedup(self, *a, **k)
    def consolidate(self, *a, **k):     return Inspeximus.consolidate(self, *a, **k)
    def consolidate_clusters(self, *a, **k): return Inspeximus.consolidate_clusters(self, *a, **k)
    def contradictions(self, *a, **k):  return Inspeximus.contradictions(self, *a, **k)
    def check_conflict(self, *a, **k):  return Inspeximus.check_conflict(self, *a, **k)
    def verify_claim(self, *a, **k):    return Inspeximus.verify_claim(self, *a, **k)
    def recommit(self, *a, **k):        return Inspeximus.recommit(self, *a, **k)
    def selection_integrity(self, *a, **k): return Inspeximus.selection_integrity(self, *a, **k)
    def _cluster_active(self, *a, **k): return Inspeximus._cluster_active(self, *a, **k)
    def _supersede_by_key(self, *a, **k): return Inspeximus._supersede_by_key(self, *a, **k)
    # REBOUND, like _tenant_rows beside it. Private names fall through __getattr__ to the parent, whose
    # tenant is None, so the un-rebound version handed a tenant view every tenant's rows -- and
    # memory_report(), its first caller, reported 2 records to a tenant that owns 1. Caught by
    # test_aggregate_reports_do_not_count_another_tenant, which is why that test exists.
    def _content_rows(self, *a, **k):   return Inspeximus._content_rows(self, *a, **k)
    # MOVED OUT of _STORE_LEVEL. It reads record TEXT off `self.items`, so passing it through ran it on the
    # parent (tenant/agent None = operator) and returned another scope's plaintext. The tenant sweep did not
    # catch it because its fixture puts the secret on a SUPERSEDED record while believed_at returns the
    # latest-asserted value -- a check that never sees its target, reporting safe. Found by the agent-grant
    # sweep, whose fixture holds one record; the fix closes it for tenants and agents alike.
    def believed_at(self, *a, **k):     return Inspeximus.believed_at(self, *a, **k)
    # ACL surface: rebound for the same reason as the tenant surface. Reaching these through __getattr__
    # would run them on the PARENT, whose tenant/agent are None (operator) -- so `alice.grant(...)` would
    # have been recorded as an operator grant over every tenant's records.
    def grant(self, *a, **k):           return Inspeximus.grant(self, *a, **k)
    def revoke(self, *a, **k):          return Inspeximus.revoke(self, *a, **k)
    def grants(self, *a, **k):          return Inspeximus.grants(self, *a, **k)
    def grant_log(self, *a, **k):       return Inspeximus.grant_log(self, *a, **k)
    def can_read(self, *a, **k):        return Inspeximus.can_read(self, *a, **k)
    def _acl_visible(self, *a, **k):    return Inspeximus._acl_visible(self, *a, **k)
    def _acl_grants_for(self, *a, **k): return Inspeximus._acl_grants_for(self, *a, **k)
    def _acl_match(self, *a, **k):      return Inspeximus._acl_match(self, *a, **k)
    def _acl_key(self, *a, **k):        return Inspeximus._acl_key(self, *a, **k)
    def _acl_resolve_by(self, *a, **k): return Inspeximus._acl_resolve_by(self, *a, **k)
    def _acl_write(self, *a, **k):      return Inspeximus._acl_write(self, *a, **k)
    def _acl_note_problem(self, *a, **k): return Inspeximus._acl_note_problem(self, *a, **k)
    def candidates(self, *a, **k):      return Inspeximus.candidates(self, *a, **k)
    def provisional(self, *a, **k):     return Inspeximus.provisional(self, *a, **k)
    def confirm(self, *a, **k):         return Inspeximus.confirm(self, *a, **k)
    def discard_provisional(self, *a, **k): return Inspeximus.discard_provisional(self, *a, **k)
    def promote_candidate(self, *a, **k): return Inspeximus.promote_candidate(self, *a, **k)
    def discard_candidate(self, *a, **k): return Inspeximus.discard_candidate(self, *a, **k)
    def observe(self, *a, **k):         return Inspeximus.observe(self, *a, **k)
    def reopened(self, *a, **k):        return Inspeximus.reopened(self, *a, **k)
    def resolve_reopened(self, *a, **k): return Inspeximus.resolve_reopened(self, *a, **k)
    def support_challenge_for(self, *a, **k): return Inspeximus.support_challenge_for(self, *a, **k)
    # PRIVATE HELPERS THAT READ RECORDS, rebound so they run scoped. `__getattr__` forwards every
    # underscore name to the PARENT, which is tenant=None -- so a public method rebound here could still
    # read the whole store through an un-rebound helper it calls. Measured 2026-08-15 by executing it:
    # `route()` is rebound, but `_route_key`/`_route_chain` were not, so acme.route("go back to the very
    # first payout") returned globex's value AND wrote it into acme; in reverse, a value acme planted on
    # globex's key became globex's own current value on its next route(). `revert_challenge` is rebound,
    # but `_current_active_id` was not, so it handed out another tenant's record id.
    #
    # The public surface already fails CLOSED here (an unclassified public name raises). The private
    # surface failed OPEN, one line above it in the same __getattr__. `test_no_unrebound_private_reads_
    # records` is what keeps these two halves in step; add a helper there and it goes red.
    #
    # NOT rebound, deliberately: _save / _load_from_disk / _atomic_write / _evict_to_capacity are
    # properties of the whole store file -- a tenant does not have its own capacity or its own bytes.
    def _current_active_id(self, *a, **k): return Inspeximus._current_active_id(self, *a, **k)
    def _route_chain(self, *a, **k): return Inspeximus._route_chain(self, *a, **k)
    def _route_key(self, *a, **k): return Inspeximus._route_key(self, *a, **k)
    def _retired_values(self, *a, **k): return Inspeximus._retired_values(self, *a, **k)
    def _latest_superseded_object(self, *a, **k): return Inspeximus._latest_superseded_object(self, *a, **k)
    def _narrow_to_subject(self, *a, **k): return Inspeximus._narrow_to_subject(self, *a, **k)
    def _erasure_collisions(self, *a, **k): return Inspeximus._erasure_collisions(self, *a, **k)
    def _erasure_preview(self, *a, **k): return Inspeximus._erasure_preview(self, *a, **k)
    def _resolve_read_conflicts(self, *a, **k): return Inspeximus._resolve_read_conflicts(self, *a, **k)
    def _nonce_consumed(self, *a, **k): return Inspeximus._nonce_consumed(self, *a, **k)
    def _current_active(self, *a, **k): return Inspeximus._current_active(self, *a, **k)
    def _tenant_rows(self, *a, **k):    return Inspeximus._tenant_rows(self, *a, **k)
    # Later tenant-sensitive additions. Reached through __getattr__ these run PARENT-bound, so `self.tenant`
    # is the parent's (normally None): remember_decision/distill_and_remember wrote records with NO tenant
    # stamp (visible to every other view), graph/subgraph returned EVERY tenant's edges, and route()'s delete
    # id-selection matched the parent's tenant. Any new tenant-aware method belongs in this list.
    def remember_decision(self, *a, **k): return Inspeximus.remember_decision(self, *a, **k)
    def distill_and_remember(self, *a, **k): return Inspeximus.distill_and_remember(self, *a, **k)
    def graph(self, *a, **k):           return Inspeximus.graph(self, *a, **k)
    def subgraph(self, *a, **k):        return Inspeximus.subgraph(self, *a, **k)
    def route(self, *a, **k):           return Inspeximus.route(self, *a, **k)
    # Session boundary + cross-session digest. Every one of these reads or writes tenant-scoped rows, so
    # reached through __getattr__ they would run PARENT-bound and a tenant's session digest would be
    # assembled from — and stored beside — every other tenant's records.
    def open_session(self, *a, **k):    return Inspeximus.open_session(self, *a, **k)
    def close_session(self, *a, **k):   return Inspeximus.close_session(self, *a, **k)
    def session_context(self, *a, **k): return Inspeximus.session_context(self, *a, **k)
    def _session_window(self, *a, **k): return Inspeximus._session_window(self, *a, **k)
    def _session_entries(self, *a, **k): return Inspeximus._session_entries(self, *a, **k)
    def _session_digests(self, *a, **k): return Inspeximus._session_digests(self, *a, **k)
    def _session_correctors(self, *a, **k): return Inspeximus._session_correctors(self, *a, **k)


# --------------------------------------------------------------------------------------------------------------
# Ready-made write-path extractors (set `m.extractor = ...`). The extractor derives a (key, object) from free
# text so supersession/echo_guard/revert engage WITHOUT the caller passing an explicit key. inspeximus ships two:
#   - regex_extractor : DETERMINISTIC, no LLM, no dependency — keeps the zero-LLM-on-write moat. Conservative
#     by design (returns None unless a clear subject/relation pattern matches), because a mis-derived key
#     mis-supersedes; a returned None just falls back to a plain append.
#   - make_llm_extractor(call_fn) : OPT-IN factory. Wraps YOUR llm(prompt)->str call to extract (key, object).
#     This PUTS AN LLM ON THE WRITE PATH — you trade determinism/zero-cost for auto-capture of unstructured text.
# Both are fail-open (Inspeximus.remember swallows extractor exceptions and appends the raw text).

# CONVERSATIONAL SURFACE FORMS (1.90.0). The shipped patterns keyed a clean declarative sentence and
# nothing else, which is what README's "honest scope" paragraph admits. Measured on a 15-chain
# conversational fixture (benchmarks/chain_binding/) BEFORE any change: 2 of 15 correction chains bound,
# and the store therefore degenerated to keep-everything on exactly the input the product is sold for.
# Four surface facts caused it, none of them about meaning:
#   1. every pattern was anchored at ^ against the WHOLE string, so any leading clause killed the match
#      ("Dana left, so my manager is Priya now" -> no key at all; the subject class has no comma).
#   2. the OBJECT side already got a leading-adverb strip and the SUBJECT side got none, so "actually my
#      title" / "so my current title" / "my title" are three keys that can never meet.
#   3. first person was not canonical: "my title is X" keyed on 'my title' while "I work at Y" keyed on
#      nothing, so no first-person chain could survive a change of phrasing.
#   4. the non-referring guard read `i` but not `i'm`, so "I'm now in the PST timezone" and "I'm now the
#      on-call engineer" BOTH keyed on "i'm" and retired each other -- a live data-loss path, not a miss.
# Everything below is closed-list surface normalisation: no lexicon of world knowledge, no model, no
# dependency. Where a chain needs world knowledge to bind ("I'm a Principal Engineer now" is a *title*),
# it deliberately still returns None -- see regex_extractor's docstring for that boundary.

# A subject: no comma, so a clause can never be swallowed. The lower bound is ONE character, not two --
# at two, the single most common conversational subject in English, "I", could not match any pattern, and
# the bare-copula fallback then consumed the verb instead and produced the key "i am".
_EX_S = r"[A-Za-z0-9 ._/@'-]{1,60}?"
_EX_R = r"[A-Za-z0-9 ._-]{2,40}?"             # a relation noun phrase
_EX_BE = r"(?:is|am|are|was|were)"
# The copula, plus the change verbs that occupy the same slot. "changed to"/"moved to"/"switched to" are
# grammatically copular here ("my email changed to X" == "my email is now X"), which is why they belong in
# the link and not in a separate pattern.
_EX_LINK = (r"(?:is|am|are|was|were|=|:|now|became|changed\s+to|moved\s+to|switched\s+to|"
            r"updated\s+to|set\s+to|went\s+to)")

# Discourse markers that can only ever be lead-ins, never the subject of the fact. Split in two because
# `correction`/`update`/`note` are also ordinary nouns ("note taking is my hobby"), so those are stripped
# ONLY when punctuation follows; the pure adverbs and interjections need no such proof.
_EX_MARK_ADV = ("actually", "so", "well", "anyway", "anyhow", "oh", "wait", "hmm", "um", "uh", "btw",
                "honestly", "basically", "sorry", "right", "yeah", "yep", "ok", "okay", "also", "then",
                "and", "but", "plus", "fyi", "by the way", "just so you know", "to be clear",
                "to clarify", "for the record", "heads up")
_EX_MARK_NOUN = ("correction", "update", "note", "edit", "ps", "quick update")
_EX_LEAD = re.compile(
    r"^\s*(?:(?:" + "|".join(_EX_MARK_ADV) + r")\b[\s]*[:,;.!-]*\s*"
    r"|(?:" + "|".join(_EX_MARK_NOUN) + r")\b\s*[:,;-]+\s*)+", re.I)

# Trailing time adverbials. "my manager is Priya now" and "my manager is Priya" are one fact; the trailing
# word is tense, not value. Stripping is TRIED, never forced: "the meeting is today" would otherwise lose
# its whole object, so a stripped candidate that fails to parse falls back to the unstripped one.
_EX_TRAIL = re.compile(
    r"[\s,;-]+(?:now|today|currently|already|again|these\s+days|nowadays|going\s+forward|"
    r"from\s+now\s+on|at\s+the\s+moment|right\s+now|yesterday|"
    r"last\s+(?:week|month|year|night)|this\s+(?:week|month|year|morning|afternoon)|"
    r"as\s+of\s+\S+|since\s+\S+)\s*[.!]?\s*$", re.I)

# `'m` and `'re` expand unambiguously. `'s` deliberately does NOT: it is the possessive as often as it is
# the copula, and guessing wrong rewrites the subject.
_EX_CONTRACT = ((re.compile(r"\bI'm\b", re.I), "I am"),
                (re.compile(r"\b(\w+)'re\b", re.I), r"\1 are"))

# Modifiers that mark the CURRENT statement of a relation, so "my current title" / "my new email" name the
# same relation as "my title" / "my email". `former`, `old`, `previous`, `original`, `ex-` are POINTEDLY
# absent: they name a DIFFERENT, historical fact, and folding them in would let "my former employer is
# Acme" retire "my employer is Globex". That asymmetry is the whole reason this is a closed list.
_EX_CURRENT_MOD = re.compile(r"^(?:new|newest|updated|current|latest|official|present|existing)\s+", re.I)

# Relational verbs that lexicalise a stable relation. Deliberately tiny and explicit: two frames, the two
# that every personal-fact schema starts from. An unlisted verb falls through to None rather than guessing.
_EX_VERB_LEMMA = {"live": "live", "lives": "live", "lived": "live",
                  "work": "work", "works": "work", "worked": "work"}
_EX_VERB_FRAMES = {("live", "in"): "residence", ("live", "at"): "residence",
                   ("work", "at"): "employer", ("work", "for"): "employer"}

_EX_REL = re.compile(r"^(?P<subject>" + _EX_S + r")(?:'s|s')\s+(?P<rel>" + _EX_R + r")\s+"
                     + _EX_LINK + r"\s+(?P<obj>.+?)\s*\.?\s*$", re.I)          # "X's Y is Z"
_EX_OF = re.compile(r"^the\s+(?P<rel>" + _EX_R + r")\s+(?:of|for)\s+(?P<subject>" + _EX_S + r")\s+"
                    + _EX_LINK + r"\s+(?P<obj>.+?)\s*\.?\s*$", re.I)           # "the Y of/for X is Z"
# "I'm on the Payments team" / "I'm in the CET timezone" / "I moved to the Platform team": the relation is
# the head noun of the complement and the value is its modifier. Runs BEFORE the first-person possessive
# pattern, or "my sister is in the Berlin office" would key on 'self::sister' and retire "my sister is Anna".
_EX_HEAD = re.compile(
    r"^(?P<subject>" + _EX_S + r")\s+(?:" + _EX_BE + r"\s+(?:now\s+)?(?:in|on|at|with)"
    r"|moved\s+to|switched\s+to|transferred\s+to|joined)\s+the\s+"
    r"(?P<obj>[A-Za-z0-9 ._/@'-]{1,40}?)\s+(?P<rel>[A-Za-z][A-Za-z-]{1,30})\s*\.?\s*$", re.I)
_EX_MINE = re.compile(r"^(?:my|our)\s+(?P<rel>" + _EX_R + r")\s+" + _EX_LINK +
                      r"\s+(?P<obj>.+?)\s*\.?\s*$", re.I)                      # "my Y is Z"
# "we switched the analytics store to ClickHouse": the thing being changed is the NP, not the agent, so the
# key comes from the NP. The NP must be DEFINITE (my/our/the/possessive) -- a bare one is not a reference.
_EX_CHANGE = re.compile(
    r"^" + _EX_S + r"\s+(?:changed|switched|moved|updated|set|renamed)\s+"
    r"(?P<np>(?:my|our|the)\s+[A-Za-z0-9 ._-]{2,40}?"
    r"|[A-Za-z0-9 ._-]{2,40}?(?:'s|s')\s+[A-Za-z0-9 ._-]{2,40}?)\s+to\s+(?P<obj>.+?)\s*\.?\s*$", re.I)
_EX_VERB = re.compile(r"^(?P<subject>" + _EX_S + r")\s+(?P<verb>lives?|lived|works?|worked)\s+"
                      r"(?P<prep>in|at|for)\s+(?P<obj>.+?)\s*\.?\s*$", re.I)
_EX_IS = re.compile(r"^(?:the\s+)?(?P<subject>" + _EX_S + r")\s+" + _EX_LINK +
                    r"\s+(?P<obj>.+?)\s*\.?\s*$", re.I)                        # "X is Z" (bare copula)


# NON-REFERRING SUBJECTS (2026-07-20). A key is only meaningful if its subject IDENTIFIES something. On
# natural prose these patterns otherwise fire on pronouns, expletives and interrogatives — "It is important
# to ...", "There are many ...", "These are just a few ...", "What is ...?" — producing the keys 'it',
# 'there', 'these', 'what', which then COLLIDE across completely unrelated sentences and make supersession
# retire live records. Measured on the MemOps conversational corpus BEFORE this guard: 103 supersessions in
# one 3.7k-sentence transcript, 83% of them driven by such a key, retiring e.g. a UBI-economics sentence
# because a London-landmark sentence shared the subject 'what'. That is silent data loss in a feature the
# README advertises for free text. Refusing to key these falls back to the extractor's documented
# behaviour (return None -> plain append), so nothing that worked before changes.
_EX_NONREFERRING = frozenset("""
it he she they them we us you i this that these those there here one ones someone somebody anyone anybody
everyone everybody something anything nothing everything what who whom whose where when why which how
each both all some any none other others another such
his her hers its their theirs mine ours yours
i'm im it's that's there's he's she's here's what's who's we're they're you're
""".split())

# A subject whose FIRST word is a quantifier, demonstrative or interrogative determiner does not identify
# an entity either -- "both approaches", "some of the tests", "one thing I noticed", "this kind of thing",
# "whose responsibility". The shipped guard only inspected the first and last token AS A WHOLE WORD, so
# every one of those keyed, and any two of them sharing a determiner phrase would have collided.
_EX_NONREF_LEAD = frozenset("""
this that these those it there here some all both each any none another other others such one ones
what which who whom whose someone somebody anyone anybody everyone everybody something anything nothing
everything
""".split())

_EX_FIRST_PERSON = frozenset("i me myself we us ourselves".split())

# Evaluative / relative adjectives cannot IDENTIFY a value, only comment on one. They matter in exactly one
# place: the head-noun frame, where the value is a bare pre-modifier and "I'm in the PST timezone" and "I'm
# in the wrong timezone" are the same shape. Without this, a complaint keys as a correction and retires the
# fact it complains about -- measured, it was the last false bind on the negative control. Closed list, so
# an evaluative adjective outside it ("the janky timezone") still binds; that residual is real and stated.
_EX_NON_VALUE = frozenset("""
wrong right correct incorrect same different other another best worst better worse good bad
only main real actual usual normal proper true false whole entire
""".split())


def _ex_canon_subject(raw):
    """Surface subject -> canonical subject. First person collapses to a single referent, because in a
    per-user store 'I', 'me' and 'my ...' all point at the same person and keeping them apart is precisely
    what stops a chain binding. A possessed subject becomes a NESTED referent ('my wife' -> 'self.wife'),
    never a relation on the user, so 'my wife works at Acme' cannot retire 'my wife is Sarah'."""
    s = " ".join((raw or "").lower().split()).strip(" .,-")
    s = re.sub(r"^(?:the|a|an)\s+", "", s)
    s = _EX_CURRENT_MOD.sub("", s)
    if s in _EX_FIRST_PERSON:
        return "self"
    mt = re.match(r"^(?:my|our)\s+(.+)$", s)
    if mt:
        return "self." + mt.group(1).strip()
    return s


def _ex_canon_relation(raw):
    r = " ".join((raw or "").lower().split()).strip(" .,-")
    r = re.sub(r"^(?:the|a|an|my|our)\s+", "", r)
    return _EX_CURRENT_MOD.sub("", r).strip()


def _ex_clean_object(raw):
    obj = (raw or "").strip().strip(".").strip()
    obj = re.sub(r"^(?:now|actually|currently|really|already|just|officially|apparently)\s+", "",
                 obj, flags=re.I).strip()
    return obj


def _ex_key(subject, relation, obj):
    """(subject, relation|None, object) -> (key, object) | None. The single place the non-referring rules
    are enforced, so every pattern is guarded identically."""
    subj = _ex_canon_subject(subject)
    rel = _ex_canon_relation(relation) if relation else None
    obj = _ex_clean_object(obj)
    if not subj or not obj or len(obj) > 200:
        return None
    selfish = subj == "self" or subj.startswith("self.")
    if not selfish:
        toks = subj.split()
        if subj in _EX_NONREFERRING or not toks:
            return None
        if toks[-1] in _EX_NONREFERRING or toks[0] in _EX_NONREF_LEAD:
            return None
    elif rel is None:
        # A bare self-predication names no relation: "I'm vegetarian" and "I'm exhausted" would share the
        # key `self` and retire each other. Whether "vegan" supersedes "vegetarian" is a question about the
        # WORLD, not about the sentence, so the deterministic keyer declines it. This is the boundary.
        return None
    if rel is not None and (not rel or rel in _EX_NONREFERRING):
        return None
    return (f"{subj}::{rel}", obj) if rel else (subj, obj)


def _ex_parse_clause(clause):
    """One clause -> (key, object) | None, markers already the caller's problem to leave attached."""
    s = _EX_LEAD.sub("", clause or "").strip()
    if not s:
        return None
    mt = _EX_REL.match(s)
    if mt:
        return _ex_key(mt.group("subject"), mt.group("rel"), mt.group("obj"))
    mt = _EX_OF.match(s)
    if mt:
        return _ex_key(mt.group("subject"), mt.group("rel"), mt.group("obj"))
    mt = _EX_HEAD.match(s)
    if mt:
        # The value here is a bare pre-modifier, so an evaluative adjective in that slot means the sentence
        # comments on the fact rather than stating it ("I'm in the WRONG timezone"). Declining is a miss;
        # keying it would retire the very record it complains about.
        head_obj = _ex_clean_object(mt.group("obj")).lower().split()
        if not head_obj or head_obj[0] in _EX_NON_VALUE:
            return None
        return _ex_key(mt.group("subject"), mt.group("rel"), mt.group("obj"))
    mt = _EX_MINE.match(s)
    if mt:
        return _ex_key("I", mt.group("rel"), mt.group("obj"))
    mt = _EX_CHANGE.match(s)
    if mt:
        np = mt.group("np").strip()
        sub = re.match(r"^(?:my|our)\s+(.+)$", np, re.I)
        if sub:
            return _ex_key("I", sub.group(1), mt.group("obj"))
        sub = re.match(r"^(?P<s>.+?)(?:'s|s')\s+(?P<r>.+)$", np)
        if sub:
            return _ex_key(sub.group("s"), sub.group("r"), mt.group("obj"))
        return _ex_key(np, None, mt.group("obj"))
    mt = _EX_VERB.match(s)
    if mt:
        frame = _EX_VERB_FRAMES.get((_EX_VERB_LEMMA.get(mt.group("verb").lower(), ""),
                                     mt.group("prep").lower()))
        if frame:
            return _ex_key(mt.group("subject"), frame, mt.group("obj"))
    mt = _EX_IS.match(s)
    if mt:
        return _ex_key(mt.group("subject"), None, mt.group("obj"))
    return None


def derive_key(text):
    """text -> (key, object) | None. The reusable keying core: `regex_extractor` is a thin alias, and any
    other caller that needs the same canonical key (a bind/route helper, a migration) should call this
    rather than re-deriving one, so there is exactly one definition of what a key IS.

    Reading order, and why it is this one:
      1. contractions that expand unambiguously are expanded, so 'I'm' is a pronoun again;
      2. the WHOLE sentence is tried first (most faithful reading), with and without a trailing time
         adverbial;
      3. only if that fails is the sentence split on commas/semicolons and each clause tried, because the
         subject class excludes commas and a leading clause otherwise blocks every pattern. If two clauses
         yield DIFFERENT keys the sentence is ambiguous and None is returned -- a mis-derived key
         mis-supersedes, and declining is the cheap half of that trade.
    """
    if not text or not isinstance(text, str):
        return None
    # COLLAPSE WHITESPACE FIRST. The subject class contains a literal space, so a run of spaces is a run of
    # equally valid split points and the non-greedy quantifiers explore them quadratically: measured 0.7 ms
    # at 100 spaces, 2.8 at 200, 11.5 at 400, 46.9 at 800, against 10 us for an ordinary sentence. This is
    # the WRITE path, so that is a denial-of-service on `remember()`, not a slow benchmark. Collapsing is
    # free of meaning (whitespace never distinguishes two facts) and makes the cost flat.
    t = re.sub(r"\s+", " ", text).strip()
    if not t:
        return None
    for rx, rep in _EX_CONTRACT:
        t = rx.sub(rep, t)

    def _variants(s):
        stripped = _EX_TRAIL.sub("", s).strip()
        # stripped first: it is the normalised form. Unstripped is the fallback, so "the meeting is today"
        # keeps its object instead of being normalised into nothing.
        return [stripped, s.strip()] if stripped and stripped != s.strip() else [s.strip()]

    for cand in _variants(t):
        got = _ex_parse_clause(cand)
        if got:
            return got
    parts = [p for p in re.split(r"[,;]|\s+--\s+|\s+—\s+", t) if p and p.strip()]
    if len(parts) < 2:
        return None
    found = {}
    for p in parts:
        for cand in _variants(p):
            got = _ex_parse_clause(cand)
            if got:
                found[got[0]] = got[1]          # later clause wins the object for the same key
                break
    if len(found) != 1:
        return None
    k, v = next(iter(found.items()))
    return (k, v)


def regex_extractor(text):
    """text -> (key, object) | None. DETERMINISTIC: no LLM, no dependency, no network — this is the
    zero-LLM-on-write path, and it stays that way.

    Recognises, in this order: "X's Y is Z", "the Y of/for X is Z", "X is/moved to the Z Y" (the relation
    is the head noun of the complement), "my Y is Z", "<agent> changed <NP> to Z", "X lives in / works at
    Z", and the bare copula "X is Z". Conversational packaging is normalised first — leading discourse
    markers ("actually", "correction:", "so"), trailing time adverbials ("... now", "... last week"),
    `I'm`/`you're` contractions, current-marking modifiers ("my CURRENT title" == "my title"), and a
    leading clause ("Dana left, so my manager is Priya now") — so a chain of corrections lands on ONE key.

    First person is canonical: `I`, `me`, `my X` all resolve to the single referent `self`, and a possessed
    third party nests ("my wife" -> `self.wife`) instead of becoming a relation on the user.

    WHERE IT STOPS, on purpose. A key is only derived when the sentence NAMES the relation. When the later
    turn names only the VALUE and leaves the relation to world knowledge — "I'm a Principal Engineer now"
    (that a Principal Engineer is a *title*), "I'm vegan now" (that vegan is a *diet*), "Dan is now an
    engineering manager" — this returns None and the write is a plain append. That is not a gap waiting to
    be patched with a bigger regex: no deterministic keyer can cross it without an ontology or a model.
    Pass `key=` explicitly, or plug `make_llm_extractor`, when you need those bound.

    A subject that does not REFER — pronoun, expletive, interrogative, quantifier ("it", "there", "these",
    "what", "both approaches") — is rejected, because such keys collide across unrelated sentences and
    would make supersession retire live records."""
    return derive_key(text)


def make_llm_extractor(call_fn, prompt_prefix=None):
    """Wrap YOUR `call_fn(prompt) -> str` into an extractor. OPT-IN: this puts an LLM on the write path (you lose
    the deterministic/zero-cost core). The LLM must return a JSON object {"key": ..., "object": ...}; anything
    else (or an exception) yields None -> plain append. Example: m.extractor = make_llm_extractor(my_llm)."""
    prefix = prompt_prefix or (
        "Extract the single (subject::relation, value) fact from the text as JSON "
        '{"key": "<subject::relation>", "object": "<current value>"}. If there is no clear single fact, '
        'reply {"key": null}. Text:\n')

    def _ex(text):
        try:
            raw = call_fn(prefix + (text or ""))
            i, j = raw.find("{"), raw.rfind("}")
            if i < 0 or j < 0:
                return None
            d = json.loads(raw[i:j + 1])
            k = d.get("key")
            if not k:
                return None
            return (str(k), str(d.get("object")) if d.get("object") is not None else None)
        except Exception:
            return None
    return _ex


def default_distiller(url=None, model=None, key=None, timeout=60):
    """Batteries-included distiller for distill_and_remember(): a zero-dependency urllib chat caller against any
    OpenAI-compatible /chat/completions endpoint (args or env INSPEXIMUS_LLM_URL / INSPEXIMUS_LLM_MODEL / INSPEXIMUS_LLM_KEY —
    e.g. local Ollama at http://localhost:11434/v1/chat/completions). Returns a `distiller(prompt, text) -> str`
    you pass straight to distill_and_remember, so capture works out of the box instead of forcing every caller to
    wire an LLM. OPT-IN: this is the only place an LLM touches capture; the core store/recall/revert stay zero-LLM.
    Raises if no URL is configured (so you know to inject your own)."""
    import urllib.request
    url = (url or os.environ.get("INSPEXIMUS_LLM_URL", "")).strip()
    if not url:
        raise RuntimeError("default_distiller needs INSPEXIMUS_LLM_URL (an OpenAI-compatible /chat/completions endpoint) "
                           "or explicit url= ; the core stays zero-LLM, so a distiller is opt-in.")
    model = (model or os.environ.get("INSPEXIMUS_LLM_MODEL", "gpt-4o-mini")).strip()
    key = (key or os.environ.get("INSPEXIMUS_LLM_KEY", "")).strip()

    def distiller(prompt, text):
        body = json.dumps({"model": model, "temperature": 0, "messages": [
            {"role": "system", "content": prompt}, {"role": "user", "content": text or ""}]}).encode()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]

    return distiller


if __name__ == "__main__":
    m = Inspeximus()                                  # no path, no embedder — pure in-memory + lexical
    m.remember("SGD converges slowly due to gradient variance.", tags=["optimization"], value=3)
    m.remember("SGD does not converge slowly.", tags=["optimization"], value=1)
    m.remember("Pre-trend tests catch only 31% of fatal DiD bias.", tags=["causal"], value=2)
    print("recall 'SGD variance':", [r["text"][:46] for r in m.recall("SGD variance", k=3)])
    print("consolidate:", m.consolidate(keep=10))
    print("contradictions:", m.contradictions())       # flags the SGD pair (related + one negates)
    print("value_by_cohort:", m.value_by_cohort())
    print("(For semantic recall, pass embed=your_model to Inspeximus(); lexical is the zero-dep fallback.)")
