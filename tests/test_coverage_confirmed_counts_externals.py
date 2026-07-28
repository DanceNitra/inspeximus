"""`confirmed` sits next to `external_targets`, so it has to count the same population.

It counted the manifest's `_SelfTarget` too -- this store attesting about itself -- and the pair then said
things it did not mean. Measured, before:

    1 external target, data absent    external_targets=1  confirmed=2   more confirmations than targets
    1 external target, DISSENTING     external_targets=1  confirmed=1   reads as "1 of 1 confirmed"

The second is the defect. A registered target that answered "the data is still recoverable" was displayed
as a confirmation, and an auditor comparing the two numbers gets the opposite of what happened. `complete`
was correct on every arm -- False on every dissent, with `unconfirmed` naming the leaker -- which is why
the pair was readable as reassuring rather than obviously broken.

The store's own answer is still reported, as `store_self_check`, under its own name. Dropping it would
hide that the self-check ran at all, and `complete` still includes it: an erasure is not complete if the
data survives HERE.

METHOD NOTE, because it nearly cost the finding: the first probe declared `still_recoverable(self, **kw)`
while the manifest calls it positionally, so the adapter raised in EVERY arm and both came back identical.
Two arms agreeing is evidence only once they are known to differ.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402


class Honest:
    """The protocol as documented: erase(subject), still_recoverable(subject, values)."""

    def __init__(self, name, recoverable):
        self.name = name
        self._recoverable = recoverable

    def erase(self, subject):
        return {"erased": 1}

    def still_recoverable(self, subject, values):
        return self._recoverable


class WrongSignature:
    """A real integrator will ship this. It must be recorded as a leak, never as a silent pass."""

    name = "broken-adapter"

    def erase(self, subject):
        return {"erased": 1}

    def still_recoverable(self, **kw):
        return False


def _coverage(*targets):
    st = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), receipts=True)
    for t in targets:
        st.register_erasure_target(t)
    st.remember("alice salary 92000", key="p", object="92000", source={"doc": "hr/alice"})
    return st.forget_subject("hr/alice", request_id="r", basis="art17",
                             values=["92000"])["coverage"]


def test_a_dissenting_target_is_not_counted_as_a_confirmation():
    """THE defect: this pair read 1 and 1."""
    cov = _coverage(Honest("vector-index", True))
    assert cov["external_targets"] == 1
    assert cov["confirmed"] == 0
    assert cov["complete"] is False
    assert cov["unconfirmed"] == ["vector-index"]


def test_confirmed_never_exceeds_the_targets_it_is_printed_beside():
    cov = _coverage(Honest("vector-index", False))
    assert cov["external_targets"] == 1 and cov["confirmed"] == 1


def test_an_honest_confirmation_still_counts():
    """CONTROL. Excluding the self target must not make `confirmed` unreachable -- a number that can
    only be zero is not a measurement."""
    cov = _coverage(Honest("a", False), Honest("b", False))
    assert cov["confirmed"] == 2 and cov["complete"] is True


def test_a_partial_confirmation_is_reported_as_partial():
    cov = _coverage(Honest("a", False), Honest("b", True))
    assert (cov["external_targets"], cov["confirmed"]) == (2, 1)
    assert cov["complete"] is False and cov["unconfirmed"] == ["b"]


def test_an_adapter_that_raises_is_a_leak_not_a_pass():
    cov = _coverage(WrongSignature())
    assert cov["confirmed"] == 0 and cov["complete"] is False
    assert cov["unconfirmed"] == ["broken-adapter"]


def test_the_stores_own_check_is_still_reported():
    """Excluding it from `confirmed` must not hide that it ran."""
    cov = _coverage(Honest("vector-index", False))
    assert cov["store_self_check"] is True


def test_with_no_external_targets_nothing_is_confirmed_and_nothing_is_complete():
    """CONTROL on the other end. The store's own pass must not become a confirmation by itself --
    that is the whole reason the field was separated."""
    cov = _coverage()
    assert cov["external_targets"] == 0 and cov["confirmed"] == 0
    assert cov["complete"] is False and cov["unregistered"] is True
