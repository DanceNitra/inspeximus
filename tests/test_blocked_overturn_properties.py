"""The five properties a refused overturn has to have, written BEFORE the fix.

Context. `consolidate()`'s state-toggle path decides whether a contradicting record overturns a
standing one. Two opt-in guards can refuse that overturn: `supersede_requires_corroboration` and
`supersede_persistence`. 2.1.1 added a review flag to the first of them and shipped a defect anyway,
so this file exists to state the properties independently of any particular fix, and to fail loudly
wherever the shipped code does not have them.

The map of the path being tested, from `inspeximus/core.py`:

    active.sort(key=-value)                       <- ordering input, reachable by reinforcement/credit
    for a in active:
      for b in active[i+1:]:
        if b.id in a.links: continue              <- once linked, the pair is never re-examined
        if similarity >= dup_threshold and (negation_clash or value_clash):
          older, newer = (a, b) if vf(a) <= vf(b) else (b, a)
                                                  <- ties in ts fall back to the value sort
          corroboration guard -> link, flag the ANCHOR, continue
          persistence guard   -> link, NO flag, continue
          otherwise           -> older.status = "superseded"

WHY EACH PROPERTY, rather than "these seem sensible":

  P1  when the dispute is REFUSED (both records still active) the reader of the surviving value must
      be told, and that must not depend on `value`. When it is RESOLVED instead -- the contradiction
      itself gets superseded -- there is nothing contested and a flag would be a false alarm.
      Measured on 2.1.1: boosting the contradiction's value moved the flag onto the attacker's own
      record and left a plain recall() of the true value showing under_review=None.
  P2  both guards, not one. 2.1.1 gave the flag to the corroboration guard only.
  P3  a TRIAGEABLE steward queue. An actor who cannot overturn a fact may well contest N genuine
      facts, and the readers of those facts should be told -- so the property is not a count cap but
      that every entry names its reason AND the record that contested it, making a single-source
      campaign one filter instead of N investigations.
  P4  `_do_reopen` came from the observe() read path; calling it from consolidation must not destroy
      an in-flight observe() accrual.
  P5  the persistence guard's decision must not depend on processing order. Traced on 2.1.1: the same
      store gave support=1 (refused) or support=3 (allowed) depending only on a `value` boost.

Every test carries the control that makes it non-vacuous: a fixture that never reaches the guard
would satisfy most of these by accident.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import Inspeximus  # noqa: E402

STANDING = "the office printer is on floor 3"
CONTRA = "the office printer is not on floor 3"
GUARDS = ("corroboration", "persistence")


def _store(guard):
    m = Inspeximus(path=None)
    if guard == "corroboration":
        m.supersede_requires_corroboration = True
    elif guard == "persistence":
        m.supersede_persistence = 3
    return m


def _contested(guard, boost_contradiction=False):
    """A standing fact and one uncorroborated contradiction, plus two fillers the attacker also wrote.

    `boost_contradiction` raises the contradiction's `value`, which is the only thing that changes the
    outer loop's sort order -- and `value` is reachable by an attacker through reinforcement or credit.
    Nothing else differs between the two arms."""
    m = _store(guard)
    standing = m.remember(STANDING, key="p::a")
    contra = m.remember(CONTRA, key="p::b", source={"doc": "one-actor.internal"})
    f1 = m.remember("filler about the office printer floor", source={"doc": "one-actor.internal"})
    f2 = m.remember("more filler about the office printer floor", source={"doc": "one-actor.internal"})
    by_id = {r["id"]: r for r in m.items}
    by_id[contra]["links"] = [f1, f2]
    if boost_contradiction:
        by_id[contra]["value"] = 9.0
    return m, standing, contra


def _reader_sees(m, text=STANDING):
    """Exactly what a consumer calling recall() and nothing else observes about `text`."""
    hits = [h for h in (m.recall(STANDING, k=10) or []) if h["text"] == text]
    return hits[0].get("under_review") if hits else None


def _status(m, rid):
    return next(r for r in m.items if r["id"] == rid)["status"]


# ── P0: the fixture must reach the guard at all ──────────────────────────────────────────────────

@pytest.mark.parametrize("guard", GUARDS)
def test_control_the_guard_is_reached_and_refuses_the_overturn(guard):
    """Everything below is meaningless if the contradiction never gets as far as the guard."""
    m, standing, _ = _contested(guard)
    m.consolidate()
    assert _status(m, standing) == "active", (
        f"{guard}: the overturn was NOT refused, so no property below is being exercised")

    off = Inspeximus(path=None)
    a = off.remember(STANDING, key="p::a")
    off.remember(CONTRA, key="p::b", source={"doc": "one-actor.internal"})
    off.consolidate()
    assert _status(off, a) == "superseded", (
        "with both guards OFF the contradiction did not overturn anything either, so this fixture "
        "does not exercise the toggle path and the refusals above prove nothing")


# ── P1 + P2: the surviving value is annotated, on both guards, regardless of value ───────────────

@pytest.mark.parametrize("guard", GUARDS)
@pytest.mark.parametrize("boost", [False, True], ids=["contradiction-value-low", "contradiction-value-high"])
def test_p1_the_surviving_value_is_annotated_regardless_of_ordering(guard, boost):
    """The reader of the value that SURVIVED must be told a retraction arrived and was refused.

    `boost` only changes the contradiction's `value`. If the annotation depends on it, an attacker who
    can raise their own record's value silences the warning on the record they attacked."""
    m, standing, contra = _contested(guard, boost_contradiction=boost)
    m.consolidate()
    standing_status, contra_status = _status(m, standing), _status(m, contra)

    # THREE outcomes, and only one of them owes the reader a warning. The first version of this test
    # missed that and demanded a flag in a case where the contradiction had simply LOST -- which would
    # have driven a fix that raises a false alarm on a record that won its dispute.
    if standing_status == "superseded":
        pytest.fail(f"{guard}, boost={boost}: the standing fact was overturned by an uncorroborated "
                    f"single-source contradiction; the guard did not hold at all")
    if contra_status == "superseded":
        # RESOLVED, not refused: the contradiction lost outright and nothing is contested any more.
        assert _reader_sees(m) is None, (
            f"{guard}, boost={boost}: the contradiction was superseded, so the standing fact is not "
            f"contested -- flagging it here is a false alarm on a record that won")
        return
    # REFUSED: both records are still active, so the dispute is unresolved and the reader must know.
    assert _reader_sees(m) is True, (
        f"{guard}, boost={boost}: both records are still active, so the dispute is unresolved, and a "
        f"plain recall() of the surviving value shows no sign that a retraction arrived")


