"""A Transparency Service is the first thing here that can honestly be called one (RFC 9943 s5.1.1).

WHAT WAS MISSING BEFORE. Receipts and Signed Statements both shipped with a docstring saying "this is
not a Transparency Service", because section 5.1.1 asks for four more things: a Registration Policy
that is transparent on the log, applied at registration time, over statements authenticated against
trust anchors, with the Receipt released only AFTER registration.

WHAT THESE TESTS PIN, and each is a property that fails silently if it is wrong:

  * The policy is ENTRY 0 of the log it governs. A policy served beside the log can be edited
    afterwards, which makes every receipt ambiguous about the rules it was admitted under.
  * A refusal appends NOTHING. A service that logs its refusals and then reports them as registrations
    is the failure this exception type exists to prevent.
  * `register()` returns a Receipt only after the entry is on disk. A receipt for an inclusion that
    never happened cannot be told apart from a real one by its holder.
  * The service key is NOT the issuer key. A receipt signed with the issuer's own key attests nothing:
    the point is a second party.
  * The registered identity survives receipt attachment. The log records the digest of the ISSUER'S
    statement; receipts are added to the unprotected header afterwards and change the bytes. If the
    recorded identity moved when somebody countersigned, it was never an identity.
  * The two bindings in this package are not interchangeable, and the wrong one refuses a good pair.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import new_receipt_keypair, scitt, signed_statement
from inspeximus.transparency import (RegistrationPolicy, RegistrationRefused, TransparencyService,
                                     verify_registered_statement)

ISSUER = "did:web:agora.example"


def _keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (Ed25519PrivateKey as SK,
                                                                   Ed25519PublicKey as PK)
    sk_hex, pk_hex = new_receipt_keypair()
    sk, pk = SK.from_private_bytes(bytes.fromhex(sk_hex)), PK.from_public_bytes(bytes.fromhex(pk_hex))

    def verify(msg, sig):
        try:
            pk.verify(sig, msg)
            return True
        except Exception:
            return False
    return sk.sign, verify, pk_hex


@pytest.fixture()
def service():
    isign, iverify, _ipub = _keypair()
    ssign, sverify, spub = _keypair()
    path = os.path.join(tempfile.mkdtemp(), "log.jsonl")
    ts = TransparencyService(path, RegistrationPolicy("agora-v1", accepted_issuers=[ISSUER]),
                             ssign, iverify, service_pubkey=spub)
    return ts, isign, iverify, sverify


def _statement(isign, text=b"a fact", subject="memory:abc", issuer=ISSUER):
    return signed_statement(hashlib.sha256(text).digest(), issuer, subject, isign)


def test_the_policy_is_entry_zero_of_the_log_it_governs(service):
    ts, _isign, _iv, _sv = service
    assert ts.size() == 1
    first = json.loads(ts.entry_leaf(0).decode("utf-8"))
    assert first["kind"] == "registration-policy"
    assert first["seq"] == 0
    assert first["policy"]["name"] == "agora-v1"
    assert first["policy_sha256"] == ts.policy.digest()


def test_a_registration_appends_then_receipts_and_verifies(service):
    ts, isign, iverify, sverify = service
    tr = ts.register_transparent(_statement(isign))
    root_then, leaf = ts.root(), ts.entry_leaf(1)
    out = verify_registered_statement(tr, iverify, sverify, leaf, root_then, expected_issuer=ISSUER)
    assert out["ok"], out["problems"]
    assert out["bound"] is True
    assert out["entry"]["subject"] == "memory:abc"
    # the entry records WHICH policy admitted it, so a later policy change cannot rewrite history
    assert out["entry"]["policy_sha256"] == ts.policy.digest()


def test_a_refused_statement_appends_nothing_and_yields_no_receipt(service):
    ts, isign, _iv, _sv = service
    before = ts.size()
    with pytest.raises(RegistrationRefused) as e:
        ts.register(_statement(isign, issuer="did:web:stranger"))
    assert "trust anchor" in str(e.value)
    assert ts.size() == before, "a refusal must not grow the log"


def test_every_reason_is_reported_at_once(service):
    """One round trip per defect is how a caller gives up. The policy returns all of them."""
    ts, isign, _iv, _sv = service
    ts.set_policy(RegistrationPolicy("strict", accepted_issuers=[ISSUER],
                                     max_payload_bytes=8, require_subject_prefix="memory:"))
    with pytest.raises(RegistrationRefused) as e:
        ts.register(_statement(isign, subject="other:abc", issuer="did:web:stranger"))
    msg = str(e.value)
    assert "trust anchor" in msg and "does not start with" in msg and "over the 8" in msg


def test_the_current_policy_applies_not_the_founding_one(service):
    """Reading the FIRST policy entry would answer with the founding rules forever, which is a check
    that keeps passing after the thing it checks has changed."""
    ts, isign, _iv, _sv = service
    assert ts.policy_in_force()["seq"] == 0
    seq = ts.set_policy(RegistrationPolicy("closed", accepted_issuers=["did:web:nobody"]))
    assert ts.policy_in_force()["seq"] == seq
    with pytest.raises(RegistrationRefused):
        ts.register(_statement(isign))          # admitted under v1, refused under the new policy


def test_the_service_key_is_not_the_issuer_key(service):
    """A receipt signed with the issuer's own key attests nothing: the whole point is a second party."""
    ts, isign, iverify, sverify = service
    tr = ts.register_transparent(_statement(isign))
    root_then, leaf = ts.root(), ts.entry_leaf(1)
    # the receipt does NOT verify under the issuer's key ...
    wrong = verify_registered_statement(tr, iverify, iverify, leaf, root_then)
    assert wrong["receipt"]["signature_ok"] is False
    # ... and does under the service's, which is the control that makes the line above mean something
    right = verify_registered_statement(tr, iverify, sverify, leaf, root_then)
    assert right["receipt"]["signature_ok"] is True


