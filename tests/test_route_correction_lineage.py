"""A correction written through `route()` must go when the thing it corrects goes.

THE DEFECT, measured before the fix. Alice's address is written with `source={'doc': 'hr/alice'}` and then
corrected through `route()`. Her right-to-erasure request:

    forget_subject('hr/alice')  ->  erased = 1, reported as success

    what survived:  [active] 'actually alice moved to 9 Oak Ave'
    residue of the CURRENT value '9 Oak Ave':  True
    residue of the OLD value '5 Elm St':       False

The erasure removed the stale address and kept the live one. That is not a partial erasure, it is the
inverse of erasure: the record that survives is the person's current data, and the caller was told the
request succeeded. The same correction written through `remember(source=...)` erased both.

THE FIX IS NOT A PARAMETER. `route()` knows exactly which record it is correcting, so it declares the
edge itself -- the same category as `revert`/`submit_revert`, where the STORE owns the derivation rather
than forwarding a caller's argument. `forget_subject` cascades along `derived_from`, so the correction
becomes reachable with no change required of the caller. A `source=` parameter exists as well, but the
lineage edge alone is what closes the hole, and that matters: the callers who hit this are the ones who
never passed provenance in the first place.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402


def _store():
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), receipts=True)


def _blob(m):
    return " ".join((r.get("text") or "") + str(r.get("object") or "") for r in m.items)


def test_a_routed_correction_goes_with_the_subject_it_corrects():
    """THE defect, and note that NO source is passed to route -- the lineage edge alone must do it."""
    m = _store()
    m.remember("alice home address is 5 Elm St", key="alice::addr", object="5 Elm St",
               source={"doc": "hr/alice"})
    m.route("actually alice moved to 9 Oak Ave", key="alice::addr", object="9 Oak Ave")
    assert m.forget_subject("hr/alice", request_id="D", basis="art17")["erased"] == 2
    assert "9 Oak" not in _blob(m), "the CURRENT value survived the erasure of the subject"


def test_it_also_works_when_the_caller_does_name_a_source():
    m = _store()
    m.remember("alice home address is 5 Elm St", key="alice::addr", object="5 Elm St",
               source={"doc": "hr/alice"})
    m.route("actually alice moved to 9 Oak Ave", key="alice::addr", object="9 Oak Ave",
            source="hr/alice")
    assert m.forget_subject("hr/alice", request_id="D", basis="art17")["erased"] == 2
    assert "9 Oak" not in _blob(m)


def test_the_source_parameter_reaches_a_record_lineage_cannot():
    """Isolates `source`. Every other test here has a lineage edge doing the work, so dropping the
    source parameter changed nothing and a mutation that removed it SURVIVED. A route() that asserts a
    value on a NEW key has no parent to derive from -- only the caller's source can make it reachable."""
    m = _store()
    out = m.route("alice's emergency contact is bob", key="alice::contact", object="bob",
                  source="hr/alice")
    assert out["intent"] == "assert"
    rec = next(r for r in m.items if r["id"] == out["id"])
    assert not rec.get("derived_from"), "this arm is only meaningful with no lineage edge present"
    assert m.forget_subject("hr/alice", request_id="D", basis="art17")["erased"] == 1


def test_without_a_source_a_first_assertion_stays_unreachable():
    """CONTROL for the arm above: the caller supplies the subject, route never invents one."""
    m = _store()
    m.route("alice's emergency contact is bob", key="alice::contact", object="bob")
    assert m.forget_subject("hr/alice", request_id="D", basis="art17")["erased"] == 0


def test_the_correction_declares_the_record_it_corrects():
    m = _store()
    first = m.remember("region is frankfurt", key="cfg::r", object="frankfurt")
    out = m.route("actually the region is ohio", key="cfg::r", object="ohio")
    rec = next(r for r in m.items if r["id"] == out["id"])
    assert rec["derived_from"] == [first]


