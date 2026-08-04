"""`supersede_requires_corroboration` must use the SAME bar it has always claimed to use.

The guard's own comment said "same bar as graduation (earned credit, or >=2 links)". The code did not
do that. It read `len(newer["links"]) >= 2` -- a raw LINK COUNT, requiring neither distinct sources nor
verified keys -- while graduation counts distinct canonical sources, or distinct Ed25519 keys under
`strict_corroboration`. So the guard was strictly weaker than the bar it named, and turning
`strict_corroboration` on did not touch it.

Measured before the fix, on the fixture below: an attacker holding ONE source string and two filler
records it also wrote overturned a standing fact. The old predicate said True; the graduation bar said
False on the identical records.

WHAT THIS FILE ASSERTS, and why each part is here:

  * the attacker's cluster does NOT overturn a standing fact (the fix);
  * a legitimate change with three genuinely distinct sources still DOES (the fix is not just "reject
    everything", which would pass the first assertion and be useless);
  * with the guard OFF the attacker's cluster DOES overturn it -- without this the two above are
    satisfied by a fixture that never reaches the guard at all, which is exactly the mistake made
    while investigating this: the first measurement used the same `key` for both records, so keyed
    last-write-wins superseded at WRITE time and the consolidate-time guard was never executed. The
    number looked right and meant nothing.
  * and the keyed path is asserted to bypass the guard, because that is a real property of the design
    and is better written down than rediscovered.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import Inspeximus  # noqa: E402

STANDING = "the office printer is on floor 3"
CONTRADICTION = "the office printer is not on floor 3"
ATTACKER = ["attacker.internal"] * 3
LEGITIMATE = ["bank-statement.pdf", "vendor-invoice.pdf", "auditor-report.pdf"]


def _toggle_fixture(sources, guard=True, strict=False):
    """Two records that CONTRADICT under DIFFERENT keys, so supersession is decided by the
    consolidate-time toggle path -- the one this guard actually protects."""
    m = Inspeximus(path=None)
    m.supersede_requires_corroboration = guard
    m.strict_corroboration = strict
    standing = m.remember(STANDING, key="printer::a")
    newer = m.remember(CONTRADICTION, key="printer::b", source={"doc": sources[0]})
    f1 = m.remember("filler about the office printer floor", source={"doc": sources[1]})
    f2 = m.remember("more filler about the office printer floor", source={"doc": sources[2]})
    by_id = {r["id"]: r for r in m.items}
    by_id[newer]["links"] = [f1, f2]
    return m, standing


def _status(m, rid):
    return next(r for r in m.items if r["id"] == rid)["status"]


def _policy(m, rid):
    return (next(r for r in m.items if r["id"] == rid).get("meta") or {}).get("superseded_by_policy")


def test_an_uncorroborated_contradiction_does_not_overturn_a_standing_fact():
    """One actor, one source string, two links it also wrote. Two links used to be enough."""
    m, standing = _toggle_fixture(ATTACKER, guard=True)
    m.consolidate()
    assert _status(m, standing) == "active", (
        "a single actor overturned a standing fact using two links it supplied itself; the guard is "
        "counting links again rather than distinct sources")


def test_a_corroborated_contradiction_still_overturns_it():
    """The other half. A guard that refuses everything passes the test above and is worthless."""
    m, standing = _toggle_fixture(LEGITIMATE, guard=True)
    m.consolidate()
    assert _status(m, standing) == "superseded", (
        "a contradiction backed by three distinct sources was refused; the guard is now rejecting "
        "legitimate corrections, which is worse than the hole it was closing")


def test_control_the_fixture_actually_reaches_the_guard():
    """Without this, both tests above are satisfied by a fixture that never executes the guard.

    That is not hypothetical: the first investigation of this bug used the same `key` for the standing
    fact and the contradiction, so keyed last-write-wins superseded at WRITE time, the consolidate-time
    guard never ran, and the resulting 'the attacker wins' reading was measured off the wrong path."""
    m, standing = _toggle_fixture(ATTACKER, guard=False)
    m.consolidate()
    assert _status(m, standing) == "superseded", (
        "with the guard OFF the attacker's contradiction did not overturn anything either, so this "
        "fixture never reaches the guard and the assertions in this file measure nothing")
    assert _policy(m, standing) == "state_toggle", (
        f"expected the consolidate-time toggle path, got {_policy(m, standing)!r}")


@pytest.mark.parametrize("strict", [False, True])
def test_the_bar_is_the_same_predicate_graduation_uses(strict):
    """Not 'behaves similarly' -- the same function, so the two cannot drift apart again."""
    m, _ = _toggle_fixture(ATTACKER, guard=True, strict=strict)
    by_id = {r["id"]: r for r in m.items}
    newer = next(r for r in m.items if r["text"] == CONTRADICTION)
    assert not m._graduation_corroborated(newer, by_id)

    m2, _ = _toggle_fixture(LEGITIMATE, guard=True, strict=False)
    by2 = {r["id"]: r for r in m2.items}
    newer2 = next(r for r in m2.items if r["text"] == CONTRADICTION)
    assert m2._graduation_corroborated(newer2, by2)


def test_the_keyed_path_bypasses_this_guard_by_design():
    """A known limit, asserted so it is documented rather than rediscovered.

    Writing the same `key` supersedes at write time under last-write-wins, with no corroboration asked
    for at all. That is the keyed-supersession model working as designed, and it is also the most
    forgeable route in the substrate: whoever knows the key overturns the fact in one write. This guard
    protects the toggle path only, and saying so is more useful than implying wider cover."""
    m = Inspeximus(path=None)
    m.supersede_requires_corroboration = True
    m.strict_corroboration = True
    standing = m.remember(STANDING, key="printer::same")
    m.remember(CONTRADICTION, key="printer::same", source={"doc": "attacker.internal"})
    assert _status(m, standing) == "superseded", "keyed supersession stopped working"
    assert _policy(m, standing) == "keyed_lww", (
        f"expected the keyed last-write-wins path, got {_policy(m, standing)!r}; if this changed, the "
        f"limit documented here is stale")


# ── the blocked contradiction must be VISIBLE, not merely blocked ────────────────────────────────

def test_a_blocked_contradiction_is_surfaced_to_a_plain_reader():
    """Refusing the overturn is half the job; the other half is telling the consumer.

    Before 2.1.1 a caller doing `recall(query)` and nothing else saw the correct live value and no
    indication that a retraction had arrived and been refused. The only trace was an extra link on the
    record, unlabelled and indistinguishable from any other link. The same substrate already flags
    contested records through `observe()` -> `reopened()`, so one of its two contradiction paths was
    not using its own fail-loud channel."""
    m, standing = _toggle_fixture(ATTACKER, guard=True)
    m.consolidate()
    assert _status(m, standing) == "active", "precondition: the overturn must have been refused"

    hits = [h for h in (m.recall("the office printer is on floor 3", k=10) or []) if h["text"] == STANDING]
    assert hits, "the contested value must still be RETURNED; hiding it is a different failure"
    assert hits[0].get("under_review") is True, (
        "a plain recall() gave no sign that a retraction had arrived and been refused")
    assert hits[0].get("review_reason") == "uncorroborated_contradiction", (
        f"expected the blocked-supersession reason, got {hits[0].get('review_reason')!r}")
    assert m.reopened(), "the steward queue does not list the contested record"


def test_control_a_store_with_no_contradiction_stays_silent():
    """Without this, the assertion above is satisfied by a flag that is always on."""
    m = Inspeximus(path=None)
    m.supersede_requires_corroboration = True
    m.remember(STANDING, key="printer::a")
    m.consolidate()
    hits = m.recall("the office printer is on floor 3", k=10) or []
    assert hits, "the fixture returns nothing at all, so the flag test measures nothing"
    assert hits[0].get("under_review") is None, "a record with no contradiction is flagged as contested"
    assert not m.reopened(), "the steward queue is populated with a record nobody contradicted"


def test_a_corroborated_overturn_does_not_leave_a_review_flag():
    """A legitimate change should settle the record, not park it in the steward queue."""
    m, standing = _toggle_fixture(LEGITIMATE, guard=True)
    m.consolidate()
    assert _status(m, standing) == "superseded", "precondition: the corroborated change must go through"
    assert not m.reopened(), (
        "a properly corroborated correction left the old record flagged for review; the queue will "
        "fill with resolved cases and stop being read")
