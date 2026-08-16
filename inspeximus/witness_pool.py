"""Witness pool — the gossip layer that makes k-of-n anchor co-signing usable.

anchor()/verify_consistency() catch a rewrite on ONE timeline; a compromised store operator can still show
DIFFERENT histories to different clients (a split-view / fork). The 1.34.0 primitives (witness_cosign,
verify_cosigned_anchor, detect_split_view) close that IF independent witnesses co-sign the signed head.
This module turns those primitives into a runnable pool:

  - `Witness` — an INDEPENDENT party that co-signs a store's `anchor()` head AND remembers, per store, the last
    head it signed, so it REFUSES to co-sign a fork or rollback. That memory is what makes the guarantee real
    across time, so it is PERSISTED (json). A witness that has never been forked will simply never co-sign two
    inconsistent heads — which is exactly what a client's k-of-n check relies on.
  - `collect_cosignatures` — a client gathers co-signatures from a set of witnesses for one anchor; a witness
    that REFUSES (raises) is surfaced as a fork alarm rather than silently dropped.

No LLM, no GPU, no network dependency in the core logic (a witness can be local, in-process, or wrapped behind
HTTP by the caller). Zero new dependencies beyond `cryptography` (already the signed-store dependency).
"""
from __future__ import annotations
import json, os, tempfile, time
from .core import new_ed25519_keypair, witness_cosign, _HAVE_ED, _canon, _sha256_hex
if _HAVE_ED:                     # OPTIONAL, exactly as in core: the Ed25519 names exist only when
    from .core import (          # `cryptography` is installed, and importing them unconditionally
        _Ed25519SK, _Ed25519PK)  # broke `import inspeximus.witness_pool` on a base install -- which
else:                            # the examples suite caught, because a zero-dependency package that
    _Ed25519SK = _Ed25519PK = None   # cannot be imported without an extra is not zero-dependency.


def _public_from_secret(secret_hex: str) -> str:
    if not _HAVE_ED:
        raise RuntimeError("witness keys need the `cryptography` package (pip install cryptography)")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization as _ser
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret_hex))
    return sk.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw).hex()


#: Newest-last bound on the refusal log. A witness watching a flapping operator must not grow its
#: state file without limit, and the oldest refusal is the least useful one to keep.
_MAX_REFUSALS = 200


