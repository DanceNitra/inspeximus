"""forget_subject(dry_run=True) — preview the blast radius of the one irreversible operation.

Why this exists. 1.52.0 made rederive() declare the record its text was rewritten from, which is correct, and
had a consequence nobody would have guessed from the call: a repaired record inherits the taint of the
RETRACTED source, so erasing that source takes the repair with it and the store keeps neither the wrong value
nor the corrected one. forget() had had a dry_run since 1.46.0; forget_subject(), the one that CASCADES, did
not. The operation whose reach you cannot predict was the one with no preview.

The load-bearing test here is not that a preview exists — it is that the preview matches what the real call
then does. A preview that can disagree with the operation is worse than none, because it is trusted.
"""
import copy
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import AmbiguousSubject, Inspeximus


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True, **kw)


def _cascade_store():
    """A repair that descends from a retracted source — the case that motivated this."""
    m = _store()
    root = m.remember("billing uses api-keys", key="billing::auth", object="api-keys",
                      source={"doc": "runbook"})
    m.remember("alice bernard reaches the nightly backup with api-keys", derived=True,
               derived_from=[root], source={"doc": "alice-ticket"})
    m.retract_lineage("runbook")
    m.remember("billing uses oauth2", key="billing::auth", object="oauth2", source={"doc": "adr-014"})
    m.rederive("runbook")
    return m


def test_the_preview_matches_what_the_real_call_does():
    """THE test. Anything else about the preview is decoration if this can drift."""
    m = _cascade_store()
    preview = m.forget_subject("runbook", dry_run=True)
    real = m.forget_subject("runbook", request_id="REQ-1", basis="gdpr-art17")
    assert preview["would_erase"] == real["erased"]
    assert preview["ids"] == real["ids"]


def test_the_preview_mutates_nothing():
    """No delete, no tombstone, no save — it must be safe to run on a production store."""
    m = _cascade_store()
    before_items = copy.deepcopy(m.items)
    before_tombstones = len(getattr(m, "tombstones", []) or [])

    m.forget_subject("runbook", dry_run=True)

    assert m.items == before_items, "the preview changed the store"
    assert len(getattr(m, "tombstones", []) or []) == before_tombstones, "the preview emitted a tombstone"


def test_it_separates_what_names_the_subject_from_what_only_inherited_it():
    """`direct` is what an operator expects; `inherited` is the number they cannot predict, and here it is
    twice the direct count."""
    m = _cascade_store()
    p = m.forget_subject("runbook", dry_run=True)

    assert p["direct"] == 1, p
    assert p["inherited"] == 2, p
    assert p["direct"] + p["inherited"] == p["would_erase"]
    whys = {row["why"] for row in p["sample"]}
    assert whys == {"direct", "inherited"}


def test_it_names_the_other_subjects_that_go_down_with_the_request():
    """One erasure is quietly several. Erasing 'runbook' also destroys records carrying alice-ticket's data,
    and an operator handling a DSR for one person needs to see the other before pressing the button."""
    m = _cascade_store()
    p = m.forget_subject("runbook", dry_run=True)
    assert "aliceticket" in p["also_carrying"], p["also_carrying"]
    assert p["also_carrying"]["aliceticket"] == 2


def test_a_subject_with_no_records_previews_as_zero_rather_than_erroring():
    """Exact equality on purpose: this is the tripwire that makes any new field in the preview a
    deliberate decision. It fired when `coverage` was added, which is the behaviour wanted -- a preview
    silently growing a key an operator then reads as a guarantee is how the coarse-key defects spread."""
    m = _cascade_store()
    p = m.forget_subject("nobody-by-that-name", dry_run=True)
    cov = p.pop("coverage")
    assert p == {"would_erase": 0, "ids": [], "direct": 0, "inherited": 0, "sample": [],
                 "also_carrying": {}, "targets": [], "dry_run": True}
    # A zero-record preview must NOT read as a discharged obligation: nothing registered, nothing
    # contacted, nothing complete.
    assert cov["preview"] is True and cov["complete"] is False
    assert cov["external_targets"] == 0 and cov["confirmed"] == 0 and cov["unregistered"] is True


def test_the_preview_respects_tenant_isolation():
    """A preview that saw across tenants would leak the existence of another tenant's records."""
    a = _store(tenant="acme")
    a.remember("acme secret about dave", source={"doc": "dave"})
    b = Inspeximus(path=a.path, receipts=True, tenant="globex")
    p = b.forget_subject("dave", dry_run=True)
    assert p["would_erase"] == 0, "another tenant's records must not appear in the preview"