def test_the_parent_is_the_corrected_record_not_merely_some_record():
    """A mutation that took `self.items[0]` instead of the key's current record SURVIVED, because in
    every other fixture here the corrected record happens to BE items[0]. The fixture could not express
    the difference, which is a weakness in the test and not in the code. So: an unrelated record goes in
    first, and the parent must still be alice's."""
    m = _store()
    unrelated = m.remember("weather is fine", key="misc::w", object="fine")
    target = m.remember("alice home address is 5 Elm St", key="alice::addr", object="5 Elm St",
                        source={"doc": "hr/alice"})
    out = m.route("actually alice moved to 9 Oak Ave", key="alice::addr", object="9 Oak Ave")
    rec = next(r for r in m.items if r["id"] == out["id"])
    assert rec["derived_from"] == [target]
    assert unrelated not in (rec.get("derived_from") or [])
    # and the consequence: erasing an unrelated subject must not drag the correction along
    assert m.forget_subject("hr/alice", request_id="D", basis="art17")["erased"] == 2
    assert "weather is fine" in _blob(m), "an unrelated record was pulled into the erasure"


def test_a_first_assertion_has_nothing_to_derive_from():
    """CONTROL. Declaring a parent that does not exist would be inventing provenance."""
    m = _store()
    out = m.route("the region is frankfurt", key="cfg::r", object="frankfurt")
    rec = next(r for r in m.items if r["id"] == out["id"])
    assert out["intent"] == "assert"
    assert not rec.get("derived_from")


def test_routing_still_routes():
    """CONTROL. The point of route() is that the correction becomes current. A change that made every
    correction erasable while breaking supersession would pass every test above."""
    m = _store()
    m.remember("region is frankfurt", key="cfg::r", object="frankfurt")
    out = m.route("actually the region is ohio", key="cfg::r", object="ohio")
    assert out["intent"] == "correct" and out["event"] == "UPDATE"
    active = [r.get("object") for r in m.items
              if r.get("key") == "cfg::r" and r.get("status") == "active"]
    assert active == ["ohio"]


def test_a_ghost_subject_still_reaches_nothing():
    """CONTROL. Making corrections reachable must not make them reachable by the wrong subject."""
    m = _store()
    m.remember("alice addr", key="a", object="x", source={"doc": "hr/alice"})
    m.route("actually y", key="a", object="y")
    assert m.forget_subject("hr/nobody-here", request_id="G", basis="art17")["erased"] == 0


def test_an_ECHO_record_goes_with_the_subject_too():
    """`route` writes at five sites and the first fix gave provenance to ONE of them -- the correction --
    while the commit message said the hole was closed. The echo branch then wrote an unattributed record
    holding the subject's address verbatim, and it survived her erasure:

        forget_subject('hr/alice')  ->  erased 2
        survivors: ['alice addr is 5 Elm St and 9OakAve']

    Found by the in-store residue check built an hour earlier, which flagged ok=false on a branch nobody
    was looking at. Audit EVERY door -- this repository's own rule, broken inside a single function."""
    m = _store()
    m.remember("alice addr is 5 Elm St", key="a::addr", object="5 Elm St", source={"doc": "hr/alice"})
    m.route("actually alice moved to X", key="a::addr", object="X")
    out = m.route("alice addr is 5 Elm St and 9OakAve", key="a::addr", object="5 Elm St")
    assert out["intent"] == "echo", "this test is about the echo branch; the fixture stopped reaching it"
    res = m.forget_subject("hr/alice", request_id="D", basis="art17")
    assert res["erased"] == 3
    assert "9OakAve" not in _blob(m)
    assert res["residue_in_store"]["ok"] is True


