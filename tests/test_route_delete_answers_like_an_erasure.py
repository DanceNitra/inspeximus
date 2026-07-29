"""`route`'s delete branch performed an erasure and answered as if it had not.

The last two items on the night's own open list, both small and both the same shape as the rest: a field
computed underneath and dropped before the caller could see it.

    route("delete the address", key="alice::addr", capability=...)
      before:  {"intent": "delete", "action": "deleted", "key": ..., "forgotten": 1}
      after:   ... plus "coverage" and "residue_in_store"

`forget()` had computed both. The caller of `route()` had no way to learn that a surviving record still
held the address they had just deleted -- the one thing the residue check exists to tell them.

The delete branch is AUTHORIZATION-GATED (content alone must not destroy memory), so reaching it needs a
signed capability. That is why the first three attempts to verify this fix landed on `assert`,
`authorization_required` and an empty key instead: the branch has several exits and only one erases.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from inspeximus import Inspeximus  # noqa: E402


def _authorized_store():
    sk = Ed25519PrivateKey.generate()
    pub = sk.public_key().public_bytes_raw().hex()
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), receipts=True, revert_pubkey=pub)
    return m, sk


def _capability(sk, challenge):
    raw = (bytes.fromhex(challenge) if all(c in "0123456789abcdef" for c in str(challenge))
           else str(challenge).encode())
    return sk.sign(raw).hex()


def test_a_routed_delete_reports_coverage_and_residue():
    """THE defect: an erasure that answered with a count and nothing else."""
    m, sk = _authorized_store()
    m.remember("alice addr is 5 Elm St", key="alice::addr", object="5 Elm St",
               source={"doc": "hr/alice"})
    m.remember("summary: she lives at 5 Elm St", source={"doc": "svc"})
    out = m.route("delete the address", key="alice::addr",
                  capability=_capability(sk, m.revert_challenge("alice::addr")))
    assert out["action"] == "deleted", f"the fixture did not reach the erasing exit: {out}"
    assert out["forgotten"] == 1
    assert out["coverage"] is not None
    assert out["residue_in_store"]["ok"] is False, "the survivor still holds the erased address"
    assert out["residue_in_store"]["findings"]


def test_an_unauthorized_delete_still_refuses():
    """CONTROL. Adding fields to the erasing exit must not open a path that content alone can walk --
    the gate exists because an utterance must not be able to destroy memory."""
    m, _ = _authorized_store()
    m.remember("alice addr is 5 Elm St", key="alice::addr", object="5 Elm St")
    out = m.route("delete the address", key="alice::addr")
    assert out["action"] == "authorization_required"
    assert any(r.get("status") == "active" for r in m.items), "the record was destroyed without a capability"


def test_the_empty_forget_early_return_carries_coverage():
    """The other item: `forget()` matched nothing and returned without `coverage`, though it carried
    `residue_in_store`. A caller who must check whether a field exists before reading it will read its
    absence as 'nothing to report'."""
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "t.json"), receipts=True)
    out = m.forget(ids=["no-such-id"])
    assert out["forgotten"] == 0
    assert "coverage" in out and out["coverage"] is not None
    assert out["residue_in_store"]["ok"] is True