def test_a_dsar_for_one_person_does_not_hard_delete_another():
    """THE data-loss bug this preview surfaced, found by an adversarial review of the preview itself.

    `_canon_source` keeps only the host — it exists to collapse sybil variants of one PUBLISHER, and for that
    it is right. As an erasure selector it merged two people: 'crm.example.com/alice' and
    'crm.example.com/bob' are one key. Measured before the fix: a DSAR for Alice reported erased=2, Bob's
    salary/PIP record was gone, and the preview said `also_carrying: {}` — the safety field reported no
    collateral because Bob WAS the request as far as the selector could tell.
    """
    m = _store()
    m.remember("Alice Novak, SSN 123-45-6789", source={"doc": "crm.example.com/alice"})
    m.remember("Bob Horvath salary 91000 EUR, on PIP", source={"doc": "crm.example.com/bob"})
    m.remember("Carol Kiss, unrelated", source={"doc": "other.example.com/carol"})

    p = m.forget_subject("crm.example.com/alice", dry_run=True)
    assert p["would_erase"] == 1, "the preview must not offer to delete the other subject"

    # ASSERTS THE OUTCOME, NOT THE MECHANISM. This used to require AmbiguousSubject, because the two
    # people were indistinguishable to the selector and refusing was the only way to protect Bob. Since
    # subject matching became path-preserving (_canon_subject), 'crm.example.com/alice' and
    # '.../bob' are different keys, so the DSAR now COMPLETES and Bob is untouched -- strictly better
    # than refusing, which left Alice's legal request unperformable. What must hold is unchanged and is
    # checked harder here than the raise ever checked it: exactly Alice goes, and nobody else moves.
    res = m.forget_subject("crm.example.com/alice", request_id="DSAR-1", basis="gdpr-art17")
    assert res["erased"] == 1, res
    left = sorted((r.get("text") or "")[:12] for r in m.items if r.get("status") == "active")
    assert any("Bob" in t for t in left), f"the third party was deleted by another person's DSAR: {left}"
    assert any("Carol" in t for t in left), f"an unrelated subject was deleted: {left}"
    assert not any("Alice" in t for t in left), f"the DSAR did not complete: {left}"

    # The old tail asserted that allow_ambiguous=True then erased BOTH people "deliberately". That
    # escape only ever existed because the two were indistinguishable; with them separable there is no
    # bucket to force, and re-running it here erases 0 simply because Alice is already gone. The escape
    # itself is still exercised where a genuine collision remains -- see the User_42/user-42 fixture.
    again = m.forget_subject("crm.example.com/alice", request_id="DSAR-2", allow_ambiguous=True)
    assert again["erased"] == 0, "nothing is left of this subject to erase twice"
    assert any("Bob" in (r.get("text") or "") for r in m.items), "forcing must still spare the other person"


def test_the_intended_canonical_resolution_still_works():
    """The guard must not break what _canon_source is FOR: erasing 'User 42' when the writer wrote 'user-42'
    has no exact raw match, so canonical resolution is what the caller meant."""
    m = _store()
    m.remember("a note about user 42", source={"doc": "user-42"})
    assert m.forget_subject("User 42", request_id="R", basis="b")["erased"] == 1


def test_the_split_reads_the_records_OWN_source_not_every_dict_value():
    """A record matched purely by taint was reported as `direct` because the first version read every value
    of a dict source. That inverts the one number the split exists to give."""
    m = _store()
    m.remember("a summary", source={"doc": "summary-svc"})
    m.items[-1]["taint"] = ["user42"]
    p = m.forget_subject("user-42", dry_run=True)
    assert (p["direct"], p["inherited"]) == (0, 1), p


def test_a_sourceless_record_matched_by_its_id_key_is_direct():
    """_rec_sources attributes a source-less record to 'id:<rid>'. The preview had no such fallback, so the
    record was reported as inherited when nothing was inherited at all."""
    m = _store()
    rid = m.remember("a source-less note")
    p = m.forget_subject("id:" + rid, dry_run=True)
    assert (p["direct"], p["inherited"]) == (1, 0), p


def test_the_subject_never_appears_in_also_carrying_under_another_spelling():
    m = _store()
    m.remember("x", source={"doc": "user-42"})
    m.items[-1]["taint"] = ["user42", "acme"]
    p = m.forget_subject("user-42", dry_run=True)
    assert set(p["also_carrying"]) == {"acme"}, p["also_carrying"]


def test_dry_run_is_not_the_default():
    """The signature must keep erasing by default — a preview that silently replaced the operation would be
    a far worse failure than no preview at all."""
    m = _cascade_store()
    res = m.forget_subject("runbook", request_id="REQ-2", basis="gdpr-art17")
    assert res["erased"] == 3 and "would_erase" not in res
