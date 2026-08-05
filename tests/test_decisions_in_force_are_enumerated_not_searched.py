""""Which decision is in force on topic X" is a scan, not a search, and it has to stay one.

WHAT THIS FIXES. The cross-session dogfood probe asks for the current value of a corrected decision
after a session boundary and reports `current_decision_found_at: null`. Diagnosed on that corpus
(2,571 records): the decision was in the store and active the whole time, but the question

    "how does a release get published these days?"

shares ZERO tokens with the decision it asks for

    "publish releases from the trusted publisher workflow"

so it ranked 25th with reinforce=False and 29th with the library default, below a draft note that
merely repeated the query's surface words. Chasing that with better ranking is a dead end this lab
has already measured. Keyed supersession, though, leaves exactly ONE active record per key, so the
answer is available by enumeration and needs no ranking at all.

WHY EACH ASSERTION. A method that returned every record would satisfy "the current decision is
returned" and be useless, so the retired record, the keyed non-decision and the unkeyed decision are
all asserted absent. The last one is not an oversight being enshrined: an unkeyed decision cannot
supersede anything, so it has no "in force" to report, and a caller who wants those should say so.

THE CONTROL THAT MAKES THIS NON-VACUOUS. `test_the_search_path_still_fails_on_this_store` asserts
that recall() does NOT find the decision on the same fixture. If a future ranking change makes
search work here, that test fails and tells us this enumeration is no longer load-bearing -- which
is information, not breakage. Without it, this file would pass just as happily on a store where
search already worked, and would prove nothing about the defect it was written for.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402

CURRENT = "DECISION: publish releases from the trusted publisher workflow"
RETIRED = "DECISION: publish releases from the tag-triggered pipeline"
QUERY = "how does a release get published these days?"
TOPIC = "release::channel"
KEY = "decision::" + TOPIC


def _store():
    """The correction case, buried in enough surface-similar noise to sink it in a ranked list."""
    m = Inspeximus(path=None)
    # Written through the real API, so the fixture exercises the `decision::<topic>` convention the
    # method keys on rather than an ad-hoc key invented by the test.
    retired = m.remember_decision(RETIRED, topic=TOPIC)
    current = m.remember_decision(CURRENT, because="the tag pipeline cannot mint the attestation",
                                  topic=TOPIC)
    other = m.remember_decision("pin the reasoning budget to 16000", topic="llm::budget")
    m.remember("file x.py :: current state -> pass", key="file:x.py", tags=["file"])
    m.remember("DECISION recorded without any key at all", tags=["decision"])
    # An EVENT decision: a commit message. Keyed by SHA, unique forever, retracts nothing.
    m.remember("DECISION: api: drop the tag-triggered publish path", key="commit::abc123def456",
               tags=["decision", "commit"])
    for i in range(60):
        m.remember(f"draft: a short summary of how release and closed interact today {i}",
                   tags=["noise"])
    return m, current, retired, other


def test_the_current_decision_is_returned():
    m, current, _, _ = _store()
    assert current in [r["id"] for r in m.decisions_in_force()]


def test_the_retired_decision_is_not():
    m, _, retired, _ = _store()
    assert retired not in [r["id"] for r in m.decisions_in_force()], (
        "keyed supersession retired it; reporting it as in force would serve a decision we reversed")


def test_one_record_per_key():
    m, _, _, _ = _store()
    keys = [r["key"] for r in m.decisions_in_force()]
    assert sorted(keys) == ["decision::llm::budget", KEY]
    assert len(keys) == len(set(keys)), "a key with two 'current' decisions is a supersession failure"


def test_a_keyed_non_decision_is_not_reported():
    m, _, _, _ = _store()
    assert not any(r["key"] == "file:x.py" for r in m.decisions_in_force()), (
        "the file-state records are the bulk of a coding store; if they come back here the caller "
        "gets the command log this call exists to replace")


def test_an_unkeyed_decision_is_not_reported():
    m, _, _, _ = _store()
    assert not any("without any key" in r["text"] for r in m.decisions_in_force()), (
        "no key means nothing can supersede it, so it has no current-value semantics to report")


def test_tag_none_and_no_prefix_enumerates_every_key():
    m, _, _, _ = _store()
    keys = {r["key"] for r in m.decisions_in_force(tag=None, key_prefix="")}
    assert "file:x.py" in keys and KEY in keys, (
        "the empty prefix with tag=None is the escape hatch for callers who key other things")


def test_a_commit_is_an_event_and_is_not_in_force_by_default():
    """The distinction the default exists for. A commit does not retract its predecessor, so every
    commit is 'current' forever; enumerating them returns the project's whole history and buries the
    handful of decisions that actually hold."""
    m, _, _, _ = _store()
    assert not any(str(r["key"]).startswith("commit::") for r in m.decisions_in_force())


def test_but_the_commit_log_is_still_reachable_on_request():
    m, _, _, _ = _store()
    got = m.decisions_in_force(key_prefix="commit::")
    assert len(got) == 1 and got[0]["key"] == "commit::abc123def456", (
        "excluding events from the default must not make them unreachable")


def test_it_is_deterministic():
    m, _, _, _ = _store()
    assert [r["id"] for r in m.decisions_in_force()] == [r["id"] for r in m.decisions_in_force()]


def test_limit_takes_the_newest():
    m, _, _, other = _store()
    top = m.decisions_in_force(limit=1)
    assert len(top) == 1 and top[0]["id"] == other, (
        "ordering is newest-first, so a bounded injection block carries the freshest decisions")


def test_the_search_path_still_fails_on_this_store():
    """THE CONTROL. If this ever passes, enumeration stopped being the thing that saves this case."""
    m, current, _, _ = _store()
    hits = m.recall(QUERY, k=5, mode="lexical", reinforce=False) or []
    ranks = [i for i, h in enumerate(hits, 1) if h["id"] == current]
    assert not ranks, (
        f"recall() now finds the current decision at rank {ranks[0]} on this fixture. That is good "
        f"news, but it means this fixture no longer reproduces the defect decisions_in_force() was "
        f"written for, and the tests above have stopped being evidence about it.")


def test_an_empty_store_reports_nothing_rather_than_erroring():
    assert Inspeximus(path=None).decisions_in_force() == []
