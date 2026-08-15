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
import json, os, tempfile
from .core import new_ed25519_keypair, witness_cosign, _HAVE_ED


def _public_from_secret(secret_hex: str) -> str:
    if not _HAVE_ED:
        raise RuntimeError("witness keys need the `cryptography` package (pip install cryptography)")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization as _ser
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret_hex))
    return sk.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw).hex()


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
                    self._last = json.load(f)
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
                json.dump(self._last, f)
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
        sig = witness_cosign(self._secret, anchor, prior_anchor=prior)   # raises on fork/rollback
        self._last[str(store_id)] = {k: anchor.get(k) for k in
                                     ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip", "sth_hash")}
        self._persist()
        return self.public, sig

    def bootstrap(self, store_id: str) -> None:
        """Declare that this witness is legitimately seeing `store_id` for the first time.

        The deliberate act `strict=True` demands, so "I have never seen this store" and "someone
        deleted what I knew about it" stop looking alike.
        """
        self._bootstrapped.add(str(store_id))

    def last_head(self, store_id: str) -> dict | None:
        return self._last.get(str(store_id))


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
