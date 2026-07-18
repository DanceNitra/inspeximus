"""
mnemo — a memory layer for AI agents.  (brand: Mnemosyne)

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

Bring your own embedder for semantic recall (any text->vector fn); with none, mnemo falls back to a
lexical token overlap so it runs anywhere, today.

    from mnemo import Mnemo
    m = Mnemo("memory.json")                 # or Mnemo("memory.json", embed=my_embedder)
    m.remember("Pre-trend tests catch only ~31% of fatal DiD bias.", tags=["causal"], value=3)
    m.recall("difference in differences", k=5)
    m.consolidate(keep=200)
    m.contradictions()

MIT-licensed. Part of Agora (https://github.com/DanceNitra/agora).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import time
import uuid
from pathlib import Path

try:                                  # OPTIONAL: numpy only ACCELERATES semantic recall at scale.
    import numpy as _np               # mnemo still runs (pure-Python cosine) with no numpy installed.
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
_MNEMO_ENC_MAGIC = b"MNMO\x01"        # versioned so the on-disk format can migrate


def new_encryption_key() -> bytes:
    """A fresh random 32-byte (AES-256) key for Mnemo(encrypt_key=...). Store it yourself (a secrets manager /
    OS keystore); mnemo never persists the key. Losing it = the store is unrecoverable (that IS crypto-shred)."""
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
    header = _MNEMO_ENC_MAGIC + salt + nonce
    ct = _AESGCM(key).encrypt(nonce, plaintext, header)   # header authenticated as AAD
    return header + ct


def _parse_enc_header(blob: bytes):
    """-> (salt, nonce, header, ciphertext) or raise ValueError if not a mnemo-encrypted blob."""
    if blob[:5] != _MNEMO_ENC_MAGIC:
        raise ValueError("not a mnemo-encrypted store")
    salt, nonce = blob[5:21], blob[21:33]
    return salt, nonce, blob[:33], blob[33:]


def _decrypt_blob(key: bytes, blob: bytes) -> bytes:
    if not _HAVE_AEAD:
        raise RuntimeError("encryption needs the `cryptography` package (pip install cryptography)")
    salt, nonce, header, ct = _parse_enc_header(blob)
    return _AESGCM(key).decrypt(nonce, ct, header)        # raises on wrong key / tampering

_GENESIS = "0" * 64


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def new_receipt_keypair():
    """Return (private_key_hex, public_key_hex) for signing mnemo write receipts. Needs `cryptography`."""
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


def _attest_message(text: str, source_doc) -> bytes:
    """Canonical message an attestation signs: the claim text bound to its canonical source, so a signature
    for 'X by source S' cannot be replayed as 'X by source T' or attached to a different claim."""
    canon_src = Mnemo._canon_source(source_doc) if source_doc else ""
    return _canon({"t": text, "s": canon_src})


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


# --- universal-executor detection (1.2.0) -------------------------------------------------------------------
# WHY: a per-tool reversibility label is unsound for VERB-POLYMORPHIC universal executors -- a shell / eval /
# arbitrary-SQL / generic-HTTP tool whose EFFECT is set by a free-form argument, so the same tool is both
# 'ls' (reversible) and 'rm -rf' (irreversible). MEASURED (mnemo lab, ToolEmu 330 tools, 2 labelers): tool
# reversibility is ~93% decidable from the signature (Cohen's kappa 0.82) but the ~7% undecidable residual is
# exactly this class, and its realized harm-reach is ENVIRONMENT-conditional -- an isolated executor reaches
# ~0% of external/API irreversible harms but a networked, ambiently-credentialed one reaches ~0.66. So the
# thing that bounds a memory-poisoned agent's irreversible EXTERNAL harm through such a tool is executor
# CONTAINMENT, not a per-tool reversibility flag. This detector + the spend_irreversible(tool=, contained=)
# gate make that undecidability EXPLICIT: an uncontained universal executor is never silently treated as
# reversible. Honest bound: heuristic name/param match (not a proof), and `contained` is a caller ASSERTION
# mnemo cannot verify -- it forces the declaration, it does not enforce the sandbox.
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
    (Mnemo.revert_challenge(key) = "revert:{key}:{current_active_id}") with the private key whose public half
    the store was given as `revert_pubkey`. The resulting hex signature is the capability passed to
    revert()/route(); the store verifies it but cannot produce it. This is the affordance a text-only attacker
    (and a store-only harness) cannot synthesize. Needs `cryptography`."""
    if not _HAVE_ED:
        raise RuntimeError("signing a revert needs the `cryptography` package (pip install cryptography)")
    sk = _Ed25519SK.from_private_bytes(bytes.fromhex(principal_sk_hex))
    return sk.sign(challenge.encode()).hex()


