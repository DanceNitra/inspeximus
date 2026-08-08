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

    def __init__(self, secret_hex: str | None = None, state_path: str | None = None):
        if secret_hex is None:
            secret_hex, public = new_ed25519_keypair()
        else:
            public = _public_from_secret(secret_hex)
        self._secret = secret_hex
        self.public = public
        self._state_path = state_path
        self._last: dict[str, dict] = {}
        if state_path and os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    self._last = json.load(f)
            except (OSError, ValueError):
                self._last = {}

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
        sig = witness_cosign(self._secret, anchor, prior_anchor=prior)   # raises on fork/rollback
        self._last[str(store_id)] = {k: anchor.get(k) for k in
                                     ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip", "sth_hash")}
        self._persist()
        return self.public, sig

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
