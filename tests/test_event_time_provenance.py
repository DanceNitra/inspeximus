"""`valid_from` is an EVENT time, and the record has to say whether anyone actually asserted it.

WHY THIS EXISTS. `valid_from` defaulted to the ingest time and nothing recorded that it had, so
`valid_from == ts` was ambiguous between "the fact became true when we wrote it" and "nobody told us
and we used the clock". That is the same shape as our `source` field holding the WRITER rather than
the origin, which is how a store reached 98.3% source coverage with 0.01% actually re-checkable.

It also accepted only a float, so `remember(..., valid_from="2024-03-01T00:00:00Z")` -- the natural
thing to pass, and the form every other timestamp on the record already uses -- died with
`ValueError: could not convert string to float`.

The distinction was named by `eventTimeSource` in joshuaswarren/remnic#1666, found by the Scout.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus                                             # noqa: E402


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), **kw)


def _rec(m, mid):
    return [r for r in m.items if r["id"] == mid][0]


MARCH = 1709251200.0        # 2024-03-01T00:00:00Z


def test_an_iso_string_is_accepted_and_lands_on_the_right_instant():
    m = _store()
    r = _rec(m, m.remember("the office moved to Brno", key="office",
                           valid_from="2024-03-01T00:00:00Z"))
    assert r["valid_from"] == MARCH, r["valid_from"]


def test_an_epoch_number_still_works():
    """The old calling convention must not break; this is the regression half."""
    m = _store()
    r = _rec(m, m.remember("the office moved to Brno", key="office", valid_from=MARCH))
    assert r["valid_from"] == MARCH


def test_an_unparseable_value_RAISES_rather_than_quietly_using_the_clock():
    """The important half. Falling back to `now` would stamp an event time nobody asserted while
    `valid_from_source` said `declared`, which is worse than a crash because it is silent."""
    m = _store()
    with pytest.raises(ValueError) as e:
        m.remember("the office moved", key="office", valid_from="last March")
    msg = str(e.value)
    # Test the remedy the message names: it must say what IS accepted, or the caller is left guessing.
    assert "ISO-8601" in msg and "epoch" in msg, msg
    assert "2024-03-01T00:00:00Z" in msg, "the message should show a form that works: %s" % msg


def test_the_record_says_whether_anyone_declared_the_event_time():
    """PRESENCE is the claim; ABSENCE means nobody asserted an event time.

    Writing "ingest" on every record was the first design and it cost +9.8% serialized bytes on the
    write benchmark, caught by the work-counter gate. A tenth of every user's disk to record the
    absence of a claim is the wrong trade, and absence already carries that meaning unambiguously:
    a defaulted record and a pre-2.8.0 record are both records nobody declared a time for.
    """
    m = _store()
    declared = _rec(m, m.remember("moved to Brno", key="a", valid_from="2024-03-01T00:00:00Z"))
    defaulted = _rec(m, m.remember("moved to Kosice", key="b"))
    assert declared["valid_from_source"] == "declared"
    assert "valid_from_source" not in defaulted, defaulted


def test_CONTROL_the_ambiguity_this_closes_was_real():
    """Without `valid_from_source` these two records are indistinguishable on their timestamps alone.

    A fact declared to start NOW and a fact whose event time nobody supplied both end up with
    valid_from == ts. That is the whole reason a second field is needed rather than a comparison.
    """
    m = _store()
    now = _rec(m, m.remember("effective immediately", key="c"))["ts"]
    declared_now = _rec(m, m.remember("also effective immediately", key="d", valid_from=now))
    defaulted = _rec(m, m.remember("nobody said when", key="e"))
    assert abs(declared_now["valid_from"] - declared_now["ts"]) < 1.0
    assert abs(defaulted["valid_from"] - defaulted["ts"]) < 1.0
    assert declared_now.get("valid_from_source") != defaulted.get("valid_from_source"), (
        "the timestamps agree; only the provenance field separates them")
    assert declared_now["valid_from_source"] == "declared" and "valid_from_source" not in defaulted


def test_as_of_surfaces_the_provenance_rather_than_only_storing_it():
    """A field nothing reads is decoration. The same shape as a README marker that broke registry
    publishing for a whole release while every test stayed green."""
    m = _store()
    m.remember("office is in Brno", key="office", object="Brno", valid_from="2024-03-01T00:00:00Z")
    got = m.as_of("office", MARCH + 86400)
    assert got is not None, "the back-dated record should be current a day later"
    assert got.get("valid_from_source") == "declared", got


def test_a_back_dated_iso_record_is_placed_by_its_EVENT_time_not_its_write_time():
    """The point of bi-temporality, exercised through the string form that used to crash."""
    m = _store()
    m.remember("office is in Kosice", key="office", object="Kosice")          # today, ingest-time
    m.remember("office was in Brno", key="office", object="Brno",
               valid_from="2024-03-01T00:00:00Z")                            # back-filled
    assert m.as_of("office", MARCH + 3600)["object"] == "Brno"