def sign_support(source_sk_hex: str, challenge: str) -> str:
    """Source-side, OFF the memory store's box: Ed25519-sign a support challenge string obtained from
    Mnemo.support_challenge_for(key, toward). The hex signature is passed to observe(..., support=[(source_
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

__version__ = "1.12.4"

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


class Mnemo:
    def __init__(self, path: str | None = None, embed=None, receipts: bool = False,
                 receipt_key: str | None = None, receipt_pubkey: str | None = None,
                 capacity: int | None = None, revert_authority: str | None = None,
                 revert_pubkey: str | None = None, max_text: int | None = None,
                 tenant: str | None = None, pii_detect: bool = False,
                 encrypt_key: bytes | None = None, encrypt_passphrase: str | None = None,
                 support_authorities: list | None = None, persist_vectors: bool = False):
        """path: optional JSON file to persist to. embed: optional fn(str)->list[float] for semantic
        recall; if omitted, recall uses lexical token overlap (zero dependencies).

        receipts/receipt_key (OPT-IN, default OFF -> identical legacy behavior): when enabled, every
        remember() appends a tamper-evident, hash-chained WRITE RECEIPT committing to the memory's
        content hash, persisted to a sidecar "<path>.receipts.json" (the main store format is unchanged).
        verify_writes() then proves the write history wasn't altered out-of-band — something an
        append-only store alone can't, because anyone who can edit the store file can rewrite a stored
        memory and the store would serve the altered text as original. The hash chain is zero-dependency;
        pass receipt_key (+ receipt_pubkey) from new_receipt_keypair() to also Ed25519-SIGN each receipt
        so a third party can verify it with the public key only. (Standalone version: agora-agent-receipts.)"""
        self.path = Path(path) if path else None
        self.embed = embed
        # OPT-IN vector persistence (default False -> legacy: vecs are a RAM-only cache, STRIPPED on save
        # to keep the file small and dodge the frozen-world GIL stall on big stores). Set True for a SMALL
        # store (e.g. the Claude Code coding memory, a few hundred items) whose process is short-lived and
        # reloaded often, so semantic recall survives a reload WITHOUT re-embedding every item each start.
        # Do NOT enable on large brain-scale stores — that is exactly the case the strip exists to protect.
        self._persist_vectors = bool(persist_vectors)
        # HARD TENANT ISOLATION (OPT-IN, default None -> unbound -> byte-identical legacy). Binding a store to
        # a tenant (Mnemo(tenant="acme")) makes isolation a STORE PROPERTY, not a per-call argument a caller can
        # forget: every remember() is stamped with this tenant, and every read/supersession/erasure the store
        # performs is HARD-filtered to it. The guarantee is FAIL-CLOSED and non-bypassable from the content path:
        #   - recall() returns ONLY this tenant's records (a wrong/absent tenant sees nothing, never another
        #     tenant's data) — unlike the soft `scope=` recall arg, which sees everything if the caller omits it;
        #   - keyed supersession + the echo guard compare only WITHIN this tenant, so tenant A writing key
        #     "billing::plan" can never retire tenant B's same-key fact (cross-tenant write-through is closed);
        #   - forget_subject()/forget_pii()/pii_report() only ever touch this tenant's rows.
        # An UNBOUND store (tenant=None) is the admin/migration view: it sees + supersedes across everything
        # (legacy behavior) and its writes carry no tenant tag, so they are invisible to any tenant-bound store.
        # HONEST SCOPE: this isolates within ONE mnemo store (logical multi-tenancy — the right model when many
        # agents share a process); it is NOT a substitute for separate stores/encryption keys when tenants are
        # mutually hostile and the process itself is the trust boundary. Mixing tenant-tagged and untagged writes
        # in one store is a migration state, not a steady one. Reversible: tenant=None. Receipt:
        # mnemo/probes/tenant_isolation_probe.py (measured cross-tenant leak 0/N).
        self.tenant = str(tenant) if tenant is not None else None
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
        # verified two-tier policy (value-protected + recency-aged, Lab 29992a). Lets mnemo run in
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
        self.items: list[dict] = []
        self._tok_cache: dict[str, set] = {}     # id -> token set, so recall doesn't re-tokenize
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
        # simulation of mnemo's own value-accrual + per-type decay: locality served-hit 0.22 -> 0.78,
        # neutral on rare-critical + poison-flood. Reversible: two_tier_keep=False -> legacy top-N-by-raw.
        self.two_tier_keep = True
        self.protect_frac = 0.30
        # Fast-novelty channel guard (OPT-IN, default OFF). mnemo's state-toggle supersedes a standing
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
        # (lab fea933, mnemo's real consolidate() path): isolated-poison false-supersede 1 -> 0 while a
        # 3-record sustained change is still adopted; it Pareto-dominates both the naive (poison-fooled) and
        # corroboration-only (change-lagging) rules — see the adaptation-corruption coupling (a classical
        # quickest-change-detection tradeoff; lab f490d8).
        # Reversible: 0 or 1 -> legacy fast supersession.
        self.supersede_persistence = 0
        # ECHO GUARD (OPT-IN, default OFF -> byte-identical legacy). Closes the ECHO ATTACK on keyed
        # supersession: after a fact is corrected (old value -> superseded), a later RE-STATEMENT of the
        # OLD value (a benign restatement or an attacker re-injection) carries a newer valid_from and would
        # otherwise retire the FRESH value and resurrect the stale one. With this ON, an incoming keyed write
        # whose OBJECT (remember(..., object=...), else the normalized text) matches a value ALREADY
        # superseded for that key is a restatement-of-superseded: it is retired stale-on-arrival and the
        # current value is preserved. MEASURED (mnemo/probes/echo_attack_probe_v2.py) on a MemBench echo
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
        self.echo_guard = False
        # STRICT corroboration (OPT-IN, default OFF -> identical legacy behavior). The corroboration bar
        # (episodic->semantic graduation AND the recall influence gate) counts ">=2 distinct sources". By
        # default a "source" is a canonical STRING (entity-resolved), which collapses honest sybil variants
        # ("Wikipedia"/"wikipedia.org"/URL) but is still SPOOFABLE by an attacker who supplies two unrelated
        # source strings it controls. With strict_corroboration ON, a corroborating link only counts if it
        # carries a VERIFIED KEY (remember(..., attestation=...)): independence is then measured by distinct
        # Ed25519 public keys an attacker cannot forge, so N sybil variants of one origin collapse to one
        # witness unless the attacker holds N distinct keys (a costly identity, Douceur 2002). This binds the
        # "independence" rail to the "origin-signed" rail; it does NOT make a claim TRUE (an attested source
        # can still sign a false claim), only makes manufactured independence expensive. Reversible: OFF.
        self.strict_corroboration = False
        # EXOGENOUS-WARRANT credit (OPT-IN, default False -> identical legacy behavior). Closes the MINJA
        # self-graded-outcome hole (arXiv:2503.03704): the influence gate's earned-outcome path counts only
        # good credited with an exogenous `warrant` (an outcome the record did not author itself), so an
        # agent that self-grades its own recalled reasoning cannot corroborate a poisoned bridge into the
        # influence set. MEASURED (mnemo/probes/minja_influence_gate.py): self-graded MINJA ASR 80% -> 0%
        # with this on, legit utility preserved when the app passes a real warrant. Reversible: False = legacy.
        self.credit_requires_warrant = False
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
        # empty set. Receipt: mnemo/probes/seed_anchored_trust_probe.py.
        self.trust_seeds: set = set()
        self.trust_hops: int = 1
        # WRITE-PATH VALUE EXTRACTOR (OPT-IN, default None -> OFF -> zero behavior change). mnemo's whole
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
        # _save() THROTTLE: serializing the whole store (json.dumps of every item) is O(store size); doing
        # it on EVERY recall/remember froze callers once the store grew (recall mutates access value, so it
        # used to re-serialize everything each call). Coalesce disk writes to at most once / _save_min_s;
        # at most _save_min_s of access-metadata is lost on a hard crash (working memory — acceptable).
        self._save_min_s = 5.0
        self._last_save = 0.0
        self._dirty = False
        # ENCRYPTION-AT-REST (OPT-IN, default None -> plaintext JSON, byte-identical legacy). encrypt_key is a
        # raw 32-byte AES-256 key (from new_encryption_key()); encrypt_passphrase is stretched with scrypt.
        # mnemo NEVER persists the key/passphrase — you hold it; lose it and the store is unrecoverable (that IS
        # crypto-shred). See the module-level note for the honest threat model + shred(). The key is resolved
        # lazily against the on-disk salt so an existing encrypted store reloads with the same passphrase.
        if encrypt_key is not None and (not isinstance(encrypt_key, (bytes, bytearray)) or len(encrypt_key) != 32):
            raise ValueError("encrypt_key must be exactly 32 bytes (use mnemo.new_encryption_key())")
        self._enc_rawkey = bytes(encrypt_key) if encrypt_key is not None else None
        self._enc_passphrase = encrypt_passphrase
        self._enc_salt = None                    # filled from the file header on load, or minted on first save
        self._encrypted = bool(encrypt_key is not None or encrypt_passphrase is not None)
        if self._encrypted and not _HAVE_AEAD:
            raise RuntimeError("encryption needs the `cryptography` package (pip install cryptography)")
        if self.path and self.path.exists():
            raw = self.path.read_bytes()
            if raw[:5] == _MNEMO_ENC_MAGIC:                           # encrypted store -> decrypt or FAIL LOUD
                if not self._encrypted:
                    raise ValueError("store is encrypted; pass encrypt_key= or encrypt_passphrase= to open it")
                self._enc_salt = raw[5:21]                            # reuse the store's salt (passphrase re-derivation)
                try:
                    self.items = json.loads(_decrypt_blob(self._resolve_key(), raw))
                except Exception as e:                                # wrong key / tampered / truncated -> never
                    raise ValueError("cannot decrypt store (wrong key/passphrase, or the file was tampered)") from e
                #                                                       silently return [] (would risk overwriting real data)
            else:
                try:
                    self.items = json.loads(raw.decode("utf-8"))     # legacy plaintext JSON
                except Exception:
                    self.items = []
        # OPT-IN write receipts (default OFF -> zero behavior change; no sidecar created)
        self.receipts_enabled = bool(receipts or receipt_key)
        self._receipt_sk = receipt_key
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
        # flags as out-of-band tampering. HONEST SCOPE: this proves the ACT of deletion within THIS mnemo store
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

    # ── capture ──────────────────────────────────────────────────────────────
    def remember(self, text: str, tags=None, value: float = 1.0, meta: dict | None = None,
                 mtype: str | None = None, valid_from: float | None = None,
                 source: dict | None = None, key: str | None = None,
                 derived_from: list | None = None, attestation=None, derived: bool = False,
                 object: str | None = None, reaffirm: bool = False, capability: str | None = None,
                 pii=None, identity_confidence: float | None = None) -> str:
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
        # WRITE-PATH EXTRACTOR: derive (key, object) from the text when the caller didn't supply a key and an
        # extractor is plugged, so the governance layer keys itself over free text. Fail-open (never break a write).
        if self.extractor is not None and key is None and not derived:
            try:
                ex = self.extractor(text)
                if isinstance(ex, tuple) and len(ex) == 2:
                    key = ex[0]
                    if object is None:
                        object = ex[1]
                elif isinstance(ex, str):
                    key = ex
            except Exception:
                pass
        # availability guard (OPT-IN): cap a single record's text so one runaway/malicious write can't exhaust
        # memory. Truncate rather than reject (don't break the app), and record the original length. SECURITY.md.
        _trunc_from = None
        if self.max_text is not None and isinstance(text, str) and len(text) > self.max_text:
            _trunc_from = len(text)
            text = text[:self.max_text]
        mid = uuid.uuid4().hex[:10]
        now = time.time()
        rec = {"id": mid, "text": text, "tags": list(tags or []), "value": float(value),
               "ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "valid_from": float(valid_from) if valid_from is not None else now,  # event-time (bi-temporal); defaults to ingest-time
               "source": dict(source) if source else None,   # re-checkable origin (e.g. {"doc": id, "span": [start, end]}) so a recalled fact can be traced back, not trusted blind
               "mtype": mtype or _infer_type(text), "last_access": now,
               "status": "active", "links": [], "meta": dict(meta or {})}
        if _trunc_from is not None:
            rec["meta"]["truncated_from"] = _trunc_from
        # TENANT STAMP: bind this write to the store's tenant so recall/supersession/erasure can isolate it.
        # Unbound stores (tenant=None) leave no tag -> byte-identical legacy.
        if self.tenant is not None:
            rec["tenant"] = self.tenant
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
            taint, links = set(), []
            for pid in derived_from:
                p = _by.get(pid)
                if p is None:
                    continue
                links.append(pid)
                taint |= Mnemo._rec_sources(p)     # parent's own source + its inherited taint (transitive)
            if taint:
                rec["taint"] = sorted(taint)
            if links:
                rec["links"] = links
                rec["derived_from"] = list(links)   # explicit lineage (distinct from corroboration links) so
                #                                     a derived memory's evidence grade can be capped at its
                #                                     weakest parent's -- trust taint propagates, not just source taint
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
        # (pubkey_hex, sig_hex) or {"pubkey":..., "sig":...}; the signature (from mnemo.attest(text, sk,
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
        if self.embed:
            try:
                rec["vec"] = list(self.embed(text))
            except Exception:
                rec["vec"] = None
        self.items.append(rec)
        if key is not None and not _is_candidate:
            self._supersede_by_key(rec, reaffirm=reaffirm)   # deterministic SRO supersession (no embedding, no threshold)
            #                                                  a candidate (low identity_confidence) never supersedes
        if self.capacity is not None:
            self._evict_to_capacity()                        # bounded working set (opt-in) BEFORE persisting
        self._save(force=True)        # a new memory is real content - persist immediately, not throttled
        if self.receipts_enabled:
            self._emit_write_receipt(rec)
        return mid

    def _evict_to_capacity(self) -> None:
        """Keep the ACTIVE working set at <= self.capacity by HARD-EVICTING the lowest-value active
        memories, using the VERIFIED two-tier policy (Lab 29992a: value-protected + recency-aged is the
        one eviction rule that is universal across regimes). Protect the top protect_frac of capacity by
        RAW value (a rare-but-critical memory survives a flood); fill the remaining budget from the REST
        by EFFECTIVE (decay-weighted) value (so a stale high-raw memory can't crowd out a freshly-useful
        one, and pure junk floods age out). Eviction REMOVES (frees space) via forget(), unlike
        consolidate(keep=) which only DEMOTES — a bounded store must actually shrink. Superseded history
        is not counted or evicted here (it is low-overhead and preserves as_of); only the active set is
        bounded. No-op when active <= capacity, so remember() stays O(1) amortized until the cap bites."""
        active = [r for r in self.items if r.get("status") == "active"]
        if len(active) <= self.capacity:
            return
        now = time.time()
        active.sort(key=lambda r: -r["value"])               # by RAW value (protected tier order)
        kprot = int(self.protect_frac * self.capacity) if self.two_tier_keep else 0
        protected, rest = active[:kprot], active[kprot:]
        rest_keep = set(id(r) for r in
                        sorted(rest, key=lambda r: -self._effective_value(r, now))[:self.capacity - kprot])
        evict_ids = [r["id"] for r in rest if id(r) not in rest_keep]
        if evict_ids:
            self.forget(evict_ids)                            # hard delete + link/toggle scrub

    # ── write receipts (OPT-IN: tamper-evident write history) ─────────────────
    def _write_commit(self, rec: dict) -> dict:
        """What a receipt commits to for a stored memory: its id, a hash of its content-bearing fields, AND a hash
        of its ATTRIBUTION (canonical sources = own source + inherited derived_from taint). Binding attribution into
        the receipt is what makes a later RELABEL detectable: k, the influence budget, the influence gate and slash
        are all keyed on the source id, so a silent relabel (rewriting a record's source, or stripping its taint)
        voids all of them at once with no inner layer to appeal to — attribution is not a fourth axis, it is the
        floor the others stand on. With the sources committed, a relabel no longer matches the receipt, so
        verify_attribution() flags it. Honest limit: this makes a relabel tamper-EVIDENT, not attribution CORRECT —
        a wrong source asserted at write time (an attacker who controls the labeling channel, e.g. MINJA) is
        committed faithfully and uselessly; that oracle problem is untouched."""
        return {"id": rec["id"],
                "content_sha256": _sha256_hex(_canon({"text": rec.get("text"), "key": rec.get("key"),
                                                      "mtype": rec.get("mtype")})),
                "attrib_sha256": _sha256_hex(_canon(sorted(Mnemo._rec_sources(rec))))}

    def _emit_write_receipt(self, rec: dict) -> dict:
        prev = self._receipts[-1]["hash"] if self._receipts else _GENESIS
        r = {"seq": len(self._receipts), "ts": rec.get("ts"), "memory_id": rec["id"],
             "commit": self._write_commit(rec), "prev": prev}
        r["hash"] = _sha256_hex(_canon({k: r[k] for k in ("seq", "ts", "memory_id", "commit", "prev")}))
        if self._receipt_sk and _HAVE_ED:
            sk = _Ed25519SK.from_private_bytes(bytes.fromhex(self._receipt_sk))
            r["pubkey"] = self.receipt_pubkey
            r["sig"] = sk.sign(bytes.fromhex(r["hash"])).hex()
        self._receipts.append(r)
        if self._receipts_path:
            try:
                self._receipts_path.write_text(json.dumps(self._receipts, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
            except Exception:
                pass
        return r

    def verify_writes(self, expected_pubkey: str | None = None, warn_unpinned: bool = False) -> tuple[bool, list[str]]:
        """Verify the write-receipt chain AND that each stored memory still matches its write receipt.
        Returns (ok, problems). Catches out-of-band edits to the store the normal flow can't see.
        Requires receipts to have been enabled at write time."""
        problems: list[str] = []
        prev = _GENESIS
        by_id = {it["id"]: it for it in self.items}
        for i, r in enumerate(self._receipts):
            core = {k: r.get(k) for k in ("seq", "ts", "memory_id", "commit", "prev")}
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
                cc = self._write_commit(cur)
                if any(cc.get(k) != v for k, v in (r.get("commit") or {}).items()):
                    problems.append(f"memory {r['memory_id']}: stored content no longer matches its write receipt (edited after write)")
            prev = r.get("hash")
        # verify the DELETION-TOMBSTONE chain too — else a forged tombstone could hide a real out-of-band delete
        tprev = _GENESIS
        for j, t in enumerate(self._tombstones):
            core = Mnemo._tombstone_core(t)
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
        return (len(problems) == 0, problems)

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
            core = {k: r.get(k) for k in ("seq", "ts", "memory_id", "commit", "prev")}
            if r.get("prev") != prev or _sha256_hex(_canon(core)) != r.get("hash"):
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
            elif _sha256_hex(_canon(sorted(Mnemo._rec_sources(cur)))) != a:
                relabeled.append(mid)
        return {"ok": chain_ok and not relabeled, "chain_ok": chain_ok,
                "relabeled": relabeled, "uncommitted": uncommitted, "missing": missing}

    @staticmethod
    def _obj_sig(r: dict) -> str:
        """The supersession OBJECT signature: the explicit `object` value if set, else normalized text.
        Value-preserving paraphrases share the object; a verbatim-only fallback (text) matches MemStrata."""
        o = r.get("object")
        s = o if o is not None else r.get("text", "")
        return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()

    def _supersede_by_key(self, rec: dict, reaffirm: bool = False) -> None:
        """Deterministic (subject, relation, object) supersession: retire active records that share
        rec['key']. No similarity threshold, no LLM call — the fix our Crucible replication validated
        (stale-fact recall 41.7% -> 0.0%, where cosine-based detection is near chance at AUROC ~0.61).
        Bi-temporal: only same-key records with valid_from <= rec's are retired; if an active same-key
        record is genuinely newer (later valid_from), the INCOMING rec is the stale one and is retired
        instead — a back-filled value never overwrites the current one. recall() hides superseded records
        by default, so a keyed store never surfaces a stale fact.

        ECHO GUARD (self.echo_guard, default OFF): before the normal path, if the incoming rec asserts an
        OBJECT that has ALREADY been superseded for this key AND differs from the current active value, it
        is a restatement-of-superseded (an echo) — retire the incoming rec stale-on-arrival and keep the
        current value, so a later re-mention of the old value cannot resurrect it. reaffirm=True bypasses
        the guard (a genuine, authoritative reversal back to a previously-superseded value)."""
        k = rec.get("key")
        if not k:
            return
        vf_new = rec.get("valid_from", rec["ts"])
        tv = rec.get("tenant")                         # tenant isolation: only same-tenant records collide on a key
        if self.echo_guard and not reaffirm:
            new_sig = self._obj_sig(rec)
            same_key = [r for r in self.items if r is not rec and r.get("key") == k and r.get("tenant") == tv]
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
                return
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
                return                                 # current value preserved; skip normal supersession
        for r in self.items:
            if r is rec or r.get("status") != "active" or r.get("key") != k or r.get("tenant") != tv:
                continue
            vf_r = r.get("valid_from", r["ts"])
            if vf_r <= vf_new:                 # r is the older value -> retire it
                r["status"] = "superseded"
                r["superseded_ts"] = time.time()
                r["invalidated_at"] = vf_new
                rm = r.setdefault("meta", {})
                rm["superseded_by_toggle"] = rec["id"]
                rm["superseded_by_policy"] = "keyed_reaffirm" if reaffirm else "keyed_lww"
            else:                              # an active same-key value is newer -> incoming is stale-on-arrival
                rec["status"] = "superseded"
                rec["superseded_ts"] = time.time()
                rec["invalidated_at"] = vf_r
                rm = rec.setdefault("meta", {})
                rm["superseded_by_toggle"] = r["id"]
                rm["superseded_by_policy"] = "keyed_lww_backfill"

    # ── candidate reconciliation queue (identity-confidence gate; Fellegi-Sunter clerical review / MDM steward
    #    queue, ported to agent memory). A fuzzy-identity keyed write forks a candidate instead of superseding;
    #    these three methods are the steward path that promotes or discards it. ────────────────────────────────
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
        for r in self.items:
            if not r.get("reopened") or r.get("status") != "active":
                continue
            if tv is not None and r.get("tenant") != tv:
                continue
            if key is not None and r.get("key") != str(key):
                continue
            m = r.get("meta", {})
            out.append({"id": r["id"], "key": r.get("key"), "object": r.get("object"), "text": r.get("text"),
                        "reason": m.get("reopened_reason"), "surfaced_prior": m.get("reopened_surfaced_prior"),
                        "contradiction": m.get("reopened_contradiction")})
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
            new_id = self.remember(f"the {key} is {prior}", key=key, object=prior, reaffirm=True,
                                   capability=capability)
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

    def forget(self, ids=None, where=None, redact_links: bool = True) -> dict:
        """HARD-DELETE memories — the one operation that genuinely REMOVES content. mnemo is otherwise
        append-only: supersession / invalidation only DEMOTE a record (it still exists, recallable with
        include_superseded). forget() is for the cases where demotion is not enough: a right-to-be-forgotten
        / erasure request, a poisoned or libellous memory, or a hard correction.

        Select by `ids` (a single id or an iterable) and/or `where` (a predicate fn(record)->bool; e.g.
        lambda r: 'secret' in r['text']). VERIFIED FORGETTING: the matched records are deleted AND their ids
        are scrubbed from every surviving record's `links` and toggle-supersession pointers, and the cached
        vec matrix + token caches are dropped — so a forgotten memory cannot resurface via recall, via a
        consolidation link, or via a stale derived-summary pointer. This is complete because consolidation
        never copies raw text into other records (it only links ids and toggles status) — there is no merged
        blob left holding the forgotten content. Returns {forgotten, ids, scrubbed_links}."""
        target = set()
        if ids is not None:
            target |= ({ids} if isinstance(ids, str) else set(ids))
        if where is not None:
            for r in self.items:
                try:
                    if where(r):
                        target.add(r["id"])
                except Exception:
                    pass
        target &= {r["id"] for r in self.items}          # ignore ids not actually present
        if not target:
            return {"forgotten": 0, "ids": [], "scrubbed_links": 0}
        self.items = [r for r in self.items if r["id"] not in target]
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
        for tid in target:
            self._tok_cache.pop(tid, None)
        self._mat = None; self._mat_built_n = -1             # force vec-matrix rebuild (drops forgotten rows)
        self._save(force=True)                               # a deletion is real content change — persist now
        return {"forgotten": len(target), "ids": sorted(target), "scrubbed_links": scrubbed}

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
                        authorization: str | None = None) -> dict:
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
        t["hash"] = _sha256_hex(_canon(Mnemo._tombstone_core(t)))
        if self._receipt_sk and _HAVE_ED:
            sk = _Ed25519SK.from_private_bytes(bytes.fromhex(self._receipt_sk))
            t["pubkey"] = self.receipt_pubkey
            t["sig"] = sk.sign(bytes.fromhex(t["hash"])).hex()
        self._tombstones.append(t)
        if self._tombstones_path:
            try:
                self._tombstones_path.write_text(json.dumps(self._tombstones, indent=2, ensure_ascii=False),
                                                 encoding="utf-8")
            except Exception:
                pass
        return t

    def register_erasure_target(self, target) -> "Mnemo":
        """Register an APP-SIDE store (the app's vector index, an embedding/response cache, a retrieval log)
        for cross-store right-to-erasure. Targets implement the two-method ErasureTarget protocol
        (mnemo.deletion_manifest): erase(subject) and still_recoverable(subject, values). Once any target is
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
                       values=None) -> dict:
        """RIGHT-TO-ERASURE across provenance lineage, with a tamper-evident audit of the ACT. Hard-deletes
        every active memory ATTRIBUTABLE to `subject` — its own canonical source OR any record that inherited
        `subject` through derived_from taint (so a summary/consolidation built from the subject's data is erased
        too, which a naive text-match delete would miss) — then records a signed, CONTENT-FREE tombstone per
        erased record so verify_writes() reports the now-missing rows as deliberately erased (not out-of-band
        tampering). `subject` is matched against canonical sources (`_rec_sources`): pass the same source string
        you wrote with (`remember(..., source={'doc': subject})`) or an attested key as 'key:<hex>'.

        Returns {erased, ids, request_id, tombstones}. HONEST SCOPE (read before relying on it for compliance):
        this erases + proves-deletion WITHIN THIS mnemo store only — NOT the app's vector store, prompt logs,
        or backups; it is an integrity primitive, NOT a compliance certification. The tombstone proves the ACT
        (a record with this surrogate id was erased at T for request R), never the CONTENT, and its signature is
        load-bearing only against a party who does NOT hold receipt_key (the operator who holds the key can forge
        tombstones too — anchor the chain head externally for operator-adversarial audit). Prior art: crypto-
        shredding; Cassandra/event-sourcing tombstones; GDPR Art.30 erasure logs; Crosby-Wallach / Certificate
        Transparency tamper-evident logs."""
        # match the subject against canonical sources; accept either the raw string the caller wrote or its
        # entity-resolved form (_canon_source collapses "user-42"/"user_42"/"User 42" -> one canonical id).
        cand = {subject, Mnemo._canon_source(subject)}
        subj_ids = [r["id"] for r in self.items if cand & Mnemo._rec_sources(r)
                    and (self.tenant is None or r.get("tenant") == self.tenant)]   # tenant isolation on erasure
        if not subj_ids:
            return {"erased": 0, "ids": [], "request_id": request_id, "tombstones": 0}
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
        now = time.time()
        res = self.forget(ids=subj_ids)
        for mid in res["ids"]:
            self._emit_tombstone(mid, now, request_id, basis=basis,
                                 authorized_by=authorized_by, authorization=authorization)
        out = {"erased": res["forgotten"], "ids": res["ids"],
               "request_id": request_id, "tombstones": len(res["ids"])}
        if targets:
            out["manifest"] = self._erasure_manifest(subject, values or [], targets, request_id,
                                                     basis, authorized_by, already_erased=res["forgotten"])
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
            name = "mnemo-store"

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

    def _tenant_rows(self) -> list:
        """The records THIS store is allowed to touch: all rows for an unbound (admin) store, else only the
        bound tenant's rows. The one place tenant scoping is resolved for the whole-store audit/sweep methods."""
        if self.tenant is None:
            return list(self.items)
        return [r for r in self.items if r.get("tenant") == self.tenant]

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
                   basis: str | None = None) -> dict:
        """DATA-MINIMIZATION SWEEP: hard-delete (+ tombstone) every record carrying a PII tag, optionally
        restricted to specific `types` (e.g. ['email','ssn']) and/or a `subject` (a canonical source string,
        as in forget_subject). Tenant-scoped when the store is bound. Like forget_subject this genuinely REMOVES
        content and records a content-free, hash-chained tombstone per erased row so verify_writes() reads the
        deletion as deliberate, not tampering. Same HONEST SCOPE as forget_subject: erases within THIS mnemo
        store only, not the app's vector store / logs / backups; not a compliance certification.

        Returns {erased, ids, request_id, tombstones}."""
        want = set(types) if types is not None else None
        cand = None
        if subject is not None:
            cand = {subject, Mnemo._canon_source(subject)}
        target = []
        for r in self._tenant_rows():
            tags = r.get("pii")
            if not tags:
                continue
            if want is not None and not (want & set(tags)):
                continue
            if cand is not None and not (cand & Mnemo._rec_sources(r)):
                continue
            target.append(r["id"])
        if not target:
            return {"erased": 0, "ids": [], "request_id": request_id, "tombstones": 0}
        now = time.time()
        res = self.forget(ids=target)
        for mid in res["ids"]:
            self._emit_tombstone(mid, now, request_id, basis=basis or "pii_minimization")
        return {"erased": res["forgotten"], "ids": res["ids"],
                "request_id": request_id, "tombstones": len(res["ids"])}

    def for_tenant(self, tenant: str):
        """Return a TENANT VIEW over THIS store (one physical store, many logically-isolated tenants). The view
        SHARES this store's items, caches, file, and config by reference — so `store.for_tenant('a')` and
        `store.for_tenant('b')` read/write ONE store with no clobber — but every write it makes is stamped with
        its tenant and every read/supersession/erasure it performs is hard-filtered to it (fail-closed, exactly
        like a tenant-bound Mnemo). Typical use: the operator holds the unbound `store` (admin/migration view)
        and hands each request a `store.for_tenant(user_id)` handle that cannot see another tenant's data.

            store = Mnemo(path="all.json")
            acme = store.for_tenant("acme"); acme.remember("secret", key="k", object="s")
            globex = store.for_tenant("globex")
            acme.recall("secret")     # -> only acme rows; globex.recall(...) never sees them
        """
        return _TenantView(self, str(tenant))

    def erasure_report(self) -> dict:
        """Audit view of deliberate erasures: total tombstones + each {memory_id, ts, request_id}. Read-only;
        carries NO erased content (by construction). The durable proof-of-deletion trail behind forget_subject."""
        return {"tombstoned_total": len(self._tombstones),
                "erasures": [{"memory_id": t["memory_id"], "ts": t.get("ts"),
                              "request_id": t.get("request_id"), "signed": "sig" in t}
                             for t in self._tombstones]}

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

        HONEST SCOPE (read before relying on it for compliance): erasure is WITHIN this mnemo store only — NOT
        the app's vector store, prompt logs, or backups — and it covers the subject PLUS its derived_from
        lineage (a summary built from the subject's data is erased too). It is a tamper-evident INTEGRITY
        primitive, NOT a compliance certification. The tombstone proves the ACT of deletion (a record with this
        surrogate id was erased at T for request R), never the CONTENT (a hash of PII is still PII). The
        signature is load-bearing only against a party who does NOT hold receipt_key (the operator who holds
        the key can forge tombstones too — anchor the chain head externally for operator-adversarial audit).
        Prior art: crypto-shredding; Cassandra / event-sourcing tombstones; GDPR Art.17/30 erasure logs;
        Crosby-Wallach / Certificate Transparency tamper-evident logs."""
        ok, problems = self.verify_writes(expected_pubkey)
        by_req: dict = {}
        for t in self._tombstones:
            by_req.setdefault(t.get("request_id"), []).append(t.get("memory_id"))
        return {
            "erasures_total": len(self._tombstones),
            "by_request": {rid: {"erased": len(ids), "memory_ids": sorted(i for i in ids if i)}
                           for rid, ids in by_req.items()},
            "proof": {
                "verified": ok,
                "problems": problems,
                "all_signed": bool(self._tombstones) and all("sig" in t for t in self._tombstones),
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
            "scope": ("Erasure is within THIS mnemo store only (not the app's vector store, prompt logs, or "
                      "backups); covers the subject PLUS its derived_from lineage. Tamper-evident integrity "
                      "primitive, NOT a compliance certification. The tombstone proves the ACT of deletion, "
                      "never the content; its signature is load-bearing only against a non-holder of "
                      "receipt_key. Anchor the chain head externally for operator-adversarial audit."),
        }

    @staticmethod
    def _chain_core(rec: dict, kind: str) -> dict:
        if kind == "write":
            return {k: rec.get(k) for k in ("seq", "ts", "memory_id", "commit", "prev")}
        return Mnemo._tombstone_core(rec)                                                   # tombstone

    def _recompute_tip(self, records, n: int, kind: str):
        """Re-derive the hash-chain tip over the FIRST n records from genesis, verifying each record's own
        hash and prev-link as it goes. Returns the tip hash, or None if the prefix is internally inconsistent
        (a record whose stored hash doesn't match its recomputed content, or a broken prev-link)."""
        prev = _GENESIS
        for r in records[:n]:
            if r.get("prev") != prev:
                return None
            h = _sha256_hex(_canon(Mnemo._chain_core(r, kind)))
            if h != r.get("hash"):
                return None
            prev = h
        return prev

    def anchor(self, sign=None) -> dict:
        """Emit a Certificate-Transparency-style SIGNED TREE HEAD — a compact, EXTERNALLY-publishable commitment
        to the entire write + tombstone history at this instant: {n_writes, writes_tip, n_tombstones,
        tombstones_tip, ts}. Because each chain is hash-linked, its tip hash commits to every prior entry, so
        publishing this anchor to a place the operator cannot retroactively alter (a public log, a witness, the
        auditor's own records) closes the one hole verify_writes/governance_report cannot: an operator who HOLDS
        receipt_key can rewrite the whole history AND re-sign it so it verifies internally — but they cannot make
        the rewritten tip equal an anchor an outsider already witnessed. This is the CT model (Laurie-Langley-
        Kasper RFC 6962): the log is untrusted; external witnesses + consistency proofs make append-only violations
        detectable without trusting the log operator. `sign(bytes)->hex` (OPT-IN) lets an EXTERNAL witness co-sign
        the anchor; mnemo deliberately does NOT sign it with receipt_key (that key is the very thing not trusted
        here). HONEST BOUNDARY: mnemo produces the anchor and the consistency proof; the external WITNESSING (that
        the auditor recorded a prior anchor out of band) is the auditor's job — without a prior witnessed anchor
        there is nothing to be consistent WITH."""
        writes_tip = self._receipts[-1]["hash"] if self._receipts else _GENESIS
        tomb_tip = self._tombstones[-1]["hash"] if self._tombstones else _GENESIS
        sth = {"n_writes": len(self._receipts), "writes_tip": writes_tip,
               "n_tombstones": len(self._tombstones), "tombstones_tip": tomb_tip,
               "ts": time.time()}
        sth["sth_hash"] = _sha256_hex(_canon({k: sth[k] for k in
                                              ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip")}))
        if sign is not None:
            try:
                sth["witness_sig"] = sign(bytes.fromhex(sth["sth_hash"]))
            except Exception:
                pass
        return sth

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

    def retract_lineage(self, subject: str, reason: str = "lineage_corrected") -> dict:
        """Lineage-aware correction: the MIDDLE PATH between a value-only supersession (which leaves records
        DERIVED from a now-corrected fact still active — the knowledge-editing 'ripple effect', Cohen et al.
        RippleEdits, TACL 2024) and forget_subject (which HARD-DELETES the lineage, losing the legitimate
        payload entangled in those derived facts). retract_lineage DEMOTES `subject` and every record that
        inherited it through derived_from taint to status='superseded' — excluded from default recall but
        RETAINED (recallable with include_superseded) and stamped needs_rederivation, so an app can re-derive
        the affected facts against the corrected root rather than lose them. This is retract-and-retain +
        dependency-directed propagation — classic Truth-Maintenance (Doyle, AIJ 1979) and provenance/bitemporal
        invalidation-with-retention, recently ported to LLM-agent memory (TOKI, arXiv 2606.06240; MemLineage,
        arXiv 2605.14421); mnemo's contribution is only that it rides the same derived_from taint as forget_
        subject, so it needs no separate graph. CAVEAT: it can only cascade on links that were actually
        recorded — derived writes that never carried derived_from are invisible to it. `subject` matches
        canonical sources exactly like forget_subject. Returns {demoted, ids}. Reversible: nothing is deleted;
        only status + meta change."""
        cand = {subject, Mnemo._canon_source(subject)}
        targets = [r for r in self.items if r.get("status") == "active" and (cand & Mnemo._rec_sources(r))]
        now = time.time()
        ids = []
        for r in targets:
            r["status"] = "superseded"
            r["invalidated_at"] = now
            meta = r.setdefault("meta", {})
            meta["retracted_reason"] = reason
            meta["needs_rederivation"] = True
            ids.append(r["id"])
        if ids:
            self._mat = None; self._mat_built_n = -1        # status change alters the recall pool
            self._save(force=True)
        return {"demoted": len(ids), "ids": sorted(ids)}

    def rederive(self, subject: str, rewrite=None, key: str | None = None) -> dict:
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
        cand = {subject, Mnemo._canon_source(subject)}
        flagged = [r for r in self.items
                   if (r.get("meta") or {}).get("needs_rederivation")
                   and not (r.get("meta") or {}).get("rederived_to")
                   and (cand & Mnemo._rec_sources(r))]
        if not flagged:
            return {"rederived": 0, "skipped": 0, "ids": []}
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
        done, skipped, ids = 0, 0, []
        for r in flagged:
            if r.get("key"):                      # the root itself is replaced by the correction, not rederived
                continue
            try:
                nt = rewrite(r.get("text", ""), old_v, new_v)
            except Exception:
                nt = None
            if not nt or nt == r.get("text"):
                skipped += 1
                continue
            rid = self.remember(nt, tags=r.get("tags"), value=r.get("value", 1.0), mtype=r.get("mtype"),
                                derived_from=[cur_id], meta={"rederived_from": r["id"]})
            r.setdefault("meta", {})["rederived_to"] = rid
            done += 1
            ids.append(rid)
        if done:
            self._save(force=True)
        return {"rederived": done, "skipped": skipped, "ids": ids, "old_value": old_v, "new_value": new_v}

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
        byte-identical text differing only in provenance. mnemo resolves this by CHANNEL SEPARATION:
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
        rid = self.remember(tgt["text"], tags=tgt.get("tags"), value=tgt.get("value", 1.0),
                            mtype=tgt.get("mtype"), key=key, object=tgt.get("object"),
                            reaffirm=True, capability=capability,
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
            rid = self.remember(tgt["text"], tags=tgt.get("tags"), value=tgt.get("value", 1.0),
                                mtype=tgt.get("mtype"), key=key, object=tgt.get("object"),
                                reaffirm=True, capability=_SANCTIONED,
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
        rid = self.remember(f"restore {key} to {target}", key=key, object=target,
                            reaffirm=True, capability=_SANCTIONED,
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
        joint TAT/mnemo analysis (Marat Sultanov), factorized into its two independent halves and shipped:

          1. REFERENCE RESOLUTION (a text problem): embed the candidate and, using the ledger's own split of
             the key's history into SUPERSEDED (old) and CURRENT records, measure how much closer the
             candidate sits to the old side than the current side. Needs an embedder (`self.embed` or the
             `embed` arg); with none it abstains rather than guessing. This is the structural-similarity step,
             scored as a MARGIN (max sim to old records minus max sim to current records) — the same
             discriminating quantity the decomposition used, not an absolute similarity.
          2. RECENCY ATTRIBUTION (a ledger problem): the old-versus-current split is read straight from
             mnemo's supersession ledger. No text method is asked to decide which value is current.

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

    def _route_chain(self, key: str) -> list[str]:
        """values that were actually CURRENT at some point for `key`, oldest->newest — skips arrivals the
        guards retired stale-on-arrival (echo_blocked / objectless_blocked were never the current value)."""
        chain = []
        for r in self.items:
            if r.get("key") != key or r.get("object") is None:
                continue
            m = r.get("meta") or {}
            if m.get("echo_blocked") or m.get("objectless_blocked"):
                continue
            if not chain or chain[-1] != r["object"]:
                chain.append(r["object"])
        return chain

    def _route_key(self, low: str) -> str | None:
        """match the utterance to a ledgered key by token presence (longest key wins)."""
        keys = {r["key"] for r in self.items if r.get("key") and r.get("object") is not None}
        hits = [k for k in keys if k.lower() in low]
        return max(hits, key=len) if hits else None

    def route(self, text: str, key: str | None = None, object: str | None = None,
              context: str | None = None, policy: str = "safe", capability: str | None = None) -> dict:
        """WRITE-PATH INTENT ROUTER: tag an utterance (assert / correct / revert / echo), resolve a fuzzy
        version reference against the key's timeline, and execute the right ledger operation — so a
        value-obscuring revert ("go back to what we had") works without the caller naming a value, and a
        similarity/cosine path never runs on a revert (a revert is an instruction on the version graph,
        not a value). This ships the split measured in mnemo/probes/intent_tagger_router_probe.py.

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
            1.00 echo-blocked / 0.00 reaffirm-honored).
          - "context": restore when `context` (the preceding turn) shows change-awareness (a change word
            + the current value). Separates honest twins (1.00/1.00) but is FORGEABLE — an attacker who
            writes two turns walks through it (forged-context echo restored 100%). Use only when the
            context channel is trusted.
          - "trusting": treat as a reaffirm — always restores (0.00 echo-blocked / 1.00 honored).
        The unforgeable separator is provenance — an authorized revert() call or an explicit marker —
        never smarter classification; that is the channel-separation thesis, now with the receipt.

        Returns {"intent", "action", "key", ...} describing what was done."""
        low = text.lower()
        if (key is None or object is None) and self.extractor is not None:
            try:
                ex = self.extractor(text)
                if ex:
                    key = key if key is not None else ex[0]
                    object = object if object is not None else ex[1]
            except Exception:
                pass
        if self._ROUTE_REVERT.search(low):
            k = key or self._route_key(low)
            if k is None:
                rid = self.remember(text)
                return {"intent": "revert", "action": "noted", "key": None, "id": rid,
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
                rid = self.remember(f"restore {k} to {named}", key=k, object=named, reaffirm=True,
                                    capability=capability, meta={"routed": "revert_named"})
                return {"intent": "revert", "action": "restored", "key": k, "target": named, "id": rid}
            if self._ROUTE_ORIGINAL.search(low) and len(chain) > 1 and chain[0] != cur:
                rid = self.remember(f"restore {k} to {chain[0]}", key=k, object=chain[0], reaffirm=True,
                                    capability=capability, meta={"routed": "revert_original"})
                return {"intent": "revert", "action": "restored", "key": k, "target": chain[0], "id": rid}
            res = self.revert(k, capability=capability)
            return {"intent": "revert", "action": "reverted" if res.get("ok") else "failed",
                    "key": k, **{kk: vv for kk, vv in res.items() if kk != "ok"}}
        if object is None or key is None:
            rid = self.remember(text, key=key, object=object)
            return {"intent": "assert", "action": "remembered", "key": key, "id": rid}
        chain = self._route_chain(key)
        cur = chain[-1] if chain else None
        if object == cur or object not in chain:
            rid = self.remember(text, key=key, object=object)
            intent = "correct" if (cur is not None and self._ROUTE_CORRECT.search(low)) else "assert"
            return {"intent": intent, "action": "remembered", "key": key, "id": rid}
        # unmarked assertion of a superseded value — the ambiguous echo-or-reaffirm case
        if policy == "trusting" or (
                policy == "context" and context and self._ROUTE_CHANGE_AWARE.search(context.lower())
                and cur is not None and str(cur).lower() in context.lower()):
            if not self._revert_authorized(key, capability):
                return {"intent": "reaffirm", "action": "authorization_required", "key": key,
                        "target": object, "challenge": self.revert_challenge(key)}
            rid = self.remember(text, key=key, object=object, reaffirm=True, capability=capability,
                                meta={"routed": f"reaffirm_{policy}"})
            return {"intent": "reaffirm", "action": "restored", "key": key, "target": object, "id": rid}
        if self.echo_guard:
            rid = self.remember(text, key=key, object=object)      # guard retires it, judge-logged
        else:
            rid = self.remember(text, meta={"routed": "echo_unkeyed"})  # keyless: cannot LWW-clobber
        return {"intent": "echo", "action": "blocked", "key": key, "id": rid,
                "policy": policy, "note": "unmarked restatement of a superseded value; not restored"}

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

        Returns {object, text, valid_from, invalidated_at, id} for the record valid at `when`, or
        None if nothing was known for `key` yet at that time. Ties (overlapping intervals from an
        unclean history) resolve to the latest valid_from <= when.

        BITEMPORAL (1.5.0): pass `as_recorded` (a transaction-time epoch) to reconstruct the KNOWLEDGE STATE as
        of that recording time — "what did we BELIEVE, at tx-time `as_recorded`, was true at valid-time `when`" —
        using only records written by then (ts <= as_recorded) with supersession recomputed within that set, so a
        correction recorded LATER cannot leak into the earlier belief. This is the second clock: valid-time
        (`when`, world truth) x transaction-time (`as_recorded`, what the store knew). Audit/replay: "what did the
        agent believe when it acted", provably, without the later correction contaminating the reconstruction."""
        if as_recorded is None:
            best = None
            for r in self.items:
                if r.get("key") != key:
                    continue
                vf = r.get("valid_from", r["ts"])
                inv = r.get("invalidated_at")
                if vf <= when and (inv is None or inv > when):
                    if best is None or vf > best.get("valid_from", best["ts"]):
                        best = r
        else:
            # transaction-time filter: only records written by `as_recorded`; supersession recomputed within it
            # (a record is superseded only by a LATER-valid_from record that was itself already recorded by then).
            cands = [r for r in self.items if r.get("key") == key
                     and r.get("valid_from", r["ts"]) <= when and r["ts"] <= as_recorded]
            best = max(cands, key=lambda r: (r.get("valid_from", r["ts"]), r["ts"]), default=None)
        if best is None:
            return None
        out = {"object": best.get("object"), "text": best.get("text"),
               "valid_from": best.get("valid_from", best["ts"]),
               "invalidated_at": best.get("invalidated_at"), "id": best["id"]}
        if as_recorded is not None:
            nxt = [r.get("valid_from", r["ts"]) for r in self.items
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

    def history(self, key: str) -> list[dict]:
        """The full validity timeline for `key`: every value it has held, in event-time order, each
        with its [valid_from, invalidated_at) interval, status, and — when it was retired — WHICH
        policy adjudicated the retirement (meta['superseded_by_policy']). The audit trail behind as_of()."""
        recs = [r for r in self.items if r.get("key") == key]
        recs.sort(key=lambda r: r.get("valid_from", r["ts"]))
        return [{"object": r.get("object"), "text": r.get("text"), "status": r.get("status"),
                 "valid_from": r.get("valid_from", r["ts"]), "invalidated_at": r.get("invalidated_at"),
                 "policy": (r.get("meta") or {}).get("superseded_by_policy"),
                 "id": r["id"]} for r in recs]

    def supersession_report(self) -> dict:
        """Audit view of WHY memories were retired: a count of superseded records per adjudicating
        policy (keyed_lww / keyed_lww_backfill / keyed_reaffirm / echo_guard / objectless_guard /
        state_toggle / toggle_corroborated / toggle_persistence / keep_budget; 'unstamped' = retired
        before 0.6.18 or by an external edit). Every supersession site stamps
        meta['superseded_by_policy'] at write/consolidate time, so the resolver that adjudicated each
        conflict is inspectable per record — the write-time judge log TOKI (arXiv:2606.06240) points
        out most memory systems omit. Read-only; the raw rows stay untouched."""
        counts: dict = {}
        for r in self.items:
            if r.get("status") != "superseded":
                continue
            p = (r.get("meta") or {}).get("superseded_by_policy") or "unstamped"
            counts[p] = counts.get(p, 0) + 1
        return {"superseded_total": sum(counts.values()), "by_policy": counts}

    # ── retrieval (value-ranked) ──────────────────────────────────────────────
    def _qvec(self, query: str):
        """Embed a query ONCE per scan, or None (no embedder / failure). Callers pass the result
        into _similarity so a recall over N memories costs 1 embedding, not N."""
        if not self.embed:
            return None
        try:
            return self.embed(query)
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
        lexical channel that makes the hybrid beat either alone (mnemo/probes/locomo_retrieval_map.py)."""
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
        out = []
        for c, L in zip(counts, dl):
            s = 0.0
            for t in qtok:
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

    def recall(self, query: str, k: int = 6, include_superseded: bool = False,
               include_hubs: bool = False, mode: str = "auto", min_relevance: float = 0.0,
               scope: str | None = None, as_of: float | None = None,
               where: dict | None = None, influence_only: bool = False,
               prefer=None, prefer_trust: float = 1.0,
               prefer_max_boost: float | None = None, near: dict | None = None,
               tie_recent: float | None = None,
               with_status: bool = False, with_warrant: bool = False,
               redact_pii: bool = False, rerank=None, rerank_pool: int | None = None) -> list[dict]:
        """Top-k memories by RELEVANCE × VALUE — high-value memories outrank merely-similar ones.
        Memories the dream pass flagged as hubs (universal matchers) are skipped unless include_hubs.

        mode: 'auto' (default) uses LEXICAL token overlap while the store is small (< semantic_threshold
        active memories) and a LEXICAL+SEMANTIC HYBRID (Reciprocal Rank Fusion) once it grows past that —
        the hybrid robustly beat either channel alone in our agent-memory benchmark (details in the recall
        body / mnemo/probes/locomo_retrieval_map.py). Force a single channel with mode='lexical' /
        'semantic', or the fusion explicitly with mode='hybrid'. Semantic/hybrid need an embedder (set on
        the store); without one, or if embedding fails, recall falls back to lexical automatically.

        where: an OPT-IN metadata pre-filter applied to the candidate pool BEFORE ranking — the cheap
        'filter before you rank' lever (measured on LoCoMo: a metadata pre-filter can beat retriever choice;
        mnemo/probes/locomo_metadata_prefilter.py). A dict of field -> condition; a record must match ALL
        fields (AND). Each field is matched against the record's top-level attributes first, then its meta
        dict, so both `valid_from`/`mtype`/`key` and any `meta` key work. A condition is either a scalar
        (equality), a list/tuple/set (membership), or a dict of operators:
        {"$gte","$lte","$gt","$lt","$in","$nin","$ne","$contains"} — e.g. a time range
        where={"valid_from": {"$gte": t0, "$lte": t1}} (hard-filter the SOLVED half), or a closed-set
        entity where={"speaker": {"$in": ["Caroline","Mel"]}}. NOTE: this is a HARD filter — a record that
        doesn't match is removed, so on lossy/predicted extraction prefer a broad/loose filter (or rerank)
        over an aggressive one, since a wrong filter hard-deletes the answer (measured harm mode).

        influence_only (OPT-IN, default False -> zero behavior change): restrict the result to CORROBORATED
        memories — those that meet the same bar mnemo uses for episodic->semantic GRADUATION (an EARNED
        net-positive outcome via credit() [good>0 and good>=bad], OR already-graduated 'semantic' type, OR
        >=2 DISTINCT-canonical-source corroborating links). This is the retrieve-then-INFLUENCE split: recall
        freely for context, but call with influence_only=True for the set that is allowed to DRIVE an action.
        MEASURED (mnemo/probes/agentpoison_influence_gate*.py) against a real AgentPoison-style single-
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
        — mnemo/probes/oracle_separation_density.py) and the load-bearing caveat that it rides on an
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
        case). MEASURED (mnemo/probes/locomo_soft_prefer_filter.py) on LoCoMo: soft prefer weighted by
        alias-strength gives the filter's benefit on reliable (exact-name) queries while backing off on
        ambiguous ones where extraction fails -- beating both no filter and a hard `where` filter under
        imperfect extraction. Reversible: prefer=None = legacy recall.

        MULTI-DIMENSION prefer (compose several soft cues at once): pass `prefer` as a LIST of specs, each
        either {"cond": <dict>, "trust": <0..1>} or a (cond, trust) tuple. Matching dimensions compose as a
        PRODUCT of neutral-at-1.0 factors — pref = Π (1 + trust_i * gain) over the dims a record matches — so
        a record matching two cues is boosted more than one matching a single cue, and non-matching dims are
        inert (factor 1.0). A single dict + scalar prefer_trust is the one-dimension case (unchanged). Cap the
        TOTAL boost with `prefer_max_boost` (a ceiling on the product, like Elasticsearch function_score's
        max_boost); default None = uncapped. MEASURED (mnemo/probes/locomo_composed_soft_filters.py) on LoCoMo:
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
        value wins rank-1 in 32.7% of update questions, identically for raw cosine and mnemo semantic —
        mnemo/probes/membench_recall_probe_v2.py). Pass a small similarity epsilon (measured sweet spot
        0.02-0.05 on centered cosine): candidates whose RELEVANCE is within tie_recent of the strongest
        candidate's relevance are re-ordered newest-first (by valid_from, falling back to ts) ahead of the
        rest; everything below the band keeps its score order. MEASURED
        (mnemo/probes/membench_recency_tiebreak_probe.py, 222 questions incl. 3 control splits):
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
        # MEASURED (mnemo/probes/continuous_chunk_recall_probe.py): on a real TAT 5-D state trace, near-boost on
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
        pool = [r for r in self.items if _eligible(r)]
        # HARD TENANT ISOLATION (fail-closed, non-bypassable): a tenant-bound store sees ONLY its own tenant's
        # records, always — this is enforced here on the STORE, not via a caller argument, so no forgotten
        # parameter can leak another tenant's data. An unbound store (tenant=None) is the admin view (sees all).
        if self.tenant is not None:
            pool = [r for r in pool if r.get("tenant") == self.tenant]
        # Scope/namespace isolation: when a scope is requested, recall ONLY sees memories tagged with that scope
        # (meta['scope']) BEFORE ranking — a shared store (e.g. many agents / tenants in one Mnemo) cannot bleed
        # one scope's memories into another's recall. scope=None (default) sees everything (legacy behavior).
        if scope is not None:
            pool = [r for r in pool if (r.get("meta") or {}).get("scope") == scope]
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
        # lexical->semantic at the threshold). Receipt: mnemo/probes/locomo_retrieval_map.py. RRF needs no
        # tuning and no extra dependency. Force a single channel with mode='lexical'/'semantic'.
        has_embed = self.embed is not None
        if mode == "lexical" or not has_embed:
            sel = "lexical"
        elif mode in ("semantic", "hybrid"):
            sel = mode
        else:                                                 # 'auto': fuse once the store is worth it
            sel = "hybrid" if len(pool) >= self.semantic_threshold else "lexical"
        qvec = self._qvec(query) if sel in ("semantic", "hybrid") else None
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
        scored.sort(key=lambda x: -x[0])
        # Near-tie recency reorder (OPT-IN via tie_recent; see docstring for the measured provenance).
        # Band on RELEVANCE (sim), not the composite score: the composite mixes value/calibration channels
        # whose scale varies per store, while sim is the [0,1] channel the epsilon was measured on.
        if tie_recent is not None and scored:
            _eps = max(0.0, float(tie_recent))
            _top_sim = max(t[1] for t in scored)
            _tied = [t for t in scored if t[1] >= _top_sim - _eps]
            _rest = [t for t in scored if t[1] < _top_sim - _eps]
            _tied.sort(key=lambda t: -(t[2].get("valid_from") or t[2]["ts"]))
            scored = _tied + _rest
        # OPT-IN reranker hook (retrieve-then-rerank). `rerank(query, records) -> list[float]` (one relevance
        # score per record, higher=better) lets a caller plug a cross-encoder / model reranker over the top
        # candidates — the one lever MEASURED to lift multi-hop recall beyond mnemo's zero-LLM base (LoCoMo
        # multi-hop full-recall ~0.30 -> ~0.48 with a reader-reranker; [[locomo-iterative-lever-full-benchmark]]).
        # Model-agnostic (mnemo never imports a model) and MOAT-SAFE: no LLM runs unless the caller supplies one,
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
            rel = (sim / _top_sim) if _top_sim > 0 else 1.0
            r["value"] += 0.25 * rel
            r["last_access"] = _now                 # ...and resets the per-type decay clock
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
            _good = float(r.get("good", 0) or 0); _bad = float(r.get("bad", 0) or 0)
            # graduation shares the influence-gate bar, incl. the exogenous-warrant rule: when
            # credit_requires_warrant is on, only warranted good can graduate an episodic memory to the
            # durable semantic tier — else a MINJA self-graded bridge would graduate and then pass the gate
            # unconditionally via its 'semantic' mtype (the graduation bypass this closes).
            _good_earned = (float(r.get("good_warranted", 0) or 0)
                            if getattr(self, "credit_requires_warrant", False) else _good)
            _links = (self._gated_links(r, _by_id)
                      if (self.coherence_gate is not None or self.temporal_gate is not None) else r.get("links"))
            _distinct = (self._distinct_verified_keys(_links, _by_id) if self.strict_corroboration
                         else self._distinct_sources(_links, _by_id))
            corroborated = ((_good_earned > 0 and _good >= _bad) or _distinct >= 2) \
                and not (r.get("meta") or {}).get("slashed") \
                and not r.get("orphan")   # landed retraction OR orphan (no lineage) blocks (re-)graduation too
            if r.get("mtype") == "episodic" and r["value"] >= _GRADUATE_VALUE and corroborated:
                r["mtype"] = "semantic"
                r.setdefault("meta", {})["graduated_from_episodic"] = True
            _o = {"id": r["id"], "text": r["text"], "tags": r["tags"], "iso": r["iso"],
                  "value": round(r["value"], 2), "relevance": round(sim, 3),
                  "score": round(score, 3), "links": r["links"],
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
                #   'earned'       -- un-self-gradable outcome credit (good>0>=bad) OR a graduated semantic memory
                #   'corroborated' -- >=2 distinct sources/verified-keys, but not yet outcome-earned (weaker)
                #   'unwarranted'  -- single self-asserted, orphan (no lineage), or slashed -> DO NOT treat as a
                #                     confirmation; weight it ~0 and, critically, mark it so downstream sees the abstention.
                if (r.get("meta") or {}).get("slashed") or r.get("orphan"):
                    _o["warrant"] = "unwarranted"
                elif (_good > 0 and _good >= _bad) or r.get("mtype") == "semantic":
                    _o["warrant"] = "earned"
                elif _distinct >= 2:
                    _o["warrant"] = "corroborated"
                else:
                    _o["warrant"] = "unwarranted"
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
        # NOTE: recall is a READ. It nudges in-memory access value / graduation, but must NOT persist the
        # whole store here — serializing (json.dumps) on every recall, across many agents' stores,
        # saturated the thread pool and FROZE the world. The in-memory nudges are persisted on the next
        # remember()/consolidate()/flush(); losing recent access metadata on a hard crash is harmless.
        if out:
            self._dirty = True   # mark for the next throttled/forced save; do NOT serialize on the read path
        return out

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
        own = Mnemo._canon_source(doc) if doc else "id:" + rec["id"]
        return {own} | set(rec.get("taint") or [])

    @staticmethod
    def _distinct_sources(links, by_id) -> int:
        """Count DISTINCT canonical sources among corroborating links — entity resolution BEFORE counting,
        so 'three names for one source' sybil variants count as one. A link whose record carries no source
        counts as its own id, so genuinely source-less corroboration is not penalised (no regression)."""
        keys = set()
        for lid in (links or []):
            lr = by_id.get(lid)
            if lr is None:
                continue
            src = lr.get("source")
            doc = src.get("doc") if isinstance(src, dict) else (src if isinstance(src, str) else None)
            keys.add(Mnemo._canon_source(doc) if doc else "id:" + lid)
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
        set. Measured: mnemo/probes/minja_influence_gate.py (self-graded ASR 80% -> 0% with the flag on).
        A LANDED RETRACTION WINS: a record slash()'d (meta['slashed']) is not corroborated on ANY path — incl.
        distinct-link corroboration — so a caught poison cannot stay load-bearing via independent-looking links
        (jacksonxly's invariant: nothing false stays load-bearing past the correctness signal). restore() clears
        the flag, so this is reversible; receipt mnemo/probes/retraction_propagation.py.
        FAIL-CLOSED PROVENANCE: an ORPHAN (a declared transformation output that named no parent, meta-flag
        rec['orphan']) is likewise not corroborated on any path -- missing lineage is treated as unverified, so
        an app-side summary that dropped its derived_from cannot quietly earn standing or survive a retraction."""
        if (rec.get("meta") or {}).get("slashed") or rec.get("orphan"):
            return False
        good = float(rec.get("good", 0) or 0)
        bad = float(rec.get("bad", 0) or 0)
        good_earned = float(rec.get("good_warranted", 0) or 0) if require_warrant else good
        if good_earned > 0 and good >= bad:
            return True
        if rec.get("mtype") == "semantic":
            # A 'semantic' mtype counts as corroborated because it is normally an EARNED, graduated-durable
            # memory. But remember() also auto-classifies short declarative statements as semantic AT WRITE
            # TIME — and MINJA's progressive-shortening bridges are exactly such query-shaped declaratives, so
            # they would be born semantic and bypass the gate with zero corroboration. Under require_warrant
            # only EARNED semantic (graduated_from_episodic through the corroboration bar) passes; a write-time
            # semantic classification is treated as an unproven episodic claim.
            if not require_warrant or (rec.get("meta") or {}).get("graduated_from_episodic"):
                return True
        if strict:
            return Mnemo._distinct_verified_keys(rec.get("links"), by_id) >= 2
        return Mnemo._distinct_sources(rec.get("links"), by_id) >= 2

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
        entity-resolved `source.doc`/string, else 'id:'+its id. Also exposes the attested key as 'key:<k>'."""
        src = rec.get("source")
        doc = src.get("doc") if isinstance(src, dict) else (src if isinstance(src, str) else None)
        return Mnemo._canon_source(doc) if doc else "id:" + rec.get("id", "")

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
        return Mnemo._is_corroborated(rec, by_id, self.strict_corroboration,
                                      require_warrant=getattr(self, "credit_requires_warrant", False))

    def influence_gate_report(self) -> dict:
        """Report the LIVE COST of the influence gate (recall(influence_only=True)) on THIS store, so you can
        judge whether it is affordable before enabling it. The gate keeps only CORROBORATED memories
        (_is_corroborated); its cost is that not-yet-earned LEGITIMATE memories are filtered too, and that cost
        is DENSITY-DEPENDENT. MEASURED on a controlled corpus with real embeddings
        (mnemo/probes/oracle_separation_density.py): the fraction of legitimate high-stakes recalls the gate
        blocks falls from ~51% when each memory is used ~once (sparse) to ~6% when each is used ~8x (dense),
        because a legit memory only earns standing through repeated successful use — so in a SPARSE store the
        gate is expensive (it filters most legit recalls); grow density, or credit() real outcomes, before
        relying on influence_only for anything but adversarial/untrusted ingestion.
        A SECOND, load-bearing caveat the same probe measured: the gate rides ENTIRELY on credit() being an
        outcome oracle the attacker CANNOT SELF-GRADE. A MINJA-style self-graded outcome (arXiv:2503.03704)
        collapses the gate at every density — it can even block legit MORE than poison. Never let recalled
        memory content drive its own credit(); issue outcomes from the application, on real resolved work.
        Returns {active, corroborated, corroborated_frac, would_block_frac, by_path{earned_outcome, semantic,
        multi_source}, advice}. Read-only; no side effects."""
        byid = {x["id"]: x for x in self.items}
        active = [r for r in self.items if r.get("status") == "active"]
        n = len(active)
        earned = sem = multi = corr = 0
        for r in active:
            g = float(r.get("good", 0) or 0); b = float(r.get("bad", 0) or 0)
            if g > 0 and g >= b:
                corr += 1; earned += 1
            elif r.get("mtype") == "semantic":
                corr += 1; sem += 1
            elif (self._distinct_verified_keys(r.get("links"), byid) if self.strict_corroboration
                  else self._distinct_sources(r.get("links"), byid)) >= 2:
                corr += 1; multi += 1
        frac = (corr / n) if n else 0.0
        advice = ("cheap - most active memories are corroborated" if frac >= 0.7 else
                  "affordable" if frac >= 0.4 else
                  "expensive - store too sparse; influence_only will filter most legit recalls. Grow density "
                  "or credit() real outcomes first, or use it only for untrusted-ingestion defense.")
        return {"active": n, "corroborated": corr, "corroborated_frac": round(frac, 3),
                "would_block_frac": round(1.0 - frac, 3), "strict_corroboration": self.strict_corroboration,
                "by_path": {"earned_outcome": earned, "semantic": sem,
                            ("multi_verified_key" if self.strict_corroboration else "multi_source"): multi},
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
        raises attacker cost and is meant to be paired with verifiable provenance, not a proof of truth."""
        good = Mnemo._outcome_good(outcome)
        by_id = {x["id"]: x for x in self.items}
        key, updated = ("good" if good else "bad"), []
        for i in (ids or []):
            rec = by_id.get(i)
            if rec is None:
                continue
            rec[key] = float(rec.get(key, 0) or 0) + float(weight)
            if good and self._warrant_is_exogenous(rec, warrant):
                rec["good_warranted"] = float(rec.get("good_warranted", 0) or 0) + float(weight)
            updated.append(i)
        if updated:
            self._save()
        return {"updated": updated, "outcome": key, "weight": weight}

    def _warrant_is_exogenous(self, rec: dict, warrant) -> bool:
        """A warrant vouches for an outcome the record did NOT author itself. Exogenous = a non-empty token
        that is neither the record's own canonical source nor any tenant/source in its transitive lineage.
        Conservative by design: an absent warrant is never exogenous, so self-graded credit (the MINJA path)
        earns no warranted-good."""
        if not warrant:
            return False
        w = str(warrant).strip().lower()
        if not w:
            return False
        own = {str(s).strip().lower() for s in Mnemo._rec_sources(rec) if s}
        own.discard("")
        if w in own:
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
        a fundamental ceiling; mnemo/probes/retrieval_exposure_coverage_probe.py + the outcome-propagation
        lift measured in mnemo/probes/outcome_propagation_probe.py).

        `ids` defaults to the last recall set (self._last_recall). `driving_only=True` (default) restricts
        the credited set to the DECISION-DRIVING subset: pass the specific id(s) the action actually used
        (the app knows which memory it acted on), or, if ids is None, mnemo credits only the recall set's
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
        if kind not in Mnemo._RATIFY_KINDS:
            raise ValueError(f"kind must be one of {Mnemo._RATIFY_KINDS}")
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
        multi = (Mnemo._distinct_verified_keys(rec.get("links"), by_id) >= 2) if strict \
            else (Mnemo._distinct_sources(rec.get("links"), by_id) >= 2)
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
        n_src = Mnemo._distinct_sources(rec.get("links"), by_id)
        n_keys = Mnemo._distinct_verified_keys(rec.get("links"), by_id)
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
            rank = {gr: i for i, gr in enumerate(Mnemo._GRADES)}
            par_grades = [self.grade(by_id[p], _by_id=by_id)["grade"] for p in parents]
            lineage_grade = min([g["grade"]] + par_grades, key=lambda gr: rank.get(gr, 0))
            if rank.get(lineage_grade, 0) < rank.get(g["grade"], 0):
                notes.append("lineage-capped: derived from a weaker input (grade '%s') -- a derived memory is "
                             "only as adjudicated as its least-adjudicated parent" % lineage_grade)
        return {"status": status, "grade": g["grade"], "lineage_grade": lineage_grade,
                "distinct_sources": n_src, "distinct_verified_keys": n_keys, "corroborating_links": len(links),
                "low_source_diversity": low_diversity, "adjudicated": adjudicated,
                "notes": notes or ["no corroboration yet (claimed)"]}

    def slash(self, ids, scope: str = "source") -> dict:
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
        motivation: mnemo/probes/triad_attacker_split.py + reversibility_gate_frontier.py (the residual against a
        patient sleeper is a slow-cumulative in-domain attack; retroactive forfeiture, not a throughput cap, is
        the dominant control). Returns {slashed, sources, ids}. Records + raw text untouched; only good/bad/mtype
        change, auditable via meta['slashed']. Reversible: nothing is deleted."""
        by_id = {x["id"]: x for x in self.items}
        caught = [by_id[i] for i in (ids or []) if i in by_id]
        if scope == "source":
            bad_sources = set().union(*(Mnemo._rec_sources(r) for r in caught)) if caught else set()
            # a record is caught if its own source OR any inherited taint intersects the slashed sources ->
            # forfeiting a source also burns every derived summary/consolidation it fed (provenance-carried).
            targets = [r for r in self.items if r.get("status") == "active"
                       and (Mnemo._rec_sources(r) & bad_sources)]
            sources = sorted(bad_sources)
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
            if r.get("mtype") == "semantic":
                r["mtype"] = "episodic"          # revoke graduation, else it still passes _is_corroborated
            meta["slashed"] = True
            slashed.append(r["id"])
        if slashed:
            self._save()
        return {"slashed": len(slashed), "sources": sources, "ids": slashed}

    def restore(self, ids, scope: str = "source") -> dict:
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
        seed = [by_id[i] for i in (ids or []) if i in by_id]
        if scope == "source":
            srcs = set().union(*(Mnemo._rec_sources(r) for r in seed)) if seed else set()
            targets = [r for r in self.items if (Mnemo._rec_sources(r) & srcs) and (r.get("meta") or {}).get("slashed")]
            sources = sorted(srcs)
        else:
            targets, sources = [r for r in seed if (r.get("meta") or {}).get("slashed")], []
        restored = []
        for r in targets:
            meta = r.get("meta") or {}
            prev = meta.pop("pre_slash", None)
            if prev:                              # recover exact pre-slash standing
                r["good"] = float(prev.get("good", 0) or 0)
                r["bad"] = float(prev.get("bad", 0) or 0)
                r["mtype"] = prev.get("mtype", r.get("mtype", "episodic"))
            else:                                 # no record -> clean slate (must re-earn, don't snap to trusted)
                r["good"] = 0.0; r["bad"] = 0.0
            meta["slashed"] = False
            restored.append(r["id"])
        if restored:
            self._save()
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
        return self._cusum

    def _save_cusum(self):
        if self.path:
            try:
                (self.path.with_name(self.path.name + ".cusum.json")).write_text(
                    json.dumps(self._cusum, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    def monitor(self, ids, outcome, k: float = 0.3, h: float = 3.0,
                auto_slash: bool = False, weight: float = 1.0) -> dict:
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
        bad = 0.0 if Mnemo._outcome_good(outcome) else 1.0
        by_id = {x["id"]: x for x in self.items}
        recs = [by_id[i] for i in (ids or []) if i in by_id]
        srcs = set().union(*(Mnemo._rec_sources(r) for r in recs)) if recs else set()
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
                            if r.get("status") == "active" and s in Mnemo._rec_sources(r)), None)
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
                try:
                    self._irrev = json.loads((self.path.with_name(self.path.name + ".irrev.json"))
                                             .read_text(encoding="utf-8"))
                except Exception:
                    self._irrev = {}
        return self._irrev

    def _save_budget(self):
        if self.path:
            try:
                (self.path.with_name(self.path.name + ".irrev.json")).write_text(
                    json.dumps(self._irrev, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    def spend_irreversible(self, ids, amount: float = 1.0, budget: float = 1.0,
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
        recs = [by_id[i] for i in (ids or []) if i in by_id]
        srcs = sorted(set().union(*(Mnemo._rec_sources(r) for r in recs)) if recs else set())
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
        # mnemo/probes/soft_influence_taint_probe.py.
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
                    _full_srcs |= Mnemo._rec_sources(r)
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
        return {s: {"spent": round(float(v), 4), "remaining": round(max(0.0, float(budget) - float(v)), 4),
                    "exhausted": float(v) >= float(budget)}
                for s, v in sorted(B.items())}

    def _effective_value(self, r: dict, now: float) -> float:
        """Recall weight = stored value decayed by time since last access, at the memory's TYPE
        half-life (episodic fades fast, semantic slow, procedural barely). Access resets the clock,
        so memories that keep being useful stay alive while stored-but-never-recalled ones fade.
        Reversible: raw value/text are untouched; only the effective ranking weight decays."""
        hl = _HALFLIFE_S.get(r.get("mtype", "episodic"), _HALFLIFE_S["episodic"])
        age = max(0.0, now - r.get("last_access", r.get("ts", now)))
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
        `ask_followup(query, current_results) -> list[str]` is caller-supplied, so mnemo stays model-agnostic
        (inject any model/LLM). MEASURED ~3.3x multi-hop full-evidence recall vs one-shot top-k on LoCoMo
        (0.057 -> 0.186, n=70 across 3 conversations) — the one mechanism that moved the multi-hop bottleneck
        where static retrieval tricks (dense-neighbor, lexical bridges) did not. More expensive (a model call
        in the loop), so it's an explicit mode, not the default."""
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
        return list(seen.values())

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
        ranked = self.recall(query, k=k)
        rank_of = {r["id"]: i + 1 for i, r in enumerate(ranked)}
        _full = {x["id"]: x for x in self.items}          # recall() may return vec-less projections

        def _brk(rec):
            r = _full.get(rec["id"], rec)                 # resolve the full record so the vec is present
            sem = max(0.0, _cosine(qvec, r["vec"])) if (qvec is not None and r.get("vec")) else 0.0
            t = self._rec_tokens(r)
            lex = (len(qtok & t) / min(len(qtok), len(t))) if (qtok and t) else 0.0
            return {"id": r["id"], "text": (r.get("text") or "")[:80],
                    "semantic": round(float(sem), 4), "lexical": round(float(lex), 4),
                    "effective_value": round(self._effective_value(r, now), 4),
                    "good": float(r.get("good", 0) or 0), "bad": float(r.get("bad", 0) or 0),
                    "stale_derived": bool(r.get("_stale_derived")), "rank": rank_of.get(r["id"])}
        if id is not None:
            rec = next((r for r in self.items if r["id"] == id), None)
            if rec is None:
                return {"id": id, "found": False}
            b = _brk(rec); b["surfaced"] = rec["id"] in rank_of
            return b
        return [_brk(r) for r in ranked]

    def memory_report(self, dup_threshold: float = 0.9) -> dict:
        """INSPECTOR overview — 'what is in memory, and is it clean'. Counts active/superseded, by type,
        consolidated (linked), decayed (effective value < 10% of stored), and a near-duplicate REDUNDANCY estimate
        (active memories whose nearest active neighbour is >= dup_threshold, no value clash — sampled at 400 for
        cost). Read-only; the surface that proves a store did NOT accumulate 800 copies of one fact."""
        now = time.time()
        act = [r for r in self.items if r.get("status") == "active"]
        sup = [r for r in self.items if r.get("status") == "superseded"]
        from collections import Counter
        by_type = dict(Counter(r.get("mtype", "episodic") for r in act))
        linked = sum(1 for r in act if r.get("links"))
        decayed = sum(1 for r in act if self._effective_value(r, now) < 0.1 * float(r.get("value", 1.0) or 1.0))
        redundant = 0
        sample = act if len(act) <= 400 else act[:400]
        for r in sample:
            other = [h for h in self.recall(r["text"], k=2) if h["id"] != r["id"]]
            if other:
                s = self._similarity(r["text"], other[0], self._qvec(r["text"]) if self.embed else None)
                if s >= dup_threshold and not _value_clash(r["text"], other[0]["text"]):
                    redundant += 1
        return {"total": len(self.items), "active": len(act), "superseded": len(sup), "by_type": by_type,
                "consolidated": linked, "decayed": decayed, "redundant_estimate": redundant,
                "redundant_frac": round(redundant / max(1, len(sample)), 3), "sampled": len(sample)}

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
                  and (self.tenant is None or r.get("tenant") == self.tenant)]
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
                            # Resolve by VALIDITY time (valid_from = when the fact is TRUE), not ingest order
                            # (ts = when it was stored). A fact learned LATE about an EARLIER state (e.g. a
                            # back-filled record) must NOT overwrite the genuinely-current one just because it
                            # arrived later. valid_from defaults to ts, so ingest-ordered streams are unchanged;
                            # only out-of-order arrivals (the bi-temporal case) flip vs the old ts rule.
                            _vf = lambda r: r.get("valid_from", r["ts"])
                            older, newer = (a, b) if _vf(a) <= _vf(b) else (b, a)
                            # Fast-novelty guard (opt-in): supersede only on a CORROBORATED contradiction
                            # (earned credit, or >=2 links — same bar as graduation). An uncorroborated
                            # single contradiction is recorded as a link but does NOT override a standing
                            # fact (resists single-shot poison flips). Default OFF -> legacy fast behavior.
                            if self.supersede_requires_corroboration:
                                _ng = float(newer.get("good", 0) or 0); _nb = float(newer.get("bad", 0) or 0)
                                if not ((_ng > 0 and _ng >= _nb) or len(newer.get("links") or []) >= 2):
                                    a["links"].append(b["id"]); linked += 1
                                    continue
                            # Persistence (CUSUM) guard: supersede only once the NEW state is asserted by
                            # >= supersede_persistence independent records (the change has persisted). Count
                            # active records that (i) match newer's value/polarity and (ii) contradict older —
                            # an isolated poison flip stays below the threshold and is merely linked.
                            if self.supersede_persistence > 1:
                                nvec = self._qvec(newer["text"])
                                support = sum(
                                    1 for r in active if r["status"] == "active"
                                    and self._similarity(newer["text"], r, nvec) >= dup_threshold
                                    and not _value_clash(newer["text"], r["text"])
                                    and not _negation_clash(newer["text"], r["text"])
                                    and (_value_clash(older["text"], r["text"]) or _negation_clash(older["text"], r["text"])))
                                if support < self.supersede_persistence:
                                    a["links"].append(b["id"]); linked += 1
                                    continue
                            older["status"] = "superseded"
                            older["superseded_ts"] = time.time()
                            older["invalidated_at"] = _vf(newer)   # bi-temporal: when this record stopped being current
                            om = older.setdefault("meta", {})
                            om["superseded_by_toggle"] = newer["id"]
                            om["superseded_by_policy"] = ("toggle_corroborated" if self.supersede_requires_corroboration
                                                          else ("toggle_persistence" if self.supersede_persistence > 1
                                                                else "state_toggle"))
                            # Accuracy loop, live consumer: being OVERTURNED by a later contradiction is
                            # a was-wrong signal — debit the superseded claim, credit the one that
                            # corrected the record. So the consolidation pass continuously feeds each
                            # memory's reliability from real outcomes, not just external scoring.
                            older["bad"] = float(older.get("bad", 0) or 0) + 1.0
                            newer["good"] = float(newer.get("good", 0) or 0) + 1.0
                            toggled += 1
                            if older is a:
                                break                # this anchor is gone; advance to the next
                        else:
                            a["links"].append(b["id"]); linked += 1
        staled = 0
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
        self._save()
        return {"active": len([r for r in self.items if r["status"] == "active"]),
                "hubs_flagged": hubs, "linked_pairs": linked, "toggled": toggled,
                "staled": staled, "kept": keep, "total": len(self.items)}

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
        clusters = self._cluster_active(cluster_sim)
        fired = linked = toggled = staled = 0
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
                            older, newer = (a, b) if a["ts"] <= b["ts"] else (b, a)
                            older["status"] = "superseded"; older["superseded_ts"] = time.time()
                            om = older.setdefault("meta", {})
                            om["superseded_by_toggle"] = newer["id"]
                            om["superseded_by_policy"] = "state_toggle"
                            toggled += 1
                            if older is a:
                                break
                        else:
                            a["links"].append(b["id"]); linked += 1
            if keep_per_cluster is not None:
                act = sorted([r for r in members if r["status"] == "active"], key=lambda r: -r["value"])
                for r in act[keep_per_cluster:]:
                    r["status"] = "superseded"; r["superseded_ts"] = time.time(); staled += 1
                    r.setdefault("meta", {})["superseded_by_policy"] = "keep_budget"
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

        This is mnemo's answer to Letta-style sleep-time compute, but as a pure library primitive (the
        host schedules the idle window; mnemo provides the deferred maintenance op) — no agent loop, no
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

    # ── contradiction surfacing (flag, never auto-delete) ─────────────────────
    def contradictions(self, sim_threshold: float = 0.5, incompatible=None) -> list[dict]:
        """Flag mutually-incompatible memories among RELATED ones (similarity-gated) for human review.
        `incompatible(a_text, b_text)->bool` defaults to a negation/polarity heuristic."""
        inc = incompatible or _negation_clash
        active = [r for r in self.items if r["status"] == "active"
                  and (self.tenant is None or r.get("tenant") == self.tenant)]   # tenant-scoped
        flags = []
        for i, a in enumerate(active):
            avec = self._qvec(a["text"])             # embed each anchor once, not once per partner
            for b in active[i + 1:]:
                if self._similarity(a["text"], b, avec) >= sim_threshold and inc(a["text"], b["text"]):
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

    # ── value, reported at the COHORT level ───────────────────────────────────
    def value_by_cohort(self) -> dict:
        """Per-TAG value rollup. Deliberately not per-memory: at n-of-1, per-item value is noise;
        the cohort (tag / time-block) is where the signal is real."""
        out: dict[str, dict] = {}
        for r in self.items:
            if r["status"] != "active":
                continue
            for tag in (r["tags"] or ["(untagged)"]):
                c = out.setdefault(tag, {"count": 0, "value": 0.0})
                c["count"] += 1; c["value"] += r["value"]
        return {k: {"count": v["count"], "value": round(v["value"], 2),
                    "avg": round(v["value"] / v["count"], 2)} for k, v in out.items()}

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
        n = len(self.items)
        self.items = []
        self._mat = None
        self._tok_cache = {}
        return {"shredded": True, "records_dropped": n, "ts": time.time(),
                "note": "encryption key destroyed; the store at rest (and its backups) is now unrecoverable"}

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
            slim = self.items if self._persist_vectors else \
                [{k: v for k, v in r.items() if k != "vec"} for r in self.items]
            # Atomic write: a partial/interleaved write can't corrupt the store (crash- and
            # concurrent-writer-safe — last writer wins, never a torn JSON file).
            data = json.dumps(slim, ensure_ascii=False, indent=1)
            tmp = self.path.with_name(self.path.name + ".tmp")
            if self._encrypted:                                   # AES-256-GCM at rest (never a plaintext tmp)
                key = self._resolve_key()                         # sets self._enc_salt on first save
                tmp.write_bytes(_encrypt_blob(key, data.encode("utf-8"), self._enc_salt))
            else:
                tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self.path)
            self._last_save = now
            self._dirty = False
        except Exception:
            pass

    def flush(self):
        """Force-persist any pending throttled changes (call on clean shutdown)."""
        if self._dirty:
            self._save(force=True)


# ── per-type decay priors (the half-life a memory's ranking value decays at, by kind) ──────────
# episodic = events (fade fast); semantic = durable facts (fade slow); procedural = rules/prefs
# (barely fade). Access resets the decay clock (see Mnemo._effective_value). Tunable.
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


class _TenantView:
    """A logically-isolated view over a shared Mnemo store (see Mnemo.for_tenant). It carries its OWN `tenant`
    but forwards EVERY other attribute to the parent store, so all data + config are shared by reference (one
    items list, one file, one cache) while the tenant-sensitive operations run bound to THIS view's tenant.

    Implementation: the handful of tenant-aware Mnemo methods are re-bound onto the view (so `self.tenant` inside
    them is the VIEW's tenant), and their tenant-aware internal helpers (_supersede_by_key, _tenant_rows) are
    re-bound too; everything else (`items`, `_save`, `_qvec`, `embed`, config flags, ...) resolves to the parent
    via __getattr__, so reads/writes land on the shared store. Non-tenant methods (consolidate, credit, verify_*,
    anchor, route, ...) are used as-is on the parent through __getattr__ and are unaffected by tenancy."""
    __slots__ = ("_parent", "tenant")

    def __init__(self, parent: "Mnemo", tenant: str):
        object.__setattr__(self, "_parent", parent)
        object.__setattr__(self, "tenant", tenant)

    def __getattr__(self, name):                 # anything not on the view -> the shared parent store
        return getattr(self._parent, name)

    def __setattr__(self, name, value):          # config writes go to the shared parent (tenant is slot-local)
        if name == "tenant":
            object.__setattr__(self, name, value)
        else:
            setattr(self._parent, name, value)

    def for_tenant(self, tenant: str):           # re-scope from the same shared store
        return _TenantView(self._parent, str(tenant))

    # tenant-sensitive surface: rebound so `self` is the VIEW (its tenant), state stays the parent's
    def remember(self, *a, **k):        return Mnemo.remember(self, *a, **k)
    def recall(self, *a, **k):          return Mnemo.recall(self, *a, **k)
    def forget_subject(self, *a, **k):  return Mnemo.forget_subject(self, *a, **k)
    def forget_pii(self, *a, **k):      return Mnemo.forget_pii(self, *a, **k)
    def pii_report(self, *a, **k):      return Mnemo.pii_report(self, *a, **k)
    def remember_dedup(self, *a, **k):  return Mnemo.remember_dedup(self, *a, **k)
    def consolidate(self, *a, **k):     return Mnemo.consolidate(self, *a, **k)
    def consolidate_clusters(self, *a, **k): return Mnemo.consolidate_clusters(self, *a, **k)
    def contradictions(self, *a, **k):  return Mnemo.contradictions(self, *a, **k)
    def check_conflict(self, *a, **k):  return Mnemo.check_conflict(self, *a, **k)
    def _cluster_active(self, *a, **k): return Mnemo._cluster_active(self, *a, **k)
    def _supersede_by_key(self, *a, **k): return Mnemo._supersede_by_key(self, *a, **k)
    def candidates(self, *a, **k):      return Mnemo.candidates(self, *a, **k)
    def promote_candidate(self, *a, **k): return Mnemo.promote_candidate(self, *a, **k)
    def discard_candidate(self, *a, **k): return Mnemo.discard_candidate(self, *a, **k)
    def observe(self, *a, **k):         return Mnemo.observe(self, *a, **k)
    def reopened(self, *a, **k):        return Mnemo.reopened(self, *a, **k)
    def resolve_reopened(self, *a, **k): return Mnemo.resolve_reopened(self, *a, **k)
    def support_challenge_for(self, *a, **k): return Mnemo.support_challenge_for(self, *a, **k)
    def _current_active(self, *a, **k): return Mnemo._current_active(self, *a, **k)
    def _tenant_rows(self, *a, **k):    return Mnemo._tenant_rows(self, *a, **k)


# --------------------------------------------------------------------------------------------------------------
# Ready-made write-path extractors (set `m.extractor = ...`). The extractor derives a (key, object) from free
# text so supersession/echo_guard/revert engage WITHOUT the caller passing an explicit key. mnemo ships two:
#   - regex_extractor : DETERMINISTIC, no LLM, no dependency — keeps the zero-LLM-on-write moat. Conservative
#     by design (returns None unless a clear subject/relation pattern matches), because a mis-derived key
#     mis-supersedes; a returned None just falls back to a plain append.
#   - make_llm_extractor(call_fn) : OPT-IN factory. Wraps YOUR llm(prompt)->str call to extract (key, object).
#     This PUTS AN LLM ON THE WRITE PATH — you trade determinism/zero-cost for auto-capture of unstructured text.
# Both are fail-open (Mnemo.remember swallows extractor exceptions and appends the raw text).

_EX_REL = re.compile(
    r"^\s*(?:correction|update|note|fyi)?\s*[:,-]?\s*"          # optional correction marker, stripped
    r"(?:the\s+)?(?P<subject>[A-Za-z0-9 ._/@'-]{2,60}?)"        # subject
    r"(?:'s|s')\s+(?P<rel>[A-Za-z0-9 ._-]{2,40}?)"              # possessive relation:  "X's Y"
    r"\s+(?:is|was|are|were|=|:|now|became|changed to)\s+"
    r"(?P<obj>.+?)\s*\.?\s*$", re.I)
_EX_OF = re.compile(
    r"^\s*(?:correction|update|note|fyi)?\s*[:,-]?\s*"
    r"the\s+(?P<rel>[A-Za-z0-9 ._-]{2,40}?)\s+of\s+(?P<subject>[A-Za-z0-9 ._/@'-]{2,60}?)"   # "the Y of X"
    r"\s+(?:is|was|are|were|=|:|now)\s+(?P<obj>.+?)\s*\.?\s*$", re.I)
_EX_IS = re.compile(
    r"^\s*(?:correction|update|note|fyi)?\s*[:,-]?\s*"
    r"(?:the\s+)?(?P<subject>[A-Za-z0-9 ._/@'-]{2,60}?)"        # "X is Y"
    r"\s+(?:is|was|are|were|=|:|now|became|changed to)\s+"
    r"(?P<obj>.+?)\s*\.?\s*$", re.I)


def regex_extractor(text):
    """text -> (key, object) | None. Deterministic, no LLM. Recognizes 'X's Y is Z', 'the Y of X is Z', and
    'X is Z' (with optional leading correction/update/now markers). key is a canonical 'subject::relation' (or
    just 'subject' for the plain copula) so a reworded restatement maps to the SAME key. Returns None (=> plain
    append) when nothing matches confidently."""
    if not text:
        return None
    for rx, keyed in ((_EX_REL, True), (_EX_OF, True), (_EX_IS, False)):
        mt = rx.match(text)
        if mt:
            subj = " ".join(mt.group("subject").lower().split())
            obj = mt.group("obj").strip().strip(".").strip()
            obj = re.sub(r"^(?:now|actually|currently|really)\s+", "", obj, flags=re.I).strip()   # copula adverb
            if not subj or not obj or len(obj) > 200:
                return None
            if keyed:
                rel = " ".join(mt.group("rel").lower().split())
                return (f"{subj}::{rel}", obj)
            return (subj, obj)
    return None


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


if __name__ == "__main__":
    m = Mnemo()                                  # no path, no embedder — pure in-memory + lexical
    m.remember("SGD converges slowly due to gradient variance.", tags=["optimization"], value=3)
    m.remember("SGD does not converge slowly.", tags=["optimization"], value=1)
    m.remember("Pre-trend tests catch only 31% of fatal DiD bias.", tags=["causal"], value=2)
    print("recall 'SGD variance':", [r["text"][:46] for r in m.recall("SGD variance", k=3)])
    print("consolidate:", m.consolidate(keep=10))
    print("contradictions:", m.contradictions())       # flags the SGD pair (related + one negates)
    print("value_by_cohort:", m.value_by_cohort())
    print("(For semantic recall, pass embed=your_model to Mnemo(); lexical is the zero-dep fallback.)")
