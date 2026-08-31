"""A SCITT Transparency Service (RFC 9943 section 5) — zero dependencies.

WHAT THIS FINALLY EARNS. `cose.py` emits Receipts and `scitt.py` emits Signed Statements, and both
docstrings said the same thing: this is not a Transparency Service. Section 5.1.1 lists what a
conformant one MUST do, and the missing items were a published Registration Policy, applied at
registration time, over statements authenticated against trust anchors, with the Receipt released
only after registration. That is what this file adds.

THE REQUIREMENTS, read from RFC 9943 section 5.1 and 5.1.1, and where each is met:

  1  produce COSE Receipts                      `register()` returns one, built by cose.py
  2  maintain a Registration Policy and make it  the policy is registered as ENTRY 0 of the log,
     transparent on the verifiable data structure   so it is inside the structure it governs
  3  verify signatures per COSE                  `register()` refuses an unverifiable statement
  4  maintain trust anchors, authenticate        `trust_anchors`, and an empty set is a DECISION the
     Signed Statements                             policy has to state out loud, never a default
  5  apply the CURRENT policy at registration    the policy in force is read at call time and its
                                                    digest is recorded on the entry
  6  register BEFORE releasing a Receipt         the leaf is appended and persisted first; a failure
                                                    anywhere before that returns no receipt at all
  7  support bootstrapping                       entry 0 is the policy, self-describing from genesis

WHY ENTRY 0 IS THE POLICY, and it is the only design choice here worth arguing about. A policy served
beside the log can be changed after the fact, which makes every receipt ambiguous: a verifier cannot
tell which rules were in force when a statement was admitted. Putting it in the log means the rules
are as tamper-evident as the entries they governed, and a policy change is itself an entry with a
position and a proof.

WHAT THIS IS STILL NOT. There is no SCRAPI HTTP surface (draft-ietf-scitt-scrapi), no distributed
consensus, and one operator holds the log. Non-equivocation rests on external witnesses
(`witness_pool`), exactly as RFC 6962 intends: the log is untrusted, and someone else's copy of the
root is what makes an append-only violation visible. A single-operator service that verifies its own
append-only claim is verifying nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

from . import cose, scitt
from .merkle import inclusion_proof, root

__all__ = ["RegistrationPolicy", "TransparencyService", "RegistrationRefused",
           "verify_registered_statement"]

POLICY_SUBJECT = "urn:ietf:params:scitt:registration-policy"


class RegistrationRefused(Exception):
    """The statement was NOT registered, so no Receipt exists for it.

    A distinct exception because the alternative is a caller who treats a refusal as a transient
    error and retries, or worse, one who receives `None` and reads it as success.
    """


class RegistrationPolicy:
    """The rules a Transparency Service applies, in a form that can be published and hashed.

    `accepted_issuers` is the trust anchor set. An EMPTY set means the service admits any issuer, and
    that is written into the policy in those words rather than left as an absence: "no anchors
    configured" and "anchors deliberately open" look identical in a config file and mean opposite
    things to an auditor.
    """

    def __init__(self, name: str, accepted_issuers=None, max_payload_bytes: int = 4096,
                 require_subject_prefix: str | None = None, notes: str = ""):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("a Registration Policy needs a name a reader can cite")
        self.name = name
        self.accepted_issuers = sorted(set(accepted_issuers or []))
        self.max_payload_bytes = int(max_payload_bytes)
        self.require_subject_prefix = require_subject_prefix
        self.notes = notes

    def as_dict(self) -> dict:
        return {
            "scitt_registration_policy": "1.0",
            "name": self.name,
            "accepted_issuers": list(self.accepted_issuers),
            "issuer_rule": ("only the issuers listed" if self.accepted_issuers else
                            "ANY issuer is admitted: this service is deliberately open, and a "
                            "Receipt from it says a statement was recorded, never that its issuer "
                            "was vetted"),
            "max_payload_bytes": self.max_payload_bytes,
            "require_subject_prefix": self.require_subject_prefix,
            "notes": self.notes,
        }

    def canonical(self) -> bytes:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")

    def digest(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()

    def check(self, verified: dict, payload_len: int) -> list:
        """Return the reasons this statement is refused; an empty list admits it."""
        why = []
        if not verified.get("signature_ok"):
            why.append("the statement's signature does not verify")
        issuer = verified.get("issuer")
        if not issuer:
            why.append("no Issuer claim")
        elif self.accepted_issuers and issuer not in self.accepted_issuers:
            why.append("issuer %r is not a trust anchor of this service" % issuer)
        subject = verified.get("subject")
        if not subject:
            why.append("no Subject claim")
        elif self.require_subject_prefix and not str(subject).startswith(self.require_subject_prefix):
            why.append("subject %r does not start with %r" % (subject, self.require_subject_prefix))
        if payload_len > self.max_payload_bytes:
            why.append("payload is %d bytes, over the %d this policy admits"
                       % (payload_len, self.max_payload_bytes))
        return why


class TransparencyService:
    """An append-only log of registered Signed Statements, with its policy as entry 0.

    `sign` signs on behalf of the SERVICE and is separate from any Issuer key by construction: the
    whole point of a Receipt is that a second party attests to what the first party said, and a
    service that signs with the issuer's key attests to nothing.
    """

    def __init__(self, path: str, policy: RegistrationPolicy, sign, verify_issuer,
                 service_pubkey: str | None = None):
        if not callable(sign) or not callable(verify_issuer):
            raise TypeError("a Transparency Service needs a signer and an issuer verifier")
        self.path = str(path)
        # The witness keys its fork memory on this, so it has to be STABLE across restarts and
        # DISTINCT per log. The absolute path is both, and it never leaves this process: what the
        # witness stores is the id the caller hands it.
        self.store_id = "scitt:" + hashlib.sha256(os.path.abspath(str(path)).encode("utf-8")).hexdigest()[:16]
        self.policy = policy
        self._sign = sign
        self._verify_issuer = verify_issuer
        self.service_pubkey = service_pubkey
        self._entries: list[dict] = []
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                self._entries = [json.loads(ln) for ln in fh if ln.strip()]
        if not self._entries:
            self._append({"kind": "registration-policy", "ts": time.time(),
                          "policy": self.policy.as_dict(),
                          "policy_sha256": self.policy.digest()})

    # ---------------------------------------------------------------- the log
    def _append(self, entry: dict) -> int:
        entry = dict(entry, seq=len(self._entries))
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._entries.append(json.loads(line))
        return entry["seq"]

    def _leaves(self) -> list:
        return [json.dumps(e, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
                for e in self._entries]

    def root(self) -> bytes:
        return root(self._leaves())

    def size(self) -> int:
        return len(self._entries)

    def policy_in_force(self) -> dict:
        """The LAST policy entry, which is the one a registration is judged against.

        Reading the first would answer with the founding rules forever, which is the kind of check
        that keeps passing after the thing it checks has changed.
        """
        for e in reversed(self._entries):
            if e.get("kind") == "registration-policy":
                return e
        raise RuntimeError("this log has no policy entry, so it is not a Transparency Service log")

    def set_policy(self, policy: RegistrationPolicy) -> int:
        """Change the rules, as a new ENTRY. The old policy stays in the log with its position, so a
        receipt issued under it can still be read against the rules that actually applied."""
        self.policy = policy
        return self._append({"kind": "registration-policy", "ts": time.time(),
                             "policy": policy.as_dict(), "policy_sha256": policy.digest()})

    # ---------------------------------------------------------------- registration
    def register(self, statement: bytes) -> bytes:
        """Verify, apply the policy, append, and only THEN issue a Receipt.

        The order is the requirement, not a preference. A service that signs a receipt and appends
        afterwards can hand out proof of an inclusion that never happened if the write fails, and the
        holder of that receipt has no way to tell.

        Raises RegistrationRefused with every reason at once, rather than the first, so a caller
        fixing a statement does not discover the problems one round trip at a time.
        """
        checked = scitt.verify_signed_statement(statement, self._verify_issuer)
        policy_entry = self.policy_in_force()
        current = RegistrationPolicy(
            name=policy_entry["policy"]["name"],
            accepted_issuers=policy_entry["policy"]["accepted_issuers"],
            max_payload_bytes=policy_entry["policy"]["max_payload_bytes"],
            require_subject_prefix=policy_entry["policy"].get("require_subject_prefix"),
        )
        why = current.check(checked, len(checked.get("payload") or b""))
        if why:
            raise RegistrationRefused("; ".join(why))

        index = self._append({
            "kind": "signed-statement", "ts": time.time(),
            "statement_sha256": scitt.statement_digest(scitt.without_receipts(statement)),
            "issuer": checked["issuer"], "subject": checked["subject"],
            "payload_sha256": hashlib.sha256(checked["payload"]).hexdigest(),
            "policy_sha256": policy_entry["policy_sha256"],
        })
        leaves = self._leaves()
        return cose.inclusion_receipt(len(leaves), index, inclusion_proof(leaves, index),
                                      root(leaves), self._sign)

    def receipt_for(self, index: int) -> bytes | None:
        """A Receipt for an entry, proved against the CURRENT root.

        Generated on demand rather than stored, because an inclusion proof is a function of the tree
        and the tree only grows. Handing back the receipt issued at registration would tie a reader
        to a root that is now historical, and they would need a consistency proof before they could
        use it. This way the proof and the root a reader can fetch today agree.

        Returns None for an index that is not there, so a caller can tell "not yet" from "never",
        which is exactly the 204-versus-404 distinction SCRAPI draws.
        """
        if not 0 <= index < len(self._entries):
            return None
        leaves = self._leaves()
        return cose.inclusion_receipt(len(leaves), index, inclusion_proof(leaves, index),
                                      root(leaves), self._sign)

    def register_transparent(self, statement: bytes) -> bytes:
        """Register and return the TRANSPARENT statement: the caller's statement carrying its Receipt."""
        return scitt.transparent_statement(statement, [self.register(statement)])

    # ---------------------------------------------------------------- the external half
    def head(self) -> dict:
        """The log's head, in the shape a witness can co-sign.

        Reusing the anchor shape rather than inventing one means the witnesses, the split-view
        detector and the co-signature verifier already shipped here all work on it unchanged. The
        mapping is the honest one: entries are the writes, the Merkle root is the tip, and a
        transparency log has no tombstones because nothing is ever removed from it.

        `sth_hash` is derived by the same function the verifier re-derives it with, so an operator
        cannot paste a different root into a co-signed head and keep the signatures. That inversion
        was measured once and is the reason anchor_binds_its_fields exists.
        """
        from .core import sth_hash_of
        head = {"n_writes": self.size(), "writes_tip": self.root().hex(),
                "n_tombstones": 0, "tombstones_tip": "",
                "store_id": self.store_id, "kind": "scitt-transparency-log"}
        head["sth_hash"] = sth_hash_of(head)
        return head

    def witnessed_head(self, witnesses, threshold: int = 1) -> dict:
        """Offer the head to independent witnesses and report what they said.

        THIS IS THE PROPERTY THE SERVICE CANNOT GIVE ITSELF. Everything else here is checkable from
        bytes one operator produced: inclusion, consistency, policy, signatures. Non-equivocation is
        not, because showing two histories to two readers is invisible from inside either one. Only
        somebody else's memory of the head catches it.

        A REFUSAL IS THE ALARM, not an error to retry. An honest witness refuses exactly one thing: a
        head that forks or rolls back what it already signed. So `refused` being non-empty is the
        single most informative field here, and it is returned rather than raised, because a caller
        that only sees an exception learns that something went wrong and not that the log forked.

        Returns {head, cosignatures, witnesses, refused, threshold, met}.
        """
        from .witness_pool import collect_cosignatures
        head = self.head()
        got = collect_cosignatures(self.store_id, head, witnesses)
        met = len(got.get("cosignatures") or []) >= int(threshold)
        return {"head": head, "cosignatures": got.get("cosignatures") or [],
                "witnesses": got.get("witnesses") or [], "refused": got.get("refused") or [],
                "threshold": int(threshold), "met": bool(met)}

    # ---------------------------------------------------------------- what a reader is owed
    def entry_leaf(self, index: int) -> bytes:
        return self._leaves()[index]

    def describe(self) -> dict:
        """Everything a verifier needs to check anything this service issued, and nothing secret."""
        p = self.policy_in_force()
        return {"scitt_transparency_service": "1.0", "entries": self.size(),
                "root": self.root().hex(), "service_pubkey": self.service_pubkey,
                "store_id": self.store_id,
                "policy": p["policy"], "policy_sha256": p["policy_sha256"],
                "policy_at_seq": p["seq"],
                "scope": ("One operator holds this log. Inclusion and consistency are provable from "
                          "these bytes; NON-EQUIVOCATION is not, because a single operator checking "
                          "its own append-only claim proves nothing. Have witnesses co-sign the root "
                          "(see witness_pool) if that property is needed.")}