def test_the_registered_identity_survives_receipt_attachment(service):
    """The log records the ISSUER's statement. Receipts are attached afterwards and change the bytes,
    so an identity that moved when somebody countersigned was never an identity."""
    ts, isign, _iv, _sv = service
    st = _statement(isign)
    tr = ts.register_transparent(st)
    assert tr != st, "attaching a receipt must change the artifact"
    assert scitt.statement_digest(scitt.without_receipts(tr)) == scitt.statement_digest(st)
    entry = json.loads(ts.entry_leaf(1).decode("utf-8"))
    assert entry["statement_sha256"] == scitt.statement_digest(st)


def test_a_receipt_for_another_entry_does_not_bind(service):
    ts, isign, iverify, sverify = service
    tr = ts.register_transparent(_statement(isign, b"one", "memory:one"))
    ts.register_transparent(_statement(isign, b"two", "memory:two"))
    out = verify_registered_statement(tr, iverify, sverify, ts.entry_leaf(2), ts.root())
    assert out["bound"] is False and not out["ok"]
    assert any("different registration" in p for p in out["problems"])


def test_a_statement_with_its_receipt_stripped_is_reported_as_unregistered(service):
    ts, isign, iverify, sverify = service
    st = _statement(isign)
    ts.register(st)
    out = verify_registered_statement(st, iverify, sverify, ts.entry_leaf(1), ts.root())
    assert not out["ok"]
    assert any("never registered" in p or "stripped" in p for p in out["problems"])


def test_an_open_policy_says_so_in_words(service):
    """"No anchors configured" and "anchors deliberately open" look identical in a config file and
    mean opposite things to an auditor, so the policy has to say which it is."""
    open_policy = RegistrationPolicy("open", accepted_issuers=[]).as_dict()
    assert "ANY issuer is admitted" in open_policy["issuer_rule"]
    closed = RegistrationPolicy("closed", accepted_issuers=[ISSUER]).as_dict()
    assert closed["issuer_rule"] == "only the issuers listed"


def test_the_log_survives_a_restart(service):
    ts, isign, iverify, sverify = service
    ts.register_transparent(_statement(isign))
    reopened = TransparencyService(ts.path, ts.policy, ts._sign, ts._verify_issuer,
                                   service_pubkey=ts.service_pubkey)
    assert reopened.size() == ts.size()
    assert reopened.root() == ts.root()
    assert reopened.policy_in_force()["seq"] == 0, "reopening must not append a second policy entry"