def test_the_echo_is_still_blocked_not_restored():
    """CONTROL. Giving the echo record provenance must not turn a refusal into an acceptance -- the
    whole point of the branch is that the stale value does NOT come back."""
    m = _store()
    m.remember("v is A", key="k", object="A")
    m.route("actually v is B", key="k", object="B")
    out = m.route("v is A", key="k", object="A")
    assert (out["intent"], out["action"]) == ("echo", "blocked")
    active = [r.get("object") for r in m.items if r.get("key") == "k" and r.get("status") == "active"]
    assert active == ["B"]


def test_a_route_with_no_key_declares_no_parent():
    """A mutation that made `_parent()` fall back to `self.items[0]` when there is no key SURVIVED: no
    test covered the keyless call at all. Without a key there is nothing this write is a restatement OF,
    and naming some arbitrary record would be inventing provenance."""
    m = _store()
    m.remember("an unrelated earlier record", key="other", object="x")
    out = m.route("alice mentioned something in passing")
    rec = next(r for r in m.items if r["id"] == out["id"])
    assert out["intent"] == "assert"
    assert not rec.get("derived_from")


def test_the_keyless_echo_carries_provenance_too():
    """The guard-OFF echo branch. A mutation removing its provenance SURVIVED because the only test
    touching that branch asserted the active value and never looked at the record it wrote."""
    m = _store()
    m.echo_guard = False
    m.remember("alice addr is 5 Elm St", key="a::addr", object="5 Elm St", source={"doc": "hr/alice"})
    m.route("actually alice moved to X", key="a::addr", object="X")
    out = m.route("alice addr is 5 Elm St", key="a::addr", object="5 Elm St")
    rec = next(r for r in m.items if r["id"] == out["id"])
    assert rec.get("derived_from"), "the keyless echo record declares no parent"
    assert not rec.get("key"), "it must stay KEYLESS -- that is why this branch exists"


def test_the_keyless_echo_still_cannot_clobber():
    """CONTROL for the guard-off branch, which writes KEYLESS on purpose so it cannot LWW-clobber the
    current value. Keyless is not the same as unattributable, and adding the lineage edge must not
    quietly give it a key back."""
    m = _store()
    m.echo_guard = False
    m.remember("v is A", key="k", object="A")
    m.route("actually v is B", key="k", object="B")
    m.route("v is A", key="k", object="A")
    active = [r.get("object") for r in m.items if r.get("key") == "k" and r.get("status") == "active"]
    assert active == ["B"]


def test_a_revert_restore_goes_with_the_subject_too():
    """The census found route() has NINE write sites where five had been fixed. These two --
    `restore {k} to {named}` and `restore {k} to {chain[0]}` -- write ON A KEY, so the same argument as
    the correction and echo branches applies, and they were missed when those were done. Fourth instance
    in one night of fixing the sites I measured and writing as though I had fixed the class."""
    m = _store()
    m.remember("alice addr is 5 Elm St", key="alice::addr", object="5 Elm St",
               source={"doc": "hr/alice"})
    m.route("actually alice moved to 9OakAve", key="alice::addr", object="9OakAve")
    out = m.route("go back to 5 Elm St", key="alice::addr", object="5 Elm St", policy="trusting")
    assert out["intent"] == "revert", "the fixture stopped reaching the restore branch"
    res = m.forget_subject("hr/alice", request_id="D", basis="art17")
    assert res["erased"] == 3
    assert "9OakAve" not in _blob(m) and "5 Elm" not in _blob(m)
    assert res["residue_in_store"]["ok"] is True


def test_the_restore_declares_the_record_it_restores_over():
    """`_parent()` closed over the `key` PARAMETER, while the revert branches resolve their own
    (`k = key or self._route_key(low)`). A helper reading the wrong variable is worse than four copies:
    it looks like it was applied everywhere and silently declares no parent where it matters."""
    m = _store()
    m.remember("v is A", key="cfg::mode", object="A")
    m.route("actually v is B", key="cfg::mode", object="B")
    out = m.route("restore cfg::mode to A", key="cfg::mode", object="A", policy="trusting")
    rec = next(r for r in m.items if r["id"] == out["id"])
    parents = rec.get("derived_from") or []
    assert len(parents) == 1
    assert any(r["id"] == parents[0] for r in m.items), "the declared parent is not a real record"


