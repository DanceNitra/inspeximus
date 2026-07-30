"""The influence gate and the report that describes it must never disagree.

They did. `influence_gate_report()` re-derived the corroboration test inline instead of calling it, and
the copy had drifted from `_is_corroborated`: no slashed/orphan hard block, `require_warrant` ignored
(counting raw `good` where the gate counts `good_warranted`), and no require_warrant condition on the
semantic path. Every one of those errors ran the same direction — the report counted records the gate
refuses — so it OVERSTATED corroborated_frac and UNDERSTATED would_block_frac.

That report exists to tell an operator whether the gate is affordable BEFORE enabling it, so the
instrument was wrong in the direction that causes an outage: measured on a store with
credit_requires_warrant=True and six records holding unwarranted good credit, the gate passed 0 of 6
while the report claimed 6 of 6 with would_block_frac 0.00. Follow that advice, switch the gate on, and
recall(influence_only=True) returns nothing.

These tests pin the invariant that makes the class of bug impossible: ONE predicate,
`_corroboration_verdict`, used by both.
"""
import os
import tempfile

import pytest

from inspeximus.core import Inspeximus


def _store(warrant=False, n=6):
    m = Inspeximus(os.path.join(tempfile.mkdtemp(), "s.json"))
    m.credit_requires_warrant = warrant
    ids = []
    for i in range(n):
        r = m.remember(f"fact {i}: the {i} service drains its queue before rollback", tags=["ops"])
        ids.append(r["id"] if isinstance(r, dict) else r)
    return m, ids


def _really_passes(m):
    byid = {x["id"]: x for x in m.items}
    return sum(1 for r in m.items if r.get("status") == "active" and m._corroborated(r, byid))


@pytest.mark.parametrize("warrant", [False, True])
def test_report_agrees_with_the_gate(warrant):
    """THE REGRESSION. With require_warrant on, the report used to claim 6/6 where the gate passed 0/6."""
    m, ids = _store(warrant)
    m.credit(ids, True)                     # plain, unwarranted good credit
    rep = m.influence_gate_report()
    assert rep["corroborated"] == _really_passes(m), (
        f"report says {rep['corroborated']} corroborated, the gate passes {_really_passes(m)}. "
        f"A gate and its own cost report must not disagree — that is how an operator is told to enable "
        f"a filter that then blocks everything.")
    assert rep["would_block_frac"] == round(1.0 - rep["corroborated_frac"], 3)


def test_a_slashed_record_is_refused_by_both():
    """A landed retraction blocks corroboration on every path — the report must not count it as passing."""
    m, ids = _store()
    m.credit(ids, True)
    before = m.influence_gate_report()["corroborated"]
    # scope="memory": these records carry no source, so the default scope="source" has nothing to
    # slash by and honestly reports slashed=0 (verified).
    assert m.slash([ids[0]], scope="memory")["slashed"] == 1
    after = m.influence_gate_report()["corroborated"]
    assert after == before - 1, "a slashed record must drop out of the corroborated count"
    assert m.influence_gate_report()["corroborated"] == _really_passes(m)


def test_why_recalled_is_actually_read_only():
    """It is documented "Read-only" but called recall() with the default reinforce=True, so every
    inspection bumped value/last_access on the records it was reporting on."""
    m, ids = _store(n=4)
    q = "which service drains its queue"
    snap = [(r["id"], r["value"], r["last_access"]) for r in m.items]
    for _ in range(3):
        m.why_recalled(q)
    after = [(r["id"], r["value"], r["last_access"]) for r in m.items]
    assert snap == after, "an inspector must not mutate the state it inspects"


def test_gate_reason_names_the_bar_that_decided():
    """A record that LOST standing must not be reported as one that never had corroborating sources.

    That was the first version of this fix: the verdict fell through to the last test in the chain and
    reported "0 distinct corroborating source(s)", hiding the single case an operator most needs to see.
    """
    m, ids = _store(n=1)
    sid = ids[0]
    q = "which service drains its queue"
    for _ in range(4):
        hits = [h["id"] for h in m.recall(q, k=1)]
        if sid in hits:
            m.credit(hits, True)
    assert m.why_recalled(q, id=sid)["gated_out"] is False
    assert "earned outcome" in m.why_recalled(q, id=sid)["gate_reason"]

    for _ in range(9):
        m.credit([sid], False)
    b = m.why_recalled(q, id=sid)
    assert b["gated_out"] is True
    assert "standing lost" in b["gate_reason"], (
        f"a suppressed record must say so; got {b['gate_reason']!r}")


def test_standing_lost_surfaces_suppression_and_stays_quiet_otherwise():
    """The emit. Suppression used to be invisible unless a caller already suspected a specific record.

    The control matters as much as the signal: a store where nothing was suppressed must report 0, or
    the number is noise and gets ignored.
    """
    m, ids = _store(n=3)
    assert m.influence_gate_report()["standing_lost"] == 0, "clean store must be quiet"

    m.credit(ids, True)
    assert m.influence_gate_report()["standing_lost"] == 0, "earning standing is not losing it"

    m.credit([ids[0]], False)
    m.credit([ids[0]], False)
    assert m.influence_gate_report()["standing_lost"] == 1, "a record driven below its own good count"
    assert m.influence_gate_report()["corroborated"] == _really_passes(m)


def test_a_single_id_may_be_passed_as_a_string():
    """`credit("abc123", True)` used to iterate the id's CHARACTERS and do nothing.

    A str is iterable, so the most natural single-id call matched no record and returned
    {'updated': []} — a success-shaped result for work that never happened. Measured on credit(),
    slash(), monitor(), spend_irreversible() and rederive(); all five took the argument bare.
    """
    m, ids = _store(n=1)
    one = ids[0]

    assert m.credit(one, True)["updated"] == [one], "a bare-string id must be accepted"
    assert m.credit([one], True)["updated"] == [one], "list form must keep working"
    assert m.credit(None, True)["updated"] == [], "None must still be a safe no-op"

    assert m.slash(one, scope="memory")["slashed"] == 1