def test_describe_carries_the_scope_and_not_the_private_key(service):
    """The first version of this searched for the substring "sign" and matched "co-sign" in the scope
    text. Hunt for the actual secret, or the test fails for a reason unrelated to the property."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as SK
    sk_hex, pk_hex = new_receipt_keypair()
    sk = SK.from_private_bytes(bytes.fromhex(sk_hex))
    ts = TransparencyService(os.path.join(tempfile.mkdtemp(), "l.jsonl"),
                             RegistrationPolicy("p"), sk.sign, lambda _m, _s: True,
                             service_pubkey=pk_hex)
    blob = json.dumps(ts.describe())
    assert sk_hex not in blob, "describe() leaked the service's private key"
    assert pk_hex in blob, "a verifier needs the public key, or the description is useless"
    assert "NON-EQUIVOCATION is not" in ts.describe()["scope"]
    assert ts.describe()["policy_sha256"] == ts.policy.digest()


def test_a_policy_needs_a_name():
    with pytest.raises(ValueError):
        RegistrationPolicy("")


# ---------------------------------------------------------------------------------------------------
# The external half. Everything above is checkable from bytes ONE operator produced. Non-equivocation
# is not, and only somebody else's memory of the head catches it.
# ---------------------------------------------------------------------------------------------------

def _witness(tmp, name):
    from inspeximus.witness_pool import Witness
    return Witness(new_receipt_keypair()[0], state_path=os.path.join(tmp, name + ".json"))


def test_the_head_is_coherent_enough_for_a_witness_to_sign(service):
    """A witness refuses a head whose sth_hash does not commit to its own fields, because a signature
    over a hash nobody re-derives authenticates nothing a reader uses."""
    from inspeximus.core import anchor_binds_its_fields
    ts, _isign, _iv, _sv = service
    head = ts.head()
    assert anchor_binds_its_fields(head)
    assert head["writes_tip"] == ts.root().hex()
    assert head["n_writes"] == ts.size()


def test_independent_witnesses_cosign_and_the_threshold_is_reported(service):
    ts, isign, _iv, _sv = service
    tmp = os.path.dirname(ts.path)
    ws = [_witness(tmp, "a"), _witness(tmp, "b")]
    out = ts.witnessed_head(ws, threshold=2)
    assert out["met"] is True and len(out["cosignatures"]) == 2 and out["refused"] == []
    ts.register(_statement(isign))
    after = ts.witnessed_head(ws, threshold=2)
    assert after["met"] is True, "growing the log is not a fork"
    assert after["head"]["n_writes"] == ts.size()


def test_a_fork_at_a_witnessed_size_is_REFUSED(service):
    """THE property, and the reason any of this is worth running. Two different logs of the same size
    are exactly what showing two histories to two readers looks like. A witness that already signed
    one size must refuse a different tip at that size, and the refusal is the alarm."""
    ts, isign, iverify, _sv = service
    tmp = os.path.dirname(ts.path)
    w = _witness(tmp, "shared")

    ts.register(_statement(isign, b"the real one"))
    first = ts.witnessed_head([w], threshold=1)
    assert first["met"] is True

    # A SECOND log, same operator, same size, different contents: the fork.
    other = TransparencyService(os.path.join(tmp, "log.jsonl"), ts.policy, ts._sign, ts._verify_issuer,
                                service_pubkey=ts.service_pubkey)
    forked = os.path.join(tmp, "forked.jsonl")
    import shutil
    shutil.copy(ts.path, forked)
    fork = TransparencyService(forked, ts.policy, ts._sign, ts._verify_issuer,
                               service_pubkey=ts.service_pubkey)
    # rewrite history: drop the last entry and put a different one at the same position
    fork._entries = fork._entries[:-1]
    with open(forked, "w", encoding="utf-8", newline="\n") as fh:
        for e in fork._entries:
            fh.write(json.dumps(e, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
    fork.register(_statement(isign, b"a different history"))
    assert fork.size() == ts.size(), "the fork must be the SAME size, or this tests a rollback instead"
    assert fork.root() != ts.root()

    # The witness has the same store_id in view, because it is derived from the path... which differs
    # here. Force the honest comparison by asking it about the SAME id.
    fork.store_id = ts.store_id
    out = fork.witnessed_head([w], threshold=1)
    assert out["met"] is False, "a witness co-signed two different histories at one size"
    assert out["refused"], "the refusal is the alarm and must be reported, not swallowed"
    assert "fork" in json.dumps(out["refused"]).lower() or "split" in json.dumps(out["refused"]).lower()


def test_a_witness_that_never_saw_the_log_is_not_evidence_of_anything(service):
    """CONTROL for the refusal above: a FRESH witness signs the fork happily, because it has no
    memory to contradict. The guarantee comes from continuity, not from the signature."""
    ts, isign, _iv, _sv = service
    tmp = os.path.dirname(ts.path)
    ts.register(_statement(isign))
    fresh = _witness(tmp, "amnesiac")
    out = ts.witnessed_head([fresh], threshold=1)
    assert out["met"] is True and out["refused"] == []