class Witness:
    """An independent co-signing witness. Holds one Ed25519 key and, per store_id, the last anchor it signed;
    it refuses to co-sign a fork/rollback of that head (witness_cosign's prior-anchor guard). `state_path`
    persists the per-store last-head memory as JSON so the refusal survives a restart — without it, an operator
    could restart the witness and get a fork past it."""

    def __init__(self, secret_hex: str | None = None, state_path: str | None = None,
                 strict: bool = False):
        if secret_hex is None:
            secret_hex, public = new_ed25519_keypair()
        else:
            public = _public_from_secret(secret_hex)
        self._secret = secret_hex
        self.public = public
        self._state_path = state_path
        self._strict = bool(strict)
        self._bootstrapped: set = set()
        self._last: dict[str, dict] = {}
        self._refusals: list = []
        # AN AMNESIAC WITNESS MUST NOT CO-SIGN. This used to swallow (OSError, ValueError) and carry
        # on with `self._last = {}` -- so writing garbage into one JSON file made the witness forget
        # every head it had ever signed, and it then co-signed a rollback that
        # verify_cosigned_anchor accepted as a valid quorum. Measured 2026-08-15: with the state file
        # intact the rollback was REFUSED ("n_writes rolled back 3 -> 1"); with it corrupted, and
        # again with it deleted, the same rollback was co-signed, ok=True, count=1. The module
        # docstring says this memory is what makes the guarantee real -- "without it, an operator
        # could restart the witness and get a fork past it" -- and the recovery path handed the
        # operator exactly that, by accident, on a file anyone who can reach the witness can touch.
        #
        # A file that EXISTS but does not parse is a hard error: it means state was there and is
        # unreadable, and the only safe reading of "I cannot tell what I signed" is to stop.
        if state_path and os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    _st = json.load(f)
                # The file used to be the bare `_last` mapping. A 2.10.5 file is
                # {"heads": ..., "refusals": ...}; an older one is read as heads-only rather than
                # rejected, because refusing to start on a valid older state file would take a
                # witness offline on upgrade -- and a witness that will not start co-signs nothing.
                if isinstance(_st, dict) and ("heads" in _st or "refusals" in _st):
                    self._last = _st.get("heads") or {}
                    self._refusals = list(_st.get("refusals") or [])
                else:
                    self._last = _st
            except (OSError, ValueError) as e:
                raise ValueError(
                    f"the witness fork-memory at {state_path} exists but could not be read ({e}). "
                    f"Refusing to start: a witness that has forgotten the heads it signed will "
                    f"co-sign a rollback, which is the one thing it exists to refuse. Restore the "
                    f"file from backup, or delete it deliberately and re-bootstrap this witness."
                ) from None
        if state_path and not os.path.exists(state_path):
            # DELETION is not distinguishable from a first run by inspection. The file is written
            # so an operator has a file to back up and monitor from the first run rather than the
            # first co-signature. This does NOT by itself make a later deletion detectable -- an empty
            # recreated file and a genuine first run are the same bytes, and saying otherwise would be
            # the overclaim this review keeps finding. `strict=True` is what closes deletion.
            self._persist()

    def _persist(self) -> None:
        if not self._state_path:
            return
        d = os.path.dirname(os.path.abspath(self._state_path)) or "."
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"heads": self._last, "refusals": self._refusals}, f)
            os.replace(tmp, self._state_path)          # atomic; the fork-memory must not half-write
        except OSError:
            try: os.unlink(tmp)
            except OSError: pass
            raise

    def cosign(self, store_id: str, anchor: dict) -> tuple[str, str]:
        """Co-sign `anchor` for `store_id`. Raises ValueError if it forks/rolls back the last head this witness
        signed for that store (the refusal is the split-view defense). On success, records the new head and
        returns (public_hex, signature_hex) — pass to verify_cosigned_anchor / detect_split_view."""
        prior = self._last.get(str(store_id))
        # STRICT: refuse a store this witness has no memory of. Amnesia IS the attack -- delete the
        # state file and the witness co-signs a rollback it would otherwise refuse (measured: the same
        # rollback went from "refusing to co-sign: n_writes rolled back 3 -> 1" to ok=True, count=1).
        # Refusing a CORRUPT file closes only half of it, because a deleted file and a first run are
        # indistinguishable by inspection. Under strict, first contact with a store is an explicit act
        # -- bootstrap(store_id) -- so silence becomes evidence.
        #
        # Default OFF: a witness pool that refuses every new store on upgrade is a worse outcome than
        # the attack it prevents, and the operators who need this are the ones who will read it.
        if self._strict and prior is None and str(store_id) not in self._bootstrapped:
            raise ValueError(
                f"strict witness has no record of store {store_id!r}. Either this is genuinely its "
                f"first anchor -- call bootstrap({store_id!r}) to say so deliberately -- or the "
                f"fork-memory was deleted, in which case co-signing now would launder a rollback.")
        try:
            sig = witness_cosign(self._secret, anchor, prior_anchor=prior)   # raises on fork/rollback
        except ValueError as e:
            # A REFUSAL IS EVIDENCE AND IT MUST NOT LIVE WITH THE PARTY BEING AUDITED.
            #
            # Until 2.10.5 this simply propagated: `collect_cosignatures` caught it and
            # `build_bundle` recorded it in the bundle -- an artifact the OPERATOR builds. Measured
            # 2026-08-16: the operator deletes `witness_refusals`, reseals the (advisory) bundle
            # hash, and an auditor who does not already hold the witness allowlist sees an ordinary
            # SELF-CERTIFIED bundle with no hint that three witnesses said no. The record helped an
            # honest operator prove diligence and did not bind a dishonest one, which is the wrong
            # way round for the only operator-adversarial check in the product.
            #
            # An honest witness refuses only a fork or a rollback, so this log is the highest-value
            # thing it holds. Bounded, newest-last: a witness under a flapping operator must not grow
            # a state file without limit, and the oldest refusal is the least useful one to keep.
            self._refusals.append({
                "store_id": str(store_id), "ts": time.time(), "reason": str(e),
                "offered": {k: anchor.get(k) for k in ("n_writes", "writes_tip")},
                "last_seen": dict(prior) if prior else None,
            })
            del self._refusals[:-_MAX_REFUSALS]
            self._persist()
            raise
        self._last[str(store_id)] = {k: anchor.get(k) for k in
                                     ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip", "sth_hash")}
        # WHEN, so SILENCE becomes visible. Without a timestamp an operator who rewrote history and
        # then simply stopped submitting heads is indistinguishable from an idle store -- an attack
        # whose entire cost is doing nothing.
        self._last[str(store_id)]["seen_ts"] = time.time()
        self._persist()
        return self.public, sig

    def bootstrap(self, store_id: str) -> None:
        """Declare that this witness is legitimately seeing `store_id` for the first time.

        The deliberate act `strict=True` demands, so "I have never seen this store" and "someone
        deleted what I knew about it" stop looking alike.
        """
        self._bootstrapped.add(str(store_id))

    def attest(self, store_id: str) -> dict:
        """A SIGNED statement of what this witness has seen for `store_id`, for a third party.

        The point of a witness is that it is not the operator, and until 2.10.5 nothing carried that
        across: every path to the witness's knowledge ran through an artifact the operator built and
        could edit. This is the surface an auditor asks directly. It answers three questions the
        operator cannot answer honestly about themselves:

          * WHAT was the last head I co-signed, and at what height. A bundle whose anchor does not
            match it is either stale or a fork.
          * WHEN did I last see this store. An operator who rewrote history and then stopped
            submitting is otherwise indistinguishable from an idle one.
          * WHAT DID I REFUSE. An honest witness refuses only a fork or a rollback.

        Signed over the canonical statement, so it survives the trip and cannot be edited by whoever
        carries it. Verify with `verify_attestation(statement, witness_pubkey)`.

        HONEST SCOPE, and it is a deployment fact rather than something this can assert: if the
        operator never submitted this store to any witness, there is nothing to ask, and an empty
        attestation from a witness that never saw the store looks the same as one from a store that
        never existed. The auditor has to know WHICH witnesses should have seen it. `seen` is False
        in that case rather than absent, so the question at least has an answer.
        """
        sid = str(store_id)
        head = self._last.get(sid)
        stmt = {
            "kind": "inspeximus-witness-attestation/1",
            "store_id": sid,
            "witness": self.public,
            "issued_ts": time.time(),
            "seen": head is not None,
            "last_head": dict(head) if head else None,
            "refusals": [r for r in self._refusals if r.get("store_id") == sid],
        }
        core = _canon({k: stmt[k] for k in
                       ("kind", "store_id", "witness", "issued_ts", "seen", "last_head", "refusals")})
        stmt["hash"] = _sha256_hex(core)
        if _HAVE_ED:
            sk = _Ed25519SK.from_private_bytes(bytes.fromhex(self._secret))
            stmt["sig"] = sk.sign(bytes.fromhex(stmt["hash"])).hex()
        return stmt

    def refusals(self, store_id: str | None = None) -> list:
        """What this witness declined to co-sign, and why. Unsigned; use `attest()` to hand it on."""
        return [r for r in self._refusals
                if store_id is None or r.get("store_id") == str(store_id)]

    def last_head(self, store_id: str) -> dict | None:
        return self._last.get(str(store_id))


def verify_attestation(statement: dict, witness_pubkey: str | None = None) -> dict:
    """Check a witness attestation is intact and, when pinned, actually from that witness.

    Returns {ok, signed, problems, statement}. `witness_pubkey` PINS it: unpinned, the signature is
    checked against the key carried inside the statement, which whoever handed it to you could have
    replaced along with the signature -- the same footgun `verify_writes` grew `warn_unpinned` for,
    so it is reported rather than assumed away.
    """
    problems: list = []
    if not isinstance(statement, dict) or statement.get("kind") != "inspeximus-witness-attestation/1":
        return {"ok": False, "signed": False, "problems": ["not a witness attestation"],
                "statement": statement}
    core = _canon({k: statement.get(k) for k in
                   ("kind", "store_id", "witness", "issued_ts", "seen", "last_head", "refusals")})
    if _sha256_hex(core) != statement.get("hash"):
        problems.append("attestation hash does not match its own fields (edited in transit)")
    signed = False
    pk = witness_pubkey or statement.get("witness")
    if statement.get("sig") and pk and _HAVE_ED:
        try:
            _Ed25519PK.from_public_bytes(bytes.fromhex(pk)).verify(
                bytes.fromhex(statement["sig"]), bytes.fromhex(statement["hash"]))
            signed = True
        except Exception:
            problems.append("attestation signature does not verify")
    elif not statement.get("sig"):
        problems.append("attestation carries no signature: it is a claim, not evidence")
    if witness_pubkey and statement.get("witness") != witness_pubkey:
        problems.append("attestation is from a different witness than the one pinned")
    # THE WHOLE REASON TO ASK. A refusal recorded here cannot be deleted by the operator, because
    # this statement did not come from them.
    if statement.get("refusals"):
        problems.append(
            f"this witness REFUSED to co-sign {len(statement['refusals'])} time(s) for this store: "
            f"{[r.get('reason', '')[:70] for r in statement['refusals'][:3]]}. An honest witness "
            f"refuses only a fork or a rollback.")
    if not statement.get("seen"):
        problems.append("this witness has never seen this store, so it vouches for nothing about it "
                        "-- check you are asking the witnesses that should have")
    return {"ok": not problems, "signed": signed, "problems": problems, "statement": statement}


def collect_cosignatures(store_id: str, anchor: dict, witnesses) -> dict:
    """Client-side: gather co-signatures for `anchor` from `witnesses` (Witness instances, or callables
    `(store_id, anchor) -> (pubkey, sig)` for remote/HTTP witnesses). A witness that REFUSES (raises) is NOT
    silently dropped — it is surfaced in `refused` as a fork alarm (an honest witness only refuses a fork or a
    rollback). Returns {cosignatures, refused, witnesses}: `cosignatures` = [(pubkey, sig), ...] to feed
    Inspeximus.verify_cosigned_anchor(anchor, cosignatures, witnesses=..., threshold=k); `refused` = list of
    {index, reason} for the witnesses that would not sign; `witnesses` = the public keys that signed."""
    cosigs, refused, signers = [], [], []
    for i, w in enumerate(witnesses):
        try:
            pk, sig = w.cosign(store_id, anchor) if isinstance(w, Witness) else w(store_id, anchor)
            cosigs.append((pk, sig)); signers.append(pk)
        except Exception as e:                                          # a refusal is the split-view signal
            refused.append({"index": i, "reason": str(e)})
    return {"cosignatures": cosigs, "refused": refused, "witnesses": signers}


def http_witness(url: str, timeout: float = 10.0):
    """Return a callable `(store_id, anchor) -> (pubkey, sig)` that co-signs via a REMOTE witness HTTP server
    (see inspeximus.witness_server). Pass it in the `witnesses` list of collect_cosignatures alongside local
    Witness objects. A remote REFUSAL (HTTP 409, a fork/rollback) raises ValueError, so collect_cosignatures
    records it as a fork alarm rather than a silent drop. Stdlib urllib only -- no new dependency."""
    import urllib.request, urllib.error, json as _json
    base = url.rstrip("/")
    def _cosign(store_id, anchor):
        req = urllib.request.Request(base + "/cosign",
              data=_json.dumps({"store_id": store_id, "anchor": anchor}).encode(),
              headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = _json.loads(r.read())
            return d["pubkey"], d["sig"]
        except urllib.error.HTTPError as e:                             # 409 = refused (fork); surface the reason
            try: reason = _json.loads(e.read()).get("refused") or f"HTTP {e.code}"
            except Exception: reason = f"HTTP {e.code}"
            raise ValueError(f"remote witness refused: {reason}")
    return _cosign