# ── P3: the steward queue must be TRIAGEABLE, not small ─────────────────────────────────────────

@pytest.mark.parametrize("guard", GUARDS)
def test_p3_a_campaign_from_one_source_is_dismissable_in_one_pass(guard):
    """The first version of this test asserted `reopened() <= 1`, and that was the wrong property.

    If one actor contradicts eight DIFFERENT facts, those eight facts genuinely are contested and the
    reader of each one should be told -- capping the queue would suppress true information to make a
    number look good. What an attacker must not get is unbounded steward WORK: every entry has to say
    why it was queued and who queued it, so a campaign from a single unverified source is one filter
    rather than N investigations."""
    n = 8
    m = _store(guard)
    for i in range(n):
        m.remember(f"the printer on floor {i} is working", key=f"pr::{i}")
    for i in range(n):
        m.remember(f"the printer on floor {i} is not working", key=f"atk::{i}",
                   source={"doc": "one-actor.internal"})
    m.consolidate()

    queue = m.reopened() or []
    if not queue:
        pytest.fail(f"{guard}: nothing was queued at all, so this test is not exercising the path")

    reasons = {e.get("reason") for e in queue}
    assert reasons <= {"uncorroborated_contradiction", "insufficient_persistence"}, (
        f"{guard}: entries queued by a blocked overturn carry reasons {reasons}; a steward cannot "
        f"filter the class if it is not labelled")

    missing = [e["id"] for e in queue if not e.get("contested_by")]
    assert not missing, (
        f"{guard}: {len(missing)} of {len(queue)} queue entries do not say WHICH record contested "
        f"them, so a campaign from one source cannot be dismissed together and each entry becomes a "
        f"separate investigation")


# ── P4: consolidation must not clobber an in-flight observe() accrual ────────────────────────────

@pytest.mark.parametrize("guard", GUARDS)
def test_p4_a_blocked_write_does_not_destroy_an_in_flight_observation(guard):
    """`_do_reopen` belongs to the observe() read path. Calling it from consolidation must not throw
    away evidence another party is part-way through accumulating."""
    m = _store(guard)
    standing = m.remember(STANDING, key="p::a")
    m.observe(CONTRA, key="p::a", support="auditor.pdf")
    before = dict(next(r for r in m.items if r["id"] == standing).get("meta") or {})

    m.remember(CONTRA, key="p::b", source={"doc": "one-actor.internal"})
    m.consolidate()
    after = dict(next(r for r in m.items if r["id"] == standing).get("meta") or {})

    lost = [k for k in before if k.startswith("_reopen") and k not in after]
    assert not lost, (
        f"{guard}: an attacker's single blocked write destroyed in-flight observation state {lost}; "
        f"one unverified writer can reset another party's evidence accrual")


# ── P5: the persistence decision must not depend on processing order ─────────────────────────────

def test_p5_the_persistence_guard_decides_the_same_way_under_either_ordering():
    """Same store, same config, same records -- only `value` differs, which decides sort order.

    Traced on 2.1.1: support counted 1 (refuse) or 3 (allow) depending only on the boost, because it
    is summed over records whose `status` other pairs in the same pass may already have changed."""
    outcomes = set()
    for boost in (False, True):
        m, standing, _ = _contested("persistence", boost_contradiction=boost)
        m.consolidate()
        outcomes.add(_status(m, standing))
    assert len(outcomes) == 1, (
        f"the persistence guard reached different verdicts {outcomes} on the same store depending "
        f"only on the contradiction's value; whether a fact is overturned must not be decided by "
        f"the attacker's own ranking score")
