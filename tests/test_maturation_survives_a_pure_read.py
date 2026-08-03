"""Episodic memories must still be able to become semantic, even though reads are pure.

WHY THIS FILE EXISTS. 2.0.0 made `recall(reinforce=...)` default to False so that a read stops
writing. Graduation from the episodic tier to the durable semantic one was implemented as a side
effect of that same read, guarded by `if reinforce and ...`, so it left with it. Measured on the
shipped 2.0.0: 5 of 6 records graduate with reinforcement on, 0 of 6 on the new default, and there
was no other route in the package -- credit(), sleep() and consolidate() all left it at zero. The
slow-decay tier became unreachable and the store stopped maturing.

Nothing went red. 2422 tests passed, the release gate reported READY, and CI was 19/19, because every
test that touched graduation was written for a store whose reads reinforced. A green suite that cannot
tell "graduation is correct" from "graduation never ran" has measured nothing, which is the failure
this repository keeps rediscovering.

So the property under test is not "consolidate() graduates things". It is the PAIR:

    a corroborated, high-value episodic memory DOES mature when consolidation runs,
    and a read still does NOT mature anything.

Either half alone is satisfiable by a bug. Only the pair says the tier is reachable and the read is
still pure. The negative controls carry the other half of the meaning: a maturation pass that
graduates whatever it is handed would pass the positive assertion and be worthless.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import Inspeximus  # noqa: E402
from inspeximus.core import _GRADUATE_VALUE  # noqa: E402


def _corroborated_store(value=None):
    """Six records that clear BOTH graduation conditions: earned credit, and value over the bar."""
    m = Inspeximus(path=None)
    for i in range(6):
        m.remember("the alpha protocol handles case %d" % i, key="k%d" % i)
    m.credit([r["id"] for r in m.items], "good", weight=3)
    for r in m.items:
        r["value"] = _GRADUATE_VALUE + 4.0 if value is None else value
    return m


def _graduated(m):
    return [r for r in m.items if (r.get("meta") or {}).get("graduated_from_episodic")]


def test_consolidate_matures_a_corroborated_memory():
    """The half that 2.0.0 broke."""
    m = _corroborated_store()
    assert _graduated(m) == [], "fixture starts already graduated, so it proves nothing"
    report = m.consolidate()
    grown = _graduated(m)
    assert len(grown) == 6, (
        "no episodic memory matured on a store where every record is corroborated and above the "
        "value bar -- the semantic tier is unreachable again")
    assert all(r["mtype"] == "semantic" for r in grown), "flagged as graduated but left episodic"
    assert report.get("graduated") == 6, (
        "consolidate() matured %d records but reported %r; a report that disagrees with the store is "
        "how this went unnoticed the first time" % (len(grown), report.get("graduated")))


def test_a_read_still_matures_nothing():
    """The half 2.0.0 bought, which the fix must not spend."""
    m = _corroborated_store()
    for _ in range(25):
        m.recall("alpha protocol case", k=5)
    assert _graduated(m) == [], (
        "recall() matured a memory on the default path; the read is writing again")


def test_the_opted_in_read_still_matures():
    """`reinforce=True` is still the old behaviour, and this is also the control for the test above:
    if THIS returns zero, the fixture cannot graduate at all and the assertion above is vacuous."""
    m = _corroborated_store()
    for _ in range(25):
        m.recall("alpha protocol case", k=5, reinforce=True)
    assert len(_graduated(m)) >= 5, (
        "reinforce=True matured nothing either, so the fixture does not exercise graduation and the "
        "pure-read assertion next door is measuring nothing")


@pytest.mark.parametrize("what,build", [
    ("uncorroborated", lambda: _uncorroborated()),
    ("below the value bar", lambda: _corroborated_store(value=1.0)),
])
def test_consolidate_does_not_mature_what_has_not_earned_it(what, build):
    """A pass that graduates everything it is handed satisfies the positive test and is worthless."""
    m = build()
    m.consolidate()
    assert _graduated(m) == [], "consolidate() matured a %s memory" % what


def _uncorroborated():
    m = Inspeximus(path=None)
    for i in range(6):
        m.remember("the alpha protocol handles case %d" % i, key="k%d" % i)
    for r in m.items:          # over the value bar, but never credited and no linked sources
        r["value"] = _GRADUATE_VALUE + 4.0
    return m


def test_the_bar_is_shared_between_the_two_paths():
    """The read path and consolidate() must consult the SAME predicate.

    They were separate blocks of inline logic once; two copies of a corroboration rule drift, and the
    drift is invisible because each side keeps passing its own tests."""
    m = _corroborated_store()
    by_id = {r["id"]: r for r in m.items}
    assert all(m._may_graduate(r, by_id) for r in m.items), "the shared predicate rejects the fixture"
    low = _corroborated_store(value=1.0)
    by_id_low = {r["id"]: r for r in low.items}
    assert not any(low._may_graduate(r, by_id_low) for r in low.items), (
        "the shared predicate admits a record below the value bar")
