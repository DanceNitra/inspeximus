# -*- coding: utf-8 -*-
"""Value-level stale suppression at READ time (recall(suppress_stale_values=True)).

The defect it answers, measured on the MemOps corpus: supersession retires a RECORD, not a VALUE.
One corrected value is smeared across a dozen unkeyed sentences (user states it, assistant echoes it,
a summary repeats it) and retiring the single keyed row leaves the rest active and retrievable.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mnemo.mnemo import Mnemo, regex_extractor   # noqa: E402


def _smeared_store():
    """One correction, keyed; the retired value also stated by four records that carry no key."""
    m = Mnemo(path=None)
    m.extractor = regex_extractor
    m.remember("my title is Junior Data Analyst")                      # keyed, later superseded
    m.remember("Congratulations on the Junior Data Analyst role!")     # echo, unkeyed
    m.remember("The HR template lists Junior Data Analyst on line 4.")
    m.remember("Summary: title Junior Data Analyst, team analytics.")
    m.remember("my title is Senior Data Analyst")                      # the correction, keyed
    return m


def test_default_off_is_legacy_order():
    m = _smeared_store()
    a = [r["id"] for r in m.recall("what is my title", k=10, mode="lexical", reinforce=False)]
    b = [r["id"] for r in m.recall("what is my title", k=10, mode="lexical", reinforce=False,
                                   suppress_stale_values=False)]
    assert a == b


def test_unkeyed_echoes_of_a_retired_value_are_demoted():
    m = _smeared_store()
    q, kw = "what is my title", dict(k=3, mode="lexical", reinforce=False)
    before = " ".join(r["text"] for r in m.recall(q, **kw))
    after = " ".join(r["text"] for r in m.recall(q, suppress_stale_values=True, **kw))
    assert "Junior Data Analyst" in before          # the defect, reproduced
    assert "Junior Data Analyst" not in after       # ...and suppressed at the value level
    assert "Senior Data Analyst" in after           # the correction survives


def test_the_correction_itself_is_never_suppressed():
    """A record stating BOTH values ('changed from X to Y') is the correction, not an echo."""
    m = _smeared_store()
    m.remember("I moved from Junior Data Analyst to Senior Data Analyst last week.")
    hits = m.recall("junior senior data analyst title", k=10, mode="lexical", reinforce=False,
                    suppress_stale_values=True)
    assert any("moved from Junior Data Analyst to Senior" in r["text"] for r in hits[:3])


def test_substring_values_decide_correctly_both_directions():
    """'Data Analyst' retired vs 'Senior Data Analyst' current: a substring must not fake a match."""
    m = Mnemo(path=None)
    m.extractor = regex_extractor
    m.remember("my title is Data Analyst")
    m.remember("Nice, Data Analyst suits you.")
    m.remember("my title is Senior Data Analyst")
    hits = m.recall("what is my title", k=3, mode="lexical", reinforce=False,
                    suppress_stale_values=True)
    txt = " ".join(r["text"] for r in hits)
    assert "Senior Data Analyst" in txt
    assert "Nice, Data Analyst suits you." not in txt


def test_never_returns_an_empty_result():
    """If every candidate is stale the stage is a no-op — demotion must not become deletion."""
    m = _smeared_store()
    hits = m.recall("Junior Data Analyst", k=2, mode="lexical", reinforce=False,
                    suppress_stale_values=True)
    assert hits


def test_suppression_stage_adds_no_state_of_its_own():
    """recall() already stamps _stale_derived; this stage must add nothing beyond that."""
    kw = dict(k=5, mode="lexical", reinforce=False)
    a = _smeared_store(); a.recall("what is my title", **kw)
    b = _smeared_store(); b.recall("what is my title", suppress_stale_values=True, **kw)
    _vol = ("id", "ts", "iso", "valid_from", "last_access", "superseded_ts", "invalidated_at", "meta")
    strip = lambda m: [{k: v for k, v in r.items() if k not in _vol} for r in m.items]
    assert strip(a) == strip(b)


def test_include_superseded_still_surfaces_the_retired_value():
    m = _smeared_store()
    hits = m.recall("what is my title", k=10, mode="lexical", reinforce=False,
                    suppress_stale_values=True, include_superseded=True)
    assert any("Junior Data Analyst" in r["text"] for r in hits)