def test_the_revert_to_ORIGINAL_branch_declares_a_parent_too():
    """A separate branch from `restore {k} to {named}`: "go back to the original" walks to chain[0].
    Its mutation SURVIVED because no test uttered anything matching `\\b(original|very first|started
    with|initial)\\b`, so the branch was never entered."""
    m = _store()
    m.remember("the deploy region is frankfurt", key="deploy::region", object="frankfurt",
               source={"doc": "hr/alice"})
    m.route("actually the region is ohio", key="deploy::region", object="ohio")
    m.route("actually the region is tokyo", key="deploy::region", object="tokyo")
    out = m.route("go back to the original", key="deploy::region", policy="trusting")
    rec = next(r for r in m.items if r["id"] == out["id"])
    assert (rec.get("meta") or {}).get("routed") == "revert_original", \
        "this test is about the ORIGINAL branch; the fixture stopped reaching it"
    assert rec.get("derived_from"), "the restore-to-original record declares no parent"
    assert m.forget_subject("hr/alice", request_id="D", basis="art17")["residue_in_store"]["ok"] is True


def test_the_parent_uses_the_key_route_RESOLVED_not_the_one_passed_in():
    """`_parent()` used to close over the `key` parameter. Every existing test passed `key=` explicitly,
    so `k` and `key` were always equal and the mutation that reverts to reading `key` SURVIVED. Here the
    key is resolved FROM THE UTTERANCE with no `key=` argument, which is the case the helper exists for."""
    m = _store()
    m.remember("the deploy::region is frankfurt", key="deploy::region", object="frankfurt",
               source={"doc": "hr/alice"})
    m.route("actually the deploy::region is ohio", key="deploy::region", object="ohio")
    out = m.route("go back to the original deploy::region", policy="trusting")
    assert out["intent"] == "revert" and out.get("key") == "deploy::region", \
        f"the key was not resolved from the utterance: {out}"
    rec = next(r for r in m.items if r["id"] == out["id"])
    assert rec.get("derived_from"), "no parent declared when the key came from the utterance"


def test_the_keyless_branches_keep_the_callers_source():
    """`delete` and `revert` utterances that resolve NO key wrote with no source at all, though the
    caller had supplied one. There is nothing to derive from without a key -- but a source is still a
    source, and dropping it made the record unattributable for no reason."""
    m = _store()
    for utterance, intent in (("forget about that thing", "delete"),
                              ("go back to what we had", "revert")):
        out = m.route(utterance, source="hr/alice")
        assert out["intent"] == intent and out["key"] is None
        rec = next(r for r in m.items if r["id"] == out["id"])
        assert rec["source"] == {"doc": "hr/alice"}


def test_a_revert_on_a_subjectless_key_is_not_swept_up():
    """CONTROL. Config has no data subject; giving restores a lineage edge must not enlist them in an
    unrelated DSAR."""
    m = _store()
    m.remember("region is frankfurt", key="cfg::r", object="frankfurt")
    m.route("actually the region is ohio", key="cfg::r", object="ohio")
    m.route("go back to frankfurt", key="cfg::r", object="frankfurt", policy="trusting")
    assert m.forget_subject("hr/alice", request_id="D", basis="art17")["erased"] == 0


def test_a_correction_on_a_subjectless_key_erases_nothing_by_subject():
    """CONTROL. Config has no data subject; a DSAR must not start sweeping up unrelated records just
    because they now carry a lineage edge."""
    m = _store()
    m.remember("region is frankfurt", key="cfg::r", object="frankfurt")
    m.route("actually the region is ohio", key="cfg::r", object="ohio")
    assert m.forget_subject("hr/alice", request_id="D", basis="art17")["erased"] == 0
