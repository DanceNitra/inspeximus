"""The MCP erasure tools offered the dangerous half of a choice, and could not attribute the erasure.

TWO gaps, measured in research/probes/audit_mcp_erasure_attribution.py with the library path as control.

1. THE SAFE ESCAPE WAS UNREACHABLE. When two sources canonicalise alike, forget_subject refuses: erasing
   would hard-delete a third party's records. The core offers two ways past the guard --

       allow_ambiguous=True   erase every colliding subject      <- the only one MCP had, and the docstring
                                                                    named it as THE answer to the raise
       exact=True             erase the raw-source-equal subset  <- absent from this surface

   so the surface pointed the caller at the over-deleting half. Measured: allow_ambiguous erased Bob's
   record along with Alice's; exact erased Alice's and left Bob's, completing the DSAR either way.

   This is not an edge case. Canonicalisation is host/collection level by design -- 'employee/1001' and
   'employee/1002' both canonicalise to 'employee', and 12 realistic sources collapsed into 3 buckets
   (research/probes/audit_canon_source_collisions.py) -- so the guard fires on the common path.

2. TOMBSTONES WERE UNATTRIBUTED. `authorized_by` (the authorising principal's public key) and
   `authorization` (their signature over the erasure challenge) land in the tombstone's `auth` field, the
   Art.30 record of WHO ordered the deletion. Measured: 2/2 tombstones carried `auth` from the library and
   0/2 from the MCP-reachable call. governance_report is sold as the Art.30 surface while every erasure
   reaching it through MCP was anonymous.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("mcp")


@pytest.fixture()
def mod(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("INSPEXIMUS_PATH", os.path.join(d, "mcp.json"))
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS", "1")
    monkeypatch.delenv("INSPEXIMUS_RECEIPT_PUBKEY", raising=False)
    m = importlib.reload(importlib.import_module("inspeximus.mcp_server"))
    # Written through the STORE, not the MCP remember tool: that tool exposes no `source=`, so a
    # subject-scoped erasure only has something to match when the records came from the library or an
    # ingestion path. That is the situation this server is in whenever it is pointed at an existing store.
    m._MEM.remember("alice's payout account is at bank A", key="alice::payout", object="bank A",
                    source={"doc": "crm.example.com/Alice"})
    m._MEM.remember("bob's payout account is at bank B", key="bob::payout", object="bank B",
                    source={"doc": "crm.example.com/alice"})
    return m


SUBJ = "crm.example.com/Alice"


def _texts(m):
    return sorted(r["text"][:12] for r in m._MEM.items if r.get("status") == "active")


def test_the_collision_guard_still_refuses_by_default(mod):
    """The control: the guard must fire, or nothing below proves anything."""
    from inspeximus.core import AmbiguousSubject
    with pytest.raises(AmbiguousSubject):
        mod.forget_subject(SUBJ)


def test_exact_completes_the_dsar_without_erasing_the_third_party(mod):
    """THE gap: this call was impossible over MCP."""
    mod.forget_subject(SUBJ, exact=True, request_id="DSAR-114", basis="GDPR Art.17")
    left = _texts(mod)
    assert any("bob" in t for t in left), "exact=True erased the colliding third party"
    assert not any("alice" in t for t in left), "exact=True did not complete the erasure"


def test_allow_ambiguous_still_erases_the_whole_bucket(mod):
    """The other control: the dangerous escape must keep working, and keep being the dangerous one."""
    mod.forget_subject(SUBJ, allow_ambiguous=True, request_id="DSAR-115")
    assert _texts(mod) == [], "allow_ambiguous no longer erases every colliding subject"


def test_the_tombstone_records_who_authorised_the_erasure(mod):
    res = mod.forget_subject(SUBJ, exact=True, request_id="DSAR-116", basis="GDPR Art.17",
                             authorized_by="ab" * 32, authorization="cd" * 64)
    assert res["erased"] >= 1, res
    attributed = [t for t in mod._MEM._tombstones if (t.get("auth") or {}).get("authorized_by")]
    assert attributed, "the erasure left no record of who authorised it"
    assert attributed[0]["auth"]["authorized_by"] == "ab" * 32
    assert attributed[0]["auth"]["basis"] == "GDPR Art.17"


def test_an_unattributed_erasure_is_still_allowed(mod):
    """CONTROL. The fields are evidence, not a gate -- adding them must not make erasure require them."""
    res = mod.forget_subject(SUBJ, exact=True, request_id="DSAR-117")
    assert res["erased"] >= 1, res


def test_forget_carries_the_basis_and_the_authorising_principal(mod):
    ids = [r["id"] for r in mod._MEM.items if r.get("key") == "alice::payout"]
    res = mod.forget(ids=ids, basis="poisoned memory", request_id="INC-9",
                     authorized_by="ef" * 32, authorization="ab" * 64)
    assert res["forgotten"] == 1, res


def test_retention_and_forget_pii_accept_a_basis(mod):
    """Both emit tombstones; neither could state a ground for them."""
    assert mod.retention(max_age_days=0.0, pii_only=False, apply=False,
                         basis="storage limitation", request_id="RET-1")["eligible"] >= 1
    mod.forget_pii(types=["email"], basis="GDPR Art.17", request_id="DSAR-118")


def test_the_docstring_no_longer_names_allow_ambiguous_as_the_answer(mod):
    """The steer was the defect as much as the missing parameter: the text told the caller to reach for
    the option that deletes a third party, and never mentioned the one that does not."""
    doc = mod.forget_subject.__doc__ or ""
    assert "exact=True" in doc, "the safe escape is still unmentioned on the surface that needs it"
    i_exact, i_amb = doc.index("exact=True"), doc.index("allow_ambiguous=True")
    assert i_exact < i_amb, "the over-deleting option is still offered first"
