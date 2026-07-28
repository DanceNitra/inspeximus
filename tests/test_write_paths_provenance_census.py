"""A census of which write paths can attach provenance -- including the ones that still cannot.

Yesterday's fix put `source`/`derived_from` on `remember` and the commit message said the compliance moat
was reachable "from the surface the product is used through". An adversarial review found that false:
`remember` is one of FIVE MCP write paths. `remember_decision` -- the tool whose own docstring calls it
"the thing that actually matters" -- reproduced the original defect exactly:

    mcp.remember_decision("we will bill alice monthly", topic="billing::alice")
      forget_subject('alice')          would_erase = 0
      forget_subject('hr/alice')       would_erase = 0
      forget_subject('billing::alice') would_erase = 0

That is this project's own standing lesson -- audit EVERY door, fix the CLASS -- broken in the same commit
that cited it. The deeper finding was that CORE did not offer provenance on those paths either, so it was
never a parameter-passthrough gap; the library had no way to attribute a decision to a person.

`remember_decision` is now closed, and it is the one that mattered most: a decision is usually ABOUT
someone ("we're billing Alice monthly"), which is exactly what a DSAR must reach.

`route`, `observe` and `resolve_reopened` are NOT closed. This test says so out loud, in the pattern that
made the last finding findable at all -- a characterisation test that records what IS and instructs its
own replacement. The alternative, saying nothing, is how "the surface" came to mean "one tool of several".
"""
from __future__ import annotations

import importlib
import inspect
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402

CLOSED = ("remember", "remember_decision")
#: Characterisation, not a wish. These reach the store and cannot attribute what they write.
#: WHEN ONE IS CLOSED, MOVE IT TO `CLOSED` AND ADD A REACHABILITY TEST BELOW -- do not delete the entry.
STILL_OPEN = ("route", "observe", "resolve_reopened")


@pytest.fixture()
def mcp(monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_PATH", os.path.join(tempfile.mkdtemp(), "m.json"))
    import inspeximus.mcp_server as m
    return importlib.reload(m)


def _params(fn):
    return list(inspect.signature(fn).parameters)


def test_the_closed_write_paths_accept_provenance(mcp):
    for name in CLOSED:
        ps = _params(getattr(mcp, name))
        assert "source" in ps and "derived_from" in ps, f"{name} lost its provenance parameters"


def test_the_open_write_paths_are_named_rather_than_forgotten(mcp):
    """If this fails because a path GAINED `source`, that is the good outcome: move it to CLOSED and
    write the reachability test. It must not pass silently either way."""
    for name in STILL_OPEN:
        fn = getattr(mcp, name, None)
        if fn is None:
            continue
        ps = _params(fn)
        assert "source" not in ps, (
            f"{name} now accepts `source` -- move it to CLOSED and add a reachability test, "
            "the way this file's docstring says")


def test_a_decision_written_with_a_source_is_erasable_by_subject(mcp):
    """THE defect this commit closes. A decision about a person must be reachable by that person."""
    out = mcp.remember_decision("we will bill alice monthly", because="she asked",
                                topic="billing::alice", source="hr/alice")
    assert out["attributable"] is True
    st = Inspeximus(path=os.environ["INSPEXIMUS_PATH"], receipts=True)
    res = st.forget_subject("hr/alice", request_id="d", basis="art17", dry_run=True)
    assert res["would_erase"] == 1


def test_a_decision_without_a_source_stays_unreachable(mcp):
    """CONTROL, and the same design decision as on `remember`: the caller supplies the subject, the
    server never invents one. It also proves the previous test is not passing vacuously."""
    out = mcp.remember_decision("we will bill bob monthly", topic="billing::bob")
    assert out["attributable"] is False
    st = Inspeximus(path=os.environ["INSPEXIMUS_PATH"], receipts=True)
    assert st.forget_subject("hr/bob", request_id="d", basis="art17", dry_run=True)["would_erase"] == 0


def test_the_core_method_takes_provenance_too(mcp):
    """The gap was never only in the server: core.remember_decision had no way to accept a source, so
    no surface could have passed one."""
    ps = [p for p in inspect.signature(Inspeximus.remember_decision).parameters if p != "self"]
    assert "source" in ps and "derived_from" in ps


def test_a_decision_keeps_its_keyed_supersession_when_a_source_is_added(mcp):
    """CONTROL on the other axis. Threading provenance through must not disturb what this method is
    FOR -- a new decision on a topic retiring the old one."""
    mcp.remember_decision("bill alice monthly", topic="billing::alice", source="hr/alice")
    mcp.remember_decision("bill alice yearly", topic="billing::alice", source="hr/alice")
    st = Inspeximus(path=os.environ["INSPEXIMUS_PATH"], receipts=True)
    active = [r for r in st.items
              if r.get("key") == "decision::billing::alice" and r.get("status") == "active"]
    assert len(active) == 1
    assert "yearly" in (active[0].get("object") or "")
