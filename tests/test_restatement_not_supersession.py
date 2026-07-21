# -*- coding: utf-8 -*-
"""Agreement is not correction: a record restating the current value must not retire it.

Measured on the MemOps corpus (agora_output/lab/memops/keying_recall.py). Once the assistant's echoes
of a value are keyed - which is what a correction layer needs, since the first-person assertion is one
sentence in six - keyed last-write-wins turned every agreeing restatement into a supersession. Each key
kept exactly one active record however often the value was confirmed, and the current answer's
retrievability collapsed (present in a top-20 recall for 5 of 12 correction chains -> 3 of 12).

Two rules follow, both tested here:
  1. an identical value never supersedes (this file's first tests);
  2. a record that does not ASSERT A CHANGE never supersedes, even when its value string differs -
     "your address remains 742 Birchwood Lane, Unit 4A" and "Unit 4A" are one fact at two
     granularities, and only an extractor can see that the sentence claims no change. Extractors may
     therefore return a third element; a 2-tuple keeps the legacy behaviour exactly.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from inspeximus.core import Inspeximus   # noqa: E402


def _store(extractor):
    m = Inspeximus(path=None)
    m.extractor = extractor
    return m


def test_identical_value_does_not_supersede():
    m = _store(lambda t: ("my::title", "Senior Data Analyst"))
    for txt in ("my title is Senior Data Analyst",
                "your current title is Senior Data Analyst",
                "Confirmed: Senior Data Analyst."):
        m.remember(txt)
    assert [r["status"] for r in m.items] == ["active"] * 3


def test_a_different_value_still_supersedes():
    def ex(t):
        return ("my::title", "Senior Data Analyst" if "Senior" in t else "Data Analyst")
    m = _store(ex)
    m.remember("my title is Data Analyst")
    m.remember("my title is now Senior Data Analyst")
    assert [r["status"] for r in m.items] == ["superseded", "active"]


def test_restatement_flag_blocks_supersession_across_granularities():
    """'Unit 4A' and '742 Birchwood Lane, Unit 4A' are the same fact stated two ways."""
    def ex(t):
        obj = "742 Birchwood Lane, Unit 4A" if "Birchwood" in t else "Unit 4A"
        return ("my::address", obj, "remains" not in t and "keep" not in t)
    m = _store(ex)
    m.remember("my address is 742 Birchwood Lane, Unit 4A")     # asserts a change
    m.remember("your address remains Unit 4A")                  # restatement -> must not retire it
    assert [r["status"] for r in m.items] == ["active", "active"]


def test_a_change_assertion_still_supersedes_with_the_flag_present():
    def ex(t):
        obj = "Unit 3A" if "3A" in t else "Unit 4A"
        return ("my::address", obj, "now" in t)
    m = _store(ex)
    m.remember("my address is Unit 4A")
    m.remember("actually it's Unit 3A now")
    assert [r["status"] for r in m.items] == ["superseded", "active"]


def test_two_tuple_extractor_is_unchanged():
    """No third element -> every prior behaviour holds (this is the shipped contract)."""
    def ex(t):
        return ("my::city", "Berlin" if "Berlin" in t else "Prague")
    m = _store(ex)
    m.remember("I live in Prague")
    m.remember("I live in Berlin")
    assert [r["status"] for r in m.items] == ["superseded", "active"]


def test_a_literal_duplicate_collapses_but_a_reworded_one_does_not():
    """The line between 'evidence worth keeping' and 'the same row twice'.

    Two differently-worded sentences carrying one value are two pieces of evidence, and retiring one
    measurably cost current-value coverage. The SAME text written twice under the same key is one
    fact stored twice -- and for a keyed KV caller it is a re-put, which must leave one row. Keeping
    both broke LangGraph's checkpointer conformance (test_put_writes_idempotent).
    """
    dup = Inspeximus(path=None)
    for _ in range(2):
        dup.remember("lg write t/0", key="lgwrite::t::0", meta={"kind": "lg_write"})
    assert sum(1 for r in dup.items if r["status"] == "active") == 1

    reworded = Inspeximus(path=None)
    reworded.remember("my title is Senior Data Analyst", key="my::title", object="Senior Data Analyst")
    reworded.remember("your title is Senior Data Analyst", key="my::title", object="Senior Data Analyst")
    assert sum(1 for r in reworded.items if r["status"] == "active") == 2