def verify_registered_statement(statement: bytes, verify_issuer, verify_service,
                                entry_leaf: bytes, expected_root: bytes,
                                expected_issuer: str | None = None) -> dict:
    """Check a statement registered by a Transparency Service, and that the Receipt is about IT.

    THERE ARE TWO BINDINGS IN THIS PACKAGE AND THEY ARE NOT INTERCHANGEABLE. Getting them confused is
    how a pair passes that proves nothing, so both are named:

      store-issued   `Inspeximus.transparent_statement` signs a digest OF THE LEAF, and the leaf is
                     the record. `scitt.verify_transparent_statement` checks payload == sha256(leaf).
      service-issued a Transparency Service registers somebody else's statement. The leaf is a LOG
                     ENTRY about that statement, not the fact it asserts, so the payload has nothing
                     to do with the leaf. The link is the entry's `statement_sha256`, checked here.

    A verifier who applies the wrong one gets a refusal on a perfectly good artifact, which is the
    safe direction, and it is still a bug worth not having.

    `entry_leaf` and `expected_root` come from the log, and `expected_root` should be one witnesses
    co-signed. A root the service hands you along with the receipt proves only that the service is
    self-consistent.
    """
    out = {"ok": False, "statement": None, "receipt": None, "bound": None, "entry": None,
           "problems": []}
    st = scitt.verify_signed_statement(statement, verify_issuer, expected_issuer=expected_issuer)
    out["statement"] = st
    out["problems"] += ["statement: " + p for p in st["problems"]]

    receipts = scitt.receipts_of(statement)
    if not receipts:
        out["problems"].append("no Receipt: this statement was never registered, or the Receipt was "
                               "stripped")
        return out
    rc = cose.verify_receipt(receipts[0], verify_service, leaf_data=bytes(entry_leaf),
                             expected_root=bytes(expected_root))
    out["receipt"] = rc
    out["problems"] += ["receipt: " + p for p in rc["problems"]]

    try:
        entry = json.loads(bytes(entry_leaf).decode("utf-8"))
    except Exception as e:
        out["problems"].append("the entry is not readable JSON (%s)" % type(e).__name__)
        return out
    out["entry"] = entry
    out["bound"] = (entry.get("statement_sha256")
                == scitt.statement_digest(scitt.without_receipts(statement)))
    if not out["bound"]:
        # The mirror of the check in scitt.py: a store-issued pair reaching this verifier is a caller
        # mistake, not a forged artifact, and saying "a different registration" about it would be an
        # accusation the evidence does not support.
        if entry.get("statement_sha256") is None:
            out["problems"].append(
                "this leaf is not a Transparency Service registration entry, so the service-issued "
                "binding does not apply. Use inspeximus.verify_transparent_statement for a pair "
                "issued by a store.")
        else:
            out["problems"].append(
                "the log entry this Receipt proves does not name this statement, so the Receipt is "
                "about a different registration")

    out["ok"] = bool(st["ok"] and rc["ok"] and out["bound"])
    return out
